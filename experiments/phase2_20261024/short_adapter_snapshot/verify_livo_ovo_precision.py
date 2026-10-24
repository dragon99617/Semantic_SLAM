#!/usr/bin/env python3
"""Measure OVO same-frame geometric self-matches with FP32 vs BF16 autocast."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'third_party/OVO'))
from ovo.utils import geometry_utils as geometry
from sparse_depth_filter import valid_mask_depth_filter


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('inputs', type=Path, nargs='+')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    rows = []
    for source in args.inputs:
        manifest = json.loads((source/'manifest.json').read_text())
        with np.load(source/manifest['frames'][0]['path']) as f:
            depth = torch.from_numpy(f['depth']).cuda()
            pose = torch.from_numpy(f['c2w']).float().cuda()
        k = torch.tensor(manifest['rectified_intrinsics']).float().cuda()
        yy, xx = torch.meshgrid(torch.arange(depth.shape[0], device='cuda'),
                                torch.arange(depth.shape[1], device='cuda'), indexing='ij')
        valid = depth[::2, ::2] > 0
        x, y, z = xx[::2, ::2][valid], yy[::2, ::2][valid], depth[::2, ::2][valid]
        camera_points = torch.stack([(x-k[0, 2])*z/k[0, 0], (y-k[1, 2])*z/k[1, 1], z, torch.ones_like(z)], 1)
        world_points = (pose@camera_points.T).T[:, :3]
        expected = int((valid_mask_depth_filter(depth)[::2, ::2][valid] > 0).sum())
        row = dict(input=str(source), same_frame_points=len(world_points), expected_filtered_self_matches=expected)
        for enabled in (False, True):
            with torch.autocast('cuda', dtype=torch.bfloat16, enabled=enabled):
                corners = geometry.compute_camera_frustum_corners(depth, pose, k)
                selected = geometry.compute_frustum_point_ids(world_points, corners)
                ids, _ = geometry.match_3d_points_to_2d_pixels(valid_mask_depth_filter(depth),
                            torch.linalg.inv(pose), world_points[selected], k, .05)
            row['autocast_bfloat16_matches' if enabled else 'float32_matches'] = len(ids)
        row['passed'] = abs(row['float32_matches']-expected) <= 4
        rows.append(row)
    report = dict(passed=all(r['passed'] for r in rows), rows=rows,
                  scope='Same-frame projection self-consistency only; does not estimate semantic accuracy')
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
