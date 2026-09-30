# Hands in Frametop: migration plan

Hand tracking from the headset's own cameras has been built as a separate project, frame-hands (`~/Desktop/Projects/frame-hands` on the Frame, a local git repo with no remote). The plan is to make it a native Frametop component, like `gaze/` and `power/`, instead of a separate module. The work happens on branch `hands-migration` (worktree `frametop-hands/` in the PC workspace) and is merged into `experimental` after it's been tested in the headset.

Builds from this worktree must sync to their own folder on the Frame, never `~/dev/frametop`: run every script with `FRAME_REPO=/home/steamos/dev/frametop-hands`.

## Status (2026-09-30)

Steps 1-5 are done:

- frame-hands' pending work was committed there (6c63c9e).
- Its filtered history was merged under `hands/` (1a76d15), then laid out (`trackd/` to `track/`).
- The renames, the Frametop paths, and ft-camd's file capabilities are done. So are `hands/Makefile`, `build.sh`, `run.sh`, the two units, the README, the settings, the installer step, and ft-screens on the shared header.
- Built in the dev container on the Frame, and on the 7i.
- Checked without the headset:
  - `ft-handreplay` against frame-hands' `fh-replay`, both x86 with `--cost`, on the whole dim recording and the first 60 s of the bright one: identical summaries and byte-identical depth dumps. The Makefile's own ncnn build is included in that.
  - `ft-ringplay` into `ft-hands` on the 7i tracked, pinched, and wrote `/run/user/UID/frametop/{hands,gestures}`.
  - On the Frame, ft-hands in the container finds the calibration through `/run/host/persist`, and ft-camd without its capabilities refuses with a clear message.

Next is step 6, with the user: `hands/run.sh install` (sudo setcap), then a desktop restart from this branch so ft-screens reads the new path.

## What frame-hands is today

| Part | What it is | Size |
| --- | --- | --- |
| `camd/` | `fh-camd`, the camera broker (C). It borrows XRService's camera DMA-BUFs read-only with `pidfd_getfd`, times them with the `v4l2_dqbuf` tracepoint, and publishes the four IR cameras (and optionally the two colour cameras) to a shared-memory ring. It starts as root and drops to the user after setup. Adapted in part from FrameEyeCameraFeed (MIT, licence file kept). `fh-camprobe` is its discovery and recording probe. | camd 1.1k lines, tp 0.4k, xrcams 0.8k, camprobe 1.1k |
| `trackd/` | `fh-tracker` (C++): the tracker, the models on ncnn, the calibration (jsoncpp), the pinch detector, the publisher, and the recorder. Also `fh-replay` (offline replay and scoring), `fh-ringplay` (plays a recording into a ring), and `nettest`. | 2.9k lines |
| `include/` | The hands file (`fh_hands.h`, read by ft-screens) and the gestures file (`fh_gestures.h`, pinches). | |
| `models/ncnn/` | MediaPipe's palm detector and hand landmark model, from the OpenCV Zoo ONNX ports (Apache-2.0), converted to ncnn in float and int8. | 5.9 MB |
| `tools/` | Python analysis: side-camera check, colour calibration check, frame viewer, gesture watcher, depth report, model comparison, int8 calibration, model conversion. | ~1.1k lines |
| `tracker/` | The Python prototype of the tracker. Some tools import its `calib.py` and `models.py`. | 1.3k lines |
| `probes/`, `notes/`, `re/`, `shim/` | One-off experiments, reverse-engineering notes on SteamVR's passthrough internals, a disassembly (not in git), and a header from an abandoned XRService shim approach. | |
| `vendor/`, `captures/` | ncnn and FrameEyeCameraFeed clones, and recordings of the user's hands and room (tens of GB). Neither is in git. | |

Today it runs by hand: `sudo camd/fh-camd`, then `trackd/fh-tracker`. There are no units and no installer. Files: `/run/frame-hands/ir-ring` (the ring, in a root-owned folder), and `$XDG_RUNTIME_DIR/frame-hands/hands` and `gestures`.

Frametop already has the consumer side on `experimental`: `screens/handcut.{h,cpp}` cuts the hands out of the screens, with its own copy of the hands file layout, and `screens/handtest.cpp` tries it on a test panel.

## Where it goes

A top-level `hands/` folder, laid out like `gaze/`:

