# Hands (experimental)

Hand tracking from the headset's own cameras. It serves two things in Frametop:

- **Hand cutouts:** where your hand is between an eye and a screen, that eye sees the room through the screen (ft-screens, `screens/handcut.cpp`), so your hands show over the screens the way they do on a Vision Pro.
- **Pinches:** look at something and pinch to click it, pinch and move to drag, with the eye tracker doing the looking (`gaze/`). The tracker publishes the pinches. The pointer helper doesn't read them yet.

Two programs, each a user service that starts and stops with SteamVR:

- `ft-camd` (`camd/`, C) borrows XRService's camera buffers and publishes the four IR tracking cameras' frames to a shared-memory ring. It runs on the host.
- `ft-hands` (`track/`, C++) finds hands in those frames with MediaPipe's palm and landmark models on ncnn, triangulates them, and publishes them. It runs in the dev container.

```
hands/run.sh install            # build, give ft-camd its capabilities (sudo, once per build), enable
hands/run.sh status             # the services, and ft-hands' last status lines
hands/run.sh log [lines]
hands/run.sh restart            # after changing a setting
hands/run.sh caps               # after rebuilding ft-camd (a rebuild clears its capabilities)
hands/run.sh uninstall
```

Settings in `~/.config/frametop.conf` (`FT_<name>` in the environment overrides them):

- `HANDS_SWAP_SIDES=1`: the two side cameras' names are swapped (see ft-camd below). Check with `tools/check_sides.py --ring`.
- `HANDS_CPUS=5,6,7`: the CPUs the model threads run on (below).

Files, all in `/run/user/UID/frametop-hands/` (private to the user; not `/run/user/UID/frametop/`, which the desktop session deletes whenever it starts):

| File | Written by | Layout | Read by |
| --- | --- | --- | --- |
| `cam-ring` | ft-camd | `camd/fhring.h` | ft-hands, `tools/ring.py` |
| `hands` | ft-hands | `include/fh_hands.h` | ft-screens (`screens/handcut.cpp`) |
| `gestures` | ft-hands | `include/fh_gestures.h` | `tools/watch_gestures.py`; the pointer helper, later |

The source keeps the `fh_` names and magic strings of frame-hands, where this was developed (`~/Desktop/Projects/frame-hands` on the developer's Frame, which keeps the recordings, probes and Python prototype). So its recordings and tools still work.

## ft-camd

XRService owns the headset cameras. ft-camd borrows its DMA-BUFs read-only with `pidfd_getfd`, the same way FrameEyeCameraFeed does. It never touches XRService's V4L2 descriptors. `discovery` in `camd/xrcams.c` is adapted from FrameEyeCameraFeed (MIT, see `camd/LICENSE.FrameEyeCameraFeed`).

Polling buffers for changes can catch a frame while the camera is still writing it. Instead, ft-camd listens to the `v4l2:v4l2_dqbuf` tracepoint, which fires when XRService takes a buffer. It gives the buffer index, the sequence number and the capture timestamp. ft-camd learns which DMA-BUF holds each V4L2 index by watching which buffer changes at each dequeue:

- Right after XRService allocates its buffers, the mapping is allocation order.
- After XRService restarts streaming, the order is shuffled, and the mapping is learned index by index.
- The two upper cameras share one run of buffers. For them, only allocation order can tell the cameras apart.
- It also re-maps an index on the fly when its buffer holds no new frame.

**Privileges.** Setting up needs three things. `pidfd_getfd` on XRService needs `CAP_SYS_PTRACE`, because the Frame has `ptrace_scope=1`. The system-wide tracepoint needs `CAP_PERFMON`, because `perf_event_paranoid` is 2. Its format files are root-only, which needs `CAP_DAC_READ_SEARCH`. `hands/run.sh install` gives the binary those capabilities with `sudo setcap`. ft-camd drops them all once it has set up, before it reads a frame, and then runs as you. XRService runs as you too. It also runs under `sudo`, for trying it by hand, and then drops to the user who ran sudo. It reads nothing from the ring's readers.

The ring is mode 0600, in a folder only you can write. Frame handling:

- Only complete, bright frames are published. The cameras alternate a normal exposure with a near-black one, so each camera gets 30 of its 60 fps.
- A copy torn by the camera overwriting the buffer is dropped.
- Each copy takes about 0.1 ms, and a cache sync about 0.15 ms.

Options:

- `--with-dark`: also publish the near-black frames, as extra ring cameras flagged `FH_CAM_DARK`. They show only light sources, so they're no use for hands.
- `--with-color`: also publish the two Arcturus colour cameras, flagged `FH_CAM_COLOR`. Each is the luma of the 10-bit frame's valid 1972x2464 (the top 8 bits), at half size (`--color-scale 2`: 986x1232) and at most 30 fps (`--color-fps`; the cameras run at 60). Frames that carry the module's warped half-size copy are dropped. Their `capture_ns` is on the colour module's clock (2.2 s off the mono cameras' on 2026-09-29), so line them up with the mono cameras by `dqbuf_ns`. Each frame costs about 0.65 ms of cache sync and 1.1 ms of decoding, so both cameras at 30 fps take about 11% of a core.
- The ring holds 8 cameras: 4 mono, plus 4 dark twins or 2 colour cameras.
- Colour isn't reliable yet. In the lit-room test of 2026-09-30, the colour cameras kept losing their buffer mapping: 30 frames in a row looked unchanged, the camera relearned, and after 5 relearns ft-camd exited. Each relearn samples all 32 colour buffers, which also made the mono cameras miss frames. Runs with the headset idle (no hands, no cutouts) had none of this, and no half-size copies either, while the failing runs had many. So the passthrough compositor may be writing into the colour buffers while Room View shows. Whether a frame is new is judged on the luma rows only: the chroma after them hardly changes in a lit room. `FT_CAMD_DEBUG=1` prints, at each colour stale frame, how many sampled words changed in every candidate buffer.
- `--sensor S`: only the mono cameras whose sensor name contains S.
- `--status S`: a status line every S seconds (0: never).

