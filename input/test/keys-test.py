#!/usr/bin/env python3
"""Offline test of the input relay's key combinations and modifier taps.

Runs the relay's main() against a fake pass-through keyboard and a fake mouse (pipes), with
every socket it sends to renamed, no uinput devices, no grabs, and ft-steam swapped for a
logger. Pausing (game_pause.py) is a stub: no threads, state file, or services. Nothing reaches
the live desktop, SteamVR, or the running relay, so it's safe next to them. Rules come from the
test, not ~/.config/frametop-input.json.

  input/test/keys-test.py [RELAY]   (default: input/input-relay.py next to this folder)
"""
import fcntl
import importlib.util
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
relay_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "input-relay.py")
tag = f"ft_keys_test_{os.getpid()}"
with open(relay_path) as f:
    src = f.read().replace('"\\0frametop_relay"', f'"\\0{tag}_relay"')
relay = importlib.util.module_from_spec(importlib.util.spec_from_loader("relay", loader=None))
relay.__file__ = os.path.abspath(relay_path)
sys.path.insert(0, os.path.dirname(os.path.abspath(relay_path)))  # its game_pause and vrws
exec(compile(src, relay_path, "exec"), relay.__dict__)


class StubPause:
    """game_pause.GamePause without its threads, state file, or services."""
    paused = False

    def __init__(self, log, on_change, buttons):
        self.on_change = on_change
        stub["pause"] = self

    def toggle(self, reason, now):
        self.paused = not self.paused
        self.on_change(self.paused)

    def set(self, on, reason, now):
        if on != self.paused:
            self.toggle(reason, now)

    def status(self):
        return {"t": "pause", "paused": self.paused}

    def timeout(self, now):
        return 3600.0

    def configure(self, *args):
        pass

    game_state = tick = helper_started = controllers_changed = configure


stub = {}
relay.game_pause.GamePause = StubPause

for name in ("SCREENS", "HELPER", "FLOAT", "GAZED", "KEYS"):
    setattr(relay, name, f"\0{tag}_{name.lower()}")
OUT = tempfile.mkdtemp(prefix="ft-keys-test-")
steam_log, cmd_log = f"{OUT}/steam.log", f"{OUT}/cmd.log"
relay.FT_STEAM = f"{OUT}/ft-steam"
with open(relay.FT_STEAM, "w") as f:
    f.write(f'#!/bin/sh\necho "$@" >> {steam_log}\n')
os.chmod(relay.FT_STEAM, 0o755)


class NoDevice:
    def __init__(self, *args, **kwargs):
        pass

    def emit(self, *args):
        pass

    def sync(self):
        pass


relay.Virtual = NoDevice
relay.log = lambda *args: None  # the relay's own log
bindings = {"now": None}  # the rules' key_bindings; None: the relay's defaults


def read_rules(path=None):
    rules = {"devices": {}, "buttons": {}, "controller_buttons": {}}
    rules["key_bindings"] = dict(relay.DEFAULT_KEY_BINDINGS if bindings["now"] is None else bindings["now"])
    return rules


relay.read_rules = read_rules
relay.read_config = lambda path=None: {"POINTER": "0"}

# The fake devices: /dev/input/event900 (keyboard) and event901 (mouse), each a pipe.
kb_r, kb_w = os.pipe()
ms_r, ms_w = os.pipe()
for fd in (kb_r, ms_r):
    os.set_blocking(fd, False)
FAKE = {"/dev/input/event900": kb_r, "/dev/input/event901": ms_r}
HELD = {kb_r: set(), ms_r: set()}  # what EVIOCGKEY says each holds


class Inode:
    st_ino = 1


fake_os = type(os)("os")
fake_os.__dict__.update(os.__dict__)
fake_os.listdir = lambda path: ["event900", "event901"] if path == "/dev/input" else os.listdir(path)
fake_os.stat = lambda path, *a, **k: Inode() if path in FAKE else os.stat(path, *a, **k)
fake_os.access = lambda path, mode, *a, **k: path in FAKE or os.access(path, mode, *a, **k)
relay.os = fake_os


