# camd

Root-side camera access for frame-hands:

- `fh-camd`: the frame broker. It publishes the four IR tracking cameras, and optionally the Arcturus color pair, to a shared-memory ring that the unprivileged tracker reads.
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

Options:

- `--with-dark`: also publish the near-black frames, as extra ring cameras flagged `FH_CAM_DARK`. They show only light sources, so they're no use for hands.
- `--with-color`: also publish the two Arcturus color cameras, flagged `FH_CAM_COLOR`. Each is the luma of the 10-bit frame's valid 1972x2464 (the top 8 bits), at half size (`--color-scale 2`: 986x1232) and at most 30 fps (`--color-fps`; the cameras run at 60). Frames that carry the module's warped half-size copy are dropped. Their `capture_ns` is on the color module's clock (2.2 s off the mono cameras' on 2026-09-29), so line them up with the mono cameras by `dqbuf_ns`. Each frame costs about 0.65 ms of cache sync and 1.1 ms of decoding, so both cameras at 30 fps take about 11% of a core.
- The ring holds 8 cameras: 4 mono, plus 4 dark twins or 2 color cameras.
- `--sensor S`: only the mono cameras whose sensor name contains S.

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

Which camera is which: video9 is `slam_left`, video13 is `slam_right`, video6 is `upper_left` and video7 is `upper_right`. This was checked by rendering the same view from each camera with the factory calibration. But fh-camd tells the side cameras' buffers apart only by XRService's allocation order, and after some XRService restarts it gets them backwards: check with `tools/check_sides.py --ring` and run the tracker with `--swap-sides` when it says swapped. The color cameras are video3 (`arcimx616 0-0010`) and video0 (`0-001a`); which of them is `passthrough_left` in the module's calibration is for `tools/check_color.py` to settle on a recording with texture in view. `tracker/live.py` maps them by capture pipe (`/sys/class/video4linux/videoN/name`).

`discovery` in `xrcams.c` is adapted from FrameEyeCameraFeed (MIT, see `LICENSE.FrameEyeCameraFeed`).
