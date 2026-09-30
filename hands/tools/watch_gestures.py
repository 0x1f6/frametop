"""Watch the pinch gestures ft-hands publishes, live: begins, ends, and drags.

usage: python3 tools/watch_gestures.py [--every S] [--distance]

Prints a line when a pinch begins or ends on either hand. It goes by the counters, so a
quick tap between two reads still shows. While a pinch is held, every --every seconds
(default 0.1) it prints how far the pinch point has moved since it began, in the head
frame (turning your head moves it too; a real consumer turns both points into the room
first, see include/fh_gestures.h). --distance also prints each hand's thumb-to-index
distance, to see how close a pinch comes to the thresholds.
"""
import argparse
import mmap
import os
import struct
import time

HDR = struct.Struct('<8sIIQQQff16x')         # 64 bytes
PINCH = struct.Struct('<IIIIQQff3f3f')       # 64 bytes
TRACKED, DOWN, LOST = 1, 2, 4
SIDES = ('left ', 'right')


def path():
    return '/run/user/%d/frametop-hands/gestures' % os.getuid()


def read(m):
    """(header, [left, right]) under the sequence lock, or None if it's being written."""
    for _ in range(10):
        s1 = struct.unpack_from('<Q', m, 16)[0]
        if s1 % 2 == 0:
            h = HDR.unpack_from(m, 0)
            p = [PINCH.unpack_from(m, HDR.size + k * PINCH.size) for k in range(2)]
            if struct.unpack_from('<Q', m, 16)[0] == s1:
                return h, p
        time.sleep(0.0005)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--every', type=float, default=0.1, help='seconds between drag lines while pinching')
    ap.add_argument('--distance', action='store_true', help="print each hand's thumb-to-index distance")
    a = ap.parse_args()
    with open(path(), 'rb') as f:
        m = mmap.mmap(f.fileno(), 0, prot=mmap.PROT_READ)
    first = read(m)
    if first is None or first[0][0] != b'FHGEST01':
        raise SystemExit('%s is not an ft-hands gestures file' % path())
    h, p = first
    print('thresholds: pinch begins under %.3f m, ends over %.3f m' % (h[6], h[7]))
    seen = [(q[2], q[3]) for q in p]   # begins, ends
    last_drag = last_dist = 0.0
    while True:
        got = read(m)
        if got:
            h, p = got
            now = time.monotonic()
            for k, q in enumerate(p):
                flags, hand, begins, ends, begin_ns, end_ns, dist, strength = q[:8]
                point, begin_point = q[8:11], q[11:14]
                if begins != seen[k][0]:
                    print('%s pinch BEGIN (#%d, hand %d) at %+.3f %+.3f %+.3f  d %.3f' %
                          (SIDES[k], begins, hand, *begin_point, dist), flush=True)
                if ends != seen[k][1]:
                    held = (end_ns - begin_ns) / 1e9 if end_ns >= begin_ns else 0
                    print('%s pinch %s after %.2f s' % (SIDES[k], 'LOST' if flags & LOST else 'END', held), flush=True)
                seen[k] = (begins, ends)
                if flags & DOWN and now - last_drag >= a.every:
                    d = [point[i] - begin_point[i] for i in range(3)]
                    print('%s   drag %+6.1f %+6.1f %+6.1f mm  (%.0f mm)' %
                          (SIDES[k], *(1000 * x for x in d), 1000 * sum(x * x for x in d) ** 0.5), flush=True)
            if any(q[0] & DOWN for q in p) and now - last_drag >= a.every:
                last_drag = now
            if a.distance and now - last_dist >= 0.2:
                last_dist = now
                print('    ' + '   '.join('%s %s' % (SIDES[k].strip(), 'd %.3f s %.2f' % (q[6], q[7]) if q[0] & TRACKED
                                                        else '-') for k, q in enumerate(p)), flush=True)
        time.sleep(0.005)


if __name__ == '__main__':
    main()
