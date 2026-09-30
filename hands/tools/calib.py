"""Tracking-camera calibration from the headset's factory files.

/persist/xrservice.json (written by Valve's calibration, loaded by XRService)
holds, per camera, Kannala-Brandt fisheye intrinsics ("kb": fx fy cx cy k1-k4,
pixel centres at integer coordinates, as in OpenCV's fisheye model) and a pose
in the slam_right (Cam0) frame: plus_x/plus_z are the camera axes and position
its origin, in mm. /persist/device_config.json gives Cam0's pose in the CAD
frame (cv.cad_from_cal, metres) and the head's pose in CAD (head). The CAD frame
is +X head-left, +Y up, +Z forward; the head frame is OpenVR's: +x right, +y up,
-z forward. Camera frames: +z along the optical axis, +x right and +y down in
the image.

Everything here returns metres in the head frame.
"""
import json
import os

import numpy as np

XRSERVICE_JSON = '/persist/xrservice.json'
DEVICE_JSON = '/persist/device_config.json'
# The Arcturus color module's EEPROM: some binary, then its calibration as JSON (world-readable)
ARCTURUS_EEPROM = '/sys/devices/platform/soc@0/ac15000.cci/i2c-0/0-0050/eeprom'
ARCTURUS_WIDTH = 1972   # valid pixels per row that XRService's buffers deliver (of 2464)


def _pose(d, scale=1.0):
    """4x4 transform from a {plus_x, plus_z, position} pose (child axes in the parent frame)."""
    x = np.asarray(d['plus_x'], float)
    z = np.asarray(d['plus_z'], float)
    y = np.cross(z, x)
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2] = x, y, z
    T[:3, 3] = np.asarray(d['position'], float) * scale
    return T


class Camera:
    def __init__(self, name, width, height, kb, head_from_cam):
        self.name = name
        self.width, self.height = width, height
        self.fx, self.fy, self.cx, self.cy = kb['fx'], kb['fy'], kb['cx'], kb['cy']
        self.k = np.array([kb['k1'], kb['k2'], kb['k3'], kb['k4']])
        self.head_from_cam = head_from_cam
        self.R = head_from_cam[:3, :3]               # camera axes in the head frame
        self.origin = head_from_cam[:3, 3]           # camera centre in the head frame

    def __repr__(self):
        return 'Camera(%s %dx%d at %s mm)' % (self.name, self.width, self.height,
                                              np.round(self.origin * 1000, 1))

    def _theta_d(self, theta):
        t2 = theta * theta
        k1, k2, k3, k4 = self.k
        return theta * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4))))

    def project_cam(self, p):
        """Camera-frame points (N,3) -> pixels (N,2). Points behind the lens still map (the lens sees ~180 deg)."""
        p = np.atleast_2d(p)
        r = np.hypot(p[:, 0], p[:, 1])
        theta = np.arctan2(r, p[:, 2])
        scale = np.where(r > 1e-12, self._theta_d(theta) / np.maximum(r, 1e-12), 0.0)
        return np.stack([self.fx * p[:, 0] * scale + self.cx, self.fy * p[:, 1] * scale + self.cy], axis=1)

    def unproject(self, uv):
        """Pixels (N,2) -> unit rays (N,3) in the camera frame."""
        uv = np.atleast_2d(np.asarray(uv, float))
        mx = (uv[:, 0] - self.cx) / self.fx
        my = (uv[:, 1] - self.cy) / self.fy
        td = np.hypot(mx, my)
        theta = td.copy()
        k1, k2, k3, k4 = self.k
        for _ in range(8):                          # Newton on theta_d(theta) = td
            t2 = theta * theta
            f = self._theta_d(theta) - td
            df = 1 + t2 * (3 * k1 + t2 * (5 * k2 + t2 * (7 * k3 + t2 * 9 * k4)))
            theta = np.clip(theta - f / df, 0.0, np.pi)
        s = np.where(td > 1e-12, np.sin(theta) / np.maximum(td, 1e-12), 1.0)
        return np.stack([mx * s, my * s, np.cos(theta)], axis=1)

    def rays(self, uv):
        """Pixels -> unit rays in the head frame (all starting at self.origin)."""
        return self.unproject(uv) @ self.R.T

    def project(self, p_head):
        """Head-frame points (N,3) -> pixels (N,2) and depth along the optical axis (N,)."""
        p = (np.atleast_2d(p_head) - self.origin) @ self.R
        return self.project_cam(p), p[:, 2]

    def angle_from_axis(self, uv):
        """Angle in degrees between each pixel's ray and the optical axis."""
        return np.degrees(np.arccos(np.clip(self.unproject(uv)[:, 2], -1, 1)))


def device_path(path):
    """A headset file such as /persist/xrservice.json. In the dev container the host's / is
    at /run/host (distrobox doesn't mount /persist); off the Frame, FRAME_JOB_DEVICE_ROOT can
    point at a folder with copies of them."""
    root = os.environ.get('FRAME_JOB_DEVICE_ROOT')
    if root:
        return root + path
    if not os.access(path, os.R_OK) and os.access('/run/host' + path, os.R_OK):
        return '/run/host' + path
    return path


