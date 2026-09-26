#!/usr/bin/env python3
"""Frametop Display Settings: the Frametop screens' count, resolution, size, scale, and layout.

A Kirigami (QML) app with a Python backend, like Frametop Input Settings. It runs in
the dev container:
  - Screens (ft-screens backend): each screen's resolution (any, portrait too), its width
    in VR in metres, its scale, and which one has the taskbar. Resolution and width
    apply at once (ft-screens' control socket, @ft_screens); adding or removing a screen
    when the desktop starts again. (gamescope backend: one shared resolution, at most
    1920x1080 worth of pixels, rotation for portrait.)
  - Visibility (ft-screens): when the screens show (always, only with the SteamVR
    dashboard open, while you look at a controller, or only when toggled), the wrist
    angle within which a pinned screen shows, and pin or unpin all screens.
  - Layout: a preset (curved or flat, rows, distance, gap, height) or the arrangement
    captured from where the screens are now, with a preview; arrange now; save the
    current arrangement; arrange automatically when the desktop starts.
Settings go to ~/.config/frametop.conf and ~/.config/frametop-layout.json. Anything
that touches SteamVR runs layout/ft-layout on the host.
Launch with display-settings/ft-display-settings (host wrapper).
"""
import os
import shutil
import socket
import sys

from PySide6.QtCore import Property, QObject, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_DIR = os.path.join(HERE, "..", "layout")
sys.path.insert(0, LAYOUT_DIR)
import ft_layout  # noqa: E402  (pure Python: the same geometry ft-layout uses)

FT_LAYOUT = os.path.join(LAYOUT_DIR, "ft-layout")
DESKTOPS = os.path.join(HERE, "..", "desktops.sh")
CONF_PATH = ft_layout.CONF_PATH
# gamescope's VR backend uploads a texture the size of a screen at start, through a
# 1920x1080x4-byte buffer: more pixels than 1920x1080 abort it (see the design notes).
MAX_PIXELS = 1920 * 1080
RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080), (1728, 1080), (1920, 800), (2224, 928), (2560, 800)]
# ft-screens has no pixel limit; portrait screens are just tall.
SCREEN_RESOLUTIONS = [(1920, 1080, ""), (2560, 1440, ""), (3840, 2160, "4K"), (2560, 1080, "ultrawide"),
                      (3440, 1440, "ultrawide"), (5120, 1440, "super ultrawide"), (1920, 1200, "16:10"),
                      (2560, 1600, "16:10"), (1080, 1920, "portrait"), (1440, 2560, "portrait"),
                      (2160, 3840, "portrait 4K")]
FT_SCREENS = "\0ft_screens"
SCALES = [0.75, 1.0, 1.25, 4 / 3, 1.5, 1.75, 2.0]
ROTATIONS = [("normal", "Landscape"), ("left", "Portrait"), ("right", "Portrait (flipped)")]


