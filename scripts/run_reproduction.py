#!/usr/bin/env python3
"""Run the mapper and the full bag, observe actual topics, and archive outputs.

Run after sourcing scripts/env.sh. Uses raw RGB Retail_Street_humble input.
"""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import Image, Imu, PointCloud2
from nav_msgs.msg import Odometry
from livox_ros_driver2.msg import CustomMsg


class Observer(Node):
    def __init__(self, run_dir):
        super().__init__('reproduction_observer')
        self.counts = collections.Counter()
        self.first_stamp = {}
        self.last_stamp = {}
        self.last_rgb = None
        self.last_cloud_points = 0
        self.max_cloud_points = 0
        self.nonempty_cloud_messages = 0
        self.trajectory = (run_dir / 'observed_odometry_wall_time.tum').open('w')
        self.subscriptions_kept = []
        for name, msg_type in (
            ('/livox/imu', Imu), ('/livox/lidar', CustomMsg),
            ('/left_camera/image', Image), ('/aft_mapped_to_init', Odometry),
            ('/cloud_registered', PointCloud2), ('/rgb_img', Image),
        ):
            self.subscriptions_kept.append(self.create_subscription(
                msg_type, name, lambda msg, topic=name: self.observe(topic, msg), 1000
            ))

    def observe(self, topic, msg):
        self.counts[topic] += 1
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.first_stamp.setdefault(topic, stamp)
        self.last_stamp[topic] = stamp
        if topic == '/aft_mapped_to_init':
            p = msg.pose.pose.position
            q = msg.pose.pose.orientation
            self.trajectory.write(f'{stamp:.9f} {p.x:.9f} {p.y:.9f} {p.z:.9f} '
                                  f'{q.x:.9f} {q.y:.9f} {q.z:.9f} {q.w:.9f}\n')
            self.trajectory.flush()
        elif topic == '/rgb_img':
            self.last_rgb = msg
        elif topic == '/cloud_registered':
            self.last_cloud_points = msg.width * msg.height
            self.max_cloud_points = max(self.max_cloud_points, self.last_cloud_points)
            self.nonempty_cloud_messages += self.last_cloud_points > 0


def stop_process(process, timeout=45):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)


