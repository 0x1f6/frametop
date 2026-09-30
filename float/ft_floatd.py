#!/usr/bin/env python3
"""ft-floatd: floating windows for the Frametop desktop (see docs/floating-windows.md).

Runs inside the desktop's Plasma session (its D-Bus and Wayland). It keeps the table of
which window floats on which spare output and panel, and connects three parts:
  - the KWin script frametop-float (float/frametop-float.js), which it loads into KWin. The
    script sends events over D-Bus (org.frametop.Float.Event) and fetches commands with a
    long poll (NextCommand).
  - ft-screens, through its control socket (@ft_screens): the spare output's size, and the
    floating panel's crop, density, place, and popups.
  - kscreen-doctor, to turn spare outputs on and off and place them in KWin's layout.
Commands and ft-screens' events arrive as datagrams on @frametop_float (ft-float is the
command-line side). Replies go to the sender:
  float [ID|active]   dock [ID|active]   close ID   list   quit          (ft-float)
  dock N | close N | resize N W H | carried N                          (ft-screens, N = its screen)

Spare outputs are WL-<screens> .. WL-<screens + slots - 1>. A floating window's output is its
frame plus a margin on each side (FLOAT_MARGIN pixels), so menus have room; the panel shows
only the window. Spares are placed apart from the screens and from each other in KWin's
layout, all within Xwayland's 32767-pixel limit.

Usage: ft-floatd [--screens N] [--slots N] [--margin PX] [--control NAME] [--socket NAME]
Defaults: FT_SCREEN_COUNT (from the session) or the layout's count, FLOAT_SLOTS and
FLOAT_MARGIN from ~/.config/frametop.conf (8 and 300), @ft_screens, @frametop_float.
"""
import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "layout"))
import ft_layout  # noqa: E402  (config and layout)

SERVICE = IFACE = "org.frametop.Float"
PATH = "/Float"
SCRIPT = "frametop-float"
POLL_SECONDS = 20        # NextCommand answers empty after this (KWin's D-Bus timeout is 25 s)
SPARE_X, SPARE_CELL = 12000, 5000  # spares in KWin's layout: a grid from here, 4 across
PULL_OUT = 0.05          # a floated window starts this far in front of its screen (metres)
DEFAULT_MPP = 1.6 / 1920  # metres per pixel when ft-screens can't say (no SteamVR)


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


class Screens:
    """ft-screens' control socket."""

    def __init__(self, name):
        self.address = "\0" + name
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind("")

    def ask(self, text, quiet=False):
        self.sock.settimeout(2.0)
        try:
            self.sock.sendto(text.encode(), self.address)
            reply = self.sock.recv(8192).decode()
        except OSError as e:
            reply = f"error no answer ({e})"
        if not reply.startswith("ok") and not quiet:
            log(f"ft-screens: {text}: {reply}")
        return reply


def kscreen(*args):
    try:
        r = subprocess.run(["kscreen-doctor", *args], capture_output=True, text=True, timeout=20)
        return r.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"kscreen-doctor: {e}")
        return ""


def output_scales():
    try:
        data = json.loads(kscreen("-j") or "{}")
    except ValueError:
        return {}
    return {o["name"]: float(o.get("scale", 1)) for o in data.get("outputs", []) if o.get("name")}


class Float:
    """A floating window."""

    def __init__(self, wid, slot, saved):
        self.id = wid
        self.slot = slot          # Slot
        self.saved = saved        # where it came from: output, frame, onAllDesktops
        self.scale = 1.0          # its output's scale (pixels per logical unit)
        self.mpp = DEFAULT_MPP    # metres per pixel on its panel
        self.frame = None         # last frame (logical, global)
        self.client = None
        self.full = False         # full screen: no margin
        self.move_from = None     # frame when a title-bar move started (put back after)
        self.subs = {}            # popup or dialog id -> number on the panel