It exits when XRService exits, or when a camera's buffers keep going stale, which means XRService has reallocated them. The service starts it again, and it attaches to the new buffers.

**Which camera is which:** video9 is `slam_left`, video13 is `slam_right`, video6 is `upper_left` and video7 is `upper_right`. This was checked by rendering the same view from each camera with the factory calibration. But ft-camd tells the side cameras' buffers apart only by XRService's allocation order, and after some XRService restarts it gets them backwards. Then every hand is seen by one camera only, at the wrong depth, and the hand holes land beside the hands. With the headset on, looking at a room with some texture, `tools/check_sides.py --ring` says whether the names are right (exit 0), swapped (exit 3), or it can't tell (exit 2). When they're swapped, set `HANDS_SWAP_SIDES=1`. The colour cameras are video3 (`arcimx616 0-0010`) and video0 (`0-001a`); which of them is `passthrough_left` in the module's calibration is for `tools/check_color.py` to settle, on a recording with texture in view.

## ft-hands

```
hands/build/ft-hands                  # status every 5 s; Ctrl+C to stop
hands/build/ft-hands --int8           # the 8-bit models (models/ncnn/*-int8.ncnn.*)
```

Run it in the dev container (`distrobox enter dev -- ...`). It reads the factory calibration from `/persist` (`/run/host/persist` in the container).

Options:

- `--threads N`: model threads, pinned to the `--cpus` list. Default 3.
- `--cpus LIST`: CPUs for the model threads and the main loop. Default `5,6,7` (`HANDS_CPUS`). SteamOS starts user processes on CPUs 0-4, and XRService's head tracking runs on 2-3. With the headset on, a step took 8.4 ms on 5-7 against 13.2 ms on 2-4, and SteamVR's frame timing didn't change (2026-09-29, three rounds of the same replayed frames).
- `--contrast MODE` or `PALM/HAND`: how crops are equalized before the models see them: `clahe[:CLIP]`, `none`, or `stretch` (1st-99th percentile). Default `clahe:2/none`. In the dim recording, CLAHE let the palm search find about 10% more hands, but it made the landmarks jitter more (published median 6.9 mm, against 6.0 mm with plain landmark crops).
- `--swap-sides`: swap the two side cameras (`HANDS_SWAP_SIDES`, see ft-camd).
- `--seconds N`: stop after N seconds.
- `--status S`: how often to print status, in seconds.
- `--models DIR`: where the models are.
- `--nice N`: niceness. Default 5, so the VR stack wins contested CPUs.
- `--no-publish`: don't write the hands and gestures files.
- `--record DIR`, `--record-for S`: save every frame set for S seconds (default 120) to `DIR/sets.bin`. That's about 80 MB/s. Sending the tracker SIGUSR1 (`pkill -USR1 -x ft-hands`) starts a recording in `~/.local/share/frametop/hands/rec-<time>` without a restart. Recordings are images of your hands and room: they stay on the headset unless you move them.
- `--record-only`: record without tracking or publishing, so it can run beside the live tracker. Give it `--record DIR`, since SIGUSR1 would reach both trackers. With `ft-camd --with-dark`, recordings also hold each camera's newest dark frame as `<name>_dk`, which doubles the rate. With `--with-color`, each colour camera's newest frame is saved with every set, as `color_video<N>`, which adds about 70 MB/s. Run the recorder at normal I/O priority: idle I/O priority stalled a 165 MB/s recording.
- `--keep-presence P`: the landmark presence a tracked view needs to stay tracked. New views always need 0.5. Default 0.5. Lowering it to 0.2 barely helped in the bright recording, because lost hands drop to near-zero presence.
- `--ring PATH`: read frames from another ring, such as `ft-ringplay`'s.