```
hands/
  README.md                 # from trackd/README.md and camd/README.md
  build.sh                  # ft-camd, ft-hands; --tools also builds the replay tools
  run.sh                    # install|uninstall|start|stop|restart|status|log
  frametop-camd.service     # user units (templates, @REPO@)
  frametop-hands.service
  include/                  # fhring.h, fh_hands.h, fh_gestures.h: shared with screens/ and pointer/
  camd/                     # ft-camd: camd.c tp.c xrcams.c, LICENSE.FrameEyeCameraFeed
  track/                    # ft-hands: tracker, nets, calib, pinch, publish, record; replay.cpp
                            #   (ft-handreplay) and ringplay.cpp (ft-ringplay) for recordings
  models/                   # the ncnn models, with NOTICE (Apache-2.0, MediaPipe / OpenCV Zoo)
  tools/                    # the Python checks, watch_gestures, depth_report, calib.py, ring.py
```

Left behind in frame-hands, which stays as the lab: the recordings, the Python prototype (the tools that need `calib.py` or `models.py` get a trimmed copy in `hands/tools/`), `probes/`, `notes/`, `re/`, `shim/`, `camprobe`, and `vendor/`. The reverse-engineering notes don't belong in a public repo, and recordings are images of the user's hands and room, so they never go into git.

## Names

Programs within 15 characters, `ft-` prefix; files under `frametop`:

| Now | In Frametop |
| --- | --- |
| `fh-camd` | `ft-camd` |
| `fh-tracker` | `ft-hands` |
| `fh-replay`, `fh-ringplay` | `ft-handreplay`, `ft-ringplay` |
| `/run/frame-hands/ir-ring` | `$XDG_RUNTIME_DIR/frametop/cam-ring` |
| `$XDG_RUNTIME_DIR/frame-hands/hands`, `gestures` | `$XDG_RUNTIME_DIR/frametop/hands`, `gestures` |

The source keeps its `fh_` identifiers and header names (`fh_hands.h`, `fh_gestures.h`, `fhring.h`), and the file formats keep their magic strings, so recordings and tools from frame-hands keep working. Programs, units and runtime paths change.

## Build

- `hands/build.sh` builds in the dev container through `scripts/frame.sh --build`, into `hands/build/`, like the other components. `FRAME_BUILDER=pc` can take the ncnn build.
- ncnn: fetched at a pinned tag (20260526, as now) into `hands/build/ncnn` and built once, the way `screens/build.sh` fetches the OpenVR header, with frame-hands' options so results match. `NCNN=` points the build at an existing install instead. Every net runs single-threaded (`num_threads = 1`), with the tracker spreading nets over its own pinned threads, so OpenMP could go later.
- ft-hands runs in the dev container like ft-pointer and ft-powerd (`distrobox enter dev --`, after `scripts/container-up.sh`). Today's fh-tracker runs on the host and works only because the host happens to have the same `libjsoncpp.so.25` and libgomp as the container. Inside the container the calibration is at `/run/host/persist`, and calib.cpp (and `tools/calib.py`) fall back to it when `/persist` isn't there.
- ft-camd has to run on the host (below), so it's linked statically (only libc and libm; `glibc-static` goes into `setup/dev-container.sh`). The host has an older glibc than the container.

## Running it

**ft-camd needs privileges**, only while it sets up: `pidfd_getfd` on XRService (the Frame has `ptrace_scope=1`), system-wide tracepoints (`perf_event_paranoid=2`), and the tracepoint files, which are root-only (`/sys/kernel/tracing/events/v4l2/v4l2_dqbuf/{id,format}` are mode 0440). A rootless container's root can't do any of that, so it runs on the host. Two ways:

- **A. File capabilities (chosen, 2026-09-30).** The installer runs `sudo setcap cap_sys_ptrace,cap_perfmon,cap_dac_read_search+ep hands/build/ft-camd` once. ft-camd then runs as the user, in a user unit `PartOf=steamvr.service`, so it starts and stops with SteamVR, and its ring lives in the user's runtime folder. It drops all capabilities after setup, as it drops root today. Nothing ever runs as root. Writing the file clears its capabilities, so a rebuilt ft-camd needs the setcap again. It changes rarely. `/home` on the Frame is ext4 without `nosuid`, so file capabilities work there.
- **B. Root system service**, like the Bluetooth fixes: a root-owned copy in `/var/lib/frametop/`, a unit in `/etc/systemd/system/`. It would have to watch for XRService itself, because a system unit can't follow the user's `steamvr.service`.

