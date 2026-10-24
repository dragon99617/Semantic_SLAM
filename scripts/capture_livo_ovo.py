#!/usr/bin/env python3
"""Capture synchronized integration inputs from an unchanged frozen mapper.

Source scripts/env.sh; use system Python. A bounded slice always starts at the
bag beginning for estimator initialization. --seconds 0 selects the whole bag.
Never feed reference poses to the mapper. OVO runs separately after capture.
"""
import argparse
import collections
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, PointCloud2
import yaml

from livo_ovo_geometry import decode_cloud, decode_image
from run_livo_baseline import sha, tail_bytes
from run_reproduction import stop_mapper, stop_process

ROOT = Path(__file__).resolve().parents[1]


def stamp(msg):
    return msg.header.stamp.sec*10**9 + msg.header.stamp.nanosec


def slice_bag(source, target, seconds, topic_names):
    """Copy CDR payloads verbatim; preserve source storage/header timestamps."""
    import rosbag2_py
    meta = yaml.safe_load((source/'metadata.yaml').read_text())['rosbag2_bagfile_information']
    start = int(meta['starting_time']['nanoseconds_since_epoch'])
    stop = start + round(seconds*1e9)
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(uri=str(target), storage_id='sqlite3'),
                rosbag2_py.ConverterOptions('', ''))
    topic_info = {t['topic_metadata']['name']: t['topic_metadata'] for t in meta['topics_with_message_count']}
    for name in topic_names:
        t = topic_info[name]
        writer.create_topic(rosbag2_py.TopicMetadata(name=name, type=t['type'],
                            serialization_format='cdr', offered_qos_profiles=t.get('offered_qos_profiles', '')))
    headers = collections.defaultdict(list)
    for part in meta['relative_file_paths']:
        with sqlite3.connect(f'file:{source/part}?mode=ro', uri=True) as db:
            ids = {i: n for i, n in db.execute('SELECT id,name FROM topics') if n in topic_names}
            marks = ','.join('?' for _ in ids)
            for topic_id, timestamp, raw in db.execute(
                    f'SELECT topic_id,timestamp,data FROM messages WHERE timestamp<=? AND topic_id IN ({marks}) ORDER BY timestamp,id',
                    (stop, *ids)):
                name = ids[topic_id]
                writer.write(name, raw, timestamp)
                sec, ns = struct.unpack_from('<iI', raw, 4)
                headers[name].append(sec*10**9+ns)
    del writer
    return headers


