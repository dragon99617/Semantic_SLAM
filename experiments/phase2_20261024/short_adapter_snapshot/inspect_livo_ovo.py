#!/usr/bin/env python3
"""Reload and validate saved OVO map, descriptors and open-vocabulary queries.

Queries are an engineering check, not labelled semantic accuracy evaluation.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME', str(ROOT/'third_party/model_cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('MPLBACKEND', 'Agg')
sys.path.insert(0, str(ROOT/'third_party/OVO'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    a = p.parse_args()
    import matplotlib.pyplot as plt
    import numpy as np
    import torch
    import yaml
    from ovo.entities.clip_generator import CLIPGenerator
    from verify_phase1_handoff import sha

    run = a.run.resolve()
    runtime = json.loads((run/'runtime.json').read_text())
    if not runtime['completed']:
        raise ValueError('Only inspect a completed run')
    checkpoint = torch.load(run/'ovo_map.ckpt', map_location='cpu', weights_only=False)
    points = checkpoint['map_params']['xyz'].numpy()
    ids = checkpoint['map_params']['obj_ids'].numpy().reshape(-1)
    colors = checkpoint['map_params']['color'].numpy()
    objects = checkpoint['ovo_map_params']
    object_ids = np.asarray(objects['ins_3d_ids']).astype(int)
    assigned = np.isin(ids, object_ids)
    checks = dict(finite_map=bool(np.isfinite(points).all()), nonempty_map=len(points) > 0,
                  object_ids_resolve=bool(np.isin(ids[ids >= 0], object_ids).all()),
                  nonempty_objects=len(object_ids) > 0, nonempty_semantic_points=bool(assigned.any()))
    checks['map_grows_across_frames'] = runtime.get('mapping_frames_with_new_points', 0) > 1
    features = [torch.as_tensor(objects[f'ins3d_{i}_clip_feature']).float().reshape(-1) for i in object_ids]
    checks['finite_nonzero_descriptors'] = bool(features) and all(bool(torch.isfinite(v).all() and v.norm() > 0) for v in features)
    queries = ['building', 'ground', 'tree', 'person', 'car', 'fence', 'sky']
    query_records = []
    if checks['finite_nonzero_descriptors']:
        os.chdir(ROOT/'third_party/OVO')
        cfg = yaml.safe_load((run/'config.yaml').read_text())
        generator = CLIPGenerator(dict(cfg['semantic']['clip'], embed_type='fixed_weights'), device='cuda')
        with torch.inference_mode():
            scores = generator.get_embed_txt_similarity(torch.stack(features).cuda(), queries, templates=['an image of {}'])
        checks['finite_text_query_scores'] = bool(torch.isfinite(scores).all())
        query_records = [dict(object_id=int(i), points=int((ids == i).sum()),
                              highest_scoring_query=queries[int(row.argmax())], scores=row.tolist())
                         for i, row in zip(object_ids, scores.cpu().numpy())]
        del generator
        torch.cuda.empty_cache()
    else:
        checks['finite_text_query_scores'] = False
    fig = plt.figure(figsize=(12, 5))
    for j, name in enumerate(('RGB geometry', 'OVO instance IDs')):
        ax = fig.add_subplot(1, 2, j+1, projection='3d')
        chosen = np.arange(0, len(points), max(1, len(points)//25000))
        if j == 0:
            c = colors[chosen]/255.
        else:
            c = np.tile([.7, .7, .7, .3], (len(chosen), 1))
            ok = assigned[chosen]
            c[ok] = plt.get_cmap('tab20')((ids[chosen][ok] % 20)/19)
        ax.scatter(*points[chosen].T, c=c, s=1, linewidths=0)
        ax.set(xlabel='world x (m)', ylabel='world y (m)', zlabel='world z (m)', title=name)
        ax.set_box_aspect(np.maximum(np.ptp(points, axis=0), .1))
        ax.view_init(elev=25, azim=-65)
    fig.suptitle(f'{run.name}: {len(points):,} points, {len(object_ids)} objects (unlabelled engineering check)')
    fig.tight_layout()
    fig.savefig(run/'semantic_map.png', dpi=160)
    plt.close(fig)
    report = dict(passed=all(checks.values()), checks=checks, map_points=len(points),
                  assigned_points=int(assigned.sum()), assigned_fraction=float(assigned.mean()),
                  objects=len(object_ids), checkpoint_sha256=sha(run/'ovo_map.ckpt'),
                  queries=queries, query_results=query_records,
                  limitation='No semantic/instance GT on these real segments; query rankings are not accuracy measurements.')
    (run/'map_checks.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
