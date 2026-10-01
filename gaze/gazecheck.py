"""gazecheck: the gaze service's checks and calibration, in the panel fixed to the headset
(gaze/panel/ft-gazepanel; ft-gazed runs it). Every kind is made of dots shown at head-relative
directions: look at each one.

  quick   one dot in the middle of your view. It opens when the headset goes on: eyes seen
          for DON_DELAY after none for AWAY_MIN (SteamVR's tracker's variance for an eye under
          EYE_LOST). SteamVR's "HMD on" can't say: it repeats every minute or so, and it can
          stay on for hours with nobody in the headset. It also opens when our own tracker asks
          for a click (its "reseat": the headset may sit differently on your face now), at most
          once every QUICK_COOLDOWN, and on "quickcal" (Frametop Input Settings, or a mouse
          button or key combination mapped to Gaze quick check), and when a click's correction
          was past POINTER_GAZE_NUDGE_MAX (55 degrees; the helper's "recheck"): the tracker is
          far off. Ignored, it closes after QUICK_TIMEOUT and changes nothing.
  five    the middle and four around it, when the first FIVE_COUNT lessons after a quick check
          were all over FIVE_LIMIT degrees off: the quick check didn't fix it.
  full    the calibration, as the gaze probe's: three rounds, dark, medium and bright (pupil
          size, and the tracker's error with it, changes with brightness), each the middle and
          a ring of six (SteamVR's tracker) or eight (ours, whose fit goes wrong past its dots)
          RING degrees out, half that in the middle round, turned 20 degrees a round. It opens
          when gaze mode comes on without a calibration for the tracker in use, and on
          "calibrate". Frametop's screens hide while it runs. Quitting it while there's still
          no calibration turns gaze mode off (POINTER_GAZE=0); turning it on again reopens it.

  fit     the headset fit check (on "fitcheck", Check headset fit on the Gaze page): live, a
          card per eye (tracked or lost, the tracker's signal, how much of the last 10 s it was
          seen) and hints, from the gaze probe's Headset fit (gaze/fitcheck.py), while you
          adjust the headset. A left click or Meta+J runs its guided check (dots, then looks
          down, up, left and right); a right click or Meta+K closes it, as does FIT_TIMEOUT.

The quick check's dot captures itself: from CHECK_SETTLE after it shows (the eyes getting
there), once the gaze has held within CHECK_SPREAD for CHECK_WINDOW (the probe's max spread and
capture time). It's the gaze holding still that counts, not where the tracker puts it, so it
works however far off the tracker is; a left click or Meta+J (the pointer helper's
"calaccept") takes it now. The full calibration's and five's dots wait for that click: you
click when you're looking at the dot (the user asked for that: a steady gaze isn't always on
the dot), and the gaze held still up to then is taken (ACCEPT_SPREAD). They wait as long as
it takes, up to CLICK_IDLE. A right click or Meta+K ("calquit") closes the panel. The pointer hides meanwhile ("calpanel 1",
renewed every second; the helper shows it again by itself when that stops).

What a capture teaches:
  our tracker   quick and five: a click ("click T YAW PITCH", like a pointer lesson); full:
                calib-start, a calib-point for each dot, calib-fit (its calibration)
  SteamVR's     quick and five: a lesson for each eye, like the pointer's; full: each source's
                calibration fitted from the dots (gazecal.Correction, the probe's way), saved
                to calibration.json, and the lessons start over on top of it
Either way, how far off each eye was goes to the eye bias, and each capture to the lesson log.
"""

import json
import math
import os
import selectors
import signal
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

from fitcheck import MIN_REGION, FitCheck, wrap
from gazecal import DEFAULT_MODEL, EYE_LOST, STATE, steady_samples

REPO = Path(__file__).resolve().parents[1]
PANEL_PROG = REPO / "gaze" / "build" / "ft-gazepanel"
PANEL = "\0ft_gazepanel"
POINTER = "\0ft_pointer_helper"
SCREENS = "\0ft_screens"
EYES = "\0ft_eyes"
CONF = Path.home() / ".config" / "frametop.conf"
CALIBRATION = STATE / "calibration.json"
POINTS = STATE / "points.jsonl"   # the probe's record of calibration dots, for its refine
CHECK_LOG = STATE / "checks.jsonl"

