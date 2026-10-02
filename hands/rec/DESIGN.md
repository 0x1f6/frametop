# Hand recorder: design (Phase 1 of the hands plan)

The hand recorder guides a person through recording their hands with the headset's cameras. It saves the recordings as files, lets them review and delete anything, then exports a package to contribute to the open hand dataset. Recordings never leave the headset unless the person uploads them.

The plan this belongs to is `~/Desktop/Projects/frame-hands/notes/hands-plan.md` (on the maintainer's Frame). In short, the dataset trains a small hand model for Frametop's hand cutouts.

## Parts

| Part | What it does |
|---|---|
| `hands/rec/panel/ft-handpanel.cpp` (C++, dev container) | The headset panel: a SteamVR overlay fixed to the head that shows the prompts. It also places the "touch the dot" target in the room and logs head and controller poses. Driven over `@ft_handpanel`. |
| `hands/rec/session.py` (Python, no Qt) | The session runner. It reads `script.json`, starts and stops the recordings, and drives the panel. It reads the live hands file for feedback and writes each take's files. It also runs from the command line (`--dry-run`) for testing. |
| `hands/rec/script.json` | The guided script: sections, prompts, timings. |
| `hands/rec/takes.py` (Python, no Qt) | Reads sessions and takes from disk: frame sets for review, deleted ranges, export (compress, strip, manifest, checksums). |
| `hands/rec/ft_handrec.py` + `main.qml` (PySide6 + Kirigami, dev container) | The desktop window: consent, the before-you-start checklist, the session controls, review, export and upload instructions. |
| `hands/rec/ft-handrec` | The host launcher, like `input-settings/ft-input-settings`. |
| `hands/rec/build.sh` | Builds `ft-handpanel` into `hands/rec/build/`, like `gaze/build.sh`. |
| `hands/rec/CONSENT.md`, `hands/rec/UPLOAD.md` | The texts the window shows. |
| ft-hands `--record-hz N` (done) | Records at most N frame sets a second. The recorder uses 10. |

Every part runs in the dev container, as ft-hands and Input Settings do. The host Python isn't used: it lacks PySide6 and zstd. `setup/dev-container.sh` gains `zstd`.

## Processes during a session

- **ft-camd** publishes the camera ring. If it isn't running, the session starts it as the transient user unit `frametop-handrec-camd.service`, the way `hands/ft-cutouts` starts `frametop-cutouts-camd.service` (needs `hands/build/ft-camd` with capabilities: `hands/run.sh caps`). An ft-camd already running from ft-cutouts or ft-handsctl is used as it is.
- **A tracking ft-hands** gives feedback through the hands file: which hands are seen, the palm's distance, the index tip. If none is running, the session starts `ft-hands --no-gestures --status 0` (unit `frametop-handrec-hands.service`). An ft-hands already running is used as it is.
- **A recording ft-hands** runs once per take: `ft-hands --record-only --record TAKE_DIR --record-for SECONDS --record-hz 10 --status 0`. It runs as a plain child process of the session, ended with SIGTERM if the take stops early. SIGTERM ends ft-hands' loop, and `Recorder` writes out its queue when it's destroyed.
- **ft-handpanel** runs as a child process with `--watch-stdin`. It shows the panel and logs poses during each take.

Test hooks:
- `--ring PATH` goes to both ft-hands (`ft-ringplay` publishes a recording there, so the whole flow runs without the headset).
- `--no-start` uses only what's already running.
- `--dry-run` runs no processes and only prints the panel commands, with timing sped up by `--speed X`.

## Files

```
~/.local/share/frametop/hands/contrib/
  profile.json                  consent and contributor id (below)
  sessions/<YYYYMMDD-HHMMSS>/
    session.json                the session: checklist answers, lighting, versions, takes
    calibration.json            /persist/xrservice.json with identifying fields removed (below)
    takes/<NN>-<section>/
      sets.bin                  ft-hands' recording (FHSET01, hands/track/record.h), 10 sets/s
      prompts.jsonl             what the person was asked to do, when (below)
      poses.jsonl               head and controller poses from ft-handpanel (below)
      take.json                 {"section", "title", "started_ns", "ended_ns", "status": "complete"|"stopped"|"skipped",
                                 "deleted": [[from_ns, to_ns], ...], "notes": ""}
  exports/<session>/            what export writes (below)
```

All `_ns` times are CLOCK_MONOTONIC nanoseconds, the clock of `dqbuf_ns` in sets.bin and of `capture_ns` in the hands file. sets.bin's `capture_ns` is the camera clock (CLOCK_MONOTONIC_RAW).

### profile.json

```json
{"schema": 1, "contributor": "<random uuid4>", "consent": {"version": "2026-10-02", "accepted": "<ISO time>", "adult": true},
 "optional": {"handedness": "right|left|both|", "notes": ""}}
```

No name, email, or account. The contributor id is random, so several sessions from one person can be held out together in evaluation. Withdrawal also goes by that id.

### session.json

```json
{"schema": 1, "tool": "ft-handrec <git describe>", "started": "<ISO>", "contributor": "<uuid>",
 "lighting": {"chosen": "dim|room|daylight", "ring": {"<cam>": {"mean": 0.0, "dark_mean": 0.0}}},
 "checklist": {"objects": ["pencil", "phone", "cup", "keyboard", "mouse", "gamepad", "small"], "own_objects": ["..."],
               "controllers": "straps|none", "sleeves": "short|long|", "rings": false, "watch": false, "notes": ""},
 "device": {"steamos": "<VERSION_ID from /etc/os-release>", "steamvr": "<version if known>", "cameras": [{"name", "width", "height"}]},
 "takes": ["01-hand-size", "..."]}
```

### calibration.json

This is a copy of `/persist/xrservice.json` (`/run/host/persist/` in the container). Keep the cameras' intrinsics and extrinsics, and drop anything that identifies the unit: keys containing `serial`, `sn`, `uuid`, `mac` or `id`, or values that look like serial numbers. List what was removed in `session.json` (`"calibration_removed": [...]`), so a reviewer can check it.

### prompts.jsonl

One JSON object per line:

```json
{"t": 123, "event": "take", "section": "static-poses", "take": "03-static-poses"}
{"t": 123, "event": "prompt", "id": "static-poses/fist/left/near", "text": "...", "hands": "left|right|both|none",
 "pose": "fist", "distance": "near|mid|far|", "position": "centre|left|right|up|down|", "object": "", "controller": false}
{"t": 123, "event": "target", "id": "touch/3", "head": [x, y, z], "room": [x, y, z], "state": "show|hold|done|timeout"}
{"t": 123, "event": "bar", "target": 0.0}
{"t": 123, "event": "feedback", "left": true, "right": false, "palm_m": [0.0, 0.0]}
{"t": 123, "event": "pause"}
{"t": 123, "event": "resume"}
{"t": 123, "event": "end", "status": "complete|stopped|skipped"}
```

`feedback` is written about twice a second. It's the live tracker's view, kept for later checks; it's not a label.

### poses.jsonl

ft-handpanel writes one line per sample, 250 a second, from `GetDeviceToAbsoluteTrackingPose(TrackingUniverseStanding, 0)`:

```json
{"t": 123, "hmd": {"m": [12 floats, row-major 3x4], "r": 200, "ok": true},
 "left": {"m": [...], "r": 200, "ok": true}, "right": null}
```

`r` is `ETrackingResult` (200 = Running_OK, 201 = Running_OutOfRange, and so on). `left` and `right` are the devices holding those controller roles, or null. No device serials are logged.

## The panel (`ft-handpanel`)

- **Placement.** A SteamVR overlay fixed to the head, like `gaze/panel/ft-gazepanel.cpp`: key `frametop.handpanel`, sort order 250. It sits 1.2 m ahead, centred 12 degrees above straight ahead, so the hands stay clear below it. It's 36 degrees wide, 4:3, 1024x768 pixels, dim and see-through. It's drawn on the CPU with stb_truetype into three shared DMA-BUFs SteamVR imports once, as ft-gazepanel does, and is drawn again only when something changes.
- **Socket.** Abstract unix datagram `@ft_handpanel` (`--socket NAME`). A sender with an address gets `ok ...` or `error ...`.
- **Options:** `--watch-stdin` (quit when stdin closes), `--socket NAME`, `--distance M`, `--no-vr`. `--no-vr` makes no SteamVR connection and prints each picture's text to stdout: for testing without a headset.

Commands (UTF-8; `|` starts a new line in text):

| Command | Effect |
|---|---|
| `show` / `hide` | The panel. A "show" makes it visible with its first picture. |
| `title <text>` | Big line at the top. |
| `step <text>` | Small line at the top right, e.g. `Section 3 of 11`. |
| `text <text>` | The instruction, large, wrapped to the panel's width, centred. |
| `note <text>` | An orange line under the instruction: a warning ("I can't see your left hand"). Empty clears it. |
| `countdown <0..1>` / `countdown off` | A thin bar along the bottom: the share of this prompt's time left. |
| `hands <left> <right>` | Two chips, "Left hand" and "Right hand", each `seen` (green), `lost` (orange) or `off` (hidden). |
| `bar <target 0..1> <current 0..1 or -1> [near label] [far label]` / `bar off` | The near/far bar for the push out and back: a horizontal track with a target marker and the hand's current position. |
| `paused on` / `paused off` | A "Paused" overlay over the picture. |
| `target <x> <y> <z> [show\|hold <0..1>\|done]` / `target off` | The touch target: a small sphere-like dot about 2 cm across, in its own overlay (`frametop.handpanel.target`). The point is in the head frame (metres, +x right, +y up, -z forward). The first `target` with a new point places it in the room with the current HMD pose, and it stays there. Later commands with the same point change only the state. `hold` draws a filling ring, `done` turns it green. Reply: `ok <room x> <room y> <room z>`. |
| `poses start <path>` / `poses stop` | Log poses to the path (appending, `poses.jsonl` format above) from a thread at 250 Hz, until stopped. |
| `devices` | Reply: `ok hmd <r> left <r or -> right <r or ->`, the current `ETrackingResult` values (`-` for no device in that role). |
| `head` | Reply: `ok <12 floats>`, the current HMD pose (standing universe). |
| `ping` | `ok shown` or `ok hidden`. |

It quits on SteamVR's quit event, as ft-gazepanel does.

## The script (`script.json`)

```json
{"version": 1,
 "sections": [
   {"id": "hand-size", "title": "Hand size", "requires": [], "intro": "text shown for 4 s before the first prompt",
    "prompts": [{"text": "...", "seconds": 8, "hands": "both", "pose": "flat", "distance": "near"}]},
   {"id": "objects", "title": "Things you hold", "requires": ["objects"], "for_each": "object",
    "prompts": [{"text": "Pick up the {object} and use it the way you normally would.", "seconds": 15, "hands": "both", "object": "{object}"}]},
   {"id": "touch", "title": "Touch the dot", "kind": "targets", "hold_s": 1.0, "timeout_s": 8,
    "targets": [[0.0, -0.15, -0.40], ...], "text": "Touch the dot with your index fingertip and hold still."},
   {"id": "controller-push", "title": "Depth with controllers", "requires": ["controllers"], "kind": "bar",
    "intro": "...", "heights": ["chest", "desk", "eye", "left", "right"], "reps": 5, "period_s": 6, "near_m": 0.2, "far_m": 0.6}
 ]}
```

- `requires`: `objects` (at least one object ticked), `controllers` (straps ticked). A section whose requirements aren't met is skipped and logged.
- `kind`:
  - `prompts` (the default): each prompt shows for its `seconds` with a countdown.
  - `targets`: each target shows until the live index tip is within 3 cm of it for `hold_s`, or `timeout_s` passes.
  - `bar`: the target marker sweeps near to far and back, `reps` times per height, at `period_s` per sweep. The current marker follows the live palm distance.
- Each section is one take, one recording. Prompts within it are marked in `prompts.jsonl`.

The sections, in this order (see the plan):
1. hand size
2. static poses (both hands, then each hand: open, fist, point, pinch, OK, thumbs up, spread, claw, counting 1-5, at near, mid and far, and centre, left, right, up, down)
3. wrist rotations
4. gestures (pinch taps, pinch-drag, grabs, hands crossing and overlapping, hands near the face, hands at screen distance)
5. desk work (typing, mouse)
6. objects (one prompt per ticked object, own objects included)
7. touch the dot
8. controller depth, straps (push out and back at 3 heights and to each side, wrist turns, open and close)
9. bridge (one controller on, the bare fingertip touches the marked point on it at near, mid and far, then swap; then a controller on the desk, touched from several angles)
10. bare repeat of section 8, controllers off
11. no hands (10 s)

Before section 8: "Put on both controllers and tighten the straps". Before section 10: "Take the controllers off and put them out of view".

### Feedback while recording

- **Hands seen:** from the hands file. A hand counts as seen if its flags match the side and the file is fresh (publish within 0.3 s). Prompts with `hands` set show the `hands` chips. If an asked-for hand is lost for more than 1.5 s, the note says "I can't see your left hand: bring it into view".
- **Controller tracking:** in sections 8 and 9, `devices` is polled once a second. A result other than 200 for more than 1 s says "The left controller lost tracking: turn your palm slightly toward you". Each such stretch goes into `prompts.jsonl` as `feedback` with `"controller": {...}`.
- **Lighting check at session start:** the mean of every mono camera's `mean` and `dark_mean` from the ring (`hands/tools/ring.py` layout; struct only, no numpy). It's compared with the person's earlier sessions. If the chosen lighting matches an earlier round's within 15%, the window says so before starting.

### Controls

- The window has Start, Pause/Resume, Skip section and Stop. Space pauses and Esc stops while the window has focus.
- The panel's text says what to do: "Pause: Space in the Hand recorder window".
- A pause stops the take's recording and starts it again on resume as the next part of the same take (`sets.bin` is appended to as a second recording file `sets-2.bin`, and so on). takes.py reads all parts in order.

## Review and export (`takes.py`, the window)

- **Review.** Each session lists its takes: title, duration, status, a thumbnail (the first set's `slam_left`). A viewer shows one frame set (all cameras side by side, 8-bit grey) at a time with a slider. It can mark a range and delete it. Deleted ranges go into `take.json`, and export leaves them out; the files keep everything until export. A whole take or session can be deleted (files removed, after a confirmation).
- **Export.** It writes `exports/<session>/`:
  - `manifest.json`: profile fields except `optional.notes` unless kept, session.json, takes, schema, tool version, the consent version.
  - `calibration.json`.
  - Per take: `prompts.jsonl`, `poses.jsonl`, `take.json`, and `sets.bin.zst` (sets in deleted ranges removed, then zstd -10 with 2 threads).
  - `SHA256SUMS`.

  Compression runs at nice 19. Before starting, the window warns if the headset is worn: `/sys/bus/iio/devices/iio:device2/in_proximity_raw` over 20, if readable. CPU work while in VR causes stutter.
- **Upload.** The Upload page shows `UPLOAD.md` with the export's path and size filled in, plus the copyable command.

## Licensing and consent (texts in `CONSENT.md`)

- The dataset is CC BY-NC 4.0.
- Contributors also grant the maintainer (DeeJanuz) a broad, non-exclusive license to their contribution.
- Contributors confirm they're 18 or older.
- The text explains what's recorded and that nothing uploads automatically, how review works, how to withdraw (by contributor id), and that a withdrawal is purged from the repo's history.
- The texts need a legal review before the dataset launches. Until then they carry a "draft" banner, and the Upload page says contributions aren't open yet.
- The dataset repo: `HF_DATASET` in `ft_handrec.py`, a placeholder (`DeeJanuz/frametop-hands`) until the maintainer decides.
