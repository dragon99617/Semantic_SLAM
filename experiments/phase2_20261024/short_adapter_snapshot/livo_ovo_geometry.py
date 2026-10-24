"""Geometry contract for FAST-LIVO2 -> OVO (metres, RDF camera, c2w).

No ROS or torch dependency. T_ab maps coordinates from b into a.
Only same-frame registered points are projected; zero means missing depth.
"""
from decimal import Decimal, ROUND_HALF_EVEN

import cv2
import numpy as np


def offset_ns(seconds):
    return int((Decimal(str(seconds)) * 10**9).to_integral_value(rounding=ROUND_HALF_EVEN))


def transform(rotation, translation):
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = np.asarray(rotation).reshape(3, 3)
    out[:3, 3] = np.asarray(translation).reshape(3)
    validate_transform(out)
    return out


def validate_transform(matrix):
    matrix = np.asarray(matrix)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError('Invalid finite 4x4 transform')
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError('Invalid homogeneous row')
    r = matrix[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), atol=2e-5) or abs(np.linalg.det(r)-1) > 2e-5:
        raise ValueError('Rotation must be in SO(3)')


def camera_from_imu(parameters):
    e = parameters['extrin_calib']
    t_il = transform(e['extrinsic_R'], e['extrinsic_T'])
    t_cl = transform(e['Rcl'], e['Pcl'])
    return t_cl @ np.linalg.inv(t_il)


def world_from_camera(t_wi, t_ci):
    validate_transform(t_wi)
    return t_wi @ np.linalg.inv(t_ci)


def decode_image(msg):
    if msg.encoding not in ('bgr8', 'rgb8') or msg.step < msg.width * 3:
        raise ValueError(f'Expected BGR8/RGB8 image, got {msg.encoding}')
    pixels = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
    pixels = pixels[:, :msg.width*3].reshape(msg.height, msg.width, 3)
    return pixels[:, :, ::-1].copy() if msg.encoding == 'bgr8' else pixels.copy()


def decode_cloud(msg):
    """Read organized/padded PointCloud2, respecting byte order and field types."""
    fields = {f.name: f for f in msg.fields}
    if not all(n in fields and fields[n].datatype == 7 and fields[n].count == 1 for n in ('x', 'y', 'z')):
        raise ValueError('PointCloud2 XYZ must be scalar FLOAT32')
    dt = np.dtype({'names': ['x', 'y', 'z'],
                   'formats': [('>' if msg.is_bigendian else '<')+'f4']*3,
                   'offsets': [fields[n].offset for n in ('x', 'y', 'z')],
                   'itemsize': msg.point_step})
    a = np.ndarray((msg.height, msg.width), dtype=dt, buffer=msg.data,
                   strides=(msg.row_step, msg.point_step))
    return np.stack([a[n].reshape(-1) for n in ('x', 'y', 'z')], axis=1).astype(np.float64)


class Rectifier:
    """Rectify to fixed pinhole K without rotating camera axes or filling depth."""
    def __init__(self, camera):
        self.width, self.height = int(camera['cam_width']), int(camera['cam_height'])
        self.k = np.array([[camera['cam_fx'], 0, camera['cam_cx']],
                           [0, camera['cam_fy'], camera['cam_cy']], [0, 0, 1]], dtype=float)
        if camera.get('scale', 1) != 1:
            raise ValueError('Use an explicit camera calibration at input image resolution')
        model = camera['cam_model']
        if model == 'EquidistantCamera':
            self.d = np.array([camera[f'k{i}'] for i in range(1, 5)])
            self.mx, self.my = cv2.fisheye.initUndistortRectifyMap(
                self.k, self.d, np.eye(3), self.k, (self.width, self.height), cv2.CV_32FC1)
        elif model == 'Pinhole':
            self.d = np.array([camera.get(f'cam_d{i}', 0) for i in range(4)])
            self.mx, self.my = cv2.initUndistortRectifyMap(
                self.k, self.d, None, self.k, (self.width, self.height), cv2.CV_32FC1)
        else:
            raise ValueError(f'Unsupported camera model {model}')
        self.model = model
        # All four bilinear RGB samples must lie inside the source image.
        self.valid = ((self.mx >= 0) & (self.mx < self.width-1) &
                      (self.my >= 0) & (self.my < self.height-1))

    def rgb(self, rgb):
        if rgb.shape != (self.height, self.width, 3):
            raise ValueError(f'Image shape {rgb.shape} differs from calibration')
        return cv2.remap(rgb, self.mx, self.my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    def project(self, xyz_world, t_wc, near=.1, far=50.):
        validate_transform(t_wc)
        if not 0 < near < far:
            raise ValueError('Require 0 < near < far')
        xyz_world = np.asarray(xyz_world, dtype=np.float64)
        # Row-vector form of inverse(c2w).
        xyz = (xyz_world - t_wc[:3, 3]) @ t_wc[:3, :3]
        keep = np.isfinite(xyz).all(axis=1) & (xyz[:, 2] >= near) & (xyz[:, 2] <= far)
        source_ids = np.flatnonzero(keep)
        xyz = xyz[keep]
        uv = xyz[:, :2] / xyz[:, 2:3]
        uv = uv * [self.k[0, 0], self.k[1, 1]] + self.k[:2, 2]
        # Nearest pixel; z-buffer keeps the closest surface within each pixel.
        inside = ((uv[:, 0] >= -.5) & (uv[:, 0] < self.width-.5) &
                  (uv[:, 1] >= -.5) & (uv[:, 1] < self.height-.5))
        uv, xyz, source_ids = uv[inside], xyz[inside], source_ids[inside]
        pix = np.floor(uv+.5).astype(np.int64)
        inside = self.valid[pix[:, 1], pix[:, 0]]
        pix, xyz, source_ids = pix[inside], xyz[inside], source_ids[inside]
        flat = pix[:, 1]*self.width + pix[:, 0]
        order = np.argsort(xyz[:, 2], kind='stable')
        _, first = np.unique(flat[order], return_index=True)
        chosen = order[first]
        depth = np.zeros((self.height, self.width), dtype=np.float32)
        provenance = np.full(depth.shape, -1, dtype=np.int64)
        depth.flat[flat[chosen]] = xyz[chosen, 2]
        provenance.flat[flat[chosen]] = source_ids[chosen]
        return depth, provenance


def nearest_stamp(stamps, query, tolerance_ns=2000):
    """Integer nanosecond join. Tolerance covers upstream double stamp rounding."""
    if not len(stamps):
        return None
    j = int(np.searchsorted(stamps, query))
    candidates = [i for i in (j-1, j) if 0 <= i < len(stamps)]
    best = min(candidates, key=lambda i: abs(int(stamps[i])-int(query)))
    return best if abs(int(stamps[best])-int(query)) <= tolerance_ns else None
