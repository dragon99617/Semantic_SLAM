#!/usr/bin/env python3
"""Join captured raw RGB, LIO poses and registered points into sparse RGB-D.

No pose interpolation, depth completion, GT input, temporal cloud accumulation
or scale fitting. Output NPZ frames carry float32 axial depth in metres.
"""
import argparse
import collections
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation
import yaml

from livo_ovo_geometry import Rectifier, camera_from_imu, nearest_stamp, offset_ns, transform, world_from_camera
from verify_phase1_handoff import sha


def prepare(capture, output, near, far):
    summary = json.loads((capture/'capture_summary.json').read_text())
    if not summary['completed']:
        raise ValueError('Only accept a successfully audited capture')
    index = json.loads((capture/'capture_index.json').read_text())
    p = yaml.safe_load((capture/'mapper.yaml').read_text())['/**']['ros__parameters']
    camera = yaml.safe_load((capture/'camera.yaml').read_text())['/**']['ros__parameters']
    rect = Rectifier(camera)
    t_ci = camera_from_imu(p)
    dt_ns = offset_ns(p['time_offset'].get('img_time_offset', 0))
    images, poses, clouds = (index[k] for k in ('images', 'poses', 'clouds'))
    pose_times = np.array([r['stamp_ns'] for r in poses], dtype=np.int64)
    cloud_times = np.array([r['stamp_ns'] for r in clouds], dtype=np.int64)
    image_times = np.array([r['stamp_ns'] for r in images], dtype=np.int64)
    for stamps in (pose_times, cloud_times, image_times):
        if not len(stamps) or not (np.diff(stamps) > 0).all():
            raise ValueError('All captured streams must be nonempty and strictly monotonic')
    native = np.loadtxt(capture/'estimate_imu.tum', ndmin=2)
    if len(native) != len(poses):
        raise ValueError('Capture dropped odometry messages relative to native trajectory')
    observed = np.array([[*r['position'], *r['quaternion_xyzw']] for r in poses])
    if not np.allclose(observed, native[:, 1:], atol=1e-6, rtol=0):
        raise ValueError('Native and ROS pose values do not match')
    if np.max(np.abs(pose_times/1e9-native[:, 0])) > 1e-6:
        raise ValueError('Native and ROS pose times do not match')
    output.mkdir(parents=True, exist_ok=False)
    (output/'frames').mkdir()
    rows, rejected = [], []
    max_pixel_error = max_z_error = 0.
    used_poses, used_clouds = set(), set()
    for image_id, image in enumerate(images):
        query = image['stamp_ns']+dt_ns
        pi = nearest_stamp(pose_times, query)
        ci = nearest_stamp(cloud_times, query)
        if pi is None or ci is None:
            rejected.append(dict(image_id=image_id, image_stamp_ns=image['stamp_ns'],
                                 reason='missing_same_frame_pose' if pi is None else 'missing_same_frame_cloud'))
            continue
        if pi in used_poses or ci in used_clouds or pose_times[pi] != cloud_times[ci]:
            raise ValueError('Same-frame pose/cloud pairing must be one-to-one with identical sensor stamps')
        used_poses.add(pi)
        used_clouds.add(ci)
        pose = poses[pi]
        q = np.asarray(pose['quaternion_xyzw'])
        if abs(np.linalg.norm(q)-1) > 1e-6:
            raise ValueError('Invalid quaternion')
        t_wi = transform(Rotation.from_quat(q).as_matrix(), pose['position'])
        t_wc = world_from_camera(t_wi, t_ci)
        xyz = np.load(capture/clouds[ci]['path'])
        depth, provenance = rect.project(xyz, t_wc, near, far)
        mask = depth > 0
        if not mask.any():
            rejected.append(dict(image_id=image_id, image_stamp_ns=image['stamp_ns'], reason='no_valid_projected_depth'))
            continue
        rgb = rect.rgb(np.load(capture/image['path']))
        yy, xx = np.nonzero(mask)
        source = xyz[provenance[mask]].astype(np.float64)
        # Independent homogeneous inverse, rather than the row-vector projector.
        camera_xyz = (np.linalg.inv(t_wc) @ np.column_stack([source, np.ones(len(source))]).T).T[:, :3]
        projected, _ = cv2.projectPoints(camera_xyz, np.zeros(3), np.zeros(3), rect.k, np.zeros(4))
        pixel_error = float(np.max(np.abs(projected.reshape(-1, 2)-np.column_stack([xx, yy]))))
        z_error = float(np.max(np.abs(camera_xyz[:, 2]-depth[mask])))
        if pixel_error > .50001 or z_error > 1e-4:
            raise ValueError('Projection/axial-depth round-trip check failed')
        max_pixel_error = max(max_pixel_error, pixel_error)
        max_z_error = max(max_z_error, z_error)
        fid = len(rows)
        path = f'frames/{fid:06d}.npz'
        np.savez_compressed(output/path, rgb=rgb, depth=depth, valid=mask, c2w=t_wc,
                            image_stamp_ns=np.int64(image['stamp_ns']), state_stamp_ns=pose_times[pi])
        rows.append(dict(frame_id=fid, image_id=image_id, path=path, sha256=sha(output/path),
                         image_stamp_ns=image['stamp_ns'], image_in_imu_ns=query,
                         pose_stamp_ns=int(pose_times[pi]), cloud_stamp_ns=int(cloud_times[ci]),
                         sync_residual_ns=int(pose_times[pi])-query, valid_pixels=int(mask.sum()),
                         valid_depth_fraction=float(mask.mean()), cloud_points=len(xyz),
                         min_depth_m=float(depth[mask].min()), max_depth_m=float(depth[mask].max())))
        if fid % 50 == 0:
            view = rgb.copy()
            values = cv2.applyColorMap(np.uint8(np.clip(depth/far*255, 0, 255)), cv2.COLORMAP_TURBO)
            view[mask] = values[:, :, ::-1][mask]
            cv2.imwrite(str(output/f'projection_{fid:06d}.jpg'), cv2.cvtColor(view, cv2.COLOR_RGB2BGR))
    if not rows:
        raise ValueError('No synchronized frames')
    with (output/'frame_audit.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output/'rejected_frames.json').write_text(json.dumps(rejected, indent=2)+'\n')
    manifest = dict(schema='livo_ovo_sparse_v1', completed=True, capture=str(capture),
                    capture_index_sha256=sha(capture/'capture_index.json'),
                    source_camera_sha256=sha(capture/'camera.yaml'), source_mapper_sha256=sha(capture/'mapper.yaml'),
                    short_segment=summary['short_segment'], pose_source='FAST-LIVO2_estimated',
                    pose_stage=summary['pose_stage'], camera_axes='right_down_forward',
                    depth_convention='axial_z_metres_float32_zero_invalid',
                    world_frame='camera_init', rectified_intrinsics=rect.k.tolist(),
                    width=rect.width, height=rect.height, original_camera_model=rect.model,
                    T_camera_imu=t_ci.tolist(), image_to_imu_offset_ns=dt_ns,
                    join_tolerance_ns=2000, depth_range_m=[near, far],
                    no_depth_completion=True, no_temporal_accumulation=True,
                    input_images=len(images), input_poses=len(poses), input_clouds=len(clouds),
                    frames=rows, rejected=len(rejected),
                    rejected_reasons=dict(collections.Counter(r['reason'] for r in rejected)),
                    max_sync_residual_ns=max(abs(r['sync_residual_ns']) for r in rows),
                    mean_valid_depth_fraction=float(np.mean([r['valid_depth_fraction'] for r in rows])),
                    projection_crosscheck=dict(max_pixel_error=max_pixel_error, max_z_error_m=max_z_error,
                                               native_ros_pose_values_match=True, passed=True))
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'frames'}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--near', type=float, default=.1)
    p.add_argument('--far', type=float, default=50.)
    a = p.parse_args()
    cv2.setNumThreads(1)
    prepare(a.capture.resolve(), a.output.resolve(), a.near, a.far)


if __name__ == '__main__':
    main()
