#!/usr/bin/env python3
"""Contract tests for frame direction, timing, distortion and sparse visibility."""
from types import SimpleNamespace as NS
import unittest

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from livo_ovo_geometry import (Rectifier, camera_from_imu, decode_cloud, decode_image,
                               nearest_stamp, offset_ns, transform, world_from_camera)


def camera(model='Pinhole'):
    return dict(cam_model=model, cam_width=80, cam_height=60, cam_fx=50., cam_fy=51.,
                cam_cx=40., cam_cy=30., k1=-.04, k2=-.002, k3=.003, k4=-.001,
                cam_d0=-.08, cam_d1=.08, cam_d2=.0008, cam_d3=.0004)


class GeometryTests(unittest.TestCase):
    def test_transform_direction_and_metric_scale(self):
        t_il = transform(Rotation.from_euler('xyz', [.3, -.2, 1]).as_matrix(), [.1, .2, .3])
        t_cl = transform(Rotation.from_euler('xyz', [1, .5, -.1]).as_matrix(), [-.1, .03, .07])
        t_wi = transform(Rotation.from_euler('xyz', [0, .2, -.4]).as_matrix(), [3, 4, 2])
        p = dict(extrin_calib=dict(extrinsic_R=t_il[:3, :3], extrinsic_T=t_il[:3, 3],
                                  Rcl=t_cl[:3, :3], Pcl=t_cl[:3, 3]))
        t_wc = world_from_camera(t_wi, camera_from_imu(p))
        np.testing.assert_allclose(t_wc @ t_cl, t_wi @ t_il, atol=1e-12)
        # Same LiDAR point reaches the same world point along independent paths.
        point = np.array([1., 2., 5., 1.])
        np.testing.assert_allclose(t_wc @ (t_cl @ point), t_wi @ (t_il @ point), atol=1e-12)

    def test_integer_time_offset_and_rejection(self):
        self.assertEqual(offset_ns(-.0136182168), -13618217)
        ns = 1710754095205436492
        stamps = np.array([ns, ns+50000000], dtype=np.int64)
        self.assertEqual(nearest_stamp(stamps, ns+2000), 0)
        self.assertIsNone(nearest_stamp(stamps, ns+2001))
        self.assertIsNone(nearest_stamp([], ns))

    def test_zbuffer_invalids_and_axial_depth(self):
        rect = Rectifier(camera())
        xyz = np.array([[0, 0, 4], [0, 0, 2], [.2, .3, 3], [0, 0, -1],
                        [np.nan, 0, 3], [100, 0, 2], [0, 0, 60]])
        depth, ids = rect.project(xyz, np.eye(4))
        self.assertEqual(depth[30, 40], 2)
        self.assertEqual(ids[30, 40], 1)
        self.assertEqual(int((depth > 0).sum()), 2)
        self.assertTrue((ids[depth == 0] == -1).all())
        self.assertEqual(Rectifier(camera()).project(np.empty((0, 3)), np.eye(4))[0].sum(), 0)

    def test_distortion_maps_against_opencv_projection(self):
        for model in ('Pinhole', 'EquidistantCamera'):
            rect = Rectifier(camera(model))
            pixels = np.array([[20, 20], [40, 30], [60, 45]])
            rays = np.column_stack([(pixels[:, 0]-40)/50., (pixels[:, 1]-30)/51., np.ones(3)])
            if model == 'Pinhole':
                expected, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), rect.k, rect.d)
            else:
                expected, _ = cv2.fisheye.projectPoints(rays.reshape(-1, 1, 3), np.zeros(3), np.zeros(3), rect.k, rect.d)
            actual = np.column_stack([rect.mx[pixels[:, 1], pixels[:, 0]], rect.my[pixels[:, 1], pixels[:, 0]]])
            np.testing.assert_allclose(actual, expected.reshape(-1, 2), atol=3e-6)

    def test_image_padding_bgr_to_rgb(self):
        msg = NS(encoding='bgr8', height=1, width=2, step=8, data=bytes([1, 2, 3, 4, 5, 6, 99, 99]))
        np.testing.assert_array_equal(decode_image(msg), [[[3, 2, 1], [6, 5, 4]]])

    def test_bigendian_organized_padded_cloud(self):
        data = bytearray(48)
        a = np.ndarray((2, 1, 3), dtype='>f4', buffer=data, strides=(24, 12, 4))
        a[:] = [[[1, 2, 3]], [[4, 5, 6]]]
        msg = NS(fields=[NS(name=n, offset=i*4, datatype=7, count=1) for i, n in enumerate('xyz')],
                 point_step=12, row_step=24, height=2, width=1, is_bigendian=True, data=data)
        np.testing.assert_array_equal(decode_cloud(msg), [[1, 2, 3], [4, 5, 6]])


if __name__ == '__main__':
    unittest.main(verbosity=2)
