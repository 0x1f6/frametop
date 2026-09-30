"""MediaPipe's palm detector and hand landmark model on ncnn, CPU or Vulkan GPU.

The models are the OpenCV Zoo ONNX ports, converted by tools/convert_models.py.
Crops are square regions of a camera image given as (centre, size, rotation)
in pixels and radians; rotation turns the crop's "up" towards the image
direction (sin r, -cos r), as in MediaPipe. Both models take RGB in [0, 1];
mono crops are replicated to three planes.

Preparing crops and decoding outputs happen here; running the networks is an
engine's job: Engine runs them in this process, Pool in worker processes
(ncnn's Python binding holds the GIL while it infers, so threads don't help).
"""
import multiprocessing as mp
import os
from multiprocessing import connection, shared_memory

import cv2
import ncnn
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, '..', 'models', 'ncnn')

# MediaPipe hand_landmarks_to_rect: the palm and finger bases used for the next ROI
ROI_LANDMARKS = [0, 1, 2, 3, 5, 6, 9, 10, 13, 14, 17, 18]

# per model: input size, output blobs and their sizes
SPECS = {'palm': (192, [('out0', 2016 * 18), ('out1', 2016)]),
         'hand': (224, [('out0', 63), ('out1', 1), ('out2', 1), ('out3', 63)])}


def load_net(name, gpu, threads=1):
    net = ncnn.Net()
    net.opt.use_vulkan_compute = gpu
    net.opt.num_threads = threads
    net.opt.use_fp16_packed = net.opt.use_fp16_storage = net.opt.use_fp16_arithmetic = True
    net.load_param(os.path.join(MODELS, name + '.ncnn.param'))
    net.load_model(os.path.join(MODELS, name + '.ncnn.bin'))
    return net


def infer(net, kind, patch):
    """Run one network on an 8-bit mono crop; returns its outputs, flattened."""
    plane = patch.astype(np.float32) * (1.0 / 255.0)
    x = np.ascontiguousarray(np.broadcast_to(plane, (3,) + plane.shape))
    ex = net.create_extractor()
    ex.input('in0', ncnn.Mat(x))
    return [np.array(ex.extract(name)[1], np.float32).reshape(-1) for name, _ in SPECS[kind][1]]


class Engine:
    """Runs the networks in this process, one after another."""

    def __init__(self, palm_gpu=False, hand_gpu=False):
        self.nets = {'palm': load_net('palm', palm_gpu), 'hand': load_net('hand', hand_gpu)}

    def run(self, jobs):
        """jobs: [(kind, patch)] -> [outputs]"""
        return [infer(self.nets[kind], kind, patch) for kind, patch in jobs]

    def close(self):
        pass


def _worker(index, shm_name, conn, palm_gpu, hand_gpu, cpu):
    if cpu is not None and cpu in os.sched_getaffinity(0):
        os.sched_setaffinity(0, {cpu})
    nets = {'palm': load_net('palm', palm_gpu), 'hand': load_net('hand', hand_gpu)}
    shm = shared_memory.SharedMemory(name=shm_name)
    base = index * Pool.STRIDE
    while True:
        msg = conn.recv()
        if msg is None:
            break
        kind = msg
        size = SPECS[kind][0]
        patch = np.ndarray((size, size), np.uint8, shm.buf, base)
        outs = infer(nets[kind], kind, patch)
        off = base + Pool.IN_BYTES
        for o in outs:
            np.ndarray(o.shape, np.float32, shm.buf, off)[:] = o
            off += o.nbytes
        conn.send(True)
    shm.close()