CHECK_SETTLE = 0.45
CHECK_WINDOW = 0.6
CHECK_SPREAD = 1.0     # degrees
ACCEPT_SPREAD = 2.5    # degrees: a capture asked for (calaccept) takes this much
CLICK_IDLE = 120.0     # seconds a dot of five or full waits for its click; then the check closes
QUICK_TIMEOUT = 6.0
QUICK_COOLDOWN = 120.0
DON_DELAY = 3.0        # seconds of eyes after AWAY_MIN without: the headset went on
AWAY_MIN = 3.0
EYES_GONE = 2.0        # seconds without eyes that close a check: the headset came off
FIVE_LIMIT = 2.0
FIVE_COUNT = 3
DONE_PAUSE = 0.35      # seconds the filled dot shows before the next
RING = 20.0
ROUND_BG = (0.03, 0.33, 0.8)
ROUND_NAMES = ("dark", "medium", "bright")
RING_SCALE = (1.0, 0.5, 1.0)
PANEL_RETRY = 10.0
FIT_TIMEOUT = 300.0    # seconds the fit check stays up
FIT_EVERY = 0.5        # seconds between its cards' updates (each is a new picture for the panel)
FIT_HINT_WIDTH = 95    # characters a hint line holds in the panel


def log(msg):
    print(f"ft-gazed: {msg}", file=sys.stderr, flush=True)


def check_dots(kind, own):
    """(yaw, pitch, round): head-relative degrees, yaw +left, pitch +up."""
    if kind == "quick":
        return [(0.0, 0.0, 0)]
    if kind == "five":
        return [(0.0, 0.0, 0), (12.0, 0.0, 0), (-12.0, 0.0, 0), (0.0, 9.0, 0), (0.0, -9.0, 0)]
    out = []
    n = 8 if own else 6
    for rnd in range(3):
        out.append((0.0, 0.0, rnd))
        r = RING * RING_SCALE[rnd]
        for i in range(n):
            a = math.radians(-90 + rnd * 20 + i * 360 / n)  # as the probe: x right, y down
            x, y = r * math.cos(a), r * math.sin(a) * (0.9 if own else 1.0)
            out.append((-x, -y, rnd))
    return out


def spread(points):
    """The median point and the spread around it (1.4826 x the median distance: a standard
    deviation that one stray sample can't move far)."""
    mx = statistics.median(p[0] for p in points)
    my = statistics.median(p[1] for p in points)
    return 1.4826 * statistics.median(math.hypot(p[0] - mx, p[1] - my) for p in points), (mx, my)


def write_gaze(on, path=CONF):
    """POINTER_GAZE=1|0 in the config, every other line kept."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    value = f"POINTER_GAZE={1 if on else 0}"
    for i, line in enumerate(lines):
        if line.split("#", 1)[0].split("=", 1)[0].strip() == "POINTER_GAZE":
            lines[i] = value
            break
    else:
        lines.append(value)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def ask(addr, command, timeout=1.0):
    """A command to a local datagram socket, and its reply ("" without one)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
    try:
        s.bind("")
        s.settimeout(timeout)
        s.sendto(command.encode(), addr)
        return s.recv(4096).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        s.close()