def write_conf_value(key, value):
    """Set KEY=value in frametop.conf, keeping comments and the rest of the file."""
    try:
        with open(CONF_PATH) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        if line.split("#", 1)[0].strip().startswith(f"{key}="):
            comment = line[line.index("#"):] if "#" in line else ""
            lines[i] = f"{key}={value}" + (f"   {comment}" if comment else "")
            break
    else:
        lines.append(f"{key}={value}")
    with open(CONF_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def host_command(*cmd):
    """argv to run a command on the SteamOS host (we live in the dev container).

    distrobox-host-exec reaches the host through the user's real session bus; inside the
    desktop our DBUS_SESSION_BUS_ADDRESS is the nested session's private one, where it
    fails (exit 127, silently)."""
    if not shutil.which("distrobox-host-exec"):
        return list(cmd)
    bus = f"unix:path=/run/user/{os.getuid()}/bus"
    return ["env", f"DBUS_SESSION_BUS_ADDRESS={bus}", "distrobox-host-exec"] + list(cmd)


class Backend(QObject):
    changed = Signal()
    busyChanged = Signal()
    message = Signal(str, bool)  # text, is error

    def __init__(self):
        super().__init__()
        self._busy = ""
        self._proc = None
        self._running = False
        self._running_count = 0
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.bind("")  # an abstract address ft-screens can reply to
        self._sock.settimeout(1.0)
        self._started = {}  # conf values the running desktop started with
        self.poll = QTimer(interval=3000, timeout=self._check_running)
        self.poll.start()
        self._check_running()

    # --- state ---
    def _conf(self):
        conf = ft_layout.read_conf()
        def num(key, default, cast=float):
            try:
                return cast(conf.get(key, default))
            except ValueError:
                return default
        return {"screens": num("SCREENS", 2, int), "width": num("WIDTH", 1920, int),
                "height": num("HEIGHT", 1080, int), "physWidth": num("PHYS_WIDTH", 1.6)}

    def _check_running(self):
        # The session's own Wayland socket (host processes' environments aren't readable
        # from the container, so ft_layout.nested_env() doesn't work here).
        running = os.path.exists(f"/run/user/{os.getuid()}/frametop/wayland-0")
        count = self._screens_running() if running and ft_layout.backend() == "screens" else 0
        if running != self._running or count != self._running_count:
            self._running = running
            self._running_count = count
            self._started = self._conf() if running else {}
            self.changed.emit()

    def _ask_screens(self, text):
        """Request/reply to ft-screens; None if it isn't running."""
        try:
            self._sock.sendto(text.encode(), FT_SCREENS)
            return self._sock.recv(4096).decode()
        except OSError:
            return None

    def _screens_running(self):
        reply = self._ask_screens("screens")
        return int(reply.split()[1]) if reply and reply.startswith("ok") else 0

    @Property(bool, notify=changed)
    def desktopRunning(self):
        return self._running

    @Property(str, notify=changed)
    def backend(self):
        return ft_layout.backend()

    @Property(bool, notify=changed)
    def restartNeeded(self):
        """ft-screens: screens added or removed since the desktop started; gamescope: count,
        resolution, or width changed."""
        if not self._running:
            return False
        if ft_layout.backend() == "screens":
            return self._running_count != ft_layout.screen_count(ft_layout.load_layout())
        return bool(self._started) and self._started != self._conf()

    @Property("QVariantList", constant=True)
    def screenResolutions(self):
        return [{"text": f"{w} × {h}" + (f"  ({t})" if t else ""), "width": w, "height": h}
                for w, h, t in SCREEN_RESOLUTIONS]

    @Property(int, notify=changed)
    def screens(self):
        return self._conf()["screens"]

    @Property(int, notify=changed)
    def width(self):
        return self._conf()["width"]

    @Property(int, notify=changed)
    def height(self):
        return self._conf()["height"]

    @Property(float, notify=changed)
    def physWidth(self):
        return self._conf()["physWidth"]

    @Property("QVariantList", constant=True)
    def resolutions(self):
        return [{"text": f"{w} × {h}", "width": w, "height": h} for w, h in RESOLUTIONS]

    @Property("QVariantList", constant=True)
    def scales(self):
        return [{"text": f"{round(s * 100)}%", "value": round(s, 4)} for s in SCALES]

    @Property("QVariantList", constant=True)
    def rotations(self):
        return [{"text": t, "value": v} for v, t in ROTATIONS]

    @Property("QVariantList", notify=changed)
    def screenList(self):
        c, layout = self._conf(), ft_layout.load_layout()
        out = []
        if ft_layout.backend() == "screens":
            primary = ft_layout.primary_screen(layout)
            for i in range(ft_layout.screen_count(layout)):
                w, h = ft_layout.screen_pixels(layout, i)
                s = ft_layout.screen_scale(layout, i)
                out.append({"index": i, "width": w, "height": h, "metres": ft_layout.screen_metres(layout, i),
                            "scale": round(s, 4), "primary": i == primary,
                            "curved": float(ft_layout.screen_entry(layout, i).get("curve", 0)) > 0,
                            "effective": f"{round(w / s)} × {round(h / s)}"})
            return out
        for i in range(c["screens"]):
            s = ft_layout.screen_scale(layout, i)
            rot = ft_layout.screen_rotation(layout, i)
            w, h = (c["height"], c["width"]) if rot != "normal" else (c["width"], c["height"])
            out.append({"index": i, "scale": round(s, 4), "rotation": rot,
                        "effective": f"{round(w / s)} × {round(h / s)}"})
        return out

    @Property("QVariantMap", notify=changed)
    def layout(self):
        return ft_layout.load_layout()

    @Property("QVariantList", notify=changed)
    def plan(self):
        """The arrangement in the head frame, for the preview."""
        layout = ft_layout.load_layout()
        c = self._conf()
        # Before any panel was measured: the width SteamVR floats a panel at, and the aspect.
        if "panel_size" not in layout or layout["panel_size"] == list(ft_layout.DEFAULT_PANEL):
            layout["panel_size"] = [ft_layout.DEFAULT_PANEL[0], ft_layout.DEFAULT_PANEL[0] * c["height"] / c["width"]]
        out = []
        for i, t in enumerate(ft_layout.plan(layout, c["screens"])):
            w, h = ft_layout.screen_size(layout, i)
            out.append({"index": i, "x": t["pos"][0], "y": t["pos"][1], "z": t["pos"][2],
                        "faceYaw": t["face"][0], "facePitch": t["face"][1], "width": w, "height": h})
        return out

    @Property(str, notify=busyChanged)
    def busy(self):
        return self._busy

    # --- screens ---
    @Slot(int)
    def setScreens(self, n):
        write_conf_value("SCREENS", str(max(1, min(6, n))))
        self.changed.emit()

    @Slot(int, int)
    def setResolution(self, w, h):
        if w * h > MAX_PIXELS:
            self.message.emit(f"{w} × {h} is more than gamescope's VR mode can draw (1920 × 1080 worth of pixels, "
                              f"about {MAX_PIXELS // 1000} thousand); try {round((MAX_PIXELS * w / h) ** 0.5) // 8 * 8} × "
                              f"{round((MAX_PIXELS * h / w) ** 0.5) // 8 * 8}", True)
            return
        if w >= 640 and h >= 360:
            write_conf_value("WIDTH", str(w))
            write_conf_value("HEIGHT", str(h))
            self.changed.emit()

    @Slot(float)
    def setPhysWidth(self, w):
        write_conf_value("PHYS_WIDTH", f"{w:.2f}")
        self.changed.emit()

    @Slot(int, float)
    def setScale(self, i, s):
        layout = ft_layout.load_layout()
        screens = layout.setdefault("screens", [])
        while len(screens) <= i:
            screens.append({})
        screens[i]["scale"] = s
        ft_layout.save_layout(layout)
        self.changed.emit()
        if self._running:
            self._run("Applying scale", "scale")

    @Slot(int, str)
    def setRotation(self, i, rotation):
        layout = ft_layout.load_layout()
        screens = layout.setdefault("screens", [])
        while len(screens) <= i:
            screens.append({})
        screens[i]["rotation"] = rotation
        screens[i].pop("roll", None)  # a saved arrangement follows the new rotation
        ft_layout.save_layout(layout)
        self.changed.emit()
        if self._running:
            self._run("Rotating", "scale")

    # --- ft-screens: per-screen resolution and size ---
    def _edit_screen(self, i, fn):
        layout = ft_layout.load_layout()
        screens = layout.setdefault("screens", [])
        while len(screens) <= i:
            screens.append({"size": [1920, 1080], "metres": 1920 / ft_layout.PIXELS_PER_METRE})
        fn(screens[i])
        ft_layout.save_layout(layout)
        self.changed.emit()

    @Slot(int, int, int)
    def setScreenSize(self, i, w, h):
        if not (320 <= w <= 16384 and 200 <= h <= 16384):
            self.message.emit("Width and height: 320 to 16384 pixels", True)
            return
        self._edit_screen(i, lambda s: s.__setitem__("size", [w, h]))
        if self._running and i < self._running_count:
            self._ask_screens(f"size {i + 1} {w} {h}")

    @Slot(int, float)
    def setScreenMetres(self, i, m):
        self._edit_screen(i, lambda s: s.__setitem__("metres", round(m, 3)))
        if self._running and i < self._running_count:
            self._ask_screens(f"width {i + 1} {m:.3f}")

    @Slot(int, bool)
    def setCurved(self, i, on):
        """Curve a screen into a cylinder around you (radius: your distance to it now, from
        ft-screens; the layout's distance if the desktop isn't running)."""
        radius = float(ft_layout.load_layout()["preset"].get("distance", 2.0)) if on else 0.0
        if self._running and i < self._running_count:
            reply = self._ask_screens(f"curve {i + 1} {'on' if on else 'off'}")
            if reply and reply.startswith("ok"):
                radius = float(reply.split()[1])
        self._edit_screen(i, lambda s: s.__setitem__("curve", round(radius, 3)))

    @Slot(int)
    def setPrimary(self, i):
        layout = ft_layout.load_layout()
        layout["primary"] = i + 1
        ft_layout.save_layout(layout)
        self.changed.emit()
        if self._running:
            self._run("Moving the taskbar", "scale")

    @Slot()
    def addScreen(self):
        layout = ft_layout.load_layout()
        layout.setdefault("screens", []).append({"size": [1920, 1080], "metres": 1920 / ft_layout.PIXELS_PER_METRE})
        ft_layout.save_layout(layout)
        self.changed.emit()

    @Slot(int)
    def removeScreen(self, i):
        layout = ft_layout.load_layout()
        screens = layout.get("screens", [])
        if len(screens) <= 1 or i >= len(screens):
            return
        screens.pop(i)
        if layout.get("primary") == i + 1:
            layout.pop("primary")
        ft_layout.save_layout(layout)
        self.changed.emit()

    @Slot()
    def toggleScreens(self):
        self._ask_screens("toggle")

    # --- ft-screens: visibility and pinning ---
    @Property("QVariantMap", notify=changed)
    def visibility(self):
        return ft_layout.visibility(ft_layout.load_layout())

    @Slot(str, "QVariant")
    def setVisibility(self, key, value):
        layout = ft_layout.load_layout()
        v = ft_layout.visibility(layout)
        v[key] = value
        layout["visibility"] = v
        ft_layout.save_layout(layout)
        self.changed.emit()
        if self._running:
            if key == "mode":
                self._ask_screens(f"visibility {value}")
            elif key == "wrist_angle":
                self._ask_screens(f"wrist {float(value):.1f}")
            else:
                self._ask_screens(f"gesture {v['gesture_hand']} {float(v['gesture_angle']):.1f}")

    @Slot(str)
    def pinAll(self, hand):
        reply = self._ask_screens(f"pin all {hand}") if self._running else None
        if reply and reply.startswith("ok"):
            self.message.emit(f"All screens ride on your {hand} wrist now; grab a screen's bar to take it off. "
                              "Save current arrangement keeps it.", False)
        else:
            self.message.emit(f"Couldn't pin: {reply or 'the desktop is not running'}", True)

    @Slot()
    def unpinAll(self):
        reply = self._ask_screens("unpin all") if self._running else None
        if not (reply and reply.startswith("ok")):
            self.message.emit(f"Couldn't unpin: {reply or 'the desktop is not running'}", True)

    @Slot()
    def restartDesktop(self):
        """Restart the Frametop desktop (this app closes with it) to apply count and resolution."""
        argv = host_command("systemd-run", "--user", "--collect", "--quiet", os.path.abspath(DESKTOPS), "restart")
        QProcess.startDetached(argv[0], argv[1:])
        self.message.emit("Restarting the desktop…", False)

    # --- layout ---
    def _edit_layout(self, fn):
        layout = ft_layout.load_layout()
        fn(layout)
        ft_layout.save_layout(layout)
        self.changed.emit()

    @Slot(str)
    def setMode(self, mode):
        self._edit_layout(lambda l: l.__setitem__("mode", mode))

    @Slot(str, "QVariant")
    def setPreset(self, key, value):
        def edit(layout):
            layout["mode"] = "preset"
            layout["preset"][key] = value
        self._edit_layout(edit)

    @Slot(bool)
    def setAuto(self, on):
        self._edit_layout(lambda l: l.__setitem__("auto", bool(on)))

    @Slot()
    def arrange(self):
        self._run("Arranging the screens", "apply")

    @Slot()
    def capture(self):
        self._run("Saving the current arrangement", "capture")

    # --- ft-layout on the host ---
    def _run(self, label, *args):
        if self._proc is not None:
            self.message.emit(f"Still busy: {self._busy}", True)
            return
        self._busy = label
        self.busyChanged.emit()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        argv = host_command(os.path.abspath(FT_LAYOUT), *args)
        proc.finished.connect(lambda code, _status: self._done(proc, label, code))
        self._proc = proc
        proc.start(argv[0], argv[1:])

    def _done(self, proc, label, code):
        out = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
        self._proc = None
        self._busy = ""
        self.busyChanged.emit()
        self.changed.emit()
        last = out.splitlines()[-1] if out else ""
        if code == 0:
            self.message.emit(f"{label}: done" + (f" ({last})" if last and not last.startswith("ok") else ""), False)
        else:
            self.message.emit(f"{label} failed: {last or 'exit code ' + str(code)}", True)


def main():
    app = QGuiApplication(sys.argv)
    app.setApplicationName("ft-display-settings")
    app.setApplicationDisplayName("Frametop Display Settings")
    app.setDesktopFileName("ft-display-settings")
    if not QIcon.themeName():
        QIcon.setThemeName("breeze")
    QQuickStyle.setStyle("org.kde.desktop")
    engine = QQmlApplicationEngine()
    backend = Backend()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("startPage", os.environ.get("FT_DISPLAY_PAGE", "screens"))
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "main.qml")))
    if not engine.rootObjects():
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