class Slot:
    def __init__(self, k, screens):
        self.k = k
        self.output = f"WL-{screens + k}"
        self.index = screens + k + 1  # ft-screens' number (1-based)
        self.pos = (SPARE_X + (k % 4) * SPARE_CELL, (k // 4) * SPARE_CELL)
        self.size = None              # its output's size in pixels, as last set
        self.window = None            # Float


class Daemon:
    def __init__(self, args):
        self.screens_n = args.screens
        self.margin = args.margin
        self.slots = [Slot(k, args.screens) for k in range(args.slots)]
        self.floats = {}         # window id -> Float
        self.windows = {}        # window id -> last info from the script
        self.pending = []        # commands for the script
        self.waiter = None       # (reply callback, timeout source) while the script waits
        self.screens = Screens(args.control)
        self.sub_numbers = {}    # popup/dialog id -> (window id, number)
        self.next_sub = 1

    # ------------------------------------------------------------ the script

    def command(self, **cmd):
        self.pending.append(cmd)
        self.flush()

    def flush(self):
        if not self.waiter or not self.pending:
            return
        reply, source = self.waiter
        self.waiter = None
        GLib.source_remove(source)
        text, self.pending = json.dumps(self.pending), []
        reply(text)

    def wait(self, reply):
        if self.waiter:  # a stale poll (the script reloaded): let it go
            old, source = self.waiter
            GLib.source_remove(source)
            old("")

        def timeout():
            if self.waiter and self.waiter[0] is reply:
                self.waiter = None
                reply("")
            return False
        self.waiter = (reply, GLib.timeout_add_seconds(POLL_SECONDS, timeout))
        self.flush()

    def load_script(self, bus):
        kwin = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting")
        if kwin.isScriptLoaded(SCRIPT):
            kwin.unloadScript(SCRIPT)
        sid = int(kwin.loadScript(os.path.join(HERE, "frametop-float.js"), SCRIPT, signature="ss"))
        if sid < 0:
            raise RuntimeError("KWin didn't load the script")
        bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}").run(dbus_interface="org.kde.kwin.Script")
        log(f"script loaded ({sid})")

    # ------------------------------------------------------------ events from the script

    def on_event(self, ev):
        kind = ev.get("ev")
        wid = ev.get("id", "")
        if kind == "hello":
            self.command(cmd="config", screens=self.screens_n)
            # The script reports every window after "config"; spares nothing floats on are off.
            GLib.timeout_add(1500, self.disable_unused)
            return
        if kind == "removed":
            self.windows.pop(wid, None)
            if wid in self.floats:
                log(f"{wid[:9]} closed")
                self.release(self.floats.pop(wid))
            self.drop_sub(wid)
            return
        if wid:
            self.windows[wid] = ev
        f = self.floats.get(wid)
        if kind == "float-request":
            self.float_window(ev)
        elif kind == "dock-request":
            if f:
                self.dock(f)
        elif kind in ("added", "window", "output"):
            self.seen(ev)
        elif kind == "geometry":
            if f:
                self.follow(f, ev)
            else:
                self.sub(ev)
        elif kind == "move-start" and f and ev.get("move"):
            f.move_from = ev["frame"]
            self.screens.ask(f"carry {f.slot.index}", quiet=True)
        elif kind == "move-end" and f and f.move_from:
            # The panel carried the window; KWin may have slipped it a few pixels first.
            m, f.move_from = f.move_from, None
            if (m["x"], m["y"]) != (ev["frame"]["x"], ev["frame"]["y"]):
                self.command(cmd="geometry", id=f.id, x=m["x"], y=m["y"], w=ev["frame"]["w"], h=ev["frame"]["h"])
        elif kind == "fullscreen" and f:
            f.full = bool(ev.get("fullScreen"))
            self.follow(f, ev, refit=True)
        elif kind == "minimized" and f:
            self.screens.ask(f"minimized {f.slot.index} {1 if ev.get('minimized') else 0}", quiet=True)

    def spare_slot(self, output):
        for s in self.slots:
            if s.output == output:
                return s
        return None

    def seen(self, ev):
        """A window the script told us about: is it somewhere it shouldn't be?"""
        wid = ev["id"]
        slot = self.spare_slot(ev.get("output", ""))
        f = self.floats.get(wid)
        if f and f.slot is not slot and ev["ev"] == "output":
            # Left its spare (KWin moved it, or docking): it's not floating any more.
            if slot is None:
                log(f"{wid[:9]} left its floating output")
                del self.floats[wid]
                self.release(f)
            return
        if slot is None or f:
            return
        if ev.get("popup") or (ev.get("transient") and ev.get("parent") in self.floats):
            self.sub(ev)
            return
        if not ev.get("normal"):
            return
        if slot.window is None:
            # Floating when ft-floatd (re)started: take it over where it is.
            f = Float(wid, slot, None)
            slot.window = f
            self.floats[wid] = f
            f.scale = output_scales().get(slot.output, 1.0)
            log(f"{wid[:9]} already floats on {slot.output}")
            self.follow(f, ev, refit=True)
            return
        # A new window that opened on a floating window's output: windows of floating apps
        # float too; anything else goes to the screens.
        if ev["ev"] == "added" and any(o.saved is not None and self.windows.get(o.id, {}).get("pid") == ev.get("pid")
                                       for o in self.floats.values()):
            self.float_window(ev)
        else:
            self.command(cmd="place", id=wid, output="WL-0", x=ev["frame"]["x"] % 400 + 100,
                         y=ev["frame"]["y"] % 300 + 100, w=ev["frame"]["w"], h=ev["frame"]["h"])

    # ------------------------------------------------------------ floating and docking

    def disable_unused(self):
        off = [f"output.{s.output}.disable" for s in self.slots if s.window is None]
        if off:
            kscreen(*off)
        return False

    def free_slot(self):
        for s in self.slots:
            if s.window is None:
                return s
        return None

    def screen_mpp(self, output):
        """Metres per pixel on the screen showing this output, from ft-screens."""
        m = re.match(r"WL-(\d+)$", output or "")
        reply = self.screens.ask("screens", quiet=True)
        if m and reply.startswith("ok"):
            for part in reply.split()[2:]:
                idx, size, metres = part.split(":")
                if int(idx) == int(m.group(1)) + 1:
                    return float(metres) / max(1, int(size.split("x")[0]))
        return DEFAULT_MPP

    def float_window(self, ev):
        wid = ev["id"]
        if wid in self.floats:
            return
        slot = self.free_slot()
        if slot is None:
            self.notify(f"All {len(self.slots)} floating windows are in use. Put one back on the desktop "
                        "to float another.")
            return
        f = Float(wid, slot, {"output": ev["output"], "frame": ev["frame"], "onAllDesktops": ev.get("onAllDesktops")})
        slot.window = f
        self.floats[wid] = f
        f.scale = output_scales().get(ev["output"], 1.0)
        f.mpp = self.screen_mpp(ev["output"])
        fr, s, m = ev["frame"], f.scale, self.margin
        w, h = round(fr["w"] * s), round(fr["h"] * s)
        slot.size = (w + 2 * m, h + 2 * m)
        log(f"{wid[:9]} ({ev.get('cls')}) floats on {slot.output}: {w}x{h} px, scale {s:g}")
        # The spare's size first (while it's off, so its first frame is right), then its panel,
        # then turn it on, then the window.
        self.screens.ask(f"size {slot.index} {slot.size[0]} {slot.size[1]}")
        self.screens.ask(f"scale {slot.index} {s:g}")  # for pointer positions (KWin's units)
        self.set_panel(f, (m, m, w, h), title=round((ev["client"]["y"] - fr["y"]) * s))
        self.place_panel(f, ev)
        kscreen(f"output.{slot.output}.enable", f"output.{slot.output}.scale.{s:g}",
                f"output.{slot.output}.position.{slot.pos[0]},{slot.pos[1]}")
        self.command(cmd="place", id=wid, output=slot.output, x=slot.pos[0] + m / s, y=slot.pos[1] + m / s,
                     w=fr["w"], h=fr["h"], onAllDesktops=True)

    def set_panel(self, f, crop, title=0):
        x, y, w, h = crop
        self.screens.ask(f"float {f.slot.index} {f.mpp:.7f} {x} {y} {w} {h} {title}")

    def place_panel(self, f, ev):
        """Put the panel where the window was on its screen, a little in front of it."""
        m = re.match(r"WL-(\d+)$", ev["output"])
        reply = self.screens.ask(f"get {int(m.group(1)) + 1}", quiet=True) if m else ""
        if not reply.startswith("ok"):
            return
        g = ft_layout.parse_get(reply)
        c, xa, ya, za = g["center"], g["x"], g["y"], g["z"]
        out, fr, s = ev["outputRect"], ev["frame"], f.scale
        # The window's centre relative to the screen's, in panel pixels, then metres.
        dx = ((fr["x"] - out["x"]) + fr["w"] / 2 - out["w"] / 2) * s * f.mpp
        dy = ((fr["y"] - out["y"]) + fr["h"] / 2 - out["h"] / 2) * s * f.mpp
        p = [c[k] + xa[k] * dx - ya[k] * dy + za[k] * PULL_OUT for k in range(3)]
        rows = [f"{xa[k]:.5f} {ya[k]:.5f} {za[k]:.5f} {p[k]:.4f}" for k in range(3)]
        self.screens.ask(f"pose {f.slot.index} {' '.join(rows)}")

    def follow(self, f, ev, refit=False):
        """The window moved or resized on its output: crop the panel to it, and keep the
        output its size plus the margin."""
        slot, s = f.slot, f.scale
        fr, cl, out = ev["frame"], ev["client"], ev["outputRect"]
        f.frame, f.client = fr, cl
        m = 0 if f.full else self.margin
        w, h = round(fr["w"] * s), round(fr["h"] * s)
        want = (w + 2 * m, h + 2 * m)
        if f.full:
            want = slot.size or want
        if refit or want != slot.size:
            if not f.full and want != slot.size:
                slot.size = want
                self.screens.ask(f"size {slot.index} {want[0]} {want[1]}")
            x0, y0 = slot.pos[0] + m / s, slot.pos[1] + m / s
            if not f.full and (abs(fr["x"] - x0) > 0.5 or abs(fr["y"] - y0) > 0.5):
                self.command(cmd="geometry", id=f.id, x=x0, y=y0, w=fr["w"], h=fr["h"])
                return  # the next geometry event crops the panel
        x, y = round((fr["x"] - out["x"]) * s), round((fr["y"] - out["y"]) * s)
        self.set_panel(f, (x, y, w, h), title=0 if f.full else round((cl["y"] - fr["y"]) * s))

    def dock(self, f, frame=None):
        """Back where it came from (or onto screen 1 if we don't know)."""
        saved = f.saved or {"output": "WL-0", "frame": dict(f.frame or {"x": 100, "y": 100, "w": 800, "h": 600}),
                            "onAllDesktops": False}
        fr = frame or saved["frame"]
        log(f"{f.id[:9]} back to {saved['output']}")
        self.command(cmd="place", id=f.id, output=saved["output"], x=fr["x"], y=fr["y"], w=fr["w"], h=fr["h"],
                     onAllDesktops=bool(saved.get("onAllDesktops")))

    def release(self, f):
        """Its window left: hide the panel and turn the spare off."""
        slot = f.slot
        if slot.window is f:
            slot.window = None
        for sub_id in list(f.subs):
            self.drop_sub(sub_id)
        self.screens.ask(f"unfloat {slot.index}", quiet=True)
        kscreen(f"output.{slot.output}.disable")

    # ------------------------------------------------------------ popups and dialogs

    def sub(self, ev):
        parent = self.floats.get(ev.get("parent", ""))
        if parent is None:
            # A popup of a popup: its top-level parent is the floating window.
            known = self.sub_numbers.get(ev.get("parent", ""))
            parent = self.floats.get(known[0]) if known else None
        if parent is None:
            return
        wid = ev["id"]
        if wid not in self.sub_numbers:
            self.sub_numbers[wid] = (parent.id, self.next_sub)
            parent.subs[wid] = self.next_sub
            self.next_sub += 1
        n = self.sub_numbers[wid][1]
        fr, out, s = ev["frame"], ev["outputRect"], parent.scale
        x, y = round((fr["x"] - out["x"]) * s), round((fr["y"] - out["y"]) * s)
        self.screens.ask(f"sub {parent.slot.index} {n} {x} {y} {round(fr['w'] * s)} {round(fr['h'] * s)}", quiet=True)

    def drop_sub(self, wid):
        known = self.sub_numbers.pop(wid, None)
        if not known:
            return
        parent = self.floats.get(known[0])
        if parent:
            parent.subs.pop(wid, None)
            self.screens.ask(f"sub {parent.slot.index} {known[1]} off", quiet=True)

    # ------------------------------------------------------------ requests on @frametop_float

    def by_panel(self, index):
        for s in self.slots:
            if s.index == index:
                return s.window
        return None

    def request(self, text):
        words = text.split()
        if not words:
            return "error empty"
        cmd, rest = words[0], words[1:]
        if cmd == "list":
            return "ok " + " ".join(f"{s.output}:{s.window.id if s.window else '-'}" for s in self.slots)
        if cmd == "quit":
            GLib.idle_add(self.loop.quit)
            return "ok"
        if cmd in ("float", "dock") and (not rest or rest[0] == "active"):
            self.command(cmd="request-active")
            return "ok"
        if cmd in ("dock", "close", "resize", "carried") and rest and rest[0].isdigit():
            f = self.by_panel(int(rest[0]))
            if not f:
                return f"error no floating window on screen {rest[0]}"
            if cmd == "dock":
                self.dock(f)
            elif cmd == "close":
                self.command(cmd="close", id=f.id)
            elif cmd == "resize" and len(rest) == 3 and f.frame:
                w, h = max(320, int(rest[1])), max(200, int(rest[2]))
                self.command(cmd="geometry", id=f.id, x=f.frame["x"], y=f.frame["y"], w=w / f.scale, h=h / f.scale)
            return "ok"
        if cmd in ("float", "dock", "close") and rest:
            ev = self.windows.get(rest[0])
            if not ev:
                return f"error no window {rest[0]}"
            if cmd == "float":
                self.float_window(ev)
            elif cmd == "dock" and rest[0] in self.floats:
                self.dock(self.floats[rest[0]])
            elif cmd == "close":
                self.command(cmd="close", id=rest[0])
            return "ok"
        return "error unknown command"

    def notify(self, text):
        log(text)
        try:
            n = dbus.Interface(dbus.SessionBus().get_object("org.freedesktop.Notifications",
                                                            "/org/freedesktop/Notifications"),
                               "org.freedesktop.Notifications")
            n.Notify("Frametop", 0, "window-new", "Floating windows", text, [], {}, 5000)
        except dbus.DBusException as e:
            log(f"notification: {e.get_dbus_message()}")


class Service(dbus.service.Object):
    def __init__(self, bus, daemon):
        super().__init__(dbus.service.BusName(SERVICE, bus), PATH)
        self.daemon = daemon

    @dbus.service.method(IFACE, in_signature="s", out_signature="")
    def Event(self, text):
        try:
            self.daemon.on_event(json.loads(text))
        except (ValueError, KeyError, TypeError) as e:
            log(f"bad event {text[:200]}: {e!r}")

    @dbus.service.method(IFACE, in_signature="", out_signature="s", async_callbacks=("reply", "error"))
    def NextCommand(self, reply, error):
        self.daemon.wait(reply)


def main():
    conf = ft_layout.read_conf()
    p = argparse.ArgumentParser(description="Floating windows for the Frametop desktop")
    p.add_argument("--screens", type=int, default=int(os.environ.get("FT_SCREEN_COUNT") or 0))
    p.add_argument("--slots", type=int, default=int(conf.get("FLOAT_SLOTS") or 8))
    p.add_argument("--margin", type=int, default=int(conf.get("FLOAT_MARGIN") or 300))
    p.add_argument("--control", default="ft_screens")
    p.add_argument("--socket", default="frametop_float")
    args = p.parse_args()
    if args.screens <= 0:
        args.screens = ft_layout.screen_count()
    args.slots = max(0, min(16, args.slots))
    args.margin = max(0, min(1000, args.margin))

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    daemon = Daemon(args)
    service = Service(bus, daemon)  # noqa: F841 (keeps the name)
    daemon.loop = GLib.MainLoop()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind("\0" + args.socket)
    sock.setblocking(False)

    def readable(*_):
        while True:
            try:
                data, sender = sock.recvfrom(4096)
            except BlockingIOError:
                return True
            reply = daemon.request(data.decode(errors="replace").strip())
            if sender:
                try:
                    sock.sendto(reply.encode(), sender)
                except OSError:
                    pass
    GLib.io_add_watch(sock.fileno(), GLib.IO_IN, readable)

    log(f"{args.screens} screens, {args.slots} floating slots (WL-{args.screens} and up), margin {args.margin} px")
    daemon.load_script(bus)
    daemon.loop.run()


if __name__ == "__main__":
    main()
