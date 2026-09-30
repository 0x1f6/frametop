"""How good the tracker's depth is, from a replay's depth dump, without ground truth.

usage: python3 tools/depth_report.py DEPTH [DEPTH...] [--still M/S]

DEPTH comes from `hands/build/ft-handreplay DIR --depth DEPTH`. Every measure is split by how the
hand was seen: by the two lower cameras ("lower pair"), by a lower and an upper camera on
one side ("lower+upper"), or by one camera. Distances are from the head (between the eyes).

1. How the hands were seen: the share of hand updates in each way, by distance.
2. Noise along the line of sight against across it. Each update's palm is compared with a
   straight line through the two updates before it (ft-handreplay's jitter measure), and the
   miss is split along the line from the hand's cameras to the palm and across it. Given
   as a robust sigma per axis, measured (as triangulated) and published (after the One Euro
   filter), on updates where the published palm moved slower than --still (default 0.15
   m/s), so the miss is mostly noise and not the hand speeding up. For two cameras,
   geometry predicts along/across = 2 Z / B: Z the distance, B the cameras' baseline
   across the line of sight.
3. One-camera distance: on two-camera updates, each camera's one-view guess (distance from
   how big the palm looks, at the user's learned hand size) against the triangulated
   distance from that camera.
4. A camera lost: from two-camera updates, what the tracker would have had if one of the
   two cameras dropped out there. It keeps the last distance and moves a share of the way
   to the one-view guess each update (0.1 now, kMonoDepthGain in track/tracker.cpp);
   also shown with other shares, 0 (keep the distance) and 1 (take each guess), and with
   the guess first scaled by how far off it was while both cameras saw the hand. Compared with the
   triangulated distance from that camera, 0.1-2 s after the loss.
"""
import argparse
import collections

import numpy as np

BINS = [0.0, 0.35, 0.50, 0.65, 9.0]
BIN_NAMES = ['<35 cm', '35-50', '50-65', '65+ cm']
MODES = ['lower pair', 'lower+upper', 'one camera']
HORIZONS = [0.1, 0.25, 0.5, 1.0, 2.0]
# (share of the way toward the one-view guess per update, whether the guess is first scaled by
# how far off it was while both cameras saw the hand)
GAINS = [(0.0, False), (0.02, False), (0.05, False), (0.1, False), (1.0, False), (0.1, True), (1.0, True)]
GAP = 0.1   # s: a longer gap between a hand's updates breaks its run


def load(path):
    cams, rows = {}, []
    with open(path) as f:
        for line in f:
            w = line.split()
            if not w:
                continue
            if w[0] == '#':
                if w[1] == 'cam':
                    cams[w[2]] = (np.array([float(x) for x in w[3:6]]), float(w[6]))
                continue
            r = {'t': float(w[0]), 'id': int(w[1]), 'side': w[2], 'n': int(w[3]),
                 'cams': w[4], 'res': float(w[5]), 'scale': float(w[6]),
                 'raw': np.array([float(x) for x in w[7:10]]), 'sm': np.array([float(x) for x in w[10:13]]),
                 'views': {}}
            for k in range(13, len(w), 5):
                r['views'][w[k]] = np.array([float(x) for x in w[k + 2:k + 5]])
            rows.append(r)
    return cams, rows


def mode(r):
    names = r['cams'].split('+')
    if r['n'] == 1:
        return 'one camera'
    if sorted(names) == ['slam_left', 'slam_right']:
        return 'lower pair'
    if len(names) == 2 and all(n.startswith(('slam_', 'upper_')) for n in names) and \
            names[0].split('_')[1] == names[1].split('_')[1]:
        return 'lower+upper'
    return 'other'


def dist_bin(p):
    return min(np.searchsorted(BINS, np.linalg.norm(p), side='right') - 1, len(BIN_NAMES) - 1)


def tracks(rows):
    """A hand's updates, in order, split where they're more than GAP apart."""
    by_id = collections.defaultdict(list)
    for r in rows:
        by_id[r['id']].append(r)
    for rs in by_id.values():
        run = [rs[0]]
        for r in rs[1:]:
            if r['t'] - run[-1]['t'] >= GAP:
                yield run
                run = []
            run.append(r)
        yield run


