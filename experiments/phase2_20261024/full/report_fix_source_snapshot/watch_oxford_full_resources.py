#!/usr/bin/env python3
"""Sample kernel mapper RSS/HWM while full Oxford batch is running."""
import datetime
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

BASE = Path(__file__).resolve().parents[1]/'experiments/phase2_20261024/full'
out = BASE/'mapper_resources.jsonl'
started = datetime.datetime.now().astimezone().isoformat()
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--resume', action='store_true')
args = ap.parse_args()
peaks = {}
if args.resume and (BASE/'mapper_resource_summary.json').exists():
    old = json.loads((BASE/'mapper_resource_summary.json').read_text())
    peaks = old['kernel_peak_rss_kib']
    started = old['started_at']
with out.open('a' if args.resume else 'x') as f:
    while True:
        found = subprocess.run(['pgrep', '-x', 'fastlivo_mappin'], capture_output=True, text=True)
        for pid in found.stdout.split():
            path = Path('/proc')/pid
            try:
                cmd = (path/'cmdline').read_bytes().replace(b'\0', b' ').decode()
                if '/full/captures/' not in cmd:
                    continue
                seq = 'oxford02' if 'oxford02_full' in cmd else 'oxford05'
                fields = {k: int(v) for k, v in re.findall(r'^(VmRSS|VmHWM|VmSwap):\s+(\d+)', (path/'status').read_text(), re.M)}
                peaks[seq] = max(peaks.get(seq, 0), fields.get('VmHWM', 0))
                f.write(json.dumps(dict(at=datetime.datetime.now().astimezone().isoformat(), pid=int(pid), sequence=seq, **fields))+'\n')
                f.flush()
            except FileNotFoundError:
                pass
        status_path = BASE/'batch_status.json'
        if status_path.exists() and json.loads(status_path.read_text())['status'] != 'running':
            break
        time.sleep(1)
(BASE/'mapper_resource_summary.json').write_text(json.dumps(dict(started_at=started, kernel_peak_rss_kib=peaks,
    scope='Sampling began after Oxford02 capture startup; kernel HWM includes earlier process peak. Not a full wall-time RSS trace.'), indent=2)+'\n')
