#!/usr/bin/env python3
"""Run frozen author OVO on audited FAST-LIVO2 sparse RGB-D inputs.

Isolated semantic_venv; no ROS imports or edits to third-party source. The
explicit valid_mask policies adapt depth filtering and duplicate suppression
to missing data. Author policies remain available for compatibility diagnosis.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OVO = ROOT/'third_party/OVO'
os.environ.setdefault('HF_HOME', str(ROOT/'third_party/model_cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('DISABLE_WANDB', 'true')
os.environ.setdefault('MPLBACKEND', 'Agg')
sys.path.insert(0, str(OVO))

from verify_phase1_handoff import sha


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--depth-policy', choices=('author', 'valid_mask'), required=True)
    ap.add_argument('--mapping-policy', choices=('author', 'valid_mask'), default='author')
    ap.add_argument('--frame-limit', type=int, default=-1)
    a = ap.parse_args()
    import numpy as np
    import torch
    from ovo.utils import io_utils, gen_utils, geometry_utils
    from ovo.entities import ovomapping
    from sparse_depth_filter import valid_mask_depth_filter, valid_mask_pooling
    from ovo.slam.vanilla_mapper import VanillaMapper

    source = a.input.resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    if not manifest['completed'] or manifest['pose_source'] != 'FAST-LIVO2_estimated':
        raise ValueError('Require prepared estimated-pose input')
    frames = manifest['frames']
    count = len(frames) if a.frame_limit < 0 else a.frame_limit
    if not 1 <= count <= len(frames):
        raise ValueError('Invalid frame limit')
    for row in frames[:count]:
        if sha(source/row['path']) != row['sha256']:
            raise ValueError('Prepared frame hash mismatch')
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_snapshot = output/'adapter_sources'
    source_snapshot.mkdir()
    adapter_hashes = {}
    for name in ('run_livo_ovo.py', 'sparse_depth_filter.py'):
        shutil.copy2(ROOT/'scripts'/name, source_snapshot/name)
        adapter_hashes[name] = sha(source_snapshot/name)
    if not torch.cuda.is_available():
        raise RuntimeError('OVO integration run requires the recorded CUDA environment')
    os.chdir(OVO)
    # Copy effective phase-one config rather than reinterpreting defaults.
    config = io_utils.load_config(str(ROOT/'experiments/multibench_20261020/runs/Replica/office0/config.yaml'))
    if config['slam']['slam_module'] != 'vanilla':
        raise ValueError('This adapter requires the frozen vanilla mapping backbone')
    original_semantic = json.loads(json.dumps(config['semantic']))
    k = np.array(manifest['rectified_intrinsics'])
    config['dataset_name'] = 'livo_sparse'
    config['cam'] = dict(H=manifest['height'], W=manifest['width'], fx=float(k[0, 0]),
                         fy=float(k[1, 1]), cx=float(k[0, 2]), cy=float(k[1, 2]), depth_scale=1., crop_edge=0)
    config['data'] = dict(scene_name=source.name, input_path=str(source), frame_limit=count)
    config['benchmark'] = dict(pose_source='FAST-LIVO2_estimated',
                               short_segment=manifest['short_segment'], depth_policy=a.depth_policy,
                               mapping_policy=a.mapping_policy,
                               camera_axes='right_down_forward', input_manifest_sha256=sha(source/'manifest.json'))

    class SparseDataset:
        def __init__(self, dataset_config):
            self.dataset_config = dataset_config
            self.height, self.width = manifest['height'], manifest['width']
            self.crop_edge = 0
            self.intrinsics = k

        def __len__(self):
            return count

        def __getitem__(self, i):
            with np.load(source/frames[i]['path']) as data:
                rgb, depth, mask, pose = (data[n].copy() for n in ('rgb', 'depth', 'valid', 'c2w'))
            if (rgb.shape != (self.height, self.width, 3) or depth.shape != (self.height, self.width)
                    or not np.array_equal(mask, depth > 0) or not np.isfinite(depth).all()):
                raise ValueError(f'Frame {i} failed RGB/depth/mask validation')
            return i, rgb, depth, pose.astype(np.float32)

    get_dataset = ovomapping.get_dataset
    ovomapping.get_dataset = lambda name: SparseDataset if name == 'livo_sparse' else get_dataset(name)
    mapping_trace = []

    class SparseMapper(VanillaMapper):
        def __init__(self, config_, intrinsics):
            super().__init__(config_, intrinsics)
            if a.mapping_policy == 'valid_mask' and config_['mapping'].get('k_pooling', 3) > 1:
                kernel = config_['mapping'].get('k_pooling', 3)
                self.pooling = lambda mask: valid_mask_pooling(mask, self.observed, kernel)

        def map(self, frame_data, c2w):
            self.observed = torch.from_numpy(frame_data[2] > 0).to(self.device)
            before = self.max_id
            super().map(frame_data, c2w)
            mapping_trace.append(dict(frame_id=frame_data[0], points_added=self.max_id-before, map_points=self.max_id))

    original_backbone = ovomapping.get_slam_backbone
    ovomapping.get_slam_backbone = lambda cfg, ds, intr: SparseMapper(cfg, intr)
    original_filter = geometry_utils.depth_filter
    if a.depth_policy == 'valid_mask':
        geometry_utils.depth_filter = valid_mask_depth_filter
    policy_probe = []
    for i in range(0, count, config['semantic']['segment_every']):
        with np.load(source/frames[i]['path']) as frame:
            depth = torch.from_numpy(frame['depth']).to('cuda')
        with torch.no_grad():
            original = original_filter(depth)
            adapted = valid_mask_depth_filter(depth)
        policy_probe.append(dict(frame_id=i, valid_pixels=int((depth > 0).sum()),
                                 author_surviving_pixels=int((original > 0).sum()),
                                 valid_mask_surviving_pixels=int((adapted > 0).sum())))
    audit = dict(started_at=datetime.datetime.now().astimezone().isoformat(),
                 input_manifest=str(source/'manifest.json'), input_manifest_sha256=sha(source/'manifest.json'),
                 source_version=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                 ovo_patch_sha256=__import__('hashlib').sha256(subprocess.check_output(['git', 'diff', '--binary'])).hexdigest(),
                 torch=torch.__version__, cuda=torch.version.cuda, device=torch.cuda.get_device_name(0),
                 frame_count=count, pose_source='FAST-LIVO2_estimated',
                 depth_policy=a.depth_policy, original_semantic_config_unchanged=config['semantic'] == original_semantic,
                 mapping_policy=a.mapping_policy,
                 adapter_source_hashes=adapter_hashes,
                 filter_probe=policy_probe)
    (output/'audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    gen_utils.setup_seed(config['seed'])
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    runtime = dict(completed=False, error=None)
    try:
        mapper = ovomapping.OVOSemMap(config, output_path=output)
        initialized = time.perf_counter()
        mapper.run()
        torch.cuda.synchronize()
        cameras = torch.load(output/'estimated_c2w.npy', map_location='cpu', weights_only=False)
        if set(cameras) != set(range(count)):
            raise ValueError('OVO did not traverse every accepted frame')
        max_camera_error = 0.
        for i, value in cameras.items():
            with np.load(source/frames[i]['path']) as data:
                max_camera_error = max(max_camera_error, float(np.max(np.abs(np.asarray(value)-data['c2w']))))
        if max_camera_error > 1e-4:
            raise ValueError('OVO used an unexpected camera transform')
        # Upstream run() saves the map and deletes its GPU backbone/OVO objects.
        saved = torch.load(output/'ovo_map.ckpt', map_location='cpu', weights_only=False)
        points = saved['map_params']['xyz']
        obj_ids = saved['map_params']['obj_ids'].reshape(-1)
        object_ids = np.asarray(saved['ovo_map_params']['ins_3d_ids'])
        assigned = int(np.isin(obj_ids.numpy(), object_ids).sum())
        if not torch.isfinite(points).all():
            raise ValueError('Nonfinite semantic map')
        runtime.update(completed=True, initialization_seconds=initialized-start,
                       processed_frames=count, segmented_frame_ids=sorted(set(mapper.logger.stats['frame_id'])),
                       map_points=len(points), assigned_points=assigned, objects=len(object_ids),
                       semantic_map_nonempty=assigned > 0 and len(object_ids) > 0,
                       mapping_frames_with_new_points=sum(r['points_added'] > 0 for r in mapping_trace),
                       max_saved_camera_error=max_camera_error)
        np.savez_compressed(output/'map_preview.npz', xyz=points.detach().cpu().numpy(),
                            obj_ids=obj_ids.numpy(), colors=saved['map_params']['color'].numpy())
    except Exception as e:
        runtime['error'] = f'{type(e).__name__}: {e}'
        raise
    finally:
        geometry_utils.depth_filter = original_filter
        ovomapping.get_dataset = get_dataset
        ovomapping.get_slam_backbone = original_backbone
        runtime.update(total_wall_seconds=time.perf_counter()-start,
                       peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
                       peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved(),
                       short_segment=manifest['short_segment'], depth_policy=a.depth_policy)
        runtime['mapping_policy'] = a.mapping_policy
        (output/'mapping_trace.json').write_text(json.dumps(mapping_trace, indent=2)+'\n')
        (output/'runtime.json').write_text(json.dumps(runtime, indent=2)+'\n')
        print(json.dumps(runtime, indent=2), flush=True)


if __name__ == '__main__':
    main()
