"""Calibration crops for quantizing the models to int8 (ncnn2table).

Takes the frames of an fh-camprobe capture, cuts the crops the tracker would feed
the models (CLAHE-equalized, as tracker/models.py prepares them), and writes them
as PNGs plus a list per model:
  palm: every search tile of every frame (a sample of them)
  hand: the hands found in them, each also shifted, scaled and turned a little

usage: python tools/make_int8_calib.py CAPTURE_DIR OUT_DIR
"""
import glob
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tracker'))
import calib    # noqa: E402
import hands    # noqa: E402
import models   # noqa: E402

NODES = {'video9': 'slam_left', 'video13': 'slam_right', 'video6': 'upper_left', 'video7': 'upper_right'}


def save(path, patch):
    cv2.imwrite(path, cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR))


def main():
    cap, out = sys.argv[1], sys.argv[2]
    random.seed(1)
    cams = calib.load()
    eng = models.Engine()
    palm, hm = models.PalmDetector(), models.HandLandmarker()
    tracker = hands.Tracker(cams, eng)
    for d in ('palm', 'hand'):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    palm_files, hand_files = [], []
    for node, name in NODES.items():
        tiles = [t for t in tracker.tiles if t.cam.name == name]
        for f in sorted(glob.glob(os.path.join(cap, '*_%s.pgm' % node))):
            g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            base = os.path.basename(f)[:-4]
            prep = [palm.prepare(g, t.center, t.size, t.rotation) for t in tiles]
            outs = eng.run([('palm', p) for p, _ in prep])
            rois = []
            for k, ((p, ctx), o) in enumerate(zip(prep, outs)):
                if random.random() < 0.3:
                    path = os.path.join(out, 'palm', '%s_t%02d.png' % (base, k))
                    save(path, p)
                    palm_files.append(path)
                for d in palm.decode(o, ctx):
                    r = d.roi()
                    if all(np.linalg.norm(r[0] - q[0]) > 0.5 * r[1] for q in rois):
                        rois.append(r)
            for j, r in enumerate(rois):
                p, ctx = hm.prepare(g, r)
                if hm.decode(eng.run([('hand', p)])[0], ctx).presence < 0.5:
                    continue
                for v in range(5):
                    c, s, rot = r
                    if v:
                        c = np.asarray(c) + np.random.uniform(-0.08, 0.08, 2) * s
                        s = s * np.random.uniform(0.87, 1.15)
                        rot = rot + np.radians(np.random.uniform(-20, 20))
                    p, _ = hm.prepare(g, (c, s, rot))
                    path = os.path.join(out, 'hand', '%s_h%d_%d.png' % (base, j, v))
                    save(path, p)
                    hand_files.append(path)
    for name, files in (('palm', palm_files), ('hand', hand_files)):
        with open(os.path.join(out, name + '.txt'), 'w') as fh:
            fh.write('\n'.join(files) + '\n')
        print(name, len(files), 'crops')


if __name__ == '__main__':
    main()
