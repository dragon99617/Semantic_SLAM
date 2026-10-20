#!/usr/bin/env python3
"""Render official OSMa lighting configs at published poses, with fixed RGB-D GT.

Generated data are a separate, locally replayed controlled experiment. They are
not presented as missing original released sequences or exact paper results.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from probe_osma_replay import ROOT,HADAGE

SCENES=['v3_sc1_staging_00','v3_sc0_staging_00','v3_sc2_staging_00']
LIGHTS={'baseline':None,'camera_lights':'light_settings_camera.json',
        'no_lights':'light_settings_no_lights.json','dynamic_lights':'light_settings_dynamic_lights.json'}


def main():
    import habitat_sim
    import numpy as np
    import quaternion
    from PIL import Image
    from hadage.core.settings import load_sim_settings,make_cfg,load_light_settings
    from hadage.core.lights import change_lights
    for scene in SCENES:
        source=ROOT/'datasets/OSMa-Bench/data/replica_cad'/scene
        poses=np.loadtxt(source/'traj.txt').reshape(-1,4,4)
        boundaries=[int(re.match(r'\[(\d+)\]',line)[1]) for line in (source/'log.txt').read_text().splitlines() if 'Light setup:' in line]
        assert boundaries and boundaries[0]==0
        for condition,light_file in LIGHTS.items():
            output=ROOT/'datasets/OSMa_paired'/condition/scene
            marker=output/'render_manifest.json'
            if marker.exists() and json.loads(marker.read_text()).get('completed'):
                print('already rendered',scene,condition,flush=True);continue
            output.mkdir(parents=True,exist_ok=True)
            (output/'results').mkdir(exist_ok=True)
            settings=load_sim_settings(str(next((source/'configs/sim_settings').glob('*.json'))),str(ROOT/'datasets/OSMa_sim'))
            settings['label']=condition
            settings['load_semseg_classes']=True
            # Keep all baseline sensors. Other conditions render RGB only;
            # measured depth, truth and trajectory are fixed by construction.
            if condition!='baseline':
                settings['depth_sensor']=False;settings['semantic_sensor']=False
            cfg=make_cfg(settings);cfg.sim_cfg.gpu_device_id=-1
            for name in ['traj.txt','camera_params.yaml','camera_params.txt','embed_semseg_classes.json']:
                shutil.copy2(source/name,output/name)
            (output/'configs').mkdir(exist_ok=True)
            (output/'configs/sim_settings.json').write_text(json.dumps(settings,indent=2,default=str))
            light_path=next((source/'configs/lighting').glob('*.json')) if light_file is None else HADAGE/'configs/lighting'/light_file
            shutil.copy2(light_path,output/'configs/lighting.json')
            lights=load_light_settings(str(light_path))
            sim=habitat_sim.Simulator(cfg)
            start=time.perf_counter();last_nav=-1;hashes={k:hashlib.sha256() for k in ['rgb','depth','semantic']}
            agreement=[]
            for i,pose in enumerate(poses):
                nav=max(j for j,boundary in enumerate(boundaries) if boundary<=i)
                if nav!=last_nav:
                    sim,_=change_lights(sim,settings,lights,nav)
                    last_nav=nav
                rotation=pose[:3,:3]@np.diag([1,-1,-1])
                state=habitat_sim.AgentState()
                state.rotation=quaternion.from_rotation_matrix(rotation)
                state.position=pose[:3,3]-rotation@np.array([0,settings['sensor_height'],0])
                sim.agents[0].set_state(state,reset_sensors=True)
                obs=sim.get_sensor_observations()
                rgb=obs['color_sensor'][...,:3]
                path=output/'results'/f'frame{i:06d}.jpg'
                Image.fromarray(rgb).save(path,quality=75)
                hashes['rgb'].update(path.read_bytes())
                for kind,key in [('depth','depth_sensor'),('semantic','semantic_sensor')]:
                    target=output/'results'/f'{kind}{i:06d}.png'
                    if condition=='baseline':
                        values=obs[key]
                        if kind=='depth':values=np.clip(values.astype(float)*settings['depth_scale'],0,65535)
                        Image.fromarray(values.astype(np.int32),mode='I').save(target)
                    elif not target.exists():
                        target.symlink_to(ROOT/'datasets/OSMa_paired/baseline'/scene/'results'/target.name)
                    if condition=='baseline':hashes[kind].update(target.read_bytes())
                if condition=='baseline' and i%100==0:
                    depth=np.asarray(Image.open(output/'results'/f'depth{i:06d}.png')).astype(float)
                    original=np.asarray(Image.open(source/'results'/f'depth{i:06d}.png')).astype(float)
                    sem=np.asarray(Image.open(output/'results'/f'semantic{i:06d}.png'))
                    sem_orig=np.asarray(Image.open(source/'results'/f'semantic{i:06d}.png'))
                    agreement.append({'frame':i,'depth_mae_m':float(np.abs(depth-original).mean()/settings['depth_scale']),
                                      'sem_agreement':float((sem==sem_orig).mean())})
                if i%100==0:print(scene,condition,f'{i}/{len(poses)}',flush=True)
            sim.close()
            manifest=dict(scene=scene,condition=condition,completed=True,frames=len(poses),
                          source='local Habitat 0.3.3 replay using official HaDaGe lighting configs',
                          source_published=str(source),pose_source='published reference sensor poses',
                          rgb_rendering='Mesa llvmpipe, JPEG quality=75',
                          fixed_depth_and_truth_from=str(ROOT/'datasets/OSMa_paired/baseline'/scene),
                          navigation_boundary_frame_ids=boundaries,baseline_comparison_samples=agreement,
                          wall_seconds=time.perf_counter()-start,sha256={k:v.hexdigest() for k,v in hashes.items() if condition=='baseline' or k=='rgb'})
            marker.write_text(json.dumps(manifest,indent=2))
            print('render complete',scene,condition,manifest['wall_seconds'],flush=True)


if __name__=='__main__':
    main()
