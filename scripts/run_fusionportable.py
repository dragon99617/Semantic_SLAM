#!/usr/bin/env python3
"""Run an adapted FusionPortableV2 bag and archive actual outputs.

Source scripts/env.sh; then use system python3. A diagnostic input is explicitly
labelled with --diagnostic and is never reported as a complete sequence.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2, Image
from nav_msgs.msg import Odometry
import yaml

from run_reproduction import stop_mapper, stop_process


class Observer(Node):
    def __init__(self):
        super().__init__('fusionportable_observer')
        self.counts = collections.Counter()
        self.last_rgb = None
        self.cloud_samples = []
        self.max_points = 0
        self.subs = [self.create_subscription(typ, topic,
            lambda m, t=topic: self.observe(t, m), qos_profile_sensor_data)
            for topic, typ in [('/aft_mapped_to_init', Odometry),
                               ('/cloud_registered', PointCloud2), ('/rgb_img', Image)]]

    def observe(self, topic, msg):
        self.counts[topic] += 1
        if topic == '/rgb_img':
            self.last_rgb = msg
        elif topic == '/cloud_registered':
            points = msg.width * msg.height
            self.max_points = max(self.max_points, points)
            if points and self.counts[topic] % 10 == 0:
                fields = {f.name: f.offset for f in msg.fields}
                names = [n for n in ['x', 'y', 'z', 'rgb'] if n in fields]
                dtype = np.dtype({'names': names,
                    'formats': ['<u4' if n == 'rgb' else '<f4' for n in names],
                    'offsets': [fields[n] for n in names], 'itemsize': msg.point_step})
                a = np.frombuffer(msg.data, dtype=dtype)[::max(1, points // 512)]
                xyz = np.column_stack([a[n] for n in ['x', 'y', 'z']])
                rgb = a['rgb'] if 'rgb' in names else np.zeros(len(a), dtype=np.uint32)
                self.cloud_samples.append((xyz.copy(), rgb.copy()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bag', type=Path, default=Path('datasets/FusionPortableV2/ugv_transition00_humble'))
    parser.add_argument('--rate', type=float, default=0.5)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    bag = args.bag.resolve()
    prep = json.loads((bag / 'preparation.json').read_text())
    if not prep['all_cdr_messages_roundtrip_verified'] or prep['additional_lidar_time_offset_required'] != 0.0:
        raise RuntimeError('Bag has not passed expected conversion checks')
    lidar_headers = prep['headers']['/os_cloud_node/points']
    if not args.diagnostic and (lidar_headers['count'] != 4921 or
            lidar_headers['last_ns'] - lidar_headers['first_ns'] < 480 * 10**9):
        raise RuntimeError('Expected complete ugv_transition00 input; use --diagnostic for short bags')
    run = args.output or root / 'experiments/fusionportable_v2/runs' / datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    run = run.resolve()
    run.mkdir(parents=True, exist_ok=False)
    source = root / 'fast_livo2_ws/src/FAST-LIVO2'
    config_dir = root / 'experiments/fusionportable_v2/config'
    config = yaml.safe_load((config_dir / 'fusionportable_v2.yaml').read_text())
    p = config['/**']['ros__parameters']
    sequence = f'FusionPortableV2_ugv_transition00_{run.name}'
    p['evo']['seq_name'] = sequence
    assert p['time_offset']['lidar_time_offset'] == 0.0
    config_path = run / 'mapper.yaml'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    camera = run / 'camera.yaml'
    shutil.copy2(config_dir / 'camera.yaml', camera)
    shutil.copy2(bag / 'preparation.json', run / 'bag_preparation.json')
    with (run / 'source_working_changes.patch').open('w') as f:
        subprocess.run(['git', '-C', str(source), 'diff', '--binary'], stdout=f, check=True)
    prefix = Path(subprocess.check_output(['ros2', 'pkg', 'prefix', 'fast_livo'], text=True).strip())
    binary = prefix / 'lib/fast_livo/fastlivo_mapping'
    (run / 'binary.json').write_text(json.dumps({'path': str(binary),
        'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2) + '\n')
    with (run / 'bag_info.txt').open('w') as f:
        subprocess.run(['ros2', 'bag', 'info', str(bag)], stdout=f, check=True)
    mapper_cmd = ['ros2', 'launch', 'fast_livo', 'mapping_aviz.launch.py',
        'use_rviz:=False', 'use_compressed_image:=False', f'avia_params_file:={config_path}',
        f'camera_params_file:={camera}']
    player_cmd = ['ros2', 'bag', 'play', str(bag), '--rate', str(args.rate),
                  '--read-ahead-queue-size', '300']
    (run / 'commands.json').write_text(json.dumps({'mapper': mapper_cmd, 'player': player_cmd,
        'ROS_DOMAIN_ID': os.environ.get('ROS_DOMAIN_ID', '0'), 'diagnostic': args.diagnostic}, indent=2) + '\n')
    trajectory = source / 'Log/result' / f'{sequence}.txt'
    rclpy.init()
    observer = Observer()
    mapper = player = None
    completed = clean = False
    error = None
    started = time.monotonic()
    lidar = lidar_headers
    expected_first, expected_last = lidar['first_ns'] / 1e9, lidar['last_ns'] / 1e9
    (run / 'running.json').write_text(json.dumps({'started': datetime.datetime.now().isoformat(),
        'expected_sensor_span_s': expected_last - expected_first}) + '\n')
    print(f'Run directory: {run}', flush=True)
    log = (run / 'mapping.log').open('w')
    playlog = (run / 'playback.log').open('w')
    try:
        mapper = subprocess.Popen(mapper_cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 40
        while not observer.get_publishers_info_by_topic('/aft_mapped_to_init'):
            rclpy.spin_once(observer, timeout_sec=0.1)
            if mapper.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Mapper did not initialize')
        for node, name in [('/laserMapping', 'mapper_parameters.yaml'),
                           ('/parameter_blackboard', 'camera_parameters.yaml')]:
            for attempt in range(3):
                with (run / name).open('w') as f:
                    dumped = subprocess.run(['ros2', 'param', 'dump', '--no-daemon', '--spin-time', '4', node],
                                            stdout=f, timeout=25)
                if dumped.returncode == 0:
                    break
                if attempt == 2:
                    raise RuntimeError(f'Parameter discovery failed for {node}')
        player = subprocess.Popen(player_cmd, stdout=playlog, stderr=subprocess.STDOUT, start_new_session=True)
        report = time.monotonic()
        deadline = time.monotonic() + (expected_last - expected_first + 60) / args.rate + 120
        while player.poll() is None:
            rclpy.spin_once(observer, timeout_sec=0.05)
            if mapper.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Launch exited or playback timed out')
            if time.monotonic() - report >= 30:
                tail = (run / 'mapping.log').read_bytes()[-10000:].decode(errors='replace')
                if 'process has died' in tail:
                    raise RuntimeError('Mapper process died')
                poses = trajectory.read_text().splitlines() if trajectory.exists() else []
                stamp = float(poses[-1].split()[0]) if poses else None
                progress = {'elapsed_s': round(time.monotonic() - started), 'poses': len(poses),
                    'processed_sensor_s': round(stamp - expected_first, 2) if stamp else None,
                    'received_outputs': dict(observer.counts)}
                (run / 'progress.json').write_text(json.dumps(progress, indent=2) + '\n')
                print(json.dumps(progress), flush=True)
                report = time.monotonic()
        if player.returncode != 0:
            raise RuntimeError(f'Player exit {player.returncode}')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rclpy.spin_once(observer, timeout_sec=0.05)
        poses = np.loadtxt(trajectory, ndmin=2)
        if poses.shape[1] != 8 or not np.isfinite(poses).all() or not (np.diff(poses[:, 0]) > 0).all():
            raise RuntimeError('Invalid/nonmonotonic sensor-time trajectory')
        if (poses[-1, 0] - poses[0, 0]) < 0.95 * (expected_last - expected_first) or abs(poses[-1, 0] - expected_last) > 1:
            raise RuntimeError('Insufficient trajectory coverage')
        if any(not observer.counts[t] for t in ['/aft_mapped_to_init', '/cloud_registered', '/rgb_img']):
            raise RuntimeError('Missing expected actual outputs')
        completed = True
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or 'Interrupted by controller/user'
        print(f'Run failed: {error}', flush=True)
    finally:
        stop_process(player)
        if mapper:
            clean = stop_mapper(mapper, run / 'mapping.log')
        log.close()
        playlog.close()
        if trajectory.exists():
            shutil.copy2(trajectory, run / 'estimate_imu.tum')
        for name in ['mat_pre.txt', 'mat_out.txt', 'imu.txt']:
            f = source / 'Log' / name
            if f.exists():
                shutil.copy2(f, run / name)
        if observer.last_rgb is not None:
            from cv_bridge import CvBridge
            import cv2
            cv2.imwrite(str(run / 'last_tracking_image.png'),
                CvBridge().imgmsg_to_cv2(observer.last_rgb, desired_encoding='bgr8'))
        if observer.cloud_samples:
            xyz = np.concatenate([x for x, _ in observer.cloud_samples])
            rgb = np.concatenate([c for _, c in observer.cloud_samples])
            finite = np.isfinite(xyz).all(axis=1)
            xyz, rgb = xyz[finite], rgb[finite]
            _, indices = np.unique(np.floor(xyz / 0.1).astype(np.int64), axis=0, return_index=True)
            np.savez_compressed(run / 'sampled_map.npz', xyz=xyz[indices], rgb=rgb[indices])
        summary = {'status': ('diagnostic_completed' if args.diagnostic else 'playback_completed')
            if completed and clean else 'failed', 'error': error, 'diagnostic': args.diagnostic,
            'rate': args.rate, 'mapper_clean_exit': clean, 'player_returncode': player.returncode if player else None,
            'wall_seconds': time.monotonic() - started, 'received_outputs': dict(observer.counts),
            'max_cloud_points': observer.max_points, 'timestamp_basis': 'saved TUM sensor time; ROS output stamps are node time',
            'map_artifact': 'sampled output clouds, every tenth message, ~512 points/message, 10 cm deduplication',
            'run_dir': str(run)}
        (run / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        observer.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        print(json.dumps(summary, indent=2), flush=True)
    if not (completed and clean):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
