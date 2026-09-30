"""Check that the side cameras' images carry the right names (slam_left vs slam_right).

usage: python tools/check_sides.py REC_DIR [--sets N]
       python tools/check_sides.py --ring [--sets N]    (live, from ft-camd's ring)

With --ring it exits 0 when the names are right, 3 when they're swapped (run ft-hands
with --swap-sides), and 2 when it can't tell (too little texture in view, or the headset
isn't worn).

ft-camd tells the two side cameras' buffers apart by the order XRService allocated them,
and after some XRService restarts that order puts each camera's images under the other's
name. The tracker then sees every hand in one camera only, at the wrong depth. This
matches features between the two images and measures how close each pair's rays pass
with the factory calibration, once as named and once swapped: true matches meet in
front of both cameras only under the right naming.
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from tools.show_set import index, read_set  # noqa: E402
from tools import calib  # noqa: E402


PIPES = {'msm_vfe3_video0': 'slam_left', 'msm_vfe4_video0': 'slam_right'}   # as ft-hands maps them


def load_cams():
    return calib.load()


def matches(a, b):
    """Pixel pairs (N,2), (N,2) of ORB matches between two grey images."""
    clahe = cv2.createCLAHE(2.0, (8, 8))
    orb = cv2.ORB_create(3000)
    ka, da = orb.detectAndCompute(clahe.apply(a), None)
    kb, db = orb.detectAndCompute(clahe.apply(b), None)
    if da is None or db is None:
        return np.zeros((0, 2)), np.zeros((0, 2))
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    return (np.array([ka[m.queryIdx].pt for m in good]).reshape(-1, 2),
            np.array([kb[m.trainIdx].pt for m in good]).reshape(-1, 2))


def meet(cam_a, cam_b, ua, ub):
    """Per match: closest distance between the two rays (m), and whether they meet in front of both."""
    ra, rb = cam_a.rays(ua), cam_b.rays(ub)
    w = cam_b.origin - cam_a.origin
    n = np.cross(ra, rb)
    nn = np.linalg.norm(n, axis=1)
    dist = np.abs(w @ n.T) / np.maximum(nn, 1e-12)
    # ray parameters at the closest points
    ta = np.einsum('ij,ij->i', np.cross(np.broadcast_to(w, rb.shape), rb), n) / np.maximum(nn ** 2, 1e-12)
    tb = np.einsum('ij,ij->i', np.cross(np.broadcast_to(w, ra.shape), ra), n) / np.maximum(nn ** 2, 1e-12)
    return dist, (ta > 0.05) & (tb > 0.05)


def score(cam_a, cam_b, ua, ub):
    """Share of matches whose rays meet within 1 cm, in front of both cameras."""
    if len(ua) == 0:
        return 0.0
    d, front = meet(cam_a, cam_b, ua, ub)
    return float(np.mean((d < 0.01) & front))


def recorded_pairs(rec, count):
    """(label, slam_left image, slam_right image) from sets spread across a recording."""
    path = os.path.join(rec, 'sets.bin')
    offs = index(path)
    for n in np.linspace(0, len(offs) - 1, count).astype(int):
        images = read_set(path, offs[n])
        if 'slam_left' in images and 'slam_right' in images:
            yield 'set %5d' % n, images['slam_left'][0], images['slam_right'][0]


def live_pairs(count):
    """(label, slam_left image, slam_right image) from ft-camd's ring, half a second apart."""
    import time
    from tools.ring import Ring
    ring = Ring()
    if not ring.alive():
        sys.exit('ft-camd isn\'t running (no heartbeat)')
    cams = {}
    for c in ring.cams:
        name = PIPES.get(open('/sys/class/video4linux/video%d/name' % c.node).read().strip())
        if name and not c.name.endswith('-dark'):
            cams[name] = c
    for k in range(count):
        a, b = ring.read(cams['slam_left']), ring.read(cams['slam_right'])
        if a is not None and b is not None:
            yield 'frame %2d' % k, a.image, b.image
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rec', nargs='?')
    ap.add_argument('--ring', action='store_true', help='check the live cameras instead of a recording')
    ap.add_argument('--sets', type=int, default=8, help='how many sets or live frames to check')
    a = ap.parse_args()
    if not a.ring and not a.rec:
        ap.error('give a recording or --ring')
    cams = load_cams()
    left, right = cams['slam_left'], cams['slam_right']
    named = swapped = 0.0
    n = total_matches = 0
    for label, img_l, img_r in (live_pairs(a.sets) if a.ring else recorded_pairs(a.rec, a.sets)):
        ua, ub = matches(img_l, img_r)
        s_named = score(left, right, ua, ub)     # slam_left's image seen by the left camera
        s_swapped = score(right, left, ua, ub)   # ... by the right camera
        named, swapped, n, total_matches = named + s_named, swapped + s_swapped, n + 1, total_matches + len(ua)
        print('%s: %4d matches, meeting as named %3.0f%%, swapped %3.0f%%' %
              (label, len(ua), 100 * s_named, 100 * s_swapped))
    if n == 0 or total_matches < 100 or abs(named - swapped) / n < 0.2:
        print('side cameras: can\'t tell (%d matches)' % total_matches)
        sys.exit(2)
    print('side cameras: %s (named %.2f, swapped %.2f)' %
          ('as named' if named > swapped else 'SWAPPED', named / n, swapped / n))
    sys.exit(0 if named > swapped else 3)


if __name__ == '__main__':
    main()
