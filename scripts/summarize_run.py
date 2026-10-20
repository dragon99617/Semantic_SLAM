#!/usr/bin/env python3
"""Validate and plot the archived trajectory and colored map of one run."""
import argparse
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def read_pcd(path):
    header = {}
    with path.open('rb') as stream:
        while True:
            line = stream.readline().decode('ascii').strip()
            if not line or line.startswith('#'):
                continue
            key, *value = line.split()
            header[key] = value
            if key == 'DATA':
                break
        if header['DATA'] != ['binary']:
            raise ValueError('Expected uncompressed binary PCD')
        types = {('F', 4): '<f4', ('F', 8): '<f8', ('U', 4): '<u4', ('I', 4): '<i4'}
        dtype = np.dtype([(name, types[(kind, int(size))]) for name, kind, size in zip(
            header['FIELDS'], header['TYPE'], header['SIZE'])])
        count = int(header['POINTS'][0])
        data = np.fromfile(stream, dtype=dtype, count=count)
        if len(data) != count:
            raise ValueError('Truncated point cloud')
    return header, data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    summary = json.loads((run_dir / 'summary.json').read_text())
    trajectory = np.loadtxt(run_dir / 'Retail_Street.txt', ndmin=2)
    if trajectory.shape[1] != 8 or not np.isfinite(trajectory).all():
        raise ValueError('Invalid trajectory values')
    if not (np.diff(trajectory[:, 0]) > 0).all():
        raise ValueError('Trajectory timestamps are not strictly increasing')
    norms = np.linalg.norm(trajectory[:, 4:8], axis=1)
    if not np.allclose(norms, 1, atol=1e-4):
        raise ValueError('Non-unit trajectory quaternions')
    xyz = trajectory[:, 1:4]
    path_length = float(np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum())
    input_report = json.loads((run_dir.parents[1] / 'retail_street_humble_inspection.json').read_text())
    lidar_input = next(t for t in input_report['topics'] if t['name'] == '/livox/lidar')
    input_start = lidar_input['samples'][0]['stamp_ns'] * 1e-9
    input_end = lidar_input['samples'][-1]['stamp_ns'] * 1e-9
    coverage = float((trajectory[-1, 0] - trajectory[0, 0]) / (input_end - input_start))
    if coverage < 0.95 or len(trajectory) < lidar_input['count'] * 0.9:
        raise ValueError('Trajectory does not cover enough of the full input sequence')
    mapping_log = (run_dir / 'mapping.log').read_text(errors='replace')
    retrieves = [int(n) for n in re.findall(r'\[ VIO \] Retrieve (\d+) points', mapping_log)]
    updates = [int(n) for n in re.findall(r'\[ VIO \] Update (\d+) points', mapping_log)]
    header, cloud = read_pcd(run_dir / 'all_downsampled_points.pcd')
    cloud_xyz = np.column_stack([cloud[c] for c in ('x', 'y', 'z')])
    finite = np.isfinite(cloud_xyz).all(axis=1)
    if not finite.all() or len(cloud) == 0:
        raise ValueError('Empty cloud or nonfinite map coordinates')
    quality = {
        'run_status': summary['status'], 'trajectory_poses': len(trajectory),
        'mapper_clean_exit': summary.get('mapper_clean_exit', False),
        'trajectory_duration_seconds': float(trajectory[-1, 0] - trajectory[0, 0]),
        'lidar_input_messages': lidar_input['count'],
        'sensor_time_coverage_fraction': coverage,
        'initialization_gap_seconds': float(trajectory[0, 0] - input_start),
        'path_length_meters': path_length,
        'start_xyz': xyz[0].tolist(), 'end_xyz': xyz[-1].tolist(),
        'trajectory_xyz_min': xyz.min(axis=0).tolist(),
        'trajectory_xyz_max': xyz.max(axis=0).tolist(),
        'last_trajectory_to_last_lidar_seconds': float(
            summary['last_header_stamp']['/livox/lidar'] - trajectory[-1, 0]),
        'vio_frames_in_log': len(retrieves),
        'vio_frames_with_retrieved_points': sum(n > 0 for n in retrieves),
        'vio_frames_with_reference_updates': sum(n > 0 for n in updates),
        'downsampled_map_points': len(cloud), 'map_fields': header['FIELDS'],
        'map_xyz_min': cloud_xyz.min(axis=0).tolist(),
        'map_xyz_max': cloud_xyz.max(axis=0).tolist(),
        'ground_truth_evaluated': False,
        'scope': 'Basic full-sequence execution and output validation; no ATE/RPE claim.',
    }
    if summary['status'] != 'playback_completed' or summary.get('mapper_clean_exit') is not True or not retrieves or not any(n > 0 for n in retrieves):
        raise ValueError('Playback or visual processing was not successful')
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
    axes[0].plot(xyz[:, 0], xyz[:, 1], color='#2563eb', linewidth=1.7)
    axes[0].scatter(*xyz[0, :2], c='#16a34a', s=45, label='Start', zorder=3)
    axes[0].scatter(*xyz[-1, :2], c='#dc2626', s=45, label='End', zorder=3)
    axes[0].set(xlabel='X (m)', ylabel='Y (m)', title='Estimated trajectory (XY)')
    axes[0].set_aspect('equal', adjustable='datalim')
    axes[0].legend()
    axes[1].plot(trajectory[:, 0] - trajectory[0, 0], xyz[:, 2], color='#7c3aed')
    axes[1].set(xlabel='Elapsed sensor time (s)', ylabel='Z (m)', title='Estimated height')
    for axis in axes:
        axis.grid(alpha=0.25)
    fig.suptitle(f'Retail_Street | FAST-LIVO2 ROS 2 | {len(trajectory)} poses | {path_length:.1f} m')
    fig.savefig(run_dir / 'trajectory.png', dpi=180)
    plt.close(fig)

    sampled = cloud[np.linspace(0, len(cloud) - 1, min(len(cloud), 150000), dtype=int)]
    color = None
    if 'rgb' in sampled.dtype.names:
        rgb = sampled['rgb'].copy().view(np.uint32)
        color = np.column_stack([(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255]) / 255
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    for axis, a, b in ((axes[0], 'x', 'y'), (axes[1], 'x', 'z')):
        axis.scatter(sampled[a], sampled[b], c=color, s=0.3, linewidths=0, rasterized=True)
        axis.set(xlabel=f'{a.upper()} (m)', ylabel=f'{b.upper()} (m)', title=f'Colored map ({a.upper()}{b.upper()})')
        axis.set_aspect('equal', adjustable='datalim')
    fig.suptitle(f'Retail_Street | {len(cloud):,} downsampled points | showing {len(sampled):,}')
    fig.savefig(run_dir / 'map_preview.png', dpi=180)
    plt.close(fig)
    (run_dir / 'validation.json').write_text(json.dumps(quality, indent=2) + '\n')
    print(json.dumps(quality, indent=2))


if __name__ == '__main__':
    main()
