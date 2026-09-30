# camd

Root-side camera access for frame-hands:

- `fh-camd`: the frame broker. It publishes the four IR tracking cameras to a shared-memory ring that the unprivileged tracker reads.
- `fh-camprobe`: a test tool that records timestamped frames from every camera, including the Arcturus color pair, with CSV logs.

## How it gets frames

XRService owns the headset cameras. Both tools borrow its DMA-BUFs read-only with `pidfd_getfd`, the same way FrameEyeCameraFeed does. They never touch XRService's V4L2 descriptors.

Polling buffers for changes can catch a frame while the camera is still writing it. Instead, they listen to the `v4l2:v4l2_dqbuf` tracepoint, which fires when XRService takes a buffer. It gives the buffer index, the sequence number and the capture timestamp.

The tools learn which DMA-BUF holds each V4L2 index by watching which buffer changes at each dequeue.

- Right after XRService allocates its buffers, the mapping is allocation order.
- After XRService restarts streaming, the order is shuffled, and the mapping is learned index by index.
- The two upper cameras share one run of buffers. For them, only allocation order can tell the cameras apart.
- `fh-camd` also re-maps an index on the fly when its buffer holds no new frame.

## fh-camd

```
make
sudo ./fh-camd               # runs until stopped or XRService exits
```

It needs root only to set up: to borrow the buffers (`ptrace_scope=1` blocks `pidfd_getfd`) and to open the root-only tracepoints. Then it drops to the invoking user for good; XRService itself runs as that user. It reads nothing from the ring's readers.

Frames go to `/run/frame-hands/ir-ring`. The file is mode 0600 and owned by the user. It sits in a root-owned directory, so no other account can plant a file or link there. The layout is in `fhring.h`, and `tracker/ring.py` reads it.

- Only complete, bright frames are published. The cameras alternate a normal exposure with a near-black one, so each camera gets 30 of its 60 fps.
- A copy torn by the camera overwriting the buffer is dropped.
- Each copy takes about 0.1 ms, and a cache sync about 0.15 ms.

It exits when XRService exits, or when a camera's buffers keep going stale, which means XRService has reallocated them. Start it again to re-attach.

## fh-camprobe

Wear the headset (cameras only stream while it's worn), then run:

```
sudo ./fh-camprobe            # records 15 s
sudo ./fh-camprobe --list     # discovery and tracepoints only
```

Output goes to `~/Pictures/framecap/stereo-<time>/`:

- `summary.txt`: delays, buffer mapping, exposure pattern, stereo sync, and torn or stale copies.
- `frames.csv`: one row per frame. `events.csv`: every tracepoint sample.
- `<model>_NNNN_<camera>.pgm`: saved bright pairs. The color cameras are saved raw as `.yuv420_10p` (`tools/decode.py` reads them).
- `plane1_*.bin`: raw plane 1 of a few frames, which may hold sensor metadata.

Which camera is which: video9 is `slam_left`, video13 is `slam_right`, video6 is `upper_left` and video7 is `upper_right`. This was checked by rendering the same view from each camera with the factory calibration. `tracker/live.py` maps them by capture pipe (`/sys/class/video4linux/videoN/name`).

`discovery` in `xrcams.c` is adapted from FrameEyeCameraFeed (MIT, see `LICENSE.FrameEyeCameraFeed`).