The status line also says how often a hand was on each side (by where the wrist is), and why views and hands came and went: views lost (the landmark model stopped seeing the hand), handoff misses (a crop projected from the hand's 3D position found nothing), duplicates, splits (two views disagreed in 3D), and hands created, merged and forgotten.

### Scheduling

- Each hand is tracked in its best two cameras, the way MediaPipe tracks: the landmark model runs on a crop placed from the previous landmarks, with no palm detection.
- A hand seen in too few cameras is projected into the others through the calibration. Where it lands well inside a camera, that camera gets a crop to try. This is how a hand raised out of the side cameras reaches the upper ones.
- The palm detector runs only while fewer than two hands are tracked, at most 5 times a second, on a few zoomed tiles per search. Tiles are picked in proportion to how likely hands are there. Each tile is turned so the expected shoulder-to-hand direction points up.
- Frame sets are processed at 30 Hz while a hand moves faster than 0.25 m/s (or a pinch is down or closing), at 15 Hz otherwise, and at 5 Hz while no hand is in view.

### 3D

- **Two or more views:** each landmark is triangulated from the camera rays, weighted by the model's presence score. The median ray distance is reported as the residual.
- **Pairing views across cameras.** The side cameras sit side by side, so two hands next to each other at the same height fall on the same epipolar lines, and rays to two different hands can nearly meet close to the cameras. That made phantom hands 12-15 cm in front of the eyes, which tore holes through the screens. Each step now scores every way of pairing the views in two cameras and keeps the best. A pair scores well when its rays meet, when each view's apparent size matches the triangulated distance, and when the model calls both the same hand. The size check uses a fixed prior: with the model's average hand, clean pairs measure 0.71-1.51 times the one-view distance, and mismatched pairs mostly far less.
- **One view:** depth comes from the model's metric world landmarks, their spread across the palm against the angle it covers in the image, scaled by the user's hand size (learned while two views are available). That distance is off by 10-30% and wanders about 10% between frames, so a hand that drops to one camera keeps its last distance and drifts toward the one-view guess by 10% a frame.
- **Smoothing.** The published landmarks go through a One Euro filter: it smooths hard while the hand is still (tracking noise is several mm per frame) and hardly at all while it moves fast. The palm speed that sets the update rate is the filtered one; the raw speed read about 0.25 m/s from noise alone.
- **Capsules.** Forearms follow the hand's own axis, and nothing within 12 cm in front of the eyes is published.

How good the depth is, measured from recordings (2026-09-30, `--depth` below): the two lower cameras see the hands about 77% of the time, a lower and an upper camera 7-12%, and one camera 12-15%. Depth is the noisy direction. With the lower pair, it jitters 4-6 times as much as sideways position (published: 3-7 mm against 1-2 mm). The one-camera guess is a median 2-6 cm off. When a camera drops out, drifting 10% a frame toward that guess is worse than keeping the last distance (after 0.5 s a median 23-30 mm off, against 11-12 mm).

## Pinch

ft-hands detects a pinch per hand (`track/pinch.h`) and publishes it to the gestures file. The layout, and how to read it without missing quick taps, is in `include/fh_gestures.h`.

- A pinch begins when the thumb and index tips come within `--pinch-begin` (default 0.020 m). It ends when they open past `--pinch-end` (0.035 m) for 2 processed frames in a row, or when the hand stays lost for 0.25 s (flagged lost).
- The distance comes from MediaPipe's world landmarks: the model's own 3D hand pose, averaged over the hand's views, at the user's hand size. `--pinch-triangulated` uses the triangulated tips instead. On two recordings without deliberate pinches, the world landmarks came under 2 cm in 0.2-1% of frames, against 3.3-4.5% for the triangulated tips. In the dim recording, typing still gave 2 pinches a minute before the palm check below.
- No pinch begins while the palm faces down (`--pinch-palm-down MAX`: the palm normal's share of the head's up axis, default 0.6; 1 turns it off), and a close held back that way has to open again before a pinch can begin. Typing curls the thumb onto the index. In the lit recording of 2026-09-30, typing on a keyboard in the lap began 23 pinches in about 2 minutes, all with the palm facing down (0.69-1.00), while the 26 deliberate ones read 0.00-0.50. The limit held back every typing pinch and none of the deliberate ones. Looking down tilts the head frame, which lowers the reading for a hand on a keyboard, so the consumer's gaze check stays the other guard.
- A hand a pinch is down on stays with that side until the pinch ends. The left/right call is a running average of the model's, and when it flipped mid-pinch, the other side took the same hand and both sides pinched at once.
- The pinch point is midway between the thumb and index tips. A drag is the pinch point now, minus where it was when the pinch began, both turned into the room with the HMD pose at their capture times.
- `tools/watch_gestures.py` prints begins, ends and drag offsets live, and `--distance` prints each hand's distance.

The pointer helper is the natural consumer. Its gaze mode already treats a press as "stop where the gaze put it, drag onto the target, click on release", and "hold still for half a second, then move" as a drag. A pinch begin would be the press, the end the release, and the pinch point's movement the drag.

## Recordings

`hands/build.sh --tools` also builds the offline tools.

`ft-handreplay DIR` runs a recording through the tracker with the live scheduling and reports how well it kept the hands: hands per set, left and right coverage, track lengths, pinches, jitter, and the same reasons as the status line.

```
hands/build/ft-handreplay ~/.local/share/frametop/hands/rec-20260929-120000 --cost --oracle 10 --timeline /tmp/tl.txt
```

- `--cost`: instead of timing the steps, charge each round of model calls what it typically costs live (10 ms landmarks, 18 ms palms), so results repeat exactly.
- `--oracle N`: every N-th set, also search every tile of every camera, and report how often the tracker had the hands that full search could find.
- `--slow F`: live, the tracker skips sets that arrive while it's busy. Replay counts each step's time times F as busy (default 1; the headset is busier live).
- `--timeline FILE`: a line per processed set and hand, with pinch events and distances.
- `--cams mono|color|all`: which cameras to track with (default `mono`). `color` tracks with the Arcturus pair alone, for comparing it with the IR cameras on the same recording. It needs a recording made with `ft-camd --with-color`. `--color-left NODE` (`color_video0` or `color_video3`) and `--color-crop subtract|none` say how the module's calibration maps onto the images; `tools/check_color.py` finds out.
- `--depth FILE`: a line per hand per processed set for `tools/depth_report.py`, which measures the depth without ground truth: how the hands were seen, the noise along the line of sight against across it, each camera's one-view distance against the triangulated one, and what a camera dropping out would do.
- The pinch, contrast and presence options are ft-hands'.

`ft-ringplay DIR --ring PATH [--from S] [--to S] [--loop]` publishes a recording into a ring file in real time, as ft-camd would, so `ft-hands --ring PATH --no-publish` runs the same frames run after run. It needs no privileges, and it skips the dark frames.

## Tools

Python, with NumPy and OpenCV (in the dev container: `python3-numpy`, `python3-opencv`, which `setup/dev-container.sh` installs). Off the Frame, `FRAME_JOB_DEVICE_ROOT` can point at a folder with copies of the headset's calibration files.

- `tools/check_sides.py --ring` (or a recording): are the side cameras named right?
- `tools/check_color.py REC`: how the colour module's calibration maps onto its images.
- `tools/show_set.py REC`: a recording's frame sets as images.
- `tools/watch_gestures.py [--distance]`: pinches, live.
- `tools/depth_report.py DEPTH`: the depth measures above.
- `tools/convert_models.py`: how `models/ncnn` was made from the OpenCV Zoo ONNX ports of MediaPipe's models (see `models/NOTICE`).

## Build

`hands/build.sh` builds in the dev container on the Frame, into `hands/build/`, with `hands/Makefile`. The first build fetches ncnn at a pinned tag and builds it into `hands/build/ncnn`, which takes a few minutes; `NCNN=DIR` points at an ncnn install already built instead. ft-camd is linked statically, because it runs on the host, which has an older glibc than the container.