class Pool:
    """Runs the networks in worker processes pinned to CPUs, several crops at once."""
    IN_BYTES = 224 * 224
    OUT_BYTES = 4 * (2016 * 18 + 2016)
    STRIDE = IN_BYTES + OUT_BYTES

    # SteamOS on the Frame keeps user processes on CPUs 0-4 (5-7 carry pinned VR threads);
    # 2-4 are the big cores among those
    def __init__(self, workers=2, cpus=(2, 3, 4), palm_gpu=False, hand_gpu=False):
        ctx = mp.get_context('spawn')
        self.shm = shared_memory.SharedMemory(create=True, size=workers * self.STRIDE)
        self.conns, self.procs = [], []
        for i in range(workers):
            a, b = ctx.Pipe()
            cpu = cpus[i % len(cpus)] if cpus else None
            p = ctx.Process(target=_worker, args=(i, self.shm.name, b, palm_gpu, hand_gpu, cpu), daemon=True)
            p.start()
            self.conns.append(a)
            self.procs.append(p)

    def run(self, jobs):
        results = [None] * len(jobs)
        pending = list(range(len(jobs)))
        busy = {}                                          # conn -> job index
        free = list(range(len(self.conns)))
        while pending or busy:
            while pending and free:
                w = free.pop()
                j = pending.pop(0)
                kind, patch = jobs[j]
                base = w * self.STRIDE
                np.ndarray(patch.shape, np.uint8, self.shm.buf, base)[:] = patch
                self.conns[w].send(kind)
                busy[self.conns[w]] = (w, j)
            for c in connection.wait(list(busy)):
                c.recv()
                w, j = busy.pop(c)
                kind = jobs[j][0]
                off = w * self.STRIDE + self.IN_BYTES
                outs = []
                for _, n in SPECS[kind][1]:
                    outs.append(np.ndarray((n,), np.float32, self.shm.buf, off).copy())
                    off += 4 * n
                results[j] = outs
                free.append(w)
        return results

    def pids(self):
        return [p.pid for p in self.procs]

    def close(self):
        for c in self.conns:
            try:
                c.send(None)
            except OSError:
                pass
        for p in self.procs:
            p.join(1)
        self.shm.close()
        self.shm.unlink()


def crop_matrix(center, size, rotation, out):
    """2x3 affine taking crop pixels (0..out) to image pixels."""
    c, s = np.cos(rotation), np.sin(rotation)
    k = size / out
    R = np.array([[c, -s], [s, c]]) * k
    t = np.asarray(center, float) - R @ np.array([out / 2.0, out / 2.0])
    return np.hstack([R, t[:, None]])


# Local contrast per crop: the IR frames are dim and uneven (the upper cameras
# especially). On the 2026-09-28 capture this found hands in more frames on every
# camera than plain, stretched or gamma-corrected crops.
CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))


