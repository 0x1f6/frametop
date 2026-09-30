"""Which color camera is which, and how their calibration maps onto ft-camd's images.

usage: python tools/check_color.py REC_DIR [--sets N]

A recording made with ft-camd --with-color holds color_video<N> frames with each set.
This matches features between the two color images and scores every reading of the
calibration: which video node is passthrough_left, and whether the calibration's
cropRegion is subtracted from x ('subtract') or not ('none'). Only the right reading
makes true matches' rays meet in front of both cameras. Then it checks the winner against
the side tracking cameras, which tests the CAD-to-head chain shared with them.
"""
import argparse
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from tools.check_sides import load_cams, matches, score  # noqa: E402
from tools.show_set import index, read_set  # noqa: E402
from tools import calib  # noqa: E402


def load_color(crop):
    return calib.load_color(crop=crop)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rec')
    ap.add_argument('--sets', type=int, default=8)
    a = ap.parse_args()
    path = os.path.join(a.rec, 'sets.bin')
    offs = index(path)
    sets = [read_set(path, offs[n]) for n in np.linspace(0, len(offs) - 1, a.sets).astype(int)]
    nodes = sorted(k for k in sets[0] if k.startswith('color_video'))
    if len(nodes) != 2:
        sys.exit('need two color_video<N> cameras in the recording (ft-camd --with-color); found %s' % nodes)
    pairs = [matches(s[nodes[0]][0], s[nodes[1]][0]) for s in sets]
    print('%d sets, %d matches between %s and %s' % (len(sets), sum(len(p[0]) for p in pairs), *nodes))

    best = None
    for crop, left in itertools.product(['subtract', 'none'], nodes):
        cams = load_color(crop)
        right = nodes[1] if left == nodes[0] else nodes[0]
        cam = {left: cams['passthrough_left'], right: cams['passthrough_right']}
        s = np.mean([score(cam[nodes[0]], cam[nodes[1]], ua, ub) for ua, ub in pairs])
        print('  %s = passthrough_left, crop %-8s: %3.0f%% of matches meet' % (left, crop, 100 * s))
        if best is None or s > best[0]:
            best = (s, crop, left, cam)
    s, crop, left, cam = best
    print('best: %s = passthrough_left, crop %s (%.0f%%)' % (left, crop, 100 * s))

    mono = load_cams()
    for node in nodes:
        for side in ['slam_left', 'slam_right']:
            ms = [matches(st[node][0], st[side][0]) for st in sets if side in st]
            sc = np.mean([score(cam[node], mono[side], ua, ub) for ua, ub in ms]) if ms else 0
            print('  %s vs %-10s: %4d matches, %3.0f%% meet' % (node, side, sum(len(m[0]) for m in ms), 100 * sc))


if __name__ == '__main__':
    main()
