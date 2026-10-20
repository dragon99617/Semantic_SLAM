#!/usr/bin/env python3
"""Wait for released-data GPU jobs, then score validated local light replays."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/multibench_20261020'
PYTHON=ROOT/'third_party/semantic_venv/bin/python'
SCENES=['v3_sc1_staging_00','v3_sc0_staging_00','v3_sc2_staging_00']
CONDITIONS=['baseline','camera_lights','no_lights','dynamic_lights']


def main():
    while True:
        current=json.loads((BASE/'batch_status.json').read_text())
        if current.get('available_data_jobs_completed'):break
        if any(x['state']=='failed' for x in current['jobs']):
            raise SystemExit('Released-data job failed; resolve it before sharing the GPU.')
        time.sleep(15)
    state={'jobs':[],'source':'local Habitat replay, separate from released-data benchmark'}
    env=dict(os.environ,OMP_NUM_THREADS='4',MPLBACKEND='Agg',HF_HUB_OFFLINE='1',
             HF_HOME=str(ROOT/'third_party/model_cache'),DISABLE_WANDB='true')
    for scene in SCENES:
        for condition in CONDITIONS:
            source=ROOT/'datasets/OSMa_paired'/condition/scene
            item={'scene':scene,'condition':condition,'state':'waiting_for_render'}
            state['jobs'].append(item)
            (BASE/'paired_status.json').write_text(json.dumps(state,indent=2))
            while not (source/'render_manifest.json').exists():time.sleep(15)
            rendered=json.loads((source/'render_manifest.json').read_text())
            assert rendered['completed']
            truth=ROOT/'datasets/OSMa_paired/baseline'/scene
            for name in ['traj.txt','camera_params.yaml','embed_semseg_classes.json']:
                assert (source/name).read_bytes()==(truth/name).read_bytes()
            run=BASE/'paired_runs'/condition/scene
            run.mkdir(parents=True,exist_ok=True)
            item['run']=str(run.relative_to(ROOT))
            for stage,script,extra in [
                ('mapping','run_semantic_benchmark.py',['--output',str(run),'--input-dir',str(source),'--condition',condition]),
                ('evaluation','evaluate_semantic_benchmark.py',['--run',str(run)])]:
                if stage=='mapping' and (run/'runtime.json').exists():
                    done=json.loads((run/'runtime.json').read_text())
                    if done['completed'] and done['full_scene']:continue
                if stage=='evaluation' and (run/'metrics.json').exists():continue
                item.update(state='running',stage=stage)
                (BASE/'paired_status.json').write_text(json.dumps(state,indent=2))
                print(scene,condition,stage,flush=True)
                with (run/f'{stage}.log').open('a') as f:
                    result=subprocess.run([str(PYTHON),'-u',str(ROOT/'scripts'/script),
                                           '--dataset','OSMa','--scene',scene]+extra,
                                           cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
                if result.returncode:
                    item.update(state='failed',exit_code=result.returncode)
                    (BASE/'paired_status.json').write_text(json.dumps(state,indent=2))
                    raise SystemExit(f'{scene}/{condition}/{stage} failed')
            item.update(state='completed')
            (BASE/'paired_status.json').write_text(json.dumps(state,indent=2))
            subprocess.run([str(PYTHON),str(ROOT/'scripts/summarize_osma_paired.py')],cwd=ROOT,check=True)
    state['completed']=True
    (BASE/'paired_status.json').write_text(json.dumps(state,indent=2))


if __name__=='__main__':
    main()