def ioctl(fd, request, arg=0, *rest):
    if fd in HELD and request == relay.EVIOCGKEY:
        for c in HELD[fd]:
            arg[c // 8] |= 1 << (c % 8)
        return 0
    return fcntl.ioctl(fd, request, arg, *rest)


fake_fcntl = type(fcntl)("fcntl")
fake_fcntl.__dict__.update(fcntl.__dict__)
fake_fcntl.ioctl = ioctl
relay.fcntl = fake_fcntl


def probe(path):
    if FAKE[path] == kb_r:
        return relay.Node(path, kb_r, "test keyboard", relay.BUS_USB, 1, 2, "", False, True)
    return relay.Node(path, ms_r, "test mouse", relay.BUS_USB, 3, 4, "", True, False)


relay.probe = probe

screens = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
screens.bind(relay.SCREENS)
screens.settimeout(0.05)
EVENT = struct.Struct("llHHi")


def send(fd, etype, code, value):
    os.write(fd, EVENT.pack(0, 0, etype, code, value) + EVENT.pack(0, 0, 0, 0, 0))
    time.sleep(0.03)


def key(code, value, fd=kb_w):
    held = HELD[ms_r if fd == ms_w else kb_r]
    (held.add if value else held.discard)(code)
    send(fd, relay.EV_KEY, code, value)


def typed():
    """What the desktop was sent since the last call."""
    got = []
    while True:
        try:
            got.append(screens.recv(256).decode())
        except socket.timeout:
            return got


def use(key_bindings):
    bindings["now"] = key_bindings
    c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    c.bind("")
    c.settimeout(2)
    c.sendto(b"reload", f"\0{tag}_relay")
    c.recv(4096)
    time.sleep(0.05)
    typed()


def pause(cmd):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    c.bind("")
    c.settimeout(2)
    c.sendto(f"pause {cmd} test".encode(), f"\0{tag}_relay")
    c.recv(4096)
    time.sleep(0.05)


def lines(path, wait=0.3):
    time.sleep(wait)
    try:
        with open(path) as f:
            return f.read().split()
    except OSError:
        return []


failures = []


def check(label, got, want):
    ok = got == want
    print(("ok    " if ok else "FAIL  ") + label + ("" if ok else f": got {got!r}, want {want!r}"), flush=True)
    if not ok:
        failures.append(label)


META, RMETA, SHIFT, CTRL, RCTRL, ALT, A, F, J, P, TAB = 125, 126, 42, 29, 97, 56, 30, 33, 36, 25, 15
F24 = ["key 194 1", "key 194 0"]


def tests():
    time.sleep(1.5)  # the relay's first device scan
    typed()
    key(META, 1); key(META, 0)
    check("Meta tap (default): Steam menu", lines(steam_log), ["menu"])
    check("Meta tap: the desktop gets F24 before Meta's release", typed(), ["key 125 1"] + F24 + ["key 125 0"])

    key(RMETA, 1); key(RMETA, 2); key(RMETA, 2); key(RMETA, 0)
    check("right Meta held (autorepeat), then released: a tap", lines(steam_log), ["menu", "menu"])
    typed()

    key(META, 1); key(A, 1); key(A, 0); key(META, 0)
    check("Meta+A: no tap", lines(steam_log), ["menu", "menu"])
    check("Meta+A (unbound) is typed as is", typed(), ["key 125 1", "key 30 1", "key 30 0", "key 125 0"])

    key(META, 1); key(relay.BTN_LEFT, 1, ms_w); key(relay.BTN_LEFT, 0, ms_w); key(META, 0)
    check("Meta+click: no tap", lines(steam_log), ["menu", "menu"])
    key(META, 1); send(ms_w, relay.EV_REL, relay.REL_WHEEL, 1); key(META, 0)
    check("Meta+scroll: no tap", lines(steam_log), ["menu", "menu"])
    key(META, 1); key(SHIFT, 1); key(SHIFT, 0); key(META, 0)
    check("Meta+Shift: no tap", lines(steam_log), ["menu", "menu"])
    typed()

    key(META, 1); key(J, 1); key(J, 0); key(META, 0)
    check("Meta+J (default gaze click): no tap", lines(steam_log), ["menu", "menu"])
    check("Meta+J: F24, Meta up early, its real release dropped", typed(), ["key 125 1"] + F24 + ["key 125 0"])

    use({"29+42+33": f"command:echo combo >> {cmd_log}", "125": "none"})
    key(CTRL, 1); key(SHIFT, 1); key(F, 1); key(F, 0); key(SHIFT, 0); key(CTRL, 0)
    check("Ctrl+Shift+F runs its command", lines(cmd_log), ["combo"])
    check("the combination's last key isn't typed", [k for k in typed() if k.startswith("key 33 ")], [])
    key(META, 1); key(META, 0)
    check("Meta tap bound to none: nothing", lines(steam_log), ["menu", "menu"])
    check("Meta tap bound to none: no F24", typed(), ["key 125 1", "key 125 0"])

    use({"29": f"command:command -v ft-steam ft-layout ft-float | wc -l >> {cmd_log}"})
    key(RCTRL, 1); key(RCTRL, 0)
    check("right Ctrl tap runs its command, which finds Frametop's tools", lines(cmd_log), ["combo", "3"])
    check("Ctrl tap: F24 before Ctrl's release", typed(), ["key 97 1"] + F24 + ["key 97 0"])

    use({"125+36": "gaze_left", "29": f"command:echo paused >> {cmd_log}", "42+125+25": "pause_toggle"})
    pause("on")
    check("pause on: paused", stub["pause"].paused, True)
    key(META, 1); key(J, 1); key(J, 0); key(META, 0)
    check("paused: Meta+J (gaze click) does nothing and is typed as is", typed(),
          ["key 125 1", "key 36 1", "key 36 0", "key 125 0"])
    key(RCTRL, 1); key(RCTRL, 0)
    check("paused: a command still runs", lines(cmd_log), ["combo", "3", "paused"])
    typed()
    key(SHIFT, 1); key(META, 1); key(P, 1); key(P, 0); key(META, 0); key(SHIFT, 0)
    check("paused: Meta+Shift+P (pause_toggle) resumes", stub["pause"].paused, False)
    check("Meta+Shift+P isn't typed", [k for k in typed() if k.startswith("key 25 ")], [])
    key(META, 1); key(J, 1); key(J, 0); key(META, 0)
    check("resumed: Meta+J is a combination again", typed(), ["key 125 1"] + F24 + ["key 125 0"])

    use(None)  # the defaults: Meta+Alt+Tab and Meta+Alt+Shift+Tab spin the panels (ft-screens)
    key(META, 1); key(ALT, 1); key(TAB, 1); key(TAB, 0); key(ALT, 0); key(META, 0)
    got = typed()
    check("Meta+Alt+Tab (default): spin next", [m for m in got if m.startswith("spin")], ["spin next"])
    check("Meta+Alt+Tab: Tab isn't typed", [m for m in got if m.startswith("key 15 ")], [])
    key(META, 1); key(ALT, 1); key(SHIFT, 1); key(TAB, 1); key(TAB, 0); key(SHIFT, 0); key(ALT, 0); key(META, 0)
    check("Meta+Alt+Shift+Tab (default): spin prev", [m for m in typed() if m.startswith("spin")], ["spin prev"])
    pause("on")
    key(META, 1); key(ALT, 1); key(TAB, 1); key(TAB, 0); key(ALT, 0); key(META, 0)
    check("paused: Meta+Alt+Tab doesn't spin", [m for m in typed() if m.startswith("spin")], [])
    pause("off")
    check("spinning works without pointer mode", (relay.needs_pointer("spin_next"), relay.needs_pointer("spin_prev")),
          (False, False))

    check("an empty command isn't an action", relay.known_action("command:  "), False)
    check("steam_menu, pause_toggle and commands work without pointer mode",
          (relay.needs_pointer("steam_menu"), relay.needs_pointer("pause_toggle"), relay.needs_pointer("command:ls")),
          (False, False, False))
    print("FAILED: " + ", ".join(failures) if failures else "all passed", flush=True)
    shutil.rmtree(OUT, ignore_errors=True)
    os._exit(1 if failures else 0)


threading.Thread(target=tests, daemon=True).start()
sys.argv = [relay_path, "--no-grab"]
relay.main()