def two_camera_runs(rows):
    """Stretches of a hand's updates all seen by the same two cameras."""
    for run in tracks(rows):
        seg = []
        for r in run:
            ok = r['n'] == 2 and mode(r) != 'other'
            if ok and seg and r['cams'] == seg[-1]['cams']:
                seg.append(r)
                continue
            if len(seg) > 2:
                yield seg
            seg = [r] if ok else []
        if len(seg) > 2:
            yield seg


def pct(v, q):
    return np.percentile(v, q) if len(v) else float('nan')


def seen_share(rows):
    print('\n1. How the hands were seen (share of hand updates)')
    count = collections.Counter((mode(r), dist_bin(r['raw'])) for r in rows)
    total = collections.Counter(dist_bin(r['raw']) for r in rows)
    print('%-12s' % '' + ''.join('%10s' % b for b in BIN_NAMES) + '%10s' % 'all')
    for m in MODES + ['other']:
        cells = [100 * count[m, b] / max(total[b], 1) for b in range(len(BIN_NAMES))]
        allp = 100 * sum(count[m, b] for b in range(len(BIN_NAMES))) / max(len(rows), 1)
        print('%-12s' % m + ''.join('%9.0f%%' % c for c in cells) + '%9.0f%%' % allp)
    print('%-12s' % 'updates' + ''.join('%10d' % total[b] for b in range(len(BIN_NAMES))) + '%10d' % len(rows))
    res = collections.defaultdict(list)
    for r in rows:
        if r['res'] >= 0:
            res[mode(r)].append(r['res'] * 1000)
    print('triangulation residual (rms ray miss, median): ' +
          ', '.join('%s %.1f mm' % (m, np.median(v)) for m, v in res.items()))


def noise(rows, cams, still):
    print('\n2. Noise along the line of sight vs across it (sigma per axis, mm; palm slower than %.2f m/s)' % still)
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for run in tracks(rows):
        for a, b, c in zip(run, run[1:], run[2:]):
            if not (mode(a) == mode(b) == mode(c)) or a['cams'] != b['cams'] or b['cams'] != c['cams']:
                continue
            dt0, dt1 = b['t'] - a['t'], c['t'] - b['t']
            if dt0 < 1e-3 or np.linalg.norm(b['sm'] - a['sm']) / dt0 > still:
                continue
            names = b['cams'].split('+')
            origins = [cams[n][0] for n in names]
            o = np.mean(origins, axis=0)
            u = b['raw'] - o
            z = np.linalg.norm(u)
            u /= z
            key = (mode(b), dist_bin(b['raw']))
            for kind in ('raw', 'sm'):
                miss = c[kind] - b[kind] - (b[kind] - a[kind]) * (dt1 / dt0)
                along = miss @ u
                acc[key][kind + '_along'].append(abs(along))
                acc[key][kind + '_across'].append(np.linalg.norm(miss - along * u))
            if len(origins) == 2:
                base = origins[0] - origins[1]
                acc[key]['pred'].append(2 * z / np.linalg.norm(base - (base @ u) * u))
    # |along| is half-normal: sigma = median / 0.674; |across| is Rayleigh (2 axes): sigma = median / 1.177
    print('%-12s %-7s %6s | %-24s | %-24s | %s' % ('', '', 'n', 'measured along/across', 'published along/across',
                                                  'ratio measured (geometry)'))
    for m in MODES:
        for bi, bn in enumerate(BIN_NAMES):
            d = acc.get((m, bi))
            if not d or len(d['raw_along']) < 20:
                continue
            s = {k: np.median(v) / (0.674 if k.endswith('along') else 1.177) * 1000
                 for k, v in d.items() if k != 'pred'}
            pred = '(%.1f)' % np.median(d['pred']) if d['pred'] else ''
            print('%-12s %-7s %6d | %7.1f / %-5.1f  x%-6.1f | %7.1f / %-5.1f  x%-6.1f | x%.1f %s' % (
                m, bn, len(d['raw_along']), s['raw_along'], s['raw_across'], s['raw_along'] / s['raw_across'],
                s['sm_along'], s['sm_across'], s['sm_along'] / s['sm_across'],
                s['raw_along'] / s['raw_across'], pred))


