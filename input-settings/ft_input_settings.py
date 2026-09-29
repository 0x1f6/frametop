#!/usr/bin/env python3
"""Frametop Input Settings: choose and map input devices for the universal 3D mouse.

A Kirigami (QML) app with a Python backend. It runs in the dev container and talks
to the input relay over its control socket (@frametop_relay):
  - Devices: every USB/Bluetooth mouse and keyboard, a live activity light to
    identify them, and a role for each (3D pointer, pass through, ignore).
  - Buttons: press a button or key on a pointer device, then pick an action.
  - Controllers: the same for the Frame controllers' buttons. They're read by the pointer
    helper through SteamVR input (@ft_pointer_helper: vrstatus, vrglobal), and a mapped
    button is taken from games.
  - Pointer: speed, dot size, distance and the rest, applied live.
  - Gaze: the pointer's gaze mode (@ft_pointer_helper "gaze") and the gaze service
    (gaze/ft-gazed, @ft_gazed: status, forget, reload).
  - Bluetooth: paired devices, and re-applying the Bluetooth LE fixes after pairing.
  - A warning on every page when SteamVR won't load the ft_pointer driver (blocked after a
    crash, disabled, or SteamVR in safe mode) or hasn't loaded it (@ft_pointer doesn't answer
    while SteamVR runs): the cursor still moves, but no click lands. Checked at startup and
    every 30 minutes.
Rules go to ~/.config/frametop-input.json and pointer settings to
~/.config/frametop.conf; then the relay (and through it the helper) reloads.
Launch with input-settings/ft-input-settings (host wrapper).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

from PySide6.QtCore import Property, QObject, QSocketNotifier, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

RULES_PATH = os.path.expanduser("~/.config/frametop-input.json")
CONF_PATH = os.path.expanduser("~/.config/frametop.conf")
RELAY = "\0frametop_relay"
HELPER = "\0ft_pointer_helper"
GAZED = "\0ft_gazed"
DRIVER = "\0ft_pointer"  # the ft_pointer driver's control socket, bound while SteamVR has it loaded
# SteamVR's settings; older installs keep them under Steam's config.
VRSETTINGS_PATHS = [os.path.expanduser("~/.config/openvr/config/steamvr.vrsettings"),
                    os.path.expanduser("~/.steam/steam/config/steamvr.vrsettings")]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAZE_PROBE = os.path.join(REPO, "gaze", "probe", "ft-gazeprobe")
BTN_MISC = 0x100
# Header names that mark the start of a range, not a real key (BTN_MOUSE == BTN_LEFT).
RANGE_ALIASES = {"BTN_MISC", "BTN_MOUSE", "BTN_JOYSTICK", "BTN_GAMEPAD", "BTN_DIGI", "BTN_WHEEL",
                 "BTN_TRIGGER_HAPPY", "KEY_MIN_INTERESTING", "KEY_MAX", "KEY_CNT", "BTN_A", "BTN_B", "BTN_X", "BTN_Y"}
DEFAULT_BUTTONS = {0x110: "left", 0x111: "right", 0x112: "middle", 0x113: "back", 0x114: "back"}
ACTION_LABELS = {
    "left": "Left click", "right": "Right click", "middle": "Middle click", "back": "Back",
    "scroll_up": "Scroll up", "scroll_down": "Scroll down", "dashboard": "Toggle SteamVR dashboard",
    "recenter": "Recenter pointer", "pointer_toggle": "Pointer on/off",
    "follow_toggle": "Head follow on/off (experimental)", "gaze_toggle": "Gaze pointer on/off (experimental)",
    "sens_up": "Faster pointer",
    "sens_down": "Slower pointer", "layout_reset": "Reset desktop screen layout",
    "screens_toggle": "Hide/show desktop screens", "key": "Pass through as key",
    "none": "Do nothing",
}
ROLE_LABELS = {"pointer": "3D pointer", "passthrough": "Pass through", "ignore": "Ignore"}
# Frame controller buttons the pointer helper can read (pointer/helper/vrbuttons.h). The
# system button stays SteamVR's.
CONTROLLER_BUTTONS = {
    "left/view": "Left View", "left/dpad_up": "Left D-pad up", "left/dpad_down": "Left D-pad down",
    "left/dpad_left": "Left D-pad left", "left/dpad_right": "Left D-pad right", "left/bumper": "Left bumper",
    "left/trigger": "Left trigger", "left/grip": "Left grip", "left/thumbstick": "Left stick click",
    "right/menu": "Right Menu", "right/a": "Right A", "right/b": "Right B", "right/x": "Right X", "right/y": "Right Y",
    "right/bumper": "Right bumper", "right/trigger": "Right trigger", "right/grip": "Right grip",
    "right/thumbstick": "Right stick click",
}
CONTROLLER_ACTIONS = [a for a in ACTION_LABELS if a not in ("key", "none")]
# Gaze mode settings (pointer helper), like POINTER_SETTINGS.
GAZE_SETTINGS = [
    ("POINTER_GAZE_RETAKE", "Look away to hand back", 5, 1, 45, 0.5, "°"),
    ("POINTER_GAZE_NUDGE_MAX", "Largest nudge to learn", 8, 1, 30, 0.5, "°"),
    ("POINTER_GAZE_HOLD", "Hold still to drag", 0.5, 0.1, 2.0, 0.05, "s"),
    ("POINTER_GAZE_SHOW", "Dot shows after moving", 1.0, 0.0, 5.0, 0.1, "s"),
]
# Pointer settings: key, label, default, min, max, step, unit.
POINTER_SETTINGS = [
    ("POINTER_SENSITIVITY", "Speed", 0.03, 0.005, 0.12, 0.001, "°/count"),
    ("POINTER_CURSOR_DEG", "Dot size", 0.4, 0.1, 2.0, 0.05, "°"),
    ("POINTER_DISTANCE", "Distance in open space", 1.5, 0.5, 4.0, 0.1, "m"),
    ("POINTER_ORIGIN_FRACTION", "SteamVR dot shrink", 0.95, 0.5, 0.98, 0.01, ""),
    ("POINTER_ORIGIN_MARGIN", "Room for small controls", 0.15, 0.03, 0.5, 0.01, "m"),
    ("POINTER_SCENE_RADIUS", "Dock / window-control reach", 0.5, 0.1, 1.5, 0.05, "m"),
    ("POINTER_EDGE_REACH", "Panel edge reach", 0.3, 0.0, 1.0, 0.05, "m"),
    ("POINTER_LEASH_DEG", "Head follow leash", 10, 0, 60, 1, "°"),
    ("POINTER_LEASH_DELAY", "Head follow delay", 0.2, 0.0, 1.0, 0.05, "s"),
    ("POINTER_LEASH_RETURN", "Head follow catch-up", 0.2, 0.05, 2.0, 0.05, "s"),
    ("POINTER_FOLLOW_REACH", "Head follow reach", 70, 20, 85, 1, "°"),
    ("POINTER_WAKE_COUNTS", "Movement to wake", 40, 5, 200, 5, "counts"),
    ("POINTER_IDLE", "Release after idle", 30, 5, 120, 5, "s"),
    ("POINTER_CONTROLLER_PICKUP", "Controller movement to take over", 1.0, 0.5, 5.0, 0.1, "×"),
]


def code_names():
    """evdev key/button code -> name, from the kernel header (first name wins, BTN_ for buttons)."""
    names = {}
    try:
        with open("/usr/include/linux/input-event-codes.h") as f:
            for m in re.finditer(r"#define\s+((?:KEY|BTN)_\w+)\s+(0x[0-9a-fA-F]+|\d+)", f.read()):
                code = int(m.group(2), 0)
                name = m.group(1)
                if name in RANGE_ALIASES:
                    continue
                if code not in names or (code >= BTN_MISC and name.startswith("BTN_") and not names[code].startswith("BTN_")):
                    names[code] = name
    except OSError:
        pass
    return names


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_conf():
    conf = {}
    try:
        with open(CONF_PATH) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip()
    except OSError:
        pass
    return conf


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


def host(*cmd):
    """Run a command on the SteamOS host (we live in the dev container)."""
    runner = ["distrobox-host-exec"] if shutil.which("distrobox-host-exec") else []
    try:
        return subprocess.run(runner + list(cmd), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def driver_block():
    """Why SteamVR won't load the ft_pointer driver: "blocked" (safe mode blocked it after a
    crash), "disabled" (turned off in Manage Add-Ons), "safemode" (SteamVR in safe mode, every
    add-on off) or "" (nothing stops it). SteamVR reads these at startup."""
    for path in VRSETTINGS_PATHS:
        if os.path.exists(path):
            settings = read_json(path)
            break
    else:
        return ""
    section = lambda name: settings.get(name) if isinstance(settings.get(name), dict) else {}
    if not isinstance(settings, dict):
        return ""
    driver = section("driver_ft_pointer")
    if driver.get("blocked_by_safe_mode") is True:
        return "blocked"
    if driver.get("enable") is False:
        return "disabled"
    if section("steamvr").get("enableSafeMode") is True:
        return "safemode"
    return ""


class Backend(QObject):
    devicesChanged = Signal()
    mappingsChanged = Signal()
    pointerChanged = Signal()
    bluetoothChanged = Signal()
    controllersChanged = Signal()
    gazeChanged = Signal()
    driverChanged = Signal()
    activity = Signal(str)  # device id
    captured = Signal(int, str)  # code, name
    capturedController = Signal(str, str)  # button, label
    message = Signal(str, bool)  # text, is error

    def __init__(self):
        super().__init__()
        self._names = code_names()
        self._nodes = []
        self._pointer_mode = False
        self._capture_id = ""
        self._bluetooth = []
        self._relay_ok = False
        self._capture_vr = False
        self._vr = {}  # the helper's vrstatus, {} when it doesn't answer
        self._vr_at = 0.0
        self._gaze = {}  # ft-gazed's status, {} when it isn't running
        self._gaze_prev = None  # the status before, for rates
        self._gaze_at = 0.0
        self._gaze_mode = None  # the helper's gaze mode: True, False, None (no answer)
        self._driver_block = ""  # set by _check_driver
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind("")  # autobind an abstract address the relay can reply to
        self.sock.setblocking(False)
        self.notifier = QSocketNotifier(self.sock.fileno(), QSocketNotifier.Read)
        self.notifier.activated.connect(self._read)
        self.poll = QTimer(interval=2000, timeout=self._refresh)
        self.poll.start()
        self.rewatch = QTimer(interval=50000, timeout=lambda: self._send("watch 60"))
        self.rewatch.start()
        self.reload_timer = QTimer(singleShot=True, interval=400, timeout=lambda: self._send("reload"))
        # The driver only changes with a SteamVR restart, so a rare check is enough. The first one
        # waits a moment for the helper's vrstatus answer, which tells us SteamVR is up.
        self.driver_timer = QTimer(interval=30 * 60 * 1000, timeout=self._check_driver)
        self.driver_timer.start()
        QTimer.singleShot(3000, self._check_driver)
        self._refresh()
        self._send("watch 60")
        self.refreshBluetooth()

    # --- relay socket ---
    def _send(self, text, to=RELAY):
        try:
            self.sock.sendto(text.encode(), to)
            return True
        except OSError:
            if to == RELAY and self._relay_ok:
                self._relay_ok = False
                self.devicesChanged.emit()
            return False

    def _refresh(self):
        self._send("devices")
        self._send("vrstatus", HELPER)
        self._send("gaze ?", HELPER)
        if not self._send("status", GAZED) and self._gaze:
            self._gaze, self._gaze_prev = {}, None
            self.gazeChanged.emit()
        now = time.monotonic()
        # No answer for a while: that side isn't running (any more).
        if self._vr and now - self._vr_at > 5:
            self._vr = {}
            self.controllersChanged.emit()
        if self._gaze_mode is not None and now - self._gaze_at > 5:
            self._gaze_mode = None
            self.gazeChanged.emit()

    def _check_driver(self):
        block = driver_block()
        if not block and self._vr and not self._send("ping", DRIVER):
            block = "unloaded"  # SteamVR runs (the helper answers) without the driver
        if block != self._driver_block:
            self._driver_block = block
            self.driverChanged.emit()

    def _read(self):
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                return
            text = data.decode(errors="replace")
            if text in ("ok on", "ok off"):  # the helper's answer to "gaze ?" (from an unbound socket)
                self._gaze_mode = text == "ok on"
                self._gaze_at = time.monotonic()
                self.gazeChanged.emit()
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if isinstance(msg, dict) and "samples" in msg and "t" not in msg:  # ft-gazed's status
                self._gaze_status(msg)
                continue
            if not isinstance(msg, dict):
                continue
            t = msg.get("t")
            if t == "devices":
                self._nodes = msg.get("nodes", [])
                self._pointer_mode = bool(msg.get("pointer_mode"))
                self._relay_ok = True
                self.devicesChanged.emit()
            elif t == "vrstatus":
                self._vr = msg
                self._vr_at = time.monotonic()
                self.controllersChanged.emit()
            elif t == "event" and msg.get("type") == "vr":
                self.activity.emit(msg["id"])
                if self._capture_vr and msg["value"] == 1 and msg["code"] in CONTROLLER_BUTTONS:
                    self._capture_vr = False
                    self._send("vrcapture 0")
                    self.capturedController.emit(msg["code"], CONTROLLER_BUTTONS[msg["code"]])
            elif t == "event":
                self.activity.emit(msg["id"])
                if (self._capture_id and msg["id"] == self._capture_id and msg["type"] == "key"
                        and msg["value"] == 1):
                    code = int(msg["code"])
                    self._capture_id = ""
                    self.captured.emit(code, self.codeName(code))

    # --- SteamVR driver ---
    @Property(str, notify=driverChanged)
    def driverBlock(self):
        """driver_block(), or "unloaded": SteamVR runs without the driver (unblocked since it
        started, or not installed)."""
        return self._driver_block

    # --- devices ---
    @Property(bool, notify=devicesChanged)
    def relayRunning(self):
        return self._relay_ok

    @Property(bool, notify=devicesChanged)
    def pointerMode(self):
        return self._pointer_mode

    @Property("QVariantList", notify=devicesChanged)
    def devices(self):
        """Connected devices, plus devices with saved rules or bindings that aren't connected
        (a sleeping Bluetooth mouse), so their bindings can still be edited and removed."""
        all_rules = read_json(RULES_PATH)
        rules = all_rules.get("devices", {})
        bindings = all_rules.get("buttons", {})
        grouped = {}
        for n in self._nodes:
            d = grouped.setdefault(n["id"], {"id": n["id"], "name": n["name"], "bus": n["bus"], "kinds": [],
                                             "nodes": [], "role": n["role"], "grabbed": False})
            d["nodes"].append(n["path"])
            d["kinds"] = sorted(set(d["kinds"]) | set(n["kinds"]))
            d["grabbed"] = d["grabbed"] or n["grabbed"]
            if "mouse" in n["kinds"]:
                d["name"] = n["name"].replace(" Mouse", "")  # the Z3's nodes are "Z3 Mouse"/"Z3 Keyboard"
        for d in grouped.values():
            d["connected"] = True
        for device_id in set(rules) | {i for i, b in bindings.items() if b}:
            if device_id in grouped:
                continue
            rule = rules.get(device_id, {})
            # Not connected: the relay's default for a device we have bindings for is "pointer".
            grouped[device_id] = {"id": device_id, "name": rule.get("name", device_id), "bus": "",
                                  "kinds": [], "nodes": [], "role": rule.get("role", "pointer"),
                                  "grabbed": False, "connected": False}
        for d in grouped.values():
            d["explicit"] = d["id"] in rules and "role" in rules[d["id"]]
            d["roleLabel"] = ROLE_LABELS.get(d["role"], d["role"])
        return sorted(grouped.values(), key=lambda d: (not d["connected"], d["role"] != "pointer", d["name"].lower()))

    @Property("QVariantList", constant=True)
    def roles(self):
        return [{"value": k, "text": v} for k, v in ROLE_LABELS.items()]

    @Slot(str, str, str)
    def setRole(self, device_id, role, name):
        rules = read_json(RULES_PATH)
        rules.setdefault("devices", {})[device_id] = {"role": role, "name": name}
        self._save_rules(rules)
        self.message.emit(f"{name}: {ROLE_LABELS.get(role, role)}", False)

    @Slot(str)
    def resetRole(self, device_id):
        rules = read_json(RULES_PATH)
        rules.get("devices", {}).get(device_id, {}).pop("role", None)
        self._save_rules(rules)

    @Slot(str)
    def forgetDevice(self, device_id):
        """Drop everything saved for a device: role, name, and bindings."""
        rules = read_json(RULES_PATH)
        name = rules.get("devices", {}).pop(device_id, {}).get("name", device_id)
        rules.get("buttons", {}).pop(device_id, None)
        self._save_rules(rules)
        self.message.emit(f"Forgot {name}", False)

    def _remember_name(self, rules, device_id):
        """Keep the device's name with its rules, so it can be listed while disconnected."""
        entry = rules.setdefault("devices", {}).setdefault(device_id, {})
        if "name" not in entry:
            for d in self.devices:
                if d["id"] == device_id:
                    entry["name"] = d["name"]

    def _save_rules(self, rules):
        os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
        with open(RULES_PATH, "w") as f:
            json.dump(rules, f, indent=2)
        self._send("reload")
        QTimer.singleShot(300, self._refresh)
        self.mappingsChanged.emit()
        self.devicesChanged.emit()

    # --- buttons ---
    @Slot(int, result=str)
    def codeName(self, code):
        return self._names.get(code, f"code {code}")

    @Property("QVariantList", constant=True)
    def actions(self):
        return [{"value": k, "text": v} for k, v in ACTION_LABELS.items()]

    @Slot(str, result="QVariantList")
    def mappings(self, device_id):
        custom = read_json(RULES_PATH).get("buttons", {}).get(device_id, {})
        rows = {}
        for code, action in DEFAULT_BUTTONS.items():
            rows[code] = {"code": code, "action": action, "custom": False}
        for code, action in custom.items():
            rows[int(code)] = {"code": int(code), "action": action, "custom": True}
        out = []
        for code in sorted(rows):
            r = rows[code]
            r["isDefault"] = code in DEFAULT_BUTTONS  # a built-in binding (left/right/middle/side/extra)
            r["name"] = self.codeName(code)
            r["actionLabel"] = ACTION_LABELS.get(r["action"], r["action"])
            out.append(r)
        return out

    @Slot(str)
    def startCapture(self, device_id):
        self._capture_id = device_id
        self._send("watch 60")

    @Slot()
    def cancelCapture(self):
        self._capture_id = ""

    @Slot(str, int, str)
    def setMapping(self, device_id, code, action):
        rules = read_json(RULES_PATH)
        rules.setdefault("buttons", {}).setdefault(device_id, {})[str(code)] = action
        self._remember_name(rules, device_id)
        self._save_rules(rules)
        self.message.emit(f"{self.codeName(code)} → {ACTION_LABELS.get(action, action)}", False)

    @Slot(str, int)
    def removeMapping(self, device_id, code):
        """Drop a custom binding: built-in buttons go back to their default, others pass through."""
        rules = read_json(RULES_PATH)
        rules.get("buttons", {}).get(device_id, {}).pop(str(code), None)
        self._save_rules(rules)
        default = DEFAULT_BUTTONS.get(code)
        self.message.emit(f"{self.codeName(code)}: " + (f"back to {ACTION_LABELS[default]}" if default
                                                         else "binding removed"), False)

    @Slot(str, int)
    def unbind(self, device_id, code):
        """Make a button do nothing (also works for the built-in bindings)."""
        self.setMapping(device_id, code, "none")

    @Slot(str)
    def clearMappings(self, device_id):
        rules = read_json(RULES_PATH)
        removed = len(rules.get("buttons", {}).pop(device_id, {}) or {})
        self._save_rules(rules)
        self.message.emit(f"Removed {removed} binding{'s' if removed != 1 else ''}; defaults restored", False)

    # --- pointer settings ---
    @Property("QVariantList", notify=pointerChanged)
    def pointerSettings(self):
        conf = read_conf()
        out = []
        for key, label, default, lo, hi, step, unit in POINTER_SETTINGS:
            try:
                value = float(conf.get(key, default))
            except ValueError:
                value = default
            out.append({"key": key, "label": label, "value": value, "min": lo, "max": hi, "step": step,
                        "unit": unit, "default": default})
        return out

    @Slot(str, float)
    def setPointerSetting(self, key, value):
        integer = key in ("POINTER_WAKE_COUNTS", "POINTER_IDLE", "POINTER_LEASH_DEG", "POINTER_FOLLOW_REACH")
        write_conf_value(key, str(int(round(value))) if integer else f"{value:.3f}".rstrip("0").rstrip("."))
        self.reload_timer.start()  # debounce slider drags
        self.pointerChanged.emit()

    @Property(bool, notify=pointerChanged)
    def pointerFollow(self):
        return read_conf().get("POINTER_FOLLOW", "0") not in ("", "0")

    @Slot(bool)
    def setPointerFollow(self, on):
        write_conf_value("POINTER_FOLLOW", "1" if on else "0")
        self.reload_timer.start()
        self.pointerChanged.emit()

    @Slot(result=bool)
    def recenter(self):
        return self._send("recenter", HELPER)

    # --- controllers ---
    @Property("QVariantList", constant=True)
    def controllerActions(self):
        return [{"value": a, "text": ACTION_LABELS[a]} for a in CONTROLLER_ACTIONS]

    @Property("QVariantList", constant=True)
    def controllerButtons(self):
        return [{"value": b, "text": label} for b, label in CONTROLLER_BUTTONS.items()]

    @Property("QVariantList", notify=mappingsChanged)
    def controllerMappings(self):
        mapped = read_json(RULES_PATH).get("controller_buttons", {})
        return [{"button": b, "label": label, "action": mapped[b],
                 "actionLabel": ACTION_LABELS.get(mapped[b], mapped[b])}
                for b, label in CONTROLLER_BUTTONS.items() if b in mapped]

    @Property("QVariantMap", notify=controllersChanged)
    def controllerStatus(self):
        """helper: answering; manifest: its SteamVR input is set up; global: SteamVR's
        "Enable global input from overlays"; active: mapped buttons SteamVR delivers now."""
        vr = self._vr
        return {"helper": bool(vr), "manifest": bool(vr.get("manifest")), "global": bool(vr.get("global")),
                "inGame": bool(vr.get("in_game")), "bound": vr.get("bound", []), "active": vr.get("active", [])}

    @Property(bool, notify=mappingsChanged)
    def controllerInGames(self):
        return bool(read_json(RULES_PATH).get("controller_in_games"))

    @Slot(bool)
    def setControllerInGames(self, on):
        rules = read_json(RULES_PATH)
        rules["controller_in_games"] = bool(on)
        self._save_rules(rules)
        self.message.emit("Mapped controller buttons: " + ("taken in games too" if on else "left to games"), False)

    @Slot(str, str)
    def setControllerMapping(self, button, action):
        if button not in CONTROLLER_BUTTONS or action not in CONTROLLER_ACTIONS:
            return
        rules = read_json(RULES_PATH)
        rules.setdefault("controller_buttons", {})[button] = action
        self._save_rules(rules)
        self.message.emit(f"{CONTROLLER_BUTTONS[button]} → {ACTION_LABELS[action]}", False)

    @Slot(str)
    def removeControllerMapping(self, button):
        rules = read_json(RULES_PATH)
        rules.get("controller_buttons", {}).pop(button, None)
        self._save_rules(rules)
        self.message.emit(f"{CONTROLLER_BUTTONS.get(button, button)}: back to games", False)

    @Slot()
    def clearControllerMappings(self):
        rules = read_json(RULES_PATH)
        removed = len(rules.pop("controller_buttons", {}) or {})
        self._save_rules(rules)
        self.message.emit(f"Removed {removed} controller binding{'s' if removed != 1 else ''}", False)

    @Slot()
    def startControllerCapture(self):
        self._capture_vr = True
        self._send("watch 60")
        self._send("vrcapture 30")

    @Slot()
    def cancelControllerCapture(self):
        if self._capture_vr:
            self._capture_vr = False
            self._send("vrcapture 0")

    @Slot(bool)
    def setGlobalInput(self, on):
        """SteamVR's "Enable global input from overlays (Experimental)", which the helper needs
        to get controller buttons while a game or the dashboard has focus."""
        if self._send(f"vrglobal {'on' if on else 'off'}", HELPER):
            self.message.emit(f"SteamVR global input from overlays {'on' if on else 'off'}", False)
        else:
            self.message.emit("The pointer helper isn't running (frametop-pointer.service)", True)

    # --- gaze ---
    def _gaze_status(self, status):
        now = time.monotonic()
        prev = self._gaze_prev
        # Rates over the last poll: samples per second, and the share with only one eye.
        if prev and now - prev[0] > 0.5 and status["samples"] >= prev[1]["samples"]:
            n = status["samples"] - prev[1]["samples"]
            status["rate"] = n / (now - prev[0])
            status["one_eye_share"] = (status["one_eye"] - prev[1]["one_eye"]) / n if n else 0.0
            for key in ("lost_left", "lost_right"):
                if key in status and key in prev[1]:
                    status[key + "_share"] = (status[key] - prev[1][key]) / n if n else 0.0
        elif self._gaze:
            for key in ("rate", "one_eye_share", "lost_left_share", "lost_right_share"):
                status.setdefault(key, self._gaze.get(key))
        if not prev or now - prev[0] > 0.5:
            self._gaze_prev = (now, status)
        self._gaze = status
        self.gazeChanged.emit()

    @Property("QVariantMap", notify=gazeChanged)
    def gazeStatus(self):
        return self._gaze

    @Property(bool, notify=gazeChanged)
    def gazeServiceRunning(self):
        return bool(self._gaze)

    @Property(int, notify=gazeChanged)
    def gazeMode(self):
        """The helper's gaze mode now: 1 on, 0 off, -1 no answer (helper not running)."""
        return -1 if self._gaze_mode is None else int(self._gaze_mode)

    @Property(bool, notify=gazeChanged)
    def gazeDefault(self):
        return read_conf().get("POINTER_GAZE", "0") not in ("", "0")

    @Slot(bool)
    def setGazeMode(self, on):
        """On or off now and from now on (POINTER_GAZE); a mapped button toggles it until restart."""
        write_conf_value("POINTER_GAZE", "1" if on else "0")
        self._send(f"gaze {'on' if on else 'off'}", HELPER)
        self.reload_timer.start()
        self.gazeChanged.emit()

    @Property("QVariantList", notify=pointerChanged)
    def gazeSettings(self):
        conf = read_conf()
        out = []
        for key, label, default, lo, hi, step, unit in GAZE_SETTINGS:
            try:
                value = float(conf.get(key, default))
            except ValueError:
                value = default
            out.append({"key": key, "label": label, "value": value, "min": lo, "max": hi, "step": step,
                        "unit": unit, "default": default})
        return out

    @Slot()
    def forgetGazeLessons(self):
        if self._send("forget", GAZED):
            self.message.emit("Forgot what the pointer's nudges taught; the calibration stays", False)
        else:
            self.message.emit("The gaze service isn't running (frametop-gaze.service)", True)

    @Slot()
    def reloadGazeCalibration(self):
        if self._send("reload", GAZED):
            self.message.emit("The gaze service read the calibration again", False)
        else:
            self.message.emit("The gaze service isn't running (frametop-gaze.service)", True)

    @Slot()
    def openGazeProbe(self):
        """Calibrate in ft-gazeprobe (a GTK app on the host, fullscreen on a Frametop screen)."""
        self._open_probe([], "Opening the gaze probe: calibrate there, then close it")

    @Slot()
    def openHeadsetFit(self):
        """The probe's Headset fit mode: how well the tracker sees each eye, as you adjust."""
        self._open_probe(["--mode", "fit"], "Opening the headset fit check in the gaze probe")

    def _open_probe(self, args, done):
        runner = ["distrobox-host-exec"] if shutil.which("distrobox-host-exec") else []
        env = [f"{k}={os.environ[k]}" for k in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
               if os.environ.get(k)]
        try:
            subprocess.Popen(runner + ["env"] + env + [GAZE_PROBE] + args, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.message.emit(done, False)
        except OSError as e:
            self.message.emit(f"Couldn't open the gaze probe: {e}", True)

    # --- bluetooth ---
    @Property("QVariantList", notify=bluetoothChanged)
    def bluetooth(self):
        return self._bluetooth

    @Slot()
    def refreshBluetooth(self):
        devices = []
        listing = host("bluetoothctl", "devices", "Paired")
        for line in listing.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3 or parts[0] != "Device":
                continue
            info = host("bluetoothctl", "info", parts[1]).stdout
            battery = re.search(r"Battery Percentage: \S+ \((\d+)\)", info)
            devices.append({"address": parts[1], "name": parts[2],
                            "connected": "Connected: yes" in info,
                            "battery": int(battery.group(1)) if battery else -1})
        self._bluetooth = devices
        self.bluetoothChanged.emit()

    @Slot()
    def applyBluetoothFixes(self):
        if not os.path.exists("/run/host/etc/steamframe/bt-fixups.sh") and not os.path.exists("/etc/steamframe/bt-fixups.sh"):
            self.message.emit("Bluetooth fixes aren't installed (setup/bluetooth/install.sh)", True)
            return
        result = host("pkexec", "/etc/steamframe/bt-fixups.sh")
        if result.returncode == 0:
            self.message.emit("Bluetooth fixes applied: " + " ".join(result.stdout.split())[:120], False)
        else:
            self.message.emit("Couldn't apply the Bluetooth fixes: " + (result.stderr.strip() or "cancelled")[:160], True)
        self.refreshBluetooth()


def main():
    app = QGuiApplication(sys.argv)
    app.setApplicationName("ft-input-settings")
    app.setApplicationDisplayName("Frametop Input Settings")
    app.setDesktopFileName("ft-input-settings")
    if not QIcon.themeName():
        QIcon.setThemeName("breeze")
    QQuickStyle.setStyle("org.kde.desktop")
    engine = QQmlApplicationEngine()
    backend = Backend()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("startPage", os.environ.get("FT_INPUT_PAGE", "devices"))
    engine.load(QUrl.fromLocalFile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.qml")))
    if not engine.rootObjects():
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
