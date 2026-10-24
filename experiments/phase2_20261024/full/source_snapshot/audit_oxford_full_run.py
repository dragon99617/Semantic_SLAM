#!/usr/bin/env python3
"""Audit full run coverage and frozen transition windows; GT is geometry only."""
import argparse
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np
import torch
from scipy.spatial import cKDTree

from verify_phase1_handoff import sha

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'experiments/phase2_20261024/full'
WINDOWS = dict(before=(-30, -10), transition=(-10, 10), after=(10, 30))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('sequence', choices=('oxford02', 'oxford05'))
    seq = ap.parse_args().sequence
    capture, data, run = BASE/'captures'/f'{seq}_full_r1', BASE/'inputs'/seq, BASE/'runs'/seq
    c = json.loads((capture/'capture_summary.json').read_text())
    m = json.loads((data/'manifest.json').read_text())
    r = json.loads((run/'runtime.json').read_text())
    v = json.loads((run/'map_checks.json').read_text())
    ann_path = ROOT/'experiments/phase1_20261022/input_annotations'/seq/'events.json'
    ann = json.loads(ann_path.read_text())
    index = json.loads((capture/'capture_index.json').read_text())
    native = np.loadtxt(capture/'estimate_imu.tum', ndmin=2)
    with np.load(capture/'expected_input_headers.npz') as headers:
        expected_lidar = headers['/hesai/pandar']
        span = (int(expected_lidar[-1])-int(expected_lidar[0]))/1e9
        expected_counts = {k: len(headers[k]) for k in headers.files}
    observed = np.array([[p['stamp_ns']/1e9, *p['position'], *p['quaternion_xyzw']] for p in index['poses']])
    np.savetxt(capture/'observed_odometry.tum', observed, fmt='%.9f')
    compatibility = dict(status='playback_completed' if c['completed'] else 'failed', diagnostic=False,
                         expected_lidar_span_s=span, input_callback_audit={
                             t: dict(expected=entry['expected'], received_callbacks=entry['received'],
                                     received_is_exact_multiset=entry['exact']) for t, entry in c['input_audit'].items()})
    (capture/'summary.json').write_text(json.dumps(compatibility, indent=2)+'\n')
    subprocess.run([str(ROOT/'third_party/accuracy_venv/bin/python'), str(ROOT/'scripts/evaluate_livo_baseline.py'), str(capture)], check=True)
    geometry = json.loads((capture/'accuracy/metrics.json').read_text())
    raw = np.array([p['stamp_ns']/1e9 for p in index['images']])
    accepted = np.array([p['image_stamp_ns']/1e9 for p in m['frames']])
    sem_times = accepted[np.asarray(r['segmented_frame_ids'], int)]
    trace = json.loads((run/'mapping_trace.json').read_text())
    prediction_index = json.loads((run/'transition_predictions/index.json').read_text())
    annotations = json.loads((BASE/'annotations/oxford_transitions_v1/manifest.json').read_text())
    requested = [s for s in annotations['samples'] if s['sequence'] == seq]
    declared_camera = next(s for s in annotations['sources'] if s['sequence'] == seq)['camera_sha256']
    accepted_by_ns = {f['image_stamp_ns']: f for f in m['frames']}
    annotation_rgb_matches = True
    compared_rgb = 0
    for sample in requested:
        if sample['image_stamp_ns'] in accepted_by_ns:
            frame = accepted_by_ns[sample['image_stamp_ns']]
            with np.load(data/frame['path']) as bundle:
                expected_rgb = bundle['rgb']
            image = cv2.cvtColor(cv2.imread(str(BASE/'annotations/oxford_transitions_v1'/sample['image_path'])), cv2.COLOR_BGR2RGB)
            annotation_rgb_matches = annotation_rgb_matches and np.array_equal(image, expected_rgb)
            compared_rgb += 1
    events = []
    for event in ann['events']:
        row = dict(event_id=event['id'], primary_indoor_outdoor=event['primary_indoor_outdoor'],
                   from_environment=event['from_environment'], to_environment=event['to_environment'], windows={})
        for window, (begin, end) in WINDOWS.items():
            lo, hi = event['center_sensor_s']+begin, event['center_sensor_s']+end
            inside = lambda values: (values >= lo) & (values < hi)
            a, b, d = int(inside(raw).sum()), int(inside(accepted).sum()), int(inside(sem_times).sum())
            ids = [s['sample_id'] for s in requested if any(x['event_id'] == event['id'] and x['window'] == window for x in s['memberships'])]
            predictions = [p for p in prediction_index if p['sample_id'] in ids]
            row['windows'][window] = dict(sensor_interval_s=[lo, hi], raw_images=a, accepted_rgbd=b,
                accepted_fraction=b/a if a else None, semantic_updates=d,
                truncated=lo < raw[0] or hi > raw[-1],
                overlaps=[e['id'] for e in ann['events'] if e['id'] != event['id'] and e['interval_start_sensor_s'] < hi and e['interval_end_sensor_s'] >= lo],
                annotation_images=len(ids), causal_prediction_images=len(predictions),
                predicted_pixels=sum(p['predicted_pixels'] for p in predictions),
                semantic_accuracy=None, semantic_accuracy_status='awaiting_independent_annotation')
        events.append(row)
    checks = dict(full_capture=c['completed'] and not c['short_segment'],
                  full_sensor_counts=all(c['input_audit'][k]['expected'] == n and c['input_audit'][k]['exact'] for k, n in expected_counts.items()),
                  native_pose_span_over_99_percent=(native[-1, 0]-native[0, 0])/span >= .99,
                  native_pose_count_matches=len(native) == len(index['poses']),
                  all_accepted_frames_traversed=r['completed'] and r['processed_frames'] == len(m['frames']),
                  projection_roundtrip=m['projection_crosscheck']['passed'], finite_saved_map_and_queries=v['passed'],
                  every_transition_has_semantic_updates=all(e['windows']['transition']['semantic_updates'] > 0 for e in events),
                  map_grows=all(t['map_points'] >= 0 and t['points_added'] >= 0 for t in trace) and r['mapping_frames_with_new_points'] > 1,
                  all_blind_sample_predictions_exported={p['sample_id'] for p in prediction_index} == {s['sample_id'] for s in requested},
                  prediction_causality=all(p['map_image_stamp_ns'] is None or p['map_image_stamp_ns'] <= p['raw_image_stamp_ns'] for p in prediction_index))
    checks['annotation_camera_and_rgb_match'] = declared_camera == m['source_camera_sha256'] and annotation_rgb_matches and compared_rgb > 0
    checkpoint = torch.load(run/'ovo_map.ckpt', map_location='cpu', weights_only=False)
    xyz = checkpoint['map_params']['xyz'].numpy()
    alignment = geometry['comparisons']['base']['nearest']
    aligned = xyz@np.array(alignment['alignment_R']).T+np.array(alignment['alignment_t'])
    with np.load(ROOT/'datasets/OxfordSpires/christ_church_tls_5cm.npz') as tls:
        reference_xyz = tls['xyz']
    tree = cKDTree(reference_xyz)
    distance = np.concatenate([tree.query(aligned[i:i+100000], k=1, workers=2)[0] for i in range(0, len(aligned), 100000)])
    tls_check = dict(points=len(distance), mean_m=float(distance.mean()), median_m=float(np.median(distance)),
                     p95_m=float(np.percentile(distance, 95)), rmse_m=float(np.sqrt(np.mean(distance**2))),
                     fraction_within_0_2_m=float((distance <= .2).mean()), fraction_within_0_5_m=float((distance <= .5).mean()),
                     alignment='Reuse the full-trajectory SE3; no map ICP, no scale fitting or distance trimming',
                     limitation='Sparse observed map to 5cm TLS; reference coverage/occlusion affect distances; not semantic accuracy or full-scene completeness')
    checks['finite_registered_map_tls_distances'] = bool(np.isfinite(distance).all())
    report = dict(sequence=seq, engineering_passed=all(checks.values()), checks=checks,
                  semantic_accuracy_status='awaiting_independent_annotation', semantic_accuracy_accepted=False,
                  pose_count=len(native), input_images=len(raw), accepted_frames=len(accepted),
                  mean_valid_depth_fraction=m['mean_valid_depth_fraction'], map_points=r['map_points'],
                  semantic_points=r['assigned_points'], objects=r['objects'], max_sync_residual_ns=m['max_sync_residual_ns'],
                  trajectory_coverage=(native[-1, 0]-native[0, 0])/span,
                  nearest_ape_rmse_m=geometry['comparisons']['base']['nearest']['stats_m']['rmse'],
                  interpolated_ape_rmse_m=geometry['comparisons']['base']['interpolated']['rmse_m'],
                  rpe_translation_rmse_m=geometry['RPE']['translation_m']['stats']['rmse'],
                  rpe_rotation_rmse_deg=geometry['RPE']['rotation_deg']['stats']['rmse'],
                  annotation_rgb_images_compared=compared_rgb, integrated_map_tls_check=tls_check,
                  events=events, frozen_events_sha256=sha(ann_path),
                  map_checks_sha256=sha(run/'map_checks.json'), limitations=['No independent semantic labels yet', 'Coverage and geometry metrics do not constitute semantic accuracy'])
    (run/'full_audit.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'events'}, indent=2))
    if not report['engineering_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
