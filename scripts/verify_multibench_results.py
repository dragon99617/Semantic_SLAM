#!/usr/bin/env python3
"""Independently rebuild confusion matrices from saved point predictions and GT.

Also check sampled official Replica 5-NN matching, full frame traversal and
upstream metric formulas. This audit performs no new model inference.
"""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
from plyfile import PlyData
import torch
import yaml

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'experiments/multibench_20261020'
sys.path.insert(0,str(ROOT/'third_party/OVO'))
from ovo.utils import eval_utils
spec=importlib.util.spec_from_file_location('osma_metrics',ROOT/'third_party/OSMa-Bench/semseg/scripts/compute_metrics.py')
osma=importlib.util.module_from_spec(spec);spec.loader.exec_module(osma)


def main():
    paths=sorted((BASE/'runs').glob('*/*/metrics.json'))+sorted((BASE/'paired_runs').glob('*/*/metrics.json'))
    assert len(paths)==18,len(paths)
    rows=[]
    for path in paths:
        run=path.parent;m=json.loads(path.read_text());cfg=yaml.safe_load((run/'config.yaml').read_text())
        rt=json.loads((run/'runtime.json').read_text());audit=json.loads((run/'audit.json').read_text())
        assert rt['completed'] and rt['full_scene'] and rt['processed_frames']==audit['rgb_frames']
        cameras=torch.load(run/'estimated_c2w.npy',map_location='cpu',weights_only=False)
        assert set(cameras)==set(range(rt['processed_frames']))
        classes=np.load(run/'instance_classes.npz')
        ids=classes['class_ids'];n=len(ids)+1
        pred=np.load(run/'point_predictions.npy',mmap_mode='r')
        d=np.load(run/'nearest_distances_m.npy',mmap_mode='r')
        assert len(pred)==len(d)==m['gt_points']
        assert not np.isnan(d).any()
        if m['dataset']=='Replica':
            info=yaml.safe_load((ROOT/'third_party/OVO/data/working/configs/Replica/eval_info.yaml').read_text())
            raw=np.loadtxt(ROOT/'third_party/OVO/data/input/replica_semantic_gt'/f"{m['scene']}.txt",dtype=int)
            gt=np.array([info['map_to_reduced'].get(int(x),51) for x in raw])
        else:
            cache=ROOT/'datasets'/('OSMa_paired_gt' if 'paired_runs' in run.parts else 'OSMa-Bench_gt')/m['scene']
            gt=np.memmap(cache/'labels.bin',mode='r',dtype='<i4')
        assert len(gt)==len(pred)
        cm=np.zeros((n,n),dtype=np.int64)
        for first in range(0,len(gt),250000):
            truth=np.asarray(gt[first:first+250000]);prediction=np.asarray(pred[first:first+250000])
            keep=np.isin(truth,ids)
            assert set(np.unique(prediction))<=set(ids.tolist()+[-1])
            x=np.searchsorted(ids,truth[keep]);y=np.searchsorted(ids,prediction[keep])
            y[prediction[keep]==-1]=n-1
            cm+=np.bincount(x*n+y,minlength=n*n).reshape(n,n)
        assert np.array_equal(cm,np.load(run/'confusion.npy')),run
        assert cm.sum()==m['evaluated_gt_points']
        if m['dataset']=='Replica':
            a,mask,_,b,bmask=eval_utils.iou_acc_from_confmat(cm,n-1,[],labels=classes['class_names'])
            miou,macc=a[mask].mean(),b[bmask].mean()
            ckpt=torch.load(run/'ovo_map.ckpt',map_location='cpu',weights_only=False)
            obj_ids=ckpt['map_params']['obj_ids'].reshape(-1)
            assert set(obj_ids[obj_ids>=0].tolist())<=set(classes['ids'])
            mesh=PlyData.read(ROOT/'datasets/Replica'/f"{m['scene']}_mesh.ply")
            xyz=np.stack([mesh['vertex'][k] for k in 'xyz'],-1)
            sample=np.linspace(0,len(xyz)-1,96,dtype=int)
            voted,_,_=eval_utils.match_labels_to_vtx(obj_ids,ckpt['map_params']['xyz'],torch.from_numpy(xyz[sample]))
            table=dict(zip(classes['ids'],ids[classes['predicted_indices']]))
            expected=np.array([table[int(i)] for i in voted])
            assert np.array_equal(expected,pred[sample]),'Official nearest-neighbor vote mismatch'
            del ckpt
        else:
            official=osma.compute_metrics(cm,labels=np.r_[ids,-1],excluded=[0,-1])
            miou,macc=official['miou'],official['macc']
        assert np.isfinite([miou,macc]).all()
        assert np.isclose(miou,m['miou']) and np.isclose(macc,m['macc'])
        row=dict(run=str(run.relative_to(ROOT)),frames=rt['processed_frames'],gt_points=len(gt),
                 confusion_rebuilt_exact=True,official_metric_formula_matches=True,
                 official_neighbor_sample_matches=m['dataset']=='Replica')
        rows.append(row);print('verified',row['run'],flush=True)
    (BASE/'final_result_checks.json').write_text(json.dumps({'passed':True,'runs':rows},indent=2))
    print('18 result groups independently verified.',flush=True)


if __name__=='__main__':main()
