"""Per-frame pupil ellipses through a recording, cached in the capture (numbers only).

ellipses(cap) -> {camera: array of rows (t, x, y, a, b, major, fill, glint mid x, y)}, every
EVERY-th frame of each camera; the glint midpoint is NaN when the pair isn't seen.
"""
import numpy as np

import eyes_lab  # noqa: F401  (puts gaze/tracker on the path)
import eyes_pupil

EVERY = 2
VERSION = 2


def load_index(cap):
    return np.array([[float(v) for v in l.split()]
                     for l in (cap / "index.txt").read_text().splitlines() if len(l.split()) == 4])


def ellipses(cap):
    cache = cap / f"ellipses-v{VERSION}.npz"
    if cache.exists():
        z = np.load(cache)
        return {0: z["cam0"], 1: z["cam1"]}
    idx = load_index(cap)
    frames = np.memmap(cap / "frames.raw", dtype=np.uint8, mode="r").reshape(-1, 400, 512)
    idx = idx[:len(frames)]
    out = {}
    for c in (0, 1):
        rows, prev = [], None
        for i in np.where(idx[:, 2] == c)[0][::EVERY]:
            p = eyes_pupil.find_pupil(frames[i], prev)
            prev = p
            if p is None:
                continue
            pair = eyes_pupil.glint_pair(p)
            mx, my = ((pair[0][0] + pair[1][0]) / 2, (pair[0][1] + pair[1][1]) / 2) if pair else (np.nan, np.nan)
            rows.append((idx[i, 3], p["x"], p["y"], p["a"], p["b"], p["major"], p["fill"], mx, my))
        out[c] = np.array(rows).reshape(-1, 9)
    np.savez(cache, cam0=out[0], cam1=out[1])
    return out
