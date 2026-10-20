#!/usr/bin/env python3
"""Short data-backed shutdown probe under a project-local GDB."""
import os
from pathlib import Path
import signal
import subprocess
import time
import rclpy
from rclpy.node import Node

root = Path(__file__).resolve().parents[1]
debug_dir = root / 'experiments/shutdown_debug'
debug_dir.mkdir(exist_ok=True)
config = (root / 'experiments/config/retail_street.yaml').read_text()
config = config.replace('pcd_save_en: true', 'pcd_save_en: false')
config = config.replace('pose_output_en: true', 'pose_output_en: false')
(debug_dir / 'probe.yaml').write_text(config)
(debug_dir / 'commands.gdb').write_text('''set pagination off
set confirm off
set print thread-events off
handle SIGINT nostop noprint pass
run
thread apply all bt 12
quit
''')
env = os.environ.copy()
env['LD_LIBRARY_PATH'] = str(root / 'third_party/debug/usr/lib/x86_64-linux-gnu') + ':' + env.get('LD_LIBRARY_PATH', '')
camera_log = (debug_dir / 'camera.log').open('w')
gdb_log = (debug_dir / 'gdb.log').open('w')
camera = subprocess.Popen(['/opt/ros/humble/lib/demo_nodes_cpp/parameter_blackboard',
    '--ros-args', '--params-file', str(root / 'fast_livo2_ws/src/FAST-LIVO2/config/camera_pinhole.yaml')],
    stdout=camera_log, stderr=subprocess.STDOUT, start_new_session=True)
gdb = subprocess.Popen([str(root / 'third_party/debug/usr/bin/gdb'),
    '--data-directory=' + str(root / 'third_party/debug/usr/share/gdb'), '--batch', '-nx',
    '-x', str(debug_dir / 'commands.gdb'), '--args',
    str(root / 'fast_livo2_ws/install/fast_livo/lib/fast_livo/fastlivo_mapping'),
    '--ros-args', '--params-file', str(debug_dir / 'probe.yaml')],
    env=env, stdout=gdb_log, stderr=subprocess.STDOUT, start_new_session=True)
player = None
rclpy.init()
node = Node('shutdown_probe_controller')
try:
    deadline = time.monotonic() + 45
    while not node.get_publishers_info_by_topic('/aft_mapped_to_init'):
        rclpy.spin_once(node, timeout_sec=0.1)
        if gdb.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Probe mapper failed to initialize')
    with (debug_dir / 'playback.log').open('w') as player_log:
        player = subprocess.Popen(['ros2', 'bag', 'play', str(root / 'datasets/Retail_Street_humble')],
            stdout=player_log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(5)
        os.killpg(player.pid, signal.SIGINT)
        player.wait(timeout=10)
    children = Path(f'/proc/{gdb.pid}/task/{gdb.pid}/children').read_text().split()
    print('GDB children:', children, flush=True)
    for pid in children:
        cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
        if b'fastlivo_mapping' in cmdline:
            os.kill(int(pid), signal.SIGINT)
            print('Sent SIGINT to mapper', pid, flush=True)
    gdb.wait(timeout=45)
    print('GDB finished:', gdb.returncode, flush=True)
finally:
    for process in (player, gdb, camera):
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
    node.destroy_node()
    rclpy.shutdown()
    camera_log.close()
    gdb_log.close()