Either way the password is needed once at install, through the same `sudo -S` path the Bluetooth fixes use, and only after asking.

**ft-hands** is a user unit, `frametop-hands.service`: after `frametop-camd.service`, `PartOf=steamvr.service`, nice 5, model threads on CPUs 5-7 (measured best on 2026-09-29).

**Settings** in `~/.config/frametop.conf`: `HANDS_SWAP_SIDES=1` and `HANDS_CPUS=5,6,7`, read by ft-hands. It's on while its services are installed (`hands/run.sh install`, `uninstall`), so there's no `HANDS` switch. There's no setting for colour yet. Later, a switch in Frametop Display Settings.

**Installer:** an optional last step in `install.sh`, off by default, which asks first because it needs sudo.

## Interfaces

- `screens/handcut.cpp` includes `hands/include/ft_hands.h` instead of its own copy of the layout, and reads the new path. ft-screens and ft-hands change together on this branch.
- Pinches go to the pointer helper. It maps the gestures file and checks the begin and end counters each tick. A begin is a press, an end the release, and the pinch point's movement a drag. In gaze mode, the press lands where you look. The counters mean a quick tap between two ticks isn't missed. The tracker knows nothing about the pointer.

## Open items that aren't part of the move

These block shipping hands to other people, not the migration:

- **The side-camera swap.** After some XRService restarts, fh-camd publishes the two side cameras under each other's names. Today it's caught by hand (`tools/check_sides.py --ring`, then `--swap-sides`). It needs fixing at the source (tell the buffers apart by the `dqbuf` tracepoint's device, the way the colour pair is split), or at least an automatic check at start-up.
- **The colour cameras' calibration mapping** (`tools/check_color.py` on a recording with texture).
- **Depth when one camera loses the hand.** From the 2026-09-30 replay measurements: drifting 10% per update toward the one-camera guess (`kMonoDepthGain`) makes the depth worse than keeping the last distance. Try 0.02.

## Public repo

Frametop is public. **Not pushed to GitHub until the user says it's ready** (user decision, 2026-09-30). When it is, it publishes:

- The camera borrowing (`pidfd_getfd` on XRService's buffers) and the tracepoint timing. FrameEyeCameraFeed already does the same publicly. Its MIT licence and credit stay with the code.
- The models, under Apache-2.0, with a NOTICE.

It doesn't publish the reverse-engineering notes, the probes, or any recording. They stay in frame-hands.

## History

frame-hands' work was committed there first (6c63c9e, its 4th commit). Its history was then filtered to drop what stays behind (`notes/`, `probes/`, `shim/`, `camd/camprobe.c`, the camprobe tools, the Python prototype except `calib.py` and `models.py`, and `.frame-job`) from every commit. It was merged into this branch under `hands/` (a subtree merge), so blame still leads to where each line came from. The renames come after, as their own commits.

## Steps

1. In frame-hands: commit the pending work, as its last state before the move (needs the user's OK).
2. On this branch: import it under `hands/`, then rename the programs and paths. The behaviour stays identical.
3. `hands/build.sh`, `run.sh`, the two units, the README, the settings, and the installer step.
4. ft-screens' hand cutouts on the shared header and the new path.
5. Check without the headset. `ft-handreplay` on the 2026-09-29 recordings with `--cost` is repeatable, so its summary must match `fh-replay`'s exactly. And `ft-ringplay` into ft-hands must publish the same hands as into fh-tracker.
6. In the headset, with the user and after asking: stop fh-camd and fh-tracker, install the services from `~/dev/frametop-hands`, and restart the desktop from this branch so ft-screens reads the new path.
7. Pinch into the pointer helper (it can also follow the merge). The gaze work is on the Frame's `~/frametop` main: on 2026-09-30 that branch had 4 commits `experimental` doesn't have, plus uncommitted work in the pointer helper's gaze mode. Build this step on wherever that work lands, not on this branch's older copy.
8. Merge into `experimental`. It's checked out in a worktree on the Frame (`~/frametop/.worktrees/experimental`, where the live desktop runs), so the merge happens there, or `experimental` is switched away first.