class Checks:
    def __init__(self, svc, sel):
        self.svc = svc
        self.sel = sel
        self.out = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC | socket.SOCK_NONBLOCK)
        self.out.bind("")  # the panel's and the helper's replies come back here
        sel.register(self.out, selectors.EVENT_READ, "checks")
        self.check = None
        self.gaze_on = None
        self.gaze_heard = 0.0
        self.want_full_until = 0.0  # gaze mode came on before the tracker said whether it's calibrated
        self.last_quick = 0.0
        self.sample_at = 0.0     # the tracker last sent anything
        self.seen_at = 0.0       # eyes last seen (SteamVR's tracker's variance for them, "unc")
        self.away = True         # no eyes for AWAY_MIN: their coming back is the headset going on
        self.back_since = None
        self.reseat_seen = False
        self.after_quick = None
        self.panel_proc = None
        self.panel_restart_at = 0.0
        self.screens_shown = None
        self.last_progress = 0.0

    @property
    def active(self):
        return self.check is not None

    # --- The panel process ---

    def start_panel(self):
        if not PANEL_PROG.exists():
            log(f"ft-gazepanel isn't built: run {REPO}/gaze/build.sh")
            self.panel_restart_at = time.monotonic() + 60
            return
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        distrobox = Path.home() / ".local" / "bin" / "distrobox"
        self.panel_proc = subprocess.Popen([str(distrobox), "enter", "dev", "--", str(PANEL_PROG), "--watch-stdin"],
                                           env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, start_new_session=True)
        os.set_blocking(self.panel_proc.stderr.fileno(), False)
        self.sel.register(self.panel_proc.stderr, selectors.EVENT_READ, "panel")
        log("ft-gazepanel started")

    def read_panel(self):
        try:
            data = os.read(self.panel_proc.stderr.fileno(), 65536)
        except BlockingIOError:
            return
        if not data:
            log(f"ft-gazepanel stopped (exit {self.panel_proc.poll()}); again in {PANEL_RETRY:.0f} s")
            self.stop_panel()
            self.panel_restart_at = time.monotonic() + PANEL_RETRY
            if self.check:
                self.close("the panel stopped")
            return
        for line in data.decode("utf-8", "replace").splitlines():
            if line.strip():
                log(line)

    def stop_panel(self):
        if not self.panel_proc:
            return
        try:
            self.sel.unregister(self.panel_proc.stderr)
        except (KeyError, ValueError):
            pass
        if self.panel_proc.stdin and not self.panel_proc.stdin.closed:
            self.panel_proc.stdin.close()
        try:
            self.panel_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.panel_proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.panel_proc = None

    def to_panel(self, command):
        try:
            self.out.sendto(command.encode(), PANEL)
        except OSError:
            pass

    def to_helper(self, command):
        try:
            self.out.sendto(command.encode(), POINTER)
        except OSError:
            pass

    def on_readable(self):
        """Replies on our socket: the helper's "ok on|off" to "gaze ?"; the panel's are dropped."""
        while True:
            try:
                data = self.out.recv(4096).decode("utf-8", "replace")
            except (BlockingIOError, OSError):
                return
            if data in ("ok on", "ok off"):
                on = data == "ok on"
                was, self.gaze_on, self.gaze_heard = self.gaze_on, on, time.monotonic()
                if on and was is False:
                    self.on_gaze_on()

    # --- State ---

    def calibrated(self):
        """Does the tracker in use have a calibration? None: our tracker hasn't said yet."""
        svc = self.svc
        kind = svc.kind
        if kind == "own":
            if time.monotonic() - svc.own_at > 5 or not svc.own:
                return None
            return bool((svc.own.get("calibration") or {}).get("dots"))
        if kind == "eyes":
            return True
        return svc.models[svc.source].samples > 0

    def eyes_seen(self, within=1.0):
        return time.monotonic() - self.seen_at < within

    def can_run(self):
        """Someone's in the headset and the tracker is sending."""
        return self.eyes_seen() and time.monotonic() - self.svc.last_sample < 2

    def on_gaze_on(self):
        cal = self.calibrated()
        if cal is False:
            self.start("full", "gaze mode came on without a calibration")
        elif cal is None:
            self.want_full_until = time.monotonic() + 20

    def auto_quick(self, reason):
        now = time.monotonic()
        if self.check or not self.gaze_on or not self.can_run() or self.calibrated() is not True:
            return
        if now - self.last_quick < QUICK_COOLDOWN:
            log(f"quick check skipped ({reason}): one ran {now - self.last_quick:.0f} s ago")
            return
        self.start("quick", reason)

    # --- Running a check ---

    def start(self, kind, reason):
        svc = self.svc
        if self.check:
            return "error a check is running"
        if not self.panel_proc:
            return "error the panel isn't running (gaze/build.sh builds it)"
        if kind == "fit":
            return self.start_fit(reason)
        if not self.can_run():
            return "error the headset is off or the tracker isn't sending"
        own = svc.kind == "own"
        if kind == "full" and own:
            reply = ask(EYES, "calib-start", 3.0)
            if not reply.startswith("ok"):
                log(f"calibration not started: our tracker says {reply or 'nothing'}")
                return f"error our tracker: {reply or 'no reply'}"
        now = time.monotonic()
        self.check = {"kind": kind, "reason": reason, "own": own, "dots": check_dots(kind, own), "i": 0,
                      "started": now, "shown": now, "run": [], "accept": False, "done_at": None, "tries": 0,
                      "skipped": 0, "captured": 0, "points": {}}
        log(f"{kind} check: {reason}")
        if kind == "full":
            st = ask(SCREENS, "state", 0.5).split()
            if len(st) >= 3 and st[0] == "ok":
                self.screens_shown = (st[2] == "0") if st[1] == "always" else (st[2] == "1")
                ask(SCREENS, "hide", 0.5)
        self.to_helper("calpanel 1")
        self.to_panel(f"show {'full' if kind == 'full' else 'quick'}")
        self.show_dot()
        if kind == "quick":
            self.last_quick = now
        return "ok"

    def start_fit(self, reason):
        # Eyes lost are what it's there to show, so it needs only the tracker sending.
        if time.monotonic() - self.sample_at > 2:
            return "error the headset is off or the eye tracker isn't sending"
        now = time.monotonic()
        self.check = {"kind": "fit", "reason": reason, "own": False, "dots": [], "i": 0, "started": now, "shown": now,
                      "run": [], "accept": False, "done_at": None, "tries": 0, "skipped": 0, "captured": 0,
                      "points": {}, "fit": FitCheck(), "drawn": {}, "drawn_at": 0.0, "step": None}
        log(f"fit check: {reason}")
        self.to_helper("calpanel 1")
        self.to_panel("show fit")
        self.to_panel("title Headset fit: adjust the headset while you watch")
        self.to_panel("text Left click or Meta+J: guided check · Right click or Meta+K: done")
        return "ok"

    def fit_tick(self, now):
        c = self.check
        fit = c["fit"]
        # The guided check: its dots at head-relative directions in the panel (the probe's
        # screen fractions, spread over the middle of the panel), and its looks as the title.
        step = fit.guide_step(now)
        key = None if step is None else (step[0], step[1])
        if key != c["step"]:
            c["step"] = key
            if step is None:
                self.to_panel("dot 0 0 off")
                self.to_panel("title Headset fit: adjust the headset while you watch")
            elif step[0] == "dot":
                fx, fy = step[1]
                self.to_panel(f"dot {(0.5 - fx) * 32:.2f} {(0.5 - fy) * 24:.2f} look")
                self.to_panel("title Look at the dot")
            else:
                self.to_panel("dot 0 0 off")
                self.to_panel(f"title {step[1]}")
        if now - c["drawn_at"] < FIT_EVERY:
            return
        c["drawn_at"] = now
        drawn = c["drawn"]
        for k in (0, 1):
            word, (r, g, b) = fit.status(k)
            sig, seen = fit.signal(k), fit.tracked_share(k, now)
            cmd = (f"eye {k} {r:.2f} {g:.2f} {b:.2f} {-1 if sig is None else round(sig, 1):g} "
                   f"{-1 if seen is None else round(seen * 20) / 20:g} {word.capitalize()}")
            if drawn.get(k) != cmd:
                drawn[k] = cmd
                self.to_panel(cmd)
        if fit.have_eye_data and fit.samples < 3 * MIN_REGION:
            hints = ["Look around slowly: up, down, left and right. Or left click (Meta+J) for a guided check."]
        else:
            hints = fit.hints()
        lines = [line for h in hints for line in wrap(h, FIT_HINT_WIDTH)][:8]
        cmd = "hints " + "|".join(lines)
        if drawn.get("hints") != cmd:
            drawn["hints"] = cmd
            self.to_panel(cmd)

    def show_dot(self):
        c = self.check
        yaw, pitch, rnd = c["dots"][c["i"]]
        if c["kind"] == "full":
            self.to_panel(f"bg {ROUND_BG[rnd]}")
            self.to_panel(f"title Gaze calibration: {ROUND_NAMES[rnd]} round, {rnd + 1} of 3")
            self.to_panel("text Look at the dot and click (left click or Meta+J). Right click or Meta+K: stop")
        elif c["kind"] == "five":
            self.to_panel(f"text Look at the dot and click ({c['i'] + 1} of {len(c['dots'])})")
        else:
            self.to_panel("text Look at the dot")
        self.to_panel(f"dot {yaw:.3f} {pitch:.3f} look")
        self.last_progress = None
        c["shown"] = time.monotonic()
        c["run"], c["accept"], c["done_at"] = [], False, None

    def on_sample(self, s):
        self.sample_at = time.monotonic()
        unc = (s["src"].get("mmap1") or {}).get("unc")
        if unc and min(unc) <= EYE_LOST:
            self.seen_at = time.monotonic()
            if self.away and self.back_since is None:
                self.back_since = self.seen_at
        c = self.check
        if c and c["kind"] == "fit":
            c["fit"].feed(s, self.sample_at)
            return
        if not c or c["done_at"]:
            return
        if c["own"]:
            src = s["src"].get("own") or {}
        else:
            src = s["src"].get("mmap1") or {}
            if "hy" not in src:
                per = [s["src"].get(n) or {} for n in ("left", "right")]
                per = [p for p in per if "hy" in p]
                src = {"hy": statistics.fmean(p["hy"] for p in per), "hp": statistics.fmean(p["hp"] for p in per)} if per else {}
        if "hy" not in src:
            return
        now = time.monotonic()
        if now < c["shown"] + CHECK_SETTLE:
            return
        g = (src["hy"], src["hp"])
        run = c["run"]
        if run:
            recent = run[-30:]
            my, mp = statistics.median(p[2] for p in recent), statistics.median(p[3] for p in recent)
            if math.hypot(g[0] - my, g[1] - mp) > 2.5 * CHECK_SPREAD:
                run.clear()  # the eyes moved on (a saccade, a blink): start over
        run.append((now, s, g[0], g[1]))
        window = [p for p in run if p[0] >= now - CHECK_WINDOW]
        held = now - run[0][0]
        # The quick check's ring fills in quarters: each step is a new picture for the panel, so
        # few of them keep it solid. The others wait for the click (see the top): no ring.
        progress = math.floor(min(1.0, held / CHECK_WINDOW) * 4) / 4
        if c["kind"] == "quick" and progress != self.last_progress:
            yaw, pitch, _ = c["dots"][c["i"]]
            self.to_panel(f"dot {yaw:.3f} {pitch:.3f} capture {progress:.2f}")
            self.last_progress = progress
        if c["accept"] and held >= 0.3 and len(window) >= 10:
            sd, _ = spread([(p[2], p[3]) for p in window])
            if sd <= ACCEPT_SPREAD:
                self.capture(window)
            return
        if c["kind"] == "quick" and held >= CHECK_WINDOW and len(window) >= 20:
            sd, _ = spread([(p[2], p[3]) for p in window])
            if sd <= CHECK_SPREAD:
                self.capture(window)
            else:
                del run[:len(run) // 2]  # not steady enough yet: keep trying with the newer half

    def capture(self, window):
        svc = self.svc
        c = self.check
        yaw, pitch, rnd = c["dots"][c["i"]]
        samples = [p[1] for p in window]
        rec = {"time": time.time(), "check": c["kind"], "dot": c["i"], "round": rnd, "true": [yaw, pitch],
               "samples": len(samples), "own": c["own"]}
        ok = True
        if c["own"]:
            eyes = []
            for k in (0, 1):
                seen = [smp["src"]["own"]["eyes"][k] for smp in samples
                        if (smp["src"].get("own") or {}).get("eyes") and smp["src"]["own"]["eyes"][k]]
                eyes.append((statistics.median(e[0] for e in seen), statistics.median(e[1] for e in seen)) if seen else None)
            miss = [math.hypot(yaw - e[0], pitch - e[1]) if e else None for e in eyes]
            rec.update(eyes=eyes, miss=miss)
            t0, t1 = samples[0]["t"], samples[-1]["t"]
            if c["kind"] == "full":
                reply = ask(EYES, f"calib-point {t0:.6f} {t1:.6f} {yaw:.4f} {pitch:.4f}", 3.0)
                rec["reply"] = reply
                ok = reply.startswith("ok")
            else:
                try:
                    svc.eyes_sock.sendto(f"click {t1:.6f} {yaw:.4f} {pitch:.4f}".encode(), EYES)
                except OSError as e:
                    rec["reply"], ok = f"our tracker isn't running ({e})", False
            if ok:
                svc.weights["own"].add(miss)
        else:
            steady = steady_samples(samples)
            reads = {}
            for name in ("action", "mmap1", "mmap2", "left", "right"):
                pts = [(smp["src"][name]["hy"], smp["src"][name]["hp"]) for smp in steady
                       if "hy" in (smp["src"].get(name) or {})]
                if len(pts) >= 15:
                    reads[name] = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
            rec["reads"] = reads
            main = ("left", "right") if svc.kind == "eyes" else (svc.source,)
            if not all(n in reads for n in main):
                ok = False
                rec["reply"] = f"only {len(steady)} of {len(samples)} samples had both eyes"
            elif c["kind"] == "full":
                for name, (hy, hp) in reads.items():
                    c["points"].setdefault(name, []).append((hy, hp, yaw - hy, pitch - hp))
                try:
                    with open(POINTS, "a") as f:
                        for name, (hy, hp) in reads.items():
                            f.write(json.dumps({"time": time.time(), "source": name, "hy": hy, "hp": hp,
                                                "off": [yaw - hy, pitch - hp], "layout": None, "how": "panel"}) + "\n")
                except OSError:
                    pass
            else:
                miss = []
                for name in main:
                    hy, hp = reads[name]
                    cy, cp = svc.correction(name, hy, hp)
                    miss.append(math.hypot(yaw - hy - cy, pitch - hp - cp))
                    svc.lives[name].add({"time": time.time(), "hy": hy, "hp": hp, "dy": yaw - hy, "dp": pitch - hp,
                                         "wy": 1.0, "wp": 1.0, "how": "check"}, svc.models[name], svc.mode)
                rec["miss"] = miss
                if svc.kind == "eyes":
                    svc.weights["steam"].add(miss)
                svc.dirty = True
        rec["accepted"] = ok
        self.log_check(rec)
        if not ok:
            c["tries"] += 1
            log(f"{c['kind']} check dot {c['i'] + 1}: not taken ({rec.get('reply', '')})")
            if c["tries"] >= 2 or c["kind"] != "full":
                self.skip()
            else:
                self.to_panel(f"dot {yaw:.3f} {pitch:.3f} fail")
                c["run"], c["accept"] = [], False
                c["shown"] = time.monotonic()  # settle again, then retry
            return
        c["captured"] += 1
        c["tries"] = 0
        self.to_panel(f"dot {yaw:.3f} {pitch:.3f} done")
        c["done_at"] = time.monotonic() + DONE_PAUSE

    def skip(self):
        c = self.check
        yaw, pitch, _ = c["dots"][c["i"]]
        c["skipped"] += 1
        c["tries"] = 0
        self.to_panel(f"dot {yaw:.3f} {pitch:.3f} fail")
        c["done_at"] = time.monotonic() + DONE_PAUSE

    def advance(self):
        c = self.check
        c["i"] += 1
        if c["i"] < len(c["dots"]):
            self.show_dot()
            return
        self.finish()

    def finish(self):
        svc = self.svc
        c = self.check
        kind = c["kind"]
        if kind in ("quick", "five"):
            if c["captured"]:
                log(f"{kind} check done ({c['captured']} of {len(c['dots'])} dots)")
                self.after_quick = [] if kind == "quick" else None
            self.close()
            return
        n = len(c["dots"])
        if c["captured"] < n * 2 / 3:
            log(f"calibration failed: only {c['captured']} of {n} dots; the old one stays")
            self.to_panel(f"text Calibration failed: only {c['captured']} of {n} dots. Try again from Input Settings")
            self.to_panel("dot 0 0 off")
            c["done_at"] = time.monotonic() + 3.0
            c["closing"] = True
            return
        if c["own"]:
            reply = ask(EYES, "calib-fit", 10.0)
            log(f"calibration ({c['captured']} of {n} dots): our tracker says {reply or 'nothing'}")
        else:
            mode = svc.mode if svc.mode != "none" else DEFAULT_MODEL
            for name, pts in c["points"].items():
                if name in svc.models and pts:
                    svc.models[name].fit(pts, mode)
            d = {name: m.to_json() for name, m in svc.models.items()}
            d["_meta"] = {"calibrated_at": time.time(), "model": mode, "how": "panel"}
            d["_live"] = {}
            tmp = CALIBRATION.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1))
            tmp.replace(CALIBRATION)
            svc.forget_lessons()  # a new calibration: the pointer's lessons start over on top of it
            svc.load_calibration()
            svc.refit()
            log(f"calibration ({c['captured']} of {n} dots): {mode}, saved")
        self.close()

    def close(self, why=None):
        if not self.check:
            return
        if why:
            log(f"{self.check['kind']} check closed: {why}")
        self.to_panel("hide")
        self.to_helper("calpanel 0")
        if self.check["kind"] == "full" and self.screens_shown:
            ask(SCREENS, "show", 0.5)
        self.screens_shown = None
        self.check = None

    def quit(self):
        c = self.check
        if not c:
            return
        self.close("quit")
        if c["kind"] == "full" and self.calibrated() is False:
            # No calibration still: gaze mode can't work, so it goes off until it's turned on again.
            try:
                write_gaze(False)
            except OSError as e:
                log(f"can't write {CONF}: {e}")
            self.to_helper("gaze off")
            self.gaze_on = False
            log("gaze mode off: no calibration")

    def log_check(self, rec):
        try:
            with open(CHECK_LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    # --- From the service ---

    def command(self, words):
        """quickcal, calibrate, calaccept, calquit -> a reply."""
        cmd = words[0]
        if cmd == "quickcal":
            return self.start("full" if self.calibrated() is False else "quick", "asked for")
        if cmd == "calibrate":
            return self.start("full", "asked for")
        if cmd == "fitcheck":
            return self.start("fit", "asked for")
        if cmd == "calaccept":
            if self.check and self.check["kind"] == "fit":
                self.check["fit"].toggle_guide(time.monotonic())
            elif self.check:
                self.check["accept"] = True
            return "ok"
        if cmd == "calquit":
            self.quit()
            return "ok"
        if cmd == "recheck" and len(words) == 2:
            self.auto_quick(f"a click was corrected {words[1]} deg")
            return "ok"
        return "error unknown command"

    def after_lesson(self, rec):
        if self.after_quick is None or "refused" in rec:
            return
        self.after_quick.append(rec.get("lesson_deg", 0.0))
        if len(self.after_quick) < FIVE_COUNT:
            return
        off = self.after_quick
        self.after_quick = None
        if all(d > FIVE_LIMIT for d in off):
            log(f"the {FIVE_COUNT} lessons after the quick check were {', '.join(f'{d:.1f}' for d in off)} deg off")
            if self.gaze_on and self.can_run() and not self.check:
                self.start("five", "the quick check didn't fix it")

    def tick(self):
        c = self.check
        if not c:
            return
        now = time.monotonic()
        if c["kind"] == "fit":
            # Not closed when the eyes go: adjusting the headset loses them.
            if now - c["started"] > FIT_TIMEOUT:
                self.close("timed out")
            else:
                self.fit_tick(now)
            return
        if c["done_at"] and now >= c["done_at"]:
            if c.get("closing"):
                self.close()
            else:
                self.advance()
            return
        if not self.eyes_seen(EYES_GONE):
            self.close("the headset came off")
            return
        if c["done_at"]:
            return
        if c["kind"] == "quick" and now - c["started"] > QUICK_TIMEOUT:
            self.close("ignored")
        elif c["kind"] != "quick" and now - c["shown"] > CLICK_IDLE:
            self.close(f"no click on dot {c['i'] + 1} for {CLICK_IDLE:.0f} s")

    def periodic(self):
        svc = self.svc
        now = time.monotonic()
        if not self.panel_proc and now >= self.panel_restart_at:
            self.start_panel()
        self.to_helper("gaze ?")
        if now - self.gaze_heard > 5:
            self.gaze_on = None  # the helper isn't answering
        if self.check:
            self.to_helper("calpanel 1")
        if self.want_full_until:
            cal = self.calibrated()
            if cal is not None or now > self.want_full_until:
                self.want_full_until = 0.0
                if cal is False and self.gaze_on:
                    self.start("full", "gaze mode came on without a calibration")
        if not self.eyes_seen(AWAY_MIN):
            self.away, self.back_since = True, None
        elif self.back_since is not None and not self.eyes_seen():
            self.back_since = None  # gone again before DON_DELAY
        elif self.back_since is not None and now - self.back_since >= DON_DELAY:
            self.away, self.back_since = False, None
            self.auto_quick("the headset went on")
        if svc.kind == "own" and now - svc.own_at < 5:
            reseat = any(e.get("reseat") for e in (svc.own.get("eyes") or {}).values())
            if not reseat:
                self.reseat_seen = False
            elif not self.reseat_seen and self.can_run():
                self.reseat_seen = True
                self.auto_quick("our tracker asked for a click")

    def status(self):
        c = self.check
        st = {"check": None, "gaze_mode": self.gaze_on, "calibrated": self.calibrated(), "eyes": self.eyes_seen(),
              "panel": self.panel_proc is not None,
              "last_quick_s": round(time.monotonic() - self.last_quick) if self.last_quick else None}
        if c:
            st["check"] = {"kind": c["kind"], "dot": c["i"] + 1, "dots": len(c["dots"]), "captured": c["captured"],
                           "skipped": c["skipped"], "reason": c["reason"]}
        return st

    def stop(self):
        self.close("the gaze service is stopping")
        self.stop_panel()
