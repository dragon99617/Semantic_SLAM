#!/usr/bin/env python3
"""Evaluate our eee_01 poses and the author's poses with identical evo rules.

Run with third_party/accuracy_venv/bin/python. Uses independent measured
positions, compensates the rotating IMU-to-prism lever arm, and fixes scale.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation
from evo.core import metrics, sync
from evo.tools import file_interface


LEVER_ARM = np.array([-0.293656, -0.012288, -0.273095])


def audit_callbacks(root, run):
    log = (run / 'mapping.log').read_text(errors='replace')
    database = root / 'datasets/NTU_VIRAL_eee01_humble/eee_01.db3'
    error_lines = [line for line in log.splitlines() if '[ERROR]' in line]
    runtime_log = log.split('[rclcpp]: signal_handler(SIGINT/SIGTERM)')[0]
    result = {'error_log_lines': len(error_lines), 'error_log_details': error_lines,
        'runtime_error_log_lines': runtime_log.count('[ERROR]'),
        'mapper_error_log_lines': sum('[fastlivo_mapping-' in line or '[laserMapping]' in line for line in error_lines),
        'vio_frames_with_raw_features': len(re.findall(r'\[ VIO \] Raw feature num:', log)),
        'limitation': 'These are callback log matches; PointCloud2 callback has no per-message log, so this does not prove every LiDAR record was consumed.'}
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        for topic, pattern in (('/imu/imu', r'get imu at time: ([0-9.]+)'),
                               ('/left/image_raw', r'Get image, its header time: ([0-9.]+)')):
            idx = conn.execute('SELECT id FROM topics WHERE name=?', (topic,)).fetchone()[0]
            # Header starts directly after the 4-byte CDR encapsulation.
            stamps = np.array([s + ns / 1e9 for (raw,) in conn.execute(
                'SELECT substr(data,5,8) FROM messages WHERE topic_id=? ORDER BY id', (idx,))
                for s, ns in [struct.unpack('<iI', raw)]])
            observed = np.array([float(v) for v in re.findall(pattern, log)])
            if not len(observed):
                raise RuntimeError(f'No callbacks logged for {topic}')
            observed.sort()
            right = np.clip(np.searchsorted(observed, stamps), 0, len(observed) - 1)
            left = np.maximum(right - 1, 0)
            matched = np.minimum(np.abs(stamps - observed[right]), np.abs(stamps - observed[left])) <= 2e-6
            missing = stamps[~matched]
            result[topic] = {'bag_records': len(stamps), 'callback_log_entries': len(observed),
                'matched_records': int(matched.sum()), 'unmatched_records': int((~matched).sum()),
                'unmatched_before_first_callback': int(np.sum(missing < observed[0] - 2e-6)),
                'unmatched_between_callbacks': int(np.sum((missing >= observed[0] - 2e-6) & (missing <= observed[-1] + 2e-6))),
                'unmatched_after_last_callback': int(np.sum(missing > observed[-1] + 2e-6)),
                'unmatched_sensor_times': missing.tolist(), 'matching_tolerance_s': 2e-6}
    return result


def interpolation_check(reference, estimate, output, prefix):
    """Dataset tutorial protocol: interpolate bracketed GT, each side <=0.1s."""
    gt = np.loadtxt(reference)
    est = np.loadtxt(estimate)
    if not np.all(np.diff(gt[:, 0]) > 0):
        raise RuntimeError('Ground truth timestamps must be strictly increasing')
    right = np.searchsorted(gt[:, 0], est[:, 0], side='right')
    left = right - 1
    interior = (left >= 0) & (right < len(gt))
    left = np.clip(left, 0, len(gt) - 1)
    right = np.clip(right, 0, len(gt) - 1)
    keep = interior & (est[:, 0] - gt[left, 0] <= 0.1) & (gt[right, 0] - est[:, 0] <= 0.1)
    times = est[keep, 0]
    interpolated = np.column_stack([np.interp(times, gt[:, 0], gt[:, i]) for i in (1, 2, 3)])
    measured = est[keep, 1:4]
    a = measured - measured.mean(axis=0)
    b = interpolated - interpolated.mean(axis=0)
    u, _, vt = np.linalg.svd(a.T @ b)
    if np.linalg.det(vt.T @ u.T) < 0:
        vt[-1] *= -1
    rotation = vt.T @ u.T
    aligned = a @ rotation.T + interpolated.mean(axis=0)
    errors = np.linalg.norm(aligned - interpolated, axis=1)
    np.savetxt(output / f'{prefix}_interpolated.csv', np.column_stack((times, interpolated, aligned, errors)),
        delimiter=',', header='sensor_time,gt_x,gt_y,gt_z,estimate_x,estimate_y,estimate_z,ape_m', comments='')
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].plot(*interpolated[:, :2].T, color='black', linewidth=1, label='Interpolated Leica GT')
    axes[0].plot(*aligned[:, :2].T, linewidth=0.6, label=prefix)
    axes[0].set(xlabel='x [m]', ylabel='y [m]', title='Matched trajectory, XY view', aspect='equal')
    axes[0].legend()
    axes[1].scatter(times - times[0], errors * 100, s=2)
    axes[1].set(xlabel='Time from first matched pose [s]', ylabel='Translation APE [cm]',
        title=f'Interpolated GT: RMSE {np.sqrt(np.mean(errors**2)) * 100:.3f} cm; {len(times)} matches')
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(output / f'{prefix}_interpolated_accuracy.png', dpi=160)
    plt.close(fig)
    return {'rmse_m': float(np.sqrt(np.mean(errors**2))), 'mean_m': float(errors.mean()),
        'median_m': float(np.median(errors)), 'max_m': float(errors.max()),
        'matched_poses': len(times), 'estimated_poses': len(est), 'max_bracket_distance_s': 0.1,
        'matched_time_start_s': float(times[0]), 'matched_time_end_s': float(times[-1]),
        'alignment': 'rigid rotation and translation, no scale correction',
        'time_offset_s': 0.0, 'reference_file': str(reference)}


def evaluate(reference, estimate, output, prefix):
    ref = file_interface.read_tum_trajectory_file(reference)
    est = file_interface.read_tum_trajectory_file(estimate)
    matched_ref, matched_est = sync.associate_trajectories(ref, est, max_diff=0.01)
    alignment = matched_est.align(matched_ref, correct_scale=False)
    ape = metrics.APE(metrics.PoseRelation.translation_part)
    ape.process_data((matched_ref, matched_est))
    dt = np.abs(matched_ref.timestamps - matched_est.timestamps)
    result = {'stats_m': {k: float(v) for k, v in ape.get_all_statistics().items()},
        'reference_poses': ref.num_poses, 'estimated_poses': est.num_poses,
        'associated_poses': matched_ref.num_poses,
        'associated_estimate_fraction': matched_ref.num_poses / est.num_poses,
        'matched_time_start_s': float(matched_est.timestamps[0]),
        'matched_time_end_s': float(matched_est.timestamps[-1]),
        'reference_time_start_s': float(ref.timestamps[0]),
        'reference_time_end_s': float(ref.timestamps[-1]),
        'association_abs_dt_max_s': float(dt.max()),
        'association_abs_dt_median_s': float(np.median(dt)),
        'alignment_R': alignment[0].tolist(), 'alignment_t': alignment[1].tolist(),
        'scale': float(alignment[2]), 'reference_file': str(reference), 'estimate_file': str(estimate)}
    np.savetxt(output / f'{prefix}_matched.csv', np.column_stack((matched_est.timestamps,
        matched_ref.positions_xyz, matched_est.positions_xyz, ape.error, dt)), delimiter=',',
        header='estimate_sensor_time,gt_x,gt_y,gt_z,estimate_x,estimate_y,estimate_z,ape_m,abs_time_difference_s', comments='')
    fig = plt.figure(figsize=(13, 5))
    ax = fig.add_subplot(121, projection='3d')
    ax.scatter(*matched_ref.positions_xyz.T, label='Leica matched samples', color='black', s=3)
    ax.scatter(*matched_est.positions_xyz.T, label=f'{prefix} matched samples', s=2)
    ax.set(xlabel='x [m]', ylabel='y [m]', zlabel='z [m]')
    ax.legend()
    ax = fig.add_subplot(122)
    ax.scatter(matched_est.timestamps - matched_est.timestamps[0], ape.error * 100, s=3)
    ax.set(xlabel='Time from first matched pose [s]', ylabel='Translation APE [cm]',
           title=f'RMSE {result["stats_m"]["rmse"] * 100:.3f} cm; {matched_ref.num_poses} matches')
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(output / f'{prefix}_accuracy.png', dpi=160)
    plt.close(fig)
    # Save an independently inspectable standard evo result archive too.
    command = [str(Path(sys.executable).with_name('evo_ape')), 'tum', str(reference), str(estimate),
        '-a', '--t_max_diff', '0.01', '--save_results', str(output / f'{prefix}_evo_ape.zip')]
    with (output / f'{prefix}_evo_stdout.txt').open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    result['command'] = command
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    run = args.run.resolve()
    summary = json.loads((run / 'summary.json').read_text())
    if summary['status'] != 'playback_completed' or summary['diagnostic_duration_s']:
        raise RuntimeError('Full clean playback required before accuracy evaluation')
    output = run / 'accuracy'
    output.mkdir(exist_ok=False)
    estimate = np.loadtxt(run / 'estimate_imu.tum')
    assert estimate.ndim == 2 and estimate.shape[1] == 8 and np.isfinite(estimate).all()
    assert np.all(np.diff(estimate[:, 0]) > 0)
    prism = estimate.copy()
    prism[:, 1:4] += Rotation.from_quat(estimate[:, 4:8]).apply(LEVER_ARM)
    np.savetxt(output / 'estimate_prism.tum', prism, fmt='%.9f')
    upstream = root / 'third_party/FAST-LIVO2_original/Log/result/ntu_viral'
    ref = upstream / 'eee_01_gt.txt'
    author = upstream / 'eee_01_prism.txt'
    raw_gt = root / 'datasets/NTU_VIRAL_eee01_humble/eee_01_gt_from_bag.tum'
    for path in (ref, author, raw_gt):
        shutil_target = output / path.name
        shutil_target.write_bytes(path.read_bytes())
    # Main comparison follows the upstream README exactly: same GT and
    # association tolerance for our output and the supplied author output.
    results = {'protocol': {'metric': 'absolute translation error RMSE, meters',
        'alignment': 'SE(3) Umeyama; scale fixed to 1', 'max_time_difference_s': 0.01,
        'time_offset_s': 0.0, 'association': 'evo nearest_time, no interpolation',
        'lever_arm_imu_to_prism_m': LEVER_ARM.tolist(),
        'rotation_error': 'not evaluated; GT orientations are identity placeholders',
        'parameters': 'unmodified community NTU estimation configuration; output options only'},
        'versions': {k: importlib.metadata.version(k) for k in ('evo', 'rosbags', 'numpy', 'scipy', 'pandas')},
        'callback_audit': audit_callbacks(root, run),
        'ros2_vs_author_gt': evaluate(output / ref.name, output / 'estimate_prism.tum', output, 'ros2'),
        'author_vs_author_gt': evaluate(output / ref.name, output / author.name, output, 'author'),
        'ros2_vs_raw_bag_gt': evaluate(output / raw_gt.name, output / 'estimate_prism.tum', output, 'ros2_raw_gt'),
        'author_vs_raw_bag_gt': evaluate(output / raw_gt.name, output / author.name, output, 'author_raw_gt'),
        'raw_gt_interpolation_crosscheck': {
            'ros2': interpolation_check(output / raw_gt.name, output / 'estimate_prism.tum', output, 'ros2'),
            'author': interpolation_check(output / raw_gt.name, output / author.name, output, 'author')},
        'trajectory': {'pose_count': len(estimate), 'start_sensor_s': float(estimate[0, 0]),
            'end_sensor_s': float(estimate[-1, 0]), 'duration_s': float(estimate[-1, 0] - estimate[0, 0]),
            'max_quaternion_norm_error': float(np.abs(np.linalg.norm(estimate[:, 4:8], axis=1) - 1).max()),
            'max_pose_interval_s': float(np.diff(estimate[:, 0]).max())}}
    results['ros2_to_author_rmse_ratio'] = (results['ros2_vs_author_gt']['stats_m']['rmse'] /
                                           results['author_vs_author_gt']['stats_m']['rmse'])
    (output / 'metrics.json').write_text(json.dumps(results, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
        for p in sorted(output.iterdir()) if p.is_file()))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