def crop(gray, M, out):
    """8-bit square crop of a mono image, with a crop->image matrix, contrast-equalized."""
    patch = cv2.warpAffine(gray, M, (out, out), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return CLAHE.apply(patch)


def to_image(M, pts):
    """Crop pixels (N,2) -> image pixels."""
    return pts @ M[:, :2].T + M[:, 2]


def normalize_angle(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


def _anchors():
    """SSD anchors of palm_detection_full: strides 8 (2 per cell) and 16 (6 per cell), 192x192."""
    out = []
    for stride, per_cell in ((8, 2), (16, 6)):
        n = 192 // stride
        for y in range(n):
            for x in range(n):
                out += [((x + 0.5) / n, (y + 0.5) / n)] * per_cell
    return np.array(out, np.float32)


class Detection:
    """A palm in image pixels: box centre/size, 7 keypoints, score."""
    __slots__ = ('center', 'size', 'keypoints', 'score')

    def __init__(self, center, size, keypoints, score):
        self.center, self.size, self.keypoints, self.score = center, size, keypoints, score

    def roi(self):
        """MediaPipe's hand ROI from a palm: wrist->middle-finger-base sets the rotation, then
        shift 0.5 towards the fingers and scale the square box 2.6x."""
        (x0, y0), (x1, y1) = self.keypoints[0], self.keypoints[2]
        rot = normalize_angle(np.pi / 2 - np.arctan2(-(y1 - y0), x1 - x0))
        w, h = (float(v) for v in self.size)
        shift = np.array([-h * -0.5 * np.sin(rot), h * -0.5 * np.cos(rot)])
        return np.asarray(self.center) + shift, max(w, h) * 2.6, rot


class PalmDetector:
    SIZE = 192

    def __init__(self, min_score=0.5):
        self.anchors = _anchors()
        self.min_score = min_score

    def prepare(self, gray, center, size, rotation=0.0):
        M = crop_matrix(center, size, rotation, self.SIZE)
        return crop(gray, M, self.SIZE), (M, size)

    def decode(self, outputs, ctx):
        """Palms found in one crop, in image pixels."""
        M, size = ctx
        raw = outputs[0].reshape(-1, 18)
        logit = outputs[1]
        keep = np.nonzero(logit > np.log(self.min_score / (1 - self.min_score)))[0]
        if not len(keep):
            return []
        a = self.anchors[keep] * self.SIZE
        r = raw[keep]
        centers = r[:, 0:2] + a
        sizes = r[:, 2:4]
        kps = r[:, 4:18].reshape(-1, 7, 2) + a[:, None, :]
        scores = 1 / (1 + np.exp(-np.clip(logit[keep], -100, 100)))
        return [Detection(to_image(M, c[None])[0], s * size / self.SIZE, to_image(M, k), sc)
                for c, s, k, sc in _weighted_nms(centers, sizes, scores, kps)]


def _weighted_nms(centers, sizes, scores, kps, iou_thresh=0.3):
    """MediaPipe's weighted NMS: overlapping boxes are averaged, weighted by score."""
    order = np.argsort(-scores)
    boxes = np.hstack([centers - sizes / 2, centers + sizes / 2])
    area = np.prod(sizes, axis=1)
    out = []
    while len(order):
        i = order[0]
        xy0 = np.maximum(boxes[i, :2], boxes[order, :2])
        xy1 = np.minimum(boxes[i, 2:], boxes[order, 2:])
        inter = np.prod(np.clip(xy1 - xy0, 0, None), axis=1)
        iou = inter / (area[i] + area[order] - inter + 1e-9)
        group = order[iou > iou_thresh]
        w = scores[group][:, None]
        out.append(((centers[group] * w).sum(0) / w.sum(), (sizes[group] * w).sum(0) / w.sum(),
                    (kps[group] * w[:, :, None]).sum(0) / w.sum(), float(scores[i])))
        order = order[iou <= iou_thresh]
    return out


class Landmarks:
    """21 hand landmarks in image pixels, plus MediaPipe's metric 'world' landmarks."""
    __slots__ = ('pts', 'depth', 'world', 'presence', 'right', 'roi')

    def __init__(self, pts, depth, world, presence, right, roi):
        self.pts, self.depth, self.world, self.presence = pts, depth, world, presence
        self.right, self.roi = right, roi

    def next_roi(self):
        return roi_from_points(self.pts)


def roi_from_points(p):
    """MediaPipe's hand_landmarks_to_rect: the crop to track a hand given its landmarks."""
    x0, y0 = p[0]
    x1, y1 = ((p[5] + p[13]) / 2 + p[9]) / 2
    rot = normalize_angle(np.pi / 2 - np.arctan2(-(y1 - y0), x1 - x0))
    sub = p[ROI_LANDMARKS]
    center = (sub.min(0) + sub.max(0)) / 2
    c, s = np.cos(-rot), np.sin(-rot)
    q = (sub - center) @ np.array([[c, -s], [s, c]]).T
    lo, hi = q.min(0), q.max(0)
    mid = (lo + hi) / 2
    c2, s2 = np.cos(rot), np.sin(rot)
    center = center + np.array([mid[0] * c2 - mid[1] * s2, mid[0] * s2 + mid[1] * c2])
    w, h = hi - lo
    center = center + np.array([-h * -0.1 * s2, h * -0.1 * c2])
    return center, max(w, h) * 2.0, rot


class HandLandmarker:
    SIZE = 224

    def prepare(self, gray, roi):
        center, size, rotation = roi
        M = crop_matrix(center, size, rotation, self.SIZE)
        return crop(gray, M, self.SIZE), (M, roi)

    def decode(self, outputs, ctx):
        M, roi = ctx
        screen = outputs[0].reshape(21, 3)
        pts = to_image(M, screen[:, :2])
        depth = screen[:, 2] * roi[1] / self.SIZE                # relative depth, image pixels
        return Landmarks(pts, depth, outputs[3].reshape(21, 3), float(outputs[1][0]), float(outputs[2][0]), roi)
