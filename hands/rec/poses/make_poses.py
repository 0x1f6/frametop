#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 DeeJanuz
"""Example pose images for the hand recorder's headset panel.

Everything here is drawn by this script: a simple parametric hand (a palm
slab and tapered capsules for the finger bones), posed with joint angles,
ray-marched as a signed distance field with numpy, shaded and outlined.
No outside images, hand models or image generators.

    python3 make_poses.py                     # all poses into this folder
    python3 make_poses.py --only fist,ok      # some of them
    python3 make_poses.py --size 256 --ss 1   # quick, rough preview

Writes <id>.png (RGBA, transparent), poses.json and contact-sheet.png.
Needs numpy and Pillow. It is CPU heavy (about a minute per image on one
core), so run it on a build machine, not on the headset.
"""

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # one thread per process: --jobs sets the parallelism

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ math


def Rx(a):
    a = math.radians(a)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)


def Ry(a):
    a = math.radians(a)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def Rz(a):
    a = math.radians(a)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def orient(f, p):
    """Hand rotation: fingers along f, palm facing p (right hand: thumb = f x p)."""
    f = unit(f)
    p = np.asarray(p, float)
    p = unit(p - f * (p @ f))
    return np.column_stack([np.cross(f, p), f, p])


V = lambda *a: np.array(a, float)  # noqa: E731


def smin(a, b, k):
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


# ------------------------------------------------------------------ SDF nodes
# Every node maps points P (N,3) to signed distances (N,). Units are cm.
# xf(M, t) returns a copy moved by p -> M p + t (M orthonormal, may mirror).


class Cone:
    """Round cone (tapered capsule) from a (radius r1) to b (radius r2)."""

    def __init__(s, a, b, r1, r2):
        s.a, s.b, s.r1, s.r2 = V(*a), V(*b), float(r1), float(r2)

    def xf(s, M, t):
        return Cone(M @ s.a + t, M @ s.b + t, s.r1, s.r2)

    def bounds(s):
        return [((s.a + s.b) / 2, np.linalg.norm(s.b - s.a) / 2 + max(s.r1, s.r2))]

    def d(s, P):
        ba = s.b - s.a
        l2 = ba @ ba
        rr = s.r1 - s.r2
        a2 = l2 - rr * rr
        il2 = 1.0 / l2
        pa = P - s.a
        y = pa @ ba
        z = y - l2
        q = pa * l2 - y[:, None] * ba
        x2 = np.einsum("ij,ij->i", q, q)
        y2 = y * y * l2
        z2 = z * z * l2
        k = math.copysign(1.0, rr) * rr * rr * x2 if rr != 0 else np.zeros_like(x2)
        d1 = np.sqrt(x2 + z2) * il2 - s.r2
        d2 = np.sqrt(x2 + y2) * il2 - s.r1
        d3 = (np.sqrt(np.maximum(x2 * a2 * il2, 0.0)) + y * rr) * il2 - s.r1
        return np.where(np.sign(z) * a2 * z2 > k, d1, np.where(np.sign(y) * a2 * y2 < k, d2, d3))


class Box:
    """Rounded box. R's columns are the box axes. taper: x half-size factor at -y end."""

    def __init__(s, c, R, h, r, taper=None, bulge=0.0):
        s.c, s.R, s.h, s.r, s.taper, s.bulge = V(*c), np.asarray(R, float), V(*h), float(r), taper, bulge

    def xf(s, M, t):
        return Box(M @ s.c + t, M @ s.R, s.h, s.r, s.taper, s.bulge)

    def bounds(s):
        return [(s.c, np.linalg.norm(s.h) + s.r)]

    def d(s, P):
        L = (P - s.c) @ s.R
        q = np.abs(L) - s.h
        if s.taper is not None:
            tt = np.clip((L[:, 1] + s.h[1]) / (2 * s.h[1]), 0, 1)
            q[:, 0] = np.abs(L[:, 0]) - s.h[0] * (s.taper + (1 - s.taper) * tt)
        d = np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0) - s.r
        if s.bulge:  # gently convex faces instead of flat ones
            ex, ey = s.h[0] + s.r, s.h[1] + s.r
            d = d - s.bulge * np.clip(1 - (L[:, 0] / ex) ** 2, 0, 1) * np.clip(1 - (L[:, 1] / ey) ** 2, 0, 1)
        return d


class Ell:
    """Ellipsoid (approximate distance)."""

    def __init__(s, c, R, rad):
        s.c, s.R, s.rad = V(*c), np.asarray(R, float), V(*rad)

    def xf(s, M, t):
        return Ell(M @ s.c + t, M @ s.R, s.rad)

    def bounds(s):
        return [(s.c, s.rad.max())]

    def d(s, P):
        L = (P - s.c) @ s.R
        k0 = np.linalg.norm(L / s.rad, axis=1)
        k1 = np.linalg.norm(L / (s.rad * s.rad), axis=1)
        return k0 * (k0 - 1.0) / np.maximum(k1, 1e-6)


class Cyl:
    """Rounded cylinder along local y: radius ra, half height hh, edge rounding rb."""

    def __init__(s, c, R, ra, hh, rb=0.1):
        s.c, s.R, s.ra, s.hh, s.rb = V(*c), np.asarray(R, float), ra, hh, rb

    def xf(s, M, t):
        return Cyl(M @ s.c + t, M @ s.R, s.ra, s.hh, s.rb)

    def bounds(s):
        return [(s.c, math.hypot(s.ra, s.hh))]

    def d(s, P):
        L = (P - s.c) @ s.R
        dx = np.hypot(L[:, 0], L[:, 2]) - s.ra + s.rb
        dy = np.abs(L[:, 1]) - s.hh + s.rb
        return np.minimum(np.maximum(dx, dy), 0) + np.hypot(np.maximum(dx, 0), np.maximum(dy, 0)) - s.rb


class Torus:
    """Torus in the local xz plane (axis local y)."""

    def __init__(s, c, R, R1, r2):
        s.c, s.R, s.R1, s.r2 = V(*c), np.asarray(R, float), R1, r2

    def xf(s, M, t):
        return Torus(M @ s.c + t, M @ s.R, s.R1, s.r2)

    def bounds(s):
        return [(s.c, s.R1 + s.r2)]

    def d(s, P):
        L = (P - s.c) @ s.R
        return np.hypot(np.hypot(L[:, 0], L[:, 2]) - s.R1, L[:, 1]) - s.r2


class Keys:
    """A grid of nx * nz key caps on the local xz plane (local y up)."""

    def __init__(s, c, R, nx, nz, pitch, kh, r):
        s.c, s.R, s.nx, s.nz, s.pitch, s.kh, s.r = V(*c), np.asarray(R, float), nx, nz, pitch, V(*kh), r

    def xf(s, M, t):
        return Keys(M @ s.c + t, M @ s.R, s.nx, s.nz, s.pitch, s.kh, s.r)

    def bounds(s):
        return [(s.c, math.hypot(s.nx * s.pitch, s.nz * s.pitch) / 2 + 1)]

    def d(s, P):
        L = (P - s.c) @ s.R
        cx, cz = (s.nx - 1) / 2, (s.nz - 1) / 2
        ix = np.clip(np.round(L[:, 0] / s.pitch + cx), 0, s.nx - 1)
        iz = np.clip(np.round(L[:, 2] / s.pitch + cz), 0, s.nz - 1)
        q = np.abs(np.stack([L[:, 0] - (ix - cx) * s.pitch, L[:, 1], L[:, 2] - (iz - cz) * s.pitch], 1)) - s.kh
        return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0) - s.r


class U:
    """Union of nodes, smooth when k > 0."""

    def __init__(s, kids, k=0.0):
        s.kids, s.k = list(kids), k

    def xf(s, M, t):
        return U([c.xf(M, t) for c in s.kids], s.k)

    def bounds(s):
        return [b for c in s.kids for b in c.bounds()]

    def d(s, P):
        d = s.kids[0].d(P)
        for c in s.kids[1:]:
            d = smin(d, c.d(P), s.k)
        return d


class Sub:
    """a minus b."""

    def __init__(s, a, b):
        s.a, s.b = a, b

    def xf(s, M, t):
        return Sub(s.a.xf(M, t), s.b.xf(M, t))

    def bounds(s):
        return s.a.bounds()

    def d(s, P):
        return np.maximum(s.a.d(P), -s.b.d(P))


