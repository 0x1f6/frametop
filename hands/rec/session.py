#!/usr/bin/env python3
"""The hand recorder's session runner (DESIGN.md: "Processes during a session", "Files", "The
script", "Feedback while recording", "Controls").

It reads script.json, starts what the session needs (ft-camd and a tracking ft-hands, only if
they aren't running), and runs the sections in order. Each section is one take: a recording
ft-hands child process (--record-only, 10 sets a second) plus prompts.jsonl, take.json and the
panel's poses.jsonl. The headset panel (ft-handpanel) shows the prompts; the live hands file
gives feedback ("I can't see your left hand").

Plain Python, standard library only: ft_handrec.py imports it, and it runs from the command line
for testing (in the dev container):

  python3 hands/rec/session.py --dry-run --speed 20            # no processes: prints the panel commands
  python3 hands/rec/session.py --ring /tmp/ring --base /tmp/hr  # ft-ringplay's frames, no headset needed

All _ns times are CLOCK_MONOTONIC nanoseconds.
"""
import argparse
import glob
import json
import math
import mmap
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
HANDS = os.path.join(REPO, "hands")
FT_HANDS = os.path.join(HANDS, "build", "ft-hands")
FT_CAMD = os.path.join(HANDS, "build", "ft-camd")
PANEL_BIN = os.path.join(HERE, "build", "ft-handpanel")
SCRIPT_PATH = os.path.join(HERE, "script.json")
BASE_DIR = os.path.expanduser("~/.local/share/frametop/hands/contrib")
PANEL_SOCKET = "ft_handpanel"
CAMD_UNIT = "frametop-handrec-camd.service"
HANDS_UNIT = "frametop-handrec-hands.service"

RECORD_HZ = 10
TICK_S = 0.05            # the session loop's step (real time)
FRESH_S = 0.3            # the hands file counts as live if published this recently
LOST_S = 1.5             # an asked-for hand lost this long gets a note
CONTROLLER_LOST_S = 1.0  # a controller off 200 (Running_OK) this long gets a note
TOUCH_M = 0.03           # touch the dot: the index tip within this of the dot
MIN_FREE = 1.5e9         # stop the session before the disk fills
RESUME_HINT = "Paused. Resume: Space in the Hand recorder window"


def mono_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def run_dir():
    return "/run/user/%d/frametop-hands" % os.getuid()


def in_container():
    return os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv")


def host_command(*cmd):
    """argv to run a command on the SteamOS host from the dev container (as in
    display-settings: distrobox-host-exec needs the user's real session bus)."""
    exe = shutil.which("distrobox-host-exec")
    if not in_container() or not exe:
        return list(cmd)
    return ["env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus" % os.getuid(), exe] + list(cmd)


def host_path(path):
    """A host file, from the container (/run/host) or the host itself."""
    for p in ("/run/host" + path, path):
        if os.path.exists(p):
            return p
    return None


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def clean_text(s):
    """Text for a panel command: one line; "|" is the panel's line break, so callers use it."""
    return " ".join(str(s).replace("\r", " ").replace("\n", " ").split())


# ------------------------------------------------------------------------------------------
# The camera ring (camd/fhring.h; tools/ring.py's layout, struct only)

RING_HDR = struct.Struct("<8sIIIIQqQ16x")              # 64 bytes
RING_CAM = struct.Struct("<32s32siIIIIIQQQQQIf24x")      # 160 bytes
RING_SLOT = struct.Struct("<QQQQQIf16x")                 # 64 bytes, the image follows
FH_CAM_DARK, FH_CAM_COLOR = 1, 2


