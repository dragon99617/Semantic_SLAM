#!/usr/bin/env python3
"""Run the official OVO reference-pose mapper with immutable per-run config.

Replica and OSMa RGB-D are semantic-mapping baselines, not FAST-LIVO2 runs.
Use the isolated semantic_venv. Outputs include all effective settings.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OVO = ROOT / 'third_party/OVO'
os.environ.setdefault('HF_HOME', str(ROOT / 'third_party/model_cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('DISABLE_WANDB', 'true')
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '4')
sys.path.insert(0, str(OVO))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=['Replica', 'OSMa'], required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--input-dir', type=Path, help='Completed locally replayed OSMa RGB-D directory.')
    p.add_argument('--condition', default='baseline')
    p.add_argument('--frame-limit', type=int, default=-1,
                   help='Positive values are smoke tests, not full-scene results.')
    args = p.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'ovo_map.ckpt').exists():
        raise SystemExit('Output already has a map; choose a new directory.')
    os.chdir(OVO)
    import torch
    import numpy as np
    import yaml
    from ovo.utils import io_utils, gen_utils
    from ovo.entities.ovomapping import OVOSemMap

    config = io_utils.load_config('data/working/configs/ovo.yaml')
    io_utils.update_recursive(config, io_utils.load_config('data/working/configs/slam/vanilla/replica.yaml'))
    io_utils.update_recursive(config, io_utils.load_config('data/working/configs/Replica/replica.yaml'))
    if args.dataset == 'Replica':
        source = ROOT / 'datasets/Replica' / args.scene
    else:
        source = (args.input_dir.resolve() if args.input_dir else
                  ROOT / 'datasets/OSMa-Bench/data/replica_cad' / args.scene)
        if args.input_dir:
            manifest = json.loads((source / 'render_manifest.json').read_text())
            assert manifest['completed'] and manifest['scene'] == args.scene
            assert manifest['condition'] == args.condition
        camera = yaml.safe_load((source / 'camera_params.yaml').read_text())['camera_params']
        config['cam'] = dict(H=camera['image_height'], W=camera['image_width'],
                             fx=camera['fx'], fy=camera['fy'], cx=camera['cx'], cy=camera['cy'],
                             depth_scale=camera['png_depth_scale'], crop_edge=camera.get('crop_edge', 0))
    frames = len(list((source / 'results').glob('frame*.jpg')))
    depths = len(list((source / 'results').glob('depth*.png')))
    poses = sum(1 for _ in (source / 'traj.txt').open())
    assert frames == depths == poses and frames > 0, (frames, depths, poses)
    matrices = np.loadtxt(source / 'traj.txt').reshape(-1,4,4)
    assert np.isfinite(matrices).all(), 'Invalid reference poses'
    assert np.allclose(matrices[:,3,:], [0,0,0,1]), 'Invalid pose convention'
    if args.frame_limit > 0:
        assert args.frame_limit <= frames
    config['data'].update(scene_name=args.scene, input_path=str(source), frame_limit=args.frame_limit)
    config.update(use_wandb=False, debug=False, debug_info=False)
    config['semantic']['clip'].update(use_half=True, image_batch_size=8)
    config['semantic']['sam']['points_per_batch'] = 16
    config['semantic']['verbose'] = False
    config['semantic']['log'] = True
    config['vis'].update(stream=False, show_stream=False)
    config['benchmark'] = dict(dataset=args.dataset, scene=args.scene,
                                pose_source='dataset_reference', smoke_test=args.frame_limit > 0,
                                condition=args.condition, local_replay=bool(args.input_dir))
    (output / 'audit.json').write_text(json.dumps(dict(
        dataset=args.dataset, scene=args.scene, condition=args.condition, input_path=str(source),
        rgb_frames=frames, depth_frames=depths, pose_frames=poses,
        requested_frame_limit=args.frame_limit, pose_source='dataset_reference',
        ovo_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=OVO, text=True).strip(),
        torch=torch.__version__, cuda=torch.version.cuda,
        device=torch.cuda.get_device_name(0), start_time=time.strftime('%Y-%m-%dT%H:%M:%S%z')), indent=2))
    gen_utils.setup_seed(config['seed'])
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    mapper = OVOSemMap(config, output_path=output)
    initialized = time.perf_counter()
    mapper.run()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    expected = frames if args.frame_limit < 0 else args.frame_limit
    cameras = torch.load(output / 'estimated_c2w.npy', map_location='cpu', weights_only=False)
    assert set(cameras) == set(range(expected)), 'Incomplete frame traversal'
    (output / 'runtime.json').write_text(json.dumps(dict(
        initialization_seconds=initialized-start, total_wall_seconds=elapsed,
        processed_frames=expected, reference_cameras_saved=len(cameras),
        segmented_frame_ids=sorted(set(mapper.logger.stats['frame_id'])),
        peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
        peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved(),
        completed=True, full_scene=args.frame_limit < 0), indent=2))


if __name__ == '__main__':
    main()
