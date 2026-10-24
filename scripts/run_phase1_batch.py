#!/usr/bin/env python3
"""Serial phase-one jobs with immutable native artifacts and resumable state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root=Path(__file__).resolve().parent.parent
    os.chdir(root)
    base=root/'experiments/phase1_20261022'
    freeze=json.loads((base/'baseline_freeze.json').read_text())
    jobs=json.loads((base/'jobs.json').read_text())['jobs']
    states=[]
    path=base/'batch_status.json'
    def save(status):
        d={'status':status,'updated_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'jobs':states}
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(d,indent=2)+'\n');tmp.replace(path)
    gate=base/'diagnostics/oxford02_45s_pacing_v5/summary.json'
    while not gate.exists():
        print('Waiting for final diagnostic',flush=True);time.sleep(15)
    diagnostic=json.loads(gate.read_text())
    if diagnostic['status']!='diagnostic_completed':raise RuntimeError('Final diagnostic failed')
    for job in jobs:
        desc=json.loads(Path(job['descriptor']).read_text())
        run=base/'runs'/job['id']
        row={'id':job['id'],'descriptor':job['descriptor'],'repeat':job['repeat'],'run':str(run.relative_to(root)),'state':'preparing'}
        states.append(row);save('running')
        if (run/'summary.json').exists():
            s=json.loads((run/'summary.json').read_text())
            row['state']='completed' if s['status']=='playback_completed' else 'failed'
            row['reused_existing_run']=True
            if row['state']=='failed':save('running');continue
        else:
            if run.exists():raise RuntimeError(f'Incomplete unclassified run directory: {run}')
            bag=root/desc['bag']
            ready=bag/desc.get('input_readiness_file','metadata.yaml')
            while not ready.exists():
                print('Waiting for verified input',job['id'],flush=True);save('waiting_for_input');time.sleep(30)
            for file,digest in freeze['artifacts'].items():
                if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:raise RuntimeError('Frozen native artifact changed: '+file)
            command=[sys.executable,'-u','scripts/run_livo_baseline.py',job['descriptor'],'--output',str(run),'--rate','.5']
            if job['capture_full_registered_outputs']:command.append('--record-clouds')
            row.update(state='running',command=command,started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'));save('running')
            env=os.environ.copy();env.update(ROS_DOMAIN_ID='82',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1')
            print('START',job['id'],flush=True)
            with (base/(job['id']+'.log')).open('w') as log:
                result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=env)
            row['exit_code']=result.returncode
            row['state']='completed' if result.returncode==0 else 'failed'
        if row['state']=='completed':
            row['evaluation_state']='pending';save('running')
            evaluator=root/'scripts/evaluate_livo_baseline.py'
            if evaluator.exists() and not (run/'accuracy/metrics.json').exists():
                with (base/(job['id']+'_evaluation.log')).open('w') as log:
                    result=subprocess.run([str(root/'third_party/accuracy_venv/bin/python'),str(evaluator),str(run)],stdout=log,stderr=subprocess.STDOUT)
                row['evaluation_state']='completed' if result.returncode==0 else 'failed'
                row['evaluation_exit_code']=result.returncode
            elif (run/'accuracy/metrics.json').exists():row['evaluation_state']='completed'
        save('running');print('END',job['id'],row['state'],flush=True)
    status='completed' if all(x['state']=='completed' and x.get('evaluation_state')=='completed' for x in states) else 'finished_with_pending_or_failed_items'
    save(status);print(status,flush=True)


if __name__=='__main__':main()
