# trackd

`fh-tracker` is the C++ version of `tracker/live.py`. It uses the same scheduling and the same models. The models run on a few threads, with no Python in the loop. It reads `fh-camd`'s ring and publishes hands to `$XDG_RUNTIME_DIR/frame-hands/hands` (`include/fh_hands.h`), where Frametop's ft-screens picks them up for the hand cutouts.

```
sudo camd/fh-camd &
trackd/fh-tracker                  # status every 5 s; Ctrl+C to stop
trackd/fh-tracker --int8           # the 8-bit models (models/ncnn/*-int8.ncnn.*)
```

Options:

- `--threads N`: model threads, pinned to the `--cpus` list. Default 3.
- `--cpus LIST`: CPUs for the model threads and the main loop. Default `5,6,7`. With the headset on, that ran a step in 8.4 ms against 13.2 ms on `2,3,4`, where XRService's head tracking also runs, and SteamVR's frame timing didn't change (`probes/core_ab.py`, 2026-09-29).
- `--contrast MODE` or `PALM/HAND`: how crops are equalized before the models see them: `clahe[:CLIP]`, `none`, or `stretch` (1st-99th percentile). Default `clahe:2/none`. In the dim recording, CLAHE let the palm search find about 10% more hands, but it made the landmarks jitter more (published median 6.9 mm, against 6.0 mm with plain landmark crops).
- `--swap-sides`: swap the two side cameras (`slam_left`, `slam_right`). fh-camd tells their buffers apart by XRService's allocation order, and after some XRService restarts that order is reversed. Then every hand is seen by one camera only, at the wrong depth, and the hand holes land beside the hands. With the headset on and looking at a room with some texture, `tools/check_sides.py --ring` tells whether the names are right (exit 0), swapped (exit 3), or it can't tell (exit 2).
- `--seconds N`: stop after N seconds.
- `--status S`: how often to print status, in seconds.
- `--models DIR`: where the models are.
- `--nice N`: niceness. Default 5, so the VR stack wins contested CPUs.
- `--no-publish`: don't write the hands file.
- `--record DIR`, `--record-for S`: save every frame set for S seconds (default 120) to `DIR/sets.bin`. That's about 80 MB/s. Sending the tracker SIGUSR1 (`pkill -USR1 -x fh-tracker`) starts a recording in `captures/rec-<time>` without a restart.
- `--record-only`: record without tracking or publishing, so it can run beside the live tracker. Give it `--record DIR`; SIGUSR1 would reach both trackers. With `fh-camd --with-dark`, recordings also hold each camera's newest dark frame as `<name>_dk`, which doubles the rate.
- `--keep-presence P`: the landmark presence a tracked view needs to stay tracked. New views always need 0.5. Default 0.5. Lowering it to 0.2 barely helped in the bright recording, because lost hands drop to near-zero presence.
- `--ring PATH`: read frames from another ring, such as `fh-ringplay`'s.

The status line also says how often a hand was on each side (by where the wrist is), and why views and hands came and went: views lost (the landmark model stopped seeing the hand), handoff misses (a crop projected from the hand's 3D position found nothing), duplicates, splits (two views disagreed in 3D), and hands created, merged and forgotten.

## Beyond the Python tracker

fh-tracker started as a port of `tracker/hands.py`. Replaying recordings (below) showed where it went wrong, and it now differs in these ways:

- **Pairing views across cameras.** The side cameras sit side by side, so two hands next to each other at the same height fall on the same epipolar lines, and rays to two different hands can nearly meet close to the cameras. That made phantom hands 12-15 cm in front of the eyes, which tore holes through the screens. Each step now scores every way of pairing the views in two cameras and keeps the best. A pair scores well when its rays meet, when each view's apparent size matches the triangulated distance, and when the model calls both the same hand. The size check uses a fixed prior: with the model's average hand, clean pairs measure 0.71-1.51 times the one-view distance, and mismatched pairs mostly far less. It doesn't use the learned hand size, which bad pairs had corrupted.
- **One view.** From how big the hand looks, its distance is off by 10-30% and wanders about 10% between frames. So a hand that drops to one camera keeps its last distance and drifts toward the one-view guess by 10% a frame.
- **Smoothing.** The published landmarks go through a One Euro filter: it smooths hard while the hand is still (tracking noise is several mm per frame) and hardly at all while it moves fast. The palm speed that sets the update rate (15 or 30 Hz) is the filtered one; the raw speed read about 0.25 m/s from noise alone.
- **Capsules.** Forearms follow the hand's own axis, and nothing within 12 cm in front of the eyes is published.

## Replay

`fh-replay DIR` runs a recording through the tracker with the live scheduling and reports how well it kept the hands: hands per set, left and right coverage, track lengths, and the same reasons as the status line.

```
trackd/fh-replay captures/rec-20260929-120000 --oracle 10 --timeline /tmp/tl.txt
```

- `--oracle N`: every N-th set, also search every tile of every camera, and report how often the tracker had the hands that full search could find.
- `--slow F`: live, the tracker skips sets that arrive while it's busy. Replay counts each step's time times F as busy (default 1; the headset is busier live).
- `--timeline FILE`: a line per processed set and hand.

## Playing a recording live

`fh-ringplay DIR --ring PATH [--from S] [--to S] [--loop]` publishes a recording into a ring file in real time, as fh-camd would, so `fh-tracker --ring PATH --no-publish` runs the same frames run after run. It needs no root, and it skips the dark frames. `probes/core_ab.py` uses it to compare CPU placements (A: CPUs 2-4, B: 5-7, C: no tracker), with the headset on so SteamVR's compositor is running.

## Build

ncnn is built from source into `vendor/ncnn/build/install`. The build also provides the `ncnn2table` and `ncnn2int8` quantization tools.

```
git clone --depth 1 --branch 20260526 https://github.com/Tencent/ncnn.git vendor/ncnn
cd vendor/ncnn && mkdir build && cd build
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DNCNN_VULKAN=OFF -DNCNN_BUILD_TOOLS=ON -DNCNN_SIMPLEOCV=ON \
      -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_TESTS=OFF -DCMAKE_INSTALL_PREFIX=$PWD/install ..
nice ninja install
cd ../../../trackd && make           # fh-tracker and fh-replay; `make nettest` for the model check
```

It needs jsoncpp for the calibration files (the host has it).

## Checks

- `tools/nettest_compare.py CAPTURE` runs the C++ model code (`nettest`) and `tracker/models.py` on the same crops from a recording, and compares the palms, ROIs and landmarks. Add `--int8` to check the 8-bit models.
- `tools/make_int8_calib.py CAPTURE OUT` cuts the calibration crops that `ncnn2table` needs to quantize the models.
