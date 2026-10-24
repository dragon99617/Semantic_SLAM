#!/usr/bin/env python3
"""Read-only recheck of the archived phase-one acceptance, before integration."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8*1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    phase = ROOT/'experiments/phase1_20261022'
    accepted = json.loads((phase/'final_acceptance.json').read_text())
    checks = {'archived_acceptance_passed': accepted['passed']}
    files = {}
    for row in accepted['runs']:
        for name, digest in row['compact_artifacts'].items():
            files[f"experiments/phase1_20261022/runs/{row['id']}/{name}"] = digest
    for row in accepted['scored_csv_checks']:
        files[row['file']] = row['sha256']
    for manifest in ('script_hashes.json', 'protocol_hashes.json'):
        for path, info in json.loads((phase/manifest).read_text())['files'].items():
            files[path] = info['sha256'] if isinstance(info, dict) else info
    freeze = json.loads((phase/'baseline_freeze.json').read_text())
    files.update(freeze['artifacts'])
    sem = json.loads((phase/'semantic_baseline_freeze_check.json').read_text())
    for value in sem.values():
        if isinstance(value, dict) and 'path' in value:
            files[value['path']] = value['sha256']
    for name, path in [('OVO', 'third_party/OVO'), ('OSMa-Bench', 'third_party/OSMa-Bench'),
                       ('SAM2', 'third_party/OVO/thirdParty/segment-anything-2')]:
        commit = subprocess.check_output(['git', '-C', str(ROOT/path), 'rev-parse', 'HEAD'], text=True).strip()
        checks[f'{name}_commit_matches'] = commit == sem[name]['expected']
    patch = subprocess.check_output(['git', '-C', str(ROOT/'third_party/OVO'), 'diff', '--binary'])
    checks['ovo_working_patch_matches'] = hashlib.sha256(patch).hexdigest() == sem['OVO_patch']['sha256']
    mismatches = []
    for path, expected in files.items():
        actual = sha(ROOT/path) if (ROOT/path).is_file() else None
        if actual != expected:
            mismatches.append({'path': path, 'expected': expected, 'actual': actual})
    checks['all_frozen_files_match'] = not mismatches
    jobs = json.loads((phase/'batch_status.json').read_text())['jobs']
    checks['12_completed_geometry_runs'] = len(jobs) == 12 and all(j['state'] == 'completed' for j in jobs)
    report = dict(checked_at=datetime.datetime.now().astimezone().isoformat(),
                  passed=all(checks.values()), checks=checks, files_checked=len(files),
                  mismatches=mismatches, scope='Read-only byte/commit recheck of archived acceptance; no reruns')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
