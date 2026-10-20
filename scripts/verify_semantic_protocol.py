#!/usr/bin/env python3
"""Check actual-data unprojection against Open3D before scoring any maps."""
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import open3d as o3d
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('semantic_evaluator',ROOT/'scripts/evaluate_semantic_benchmark.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows=[]
for scene in ('v3_sc0_staging_00','v3_sc1_staging_00','v3_sc2_staging_00'):
    source=ROOT/'datasets/OSMa-Bench/data/replica_cad'/scene
    camera=yaml.safe_load((source/'camera_params.yaml').read_text())['camera_params']
    rgb_path=sorted((source/'results').glob('frame*.jpg'))[0]
    depth_path=sorted((source/'results').glob('depth*.png'))[0]
    sem_path=sorted((source/'results').glob('semantic*.png'))[0]
    rgbd=o3d.geometry.RGBDImage.create_from_color_and_depth(
        o3d.io.read_image(str(rgb_path)),o3d.io.read_image(str(depth_path)),
        depth_scale=camera['png_depth_scale'],depth_trunc=np.inf,convert_rgb_to_intensity=False)
    intr=o3d.camera.PinholeCameraIntrinsic(camera['image_width'],camera['image_height'],
                                          camera['fx'],camera['fy'],camera['cx'],camera['cy'])
    pc=o3d.geometry.PointCloud.create_from_rgbd_image(rgbd,intr)
    pose=np.loadtxt(source/'traj.txt',max_rows=1).reshape(4,4)
    pc.transform(pose)
    expected=np.asarray(pc.points)[::10]
    xyz,labels=module.osma_truth(source,ROOT/'datasets/OSMa-Bench_gt'/scene)
    maximum=float(np.max(np.abs(expected-xyz[:len(expected)])))
    assert maximum < 1e-5, maximum
    depth=cv2.imread(str(depth_path),cv2.IMREAD_UNCHANGED)
    sem=cv2.imread(str(sem_path),cv2.IMREAD_UNCHANGED)
    assert np.array_equal(sem[depth>0][::10],labels[:len(expected)])
    rows.append(dict(scene=scene,first_frame_points=len(expected),max_coordinate_error_m=maximum,
                     class_labels_exact=True))
out=ROOT/'experiments/multibench_20261020/protocol_checks.json'
out.write_text(json.dumps(rows,indent=2))
print(out)
print(json.dumps(rows,indent=2))