class Clip:
    """a cut by the plane through point o with outward normal n (keeps the -n side)."""

    def __init__(s, a, o, n):
        s.a, s.o, s.n = a, V(*o), unit(n)

    def xf(s, M, t):
        return Clip(s.a.xf(M, t), M @ s.o + t, M @ s.n)

    def bounds(s):
        return s.a.bounds()

    def d(s, P):
        return np.maximum(s.a.d(P), (P - s.o) @ s.n)


# ------------------------------------------------------------------ the hand
# Local frame of a right hand: wrist joint at the origin, fingers along +y,
# palm facing +z, thumb on the +x side. cm, adult proportions.

FINGERS = [
    # name, MCP joint, bone lengths (proximal, middle, distal incl. tip), radii (MCP, PIP, DIP, tip)
    ("index", (2.5, 9.45, 0.0), (4.2, 2.45, 2.1), (1.03, 0.95, 0.85, 0.77)),
    ("middle", (0.62, 9.85, 0.0), (4.6, 2.85, 2.3), (1.06, 0.98, 0.87, 0.79)),
    ("ring", (-1.25, 9.5, 0.0), (4.35, 2.7, 2.2), (1.0, 0.92, 0.83, 0.75)),
    ("pinky", (-2.95, 8.6, 0.0), (3.5, 2.05, 2.0), (0.9, 0.82, 0.74, 0.67)),
]
THUMB_CMC = (2.1, 2.3, 0.6)
THUMB_LENS = (4.6, 3.3, 2.6)
THUMB_RADII = (1.45, 1.08, 0.98, 0.86)
PARTS = {"palm": 0, "thumb": 1, "index": 2, "middle": 3, "ring": 4, "pinky": 5, "arm": 7}

SKIN = V(0.93, 0.885, 0.84)
NAIL = V(1.0, 0.84, 0.82)
CREASE = V(0.62, 0.55, 0.52)
# palm creases (hand local x, y on the palm side): they tell the palm from the back
CREASES = [
    [(-3.7, 7.3), (-2.0, 7.6), (-0.5, 8.0), (0.8, 8.5), (1.6, 9.1)],
    [(3.5, 6.9), (2.0, 6.6), (0.5, 6.2), (-1.2, 5.8), (-2.8, 5.5)],
    [(3.5, 6.9), (2.2, 6.2), (1.3, 5.0), (0.9, 3.5), (0.9, 2.2), (1.2, 0.9)],
]


