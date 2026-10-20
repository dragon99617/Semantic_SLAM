#!/usr/bin/env python3
"""Probe native Habitat replay geometry before attempting paired-light data.

Uses the author's scene/config/light routines, skips optional navigation imports,
and replays the published OpenCV sensor poses. Never overwrites published data.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import types

os.environ.setdefault('EGL_PLATFORM','surfaceless')
os.environ.setdefault('LIBGL_ALWAYS_SOFTWARE','1')
os.environ.setdefault('LP_NUM_THREADS','2')
os.environ.setdefault('MAGNUM_LOG','quiet')
os.environ.setdefault('HABITAT_SIM_LOG','warning')
ROOT=Path(__file__).resolve().parents[1]
HADAGE=ROOT/'third_party/HaDaGe'
# Loading these upstream submodules doesn't require Habitat-Lab's navigator.
package=types.ModuleType('hadage');package.__path__=[str(HADAGE/'hadage')]
sys.modules['hadage']=package


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scene',default='v3_sc1_staging_00')
    p.add_argument('--frames',type=int,nargs='+',default=[0,100,500])
    args=p.parse_args()
    import habitat_sim
    import numpy as np
    import quaternion
    from PIL import Image
    from hadage.core.settings import load_sim_settings,make_cfg,load_light_settings
    from hadage.core.lights import change_lights

    source=ROOT/'datasets/OSMa-Bench/data/replica_cad'/args.scene
    settings_file=next((source/'configs/sim_settings').glob('*.json'))
    settings=load_sim_settings(str(settings_file),str(ROOT/'datasets/OSMa_sim'))
    config=make_cfg(settings)
    config.sim_cfg.gpu_device_id=-1
    output=ROOT/'experiments/multibench_20261020/replay_probe'/args.scene
    output.mkdir(parents=True,exist_ok=True)
    poses=np.loadtxt(source/'traj.txt').reshape(-1,4,4)
    sim=habitat_sim.Simulator(config)
    light=load_light_settings(str(next((source/'configs/lighting').glob('*.json'))))
    sim,_=change_lights(sim,settings,light,0)
    rows=[]
    for i in args.frames:
        pose=poses[i]
        rotation=pose[:3,:3]@np.diag([1,-1,-1])
        state=habitat_sim.AgentState()
        state.rotation=quaternion.from_rotation_matrix(rotation)
        state.position=pose[:3,3]-rotation@np.array([0,settings['sensor_height'],0])
        sim.agents[0].set_state(state,reset_sensors=True)
        obs=sim.get_sensor_observations()
        rgb=obs['color_sensor'][...,:3]
        depth=obs['depth_sensor']
        semantic=obs['semantic_sensor']
        original_depth=np.array(Image.open(source/'results'/f'depth{i:06d}.png'))
        original_sem=np.array(Image.open(source/'results'/f'semantic{i:06d}.png'))
        quantized=np.clip(depth.astype(float)*settings['depth_scale'],0,65535).astype(np.int32)
        error=np.abs(quantized-original_depth)/settings['depth_scale']
        sensor=sim.agents[0].get_state().sensor_states['color_sensor']
        actual=np.eye(4);actual[:3,:3]=quaternion.as_rotation_matrix(sensor.rotation)@np.diag([1,-1,-1]);actual[:3,3]=sensor.position
        Image.fromarray(rgb).save(output/f'frame{i:06d}.jpg')
        row=dict(frame=i,pose_error=float(np.max(np.abs(pose-actual))),
                 depth_mae_m=float(error.mean()),depth_within_1cm=float((error<.01).mean()),
                 semantic_agreement=float((semantic==original_sem).mean()),rgb_mean=float(rgb.mean()))
        rows.append(row);print(row,flush=True)
    sim.close()
    passed=all(r['pose_error']<1e-4 and r['depth_within_1cm']>.995 and r['semantic_agreement']>.995 for r in rows)
    (output/'geometry_checks.json').write_text(json.dumps(dict(passed=passed,frames=rows),indent=2))
    if not passed:
        raise SystemExit('Published RGB-D geometry could not be reproduced; do not reuse its GT for this render.')


if __name__=='__main__':
    main()