class Capture(Node):
    def __init__(self, output, image_topic):
        super().__init__('livo_ovo_capture')
        self.output = output
        (output/'raw').mkdir()
        self.images, self.poses, self.clouds = [], [], []
        self.counts = collections.Counter()
        self.last_rx = time.monotonic()
        qos = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
        self.subs = [self.create_subscription(typ, topic, fn, qos) for typ, topic, fn in (
            (Image, image_topic, self.image), (Odometry, '/aft_mapped_to_init', self.pose),
            (PointCloud2, '/cloud_registered', self.cloud))]

    def image(self, msg):
        t = stamp(msg)
        path = f'raw/rgb_{len(self.images):06d}.npy'
        np.save(self.output/path, decode_image(msg))
        self.images.append({'stamp_ns': t, 'path': path, 'encoding': msg.encoding})
        self.counts['images'] += 1
        self.last_rx = time.monotonic()

    def pose(self, msg):
        if msg.header.frame_id != 'camera_init':
            raise ValueError('Unexpected odometry world frame')
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        self.poses.append({'stamp_ns': stamp(msg), 'position': [p.x, p.y, p.z],
                           'quaternion_xyzw': [q.x, q.y, q.z, q.w]})
        self.counts['poses'] += 1
        self.last_rx = time.monotonic()

    def cloud(self, msg):
        self.counts['cloud_publications'] += 1
        if not msg.width*msg.height:
            self.counts['empty_cloud_publications'] += 1
            return  # In LIVO mode the frozen mapper's LIO publication is empty.
        if msg.header.frame_id != 'camera_init' or 'rgb' not in {f.name for f in msg.fields}:
            raise ValueError('Expected LIVO registered XYZRGB world cloud')
        path = f'raw/cloud_{len(self.clouds):06d}.npy'
        xyz = decode_cloud(msg)
        np.save(self.output/path, xyz.astype(np.float32))
        self.clouds.append({'stamp_ns': stamp(msg), 'path': path, 'points': len(xyz)})
        self.counts['nonempty_clouds'] += 1
        self.last_rx = time.monotonic()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('descriptor', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seconds', type=float, default=16)
    ap.add_argument('--rate', type=float, default=.5)
    a = ap.parse_args()
    if a.seconds < 0 or a.rate <= 0:
        ap.error('seconds must be nonnegative; rate must be positive')
    cv2.setNumThreads(1)
    lock = (ROOT/'experiments/.livo_capture.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    active = subprocess.run(['pgrep', '-x', 'fastlivo_mappin'], capture_output=True)
    if active.returncode == 0:
        raise RuntimeError('A mapper is already running; shared native log files must be isolated')
    desc = json.loads(a.descriptor.read_text())
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((ROOT/desc['mapper_config']).read_text())
    p = config['/**']['ros__parameters']
    if p['common']['img_en'] != 1 or p['publish']['pub_scan_num'] != 1:
        raise ValueError('Capture requires LIVO and per-frame publication')
    freeze = json.loads((ROOT/'experiments/phase1_20261022/baseline_freeze.json').read_text())
    for path, digest in freeze['artifacts'].items():
        if sha(ROOT/path) != digest:
            raise ValueError(f'Frozen binary/library mismatch: {path}')
    name = f'Phase2_{desc["id"]}_{output.name}'
    p['evo'].update(seq_name=name, pose_output_en=True)
    p['pcd_save'].update(pcd_save_en=False, colmap_output_en=False)
    p['image_save']['img_save_en'] = False
    (output/'mapper.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    shutil.copy2(ROOT/desc['camera_config'], output/'camera.yaml')
    shutil.copy2(a.descriptor, output/'descriptor.json')
    topics = [p['common'][k] for k in ('lid_topic', 'imu_topic', 'img_topic')]
    source_bag = ROOT/desc['bag']
    if a.seconds:
        headers = slice_bag(source_bag, output/'input_bag', a.seconds, topics)
        bag = output/'input_bag'
    else:
        from run_livo_baseline import input_headers
        headers = input_headers(source_bag, topics)
        bag = source_bag
    np.savez_compressed(output/'expected_input_headers.npz', **{t: np.asarray(v, dtype=np.int64) for t, v in headers.items()})
    if any(not len(headers[t]) for t in topics):
        raise ValueError('Missing sensor input')
    qos_path = output/'playback_qos.yaml'
    qos_path.write_text(yaml.safe_dump({t: dict(history='keep_last', depth=1000, reliability='reliable', durability='volatile') for t in topics}))
    mapper_cmd = ['ros2', 'launch', 'fast_livo', 'mapping_aviz.launch.py', 'use_rviz:=False',
                  'use_compressed_image:=False', f'avia_params_file:={output/"mapper.yaml"}',
                  f'camera_params_file:={output/"camera.yaml"}']
    player_cmd = ['ros2', 'bag', 'play', str(bag), '--rate', str(a.rate), '--start-paused',
                  '--read-ahead-queue-size', '300', '--wait-for-all-acked', '5000',
                  '--qos-profile-overrides-path', str(qos_path)]
    (output/'commands.json').write_text(json.dumps(dict(mapper=mapper_cmd, player=player_cmd,
        ROS_DOMAIN_ID=os.environ.get('ROS_DOMAIN_ID'), frozen_artifacts=freeze['artifacts']), indent=2)+'\n')
    source = ROOT/'fast_livo2_ws/src/FAST-LIVO2'
    native = source/'Log/result'/f'{name}.txt'
    if native.exists():
        raise FileExistsError(native)
    rclpy.init()
    node = Capture(output, topics[2])
    mapper = player = None
    error = None
    clean = False
    started = time.monotonic()
    log = (output/'mapping.log').open('w')
    playlog = (output/'playback.log').open('w')
    pacing_events = []
    try:
        mapper = subprocess.Popen(mapper_cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic()+50
        while not node.get_publishers_info_by_topic('/aft_mapped_to_init'):
            rclpy.spin_once(node, timeout_sec=.1)
            if mapper.poll() is not None or time.monotonic() > deadline or 'process has died' in (output/'mapping.log').read_text():
                raise RuntimeError('Mapper startup failed')
        for name_, file in [('/laserMapping', 'mapper_parameters.yaml'), ('/parameter_blackboard', 'camera_parameters.yaml')]:
            with (output/file).open('w') as f:
                subprocess.run(['ros2', 'param', 'dump', '--no-daemon', '--spin-time', '4', name_], stdout=f, check=True, timeout=30)
        player = subprocess.Popen(player_cmd, stdout=playlog, stderr=subprocess.STDOUT, start_new_session=True)
        from rosbag2_interfaces.srv import Resume, Pause
        resume = node.create_client(Resume, '/rosbag2_player/resume')
        pause = node.create_client(Pause, '/rosbag2_player/pause')
        if not resume.wait_for_service(timeout_sec=30):
            raise RuntimeError('Player service unavailable')
        discover = time.monotonic()+3
        while time.monotonic() < discover:
            rclpy.spin_once(node, timeout_sec=.05)
        for t in topics:
            if not node.get_publishers_info_by_topic(t):
                raise RuntimeError(f'Input publisher missing: {t}')
        future = resume.call_async(Resume.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=15)
        if future.result() is None:
            raise RuntimeError('Resume failed')
        paused = False
        pause_at = paused_s = 0.
        check_at = report_at = time.monotonic()
        span_s = (int(headers[topics[0]][-1])-int(headers[topics[0]][0]))/1e9
        limit = time.monotonic()+span_s/a.rate+240
        while player.poll() is None:
            rclpy.spin_once(node, timeout_sec=.01)
            if mapper.poll() is not None or time.monotonic() > limit+paused_s+(time.monotonic()-pause_at if paused else 0):
                raise RuntimeError('Mapper stopped or capture timeout')
            if time.monotonic()-check_at >= 1:
                check_at = time.monotonic()
                text = tail_bytes(output/'mapping.log', 16000).decode(errors='replace')
                if 'process has died' in text or 'imu time stamp Jumps' in text:
                    raise RuntimeError('Mapper failure or IMU transport gap')
                lag = ((node.images[-1]['stamp_ns']/1e9 + p['time_offset'].get('img_time_offset', 0)) -
                       node.poses[-1]['stamp_ns']/1e9) if node.images and node.poses else None
                action = 'pause' if not paused and lag is not None and lag > 2 else (
                    'resume' if paused and time.monotonic()-pause_at >= 2 and lag is not None and lag <= .25 else None)
                if action:
                    future = pause.call_async(Pause.Request()) if action == 'pause' else resume.call_async(Resume.Request())
                    rclpy.spin_until_future_complete(node, future, timeout_sec=10)
                    if future.result() is None:
                        raise RuntimeError('Pacing service failed')
                    if action == 'pause':
                        pause_at = time.monotonic()
                        paused = True
                    else:
                        paused_s += time.monotonic()-pause_at
                        paused = False
                    pacing_events.append(dict(action=action, lag_s=lag, wall_s=time.monotonic()-started))
            if time.monotonic()-report_at > 20:
                print(json.dumps(dict(wall_s=round(time.monotonic()-started), counts=dict(node.counts))), flush=True)
                report_at = time.monotonic()
        if player.returncode:
            raise RuntimeError(f'Player exit {player.returncode}')
        drain_start = time.monotonic()
        while time.monotonic()-drain_start < 30:
            rclpy.spin_once(node, timeout_sec=.05)
            if time.monotonic()-drain_start > 5 and time.monotonic()-node.last_rx > 3:
                break
    except (Exception, KeyboardInterrupt) as e:
        error = str(e) or 'Interrupted'
    finally:
        stop_process(player)
        if mapper:
            clean = stop_mapper(mapper, output/'mapping.log')
        log.close()
        playlog.close()
        if native.exists():
            shutil.copy2(native, output/'estimate_imu.tum')
        text = (output/'mapping.log').read_text(errors='replace')
        audit = {}
        for tag, topic in zip(('LIDAR', 'IMU', 'IMAGE'), topics):
            got = [int(t) for t in re.findall(r'RX_'+tag+r' (\d+)', text)]
            audit[topic] = dict(expected=len(headers[topic]), received=len(got), exact=sorted(got) == sorted(map(int, headers[topic])))
        image_receipt = sorted(i['stamp_ns'] for i in node.images) == sorted(map(int, headers[topics[2]]))
        (output/'capture_index.json').write_text(json.dumps(dict(images=node.images, poses=node.poses, clouds=node.clouds), indent=2)+'\n')
        report = dict(completed=error is None and clean and all(r['exact'] for r in audit.values()) and image_receipt,
                      short_segment=bool(a.seconds), requested_seconds=a.seconds,
                      source_bag=str(source_bag), input_audit=audit, capture_images_exact=image_receipt,
                      counts=dict(node.counts), mapper_clean_exit=clean, error=error,
                      wall_seconds=time.monotonic()-started, pacing_events=pacing_events,
                      pose_stage='post_LIO_pre_same_frame_VIO', cloud_source='same_state_world_XYZ_colored_in_VIO',
                      limitation='Registered cloud is image-filtered by upstream VIO projection, not a complete raw scan; last input frames may lack a future LiDAR bracket.')
        (output/'capture_summary.json').write_text(json.dumps(report, indent=2)+'\n')
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        print(json.dumps(report, indent=2), flush=True)
    if not report['completed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