def stop_mapper(launch_process, log_path):
    # Stop the mapper first and let launch report its real exit status before
    # stopping the camera parameter server and launch itself.
    text = log_path.read_text(errors='replace')
    matches = re.findall(r'\[fastlivo_mapping-\d+\]: process started with pid \[(\d+)\]', text)
    clean_exit = False
    if matches:
        pid = int(matches[-1])
        proc = Path(f'/proc/{pid}')
        if proc.exists():
            os.kill(pid, signal.SIGINT)
            deadline = time.monotonic() + 45
            while proc.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        time.sleep(0.2)
        text = log_path.read_text(errors='replace')
        clean_exit = bool(re.search(r'\[fastlivo_mapping-\d+\]: process has finished cleanly', text))
    stop_process(launch_process)
    return clean_exit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rate', type=float, default=0.5)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    run_dir = root / 'experiments/runs' / datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir.mkdir(parents=True)
    config = root / 'experiments/config/retail_street.yaml'
    bag = root / 'datasets/Retail_Street_humble'
    shutil.copy2(config, run_dir / config.name)
    subprocess.run(['ros2', 'bag', 'info', str(bag)], check=True,
                   stdout=(run_dir / 'bag_info.txt').open('w'))
    mapper_command = ['ros2', 'launch', 'fast_livo', 'mapping_aviz.launch.py',
                      'use_rviz:=False', 'use_compressed_image:=False',
                      f'avia_params_file:={config}']
    player_command = ['ros2', 'bag', 'play', str(bag), '--rate', str(args.rate)]
    (run_dir / 'commands.json').write_text(json.dumps({
        'mapper': mapper_command, 'player': player_command,
        'ROS_DOMAIN_ID': os.environ.get('ROS_DOMAIN_ID', '0'),
    }, indent=2) + '\n')
    print(f'Run directory: {run_dir}', flush=True)
    rclpy.init()
    observer = Observer(run_dir)
    mapper = player = None
    status = 'failed'
    error = None
    started = time.monotonic()
    started_wall = time.time()
    mapper_log = (run_dir / 'mapping.log').open('w')
    player_log = (run_dir / 'playback.log').open('w')
    try:
        mapper = subprocess.Popen(mapper_command, stdout=mapper_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        deadline = time.monotonic() + 45
        while not observer.get_publishers_info_by_topic('/aft_mapped_to_init'):
            rclpy.spin_once(observer, timeout_sec=0.1)
            if mapper.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Mapper did not initialize; see mapping.log')
        print('Mapper initialized; playing complete sequence.', flush=True)
        for node_name, filename in (('/laserMapping', 'mapper_parameters.yaml'),
                                    ('/parameter_blackboard', 'camera_parameters.yaml')):
            with (run_dir / filename).open('w') as params_file:
                subprocess.run(['ros2', 'param', 'dump', '--no-daemon', '--spin-time', '2', node_name], check=True,
                               stdout=params_file, timeout=15)
        player = subprocess.Popen(player_command, stdout=player_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        last_report = time.monotonic()
        deadline = time.monotonic() + 1200
        while player.poll() is None:
            rclpy.spin_once(observer, timeout_sec=0.05)
            if mapper.poll() is not None:
                raise RuntimeError('Launch process exited during playback')
            if time.monotonic() > deadline:
                raise RuntimeError('Playback timeout')
            if time.monotonic() - last_report >= 30:
                print(dict(observer.counts), flush=True)
                last_report = time.monotonic()
        if player.returncode != 0:
            raise RuntimeError(f'Bag player exited with {player.returncode}')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rclpy.spin_once(observer, timeout_sec=0.05)
        if not all(observer.counts[t] for t in ('/aft_mapped_to_init', '/cloud_registered', '/rgb_img')):
            raise RuntimeError('Expected odometry/cloud/visual output was missing')
        # This port stamps ROS odometry with node time, but its saved TUM file
        # uses sensor time. Compare matching clocks when checking coverage.
        trajectory_path = root / 'fast_livo2_ws/src/FAST-LIVO2/Log/result/Retail_Street.txt'
        poses = trajectory_path.read_text().splitlines()
        if not poses or abs(float(poses[-1].split()[0]) - observer.last_stamp['/livox/lidar']) > 1.0:
            raise RuntimeError('Saved trajectory did not reach the final second of LiDAR input')
        status = 'playback_completed'
    except (Exception, KeyboardInterrupt, ExternalShutdownException) as exc:
        error = str(exc)
        print(f'Run stopped: {exc}', flush=True)
    finally:
        stop_process(player)
        mapper_clean_exit = stop_mapper(mapper, run_dir / 'mapping.log') if mapper else False
        if not mapper_clean_exit and status == 'playback_completed':
            status = 'failed'
            error = 'Mapper did not exit cleanly after saving results'
        mapper_log.close()
        player_log.close()
        observer.trajectory.close()
        if observer.last_rgb is not None:
            import cv2
            from cv_bridge import CvBridge
            cv2.imwrite(str(run_dir / 'last_tracking_image.png'),
                        CvBridge().imgmsg_to_cv2(observer.last_rgb, desired_encoding='bgr8'))
        summary = {'status': status, 'error': error, 'wall_seconds': time.monotonic() - started,
                   'play_rate': args.rate, 'received_message_counts': dict(observer.counts),
                   'first_header_stamp': observer.first_stamp, 'last_header_stamp': observer.last_stamp,
                   'last_cloud_points': observer.last_cloud_points,
                   'max_cloud_points': observer.max_cloud_points,
                   'nonempty_cloud_messages': observer.nonempty_cloud_messages,
                   'mapper_clean_exit': mapper_clean_exit,
                   'timestamp_basis': {'input_headers': 'sensor time',
                       'odometry_topic': 'node/system time', 'Retail_Street.txt': 'sensor time'},
                   'mapper_launch_returncode': mapper.returncode if mapper else None,
                   'player_returncode': player.returncode if player else None}
        source_log = root / 'fast_livo2_ws/src/FAST-LIVO2/Log'
        for relative in ('result/Retail_Street.txt', 'pcd/lidar_poses.txt',
                         'pcd/all_raw_points.pcd', 'pcd/all_downsampled_points.pcd'):
            path = source_log / relative
            if path.exists() and path.stat().st_size and path.stat().st_mtime >= started_wall:
                shutil.copy2(path, run_dir / path.name)
        (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        observer.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        print(json.dumps(summary, indent=2), flush=True)
    if status != 'playback_completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
