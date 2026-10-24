#!/usr/bin/env python3
"""Finish two full Oxford integrated runs, overlapping independent CPU/GPU work.

Adopt an already-running audited Oxford02 capture. Capture05 uses shared native
logs only after capture02 exits. Never overwrite attempts or mark pending GT as
semantic acceptance. Run in the sourced system ROS environment.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'experiments/phase2_20261024/full'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--adopt-capture-pid', type=int, required=True)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--adopt-capture05-pid', type=int)
    args = ap.parse_args()
    status = dict(status='running', semantic_accuracy='awaiting_independent_annotation', stages={})
    if args.resume:
        previous = json.loads((BASE/'batch_status.json').read_text())
        (BASE/'batch_status_before_report_fix.json').write_text(json.dumps(previous, indent=2)+'\n')
        status['stages'] = previous['stages']
        status['resumed_after'] = previous.get('error')

    def record(key, state, **details):
        status['updated_at'] = datetime.datetime.now().astimezone().isoformat()
        status['stages'][key] = dict(state=state, **details)
        path = BASE/'batch_status.tmp'
        path.write_text(json.dumps(status, indent=2)+'\n')
        path.replace(BASE/'batch_status.json')
        print(key, state, details, flush=True)

    def execute(key, command, env=None):
        if args.resume and status['stages'].get(key, {}).get('state') == 'completed':
            print(key, 'reuse_completed', flush=True)
            return
        log_path = BASE/f'{key}.log'
        if log_path.exists():
            archived = BASE/'failed_controller_attempts'
            archived.mkdir(exist_ok=True)
            shutil.copy2(log_path, archived/(key+'_before_report_fix.log'))
        record(key, 'running', command=command)
        with log_path.open('w') as f:
            result = subprocess.run(command, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
        record(key, 'completed' if result.returncode == 0 else 'failed', returncode=result.returncode)
        if result.returncode:
            raise RuntimeError(f'{key} failed; see its log')

    semantic_python = str(ROOT/'third_party/semantic_venv/bin/python')
    env = dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1')
    capture02 = BASE/'captures/oxford02_full_r1'
    capture05 = BASE/'captures/oxford05_full_r1'
    package = BASE/'annotations/oxford_transitions_v1'
    try:
        record('oxford02_capture', 'adopted_running', pid=args.adopt_capture_pid)
        while not (capture02/'capture_summary.json').exists():
            if not Path(f'/proc/{args.adopt_capture_pid}').exists():
                raise RuntimeError('Adopted capture02 exited without summary')
            time.sleep(3)
        result = json.loads((capture02/'capture_summary.json').read_text())
        if not result['completed']:
            raise RuntimeError('Capture02 audit failed')
        # Wait for lock/log finalization before starting the second mapper.
        deadline = time.monotonic()+30
        while Path(f'/proc/{args.adopt_capture_pid}').exists() and time.monotonic() < deadline:
            time.sleep(.2)
        record('oxford02_capture', 'completed')
        command05 = ['python3', '-u', 'scripts/capture_livo_ovo.py', 'experiments/phase1_20261022/oxford05.json',
                     '--output', str(capture05), '--seconds', '0', '--rate', '.5']
        process05 = log05 = None
        if args.resume and capture05.exists():
            if not (capture05/'capture_summary.json').exists():
                if not args.adopt_capture05_pid or not Path(f'/proc/{args.adopt_capture05_pid}').exists():
                    raise RuntimeError('Need live capture05 PID to resume')
                cmdline = Path(f'/proc/{args.adopt_capture05_pid}/cmdline').read_bytes()
                if str(capture05).encode() not in cmdline:
                    raise RuntimeError('capture05 PID command mismatch')
            record('oxford05_capture', 'adopted_running', pid=args.adopt_capture05_pid)
        else:
            log05 = (BASE/'oxford05_capture.log').open('w')
            process05 = subprocess.Popen(command05, cwd=ROOT, stdout=log05, stderr=subprocess.STDOUT,
                                         env=dict(env, ROS_DOMAIN_ID='87', ROS_LOG_DIR='/tmp/livo_ovo_oxford05_full'))
            record('oxford05_capture', 'running', pid=process05.pid, command=command05)
        for seq, capture in [('oxford02', capture02), ('oxford05', capture05)]:
            if seq == 'oxford05':
                if process05 is not None:
                    code = process05.wait()
                    log05.close()
                else:
                    while not (capture05/'capture_summary.json').exists():
                        if not Path(f'/proc/{args.adopt_capture05_pid}').exists():
                            raise RuntimeError('Adopted capture05 exited without summary')
                        time.sleep(3)
                    code = 0 if json.loads((capture05/'capture_summary.json').read_text())['completed'] else 1
                record('oxford05_capture', 'completed' if code == 0 else 'failed', returncode=code)
                if code:
                    raise RuntimeError('Capture05 failed')
            data = BASE/'inputs'/seq
            run = BASE/'runs'/seq
            execute(f'{seq}_prepare', ['python3', '-u', 'scripts/prepare_livo_ovo.py', str(capture), '--output', str(data)], env)
            execute(f'{seq}_ovo', [semantic_python, '-u', 'scripts/run_oxford_ovo_full.py', str(data),
                    '--output', str(run), '--annotation-package', str(package)], env)
            execute(f'{seq}_map_checks', [semantic_python, 'scripts/inspect_livo_ovo.py', str(run)], env)
            execute(f'{seq}_transition_audit', [semantic_python, 'scripts/audit_oxford_full_run.py', seq], env)
        execute('full_report', ['python3', 'scripts/write_oxford_full_report.py'], env)
        status['status'] = 'engineering_completed_annotations_pending'
        record('batch', 'completed')
    except Exception as e:
        status['status'] = 'failed'
        status['error'] = f'{type(e).__name__}: {e}'
        record('batch', 'failed', error=status['error'])
        raise


if __name__ == '__main__':
    main()
