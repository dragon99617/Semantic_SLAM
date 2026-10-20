#!/usr/bin/env python3
"""Restore pinned upstream sources and apply recorded patches without resetting work."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args, check=True):
    return subprocess.run(['git', '-C', str(path), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def plan(profile, references):
    original = json.loads((ROOT/'experiments/source_versions.json').read_text())['repositories']
    semantic = json.loads((ROOT/'experiments/multibench_20261020/source_versions.json').read_text())
    repos = {}
    if profile in ('ros2', 'all'):
        repos.update(original)
    if profile in ('semantic', 'all'):
        repos.update({name: row for name, row in semantic.items() if name in ('OVO','OSMa-Bench','HaDaGe','SAM2')})
        if references:
            repos.update({name: semantic[name] for name in ('M2DGR','M2DGR-Benchmark')})
    patches = {'FAST-LIVO2':'experiments/patches/FAST-LIVO2.patch',
               'rpg_vikit':'experiments/patches/rpg_vikit.patch',
               'OVO':'experiments/multibench_20261020/OVO_memory_compat.patch'}
    return repos, patches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['ros2','semantic','all'],default='all')
    parser.add_argument('--include-reference-repos',action='store_true',help='Also fetch M2DGR documentation and its large reference benchmark repo.')
    parser.add_argument('--dry-run',action='store_true')
    args = parser.parse_args()
    repos, patches = plan(args.profile,args.include_reference_repos)
    # Parent OVO must exist before its pinned SAM2 submodule directory is restored.
    for name,row in sorted(repos.items(),key=lambda item:len(Path(item[1]['path']).parts)):
        path=(ROOT/row['path']).resolve()
        assert path.is_relative_to(ROOT),path
        url=row.get('url',row.get('remote'))
        commit=row['commit']
        print(f'{name}: {commit} -> {row["path"]}',flush=True)
        if args.dry_run:
            continue
        if not (path/'.git').exists():
            path.mkdir(parents=True,exist_ok=True)
            if any(path.iterdir()):
                raise SystemExit(f'Refusing to initialize a nonempty directory: {path}')
            git(path,'init')
            git(path,'remote','add','origin',url)
            git(path,'fetch','--depth','1','origin',commit)
            git(path,'checkout','--detach','FETCH_HEAD')
        head=git(path,'rev-parse','HEAD').stdout.strip()
        if head!=commit:
            raise SystemExit(f'{path} is at {head}; expected {commit}. Existing work was not reset.')
        patch=patches.get(name)
        if patch:
            patch_path=str(ROOT/patch)
            if git(path,'apply','--check',patch_path,check=False).returncode==0:
                git(path,'apply',patch_path)
                print('  applied '+patch,flush=True)
            elif git(path,'apply','--reverse','--check',patch_path,check=False).returncode==0:
                print('  patch already present',flush=True)
            else:
                raise SystemExit(f'Patch conflicts with existing work: {path}. Nothing was reset.')
    print('Source restore plan checked.' if args.dry_run else 'Pinned sources ready. Data, models and build products were not downloaded.')


if __name__=='__main__':
    main()