class Ring:
    def __init__(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            self.map = mmap.mmap(fd, 0, mmap.MAP_SHARED, mmap.PROT_READ)
        finally:
            os.close(fd)
        magic, version, _, ncams, _, _, self.writer_pid, _ = RING_HDR.unpack_from(self.map, 0)
        if magic != b"FHRING01" or version != 1:
            self.map.close()
            raise ValueError("%s isn't a camera ring" % path)
        self.cams = []
        for i in range(min(ncams, 8)):
            f = RING_CAM.unpack_from(self.map, RING_HDR.size + i * RING_CAM.size)
            self.cams.append({
                "index": i, "sensor": f[0].split(b"\0", 1)[0].decode(errors="replace"),
                "name": f[1].split(b"\0", 1)[0].decode(errors="replace"), "node": f[2],
                "width": f[4], "height": f[5], "stride": f[6], "nslots": f[7],
                "slot_offset": f[8], "slot_bytes": f[9], "flags": f[13]})

    def close(self):
        self.map.close()

    def alive(self, max_age=1.0):
        hb = struct.unpack_from("<Q", self.map, 40)[0]
        return hb != 0 and (mono_ns() - hb) / 1e9 < max_age

    def _cam_field(self, cam, fmt, off):
        return struct.unpack_from(fmt, self.map, RING_HDR.size + cam["index"] * RING_CAM.size + off)[0]

    def latest(self, cam):
        return self._cam_field(cam, "<Q", 104)

    def dark_mean(self, cam):
        return self._cam_field(cam, "<f", 132)

    def mono(self):
        """The mono tracking cameras (no dark twins, no colour cameras, also by name for
        ft-ringplay's rings, which carry no flags)."""
        return [c for c in self.cams if not c["flags"] & (FH_CAM_DARK | FH_CAM_COLOR)
                and not c["name"].endswith("_dk") and not c["name"].startswith("color")]

    def frame_mean(self, cam):
        """The newest frame's mean luma: ft-camd's from the slot header, else (ft-ringplay
        leaves it 0) measured on a sparse grid. None if there's no complete frame."""
        n = self.latest(cam)
        if not n:
            return None
        off = cam["slot_offset"] + (n % cam["nslots"]) * cam["slot_bytes"]
        seq, _, _, _, _, _, mean = RING_SLOT.unpack_from(self.map, off)
        if seq != 2 * n + 2:
            return None
        if mean <= 0:
            img, total, count = off + RING_SLOT.size, 0, 0
            for y in range(0, cam["height"], 16):
                row = self.map[img + y * cam["stride"]: img + y * cam["stride"] + cam["width"]: 16]
                total += sum(row)
                count += len(row)
            mean = total / count if count else 0.0
        if struct.unpack_from("<Q", self.map, off)[0] != seq:
            return None
        return float(mean)


def ring_lighting(ring_path=None, samples=5, interval=0.2):
    """Each mono camera's brightness now: {"<cam>": {"mean": float, "dark_mean": float}}, the
    newest frames' mean luma and ft-camd's near-black frame mean (the room's IR light), averaged
    over about a second. None if no camera ring is running."""
    path = ring_path or os.path.join(run_dir(), "cam-ring")
    try:
        ring = Ring(path)
    except (OSError, ValueError):
        return None
    try:
        if not ring.alive():
            return None
        sums = {}
        for k in range(samples):
            for cam in ring.mono():
                m = ring.frame_mean(cam)
                s = sums.setdefault(cam["name"], [0.0, 0, 0.0, 0])
                if m is not None:
                    s[0] += m
                    s[1] += 1
                s[2] += ring.dark_mean(cam)
                s[3] += 1
            if k + 1 < samples:
                time.sleep(interval)
        out = {name: {"mean": round(s[0] / s[1], 2) if s[1] else 0.0,
                      "dark_mean": round(s[2] / s[3], 2) if s[3] else 0.0} for name, s in sums.items()}
        return out or None
    finally:
        ring.close()


def _light_levels(ring):
    vals = [v for v in (ring or {}).values() if isinstance(v, dict)]
    if not vals:
        return None
    return (sum(float(v.get("mean", 0)) for v in vals) / len(vals),
            sum(float(v.get("dark_mean", 0)) for v in vals) / len(vals))


def _close(a, b, share=0.15, floor=1.0):
    """Within 15% of the larger; the floor keeps near-black levels (a dim room's dark mean is a
    few levels) from looking different over sensor noise."""
    return abs(a - b) <= max(share * max(abs(a), abs(b)), floor)


def similar_lighting(base_dir, lighting):
    """An earlier session whose cameras saw about the same light (the mean of every mono
    camera's mean and dark_mean, each within 15%): (session_id, chosen), the latest such, or
    None. lighting is ring_lighting()'s dict, or session.json's {"chosen", "ring"}."""
    if not lighting:
        return None
    ring = lighting.get("ring") if isinstance(lighting.get("ring"), dict) else lighting
    now = _light_levels(ring)
    if not now:
        return None
    for path in sorted(glob.glob(os.path.join(base_dir, "sessions", "*", "session.json")), reverse=True):
        try:
            with open(path) as f:
                light = json.load(f).get("lighting") or {}
        except (OSError, ValueError):
            continue
        then = _light_levels(light.get("ring"))
        if then and _close(now[0], then[0]) and _close(now[1], then[1]):
            return os.path.basename(os.path.dirname(path)), light.get("chosen", "")
    return None


# ------------------------------------------------------------------------------------------
# The factory calibration, without what identifies the unit

ID_TOKENS = {"serial", "sn", "uuid", "guid", "mac", "id", "ids", "identifier"}


def _key_identifies(key):
    k = str(key)
    if re.search(r"serial|uuid", k, re.I):
        return True
    tokens = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", k)
    return any(t.lower() in ID_TOKENS for t in tokens)


def _value_identifies(value):
    """A string holding something like a serial number: a run of 8 or more letters and digits
    with at least 4 digits and a letter (sensor names like og01a1b are shorter)."""
    if not isinstance(value, str):
        return False
    for run in re.findall(r"[A-Za-z0-9]+", value):
        if len(run) >= 8 and sum(c.isdigit() for c in run) >= 4 and any(c.isalpha() for c in run):
            return True
    return False


def strip_calibration(xrservice_json_dict):
    """/persist/xrservice.json without what identifies the unit: keys naming a serial, sn,
    uuid, mac or id, and string values that look like serial numbers. Returns (cleaned dict,
    removed key paths such as "cameras[0].serial")."""
    removed = []

    def walk(o, path):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                p = "%s.%s" % (path, k) if path else str(k)
                if _key_identifies(k) or _value_identifies(v):
                    removed.append(p)
                    continue
                out[k] = walk(v, p)
            return out
        if isinstance(o, list):
            out = []
            for i, v in enumerate(o):
                p = "%s[%d]" % (path, i)
                if _value_identifies(v):
                    removed.append(p)
                    out.append(None)   # keep the other items' positions
                    continue
                out.append(walk(v, p))
            return out
        return o

    return walk(xrservice_json_dict, ""), removed


# ------------------------------------------------------------------------------------------
# The live hands file (include/fh_hands.h)

HANDS_HDR = struct.Struct("<8sIIQQQII16x")    # 64 bytes
HAND = struct.Struct("<IIff63fI")             # 272 bytes
FH_HAND_RIGHT = 1
PALM = (0, 5, 9, 13, 17)                      # wrist and knuckles
INDEX_TIP = 8


class HandsFile:
    """Reads ft-hands' hands file under its sequence lock. read() gives None while no tracker
    publishes (no file, or nothing for FRESH_S), else {"left": hand|None, "right": hand|None}
    with hand = {"palm": [x,y,z], "palm_m": float, "tip": [x,y,z]} in the head frame."""

    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return None
        try:
            size = HANDS_HDR.size + 2 * HAND.size
            for _ in range(4):
                data = os.pread(fd, size, 0)
                if len(data) < size:
                    return None
                magic, version, _, seq, _, publish_ns, nhands, _ = HANDS_HDR.unpack_from(data, 0)
                if magic != b"FHHANDS1" or version != 1:
                    return None
                if seq % 2 or os.pread(fd, 8, 16) != data[16:24]:
                    continue
                if (mono_ns() - publish_ns) / 1e9 > FRESH_S:
                    return None
                out = {"left": None, "right": None}
                for k in range(min(nhands, 2)):
                    f = HAND.unpack_from(data, HANDS_HDR.size + k * HAND.size)
                    side = "right" if f[1] & FH_HAND_RIGHT else "left"
                    if out[side]:
                        continue
                    pts = [f[4 + 3 * i: 7 + 3 * i] for i in range(21)]
                    palm = [sum(pts[i][j] for i in PALM) / len(PALM) for j in range(3)]
                    out[side] = {"palm": palm, "palm_m": math.sqrt(sum(v * v for v in palm)),
                                 "tip": list(pts[INDEX_TIP])}
                return out
            return None
        finally:
            os.close(fd)


# ------------------------------------------------------------------------------------------
# The panel (ft-handpanel, @ft_handpanel)

class Panel:
    """Commands to ft-handpanel over its datagram socket. Commands that need an answer wait
    for it; the rest don't, and their replies are read (and errors logged) later. With
    dry=True nothing is sent: commands are printed and replies made up."""

    def __init__(self, name=PANEL_SOCKET, dry=False, log=None, out=None):
        self.name, self.dry, self.log, self.out = name, dry, log or (lambda s: None), out or print
        self.sock = None
        self.sent = {}
        self.pending = 0   # commands sent and not answered yet
        if not dry:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
            self.sock.bind("\0ft_handrec.%d.%d" % (os.getpid(), id(self) & 0xffff))
            self.sock.setblocking(False)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _drain(self):
        while self.sock:
            try:
                r = self.sock.recv(4096).decode(errors="replace")
            except (BlockingIOError, OSError):
                return
            self.pending = max(0, self.pending - 1)
            if r.startswith("error"):
                self.log("panel: %s" % r)

    def cmd(self, text, reply=False, timeout=0.5):
        """Send a command; with reply=True, its answer ("ok ..." or "error ..."), or None.
        The panel answers every command in order, so the answer is the one after those of
        the commands still unanswered."""
        if self.dry:
            self.out("panel: %s" % text)
            if not reply:
                return None
            if text.startswith("devices"):
                return "ok hmd 200 left 200 right 200"
            return "ok shown" if text == "ping" else "ok"
        if not self.sock:
            return None
        self._drain()
        try:
            self.sock.sendto(text.encode(), "\0" + self.name)
        except OSError:
            return None
        self.pending += 1
        if not reply:
            return None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self.sock.settimeout(max(0.01, end - time.monotonic()))
                r = self.sock.recv(4096).decode(errors="replace")
            except OSError:
                break
            finally:
                self.sock.setblocking(False)
            self.pending = max(0, self.pending - 1)
            if self.pending == 0:
                return r
            if r.startswith("error"):
                self.log("panel: %s" % r)
        self.pending = 0   # a lost answer: start counting afresh
        return None

    def set(self, key, text):
        """Send a command only if it changes what that part of the panel shows."""
        if self.sent.get(key) == text:
            return
        self.sent[key] = text
        self.cmd(text)


# ------------------------------------------------------------------------------------------
# Processes

def find_processes(name):
    """[(pid, argv)] of running processes called name (the container shares the host's PIDs)."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                if f.read().strip() != name:
                    continue
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        out.append((int(pid), argv))
    return out


def tracker_running():
    """A tracking ft-hands (not a --record-only recorder)."""
    return any("--record-only" not in argv for _, argv in find_processes("ft-hands"))


class Recorder:
    """One part of a take's recording: ft-hands --record-only as a child process. Part 1
    writes TAKE/sets.bin; part N (after a pause) records into TAKE/.part-N and is moved to
    TAKE/sets-N.bin when it ends (ft-hands never overwrites, and always writes DIR/sets.bin)."""

    def __init__(self, take_dir, part, seconds, ring, log_file):
        self.take_dir, self.part = take_dir, part
        self.dir = take_dir if part == 1 else os.path.join(take_dir, ".part-%d" % part)
        argv = [FT_HANDS, "--record-only", "--record", self.dir, "--record-for", "%.0f" % max(seconds, 5),
                "--record-hz", str(RECORD_HZ), "--status", "0"]
        if ring:
            argv += ["--ring", ring]
        if not in_container():
            argv = [os.path.expanduser("~/.local/bin/distrobox"), "enter", "dev", "--"] + argv
        self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file)
        self.started_ns = mono_ns()

    def exited(self):
        return self.proc.poll() is not None

    def stop(self):
        """End it (SIGTERM: ft-hands writes out its queue) and put the part in place."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.part > 1:
            src = os.path.join(self.dir, "sets.bin")
            if os.path.exists(src):
                os.replace(src, os.path.join(self.take_dir, "sets-%d.bin" % self.part))
            try:
                os.rmdir(self.dir)
            except OSError:
                pass
        return self.proc.returncode


# ------------------------------------------------------------------------------------------
# The script

HAND_VALUES = ("left", "right", "both", "none", "any", "")
PROMPT_KEYS = ("text", "seconds", "hands", "pose", "distance", "position", "object", "controller")


def _when_ok(cond, ctx):
    """A prompt's or block's "when": "controllers", "objects:keyboard", or either negated with "!"."""
    if not cond:
        return True
    neg = cond.startswith("!")
    c = cond.lstrip("!")
    value = c[len("objects:"):] in ctx["objects"] if c.startswith("objects:") else bool(ctx.get(c))
    return value != neg


def _prompt(section, raw, ctx, subst=None):
    p = dict(section.get("defaults") or {})
    p.update(raw)
    p.pop("when", None)
    for k, v in (subst or {}).items():
        for key in ("text", "object"):
            if isinstance(p.get(key), str):
                p[key] = p[key].replace("{%s}" % k, v)
    if subst and "object_key" in subst:
        p["object"] = subst["object_key"]
    for key in PROMPT_KEYS:
        p.setdefault(key, False if key == "controller" else 0 if key == "seconds" else "")
    p["controllers"] = list(p.get("controllers") or [])
    if p["hands"] not in HAND_VALUES:
        raise ValueError("%s: hands %r" % (section["id"], p["hands"]))
    return p


def _set_ids(section, prompts):
    seen = {}
    for p in prompts:
        if not p.get("id"):
            parts = [section["id"]] + [re.sub(r"[^a-z0-9]+", "-", str(p[k]).lower()).strip("-")
                                       for k in ("pose", "hands", "distance", "position", "object") if p[k]]
            p["id"] = "/".join(parts) if len(parts) > 1 else "%s/%d" % (section["id"], len(seen) + 1)
        n = seen.get(p["id"], 0) + 1
        seen[p["id"]] = n
        if n > 1:
            p["id"] = "%s#%d" % (p["id"], n)


def load_script(path):
    with open(path) as f:
        script = json.load(f)
    if script.get("version") != 1 or not isinstance(script.get("sections"), list):
        raise ValueError("%s: not a version 1 script" % path)
    return script


def build_plan(script, checklist):
    """The sections this session runs, prompts expanded for the checklist, and the ones
    skipped: (plan, skipped) with skipped = [{"section", "reason"}]."""
    objects = [o for o in (checklist.get("objects") or []) if o]
    own = [o for o in (checklist.get("own_objects") or []) if str(o).strip()]
    ctx = {"objects": set(objects) | set(own), "controllers": checklist.get("controllers") == "straps"}
    names = script.get("objects") or {}
    plan, skipped = [], []
    for raw in script["sections"]:
        sid = raw["id"]
        missing = [r for r in raw.get("requires") or [] if not ctx.get(r)]
        if missing:
            skipped.append({"section": sid, "reason": "needs " + ", ".join(missing)})
            continue
        s = dict(raw)
        s["kind"] = raw.get("kind", "prompts")
        s["intro_s"] = float(raw.get("intro_s", script.get("intro_s", 4)))
        prompts = []
        if raw.get("for_each") == "object":
            skip = set(raw.get("skip_objects") or [])
            for o in [o for o in objects if o not in skip] + own:
                label = clean_text(names.get(o, o)).replace("|", "/")
                for p in raw.get("prompts") or []:
                    if _when_ok(p.get("when"), ctx):
                        prompts.append(_prompt(s, p, ctx, {"object": label, "object_key": clean_text(o)}))
        else:
            prompts = [_prompt(s, p, ctx) for p in raw.get("prompts") or [] if _when_ok(p.get("when"), ctx)]
        _set_ids(s, prompts)
        s["prompts"] = prompts
        if s["kind"] == "bar":
            heights = []
            for h in raw.get("heights") or []:
                h = {"id": h} if isinstance(h, str) else dict(h)
                h.setdefault("text", (raw.get("height_text") or {}).get(h["id"], raw.get("text", "")))
                h.setdefault("reps", raw.get("reps", 5))
                heights.append(h)
            s["heights"] = heights
        before = raw.get("before")
        s["before"] = before if before and _when_ok(before.get("when"), ctx) else None
        if s["kind"] == "prompts" and not prompts:
            skipped.append({"section": sid, "reason": "nothing to do"})
            continue
        if s["kind"] == "targets" and not raw.get("targets"):
            skipped.append({"section": sid, "reason": "no targets"})
            continue
        plan.append(s)
    return plan, skipped


def section_seconds(s, worst=False):
    """A section's length in script seconds (targets: about 4 s each, or the timeout if worst)."""
    t = s["intro_s"] + sum(p["seconds"] for p in s["prompts"])
    if s["kind"] == "targets":
        t += len(s["targets"]) * (s.get("timeout_s", 8) if worst else min(4.0, s.get("timeout_s", 8)))
    if s["kind"] == "bar":
        t += sum(s.get("lead_s", 3) + h["reps"] * s.get("period_s", 6) for h in s["heights"])
    return t


def plan_seconds(script, plan, worst=False):
    t = (script.get("welcome") or {}).get("seconds", 0) + (script.get("done") or {}).get("seconds", 0)
    for i, s in enumerate(plan):
        t += section_seconds(s, worst)
        t += s["before"]["seconds"] if s.get("before") else (script.get("between_s", 3) if i else 0)
    return t


# ------------------------------------------------------------------------------------------
# The session

class _Skip(Exception):
    pass


class _Stop(Exception):
    pass


class _Fail(Exception):
    pass


def _git_describe():
    try:
        r = subprocess.run(["git", "-C", REPO, "describe", "--always", "--dirty", "--tags"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _os_version():
    path = "/run/host/etc/os-release" if in_container() else "/etc/os-release"
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("VERSION_ID="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _steamvr_version():
    for db in ("/usr/lib/holo/pacmandb", "/var/lib/pacman"):   # SteamOS keeps it in the image
        found = sorted(glob.glob(("/run/host" if in_container() else "") + db + "/local/deckard-steamvr-rel-*"))
        if found:
            return os.path.basename(found[-1])[len("deckard-steamvr-rel-"):]
    p = host_path("/opt/steamvr/bin/version.txt")
    if p:
        try:
            with open(p) as f:
                return "build " + f.read().strip()
        except OSError:
            pass
    return ""


class Session:
    """One recording session. start() runs it in its own thread; pause(), resume(), skip()
    and stop() steer it from any thread. on_status(dict) is called from the session thread
    whenever something changes (see STATUS_KEYS)."""

    def __init__(self, base_dir, profile, checklist, lighting_choice, script_path, *, ring=None,
                 start_processes=True, dry_run=False, speed=1.0, on_status=None, hands_dir=None, panel_bin=None):
        self.base_dir = os.path.abspath(os.path.expanduser(base_dir or BASE_DIR))
        self.profile = dict(profile or {})
        self.checklist = dict(checklist or {})
        self.lighting_choice = lighting_choice or ""
        self.script_path = script_path or SCRIPT_PATH
        self.ring = ring
        self.start_processes = start_processes
        self.dry_run = dry_run
        self.speed = max(float(speed or 1.0), 0.01)
        self.on_status = on_status
        self.hands_dir = hands_dir or run_dir()
        self.panel_bin = panel_bin or PANEL_BIN
        self.print = print   # where dry-run panel commands go (the CLI's stdout)
        self.script = load_script(self.script_path)
        self.plan, self.skipped = build_plan(self.script, self.checklist)
        self.session_dir = ""
        self._thread = None
        self._lock = threading.Lock()
        self._want = {"pause": False, "skip": False, "stop": False}
        self._wake = threading.Event()
        self._status = {"state": "starting", "section": "", "title": "", "section_index": 0,
                        "section_count": len(self.plan), "prompt": "", "seconds_left": 0.0, "note": "",
                        "hands": {"left": None, "right": None}, "take": None, "error": ""}
        self._last_emit = 0.0
        self._log_file = None
        self._panel = None
        self._panel_proc = None
        self._units = []           # transient units this session started
        self._session_json = None
        self._take = None          # the take in progress: dict
        self._recorder = None
        self._prompt = None        # the prompt shown: dict (hands, controllers)
        self._live = None          # the hands file's last read
        self._hands_file = HandsFile(os.path.join(self.hands_dir, "hands"))
        self._paused = False
        self._fb = {}              # feedback timers

    # --- controls (any thread)
    def start(self):
        if self._thread:
            raise RuntimeError("the session has started already")
        self._thread = threading.Thread(target=self._run, name="handrec-session", daemon=True)
        self._thread.start()

    def _set(self, key, value):
        with self._lock:
            self._want[key] = value
        self._wake.set()

    def pause(self):
        self._set("pause", True)

    def resume(self):
        self._set("pause", False)

    def skip(self):
        self._set("skip", True)

    def stop(self, wait=20.0):
        """Stop. The take in progress is kept as far as it got. Blocks until the session has
        written its files (up to wait seconds), unless called from the session thread."""
        self._set("stop", True)
        if self._thread and threading.current_thread() is not self._thread and wait:
            self._thread.join(wait)

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    @property
    def state(self):
        return self._status["state"]

    # --- status and logging
    def _emit(self, force=True, **changes):
        self._status.update(changes)
        now = time.monotonic()
        if not force and now - self._last_emit < 0.25:
            return
        self._last_emit = now
        if self.on_status:
            try:
                self.on_status(dict(self._status, hands=dict(self._status["hands"])))
            except Exception:
                self._log("on_status: " + traceback.format_exc())

    def _log(self, text):
        if self._log_file:
            self._log_file.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
            self._log_file.flush()

    def _event(self, event, **fields):
        if not self._take:
            return
        line = {"t": mono_ns(), "event": event}
        line.update(fields)
        self._take["prompts"].write(json.dumps(line) + "\n")
        self._take["prompts"].flush()

    # --- the run
    def _run(self):
        try:
            self._make_dir()
            self._emit(state="starting")
            self._setup()
            self._screen("starting", self.script.get("welcome") or {})
            for i, s in enumerate(self.plan):
                self._section(i, s)
            self._finish("done")
        except _Stop:
            self._finish("stopped")
        except _Fail as e:
            self._finish("error", str(e))
        except Exception as e:
            self._log(traceback.format_exc())
            self._finish("error", "%s: %s" % (type(e).__name__, e))

    def _make_dir(self):
        sessions = os.path.join(self.base_dir, "sessions")
        os.makedirs(sessions, exist_ok=True)
        sid = time.strftime("%Y%m%d-%H%M%S")
        path, n = os.path.join(sessions, sid), 1
        while True:
            try:
                os.mkdir(path)
                break
            except FileExistsError:
                n += 1
                path = os.path.join(sessions, "%s-%d" % (sid, n))
        os.mkdir(os.path.join(path, "takes"))
        self.session_dir = path
        self._log_file = open(os.path.join(path, "session.log"), "a", buffering=1)
        self._log("session %s%s, script %s" % (os.path.basename(path), " (dry run)" if self.dry_run else "",
                                                self.script_path))
        for s in self.skipped:
            self._log("skipping %s: %s" % (s["section"], s["reason"]))

    def _setup(self):
        ring_path = self.ring or os.path.join(self.hands_dir, "cam-ring")
        if not self.dry_run:
            if not os.access(FT_HANDS, os.X_OK):
                raise _Fail("ft-hands isn't built: hands/build.sh")
            self._ensure_ring(ring_path)
            if not tracker_running():
                if self.start_processes:
                    self._start_tracker()
                else:
                    self._log("no tracking ft-hands found: feedback only if one publishes")
        lighting = ring_lighting(ring_path)
        cams = []
        try:
            ring = Ring(ring_path)
            cams = [{"name": c["name"], "width": c["width"], "height": c["height"]}
                    for c in ring.cams if not c["flags"] & FH_CAM_DARK and not c["name"].endswith("_dk")]
            ring.close()
        except (OSError, ValueError):
            pass
        removed = self._write_calibration() + self._write_device()
        self._session_json = {
            "schema": 1, "tool": "ft-handrec " + _git_describe(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "contributor": self.profile.get("contributor", ""),
            "lighting": {"chosen": self.lighting_choice, "ring": lighting or {}},
            "checklist": self.checklist,
            "device": {"steamos": _os_version(), "steamvr": _steamvr_version(), "cameras": cams},
            "calibration_removed": removed,
            "script": {"version": self.script.get("version"), "sections": [s["id"] for s in self.plan],
                       "skipped": self.skipped},
            "takes": [], "status": "recording"}
        if self.dry_run:
            self._session_json["dry_run"] = True
        if self.speed != 1:
            self._session_json["speed"] = self.speed
        self._save_session()
        self._panel = Panel(dry=self.dry_run, log=self._log, out=self.print)
        if not self.dry_run:
            self._start_panel()
        self._panel.cmd("show")
        self._panel.cmd("paused off")
        for key, c in (("note", "note "), ("countdown", "countdown off"), ("hands", "hands off off"),
                       ("bar", "bar off"), ("target", "target off")):
            self._panel.set(key, c)

    def _write_calibration(self):
        src = host_path("/persist/xrservice.json")
        if not src:
            self._log("no /persist/xrservice.json: no calibration.json")
            return []
        with open(src) as f:
            clean, removed = strip_calibration(json.load(f))
        write_json(os.path.join(self.session_dir, "calibration.json"), clean)
        return removed

    def _write_device(self):
        """device.json: the rig's pose in the CAD frame from /persist/device_config.json, only
        cv.cad_from_cal (Cam0 in CAD) and head (the head in CAD), in the shape the labeller reads
        (frame-hands train/label, as its cut.py writes it). The rest of that file names the unit
        (serial number, EDID). Returns what was removed, as "device.json:<path>"."""
        src = host_path("/persist/device_config.json")
        if not src:
            self._log("no /persist/device_config.json: no device.json")
            return []
        try:
            with open(src) as f:
                dev = json.load(f)
            picked = {"cv": {"cad_from_cal": dev["cv"]["cad_from_cal"]}, "head": dev["head"]}
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._log("device_config.json unreadable (%s): no device.json" % e)
            return []
        clean, removed = strip_calibration(picked)
        write_json(os.path.join(self.session_dir, "device.json"), clean)
        return ["device.json:" + r for r in removed]

    def _save_session(self):
        if self._session_json is not None:
            write_json(os.path.join(self.session_dir, "session.json"), self._session_json)

    def _ensure_ring(self, path):
        def alive():
            try:
                r = Ring(path)
            except (OSError, ValueError):
                return False
            try:
                return r.alive()
            finally:
                r.close()

        if alive():
            return
        if self.ring:
            raise _Fail("No frames in %s (start ft-ringplay first)" % path)
        if not self.start_processes:
            raise _Fail("ft-camd isn't running (and --no-start)")
        if not os.access(FT_CAMD, os.X_OK):
            raise _Fail("ft-camd isn't built: hands/build.sh")
        caps = subprocess.run(["getcap", FT_CAMD], capture_output=True, text=True) if shutil.which("getcap") else None
        if caps is not None and "cap_sys_ptrace" not in caps.stdout:
            raise _Fail("ft-camd needs its capabilities: hands/run.sh caps (asks for sudo)")
        self._start_unit(CAMD_UNIT, "the camera broker", [FT_CAMD, "--status", "60"])
        for _ in range(150):
            if alive():
                return
            if self._want["stop"]:
                raise _Stop()
            time.sleep(0.1)
        raise _Fail("ft-camd didn't start (is SteamVR running?): journalctl --user -u " + CAMD_UNIT)

    def _start_tracker(self):
        up = os.path.join(REPO, "scripts", "container-up.sh")
        if os.access(up, os.X_OK):
            subprocess.run(host_command(up), capture_output=True, timeout=120)
        argv = [os.path.expanduser("~/.local/bin/distrobox"), "enter", "dev", "--", FT_HANDS,
                "--no-gestures", "--status", "0"]
        if self.ring:
            argv += ["--ring", self.ring]
        self._start_unit(HANDS_UNIT, "hand tracking for feedback", argv)

    def _start_unit(self, unit, what, argv):
        def active():
            return subprocess.run(host_command("systemctl", "--user", "-q", "is-active", unit),
                                  capture_output=True, timeout=30).returncode == 0

        if active():
            self._log("%s is running already" % unit)
            return
        cmd = host_command("systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
                           "--description=Frametop hand recorder: " + what,
                           "-p", "PartOf=steamvr.service", "-p", "After=steamvr.service",
                           "-p", "Restart=on-failure", "-p", "RestartSec=3", "-p", "TimeoutStopSec=5", *argv)
        for attempt in range(3):   # distrobox-host-exec has failed once, silently, and worked again
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 or active():
                self._units.append(unit)
                self._log("started %s" % unit)
                return
            self._log("starting %s failed (exit %d): %s" % (unit, r.returncode, (r.stderr or r.stdout).strip()))
            time.sleep(0.5)
        raise _Fail("couldn't start %s (exit %d): %s" % (unit, r.returncode, (r.stderr or r.stdout).strip()))

    def _stop_units(self):
        for unit in reversed(self._units):
            subprocess.run(host_command("systemctl", "--user", "stop", unit), capture_output=True, timeout=30)
            self._log("stopped %s" % unit)
        self._units = []

    def _start_panel(self):
        if self._panel.cmd("ping", reply=True):
            self._log("using the ft-handpanel that's running")
            return
        if not os.access(self.panel_bin, os.X_OK):
            raise _Fail("ft-handpanel isn't built: hands/rec/build.sh")
        self._panel_proc = subprocess.Popen([self.panel_bin, "--watch-stdin"], stdin=subprocess.PIPE,
                                            stdout=self._log_file, stderr=self._log_file)
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if self._panel.cmd("ping", reply=True, timeout=0.2):
                return
            if self._panel_proc.poll() is not None:
                raise _Fail("ft-handpanel exited (is SteamVR running?): see session.log")
            time.sleep(0.1)
        raise _Fail("ft-handpanel doesn't answer")

    def _stop_panel(self):
        if self._panel:
            self._panel.cmd("hide")
        if self._panel_proc:
            try:
                self._panel_proc.stdin.close()
                self._panel_proc.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                self._panel_proc.terminate()
                try:
                    self._panel_proc.wait(3)
                except subprocess.TimeoutExpired:
                    self._panel_proc.kill()
            self._panel_proc = None
        if self._panel:
            self._panel.close()

    def _finish(self, state, error=""):
        if self._take:
            self._end_take({"done": "complete", "stopped": "stopped"}.get(state, "stopped"))
        if self._session_json is not None:
            self._session_json["status"] = state
            self._session_json["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if error:
                self._session_json["error"] = error
            self._save_session()
        self._log("%s%s" % (state, ": " + error if error else ""))
        self._stop_units()
        if self._panel:
            screen = {"done": self.script.get("done") or {}, "stopped": self.script.get("stopped") or {},
                      "error": {"title": "Something went wrong", "text": "See the Hand recorder window.",
                                "seconds": 4}}[state]
            try:
                self._panel.cmd("paused off")
                self._screen(state, screen, controls=False)
            except (_Stop, _Skip, _Fail):
                pass
        self._stop_panel()
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        screen = {"done": self.script.get("done"), "stopped": self.script.get("stopped")}.get(state) or {}
        self._emit(state=state, error=error, seconds_left=0.0, note="", take=None, section="",
                   prompt=error or screen.get("text", "").replace("|", "\n"), hands={"left": None, "right": None})

    # --- the timing loop
    def _controls(self):
        """Handle a pause (blocking until resumed), skip and stop. True if a pause happened."""
        with self._lock:
            want = dict(self._want)
            self._want["skip"] = False
        if want["stop"]:
            raise _Stop()
        if want["skip"]:
            raise _Skip()
        if not want["pause"]:
            return False
        self._pause()
        outcome = None
        while outcome is None:
            self._wake.wait(0.2)
            self._wake.clear()
            with self._lock:
                want = dict(self._want)
                self._want["skip"] = False
                if want["skip"]:
                    self._want["pause"] = False
            if want["stop"]:
                outcome = _Stop
            elif want["skip"]:
                outcome = _Skip
            elif not want["pause"]:
                outcome = True
        self._unpause(record=outcome is True)
        if outcome is not True:
            raise outcome()
        return True

    def _pause(self):
        self._paused = True
        self._state_before = self._status["state"]
        if self._take:
            self._stop_recording()
            self._event("pause")
        self._panel.cmd("paused on")
        self._panel.set("note", "note " + RESUME_HINT)
        self._emit(state="paused", note=RESUME_HINT)
        self._log("paused")

    def _unpause(self, record=True):
        self._paused = False
        self._panel.cmd("paused off")
        self._panel.set("note", "note ")
        self._fb.clear()
        if self._take and record:
            self._event("resume")
            self._start_recording()
        self._emit(state=self._state_before, note="")
        self._log("resumed")

    def _wait(self, seconds, tick=None, countdown=True):
        """Let `seconds` (script time) pass, handling the controls and feedback. tick(dt) runs
        every step with the script time passed; it ends the wait early by returning True.
        Returns True if tick ended it, else False."""
        left = float(seconds)
        last = time.monotonic()
        last_cd = -1.0
        while True:
            if self._controls():
                last = time.monotonic()
            now = time.monotonic()
            dt = (now - last) * self.speed
            last = now
            left -= dt
            self._feedback()
            if tick and tick(dt):
                return True
            if countdown and seconds > 0 and (now - last_cd >= 0.25 or left <= 0):
                last_cd = now
                self._panel.set("countdown", "countdown %.2f" % max(0.0, min(1.0, left / seconds)))
            self._emit(force=False, seconds_left=round(max(left, 0.0), 1))
            if left <= 0:
                return False
            self._wake.wait(TICK_S)
            self._wake.clear()

    # --- feedback
    def _feedback(self):
        now = time.monotonic()
        fb = self._fb
        if self._panel_proc and self._panel_proc.poll() is not None:
            raise _Fail("The headset panel closed (did SteamVR quit?)")
        if self._recorder and self._recorder.exited():
            raise _Fail("The recording stopped by itself: see session.log")
        if not self.dry_run and now - fb.get("disk", 0) > 5:
            fb["disk"] = now
            if shutil.disk_usage(self.session_dir).free < MIN_FREE:
                raise _Fail("The disk is nearly full: the session stopped")
        live = None if self.dry_run else self._hands_file.read()
        self._live = live
        p = self._prompt or {}
        asked = p.get("hands", "")
        seen = {s: (bool(live[s]) if live else None) for s in ("left", "right")}
        # the chips: the asked-for hands, seen or lost, while the tracker publishes
        chips = []
        for side in ("left", "right"):
            show = live is not None and (asked in (side, "both", "any"))
            chips.append(("seen" if seen[side] else "lost") if show else "off")
        self._panel.set("hands", "hands %s %s" % tuple(chips))
        # a note when an asked-for hand stays lost, or a hand shows when none is wanted
        notes = []
        if live is not None and asked:
            if asked == "none":
                missing = [] if not (seen["left"] or seen["right"]) else ["shown"]
            elif asked == "any":
                missing = [] if (seen["left"] or seen["right"]) else ["any"]
            else:
                missing = [s for s in ("left", "right") if asked in (s, "both") and not seen[s]]
            key = ",".join(missing)
            if key != fb.get("lost_key"):
                fb["lost_key"], fb["lost_since"] = key, now
            if missing and now - fb["lost_since"] > LOST_S:
                if missing == ["shown"]:
                    notes.append("I can see a hand: keep them out of view")
                elif missing == ["any"] or len(missing) == 2:
                    notes.append("I can't see your hands: bring them into view")
                else:
                    notes.append("I can't see your %s hand: bring it into view" % missing[0])
        else:
            fb.pop("lost_key", None)
        notes = self._controller_feedback(now, p) + notes
        note = notes[0] if notes else ""
        if not self._paused:
            self._panel.set("note", "note " + note)
        hands = {"left": seen["left"], "right": seen["right"]}
        if note != self._status["note"] or hands != self._status["hands"]:
            self._emit(note=note, hands=hands)
        if self._take and live is not None and now - fb.get("logged", 0) >= 0.5:
            fb["logged"] = now
            self._event("feedback", left=seen["left"], right=seen["right"],
                        palm_m=[round(live[s]["palm_m"], 4) if live[s] else None for s in ("left", "right")])

    def _controller_feedback(self, now, p):
        """Sections with controllers: `devices` once a second; a result other than 200 for
        more than a second gets a note, and each change goes into prompts.jsonl."""
        sides = p.get("controllers") or []
        fb = self._fb
        if not sides:
            fb.pop("ctl", None)
            return []
        ctl = fb.setdefault("ctl", {"polled": 0, "r": {}, "bad_since": {}, "lost": []})
        if now - ctl["polled"] >= 1.0:
            ctl["polled"] = now
            reply = self._panel.cmd("devices", reply=True, timeout=0.3) or ""
            tok = reply.split()
            vals = dict(zip(tok[1::2], tok[2::2]))
            if tok[:1] == ["ok"] and "hmd" in vals:
                ctl["r"] = {s: vals.get(s, "-") for s in ("left", "right")}
            for s in sides:
                r = ctl["r"].get(s)
                if r is None or r == "200":
                    ctl["bad_since"].pop(s, None)
                else:
                    ctl["bad_since"].setdefault(s, now)
        lost = sorted(s for s, t in ctl["bad_since"].items() if s in sides and now - t > CONTROLLER_LOST_S)
        if lost != ctl["lost"]:
            ctl["lost"] = lost
            self._event("feedback", controller={s: (None if ctl["r"].get(s) in (None, "-") else int(ctl["r"][s]))
                                                for s in ("left", "right")}, controller_lost=lost)
        notes = []
        for s in lost:
            if ctl["r"].get(s) == "-":
                notes.append("I can't find the %s controller: is it on?" % s)
            else:
                notes.append("The %s controller lost tracking: turn your palm slightly toward you" % s)
        return notes

    # --- sections and takes
    def _show(self, title=None, step=None, text=None):
        if title is not None:
            self._panel.set("title", "title " + clean_text(title))
        if step is not None:
            self._panel.set("step", "step " + clean_text(step))
        if text is not None:
            self._panel.set("text", "text " + clean_text(text))

    def _screen(self, state, screen, controls=True):
        """A screen of its own (welcome, done): title, text, a few seconds. controls=False: the
        session's end, which reports its state once all is done."""
        if not screen:
            return
        self._prompt = None
        self._show(screen.get("title", ""), "", screen.get("text", ""))
        self._panel.set("countdown", "countdown off")
        if controls:
            self._emit(state=state, title=screen.get("title", ""), prompt=screen.get("text", "").replace("|", "\n"),
                       section="", seconds_left=float(screen.get("seconds", 0)))
            try:
                self._wait(screen.get("seconds", 0), countdown=False)
            except _Skip:
                pass
        else:
            end = time.monotonic() + screen.get("seconds", 0) / self.speed
            while time.monotonic() < end and not self._want["stop"]:
                time.sleep(TICK_S)

    def _section(self, i, s):
        step = "Section %d of %d" % (i + 1, len(self.plan))
        status = {"section": s["id"], "title": s["title"], "section_index": i + 1, "section_count": len(self.plan)}
        try:
            before = s.get("before")
            if before or i > 0:
                self._prompt = None
                text = before["text"] if before else "Next: %s" % s["title"]
                secs = before.get("seconds", 10) if before else self.script.get("between_s", 3)
                self._show("Get ready" if before else s["title"], step, text)
                self._emit(state="between", prompt=text.replace("|", "\n"), take=None, **status)
                self._wait(secs)
            self._start_take(i, s)
            self._status.update(state="intro", take=self._take["id"], **status)   # sent with the intro
            self._run_prompt(s, {"id": s["id"] + "/intro", "text": s.get("intro", ""), "seconds": s["intro_s"],
                                 "hands": "", "pose": "", "distance": "", "position": "", "object": "",
                                 "controller": False, "controllers": []}, step, intro=True)
            self._status["state"] = "running"   # sent with the first prompt
            if s["kind"] == "targets":
                self._targets(s, step)
            elif s["kind"] == "bar":
                self._bar(s, step)
            for p in s["prompts"]:
                self._run_prompt(s, p, step)
            self._end_take("complete")
        except _Skip:
            self._log("skipped %s" % s["id"])
            if self._take:
                self._end_take("skipped")
        except _Stop:
            raise
        finally:
            self._panel.set("bar", "bar off")
            self._panel.set("target", "target off")

    def _start_take(self, i, s):
        n = len(self._session_json["takes"]) + 1
        take_id = "%02d-%s" % (n, s["id"])
        d = os.path.join(self.session_dir, "takes", take_id)
        os.makedirs(d)
        self._take = {"id": take_id, "dir": d, "section": s, "part": 0,
                      "prompts": open(os.path.join(d, "prompts.jsonl"), "a", buffering=1),
                      "json": {"section": s["id"], "title": s["title"], "started_ns": mono_ns(), "ended_ns": None,
                               "status": "stopped", "deleted": [], "notes": ""}}
        write_json(os.path.join(d, "take.json"), self._take["json"])
        self._session_json["takes"].append(take_id)
        self._save_session()
        self._event("take", section=s["id"], take=take_id)
        self._start_recording()
        self._log("take %s" % take_id)

    def _start_recording(self):
        t = self._take
        t["part"] += 1
        if not self.dry_run:
            # a safety net only: the session ends the recording itself
            remaining = section_seconds(t["section"], worst=True) / self.speed
            self._recorder = Recorder(t["dir"], t["part"], remaining * 1.5 + 60, self.ring, self._log_file)
        self._panel.cmd("poses start " + os.path.join(t["dir"], "poses.jsonl"))

    def _stop_recording(self):
        self._panel.cmd("poses stop", reply=not self.dry_run, timeout=1.0)
        if self._recorder:
            rec, self._recorder = self._recorder, None
            code = rec.stop()
            self._log("recording part %d ended (%s)" % (rec.part, code))

    def _end_take(self, status):
        t = self._take
        try:
            if not self._paused:
                self._stop_recording()
        finally:
            self._event("end", status=status)
            self._take = None
            t["prompts"].close()
            t["json"]["ended_ns"] = mono_ns()
            t["json"]["status"] = status
            write_json(os.path.join(t["dir"], "take.json"), t["json"])
            self._log("take %s %s" % (t["id"], status))

    def _prompt_event(self, p):
        self._event("prompt", id=p["id"], text=p["text"], hands=p["hands"], pose=p["pose"], distance=p["distance"],
                    position=p["position"], object=p["object"], controller=bool(p["controller"]),
                    **({"controllers": p["controllers"]} if p["controllers"] else {}))

    def _begin_prompt(self, s, p, step):
        self._prompt = p
        self._fb.pop("lost_key", None)
        self._show(s["title"], step, p["text"])
        self._prompt_event(p)
        self._emit(prompt=p["text"].replace("|", "\n"), seconds_left=float(p["seconds"]))
        self._log("  %s" % p["id"])

    def _run_prompt(self, s, p, step, intro=False):
        if intro and not p["text"]:
            return
        self._begin_prompt(s, p, step)
        self._wait(p["seconds"])

    def _targets(self, s, step):
        hold_s, timeout_s = float(s.get("hold_s", 1.0)), float(s.get("timeout_s", 8))
        defaults = s.get("defaults") or {}
        for k, pt in enumerate(s["targets"]):
            p = {"id": "%s/%d" % (s["id"], k + 1), "text": s.get("text", ""), "seconds": timeout_s,
                 "hands": defaults.get("hands", "any"), "pose": defaults.get("pose", "point"), "distance": "",
                 "position": "", "object": "", "controller": False, "controllers": []}
            self._begin_prompt(s, p, step)
            xyz = "%.3f %.3f %.3f" % tuple(pt)
            reply = self._panel.cmd("target %s show" % xyz, reply=True) or ""
            self._panel.sent["target"] = "target %s show" % xyz
            tok = reply.split()
            room = [float(v) for v in tok[1:4]] if tok[:1] == ["ok"] and len(tok) >= 4 else None
            target = {"id": p["id"], "head": list(pt), "room": room}
            self._event("target", state="show", **target)
            st = {"hold": 0.0, "sent": 0.0, "holding": False}

            def tick(dt, pt=pt, room=room, target=target, st=st, xyz=xyz):
                d = self._tip_distance(pt, room)
                if d is not None and d <= TOUCH_M:
                    if not st["holding"]:
                        st["holding"] = True
                        self._event("target", state="hold", **target)
                    st["hold"] += dt
                elif st["holding"]:
                    st["holding"], st["hold"] = False, 0.0
                    self._panel.set("target", "target %s show" % xyz)
                if st["holding"]:
                    frac = min(1.0, st["hold"] / hold_s)
                    if time.monotonic() - st["sent"] >= 0.1 or frac >= 1:
                        st["sent"] = time.monotonic()
                        self._panel.set("target", "target %s hold %.2f" % (xyz, frac))
                return st["hold"] >= hold_s

            done = self._wait(timeout_s, tick)
            state = "done" if done else "timeout"
            if done:
                self._panel.set("target", "target %s done" % xyz)
            self._event("target", state=state, **target)
            self._log("    %s %s" % (p["id"], state))
            if done:
                self._wait(0.6, countdown=False)
            self._panel.set("target", "target off")

    def _tip_distance(self, pt, room):
        """The nearest seen index tip's distance from the target: in the room (with the head's
        pose now) when the panel placed the target there, else in the head frame."""
        live = self._live
        if not live:
            return None
        tips = [live[s]["tip"] for s in ("left", "right") if live[s]]
        if not tips:
            return None
        ref = pt
        if room is not None:
            reply = (self._panel.cmd("head", reply=True, timeout=0.1) or "").split()
            if reply[:1] == ["ok"] and len(reply) == 13:
                m = [float(v) for v in reply[1:]]
                tips = [[m[4 * r] * t[0] + m[4 * r + 1] * t[1] + m[4 * r + 2] * t[2] + m[4 * r + 3] for r in range(3)]
                        for t in tips]
                ref = room
        return min(math.dist(t, ref) for t in tips)

    def _palm_share(self, near, far):
        live = self._live
        if not live:
            return -1.0
        ds = [live[s]["palm_m"] for s in ("left", "right") if live[s]]
        if not ds:
            return -1.0
        return max(0.0, min(1.0, (sum(ds) / len(ds) - near) / (far - near)))

    def _bar(self, s, step):
        near, far = float(s.get("near_m", 0.2)), float(s.get("far_m", 0.6))
        period, lead = float(s.get("period_s", 6)), float(s.get("lead_s", 3))
        labels = "%s %s" % (s.get("near_label", "Near"), s.get("far_label", "Far"))
        defaults = s.get("defaults") or {}
        for h in s["heights"]:
            p = {"id": "%s/%s" % (s["id"], h["id"]), "text": h["text"], "hands": defaults.get("hands", "both"),
                 "pose": defaults.get("pose", "open"), "distance": "", "position": h["id"], "object": "",
                 "controller": bool(defaults.get("controller", False)),
                 "controllers": list(defaults.get("controllers") or [])}
            total = lead + h["reps"] * period
            p["seconds"] = total
            self._begin_prompt(s, p, step)
            st = {"t": 0.0, "sent": 0.0}

            def tick(dt, st=st):
                st["t"] += dt
                sweep = st["t"] - lead
                target = 0.0 if sweep <= 0 else 1 - abs(1 - 2 * ((sweep % period) / period))
                if time.monotonic() - st["sent"] >= 0.1:
                    st["sent"] = time.monotonic()
                    cur = self._palm_share(near, far)
                    self._panel.set("bar", "bar %.3f %.3f %s" % (target, cur, labels))
                    if sweep > 0:
                        self._event("bar", target=round(target, 3), current=None if cur < 0 else round(cur, 3))
                return False

            self._wait(total, tick)
        self._panel.set("bar", "bar off")


# ------------------------------------------------------------------------------------------
# The command line

def main():
    ap = argparse.ArgumentParser(description="Run a hand recording session (the hand recorder's session runner).")
    ap.add_argument("--dry-run", action="store_true", help="run no processes; print the panel commands")
    ap.add_argument("--speed", type=float, default=1.0, help="run the script this many times faster")
    ap.add_argument("--ring", help="read frames from this ring (ft-ringplay's) instead of ft-camd's")
    ap.add_argument("--no-start", action="store_true", help="start no ft-camd or tracking ft-hands")
    ap.add_argument("--base", help="where sessions go (default %s; a temporary folder with --dry-run)" % BASE_DIR)
    ap.add_argument("--objects", default="", help="ticked objects, comma-separated (unknown names are your own)")
    ap.add_argument("--controllers", action="store_true", help="controllers with the straps")
    ap.add_argument("--lighting", default="room", choices=("dim", "room", "daylight"))
    ap.add_argument("--script", default=SCRIPT_PATH)
    ap.add_argument("--panel", help="the panel program (default hands/rec/build/ft-handpanel)")
    ap.add_argument("--hands-dir", help="where the hands file is (default /run/user/UID/frametop-hands)")
    ap.add_argument("--plan", action="store_true", help="print the sections and their length, and exit")
    a = ap.parse_args()

    known = ("pencil", "phone", "cup", "keyboard", "mouse", "gamepad", "small")
    names = [o.strip() for o in a.objects.split(",") if o.strip()]
    checklist = {"objects": [o for o in names if o in known], "own_objects": [o for o in names if o not in known],
                 "controllers": "straps" if a.controllers else "none", "sleeves": "", "rings": False,
                 "watch": False, "notes": ""}
    base = a.base
    if not base and a.dry_run:
        import tempfile
        base = tempfile.mkdtemp(prefix="handrec-dry-")
    base = base or BASE_DIR
    profile = {}
    try:
        with open(os.path.join(base, "profile.json")) as f:
            profile = json.load(f)
    except (OSError, ValueError):
        pass

    t0 = time.monotonic()
    last = {}

    def on_status(st):
        key = (st["state"], st["section"], st["prompt"], st["note"], st["take"],
               st["hands"]["left"], st["hands"]["right"], st["error"])
        if key == last.get("key"):
            return
        last["key"] = key
        hands = "".join("%s%s" % (s[0].upper(), {True: "+", False: "-", None: "?"}[st["hands"][s]])
                        for s in ("left", "right"))
        line = "[%6.1f] %-8s %d/%d %-16s %s %s" % (time.monotonic() - t0, st["state"], st["section_index"],
                                                   st["section_count"], st["section"] or "-", hands, st["prompt"])
        if st["note"]:
            line += "  (%s)" % st["note"]
        if st["error"]:
            line += "  ERROR: %s" % st["error"]
        print(line, flush=True)

    s = Session(base, profile, checklist, a.lighting, a.script, ring=a.ring, start_processes=not a.no_start,
                dry_run=a.dry_run, speed=a.speed, on_status=on_status, hands_dir=a.hands_dir, panel_bin=a.panel)
    est, worst = plan_seconds(s.script, s.plan), plan_seconds(s.script, s.plan, worst=True)
    print("%d sections, about %.1f min (at most %.1f)%s" % (len(s.plan), est / 60, worst / 60,
                                                            ", %gx speed" % a.speed if a.speed != 1 else ""))
    for sk in s.skipped:
        print("  skipping %s: %s" % (sk["section"], sk["reason"]))
    if a.plan:
        for sec in s.plan:
            print("  %-18s %-9s %3d prompts %5.0f s" % (sec["id"], sec["kind"], len(sec["prompts"]), section_seconds(sec)))
        return 0
    if not a.dry_run:
        light = ring_lighting(a.ring)
        match = similar_lighting(base, {"chosen": a.lighting, "ring": light}) if light else None
        print("lighting: %s" % (json.dumps(light) if light else "no camera ring"))
        if match:
            print("  about the same light as session %s (%s)" % match)

    def on_signal(*_):
        print("stopping", flush=True)
        threading.Thread(target=s.stop, daemon=True).start()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if sys.stdin.isatty():
        def keys():
            print("keys: p pause, r resume, s skip, q stop (then Enter)", flush=True)
            for line in sys.stdin:
                c = line.strip()[:1]
                if c == "p":
                    s.pause()
                elif c == "r":
                    s.resume()
                elif c == "s":
                    s.skip()
                elif c == "q":
                    s.stop(wait=0)
        threading.Thread(target=keys, daemon=True).start()
    s.start()
    while s._thread.is_alive():
        s.join(0.5)
    print("session: %s" % s.session_dir)
    return 0 if s.state in ("done", "stopped") else 1


if __name__ == "__main__":
    sys.exit(main())
