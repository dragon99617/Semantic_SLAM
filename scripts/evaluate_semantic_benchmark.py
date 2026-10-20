#!/usr/bin/env python3
"""Evaluate OVO maps against Replica vertices or OSMa RGB-D semantic GT.

Outputs point predictions, confusion matrices, per-class metrics and coverage.
No truth masks/labels enter the mapper. OSMa's official scene-vocabulary setting
is explicit; its GT reconstruction uses stride 5, end=-1, uniform sampling 10.
"""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OVO = ROOT / 'third_party/OVO'
os.environ.setdefault('HF_HOME', str(ROOT / 'third_party/model_cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '4')
sys.path.insert(0, str(OVO))


def osma_truth(source, cache):
    import cv2
    import numpy as np
    import yaml
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / 'manifest.json').exists():
        camera = yaml.safe_load((source / 'camera_params.yaml').read_text())['camera_params']
        depths = sorted((source / 'results').glob('depth*.png'))
        semantics = sorted((source / 'results').glob('semantic*.png'))
        poses = np.loadtxt(source / 'traj.txt').reshape(-1, 4, 4)
        assert len(depths) == len(semantics) == len(poses)
        y, x = np.indices((camera['image_height'], camera['image_width']))
        ray = np.stack(((x-camera['cx'])/camera['fx'], (y-camera['cy'])/camera['fy'], np.ones_like(x)), -1)
        total_valid = kept = 0
        frames = list(range(len(depths)))[:-1:5]
        with (cache / 'xyz.bin').open('wb') as xf, (cache / 'labels.bin').open('wb') as lf:
            for i in frames:
                depth = cv2.imread(str(depths[i]), cv2.IMREAD_UNCHANGED).astype(np.float64)/camera['png_depth_scale']
                sem = cv2.imread(str(semantics[i]), cv2.IMREAD_UNCHANGED)
                assert sem.ndim == 2
                valid = np.isfinite(depth) & (depth > 0)
                # Reproduce concatenation then uniform_down_sample(every_k_points=10).
                start = (-total_valid) % 10
                cam = (ray[valid]*depth[valid,None])[start::10]
                world = cam @ poses[i,:3,:3].T + poses[i,:3,3]
                labs = sem[valid][start::10]
                world.astype('<f4').tofile(xf)
                labs.astype('<i4').tofile(lf)
                total_valid += int(valid.sum())
                kept += len(labs)
        (cache / 'manifest.json').write_text(json.dumps(dict(
            source=str(source), count=kept, stride=5, end=-1, uniform_down_sample=10,
            selected_frames=frames, camera=camera,
            official_reference='OSMa-Bench/semseg/export/osma-bench/gt_reconstruction_replica_cad.sh'), indent=2))
    n = json.loads((cache / 'manifest.json').read_text())['count']
    return np.memmap(cache/'xyz.bin', dtype='<f4', mode='r', shape=(n,3)), np.memmap(cache/'labels.bin', dtype='<i4', mode='r', shape=(n,))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=['Replica','OSMa'], required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--run', type=Path, required=True)
    args = p.parse_args()
    run = args.run.resolve()
    os.chdir(OVO)
    import numpy as np
    import torch
    import yaml
    from scipy.spatial import cKDTree
    from scipy.stats import mode
    from plyfile import PlyData
    from ovo.entities.clip_generator import CLIPGenerator
    from ovo.utils import eval_utils

    start = time.perf_counter()
    config = yaml.safe_load((run/'config.yaml').read_text())
    audit = json.loads((run/'audit.json').read_text())
    runtime = json.loads((run/'runtime.json').read_text())
    assert runtime['completed'] and runtime['full_scene']
    assert runtime['processed_frames'] == audit['rgb_frames']
    assert audit['dataset'] == args.dataset and audit['scene'] == args.scene
    assert config['benchmark']['pose_source'] == 'dataset_reference'
    assert not config['benchmark']['smoke_test'], 'Smoke tests cannot produce benchmark metrics.'
    # Upstream io_utils deliberately saves map checkpoints in the legacy format.
    # These maps are small enough to load normally; only the 3.6 GB model weights
    # use mmap in CLIPGenerator.
    ckpt = torch.load(run/'ovo_map.ckpt', map_location='cpu', weights_only=False)
    xyz = ckpt['map_params']['xyz'].numpy()
    obj_ids = ckpt['map_params']['obj_ids'].numpy().reshape(-1)
    obj = ckpt['ovo_map_params']
    ids = obj['ins_3d_ids'].astype(int)
    assigned = (obj_ids >= 0) & np.isin(obj_ids, ids)
    if args.dataset == 'Replica':
        info = yaml.safe_load((OVO/'data/working/configs/Replica/eval_info.yaml').read_text())
        names = info['class_names_reduced']
        class_ids = np.arange(len(names))
        mesh = PlyData.read(ROOT/'datasets/Replica'/f'{args.scene}_mesh.ply')
        gt_xyz = np.stack([mesh['vertex'][k] for k in 'xyz'], -1)
        raw = np.loadtxt(OVO/'data/input/replica_semantic_gt'/f'{args.scene}.txt', dtype=int)
        assert len(raw) == len(gt_xyz)
        gt_ids = np.array([info['map_to_reduced'].get(int(i), 51) for i in raw])
        ignored = 51
        nn = 5
        template = 'This is a photo of a {}'
    else:
        source = ROOT/'datasets/OSMa-Bench/data/replica_cad'/args.scene
        gt_cache = ROOT/'datasets/OSMa-Bench_gt'/args.scene
        if config['benchmark'].get('local_replay'):
            rendered = json.loads((Path(config['data']['input_path'])/'render_manifest.json').read_text())
            source = Path(rendered['fixed_depth_and_truth_from'])
            assert json.loads((source/'render_manifest.json').read_text())['completed']
            gt_cache = ROOT/'datasets/OSMa_paired_gt'/args.scene
        gt_xyz, gt_ids = osma_truth(source, gt_cache)
        info = json.loads((source/'embed_semseg_classes.json').read_text())
        mapping = {i['id']:i['name'].replace('_',' ') for i in info['classes']}
        assert set(np.unique(gt_ids).tolist()) <= set(mapping), 'GT IDs absent from class metadata'
        class_ids = np.array(sorted(set(np.unique(gt_ids).tolist()) & set(mapping) - {0}), dtype=int)
        names = [mapping[i] for i in class_ids]
        ignored = len(names)
        nn = 1
        template = 'an image of {}'
    # Classification only needs the encoder, not SAM or the learned crop fuser.
    cc = dict(config['semantic']['clip'], embed_type='fixed_weights')
    generator = CLIPGenerator(cc, device='cuda') if len(ids) else None
    features = []
    for i in ids:
        key = f'ins3d_{i}_clip_feature'
        if key not in obj:
            raise KeyError(f'Missing object descriptor {key}; keys: {list(obj)[:15]}')
        features.append(torch.as_tensor(obj[key]).reshape(-1))
    # OVO.get_objs_clips() assembles stored descriptors into float32 tensors.
    descriptors = (torch.stack(features).to(device='cuda', dtype=torch.float32)
                   if features else torch.empty((0,1152)))
    assert torch.isfinite(descriptors).all(), 'Non-finite object descriptors'
    with torch.no_grad():
        scores = (generator.get_embed_txt_similarity(descriptors, names, templates=template)
                  if generator else torch.empty((0,len(names))))
        assert torch.isfinite(scores).all(), 'Non-finite classification scores'
        predicted = scores.argmax(-1).cpu().numpy()
    np.savez_compressed(run/'instance_classes.npz', ids=ids, predicted_indices=predicted,
                        class_ids=class_ids, class_names=np.array(names), scores=scores.cpu().numpy())
    del scores, descriptors, generator
    torch.cuda.empty_cache()
    lookup = np.full(int(max(ids.max(initial=-1),obj_ids.max(initial=-1)))+1, ignored, dtype=np.int32)
    lookup[ids] = predicted
    tree = cKDTree(xyz[assigned]) if assigned.sum() >= nn else None
    assert tree is not None or assigned.sum() == 0, 'Too few assigned points for fixed neighbor protocol.'
    valid_obj = obj_ids[assigned]
    count = len(gt_xyz)
    confusion = np.zeros((len(names)+1,len(names)+1), dtype=np.int64)
    strict = np.zeros_like(confusion)
    gt_lookup = {int(cid):j for j,cid in enumerate(class_ids)}
    gt_to_idx = np.full(max(int(np.max(gt_ids)),int(max(class_ids)))+1, ignored, dtype=np.int32)
    for cid,j in gt_lookup.items():gt_to_idx[cid] = j
    pred_out = np.lib.format.open_memmap(run/'point_predictions.npy', mode='w+', dtype=np.int32, shape=(count,))
    distances = np.lib.format.open_memmap(run/'nearest_distances_m.npy', mode='w+', dtype=np.float32, shape=(count,))
    coverage = {t:0 for t in (0.02,0.05,0.10)}
    evaluated = 0
    for first in range(0,count,50000):
        last = min(first+50000,count)
        if tree is None:
            distance = np.full(last-first, np.inf)
            labels = np.full(last-first,ignored,dtype=np.int32)
        elif nn == 5:
            d, ix = tree.query(gt_xyz[first:last], k=nn, workers=4)
            # Official Replica: vote instance IDs, then classify that instance.
            matched = mode(valid_obj[ix], axis=1, keepdims=False).mode
            distance = d[:,0]
            labels = lookup[matched]
        else:
            d, ix = tree.query(gt_xyz[first:last], k=nn, workers=4)
            matched, distance = valid_obj[ix], d
            labels = lookup[matched]
        pred_out[first:last] = np.r_[class_ids,-1][labels]
        distances[first:last] = distance
        raw_gt = np.asarray(gt_ids[first:last])
        target = np.full(len(raw_gt), ignored, dtype=np.int32)
        ok = raw_gt >= 0
        target[ok] = gt_to_idx[raw_gt[ok]]
        keep = target != ignored
        flat = target[keep]*(len(names)+1)+labels[keep]
        confusion += np.bincount(flat, minlength=confusion.size).reshape(confusion.shape)
        reject = labels.copy()
        reject[distance > .05] = ignored
        flat_strict = target[keep]*(len(names)+1)+reject[keep]
        strict += np.bincount(flat_strict, minlength=strict.size).reshape(strict.shape)
        evaluated += int(keep.sum())
        for t in coverage:coverage[t] += int(((distance <= t)&keep).sum())
    pred_out.flush();distances.flush()
    np.save(run/'confusion.npy', confusion)
    np.save(run/'confusion_5cm_abstention.npy', strict)

    def metrics(cm):
        tp = np.diag(cm)[:-1].astype(float)
        support = cm.sum(1)[:-1]
        union = support + cm.sum(0)[:-1] - tp
        iou = np.divide(tp,union,out=np.full(len(names),np.nan),where=union>0)
        recall = tp/np.maximum(support,1)
        valid = (union>0) if args.dataset == 'Replica' else (support>0)
        return dict(miou=float(np.mean(iou[valid])),macc=float(np.mean(recall[valid])),
                    overall_accuracy=float(tp.sum()/support.sum())), iou, recall, support

    result, iou, recall, support = metrics(confusion)
    with (run/'per_class.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['class_id','class_name','gt_points','iou','recall'])
        w.writerows(zip(class_ids,names,support,iou,recall))
    result.update(dataset=args.dataset,scene=args.scene,gt_points=count,evaluated_gt_points=evaluated,
                  condition=config['benchmark'].get('condition','baseline'),
                  data_origin='local_replay' if config['benchmark'].get('local_replay') else 'released',
                  assigned_map_points=int(assigned.sum()),total_map_points=len(xyz),objects=len(ids),
                  coverage={str(t):v/evaluated for t,v in coverage.items()},
                  strict_5cm=metrics(strict)[0],nearest_neighbors=nn,template=template,
                  vocabulary='Replica official reduced 51' if args.dataset=='Replica' else 'OSMa official scene label set, excluding 0',
                  pose_source='dataset_reference',classification_encoder=config['semantic']['clip']['model_card'],
                  evaluation_wall_seconds=time.perf_counter()-start)
    if args.dataset == 'Replica':
        # Cross-check metrics against the upstream implementation.
        a,mask,_,b,bmask=eval_utils.iou_acc_from_confmat(confusion, len(names), [], labels=names)
        assert np.isclose(result['miou'], a[mask].mean())
        assert np.isclose(result['macc'], b[bmask].mean())
    else:
        spec=importlib.util.spec_from_file_location('osma_metrics',ROOT/'third_party/OSMa-Bench/semseg/scripts/compute_metrics.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        ref=module.compute_metrics(confusion, labels=np.r_[class_ids,-1], excluded=[0,-1])
        assert np.isclose(result['miou'],ref['miou']) and np.isclose(result['macc'],ref['macc'])
    (run/'metrics.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