def crease_dist(xy):
    d = np.full(len(xy), 1e9)
    for line in CREASES:
        for a, b in zip(line[:-1], line[1:]):
            a, b = V(*a), V(*b)
            t = np.clip((xy - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1)
            d = np.minimum(d, np.linalg.norm(xy - (a + t[:, None] * (b - a)), axis=1))
    return d


def thumb_twist(palmar):
    return -62.0 - 0.45 * palmar


def finger_fk(base, lens, radii, ang):
    mcp, pip, dip, abd = ang
    F1 = Rz(-abd) @ Rx(mcp)
    F2 = F1 @ Rx(pip)
    F3 = F2 @ Rx(dip)
    p0 = V(*base)
    p1 = p0 + F1 @ V(0, lens[0], 0)
    p2 = p1 + F2 @ V(0, lens[1], 0)
    p3 = p2 + F3 @ V(0, lens[2] - radii[3], 0)
    return dict(pts=[p0, p1, p2, p3], frames=[F1, F2, F3], radii=radii)


def thumb_fk(q, twist=None):
    spread, palmar, mcp, ip = q
    tw = thumb_twist(palmar) if twist is None else twist
    B = Rz(-spread) @ Rx(palmar) @ Ry(tw)
    F2 = B @ Rx(mcp)
    F3 = F2 @ Rx(ip)
    p0 = V(*THUMB_CMC)
    p1 = p0 + B @ V(0, THUMB_LENS[0], 0)
    p2 = p1 + F2 @ V(0, THUMB_LENS[1], 0)
    p3 = p2 + F3 @ V(0, THUMB_LENS[2] - THUMB_RADII[3], 0)
    return dict(pts=[p0, p1, p2, p3], frames=[B, F2, F3], radii=THUMB_RADII)


THUMB_LO = V(-30, -15, -15, -25)
THUMB_HI = V(85, 85, 70, 85)


def thumb_ik(target, prior, twist=None, fixed=()):
    """Thumb angles that put the thumb tip centre at target (hand local).

    A pattern search, first held close to the prior (which picks the natural
    solution), then refined from there to hit the target."""
    target = V(*target)
    prior = V(*prior)

    def search(q, w, step):
        def cost(q):
            tip = thumb_fk(q, twist)["pts"][3]
            return float(np.sum((tip - target) ** 2) + w * np.sum((q - prior) ** 2))

        c = cost(q)
        while step > 0.05:
            better = False
            for i in range(4):
                if i in fixed:
                    continue
                for sgn in (1, -1):
                    q2 = q.copy()
                    q2[i] = np.clip(q2[i] + sgn * step, THUMB_LO[i], THUMB_HI[i])
                    c2 = cost(q2)
                    if c2 < c:
                        q, c, better = q2, c2, True
            if not better:
                step *= 0.5
        return q

    q = search(prior.copy(), 0.003, 16.0)
    q = search(q, 0.00002, 4.0)
    err = float(np.linalg.norm(thumb_fk(q, twist)["pts"][3] - target))
    if err > 0.3:
        print(f"warning: thumb misses its target by {err:.2f} cm", file=sys.stderr)
    return q


def pad_point(f, gap=0.0, seg=2, at=1.0, thumb_r=THUMB_RADII[3]):
    """A point just off the pad side of a finger segment (seg 0..2; at 0..1 along it)."""
    p = f["pts"][seg] + (f["pts"][seg + 1] - f["pts"][seg]) * at
    r = f["radii"][seg] + (f["radii"][seg + 1] - f["radii"][seg]) * at
    return p + f["frames"][seg][:, 2] * (r + thumb_r + gap - 0.08)


def back_point(f, seg=1, at=0.5, gap=0.0, thumb_r=THUMB_RADII[3]):
    """A point just off the back (nail side) of a finger segment."""
    p = f["pts"][seg] + (f["pts"][seg + 1] - f["pts"][seg]) * at
    r = f["radii"][seg] + (f["radii"][seg + 1] - f["radii"][seg]) * at
    return p - f["frames"][seg][:, 2] * (r + thumb_r + gap - 0.1)


def resolve(pose):
    """Pose dict -> local joint data. pose['thumb'] is angles or ('to', fn(J) -> point, prior)."""
    J = {}
    for name, base, lens, radii in FINGERS:
        J[name] = finger_fk(base, lens, radii, pose[name])
    th = pose["thumb"]
    tw = pose.get("twist")
    if isinstance(th, tuple) and th and th[0] == "to":
        q = thumb_ik(th[1](J), th[2], tw, th[3] if len(th) > 3 else ())
    else:
        q = V(*th)
    J["thumb"] = thumb_fk(q, tw)
    J["thumb_q"] = q
    return J


def chain_nodes(f):
    p, r = f["pts"], f["radii"]
    cones = [Cone(p[i], p[i + 1], r[i], r[i + 1]) for i in range(3)]
    # the nail: a flat ellipsoid on the back of the last bone
    F = f["frames"][2]
    j, tip = p[2], p[3]
    L = np.linalg.norm(tip - j)
    nail = Ell(j + (tip - j) * 0.8 - F[:, 2] * (r[3] * 0.64), F, (r[3] * 0.72, L * 0.4 + 0.24, 0.32))
    return cones, nail


class Hand:
    """A posed hand plus forearm, as an SDF scene item."""

    def __init__(s, pose, R=np.eye(3), t=(0, 0, 0), left=False, arm=6.5, wrist=(0, 0), _copy=None):
        if _copy is not None:
            return
        J = resolve(pose)
        s.J = J
        wf, wd = pose.get("wrist", wrist)
        R = np.asarray(R, float)
        S = np.diag([-1.0, 1, 1]) if left else np.eye(3)
        Mh = S @ R @ Rx(wf) @ Rz(-wd)
        Ma = S @ R
        t = S @ V(*t)
        s.M, s.t = Mh, t
        palm = Box((-0.05, 4.95, 0.0), np.eye(3), (2.75, 4.15, 0.35), 1.1, taper=0.74, bulge=0.45)
        meta_t = Cone(J["thumb"]["pts"][0], J["thumb"]["pts"][1], THUMB_RADII[0], THUMB_RADII[1])
        heel = Cone((-1.9, 1.6, 0.45), (-2.4, 6.5, 0.35), 1.15, 0.95)
        knuckles = [Ell(V(*base) + V(0, -0.35, -0.55), np.eye(3), (0.95, 0.9, 0.8)) for _, base, _, _ in FINGERS]
        s.core = U([U([palm, meta_t, heel], 1.1)] + knuckles, 0.5).xf(Mh, t)
        s.arm = Box((0, -arm / 2 + 0.6, 0), np.eye(3), (1.25, arm / 2, 0.3), 1.35, bulge=0.2).xf(Ma, t)
        s.arm_o = Ma @ V(0, 0.6, 0) + t
        s.arm_dir = Ma @ V(0, -1, 0)
        s.arm_len = arm
        s.fingers, s.nails, s.ids = [], [], []
        for name in ("thumb", "index", "middle", "ring", "pinky"):
            f = J[name]
            cones, nail = chain_nodes(f)
            if name == "thumb":
                cones = cones[1:]
            s.fingers.append(U(cones + [nail]).xf(Mh, t))
            s.nails.append(nail.xf(Mh, t))
            s.ids.append(PARTS[name])
        s.k = 0.75
        s.hid = 0

    def world(s, p):
        return s.M @ V(*p) + s.t

    def joint(s, name, i):
        return s.world(s.J[name]["pts"][i])

    def xf(s, M, t):
        h = Hand(None, _copy=True)
        h.J = s.J
        h.M, h.t = M @ s.M, M @ s.t + t
        h.core, h.arm = s.core.xf(M, t), s.arm.xf(M, t)
        h.arm_o, h.arm_dir, h.arm_len = M @ s.arm_o + t, M @ s.arm_dir, s.arm_len
        h.fingers = [f.xf(M, t) for f in s.fingers]
        h.nails = [n.xf(M, t) for n in s.nails]
        h.ids, h.k, h.hid = s.ids, s.k, s.hid
        return h

    def bounds(s):
        out = s.core.bounds() + s.arm.bounds()
        for f in s.fingers:
            out += f.bounds()
        return out

    def _palm(s, P):
        return smin(s.core.d(P), s.arm.d(P), 1.0)

    def d(s, P):
        dp = s._palm(P)
        out = dp
        for f in s.fingers:
            out = np.minimum(out, smin(dp, f.d(P), s.k))
        return out

    def info(s, P):
        ds = [s.core.d(P), s.arm.d(P)] + [f.d(P) for f in s.fingers]
        ids = np.array([PARTS["palm"], PARTS["arm"]] + s.ids)[np.argmin(np.stack(ds), 0)]
        col = np.tile(SKIN, (len(P), 1))
        nail = np.zeros(len(P), bool)
        for pid, n in zip(s.ids, s.nails):
            nail |= (ids == pid) & (n.d(P) < 0.03)
        col[nail] = NAIL
        palm = ids == PARTS["palm"]
        if palm.any():
            Lc = (P[palm] - s.t) @ s.M
            w = np.clip(1 - crease_dist(Lc[:, :2]) / 0.13, 0, 1) * np.clip((Lc[:, 2] - 0.9) / 0.4, 0, 1)
            col[palm] = col[palm] * (1 - w[:, None]) + CREASE * w[:, None]
        along = (P - s.arm_o) @ s.arm_dir
        fade = np.clip((s.arm_len - 0.4 - along) / (s.arm_len * 0.55), 0, 1)
        fade = fade * fade * (3 - 2 * fade)
        return s.hid * 16 + ids, col, fade, nail


class Obj:
    """A plain object: one SDF node, one colour."""

    def __init__(s, node, color=(0.56, 0.6, 0.67), oid=0):
        s.node, s.color, s.oid = node, V(*color), oid

    def xf(s, M, t):
        return Obj(s.node.xf(M, t), s.color, s.oid)

    def bounds(s):
        return s.node.bounds()

    def d(s, P):
        return s.node.d(P)

    def info(s, P):
        n = len(P)
        return np.full(n, 100 + s.oid), np.tile(s.color, (n, 1)), np.ones(n), np.zeros(n, bool)


# ------------------------------------------------------------------ rendering

LIGHT = unit((-0.45, 0.7, 0.6))
INK = V(0.1, 0.11, 0.13)


def scene_d(items, P):
    d = items[0].d(P)
    for it in items[1:]:
        d = np.minimum(d, it.d(P))
    return d


def to_cam(items, cam):
    return [it.xf(cam["R"], V(0, 0, 0)) for it in items]


def march(items, cam, W, ss):
    """Orthographic sphere tracing along -z in camera space. Returns per-pixel buffers."""
    s = cam["scale"] / ss
    Wp = W * ss
    cx, cy = cam["center"]
    bs = [b for it in items for b in it.bounds()]
    C = np.array([b[0] for b in bs])
    Rr = np.array([b[1] for b in bs])
    zmin = (C[:, 2] - Rr).min() - 0.1
    px0 = int(max(0, math.floor(((C[:, 0] - Rr).min() - cx) / s + Wp / 2)))
    px1 = int(min(Wp, math.ceil(((C[:, 0] + Rr).max() - cx) / s + Wp / 2)))
    py0 = int(max(0, math.floor((cy - (C[:, 1] + Rr).max()) / s + Wp / 2)))
    py1 = int(min(Wp, math.ceil((cy - (C[:, 1] - Rr).min()) / s + Wp / 2)))
    out = dict(hit=np.zeros((Wp, Wp), bool), z=np.full((Wp, Wp), -1e9), id=np.full((Wp, Wp), -1, int),
               rgb=np.zeros((Wp, Wp, 3)), fade=np.zeros((Wp, Wp)), nail=np.zeros((Wp, Wp), bool))
    if px1 <= px0 or py1 <= py0:
        return out
    us = cx + (np.arange(px0, px1) + 0.5 - Wp / 2) * s
    vs = cy - (np.arange(py0, py1) + 0.5 - Wp / 2) * s
    UU, VV = np.meshgrid(us, vs)
    n = UU.size
    P = np.zeros((n, 3))
    P[:, 0], P[:, 1] = UU.ravel(), VV.ravel()
    zst = np.full(n, -1e9)
    for c, r in bs:  # start each ray where it enters the first bounding sphere
        dd = r * r - (P[:, 0] - c[0]) ** 2 - (P[:, 1] - c[1]) ** 2
        ok = dd > 0
        zst[ok] = np.maximum(zst[ok], c[2] + np.sqrt(dd[ok]))
    act = np.nonzero(zst > -1e8)[0]
    P[:, 2] = zst + 0.05
    hit = np.zeros(n, bool)
    eps = 0.0025
    for _ in range(320):
        if act.size == 0:
            break
        d = scene_d(items, P[act])
        h = d < eps
        hit[act[h]] = True
        z = P[act, 2] - d * 0.9
        P[act[~h], 2] = z[~h]
        act = act[(~h) & (z > zmin)]
    if act.size:
        d = scene_d(items, P[act])
        hit[act[d < 0.03]] = True
    hi = np.nonzero(hit)[0]
    if hi.size == 0:
        return out
    Ph = P[hi]
    # normals (tetrahedron)
    e = 0.01
    K = np.array([[1, -1, -1], [-1, -1, 1], [-1, 1, -1], [1, 1, 1]], float)
    N = np.zeros_like(Ph)
    for k in K:
        N += k * scene_d(items, Ph + k * e)[:, None]
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    # ambient occlusion
    occ = np.zeros(len(Ph))
    for i in range(5):
        hh = 0.15 + 0.45 * i
        occ += (hh - scene_d(items, Ph + N * hh)) * (0.8 ** i)
    ao = np.clip(1.0 - 0.32 * occ, 0, 1)
    # soft shadow toward the key light
    sh = np.ones(len(Ph))
    t = np.full(len(Ph), 0.12)
    a2 = np.arange(len(Ph))
    P0 = Ph + N * 0.03
    for _ in range(40):
        if a2.size == 0:
            break
        hq = scene_d(items, P0[a2] + LIGHT * t[a2, None])
        sh[a2] = np.minimum(sh[a2], 7.0 * hq / t[a2])
        t[a2] += np.clip(hq, 0.06, 2.0)
        a2 = a2[(sh[a2] > 0.03) & (t[a2] < 30)]
    sh = np.clip(sh, 0, 1)
    sh = sh * sh * (3 - 2 * sh)
    # labels and base colours: nearest item wins
    dist = np.stack([it.d(Ph) for it in items])
    which = np.argmin(dist, 0)
    ids = np.zeros(len(Ph), int)
    base = np.zeros((len(Ph), 3))
    fade = np.ones(len(Ph))
    nail = np.zeros(len(Ph), bool)
    for k, it in enumerate(items):
        m = which == k
        if m.any():
            ids[m], base[m], fade[m], nail[m] = it.info(Ph[m])
    diff = np.clip(N @ LIGHT, 0, 1) * (0.25 + 0.75 * sh)
    hemi = 0.5 + 0.5 * N[:, 1]
    amb = (0.62 + 0.38 * hemi) * ao
    rim = np.clip(1 - np.clip(N[:, 2], 0, 1), 0, 1) ** 3 * 0.12
    spec = np.clip(N @ unit(LIGHT + V(0, 0, 1)), 0, 1) ** 24 * 0.08 * sh
    col = base * (0.5 * amb + 0.55 * diff)[:, None] + (rim + spec)[:, None]
    col = np.clip(col, 0, 1)
    yy, xx = np.divmod(np.arange(n)[hi], px1 - px0)
    yy += py0
    xx += px0
    out["hit"][yy, xx] = True
    out["z"][yy, xx] = Ph[:, 2]
    out["id"][yy, xx] = ids
    out["rgb"][yy, xx] = col
    out["fade"][yy, xx] = fade
    out["nail"][yy, xx] = nail
    return out


def disk(r):
    R = int(math.ceil(r + 1))
    return [(dx, dy, math.hypot(dx, dy)) for dy in range(-R, R + 1) for dx in range(-R, R + 1)
            if math.hypot(dx, dy) <= r + 0.5]


def smooth_pair(a, b):
    """No line where palm/forearm meets the same hand's fingers."""
    same = (a // 16 == b // 16) & (a < 100) & (b < 100)
    pa, pb = a % 16, b % 16
    return same & ((pa == 0) | (pa == 7) | (pb == 0) | (pb == 7))


def ink(buf, ss, out_w=2.6, in_w=2.3, zt=0.45, line=INK, nail_line=V(0.62, 0.58, 0.56)):
    """Shaded buffers -> premultiplied RGBA with outline and inner lines."""
    hit, Z, ID, rgb, F, NL = buf["hit"], buf["z"], buf["id"], buf["rgb"], buf["fade"], buf["nail"]
    ro, ri = out_w * ss / 2 + 0.5, in_w * ss / 2 + 0.25
    inner = np.zeros(hit.shape)
    outer = np.zeros(hit.shape)
    ofade = np.zeros(hit.shape)
    nl = np.zeros(hit.shape)
    Fh = np.where(hit, F, 0)
    for dx, dy, r in disk(max(ro, ri) + 1):
        Hq = np.roll(hit, (dy, dx), (0, 1))
        wo = np.clip(ro - r + 0.5, 0, 1)
        wi = np.clip(ri - r + 0.5, 0, 1)
        if wo > 0:
            m = (~hit) & Hq
            outer = np.maximum(outer, m * wo)
            ofade = np.maximum(ofade, np.roll(Fh, (dy, dx), (0, 1)) * (m * wo > 0))
        if wi > 0:
            Zq = np.roll(Z, (dy, dx), (0, 1))
            Iq = np.roll(ID, (dy, dx), (0, 1))
            e = hit & (((~Hq) & (r <= ro * 0.6)) | (Hq & (Zq - Z > zt)) |
                       (Hq & (Iq != ID) & ~smooth_pair(ID, Iq) & (Zq >= Z - 0.15)))
            inner = np.maximum(inner, e * wi)
            if r <= ss * 0.7 + 0.5:
                Nq = np.roll(NL, (dy, dx), (0, 1))
                nl = np.maximum(nl, (hit & Hq & (Nq != NL)) * 1.0)
    col = rgb.copy()
    col = col * (1 - nl[..., None] * 0.7) + nail_line * nl[..., None] * 0.7
    col = col * (1 - inner[..., None]) + line * inner[..., None]
    alpha = np.where(hit, F, outer * ofade)
    col = np.where(hit[..., None], col, line)
    return col * alpha[..., None], alpha


def over(dst_rgb, dst_a, src_rgb, src_a):
    return src_rgb + dst_rgb * (1 - src_a[..., None]), src_a + dst_a * (1 - src_a)


# ------------------------------------------------------------------ arrows

ACCENT = (255, 184, 64)
ACCENT_DARK = (24, 26, 32)


def arc(c, u, v, r, a0, a1, n=48):
    c, u, v = V(*c), unit(u), unit(v)
    a = np.radians(np.linspace(a0, a1, n))
    return [c + r * (math.cos(t) * u + math.sin(t) * v) for t in a]


def seg(p0, p1, n=12):
    p0, p1 = V(*p0), V(*p1)
    return [p0 + (p1 - p0) * t for t in np.linspace(0, 1, n)]


def project(cam, W, ss, p):
    q = cam["R"] @ V(*p)
    s = cam["scale"] / ss
    return ((q[0] - cam["center"][0]) / s + W * ss / 2, (cam["center"][1] - q[1]) / s + W * ss / 2)


def draw_labels(img, labels, cam, W, ss):
    """Short text labels at world points, light with a dark edge (Pillow's built-in font)."""
    dr = ImageDraw.Draw(img)
    sc = ss * W / 512
    font = ImageFont.load_default(size=int(round(42 * sc)))
    for lb in labels:
        x, y = project(cam, W, ss, lb["at"])
        dr.text((x, y), lb["text"], font=font, anchor=lb.get("anchor", "mm"), fill=(232, 235, 240, 255),
                stroke_width=int(round(4 * sc)), stroke_fill=ACCENT_DARK + (255,))
    return img


def draw_arrows(arrows, cam, W, ss):
    img = Image.new("RGBA", (W * ss, W * ss), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    sc = ss * W / 512
    lw, bw, hl, hw = 7 * sc, 3.2 * sc, 22 * sc, 15 * sc
    for a in arrows:
        pts = [project(cam, W, ss, p) for p in a["pts"]]
        heads = a.get("heads", "end")
        k = a.get("scale", 1.0)
        shapes = []

        def head(tip, prev):
            dx, dy = tip[0] - prev[0], tip[1] - prev[1]
            L = math.hypot(dx, dy) or 1
            dx, dy = dx / L, dy / L
            base = (tip[0] - dx * hl * k, tip[1] - dy * hl * k)
            return [(tip[0] + dx * 2 * sc, tip[1] + dy * 2 * sc),
                    (base[0] - dy * hw * k, base[1] + dx * hw * k),
                    (base[0] + dy * hw * k, base[1] - dx * hw * k)]

        def back_to(pts, dist):
            acc = 0
            for i in range(len(pts) - 1, 0, -1):
                acc += math.dist(pts[i], pts[i - 1])
                if acc >= dist:
                    return pts[i - 1]
            return pts[0]

        body = list(pts)
        if heads in ("end", "both"):
            shapes.append(head(pts[-1], back_to(pts, hl * k)))
            # stop the line under the arrow head
            while len(body) > 2 and math.dist(body[-1], pts[-1]) < hl * k * 0.6:
                body.pop()
        if heads in ("start", "both"):
            rp = pts[::-1]
            shapes.append(head(rp[-1], back_to(rp, hl * k)))
            while len(body) > 2 and math.dist(body[0], pts[0]) < hl * k * 0.6:
                body.pop(0)
        for width, colr, grow in ((lw * k + 2 * bw, ACCENT_DARK, bw), (lw * k, ACCENT, 0)):
            dr.line(body, fill=colr, width=int(round(width)), joint="curve")
            for e in (body[0], body[-1]):
                r = width / 2
                dr.ellipse([e[0] - r, e[1] - r, e[0] + r, e[1] + r], fill=colr)
            for sh in shapes:
                if grow:
                    cxs = sum(p[0] for p in sh) / 3
                    cys = sum(p[1] for p in sh) / 3
                    big = []
                    for p in sh:
                        dx, dy = p[0] - cxs, p[1] - cys
                        L = math.hypot(dx, dy) or 1
                        big.append((p[0] + dx / L * grow * 1.8, p[1] + dy / L * grow * 1.8))
                    dr.polygon(big, fill=colr)
                else:
                    dr.polygon(sh, fill=colr)
    return img


# ------------------------------------------------------------------ layout


def fit(spec, W):
    """Pick scale and centre so that everything fits with a margin."""
    cam = spec["cam"]
    pts = []
    lo = W // 4
    tmp = dict(cam, scale=1.0, center=(0.0, 0.0))
    for layer in spec["layers"]:
        items = to_cam(layer["items"], cam)
        bs = [b for it in items for b in it.bounds()]
        C = np.array([b[0] for b in bs])
        Rr = np.array([b[1] for b in bs])
        ext = max(np.abs(C[:, :2]).max() + Rr.max(), 1.0) * 2.1
        tmp = dict(cam, scale=ext / lo, center=(0.0, 0.0))
        buf = march(items, tmp, lo, 1)
        yy, xx = np.nonzero(buf["hit"] & (buf["fade"] > 0.35))
        s = tmp["scale"]
        for x, y in ((xx.min(), yy.min()), (xx.max() + 1, yy.max() + 1)):
            pts.append((((x - lo / 2) * s), (-(y - lo / 2) * s)))
    for a in spec.get("arrows", []):
        for p in a["pts"]:
            q = cam["R"] @ V(*p)
            pts.append((q[0], q[1]))
    for lb in spec.get("labels", []):  # rough text extent: about 2 cm per side
        q = cam["R"] @ V(*lb["at"])
        pts += [(q[0] - lb.get("half", 3.0), q[1] - 1.5), (q[0] + lb.get("half", 3.0), q[1] + 1.5)]
    pts = np.array(pts)
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    margin = 26 if spec.get("arrows") else 14
    scale = max(spec.get("scale", 0.056), (x1 - x0) / (W - 2 * margin), (y1 - y0) / (W - 2 * margin))
    return dict(cam, scale=scale, center=((x0 + x1) / 2, (y0 + y1) / 2))


def render_pose(spec, W=512, ss=2):
    cam = fit(spec, W)
    Wp = W * ss
    rgb = np.zeros((Wp, Wp, 3))
    a = np.zeros((Wp, Wp))
    for layer in spec["layers"]:
        items = to_cam(layer["items"], cam)
        for i, it in enumerate(items):
            if isinstance(it, Hand):
                it.hid = i
        buf = march(items, cam, W, ss)
        ghost = layer.get("ghost", 0)
        if ghost:
            buf["rgb"] = buf["rgb"] * 0.55 + V(0.62, 0.78, 1.0) * 0.45
            lrgb, la = ink(buf, ss * W / 512, out_w=2.0, in_w=1.2, line=V(0.25, 0.3, 0.4))
            lrgb, la = lrgb * ghost, la * ghost
        else:
            lrgb, la = ink(buf, ss * W / 512)
        rgb, a = over(rgb, a, lrgb, la)
    if spec.get("arrows") or spec.get("labels"):
        im = draw_arrows(spec.get("arrows", []), cam, W, ss)
        im = draw_labels(im, spec.get("labels", []), cam, W, ss)
        arr = np.asarray(im, float) / 255
        aa = arr[..., 3]
        rgb, a = over(rgb, a, arr[..., :3] * aa[..., None], aa)
    # downsample (premultiplied box filter)
    rgb = rgb.reshape(W, ss, W, ss, 3).mean((1, 3))
    a = a.reshape(W, ss, W, ss).mean((1, 3))
    col = np.where(a[..., None] > 1e-4, rgb / np.maximum(a[..., None], 1e-4), 0)
    img = np.dstack([np.clip(col, 0, 1) * 255, np.clip(a, 0, 1) * 255]).round().astype(np.uint8)
    return Image.fromarray(img, "RGBA")


# ------------------------------------------------------------------ poses

STRAIGHT = (0, 0, 0)


def P(index, middle, ring, pinky, thumb, **kw):
    d = dict(index=index, middle=middle, ring=ring, pinky=pinky, thumb=thumb)
    d.update(kw)
    return d


def ab(f, a):
    return tuple(f[:3]) + (a,)


CURL = {"index": (86, 100, 58, -2), "middle": (88, 102, 58, 1), "ring": (90, 102, 56, 4), "pinky": (92, 98, 52, 8)}
FLAT = P((0, 0, 0, 1), (0, 0, 0, 0), (0, 0, 0, -1), (0, 0, 0, -3), (16, 6, 6, 4))
SPREAD = P((-2, 0, 0, 14), (-2, 0, 0, 2), (-2, 0, 0, -11), (-2, 0, 0, -25), (42, 14, -4, -8))
RELAX = P((12, 16, 8, 6), (15, 20, 10, 1), (18, 24, 12, -5), (22, 28, 14, -12), (38, 24, 12, 14))
FIST = P(CURL["index"], CURL["middle"], CURL["ring"], CURL["pinky"],
         ("to", lambda J: back_point(J["middle"], 1, 0.35), (-5, 40, 40, 25)))
CLAW = P((-10, 80, 70, 14), (-10, 86, 72, 2), (-10, 86, 72, -11), (-10, 84, 68, -24), (45, 30, 35, 55))
POINT = P((0, 0, 0, 0), CURL["middle"], CURL["ring"], CURL["pinky"],
          ("to", lambda J: back_point(J["middle"], 1, 0.4), (-5, 40, 40, 25)))
OK = P((55, 50, 30, 3), (4, 6, 3, -3), (6, 8, 4, -13), (8, 10, 5, -26),
       ("to", lambda J: pad_point(J["index"], 0.0), (12, 50, 5, 15)))
PINCH = P((40, 48, 24, 3), (70, 80, 44, 0), (76, 84, 46, -1), (80, 84, 46, -2),
          ("to", lambda J: pad_point(J["index"], 0.0), (25, 40, 10, 10)))
PINCH_OPEN = P((34, 40, 18, 4), (46, 60, 30, 0), (54, 66, 34, -2), (60, 68, 36, -5),
               ("to", lambda J: pad_point(J["index"], 2.8), (30, 35, 5, 5)))
THUMBS = P(CURL["index"], CURL["middle"], CURL["ring"], CURL["pinky"], (62, 12, -5, -8))
GRIP = P((52, 70, 36, 0), (55, 72, 36, 1), (58, 72, 34, 3), (62, 70, 32, 6), (8, 48, 30, 25))


def COUNT(n):
    up = ["index", "middle", "ring"][:n]
    f = {}
    spread = {"index": 4, "middle": -1, "ring": -6}
    for name in ("index", "middle", "ring", "pinky"):
        f[name] = (0, 0, 0, spread[name]) if name in up else CURL[name]
    first_down = ["index", "middle", "ring", "pinky"][n]
    th = ("to", lambda J: back_point(J[first_down], 1, 0.4), (-5, 40, 40, 25))
    return P(f["index"], f["middle"], f["ring"], f["pinky"], th)


COUNT4 = P((0, 0, 0, 6), (0, 0, 0, 0), (0, 0, 0, -6), (0, 0, 0, -14),
           ("to", lambda J: (-1.6, 7.6, 2.3), (-15, 40, 40, 20)))

UP = np.eye(3)                      # palm toward you, fingers up
BACK = Ry(180)                      # back of the hand toward you
PALM_DOWN_FWD = orient((0, 0, -1), (0, -1, 0))  # on a desk: fingers away, palm down


def H(pose, R=UP, t=(0, 0, 0), **kw):
    return Hand(pose, R, t, **kw)


def L(*items, ghost=0):
    return dict(items=list(items), ghost=ghost)


def cam(pitch=0, yaw=0):
    """World -> camera. pitch > 0 looks down, yaw > 0 looks from the right."""
    return dict(R=Rx(pitch) @ Ry(-yaw))


CTRL_GRIP = P((50, 66, 36, 0), (54, 70, 36, 1), (58, 70, 34, 3), (62, 68, 32, 6), (30, 45, 10, 12))


def ctrl_local():
    """A generic VR controller in the hand frame of CTRL_GRIP (hand local coordinates):
    a handle across the curled fingers, a head above the thumb with a thumbstick,
    and a strap over the back of the hand."""
    y, z, r = grip_hole(resolve(CTRL_GRIP))
    ax = unit((0.45, 1.0, 0.1))
    c = V(0.0, y - 0.6, z)
    a, b = c - ax * 4.5, c + ax * 4.0
    grip = Cone(a, b, r * 0.92, r * 1.02)
    head_c = b + ax * 1.6 + V(0.9, 0.0, 0.4)
    head = Ell(head_c, orient(ax, (0.3, 0, 1)), (2.4, 2.0, 2.0))
    body = U([grip, head], 0.9)
    up = unit((0.55, 0.55, 0.65))
    sb = head_c + up * 1.75
    stick = U([Cone(sb - up * 0.4, sb + up * 0.7, 0.36, 0.36),
               Cyl(sb + up * 0.85, orient(up, (0, 0, 1)), 0.78, 0.2, 0.12)])
    strap = Box((0.3, y - 1.2, -2.3), orient(ax, (0, 0, 1)), (0.95, 4.4, 0.1), 0.18)
    return dict(body=body, stick=stick, strap=strap, top=sb + up * 1.05)


CTRL = None


def ctrl_parts(M=np.eye(3), t=(0, 0, 0)):
    global CTRL
    if CTRL is None:
        CTRL = ctrl_local()
    t = V(*t)
    return [Obj(CTRL["body"], (0.5, 0.54, 0.6), 1).xf(M, t), Obj(CTRL["stick"], (0.3, 0.32, 0.37), 2).xf(M, t),
            Obj(CTRL["strap"], (0.36, 0.38, 0.43), 3).xf(M, t)]


def stick_top(hand):
    if CTRL is None:
        ctrl_parts()
    return hand.world(CTRL["top"])


def with_ctrl(hand):
    """Controller objects placed in a hand's frame."""
    return ctrl_parts(hand.M, hand.t)


def ctrl_open_parts(hand):
    """The controller on an open hand, held on by its strap (hand local, then moved with the hand):
    the body lies against the palm from the heel up to the base of the thumb, the head sits
    by the thumb, the strap crosses the back of the hand, and the fingers stay free."""
    a, b = V(-1.7, 1.4, 3.5), V(1.9, 7.4, 3.4)
    ax = unit(b - a)
    grip = Cone(a, b, 1.45, 1.6)
    head_c = b + ax * 1.7 + V(0.8, 0.0, 0.5)
    head = Ell(head_c, orient(ax, (0.3, 0, 1)), (2.3, 1.9, 1.9))
    up = unit((0.3, 0.3, 1.0))
    sb = head_c + up * 1.75
    stick = U([Cone(sb - up * 0.4, sb + up * 0.6, 0.36, 0.36),
               Cyl(sb + up * 0.75, orient(up, (1, 0, 0)), 0.78, 0.2, 0.12)])
    band = Box((0.0, 4.4, -2.2), orient(ax, (0, 0, 1)), (0.95, 3.8, 0.1), 0.18)
    ends = [Cone((-1.7, 0.9, -2.15), (-3.9, 1.3, 0.2), 0.3, 0.3), Cone((-3.9, 1.3, 0.2), (-2.0, 1.4, 2.6), 0.3, 0.3),
            Cone((1.7, 7.9, -2.15), (3.9, 7.6, 0.2), 0.3, 0.3), Cone((3.9, 7.6, 0.2), (2.2, 7.4, 2.6), 0.3, 0.3)]
    M, t = hand.M, hand.t
    return [Obj(U([grip, head], 0.9), (0.5, 0.54, 0.6), 1).xf(M, t), Obj(stick, (0.3, 0.32, 0.37), 2).xf(M, t),
            Obj(U([band] + ends, 0.25), (0.36, 0.38, 0.43), 3).xf(M, t)]


def keyboard(c=(0, 0, 0), nx=14, nz=5):
    pitch = 1.9
    w, dpt = nx * pitch / 2 + 0.6, nz * pitch / 2 + 0.6
    base = Box((c[0], c[1] + 0.8, c[2]), np.eye(3), (w - 0.4, 0.4, dpt - 0.4), 0.4)
    keys = Keys((c[0], c[1] + 1.75, c[2]), np.eye(3), nx, nz, pitch, (0.62, 0.18, 0.62), 0.12)
    return [Obj(base, (0.42, 0.45, 0.5), 4), Obj(keys, (0.6, 0.64, 0.7), 5)]


def mouse(c=(0, 0, 0)):
    body = Clip(Ell((c[0], c[1] + 0.2, c[2]), np.eye(3), (3.1, 3.6, 5.6)), (c[0], c[1], c[2]), (0, -1, 0))
    groove = Box((c[0], c[1] + 3.6, c[2] - 3.0), np.eye(3), (0.08, 1.2, 2.6), 0.02)
    return [Obj(Sub(body, groove), (0.56, 0.6, 0.67), 6)]


def cup(c=(0, 0, 0)):
    c = V(*c)
    outer = Cyl(c + V(0, 4.75, 0), np.eye(3), 3.9, 4.75, 0.35)
    inner = Cyl(c + V(0, 5.6, 0), np.eye(3), 3.45, 4.75, 0.3)
    handle = Torus(c + V(-4.6, 5.2, 0), Rx(90), 2.0, 0.45)
    return [Obj(U([Sub(outer, inner), Clip(handle, c + V(-4.2, 0, 0), (1, 0, 0))], 0.3), (0.56, 0.6, 0.67), 7)]


def place(hand_pose, R, anchor, target, joint=("middle", 3), **kw):
    """Hand with a joint (default: middle fingertip) at a target point."""
    h = Hand(hand_pose, R, (0, 0, 0), **kw)
    p = h.joint(*joint)
    if kw.get("left"):
        p = p  # joint() already includes the mirror
    off = V(*target) - p + V(*anchor)
    return Hand(hand_pose, R, (np.diag([-1.0, 1, 1]) if kw.get("left") else np.eye(3)) @ off, **kw)


def typing_hands(lift=0.0):
    pose = P((18, 42, 18, 3), (20, 46, 20, 0), (22, 48, 20, -3), (26, 46, 20, -7), (30, 28, 10, 10),
             wrist=(-12, 0))
    R = PALM_DOWN_FWD
    rh = place(pose, R, (0, lift, 0), (4.0, 2.55 + 0.74, -1.0))
    lh = place(pose, R, (0, lift, 0), (-4.0, 2.55 + 0.74, -1.0), left=True)
    return rh, lh


def spec_typing():
    rh, lh = typing_hands()
    return dict(cam=cam(48), layers=[L(*keyboard(), rh, lh)])


def spec_lift():
    rh, lh = typing_hands()
    g1, g2 = typing_hands(lift=7)
    return dict(cam=cam(48), layers=[L(g1, g2, ghost=0.5), L(*keyboard(), rh, lh)],
                arrows=[dict(pts=seg((13.5, 4, 3), (13.5, 11, 3)), heads="both")])


MOUSE_POSE = P((12, 22, 10, 2), (12, 24, 12, -1), (30, 50, 26, -6), (40, 54, 28, -12), (52, 18, 10, 8),
               wrist=(-8, 0))


def mouse_hand(c=(0, 0, 0)):
    return place(MOUSE_POSE, PALM_DOWN_FWD, (0, 0, 0), V(*c) + V(0.4, 3.6, -4.6))


def spec_mouse():
    return dict(cam=cam(35, -40), layers=[L(*mouse(), mouse_hand())])


def spec_switch():
    rh, lh = typing_hands()
    mh = mouse_hand((26, 0, 0))
    return dict(cam=cam(48), layers=[L(mh, ghost=0.5), L(*keyboard(), *mouse((26, 0, 0)), rh, lh)],
                arrows=[dict(pts=arc((17, 6, 2), (1, 0, 0), (0, 1, 0), 7, 160, 20), heads="both")])


def grip_hole(J, name="middle", palm_z=1.9):
    """Centre (y, z) and radius of the biggest bar that fits inside a curled finger (hand local)."""
    f = J[name]
    best = (0.0, 0.0, 0.0)
    ys = [p[1] for p in f["pts"]]
    zs = [p[2] for p in f["pts"]]
    for y in np.linspace(ys[0], max(ys), 60):
        for z in np.linspace(palm_z, max(zs), 60):
            r = z - palm_z
            c = V(y, z)
            for i in range(3):
                a, b = f["pts"][i][1:], f["pts"][i + 1][1:]
                t = np.clip((c - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1)
                r = min(r, np.linalg.norm(c - (a + (b - a) * t)) - f["radii"][i + 1])
            if r > best[2]:
                best = (y, z, r)
    return best


GLASS = P((40, 70, 40, 0), (42, 72, 40, 1), (44, 72, 38, 3), (48, 70, 36, 6),
          ("to", lambda J: (2.4, 9.0, 6.4), (10, 50, 20, 20)))
HOLD_VIEW = Rz(75) @ Ry(-15)


def spec_hold():
    h = H(GLASS, HOLD_VIEW)
    y, z, r = grip_hole(h.J)
    c = h.world((0.3, y, z))
    ax = unit(h.M @ V(1, 0, 0))
    R = orient(ax, (0, 0, 1))  # the bottle's axis runs along the hand's x axis
    body = Cyl(c + ax * 1.0, R, r - 0.05, 6.5, 0.4)
    neck = Cone(c + ax * 7.0, c + ax * 9.6, r * 0.75, r * 0.45)
    cap = Cyl(c + ax * 10.0, R, r * 0.5, 0.6, 0.15)
    bottle = Obj(U([body, neck, cap], 0.6), (0.56, 0.6, 0.67), 7)
    top = c + ax * 9.5
    return dict(cam=cam(12), layers=[L(bottle, h)],
                arrows=[dict(pts=seg(top + V(7, -6, 0), top + V(7, 1, 0)), heads="both", scale=0.85)])


def touch_scene(lR, f, p, lt=(0, 0, 0), left=True):
    """A hand holding a controller (left by default) and the other index on its thumbstick."""
    ch = Hand(CTRL_GRIP, lR, lt, left=left)
    up = ch.M @ unit((0.55, 0.55, 0.65))
    tgt = stick_top(ch) + up * 0.7
    ph = place(POINT, orient(f, p), (0, 0, 0), tgt, joint=("index", 3), left=not left)
    return [ch, *with_ctrl(ch), ph]


def spec_touch_stick():
    return dict(cam=cam(20), layers=[L(*touch_scene(Rz(-10) @ Rx(-50), (-1, -1, -0.6), (-0.3, -0.6, -1)))])


def spec_touch_stick_desk(f=(-1, -0.8, 0), p=(0, -1, -0.3)):
    hold = Hand(CTRL_GRIP, orient((0, 0, -1), (0, 1, 0)))
    objs = with_ctrl(hold)[:2]  # no strap: it hangs loose on the desk
    up = hold.M @ unit((0.55, 0.55, 0.65))
    tgt = stick_top(hold) + up * 0.7
    rh = place(POINT, orient(f, p), (0, 0, 0), tgt, joint=("index", 3))
    return dict(cam=cam(25), layers=[L(*objs, rh)])


def head_and_headset(R=np.eye(3)):
    """The wearer's head with a headset on, facing -z (the head's local frame), as two objects."""
    head = Ell((0, 0, 0), np.eye(3), (7.6, 10.2, 8.8))
    nose = Cone((0, -1.5, -8.2), (0, -3.6, -9.6), 0.7, 0.9)
    visor = Box((0, 2.4, -6.5), np.eye(3), (8.2, 2.6, 2.6), 1.4)
    band = Torus((0, 3.0, 0.5), Rx(8), 8.0, 0.9)
    z = V(0, 0, 0)
    return [Obj(U([head, nose], 0.6), (0.5, 0.53, 0.58), 8).xf(R, z),
            Obj(U([visor, band], 0.4), (0.36, 0.38, 0.43), 9).xf(R, z)]


def push_hand(pose, z, controllers=False):
    """A right hand pushed out in front of the face (forward is -z), palm out, arm reaching back."""
    fwd = unit((0, 0.42, -1))                      # the forearm points forward and a little up
    R = orient(fwd, unit(np.cross(fwd, (1, 0, 0))))  # palm down along the arm
    h = Hand(dict(pose, wrist=(-66, 0)), R, (3.0, -17.0, z), arm=15)  # wrist bent back: fingers up
    return [h] + (ctrl_open_parts(h) if controllers else [])


def push_spec(controllers=False):
    pose = FLAT  # open hand, palm out; with controllers the strap holds the controller on
    near, far = -21.0, -44.0
    eye = V(0, 2.4, -10.5)
    tip_y = 0.0
    return dict(cam=cam(4, 65), scale=0.11,
                layers=[L(*push_hand(pose, far, controllers), ghost=0.45),
                        L(*head_and_headset(), *push_hand(pose, near, controllers))],
                arrows=[dict(pts=seg(eye + V(0, tip_y, -1.0), V(0, eye[1] + tip_y, far - 7.0)), heads="both")],
                labels=[dict(at=(0, eye[1] + tip_y + 4.2, near - 1.0), text="near", half=4.0),
                        dict(at=(0, eye[1] + tip_y + 4.2, far - 2.0), text="arm out", half=7.0)])


def ghost_pair(main_pose, ghost_pose, R, cam_, arrows, ghost_alpha=0.45, **kw):
    return dict(cam=cam_, layers=[L(H(ghost_pose, R, **kw), ghost=ghost_alpha), L(H(main_pose, R, **kw))],
                arrows=arrows)


def spec_cross():
    R = Rz(30)
    rh = H(FLAT, R, (-2.2, 0, 1.6), arm=12)
    lh = H(FLAT, R, (-2.2, 0, -1.6), left=True, arm=12)
    return dict(cam=cam(), layers=[L(rh, lh)],
                arrows=[dict(pts=seg((-5, 22, 3), (-14, 22, 3)), scale=0.85),
                        dict(pts=seg((5, 22, 3), (14, 22, 3)), scale=0.85)])


def spec_overlap():
    rh = H(FLAT, Rz(8), (-2.5, 2.5, 3))
    lh = H(FLAT, Rz(8), (-2.5, -2.5, -3), left=True)
    return dict(cam=cam(), layers=[L(rh, lh)],
                arrows=[dict(pts=arc((0, 9, 4), (1, 0, 0), (0, 1, 0), 13.5, 35, 145), heads="both")])


def spec_near_face():
    head = Ell((0, 0, 0), np.eye(3), (7.6, 10.2, 8.8))
    visor = Box((0, 2.4, 6.5), np.eye(3), (8.2, 2.6, 2.6), 1.4)
    band = Torus((0, 3.0, -0.5), Rx(-8), 8.0, 0.9)
    objs = [Obj(U([head]), (0.5, 0.53, 0.58), 8), Obj(U([visor, band], 0.4), (0.36, 0.38, 0.43), 9)]
    R = orient((0, 1, 0.15), (1, 0, 0.2))
    rh = H(RELAX, R, (-12.5, -9, 4))
    lh = H(RELAX, R, (-12.5, -9, 4), left=True)
    return dict(cam=cam(5, 22), layers=[L(*objs, rh, lh)],
                arrows=[dict(pts=arc((0, 2, 0), (1, 0, 0), (0, 1, 0), 15.5, 40, 140), heads="both")])


def spec_screen_point():
    R = Rx(-35) @ BACK
    h = H(POINT, R, (0, -12, 0))
    tip = h.joint("index", 3)
    scr = Box((2, tip[1] + 9, -30), np.eye(3), (14, 8, 0.4), 0.5)
    stand = Box((2, tip[1] - 1, -30.5), np.eye(3), (1.2, 2.5, 0.3), 0.3)
    return dict(cam=cam(8), layers=[L(Obj(U([scr, stand]), (0.42, 0.45, 0.5), 10), h)],
                arrows=[dict(pts=seg(tip + V(0, 1.2, 0), tip + V(0, 7.0, -20)), scale=0.75)])


def spec_turn_in_out():
    return dict(cam=cam(16), layers=[L(H(RELAX, Ry(-25)))],
                arrows=[dict(pts=arc((0, -1.5, 0), (1, 0, 0), (0, 0, 1), 6.5, -20, 200), heads="both")])


def spec_turn_up_down():
    R = PALM_DOWN_FWD
    Rg = orient((0, 0, -1), (0, 1, 0))
    return dict(cam=cam(40), layers=[L(H(FLAT, Rg, (0, 0.2, 0)), ghost=0.4), L(H(FLAT, R))],
                arrows=[dict(pts=arc((0, 0, 0.5), (1, 0, 0), (0, 1, 0), 7.0, 15, 165), heads="both")])


def spec_bend():
    R = PALM_DOWN_FWD
    g1 = H(dict(FLAT, wrist=(0, 28)), R)
    g2 = H(dict(FLAT, wrist=(0, -28)), R)
    g3 = H(dict(FLAT, wrist=(-35, 0)), R)
    return dict(cam=cam(38), layers=[L(g1, g2, ghost=0.35), L(g3, ghost=0.35), L(H(FLAT, R))],
                arrows=[dict(pts=arc((0, 1, 0), (1, 0, 0), (0, 0, -1), 21, 60, 120), heads="both"),
                        dict(pts=arc((0, 1, 0), (0, 0, -1), (0, 1, 0), 21, 10, 50), heads="both", scale=0.8)])


PINCH_VIEW = Ry(-90)


def spec_pinch_tap():
    h = H(PINCH_OPEN, PINCH_VIEW)
    a, b = h.joint("index", 3), h.joint("thumb", 3)
    side = V(-3.2, 0, 0)
    return dict(cam=cam(20), layers=[L(h)],
                arrows=[dict(pts=seg(a + side + V(0, 1.0, 0), b + side - V(0, 1.0, 0)), heads="both", scale=0.7)])


def spec_pinch_drag():
    h = H(PINCH, PINCH_VIEW)
    g = H(PINCH, PINCH_VIEW, (15, 0, 0))
    top = h.joint("middle", 1)
    return dict(cam=cam(20), layers=[L(g, ghost=0.4), L(h)],
                arrows=[dict(pts=seg(top + V(1, 3.5, 0), top + V(12, 3.5, 0)))])


def spec_grab():
    R = Ry(-60)
    h = H(GRIP, R)
    y, z, r = grip_hole(h.J)
    c = h.world((-0.4, y, z))
    bar = Cyl(c, orient(h.M @ V(1, 0, 0), (0, 0, 1)), r - 0.05, 8, 0.3)
    return dict(cam=cam(10), layers=[L(H(RELAX, R), ghost=0.4), L(h, Obj(bar, (0.5, 0.54, 0.6), 11))],
                arrows=[dict(pts=arc(c, (0, 1, 0), (1, 0, 0), 9.5, 20, 100), heads="both", scale=0.85)])


def spec_open_close():
    R = UP
    return dict(cam=cam(), layers=[L(H(SPREAD, R), ghost=0.45), L(H(FIST, R))],
                arrows=[dict(pts=arc((0, 11, 3), (1, 0, 0), (0, 1, 0), 9.5, -20, 60), heads="both", scale=0.85)])


def spec_no_hands():
    g = H(RELAX, BACK, (0, 6, 0))
    return dict(cam=cam(), layers=[L(g, ghost=0.4)],
                arrows=[dict(pts=seg((7.5, 12, 4), (7.5, -2, 4)))])


POSES = {
    "flat": ("Palm toward you, fingers together", False, lambda: dict(cam=cam(), layers=[L(H(FLAT))])),
    "flat-back": ("Back of the hand toward you", False, lambda: dict(cam=cam(), layers=[L(H(FLAT, BACK))])),
    "spread": ("Fingers spread wide", False, lambda: dict(cam=cam(), layers=[L(H(SPREAD))])),
    "open": ("Open and relaxed", False, lambda: dict(cam=cam(), layers=[L(H(RELAX, Ry(-25) @ Rx(-10)))])),
    "fist": ("A fist", False, lambda: dict(cam=cam(), layers=[L(H(FIST, Ry(-20)))])),
    "point": ("Point with your index finger", False,
              lambda: dict(cam=cam(20, -30), layers=[L(H(POINT, orient((0.1, 0.5, -1), (-0.6, -1, 0))))])),
    "pinch": ("Thumb and index tips touching", False,
              lambda: dict(cam=cam(20), layers=[L(H(PINCH, Ry(-90)))])),
    "ok": ("OK sign: a ring, three fingers up", False,
           lambda: dict(cam=cam(10), layers=[L(H(OK, Ry(-70)))])),
    "thumbs-up": ("Thumbs up", False, lambda: dict(cam=cam(), layers=[L(H(THUMBS, Rz(75) @ Ry(-15)))])),
    "claw": ("Fingers curled like a claw", False,
             lambda: dict(cam=cam(35), layers=[L(H(CLAW, Ry(-20)))])),
    "count-1": ("One: index finger", False, lambda: dict(cam=cam(), layers=[L(H(COUNT(1)))])),
    "count-2": ("Two: index and middle", False, lambda: dict(cam=cam(), layers=[L(H(COUNT(2)))])),
    "count-3": ("Three: index, middle, ring", False, lambda: dict(cam=cam(), layers=[L(H(COUNT(3)))])),
    "count-4": ("Four: thumb folded in", False, lambda: dict(cam=cam(), layers=[L(H(COUNT4))])),
    "count-5": ("Five: all fingers spread", False, lambda: dict(cam=cam(), layers=[L(H(SPREAD))])),
    "turn-in-out": ("Turn your wrist: palm in, palm out", False, spec_turn_in_out),
    "turn-up-down": ("Palm down, turn it up, then back", False, spec_turn_up_down),
    "bend": ("Bend your wrist up, down, side to side", False, spec_bend),
    "pinch-tap": ("Tap thumb and index together", False, spec_pinch_tap),
    "pinch-drag": ("Pinch, move to the side, let go", False, spec_pinch_drag),
    "grab": ("Close your hand around a bar, open it", False, spec_grab),
    "open-close": ("Open and close your hand", False, spec_open_close),
    "cross": ("Cross your hands, then uncross", True, spec_cross),
    "overlap": ("One hand over the other, slide around", True, spec_overlap),
    "near-face": ("Hands near your face, not touching", True, spec_near_face),
    "screen-point": ("Point at a screen at arm's length", False, spec_screen_point),
    "typing": ("Type on the keyboard", True, spec_typing),
    "lift": ("Lift your hands off the keyboard, put them back", True, spec_lift),
    "mouse": ("Hand on the mouse, move and click", False, spec_mouse),
    "switch": ("Switch between keyboard and mouse", True, spec_switch),
    "hold": ("Pick it up, use it, put it down", True, spec_hold),
    "push": ("Push straight out, away from the headset, and back", True, lambda: push_spec(False)),
    "push-controller": ("Controllers on: push straight out from the headset, and back", True,
                        lambda: push_spec(True)),
    "touch-stick": ("Touch the thumbstick with your other index", True, spec_touch_stick),
    "touch-stick-desk": ("Touch the thumbstick of the controller on the desk", True, spec_touch_stick_desk),
    "no-hands": ("Hands down, out of view", False, spec_no_hands),
}


# ------------------------------------------------------------------ main


def _job(args):
    pid, out, W, ss = args
    img = render_pose(POSES[pid][2](), W, ss)
    img.save(os.path.join(out, pid + ".png"), optimize=True)
    return pid


def contact_sheet(ids, out, thumb=240, cols=6):
    rows = (len(ids) + cols - 1) // cols
    pad, lab = 14, 26
    W = cols * (thumb + pad) + pad
    Hh = rows * (thumb + lab + pad) + pad
    sheet = Image.new("RGBA", (W, Hh), (22, 24, 29, 255))
    dr = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=17)
    except TypeError:
        font = ImageFont.load_default()
    for i, pid in enumerate(ids):
        x = pad + (i % cols) * (thumb + pad)
        y = pad + (i // cols) * (thumb + lab + pad)
        dr.rounded_rectangle([x, y, x + thumb, y + thumb], 10, fill=(34, 37, 44, 255))
        im = Image.open(os.path.join(out, pid + ".png")).convert("RGBA").resize((thumb, thumb), Image.LANCZOS)
        sheet.alpha_composite(im, (x, y))
        dr.text((x + thumb / 2, y + thumb + 4), pid, fill=(220, 224, 230, 255), font=font, anchor="mt")
    sheet.convert("RGB").save(os.path.join(out, "contact-sheet.png"), optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--only", default="", help="comma-separated pose ids")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--ss", type=int, default=3, help="supersampling per axis")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--no-sheet", action="store_true")
    a = ap.parse_args()
    ids = [p for p in a.only.split(",") if p] or list(POSES)
    bad = [p for p in ids if p not in POSES]
    if bad:
        sys.exit("unknown pose: " + ", ".join(bad))
    os.makedirs(a.out, exist_ok=True)
    work = [(p, a.out, a.size, a.ss) for p in ids]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            for pid in ex.map(_job, work):
                print(pid, flush=True)
    else:
        for w in work:
            print(_job(w), flush=True)
    if not a.only:
        meta = {pid: {"file": pid + ".png", "two_hands": two, "caption": cap}
                for pid, (cap, two, _) in POSES.items()}
        with open(os.path.join(a.out, "poses.json"), "w") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
    if not a.no_sheet:
        have = [p for p in POSES if os.path.exists(os.path.join(a.out, p + ".png"))]
        contact_sheet(have, a.out)


if __name__ == "__main__":
    main()