def load(xrservice=XRSERVICE_JSON, device=DEVICE_JSON):
    """{calibration name: Camera} for the tracking cameras, posed in the head frame."""
    with open(device_path(xrservice)) as f:
        rig = json.load(f)
    with open(device_path(device)) as f:
        dev = json.load(f)
    cad_from_cam0 = _pose(dev['cv']['cad_from_cal'])
    head_from_cad = np.linalg.inv(_pose(dev['head']))
    cams = {}
    for c in rig['cameras']:
        kb = next(i for i in c['intrinsics'] if i['cameraModel'] == 'kb')
        cam0_from_cam = _pose(c['extrinsics'], 1e-3)
        cams[c['sourceCamera']] = Camera(c['sourceCamera'], c['width'], c['height'], kb,
                                         head_from_cad @ cad_from_cam0 @ cam0_from_cam)
    return cams


def load_color(eeprom=ARCTURUS_EEPROM, device=DEVICE_JSON, scale=2, crop='subtract'):
    """{"passthrough_left"/"passthrough_right": Camera} for the Arcturus color cameras, posed in
    the head frame, for ft-camd --with-color's images (luma at 1/scale size).

    Their calibration is in the CAD frame (mm) with pixel coordinates on the full 2464x2464
    sensor; each camera also has a cropRegion. crop says how that maps to the delivered
    image: 'subtract' (image x = sensor x - cropRegion.x) or 'none'. tools/check_color.py
    tells which fits.
    """
    with open(device_path(eeprom), 'rb') as f:
        raw = f.read()
    i = raw.rfind(b'{', 0, raw.find(b'"alignment_method"'))
    rig, _ = json.JSONDecoder().raw_decode(raw[i:].decode('latin1'))
    with open(device_path(device)) as f:
        dev = json.load(f)
    head_from_cad = np.linalg.inv(_pose(dev['head']))
    cams = {}
    for c in rig['cameras']:
        kb = dict(next(k for k in c['intrinsics'] if k['cameraModel'] == 'kb'))
        region = c.get('cropRegion', {}) if crop == 'subtract' else {}
        # integer pixel centres: sensor u -> image (u - crop + 0.5) / scale - 0.5
        kb['cx'] = (kb['cx'] - region.get('x', 0) + 0.5) / scale - 0.5
        kb['cy'] = (kb['cy'] - region.get('y', 0) + 0.5) / scale - 0.5
        kb['fx'] /= scale
        kb['fy'] /= scale
        cams[c['sourceCamera']] = Camera(c['sourceCamera'], ARCTURUS_WIDTH // scale, c['height'] // scale, kb,
                                         head_from_cad @ _pose(c['extrinsics'], 1e-3))
    return cams


def triangulate(origins, dirs, weights=None):
    """Least-squares point closest to several rays. Returns (point, rms distance to the rays)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    w = np.ones(len(origins)) if weights is None else np.asarray(weights, float)
    for o, d, wi in zip(origins, dirs, w):
        P = np.eye(3) - np.outer(d, d)
        A += wi * P
        b += wi * P @ o
    p = np.linalg.solve(A, b)
    res = [np.linalg.norm((np.eye(3) - np.outer(d, d)) @ (p - o)) for o, d in zip(origins, dirs)]
    return p, float(np.sqrt(np.mean(np.square(res))))


def triangulate_many(origins, dirs, weights):
    """Triangulate K points seen from V cameras at once.

    origins (V,3), dirs (V,K,3) unit rays, weights (V,). Returns points (K,3) and
    each point's rms distance to its rays (K,).
    """
    P = np.eye(3) - dirs[..., :, None] * dirs[..., None, :]           # (V,K,3,3)
    w = weights[:, None, None, None]
    A = (w * P).sum(0)
    b = (w * (P @ origins[:, None, :, None])).sum(0)[..., 0]
    pts = np.linalg.solve(A, b[..., None])[..., 0]
    off = pts[None] - origins[:, None, :]                              # (V,K,3)
    perp = off - (off * dirs).sum(-1, keepdims=True) * dirs
    return pts, np.sqrt((perp ** 2).sum(-1).mean(0))


if __name__ == '__main__':
    cams = load()
    for cam in cams.values():
        fwd = [float(v) for v in cam.R[:, 2]]
        print('%-12s at x %+6.1f y %+6.1f z %+6.1f mm, looks %s' % (
            cam.name, *(cam.origin * 1000),
            'right' * (fwd[0] > 0.3) + 'left' * (fwd[0] < -0.3) + ' up' * (fwd[1] > 0.3) +
            ' down' * (fwd[1] < -0.3) + ' forward' * (fwd[2] < -0.3) + ' back' * (fwd[2] > 0.3)),
            np.round(fwd, 2))
        uv = np.array([[cam.cx + 200, cam.cy - 100], [cam.cx - 0.4 * cam.width, cam.cy + 0.3 * cam.height]])
        err = np.abs(cam.project_cam(cam.unproject(uv)) - uv).max()
        assert err < 1e-6, err
    a, b = cams['slam_left'], cams['slam_right']
    print('slam baseline %.2f mm' % (1000 * np.linalg.norm(a.origin - b.origin)))
