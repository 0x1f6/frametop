# Hand recorder: design (Phase 1 of the hands plan)

The hand recorder guides a person through recording their hands with the headset's cameras. It saves the recordings as files, lets them review and delete anything, then exports a package to contribute to the open hand dataset. Recordings never leave the headset unless the person uploads them.

The plan this belongs to is `~/Desktop/Projects/frame-hands/notes/hands-plan.md` (on the maintainer's Frame). In short, the dataset trains a small hand model for Frametop's hand cutouts.

## Parts

| Part | What it does |
|---|---|
| `hands/rec/panel/ft-handpanel.cpp` (C++, dev container) | The headset panel: a SteamVR overlay fixed to the head that shows the prompts. It also places the "touch the dot" target in the room and logs head and controller poses. Driven over `@ft_handpanel`. |
| `hands/rec/session.py` (Python, no Qt) | The session runner. It reads `script.json`, starts and stops the recordings, and drives the panel. It reads the live hands file for feedback and writes each take's files. It also runs from the command line (`--dry-run`) for testing. |
| `hands/rec/script.json` | The guided script: sections, prompts, timings. |
| `hands/rec/poses/` | The pose pictures, `poses.json` and its PNGs (below). |
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
- **A recording ft-hands** runs once per recording part: `ft-hands --record-only --record DIR --record-for SECONDS --record-hz 10 --status 0`. It runs as a plain child process of the session, ended with SIGTERM when the part ends. SIGTERM ends ft-hands' loop, and `Recorder` writes out its queue when it's destroyed. In step mode (below) a part is one step's countdown and hold, so a take has one part per step (about 40 in the hand poses); in auto mode a take is one part, plus one more after each pause. `--record-for` is only a safety net.
  - Why a process per part rather than one kept alive and paused: measured in the dev container with `ft-ringplay`'s ring (2026-10-02), `ft-hands --record-only` writes its first set 16-27 ms after it starts and ends 4-6 ms after SIGTERM, so a new part costs nothing the 3 s countdown doesn't cover. Every reader already takes parts in order (`takes.py`, `validate.py` through the export's single stream, the labeller's `fhl_io.py`, numbering `sets-10.bin` after `sets-9.bin`), ft-hands needs no new control, and nothing is written while a step waits. Before the hold starts the session checks that the part has written a set (`Recorder.has_data`, up to 3 s more), so the hold is recorded from its first frame.
- **ft-handpanel** runs as a child process with `--watch-stdin`. It shows the panel and logs poses during each take.

Test hooks:
- `--ring PATH` goes to both ft-hands (`ft-ringplay` publishes a recording there, so the whole flow runs without the headset).
- `--no-start` uses only what's already running.
- `--dry-run` runs no processes and only prints the panel commands, with timing sped up by `--speed X`.
- `--next-after S` presses Next by itself after S seconds of waiting (real time), so a step-mode session runs unattended. Lines on stdin steer it too: `n` or an empty line is Next, `p` pause or resume, `r` redo, `s` skip the section, `q` stop.
- `--auto` runs the timed flow; `--poses DIR` takes the pose pictures from DIR; `--plan` prints the sections, their steps and length.

## Files

```
~/.local/share/frametop/hands/contrib/
  profile.json                  consent and contributor id (below)
  sessions/<YYYYMMDD-HHMMSS>/   (a second session started in the same second gets -2, and so on)
    session.json                the session: checklist answers, lighting, versions, mode, takes
    calibration.json            /persist/xrservice.json with identifying fields removed (below)
    device.json                 the rig's pose in the CAD frame from /persist/device_config.json (below)
    takes/<NN>-<section>/
      sets.bin                  ft-hands' recording (FHSET01, hands/track/record.h), 10 sets/s: the first part
      sets-2.bin, sets-3.bin, ...  the next parts (step mode: one per step; any mode: after a pause)
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
 "mode": "step|auto", "takes": ["01-hand-size", "..."]}
```

A session started before step mode existed has no `mode`: it ran as `auto`.

### calibration.json

This is a copy of `/persist/xrservice.json` (`/run/host/persist/` in the container). Keep the cameras' intrinsics and extrinsics, and drop anything that identifies the unit: keys containing `serial`, `sn`, `uuid`, `mac` or `id`, or values that look like serial numbers. List what was removed in `session.json` (`"calibration_removed": [...]`), so a reviewer can check it.

### device.json

The head frame needs the rig's pose in the CAD frame, from `/persist/device_config.json`. Only two of its keys are kept, `cv.cad_from_cal` (Cam0 in the CAD frame) and `head` (the head in CAD), in the shape the labeller in frame-hands `train/label` reads, as its `cut.py` writes it:

```json
{"cv": {"cad_from_cal": {"method": "FrontAndUpperCamPositions", "plus_x": [x, y, z], "plus_z": [x, y, z], "position": [x, y, z]}},
 "head": {"plus_x": [x, y, z], "plus_z": [x, y, z], "position": [x, y, z]}}
```

The rest of that file identifies the unit (serial number, display EDID) and is never copied. The two kept keys go through `strip_calibration` as well, and anything it removes is listed in `calibration_removed` as `device.json:<path>`. Sessions recorded before device.json existed have none: they still validate, with a warning, and the labeller falls back to another unit's pose.

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
{"t": 123, "event": "ready", "id": "static-poses/fist/left/near", "seconds": 3}
{"t": 123, "event": "wait"}
{"t": 123, "event": "redo", "id": "static-poses/fist/left/near", "from": 123, "to": 123}
{"t": 123, "event": "end", "status": "complete|stopped|skipped"}
```

`feedback` is written about twice a second while recording. It's the live tracker's view, kept for later checks; it's not a label.

A prompt holds from its `prompt` until the next `prompt`, `ready`, `wait` or `end`. A step in step mode reads:

```
ready      Next pressed: recording part N starts, the 3-2-1 countdown runs (recorded, no label)
prompt     the hold: its labels start here
(bar, target, feedback, pause/resume during the hold)
wait       the hold is over: no labels from here; part N stops
```

The touch-the-dot targets after the first follow straight on: no `ready` or `wait` between them. `redo` marks a try done again (R): `from` is that step's `ready` (or its `prompt` if it had none), `to` its end. Its prompt is skipped; the sets stay. In auto mode there's no `ready` or `wait`, and the intro is a recorded prompt `<section>/intro`. Readers that knew only `prompt` and `end` keep working, but they'd give the countdown to the step before: `hub_review.py` (frame-hands `train/hub`) shows it as "(countdown)" and redone prompts as "(redone)"; `FORMAT.md` in the dataset repo has the rules.

### poses.jsonl

ft-handpanel writes one line per sample, 250 a second, from `GetDeviceToAbsoluteTrackingPose(TrackingUniverseStanding, 0)`:

```json
{"t": 123, "hmd": {"m": [12 floats, row-major 3x4], "r": 200, "ok": true},
 "left": {"m": [...], "r": 200, "ok": true}, "right": null}
```

`r` is `ETrackingResult` (200 = Running_OK, 201 = Running_OutOfRange, and so on). `left` and `right` are the devices holding those controller roles, or null. No device serials are logged.

## The panel (`ft-handpanel`)

- **Placement.** A SteamVR overlay fixed to the head, like `gaze/panel/ft-gazepanel.cpp`: key `frametop.handpanel`, sort order 250. It sits 1.2 m ahead, centred 12 degrees above straight ahead, so the hands stay clear below it. It's 36 degrees wide, 4:3, 1024x768 pixels, dim and see-through. It's drawn on the CPU with stb_truetype (and stb_image for the pose pictures, PNG only, the same pinned stb commit) into three shared DMA-BUFs SteamVR imports once, as ft-gazepanel does, and is drawn again only when something changes.
- **Layout.** The title and step on top (a red "Rec" by the step while recording), a rule. With a pose picture or a diagram, a 14-degree column on the left holds the picture (or the flipped copy and the picture side by side) and the where-to diagram under it; the text takes the right. The text column holds the instruction, the orange note, the big countdown ("3", "2", "1", then "Hold" or "Go") and the cyan action line ("Ready? Press Space or click Next"), centred together. At the bottom: the near/far bar, the hand chips, the time-left bar and the key hints.
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
| `image <path> [mirror\|both]` / `image off` | The pose picture, a PNG (below), in the left column. `mirror` flips it (a left hand); `both` draws a flipped copy on its left. A file that can't be read: `error ...` and no picture. |
| `where <position\|-> <distance\|->` / `where off` | The where-to diagram under the picture: a 3x3 front view with the asked cell lit (`centre`, `left`, `right`, `up`, `down`, and the push sections' `chest`, `desk`, `eye`), and a side view of the head and three marks for `near` ("Close"), `mid` ("Halfway out") and `far` ("Arm out"). `-` leaves that half out. |
| `action <text>` | The cyan line under the instruction (empty clears it). |
| `big <text>` | Large cyan text under the instruction: the countdown (empty clears it). |
| `keys <text>` | The faint key hints along the bottom. |
| `rec on` / `rec off` | The red "Rec" by the step line. |
| `target <x> <y> <z> [show\|hold <0..1>\|done]` / `target off` | The touch target: a small sphere-like dot about 2 cm across, in its own overlay (`frametop.handpanel.target`). The point is in the head frame (metres, +x right, +y up, -z forward). The first `target` with a new point places it in the room with the current HMD pose, and it stays there. Later commands with the same point change only the state. `hold` draws a filling ring, `done` turns it green. Reply: `ok <room x> <room y> <room z>`. |
| `poses start <path>` / `poses stop` | Log poses to the path (appending, `poses.jsonl` format above) from a thread at 250 Hz, until stopped. |
| `devices` | Reply: `ok hmd <r> left <r or -> right <r or ->`, the current `ETrackingResult` values (`-` for no device in that role). |
| `head` | Reply: `ok <12 floats>`, the current HMD pose (standing universe). |
| `ping` | `ok shown` or `ok hidden`. |

It quits on SteamVR's quit event, as ft-gazepanel does. `--no-vr --dump DIR` writes each picture to `DIR/panel.pam`, to check the layout without a headset.

## The pose pictures (`poses/`)

`poses/poses.json` maps the script's `pose` ids to pictures: `{"<pose>": {"file": "<name>.png", "two_hands": false, "caption": "..."}}`. Each PNG is RGBA, square (512x512), drawn as the wearer sees it: a right hand, unless `two_hands` (then it shows both). How a prompt shows it (`session.pose_view`):

| Prompt's `hands` | Picture |
|---|---|
| `right` (and `any`, `none`, empty) | as drawn |
| `left` | flipped left to right (`image ... mirror`) |
| `both`, not `two_hands` | a flipped copy on the left, the picture on the right (`image ... both`) |
| anything, `two_hands` | as drawn |

A pose with no entry, or whose file is missing, shows no picture: the text alone. The session reads `poses.json` when it starts. The window shows the same picture (QML `Image.mirror`), its caption, and the same diagram.

## The script (`script.json`)

```json
{"version": 1,
 "sections": [
   {"id": "hand-size", "title": "Hand size", "requires": [], "intro": "text shown before the first prompt: 4 s, or until Next", "go": "Hold",
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
- `go`: the word the countdown ends on, "Go" unless set ("Hold" for the still poses).
- `kind`:
  - `prompts` (the default): each prompt shows for its `seconds` with a countdown.
  - `targets`: each target shows until the live index tip is within 3 cm of it for `hold_s`, or `timeout_s` passes.
  - `bar`: the target marker sweeps near to far and back, `reps` times per height, at `period_s` per sweep. The current marker follows the live palm distance.
- Each section is one take. Prompts within it are marked in `prompts.jsonl`.

### Step mode (the default) and auto mode

The first in-headset session (2026-10-02) went too fast: each prompt advanced after 4-8 s, before there was time to read it and find the hand shape. So by default every step waits:

1. **Ready.** The panel shows the step: section title, "step N of M", the instruction, the pose picture, the where-to diagram, and "Ready? Press Space or click Next". The hand chips show which hands are seen, with no warnings yet. It waits as long as it takes, and nothing records.
2. **Countdown.** Next starts a new recording part and a "ready" event, and the panel counts 3, 2, 1 (big), recorded so the hold is captured from its first frame. In the push sections the bar sits at near meanwhile.
3. **Hold.** The `prompt` event, the section's word ("Hold" or "Go") and the time-left bar for the prompt's seconds (or the bar's sweeps, or the targets). Then a `wait` event and the part stops.

Steps that wait: each prompt, each bar height, and the first touch-the-dot target (the others follow straight on, as each waits for the touch anyway). Before a section, one screen shows the section's intro (with its `before` text, such as putting on the controllers) and waits for Next too; the welcome screen as well. The take starts with the section's first countdown, so a section skipped at its intro leaves no take. A pause in a hold works as before (the part stops; resume starts the next one).

Auto mode ("Advance by itself" on the checklist page, `session.py --auto`) is the old timed flow: the welcome, the between and before screens, the intro (recorded) and each prompt for its seconds, one recording per take. R still works there: it restarts the step running.

Holds are 5 s for still poses (4 s counting fingers), 8-10 s for movements. The core session (no objects, no controllers) records about 11 min in 62 steps; with 5 s of reading a step that's about 16 min. Everything ticked: about 16.5 min recorded in 86 steps. The window and `session.py --plan` give these (`plan_summary`); in step mode they leave the reading time out and say so.

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

- The window has Start, a big Next (while a step waits), Pause/Resume, Redo step, Skip section and Stop. While it has focus: Space is Next, P pauses or resumes, R redoes, S skips the section, Esc stops. The panel's bottom line and the window list them. The window also shows the step's picture, diagram, countdown and "Hold".
- **R (redo).** During a step's countdown or hold: that step starts again from its ready screen. At a step's ready screen: the step before it (in this section) goes again. Either way a `redo` event marks the range of the try being redone, so its labels are skipped; the sets stay, to delete in review if wanted.
- A pause stops the take's recording and starts it again on resume as the next part of the same take (`sets-2.bin`, and so on). takes.py reads all parts in order. A pause while a step waits only shows "Paused"; a Next pressed while paused doesn't count.

## Review and export (`takes.py`, the window)

- **Review.** Each session lists its takes: title, duration, status, a thumbnail (the first set's `slam_left`). A viewer shows one frame set (all cameras side by side, 8-bit grey) at a time with a slider. It can mark a range and delete it. Deleted ranges go into `take.json`, and export leaves them out; the files keep everything until export. A whole take or session can be deleted (files removed, after a confirmation).
- **Export.** It writes `exports/<session>/`:
  - `manifest.json`: profile fields except `optional.notes` unless kept, session.json (without its `uploads` records), takes, schema, tool version, the consent version.
  - `calibration.json` and `device.json`, when the session has them.
  - Per take: `prompts.jsonl`, `poses.jsonl`, `take.json`, and `sets.bin.zst` (sets in deleted ranges removed, then zstd -10 with 2 threads).
  - `SHA256SUMS`.

  Compression runs at nice 19. Before starting, the window warns if the headset is worn, judged the way `frame-job` does: `vrcompositor` runs and a `/sys/class/backlight/*/brightness` reads over 0 (SteamVR turns the panel off 5 s after the headset comes off). CPU work while in VR causes stutter. The proximity sensor is no use here: it read 9-43 with the headset sitting unworn.
- **Upload.** The Upload page uploads an export from the window. It also keeps the manual way as a fallback: `UPLOAD.md` with the export's path and size filled in, plus the copyable command (`validate.py`, then `hf upload ... --create-pr`).
  - **`hands/rec/validate.py`** (standard library) checks an export. The window runs it before an upload, and the maintainer runs it on each submission (`validate.py DIR [--json]`, exit status 1 on errors). It checks:
    - `SHA256SUMS`: every file listed and matching.
    - An allow-list: `manifest.json`, `calibration.json`, `device.json` and `SHA256SUMS` at the top, and `prompts.jsonl`, `poses.jsonl`, `take.json` and `sets.bin.zst` in `takes/<NN>-<section>/`. Anything else is an error, and so is a symlink.
    - The manifest's schema, keys and types, and that it matches the files.
    - The consent version is present and the contributor confirmed being an adult. The contributor id is a uuid4.
    - `calibration.json` has nothing that `session.py`'s `strip_calibration` would still remove.
    - `device.json` holds only `cv.cad_from_cal` and `head`, each `plus_x`, `plus_z` and `position` as 3 numbers (plus `cad_from_cal`'s `method`). Without it: a warning.
    - Each `sets.bin.zst` decompresses to its end, so a truncated one fails, and every set's FHSET01 header is sane: camera names, sizes, record length. Set counts and raw bytes match the manifest. Pixels aren't decoded.
    - Every jsonl line parses.
    - The total size: a warning over 15 GB, an error over 40 GB.

    Warnings cover notes kept in the export, a home folder path in the manifest, and missing `poses.jsonl` files.

    It runs on Linux and on Windows (the maintainer's PC) with Python 3.12 or later. It decompresses with Python 3.14's `compression.zstd`, else the `zstandard` package, else the `zstd` program, and handles several zstd frames in a row.
  - **`hands/rec/hub.py`** does the upload. It runs as a child process of the window (Cancel ends it), or from the command line (`hub.py [--base DIR] whoami | upload SESSION [--dry-run] [--again] [--json]`). It uses `huggingface_hub` (`python3-huggingface-hub` in the dev container) with the token `hf auth login` saved, and never handles a token itself. An upload goes through these steps:
    1. Validate, and stop on errors.
    2. Stop if the same export was uploaded before (same `SHA256SUMS`), unless asked again.
    3. Stop while the texts are drafts, unless `FT_HANDREC_ALLOW_UPLOAD=1`.
    4. `whoami`: a read-only token is refused.
    5. `auth_check` on the dataset: a gated dataset whose terms aren't accepted gives "accept the dataset's terms first", with the link.
    6. `upload_folder(repo_id=HF_DATASET, repo_type="dataset", folder_path=EXPORT, path_in_repo="contributions/<contributor>/<session>", create_pr=True, commit_message=..., commit_description=...)`. The description summarizes the manifest: takes, minutes, sets, lighting, objects, controllers, the consent and tool versions, the size, and validate's warnings.
    7. Add `{"repo", "pr_url", "uploaded", "export_sha"}` to `uploads` in `session.json`. `export_sha` is the SHA256 of `SHA256SUMS`. The file keeps its modification time, so the export doesn't count as out of date.

    Errors get a plain explanation: not logged in, a token Hugging Face rejects (401), a token that can't open a pull request (403), terms not accepted, dataset not found, network errors. `--dry-run` does everything except the network calls and the record, and lists what it would upload.
  - **The page** shows the login (`whoami`, with "Check again"). If nobody is logged in, it explains how to run `distrobox enter dev -- hf auth login` in Konsole with a write token: the token goes only into that terminal. The page then has Upload and Cancel, the phase with a progress bar (a share while the export is checked, a sweep while it's sent, as `huggingface_hub` reports no progress), and the pull request's link when it's done. If this export was uploaded before, the page says so, and uploading it again asks first. A stale export can't be uploaded.
  - **While the texts are drafts**, Upload stays off unless `FT_HANDREC_ALLOW_UPLOAD=1`, so the maintainer can rehearse against a private test repo. `FT_HANDREC_DATASET` overrides `HF_DATASET`. `ft-handrec --hub-dry-run` makes Upload a dry run: no network, so it isn't held back by the drafts.
  - **Rehearsal: `hands/rec/rehearse.sh [--repo ID]`** runs it all without the headset, in the dev container, in one `frame-job --local` scope when frame-job is installed. `ft-ringplay` plays 30 s of a recording into a ring in `/run/user/UID`. A tracking ft-hands that's already running is used, or one is started on that ring. `ft-handpanel --no-vr` stands in for the panel. `session.py --no-start --next-after 0.3` records a two-section test script in step mode, three parts of 6 s (countdown and hold), about 360 MB once exported. Then `takes.py` exports, `validate.py` checks, and `hub.py` uploads: a dry run by default, or for real to `--repo ID` with `FT_HANDREC_ALLOW_UPLOAD=1`. It prints a summary, deletes its temporary folders (camera images of a room) and stops everything it started, Ctrl+C included. The `--no-vr` panel logs no poses, so `poses.jsonl` is missing there (a warning).

## Licensing and consent (texts in `CONSENT.md`)

- The dataset is CC BY-NC 4.0.
- Contributors also grant the maintainer (DeeJanuz) a broad, non-exclusive license to their contribution.
- Contributors confirm they're 18 or older.
- The text explains what's recorded and that nothing uploads automatically, how review works, how to withdraw (by contributor id), and that a withdrawal is purged from the repo's history.
- The texts need a legal review before the dataset launches. Until then they carry a "draft" banner, and the Upload page says contributions aren't open yet.
- The dataset repo: `HF_DATASET` in `hub.py`, `DeeJanuz/frametop-hands` (private until launch).
