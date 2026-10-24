#!/usr/bin/env python3
"""Full Oxford OVO runner with progress and causal transition-map renders.

Reuses the validated short-segment adapter without editing it. Observations are
read-only: no queue flush, map pruning, GT input or new semantic thresholds.
Snapshot labels remain predictions, stored separately from blinded annotations.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'third_party/OVO'))
os.environ.setdefault('HF_HOME', str(ROOT/'third_party/model_cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('MPLBACKEND', 'Agg')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--annotation-package', type=Path, required=True)
    ap.add_argument('--frame-limit', type=int, default=-1)
    a = ap.parse_args()
    import cv2
    import numpy as np
    import psutil
    import torch
    from scipy.spatial.transform import Rotation
    import yaml
    from ovo.entities import ovomapping
    from livo_ovo_geometry import Rectifier, nearest_stamp, transform, world_from_camera
    from verify_phase1_handoff import sha
    import run_livo_ovo

    source, output, package = a.input.resolve(), a.output.resolve(), a.annotation_package.resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    capture = Path(manifest['capture'])
    sequence = json.loads((capture/'descriptor.json').read_text())['id']
    annotations = json.loads((package/'manifest.json').read_text())
    ontology = json.loads((package/'ontology.json').read_text())
    if sha(package/'ontology.json') != annotations['ontology_sha256']:
        raise ValueError('Frozen ontology hash mismatch')
    samples = sorted([s for s in annotations['samples'] if s['sequence'] == sequence], key=lambda s: s['image_stamp_ns'])
    pose_rows = json.loads((capture/'capture_index.json').read_text())['poses']
    pose_times = np.array([p['stamp_ns'] for p in pose_rows], np.int64)
    frame_times = np.array([f['image_stamp_ns'] for f in manifest['frames']], np.int64)
    rect = Rectifier(yaml.safe_load((capture/'camera.yaml').read_text())['/**']['ros__parameters'])
    t_ci = np.array(manifest['T_camera_imu'])
    count = len(frame_times) if a.frame_limit < 0 else a.frame_limit
    process = psutil.Process()
    started = time.monotonic()
    resources, snapshots = [], []
    next_sample = 0
    text_features = None

    class ObservedMap(ovomapping.OVOSemMap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owner = self
            self.last_finished = -1
            self.last_report = 0.
            self.base_dataset = self.dataset
            (output/'transition_predictions').mkdir()
            provenance = dict(wrapper_sha256=sha(Path(__file__).resolve()),
                              package_manifest_sha256=sha(package/'manifest.json'),
                              ontology_sha256=sha(package/'ontology.json'), sequence=sequence,
                              prediction_mode='causal_map_at_raw_input_time', no_gt_read=True,
                              pending_descriptor_policy='retain existing past descriptor; abstain if descriptor not yet computed',
                              missing_pose_policy='all pixels abstain; no future/interpolated pose',
                              rendering='sparse nearest-pixel map z-buffer, no densification; no claim of perfect occlusion')
            (output/'full_run_protocol.json').write_text(json.dumps(provenance, indent=2)+'\n')

            class DatasetView:
                def __len__(self):
                    return len(owner.base_dataset)

                def __getattr__(self, key):
                    return getattr(owner.base_dataset, key)

                def __getitem__(self, index):
                    owner.observe_before(index)
                    return owner.base_dataset[index]

            self.dataset = DatasetView()
            complete = self.ovo.complete_semantic_info

            def finish_before_flush():
                # In vanilla mapping this is called only at run end; do not
                # change when the author's delayed CLIP queue is flushed.
                if self.last_finished != count-1:
                    self.last_finished = count-1
                    self.save_remaining()
                return complete()

            self.ovo.complete_semantic_info = finish_before_flush

        def observe_before(self, index):
            self.last_finished = index-1
            while next_sample < len(samples) and samples[next_sample]['image_stamp_ns'] < int(frame_times[index]):
                self.save_one(samples[next_sample])
            if time.monotonic()-self.last_report >= 10:
                self.report(index)

        def report(self, processed):
            self.last_report = time.monotonic()
            row = dict(wall_seconds=time.monotonic()-started, processed_frames=processed, expected_frames=count,
                       fraction=processed/count, map_points=self.slam_backbone.max_id, objects=len(self.ovo.objects),
                       sensor_ns=int(frame_times[max(0, processed-1)]), rss_bytes=process.memory_info().rss,
                       cuda_allocated_bytes=torch.cuda.memory_allocated(), cuda_reserved_bytes=torch.cuda.memory_reserved(),
                       transition_predictions=len(snapshots))
            resources.append(row)
            temporary = output/'progress.tmp'
            temporary.write_text(json.dumps(row, indent=2)+'\n')
            temporary.replace(output/'progress.json')
            (output/'resources.json').write_text(json.dumps(resources, indent=2)+'\n')
            print('FULL_PROGRESS', json.dumps(row), flush=True)
            if row['rss_bytes'] > 10*1024**3:
                raise RuntimeError('OVO RSS exceeds declared 10 GiB budget; retain failed run')

        def save_one(self, sample):
            nonlocal next_sample, text_features
            t = sample['image_stamp_ns']+manifest['image_to_imu_offset_ns']
            pi = nearest_stamp(pose_times, t)
            pred = np.full((rect.height, rect.width), 255, np.uint8)
            instance_map = np.full(pred.shape, -1, np.int32)
            geometry_support = np.zeros(pred.shape, bool)
            row = dict(sample_id=sample['sample_id'], raw_image_stamp_ns=sample['image_stamp_ns'],
                       last_processed_frame_id=self.last_finished,
                       map_image_stamp_ns=int(frame_times[self.last_finished]) if self.last_finished >= 0 else None,
                       causal=True, pose_available=pi is not None, map_points=self.slam_backbone.max_id,
                       available_descriptor_objects=0, predicted_pixels=0, geometry_pixels=0)
            features, object_ids = [], []
            if pi is not None and self.last_finished >= 0:
                p = pose_rows[pi]
                t_wi = transform(Rotation.from_quat(p['quaternion_xyzw']).as_matrix(), p['position'])
                t_wc = world_from_camera(t_wi, t_ci)
                xyz, _, map_ids = self.slam_backbone.get_map()
                _, provenance = rect.project(xyz.detach().cpu().numpy(), t_wc,
                                              *manifest['depth_range_m'])
                visible = provenance >= 0
                geometry_support = visible
                instance_map[visible] = map_ids.detach().cpu().numpy()[provenance[visible]]
                for oid, obj in self.ovo.objects.items():
                    if obj.clip_feature is not None and torch.isfinite(obj.clip_feature).all():
                        object_ids.append(int(oid))
                        features.append(obj.clip_feature.detach().float().reshape(-1))
                if features:
                    generator = self.ovo.clip_generator
                    with torch.inference_mode(), torch.autocast('cuda', enabled=False):
                        if text_features is None:
                            texts = [ontology['template'].format(c['query']) for c in ontology['classes']]
                            text_features = torch.vstack([torch.nn.functional.normalize(
                                generator.get_txt_embedding([q]).float().mean(0, keepdim=True), dim=-1) for q in texts])
                        descriptors = torch.stack(features).to('cuda')
                        scores = generator.get_similarity(text_features, descriptors, *generator.similarity_args)
                    if not torch.isfinite(scores).all():
                        raise ValueError('Nonfinite causal query scores')
                    classes = scores.argmax(-1).cpu().numpy()
                    for oid, cls in zip(object_ids, classes):
                        pred[instance_map == oid] = int(cls)
                    np.savez_compressed(output/'transition_predictions'/f'{sample["sample_id"]}_features.npz',
                                        object_ids=np.asarray(object_ids), descriptors=descriptors.cpu().numpy(), scores=scores.cpu().numpy())
                row.update(available_descriptor_objects=len(object_ids), predicted_pixels=int((pred != 255).sum()), geometry_pixels=int(geometry_support.sum()))
            if row['map_image_stamp_ns'] is not None and row['map_image_stamp_ns'] > sample['image_stamp_ns']:
                raise ValueError('Causal render includes a future observation')
            cv2.imwrite(str(output/'transition_predictions'/f'{sample["sample_id"]}.png'), pred)
            np.savez_compressed(output/'transition_predictions'/f'{sample["sample_id"]}_instances.npz',
                                instance_ids=instance_map, geometry_support=geometry_support)
            snapshots.append(row)
            next_sample += 1
            (output/'transition_predictions/index.json').write_text(json.dumps(snapshots, indent=2)+'\n')

        def save_remaining(self):
            while next_sample < len(samples):
                self.save_one(samples[next_sample])
            self.report(count)

    original = ovomapping.OVOSemMap
    ovomapping.OVOSemMap = ObservedMap
    sys.argv = ['run_livo_ovo.py', str(source), '--output', str(output), '--depth-policy', 'valid_mask',
                '--mapping-policy', 'valid_mask', '--geometry-precision', 'float32', '--frame-limit', str(a.frame_limit)]
    try:
        run_livo_ovo.main()
    finally:
        ovomapping.OVOSemMap = original
        if output.exists():
            (output/'full_monitor.json').write_text(json.dumps(dict(sequence=sequence,
                finished_at=datetime.datetime.now().astimezone().isoformat(), causal_renders=len(snapshots),
                requested_renders=len(samples), all_rendered=len(snapshots) == len(samples),
                peak_sampled_rss_bytes=max((r['rss_bytes'] for r in resources), default=0)), indent=2)+'\n')


if __name__ == '__main__':
    main()
