"""Compare trackd's C++ model code with tracker/models.py on recorded frames.

For each frame, finds a crop with a palm (Python side), runs trackd/nettest on the same
crop, and reports how far apart the palms, ROIs and landmarks are.

usage: python tools/nettest_compare.py CAPTURE_DIR [--int8]
"""
import glob
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tracker'))
import calib    # noqa: E402
import hands    # noqa: E402
import models   # noqa: E402

NODES = {'video9': 'slam_left', 'video13': 'slam_right', 'video6': 'upper_left', 'video7': 'upper_right'}
NETTEST = os.path.join(HERE, '..', 'trackd', 'nettest')
MODELS = os.path.join(HERE, '..', 'models', 'ncnn')


def cpp(frame, tile, int8):
    out = subprocess.run([NETTEST, MODELS, frame, str(tile.center[0]), str(tile.center[1]), str(tile.size),
                          str(tile.rotation)] + (['--int8'] if int8 else []), capture_output=True, text=True).stdout
    palms, hands_, ms = [], [], {}
    for line in out.splitlines():
        f = line.split()
        if f[0] == 'palm':
            palms.append((float(f[1]), np.array([float(f[6]), float(f[7])]), float(f[8]), float(f[9])))
        elif f[0] == 'pts':
            hands_.append(np.array(f[1:], float).reshape(21, 2))
        elif f[0] == 'hand_ms':
            ms.setdefault('hand', []).append(float(f[1]))
            hands_presence = float(f[3])
            ms.setdefault('presence', []).append(hands_presence)
        elif f[0] == 'palm_ms':
            ms.setdefault('palm', []).append(float(f[1]))
    return palms, hands_, ms


def main():
    cap = sys.argv[1]
    int8 = '--int8' in sys.argv
    cams = calib.load()
    eng = models.Engine()
    palm, hm = models.PalmDetector(), models.HandLandmarker()
    tracker = hands.Tracker(cams, eng)
    d_roi, d_pts, n_py, n_cpp, pres = [], [], 0, 0, []
    times = {'palm': [], 'hand': []}
    for node, name in NODES.items():
        tiles = [t for t in tracker.tiles if t.cam.name == name]
        for f in sorted(glob.glob(os.path.join(cap, '*_%s.pgm' % node)))[::4]:
            g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            for t in tiles:
                p, ctx = palm.prepare(g, t.center, t.size, t.rotation)
                dets = palm.decode(eng.run([('palm', p)])[0], ctx)
                if not dets:
                    continue
                cp, ch, ms = cpp(f, t, int8)
                for k in times:
                    times[k] += ms.get(k, [])
                n_py += len(dets)
                n_cpp += len(cp)
                for d in dets:
                    r = d.roi()
                    if not cp:
                        continue
                    j = int(np.argmin([np.linalg.norm(c[1] - r[0]) for c in cp]))
                    d_roi.append(np.linalg.norm(cp[j][1] - r[0]) / r[1])
                    pp, pctx = hm.prepare(g, r)
                    lm = hm.decode(eng.run([('hand', pp)])[0], pctx)
                    if lm.presence >= 0.5 and j < len(ch):
                        d_pts.append(np.median(np.linalg.norm(ch[j] - lm.pts, axis=1)) / r[1])
                        pres.append(abs(ms['presence'][j] - lm.presence))
                break   # one palm tile per frame is enough
    print('palms: python %d, c++ %d' % (n_py, n_cpp))
    print('roi centre offset: median %.3f of the roi size (max %.3f)' % (np.median(d_roi), np.max(d_roi)))
    if d_pts:
        print('landmarks: median %.4f of the roi size (max %.4f), presence diff median %.3f' % (
            np.median(d_pts), np.max(d_pts), np.median(pres)))
    print('c++ time: palm %.1f ms, hand %.1f ms (median, one thread)' % (np.median(times['palm']), np.median(times['hand'])))


if __name__ == '__main__':
    main()
