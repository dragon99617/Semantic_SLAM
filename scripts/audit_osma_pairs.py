#!/usr/bin/env python3
"""Audit paired renders: fixed GT/poses, full frames, actual distinct RGB streams."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SCENES=['v3_sc0_staging_00','v3_sc1_staging_00','v3_sc2_staging_00']
CONDITIONS=['baseline','camera_lights','no_lights','dynamic_lights']


def digest(paths):
    h=hashlib.sha256()
    for p in paths:
        with p.open('rb') as f:
            while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--require-complete',action='store_true');args=p.parse_args()
    rows=[]
    for scene in SCENES:
        baseline=ROOT/'datasets/OSMa_paired/baseline'/scene
        if not (baseline/'render_manifest.json').exists():continue
        base=json.loads((baseline/'render_manifest.json').read_text());n=base['frames']
        for condition in CONDITIONS:
            source=ROOT/'datasets/OSMa_paired'/condition/scene
            marker=source/'render_manifest.json'
            if not marker.exists():continue
            manifest=json.loads(marker.read_text());assert manifest['completed'] and manifest['frames']==n
            for name in ['traj.txt','camera_params.yaml','embed_semseg_classes.json']:
                assert (source/name).read_bytes()==(baseline/name).read_bytes()
            paths={}
            for kind,prefix,ext in [('rgb','frame','jpg'),('depth','depth','png'),('semantic','semantic','png')]:
                paths[kind]=sorted((source/'results').glob(f'{prefix}*.{ext}'))
                assert len(paths[kind])==n
                assert [x.name for x in paths[kind]]==[f'{prefix}{i:06d}.{ext}' for i in range(n)]
                if condition!='baseline' and kind!='rgb':
                    assert all(x.is_symlink() and x.resolve()==(baseline/'results'/x.name).resolve() for x in paths[kind])
            rgb_hash=digest(paths['rgb']);assert rgb_hash==manifest['sha256']['rgb']
            if condition=='baseline':
                for kind in ['depth','semantic']:assert digest(paths[kind])==manifest['sha256'][kind]
            else:assert rgb_hash!=base['sha256']['rgb'], f'Lighting had no effect: {scene}/{condition}'
            for q in [paths['rgb'][0],paths['rgb'][-1]]:
                with Image.open(q) as im:assert im.size==(640,480);im.verify()
            rows.append(dict(scene=scene,condition=condition,frames=n,hashes_verified=True,
                             poses_intrinsics_classes_identical=True,fixed_gt_and_depth=True,
                             rgb_differs_from_baseline=condition!='baseline'))
    all_complete=len(rows)==12
    (ROOT/'experiments/multibench_20261020/paired_data_checks.json').write_text(json.dumps(dict(all_complete=all_complete,groups=rows),indent=2))
    print('verified paired groups',len(rows),'/12',flush=True)
    if args.require_complete:assert all_complete


if __name__=='__main__':main()