def one_camera(rows, cams):
    print('\n3. One-camera distance vs triangulated, on two-camera updates (error of the one-view guess)')
    acc = collections.defaultdict(list)
    for r in rows:
        if r['n'] != 2 or mode(r) == 'other':
            continue
        for name, p in r['views'].items():
            if np.isnan(p).any():
                continue
            o = cams[name][0]
            truth = np.linalg.norm(r['raw'] - o)
            acc[name.split('_')[0], dist_bin(r['raw'])].append((np.linalg.norm(p - o) - truth, truth))
    print('%-8s %-7s %6s %12s %12s %14s %12s' % ('camera', '', 'n', 'median |err|', '90% |err|', 'median |err| %',
                                                  'bias'))
    for cam in ('slam', 'upper'):
        for bi, bn in enumerate(BIN_NAMES):
            v = acc.get((cam, bi))
            if not v or len(v) < 20:
                continue
            e = np.array([x[0] for x in v])
            rel = e / np.array([x[1] for x in v])
            print('%-8s %-7s %6d %9.0f mm %9.0f mm %13.0f%% %+11.0f%%' % (
                'lower' if cam == 'slam' else 'upper', bn, len(v), 1000 * np.median(abs(e)), 1000 * pct(abs(e), 90),
                100 * np.median(abs(rel)), 100 * np.median(rel)))


def lost_camera(rows, cams):
    print('\n4. A camera lost: distance error after the loss (median |err| mm / 90% mm), by how the tracker '
          'moves toward the one-view guess each update ("scaled": the guess times how far off it was, '
          'triangulated / guess, median over the last 30 two-camera updates)')
    errs = collections.defaultdict(list)
    for run in two_camera_runs(rows):
        for s in range(1, len(run) - 1, 3):
            for name in run[s]['views']:
                o = cams[name][0]
                guess = lambda r: np.linalg.norm(r['views'][name] - o)
                ratios = [np.linalg.norm(r['raw'] - o) / guess(r) for r in run[max(0, s - 30):s]
                          if not np.isnan(r['views'][name]).any()]
                ratio = np.median(ratios) if ratios else 1.0
                for g, scaled in GAINS:
                    d = np.linalg.norm(run[s - 1]['raw'] - o)
                    h = 0
                    for r in run[s:]:
                        if np.isnan(r['views'][name]).any():
                            break
                        d += g * (guess(r) * (ratio if scaled else 1.0) - d)
                        elapsed = r['t'] - run[s - 1]['t']
                        while h < len(HORIZONS) and elapsed >= HORIZONS[h]:
                            errs[g, scaled, HORIZONS[h], name.split('_')[0]].append(abs(d - np.linalg.norm(r['raw'] - o)))
                            h += 1
    print('%-8s %-18s' % ('camera', 'toward guess') + ''.join('%14s' % ('%.2g s' % t) for t in HORIZONS))
    for cam in ('slam', 'upper'):
        for g, scaled in GAINS:
            label = {0.0: '0 (keep)', 0.1: '0.1 (now)', 1.0: '1 (guess)'}.get(g, '%g' % g)
            if scaled:
                label = '%g scaled' % g
            cells = []
            for t in HORIZONS:
                v = errs.get((g, scaled, t, cam), [])
                cells.append('%5.0f / %-4.0f' % (1000 * np.median(v), 1000 * pct(v, 90)) if len(v) >= 20 else '%14s' % '-')
            print('%-8s %-18s' % ('lower' if cam == 'slam' else 'upper', label) + ''.join('%14s' % c for c in cells))
    n = sum(len(errs.get((0.1, False, HORIZONS[0], c), [])) for c in ('slam', 'upper'))
    print('(%d simulated losses)' % n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('depth', nargs='+')
    ap.add_argument('--still', type=float, default=0.15, help='m/s: palm speed limit for the noise measure')
    a = ap.parse_args()
    for path in a.depth:
        cams, rows = load(path)
        print('== %s: %d hand updates, %d hands' % (path, len(rows), len({r['id'] for r in rows})))
        seen_share(rows)
        noise(rows, cams, a.still)
        one_camera(rows, cams)
        lost_camera(rows, cams)
        print()


if __name__ == '__main__':
    main()
