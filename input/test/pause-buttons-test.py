#!/usr/bin/env python3
"""Offline test of the relay's pointer standing down when Frametop pauses: a driver button
(a gaze drag, a gazekey click) that was still down when the pause started is released by
stand_down, instead of staying down until resume. The pointer's socket is a recorder, so
nothing reaches the helper, SteamVR, or the running relay.

  input/test/pause-buttons-test.py [RELAY]
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
relay_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "input-relay.py")
tag = f"ft_pause_buttons_test_{os.getpid()}"
src = open(relay_path).read().replace('"\\0frametop_relay"', f'"\\0{tag}_relay"')
# macOS has no SOCK_NONBLOCK; the pointer's socket is replaced below anyway.
src = src.replace("socket.SOCK_DGRAM | socket.SOCK_NONBLOCK", "socket.SOCK_DGRAM")
relay = importlib.util.module_from_spec(importlib.util.spec_from_loader("relay", loader=None))
relay.__file__ = os.path.abspath(relay_path)
sys.path.insert(0, os.path.dirname(os.path.abspath(relay_path)))
exec(compile(src, relay_path, "exec"), relay.__dict__)

failures = []


def check(label, got, want):
    ok = got == want
    print(("ok    " if ok else "FAIL  ") + label + ("" if ok else f": got {got!r}, want {want!r}"), flush=True)
    if not ok:
        failures.append(label)


class Recorder:
    """Stands in for the helper's socket: remembers every command, in order."""

    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data.decode())


def pointer():
    p = relay.Pointer(0.02, 3600.0)
    p.sock = Recorder()
    return p


# ---------------------------------------------------------------- the fix
p = pointer()
now = 10.0
p.action("left", 1, now)  # a gaze drag: btn trigger 1, held
check("press reaches the helper", p.sock.sent, ["show", "recenter", "btn trigger 1"])
check("the pointer knows the button is down", p.driver_down, {"trigger"})

p.stand_down()  # Frametop pauses while it's held
check("stand_down releases the held button",
      [s for s in p.sock.sent if s.startswith("btn")], ["btn trigger 1", "btn trigger 0"])
check("nothing stays marked down", p.driver_down, set())

# The release that pausing drops later is already covered: the button is no longer down,
# so a stray release during the pause sends nothing extra.
sent = len(p.sock.sent)
p.stand_down()
check("a second stand_down sends nothing more", len(p.sock.sent), sent)

# ---------------------------------------------------------------- the ordinary path
p = pointer()
p.action("left", 1, 10.0)
p.action("left", 0, 11.0)  # released before the pause
check("a released button isn't tracked", p.driver_down, set())
p.stand_down()
check("stand_down sends no release for it",
      [s for s in p.sock.sent if s.startswith("btn")], ["btn trigger 1", "btn trigger 0"])

# Two buttons down at once (chord: gazekey click during a head drag)
p = pointer()
p.action("left", 1, 10.0)
p.action("right", 1, 10.5)
p.stand_down()
check("both held buttons are released",
      sorted(s for s in p.sock.sent if s.startswith("btn")),
      sorted(["btn trigger 1", "btn b 1", "btn trigger 0", "btn b 0"]))

print()
if failures:
    print(f"{len(failures)} failed: {', '.join(failures)}")
    sys.exit(1)
print("all ok")
