#!/usr/bin/env python3
"""Create candidate FAST-LIVO2 ROS 2 parameters for FPV2 ugv_transition00.

Run with third_party/accuracy_venv/bin/python after downloading the official
20230426 calibration files. This does not establish sensor compatibility:
the original bag and its actual per-point timing still need inspection.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
import yaml


def transform(calibration, target):
    # Official TFGraph stores parent<-child matrices. The ouster YAML holds
    # T_lidar_body and T_lidar_camera; FAST-LIVO2 needs their inverses.
    q = calibration[f'quaternion_sensor_{target}']['data']
    r = Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()
    t = np.array(calibration[f'translation_sensor_{target}']['data'])
    return r.T, -r.T @ t


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-scale', type=float, default=1.0)
    parser.add_argument('--sliding-half-map-voxels', type=int, default=0)
    parser.add_argument('--sliding-threshold-m', type=float, default=2.0)
    args = parser.parse_args()
    if not 0 < args.image_scale <= 1 or args.sliding_half_map_voxels < 0:
        parser.error('Require 0 < image-scale <= 1 and nonnegative sliding half map size')
    root = Path(__file__).resolve().parent.parent
    calibration = root / 'datasets/FusionPortableV2/calibration/20230426_calib/calib'
    output = root / 'experiments/fusionportable_v2/config'
    output.mkdir(parents=True, exist_ok=True)
    lidar = yaml.safe_load((calibration / 'ouster00.yaml').read_text())
    camera = yaml.safe_load((calibration / 'frame_cam00.yaml').read_text())
    config = yaml.safe_load((root / 'fast_livo2_ws/src/FAST-LIVO2/config/NTU_VIRAL.yaml').read_text())
    p = config['/**']['ros__parameters']
    p['common'].update(img_topic='/left_camera/image', lid_topic='/os_cloud_node/points',
                       imu_topic='/stim300/imu/data_raw')
    ri, ti = transform(lidar, 'body_imu')
    rc, tc = transform(lidar, 'frame_cam00')
    p['extrin_calib'] = dict(extrinsic_T=ti.tolist(), extrinsic_R=ri.ravel().tolist(),
                            Rcl=rc.ravel().tolist(), Pcl=tc.tolist())
    p['preprocess'].update(scan_line=128, blind=0.5)
    # prepare_fusionportable_bag.py already converts end headers to scan start
    # using each scan's actual max(t). Do not apply a second -0.1 s shift.
    p['time_offset'].update(lidar_time_offset=0.0, imu_time_offset=0.0,
                            img_time_offset=camera['timeshift_sensor_body_imu'])
    p['evo'].update(seq_name='FusionPortableV2_ugv_transition00', pose_output_en=True)
    p['pcd_save']['pcd_save_en'] = False
    p['image_save']['img_save_en'] = False
    if args.sliding_half_map_voxels:
        p['local_map'].update(map_sliding_en=True, half_map_size=args.sliding_half_map_voxels,
                              sliding_thresh=args.sliding_threshold_m)
    (output / 'fusionportable_v2.yaml').write_text(
        '# For prepare_fusionportable_bag.py output with scan-start headers.\n' +
        yaml.safe_dump(config, sort_keys=False))
    k = np.array(camera['camera_matrix']['data']).reshape(3, 3)
    d = camera['distortion_coefficients']['data']
    cp = dict(cam_model='Pinhole', cam_width=camera['image_width'],
        cam_height=camera['image_height'], scale=args.image_scale, cam_fx=float(k[0, 0]),
        cam_fy=float(k[1, 1]), cam_cx=float(k[0, 2]), cam_cy=float(k[1, 2]))
    cp.update({f'cam_d{i}': float(d[i]) for i in range(4)})
    (output / 'camera.yaml').write_text(yaml.safe_dump({'/**': {'ros__parameters': cp}}, sort_keys=False))
    metadata = {'status': 'candidate_not_dataset_validated',
        'calibration_repository': 'https://github.com/fusionportable/calibration_files',
        'calibration_commit': '20f1b4d867dfedcdd1d4c81578f82f65a85e4ea2',
        'calibration_directory': '20230426_calib/calib',
        'transform_convention': 'Invert T_lidar_body and T_lidar_camera as used by official TFGraph',
        'input_contract': 'prepare_fusionportable_bag.py; clouds shifted by actual max(t), JPEG decoded to BGR8',
        'image_scale': args.image_scale,
        'sliding_map': p['local_map'],
        'sliding_map_half_extent_m': args.sliding_half_map_voxels * p['lio']['voxel_size'] if args.sliding_half_map_voxels else None,
        'unverified': ['End-to-end trajectory accuracy',
            'Camera calibrated offset is a fixed initial value; varying offsets are not corrected',
            'Image decoding and ROS 1 to ROS 2 conversion have not been performed',
            'Published GT reference point/lever arm must be confirmed before scoring'],
        'estimator_base': 'community NTU_VIRAL.yaml; no accuracy tuning performed'}
    (output / 'provenance.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    main()
