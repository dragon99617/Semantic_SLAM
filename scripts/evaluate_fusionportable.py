#!/usr/bin/env python3
"""Validate a completed FPV2 run and report translation APE sensitivity.

Use accuracy_venv. Published GT contains position only. Report both direct IMU
and calibrated prism comparisons because the distributed text file does not
state whether its reference point was already converted by the authors.
No time-offset search, scale fitting, orientation accuracy or segment alignment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation

from evaluate_ntu_accuracy import evaluate, interpolation_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    run = args.run.resolve()
    summary = json.loads((run / 'summary.json').read_text())
    if summary['status'] != 'playback_completed' or summary['diagnostic']:
        raise RuntimeError('Requires a full, clean, non-diagnostic run')
    output = run / 'accuracy'
    output.mkdir(exist_ok=False)
    reference = root / 'datasets/FusionPortableV2/original/ugv_transition00_gt.tum'
    gt = np.loadtxt(reference)
    est = np.loadtxt(run / 'estimate_imu.tum')
    assert np.isfinite(est).all() and np.all(np.diff(est[:, 0]) > 0)
    assert np.all(np.diff(gt[:, 0]) > 0)
    assert np.allclose(np.linalg.norm(est[:, 4:8], axis=1), 1, atol=1e-4)
    assert np.allclose(gt[:, 4:8], [0, 0, 0, 1])
    prism = est.copy()
    lever = np.array([-0.001, 0.0, 0.1441])
    prism[:, 1:4] += Rotation.from_quat(est[:, 4:8]).apply(np.broadcast_to(lever, (len(est), 3)))
    np.savetxt(output / 'estimate_prism.tum', prism, fmt='%.9f')
    results = {'status': 'published_position_reference_comparison',
        'reference_file': str(reference), 'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'reference_point_limitation': 'Published text contains no explicit reference-point metadata. Direct IMU and calibrated prism results are both reported; do not select the lower value as the authoritative score.',
        'orientation_accuracy_evaluated': False, 'RPE_evaluated': False,
        'scale': 1.0, 'time_offset_s': 0.0, 'time_offset_search_performed': False,
        'prism_in_imu_m': lever.tolist(), 'source': 'official body_imu.yaml and original bag tf_static',
        'poses': len(est), 'trajectory_duration_s': float(est[-1, 0] - est[0, 0]),
        'path_length_m': float(np.linalg.norm(np.diff(est[:, 1:4], axis=0), axis=1).sum()),
        'published_gt_rows': len(gt), 'published_gt_gap_gt_0_1_s': int((np.diff(gt[:, 0]) > 0.1).sum()),
        'imu_direct': evaluate(reference, run / 'estimate_imu.tum', output, 'imu_direct'),
        'prism_compensated': evaluate(reference, output / 'estimate_prism.tum', output, 'prism_compensated'),
        'imu_direct_interpolated': interpolation_check(reference, run / 'estimate_imu.tum', output, 'imu_direct'),
        'prism_compensated_interpolated': interpolation_check(reference, output / 'estimate_prism.tum', output, 'prism_compensated')}
    log = (run / 'mapping.log').read_text(errors='replace')
    retrieve = list(map(int, re.findall(r'\[ VIO \] Retrieve (\d+) points', log)))
    updates = list(map(int, re.findall(r'\[ VIO \] Update (\d+) points', log)))
    runtime = log.split('[rclcpp]: signal_handler(SIGINT/SIGTERM)')[0]
    audit = {'vio_frames': len(retrieve), 'vio_nonzero_retrieval_frames': sum(n > 0 for n in retrieve),
        'vio_nonzero_update_frames': sum(n > 0 for n in updates), 'runtime_error_lines': runtime.count('[ERROR]'),
        'lidar_message_consumption': 'No per-cloud callback logs; output counts do not establish zero input loss'}
    import yaml
    cfg = yaml.safe_load((run / 'mapper.yaml').read_text())['/**']['ros__parameters']
    bag = Path(json.loads((run / 'commands.json').read_text())['player'][3])
    with sqlite3.connect(f'file:{bag / "fusionportable.db3"}?mode=ro', uri=True) as conn:
        for topic, pattern, offset in [('/stim300/imu/data_raw', r'get imu at time: ([0-9.]+)', 0.0),
            ('/left_camera/image', r'Get image, its header time: ([0-9.]+)', cfg['time_offset']['img_time_offset'])]:
            idx = conn.execute('SELECT id FROM topics WHERE name=?', (topic,)).fetchone()[0]
            stamps = np.array([s + ns/1e9 + offset for (raw,) in conn.execute(
                'SELECT substr(data,5,8) FROM messages WHERE topic_id=? ORDER BY timestamp', (idx,))
                for s, ns in [struct.unpack('<iI', raw)]])
            observed = np.sort(np.array([float(v) for v in re.findall(pattern, log)]))
            if not len(observed):
                raise RuntimeError(f'No callback logs for {topic}')
            right = np.clip(np.searchsorted(observed, stamps), 0, len(observed)-1)
            left = np.maximum(right-1, 0)
            match = np.minimum(np.abs(stamps-observed[left]), np.abs(stamps-observed[right])) <= 2e-6
            audit[topic] = dict(bag_records=len(stamps), callback_log_entries=len(observed),
                matched_records=int(match.sum()), unmatched_records=int((~match).sum()), tolerance_s=2e-6,
                configured_timestamp_offset_s=offset)
    (output / 'callback_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    results['callback_audit'] = audit
    (output / 'metrics.json').write_text(json.dumps(results, indent=2) + '\n')
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.plot(est[:, 1], est[:, 2], linewidth=0.8)
    ax.scatter(est[[0, -1], 1], est[[0, -1], 2], c=['green', 'red'], s=30)
    ax.set(xlabel='x [m]', ylabel='y [m]', title='FAST-LIVO2 estimated trajectory, IMU frame', aspect='equal')
    ax.grid(alpha=0.3)
    fig.tight_layout();fig.savefig(run / 'trajectory.png', dpi=160);plt.close(fig)
    data = np.load(run / 'sampled_map.npz')
    xyz, rgb = data['xyz'], data['rgb']
    stride = max(1, len(xyz)//120000)
    xyz, rgb = xyz[::stride], rgb[::stride]
    colors = np.column_stack([(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255])/255
    fig, ax = plt.subplots(figsize=(8, 7))
    ax.scatter(xyz[:, 0], xyz[:, 1], c=colors, s=0.3)
    ax.set(xlabel='x [m]', ylabel='y [m]', title='Sampled registered RGB clouds, top view', aspect='equal')
    fig.tight_layout();fig.savefig(run / 'map_preview.png', dpi=180);plt.close(fig)
    print(json.dumps({k: results[k] for k in ['poses', 'trajectory_duration_s', 'path_length_m']}, indent=2))
    for name in ['imu_direct', 'prism_compensated']:
        print(name, 'RMSE m', results[name]['stats_m']['rmse'], 'matches', results[name]['associated_poses'])


if __name__ == '__main__':
    main()
