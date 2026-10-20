#!/usr/bin/env python3
"""Sequential GPU jobs with persistent state; stop on errors, resume completed maps."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/multibench_20261020'
PYTHON=ROOT/'third_party/semantic_venv/bin/python'
JOBS=[('OSMa','v3_sc1_staging_00'),('Replica','office0'),
      ('OSMa','v3_sc0_staging_00'),('Replica','office1'),
      ('OSMa','v3_sc2_staging_00'),('Replica','room0')]


def main():
    assert (BASE/'protocol_checks.json').exists(), 'Run geometry verification first.'
    status={'started':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'M2DGR':'blocked: official download HTTP 429; user has no local data',
            'jobs':[]}
    env=dict(os.environ,OMP_NUM_THREADS='4',MPLBACKEND='Agg',
             HF_HOME=str(ROOT/'third_party/model_cache'),DISABLE_WANDB='true')
    for dataset,scene in JOBS:
        run=BASE/'runs'/dataset/scene
        run.mkdir(parents=True,exist_ok=True)
        item={'dataset':dataset,'scene':scene,'run':str(run.relative_to(ROOT)),'state':'running'}
        status['jobs'].append(item)
        (BASE/'batch_status.json').write_text(json.dumps(status,indent=2))
        print(dataset,scene,'starting',flush=True)
        for stage,script,extra in [
            ('mapping','run_semantic_benchmark.py',['--output',str(run)]),
            ('evaluation','evaluate_semantic_benchmark.py',['--run',str(run)])]:
            if stage=='mapping' and (run/'runtime.json').exists():
                saved=json.loads((run/'runtime.json').read_text())
                if saved['completed'] and saved['full_scene']:
                    continue
            if stage=='evaluation' and (run/'metrics.json').exists():
                continue
            item['stage']=stage
            (BASE/'batch_status.json').write_text(json.dumps(status,indent=2))
            command=[str(PYTHON),'-u',str(ROOT/'scripts'/script),'--dataset',dataset,'--scene',scene]+extra
            with (run/f'{stage}.log').open('a') as log:
                result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            if result.returncode:
                item.update(state='failed',exit_code=result.returncode)
                (BASE/'batch_status.json').write_text(json.dumps(status,indent=2))
                raise SystemExit(f'{dataset}/{scene} {stage} failed: see {run}/{stage}.log')
        item.update(state='completed')
        status['updated']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
        (BASE/'batch_status.json').write_text(json.dumps(status,indent=2))
        print(dataset,scene,'completed',flush=True)
    status['available_data_jobs_completed']=True
    (BASE/'batch_status.json').write_text(json.dumps(status,indent=2))


if __name__=='__main__':
    main()
