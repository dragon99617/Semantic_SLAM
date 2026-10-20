#!/usr/bin/env python3
"""Run eee_01 once with supplied NTU parameters and archive sensor-time poses.

Source scripts/env.sh first. This script uses the system ROS Python runtime.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import yaml

from run_reproduction import stop_process, stop_mapper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rate', type=float, default=0.5)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = root / 'fast_livo2_ws/src/FAST-LIVO2'
    run = root / 'experiments/accuracy/eee_01/runs' / datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    run.mkdir(parents=True)
    config_source = source / 'config/NTU_VIRAL.yaml'
    shutil.copy2(config_source, run / 'NTU_VIRAL_source.yaml')
    config = yaml.safe_load(config_source.read_text())
    parameters = config['/**']['ros__parameters']
    parameters['evo']['seq_name'] = 'NTU_VIRAL_eee_01'
    parameters['evo']['pose_output_en'] = True
    # Accuracy is measured from poses; avoid accumulating an unnecessary map.
    parameters['pcd_save']['pcd_save_en'] = False
    parameters['image_save']['img_save_en'] = False
    config_path = run / 'NTU_VIRAL.yaml'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    camera = source / 'config/camera_NTU_VIRAL.yaml'
    shutil.copy2(camera, run / camera.name)
    for name in ('source_versions.json', 'local_patches.json'):
        shutil.copy2(root / 'experiments' / name, run / name)
    with (run / 'source_working_changes.patch').open('w') as f:
        subprocess.run(['git', '-C', str(source), 'diff', '--binary'], stdout=f, check=True)
    prefix = Path(subprocess.check_output(['ros2', 'pkg', 'prefix', 'fast_livo'], text=True).strip())
    binary = prefix / 'lib/fast_livo/fastlivo_mapping'
    (run / 'binary.json').write_text(json.dumps({'path': str(binary),
        'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2) + '\n')
    bag = root / 'datasets/NTU_VIRAL_eee01_humble'
    prep = json.loads((bag / 'preparation.json').read_text())
    shutil.copy2(bag / 'preparation.json', run / 'bag_preparation.json')
    with (run / 'bag_info.txt').open('w') as f:
        subprocess.run(['ros2', 'bag', 'info', str(bag)], stdout=f, check=True)
    mapper_command = ['ros2', 'launch', 'fast_livo', 'mapping_aviz.launch.py',
        'use_rviz:=False', 'use_compressed_image:=False', f'avia_params_file:={config_path}',
        f'camera_params_file:={camera}']
    player_command = ['ros2', 'bag', 'play', str(bag), '--rate', str(args.rate),
                      '--read-ahead-queue-size', '2000']
    (run / 'commands.json').write_text(json.dumps({'mapper': mapper_command, 'player': player_command,
        'ROS_DOMAIN_ID': os.environ.get('ROS_DOMAIN_ID', '0'), 'parameter_changes': {
            'evo.seq_name': 'NTU_VIRAL_eee_01', 'evo.pose_output_en': True,
            'pcd_save.pcd_save_en': False, 'image_save.img_save_en': False},
        'all_estimation_parameters': 'unchanged from community NTU_VIRAL.yaml'}, indent=2) + '\n')
    log = (run / 'mapping.log').open('w')
    playlog = (run / 'playback.log').open('w')
    trajectory = source / 'Log/result/NTU_VIRAL_eee_01.txt'
    if trajectory.exists():
        shutil.copy2(trajectory, run / 'previous_output_before_run.tum')
    started_wall = time.time()
    started = time.monotonic()
    mapper = player = None
    error = None
    completed = False
    clean = False
    print(f'Run directory: {run}', flush=True)
    try:
        mapper = subprocess.Popen(mapper_command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(5)
        for node, filename in (('/laserMapping', 'mapper_parameters.yaml'),
                               ('/parameter_blackboard', 'camera_parameters.yaml')):
            with (run / filename).open('w') as f:
                subprocess.run(['ros2', 'param', 'dump', '--no-daemon', '--spin-time', '2', node],
                    stdout=f, check=True, timeout=30)
        player = subprocess.Popen(player_command, stdout=playlog, stderr=subprocess.STDOUT, start_new_session=True)
        report = time.monotonic()
        while player.poll() is None:
            time.sleep(1)
            if mapper.poll() is not None:
                raise RuntimeError('Mapper launch exited during playback')
            if time.monotonic() - started > 1800:
                raise RuntimeError('Playback timeout')
            if time.monotonic() - report >= 30:
                lines = trajectory.read_text().splitlines() if trajectory.exists() else []
                stamp = float(lines[-1].split()[0]) if lines else None
                print(json.dumps({'elapsed_s': round(time.monotonic() - started), 'poses': len(lines),
                                  'last_sensor_stamp': stamp}), flush=True)
                report = time.monotonic()
        if player.returncode:
            raise RuntimeError(f'Player exit {player.returncode}')
        time.sleep(10)
        if not trajectory.exists() or trajectory.stat().st_mtime < started_wall:
            raise RuntimeError('No fresh trajectory')
        lines = trajectory.read_text().splitlines()
        first, last = float(lines[0].split()[0]), float(lines[-1].split()[0])
        lidar = prep['headers']['/os1_cloud_node1/points']
        expected_last = lidar['last_ns'] / 1e9
        if abs(last - expected_last) > 1.0 or last - first < 390:
            raise RuntimeError(f'Trajectory coverage insufficient: {first}..{last}; input end {expected_last}')
        completed = True
    except (Exception, KeyboardInterrupt) as e:
        error = str(e)
        print(f'Run failed: {e}', flush=True)
    finally:
        stop_process(player)
        if mapper:
            clean = stop_mapper(mapper, run / 'mapping.log')
        log.close()
        playlog.close()
        if trajectory.exists() and trajectory.stat().st_mtime >= started_wall:
            shutil.copy2(trajectory, run / 'estimate_imu.tum')
        for name in ('mat_pre.txt', 'mat_out.txt', 'imu.txt'):
            p = source / 'Log' / name
            if p.exists() and p.stat().st_mtime >= started_wall:
                shutil.copy2(p, run / name)
        summary = {'status': 'playback_completed' if completed and clean else 'failed', 'error': error,
            'diagnostic_duration_s': None, 'rate': args.rate, 'mapper_clean_exit': clean,
            'player_returncode': player.returncode if player else None,
            'launch_returncode': mapper.returncode if mapper else None,
            'wall_seconds': time.monotonic() - started, 'timestamp_basis': 'saved TUM uses sensor time',
            'run_dir': str(run)}
        (run / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        files = [p for p in run.iterdir() if p.is_file()]
        (run / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(files)))
        print(json.dumps(summary, indent=2), flush=True)
    if summary['status'] != 'playback_completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
