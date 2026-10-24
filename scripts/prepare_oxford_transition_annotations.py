#!/usr/bin/env python3
"""Freeze blinded annotation tasks from raw Oxford input times, never predictions.

System ROS Python after sourcing scripts/env.sh. PNGs use the same rectified
camera as the integrated mapper. Missing anchors and overlapping windows remain
explicit. Templates contain only 255 (unannotated), never ground-truth labels.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import zipfile

import cv2
import numpy as np
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image
import yaml

from livo_ovo_geometry import Rectifier, decode_image, offset_ns
from verify_phase1_handoff import sha

ROOT = Path(__file__).resolve().parents[1]
CLASSES = [
    ('ground_floor', 'ground or floor', '室内地面、室外道路、人行道、草地以外的可行走水平地面'),
    ('wall_building', 'building wall', '内外墙及建筑主体；排除门、窗、柱、楼梯等独立类别'),
    ('ceiling_roof', 'ceiling or roof', '顶棚与屋顶'),
    ('stairs', 'stairs', '台阶踏面、立面；楼梯扶手归 railing_fence'),
    ('door', 'door', '门扇与门框，不把门洞内的可见背景标成门'),
    ('window', 'window', '窗玻璃与窗框'),
    ('column', 'pillar or column', '可辨识的独立柱体'),
    ('vegetation', 'tree or vegetation', '树木、灌木、草地'),
    ('person', 'person', '行人'),
    ('vehicle', 'vehicle', '汽车、自行车及其他交通工具'),
    ('furniture', 'furniture', '桌椅、长凳、柜等家具'),
    ('sign', 'sign', '独立告示牌与标识'),
    ('sky', 'sky', '天空'),
    ('railing_fence', 'railing or fence', '栏杆、扶手与围栏'),
    ('other_object', 'other object', '确有可辨物体但不属于前述类别；无法辨识不使用此类'),
]
ANCHORS = {'before': [-25, -20, -15], 'transition': [-5, 0, 5], 'after': [15, 20, 25]}


def build(output):
    output.mkdir(parents=True, exist_ok=False)
    for name in ('images', 'valid_masks', 'label_templates', 'labels', 'contact_sheets'):
        (output/name).mkdir()
    ontology = dict(version='oxford_transition_v1', ignore_id=255,
                    classes=[dict(id=i, name=n, query=q, definition=d) for i, (n, q, d) in enumerate(CLASSES)],
                    overlap_rule='Prefer the most specific listed physical category; mark uncertain/occluded/illegible pixels 255.',
                    template='an image of {}')
    (output/'ontology.json').write_text(json.dumps(ontology, ensure_ascii=False, indent=2)+'\n')
    samples, missing, events, sources = [], [], [], []
    for seq in ('oxford02', 'oxford05'):
        desc_path = ROOT/'experiments/phase1_20261022'/f'{seq}.json'
        desc = json.loads(desc_path.read_text())
        bag = ROOT/desc['bag']
        cfg = yaml.safe_load((ROOT/desc['camera_config']).read_text())['/**']['ros__parameters']
        rect = Rectifier(cfg)
        ann_path = ROOT/'experiments/phase1_20261022/input_annotations'/seq/'events.json'
        ann = json.loads(ann_path.read_text())
        events.extend(ann['events'])
        with np.load(bag/'input_headers.npz') as cache:
            stamps = cache['/left_camera/image'].copy()
        meta = yaml.safe_load((bag/'metadata.yaml').read_text())['rosbag2_bagfile_information']
        refs = []
        for part in meta['relative_file_paths']:
            with sqlite3.connect(f'file:{bag/part}?mode=ro', uri=True) as db:
                tid = db.execute('SELECT id FROM topics WHERE name=?', ('/left_camera/image',)).fetchone()[0]
                refs.extend((bag/part, rid) for (rid,) in db.execute('SELECT id FROM messages WHERE topic_id=? ORDER BY timestamp,id', (tid,)))
        if len(refs) != len(stamps) or not (np.diff(stamps) > 0).all():
            raise ValueError('Header cache and image record index mismatch')
        selected = collections.defaultdict(list)
        for event in ann['events']:
            center = offset_ns(event['center_sensor_s'])
            for window, offsets in ANCHORS.items():
                for delta in offsets:
                    target = center+delta*10**9
                    j = int(np.searchsorted(stamps, target))
                    candidates = [i for i in (j-1, j) if 0 <= i < len(stamps)]
                    best = min(candidates, key=lambda i: abs(int(stamps[i])-target))
                    member = dict(event_id=event['id'], window=window, anchor_offset_s=delta,
                                  requested_ns=target, actual_delta_ns=int(stamps[best])-target)
                    if abs(int(stamps[best])-target) > 350000000:
                        missing.append(dict(sequence=seq, reason='no_raw_input_within_350ms_or_sequence_truncated', **member))
                    else:
                        selected[best].append(member)
        for ix, members in sorted(selected.items()):
            database, rid = refs[ix]
            with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
                raw = db.execute('SELECT data FROM messages WHERE id=?', (rid,)).fetchone()[0]
            sec, ns = struct.unpack_from('<iI', raw, 4)
            actual = sec*10**9+ns
            if actual != int(stamps[ix]):
                raise ValueError('Sampled CDR header differs from indexed header')
            image = deserialize_message(raw, Image)
            rgb = rect.rgb(decode_image(image))
            sid = f'{seq}_{actual}'
            image_path = output/'images'/f'{sid}.png'
            cv2.imwrite(str(image_path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            cv2.imwrite(str(output/'valid_masks'/f'{sid}.png'), rect.valid.astype(np.uint8)*255)
            cv2.imwrite(str(output/'label_templates'/f'{sid}.png'), np.full(rect.valid.shape, 255, np.uint8))
            samples.append(dict(sample_id=sid, sequence=seq, image_stamp_ns=actual, raw_image_index=ix,
                                image_path=f'images/{sid}.png', image_sha256=sha(image_path),
                                source_cdr_sha256=hashlib.sha256(raw).hexdigest(),
                                valid_mask_path=f'valid_masks/{sid}.png', valid_mask_sha256=sha(output/'valid_masks'/f'{sid}.png'), label_path=f'labels/{sid}.png',
                                width=rect.width, height=rect.height, memberships=members,
                                annotation_status='pending', annotator=None, reviewer=None))
        sources.append(dict(sequence=seq, descriptor=str(desc_path), bag=str(bag),
                            camera_config=str(ROOT/desc['camera_config']), camera_sha256=sha(ROOT/desc['camera_config']),
                            events_sha256=sha(ann_path), header_cache_sha256=sha(bag/'input_headers.npz')))
    for page, begin in enumerate(range(0, len(samples), 12)):
        canvas = np.full((3*230, 4*360, 3), 245, np.uint8)
        for slot, sample in enumerate(samples[begin:begin+12]):
            tile = cv2.imread(str(output/sample['image_path']))
            tile = cv2.resize(tile, (360, 200))
            y, x = (slot//4)*230, (slot%4)*360
            canvas[y:y+200, x:x+360] = tile
            member = sample['memberships'][0]
            label = f"{member['event_id']} {member['window']} {member['anchor_offset_s']:+d}s"
            cv2.putText(canvas, label, (x+4, y+219), cv2.FONT_HERSHEY_SIMPLEX, .46, (20, 20, 20), 1)
        cv2.imwrite(str(output/'contact_sheets'/f'page_{page:02d}.jpg'), canvas)
    manifest = dict(schema='oxford_transition_annotation_v1', status='awaiting_independent_annotation',
                    blinded_to_model_predictions=True, sampling='Nearest raw input to fixed frozen-event anchors; no selection by semantic output/quality',
                    anchor_offsets_s=ANCHORS, max_image_distance_ns=350000000, ontology_sha256=sha(output/'ontology.json'),
                    sources=sources, events=events, samples=samples, missing_anchors=missing,
                    requested_anchors=len(events)*9, unique_images=len(samples), labels_available=0,
                    limitations=['Boundary intervals are approximate; midpoint only anchors windows',
                                 'Overlapping event windows are correlated; deduplicate global scores',
                                 'Sampled 2D evaluation does not equal full-scene 3D or instance accuracy'])
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    with (output/'tasks.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['sample_id', 'sequence', 'image_stamp_ns', 'image_path', 'label_path', 'annotation_status', 'annotator', 'reviewer'])
        writer.writeheader()
        writer.writerows({k: s[k] for k in writer.fieldnames} for s in samples)
    readme = '''# Oxford 转场独立语义标注包 v1

本包只包含原始输入去畸变图像与冻结转场区间，不含 OVO 预测。**当前没有任何已完成语义标注。**

- 每个转场前/中/后各固定抽 3 帧（−25/−20/−15、−5/0/+5、+15/+20/+25 秒），不按模型表现择样。序列截断/缺帧见 manifest.json；重叠窗口不视为独立事件。
- 在 images/ 的原尺寸 PNG 上标注。使用 ontology.json 的 0–14 类；无法辨识、严重过曝/欠曝、遮挡边界及 valid_masks/ 外区域设为 255。
- 以单通道 uint8 PNG 保存到 labels/<sample_id>.png。禁止缩放、调色板 RGB 或 JPEG 标签。label_templates/ 全为 255，仅是空模板，不能当作完成标注。
- 更新 tasks.csv：annotation_status 改为 reviewed，填写不同的 annotator 与 reviewer；若整帧确实无法标注，填写 unscorable 并在 notes.json 说明原因，不能静默删帧。
- 只使用图像及相邻输入帧帮助判读，不查看 OVO 预测后修标签。一般优先专门类别（门、窗、柱、楼梯），剩余墙体归 wall_building；玻璃/门洞后的背景按可辨对象标，无法判读则 255。
- 标注冻结后应报告：每事件/每窗口像素准确率、mIoU、逐类 IoU、未预测比例；未知预测应计入假阴性，不可只挑已赋语义点。另报稀疏几何支持范围内的条件指标与覆盖率。实例 ID 保持率需要额外独立对象标注，本包不据此判定。

在独立标注完成前，语义精度验收状态始终为 awaiting_independent_annotation。
'''
    (output/'README.md').write_text(readme)
    archive = output.parent/(output.name+'.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                z.write(path, str(path.relative_to(output.parent)))
    print(json.dumps(dict(package=str(output), archive=str(archive), unique_images=len(samples),
                          requested_anchors=manifest['requested_anchors'], missing_anchors=len(missing), labels_available=0), indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output.resolve())
