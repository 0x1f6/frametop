#!/usr/bin/env python3
"""Input relay for the Steam Frame: stable virtual devices, the 3D pointer, device rules.

SteamVR opens /dev/input/event* only when it starts and never hotplugs, so a
Bluetooth mouse that sleeps and reconnects (new event nodes) stops working
until SteamVR restarts. This relay creates a virtual mouse and a virtual
keyboard through /dev/uinput once, before SteamVR starts, and feeds them from
the physical devices as they come and go.

Every USB or Bluetooth mouse and keyboard is a candidate. A physical device is
identified by its Bluetooth address (EVIOCGUNIQ) or USB bus:vendor:product:name,
so all its event nodes share one role (the Swiftpoint Z3 has a mouse node and a
keyboard node for its extra buttons). Roles, from ~/.config/frametop-input.json
(written by the Frametop Input Settings app):
  pointer      grabbed; drives the universal 3D mouse (default for devices with a mouse node)
  passthrough  not grabbed, only observed, e.g. for the Meta dashboard shortcut (default for keyboards)
  ignore       not grabbed, only observed for identification in the settings app
Buttons and keys of pointer devices go through a per-device map to actions
(left, right, middle, back, scroll_up, scroll_down, dashboard, recenter,
pointer_toggle, sens_up, sens_down, layout_reset = put the desktop screens back in
their saved layout, screens_toggle = hide or show the desktop screens, key = pass
through as a key, none).

Keys also go to ft-screens (@ft_screens, the Frametop desktop's compositor), which
types them into the desktop screen that has focus (not while the SteamVR dashboard is
open): from keyboards that aren't grabbed, and keys a pointer device passes through.

Pointer mode (POINTER=1 in ~/.config/frametop.conf) sends pointer devices to
the ft-pointer helper (pointer/helper), which drives the ft_pointer
SteamVR driver. With POINTER=0, pointer devices go to the virtual mouse and
keyboard instead.

Control socket (abstract datagram @frametop_relay, JSON replies to the sender):
  devices           list event nodes with id, name, kinds, role, grabbed
  watch <seconds>   stream input events from every candidate node (identification)
  reload            re-read both config files, re-apply roles, tell the helper

Runs on the Frame host as a user service (frametop-input-relay.service). The
virtual devices are parked in systemd's file descriptor store, so a relay
restart gets the same devices back and SteamVR never loses them. The service is
Type=notify: READY=1 goes out only after the devices exist, so SteamVR (ordered
after it) always finds them. Dependency-free: Python standard library plus the
kernel's evdev and uinput interfaces.

  input-relay.py            the service
  input-relay.py --no-grab  never grab, for testing next to a running SteamVR
"""
import array
import errno
import fcntl
import json
import os
import select
import socket
import struct
import subprocess
import sys
import time

# Linux input constants (include/uapi/linux/input-event-codes.h, input.h, uinput.h).
EV_SYN, EV_KEY, EV_REL, EV_MSC = 0x00, 0x01, 0x02, 0x04
SYN_REPORT = 0
BTN_MISC, KEY_MAX = 0x100, 0x2FF
KEY_A = 30
REL_X, REL_Y, REL_WHEEL, REL_MAX = 0x00, 0x01, 0x08, 0x0F
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114
KEY_LEFTMETA, KEY_RIGHTMETA = 125, 126
BUS_USB, BUS_BLUETOOTH, BUS_VIRTUAL = 0x03, 0x05, 0x06

# struct input_event on 64-bit: struct timeval (2 x long), u16 type, u16 code, s32 value.
EVENT = struct.Struct("llHHi")


def _ioc(direction, nr, size, kind):
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def _iow(kind, nr, size):
    return _ioc(1, nr, size, kind)


def _ior(kind, nr, size):
    return _ioc(2, nr, size, kind)


UI_DEV_CREATE = _ioc(0, 1, 0, "U")
UI_DEV_DESTROY = _ioc(0, 2, 0, "U")
UI_DEV_SETUP = _iow("U", 3, 92)  # struct uinput_setup: input_id (4 x u16), name[80], u32
UI_SET_EVBIT = _iow("U", 100, 4)
UI_SET_KEYBIT = _iow("U", 101, 4)
UI_SET_RELBIT = _iow("U", 102, 4)
EVIOCGRAB = _iow("E", 0x90, 4)
EVIOCGID = _ior("E", 0x02, 8)
EV_NAMES = {EV_KEY: "key", EV_REL: "rel"}


def eviocgbit(ev, length):
    return _ior("E", 0x20 + ev, length)


def eviocgname(length):
    return _ior("E", 0x06, length)


def eviocguniq(length):
    return _ior("E", 0x08, length)


VIRTUAL_PREFIX = "frametop virtual"
RULES_PATH = os.path.expanduser("~/.config/frametop-input.json")
ACTIONS = ("left", "right", "middle", "back", "scroll_up", "scroll_down", "dashboard", "recenter",
           "pointer_toggle", "sens_up", "sens_down", "layout_reset", "screens_toggle", "key", "none")
SCREENS = "\0ft_screens"
FT_LAYOUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "layout", "ft-layout")
DEFAULT_BUTTONS = {BTN_LEFT: "left", BTN_RIGHT: "right", BTN_MIDDLE: "middle",
                   BTN_SIDE: "back", BTN_EXTRA: "back"}


def log(*args):
    print(*args, flush=True)


def notify(state, fds=()):
    """sd_notify, with optional file descriptors for the fd store. No-op outside systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    # socket.send_fds() ignores its address argument (Python 3.12), so use sendmsg.
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendmsg([state.encode()], ancillary, 0, addr)
    except OSError as e:
        log(f"sd_notify failed ({state.splitlines()[0]}): {e}")


def stored_fds():
    """File descriptors handed back by systemd's fd store, by name."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return {}
    names = os.environ.get("LISTEN_FDNAMES", "").split(":")
    count = int(os.environ.get("LISTEN_FDS", "0"))
    return {names[i]: 3 + i for i in range(count) if i < len(names)}


class Virtual:
    """One uinput device, reused from systemd's fd store when possible."""

    def __init__(self, name, product, keys, rels, stored):
        self.dirty = False
        store_name = f"vdev{product}"
        if store_name in stored:
            self.fd = stored[store_name]
            os.set_blocking(self.fd, False)
            log(f"reusing {name} from the fd store")
            return
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in keys:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        if rels:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for code in rels:
                fcntl.ioctl(self.fd, UI_SET_RELBIT, code)
        setup = struct.pack("HHHH80sI", BUS_VIRTUAL, 0x4D44, product, 1, name.encode(), 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        notify(f"FDSTORE=1\nFDNAME={store_name}", [self.fd])
        log(f"created {name}")

    def emit(self, etype, code, value):
        os.write(self.fd, EVENT.pack(0, 0, etype, code, value))
        self.dirty = True

    def sync(self):
        if self.dirty:
            os.write(self.fd, EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
            self.dirty = False


def bits(fd, ev, count):
    buf = bytearray((count + 7) // 8)
    try:
        fcntl.ioctl(fd, eviocgbit(ev, len(buf)), buf)
    except OSError:
        return set()
    return {i for i in range(count) if buf[i // 8] >> (i % 8) & 1}


def read_config(path=os.path.expanduser("~/.config/frametop.conf")):
    """KEY=VALUE lines, # comments allowed. Missing file means defaults."""
    conf = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    key, value = line.split("=", 1)
                    conf[key.strip()] = value.strip()
    except OSError:
        pass
    return conf


def read_rules(path=RULES_PATH):
    """{"devices": {id: {"role", "name"}}, "buttons": {id: {"<code>": action}}}."""
    try:
        with open(path) as f:
            rules = json.load(f)
    except (OSError, ValueError):
        rules = {}
    rules.setdefault("devices", {})
    rules.setdefault("buttons", {})
    return rules


class Pointer:
    """Drives the ft_pointer SteamVR driver from a mouse (pointer mode).

    The virtual controller connects when the mouse is used (taking the right
    hand role and recentering on the gaze) and disconnects after `idle` seconds
    without mouse activity, so the real controllers get their role back: the
    last used device wins.
    """

    DRIVER_BUTTONS = {"left": "trigger", "right": "b", "middle": "x", "back": "joystick"}
    SCROLL_PULSE = 0.08  # seconds of joystick deflection per wheel notch
    CLAIM_PULSE = 0.06  # seconds the claim button (switchlaserhand, no click) is held
    RESUME_PAUSE = 1.5  # mouse idle this long, then moving again, re-claims the laser
    WAKE_WINDOW = 1.0  # seconds in which WAKE_COUNTS of motion must add up

    def __init__(self, sensitivity, idle, wake_counts=40):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sensitivity = sensitivity  # degrees per mouse count
        self.idle = idle
        self.active = False
        self.last_used = 0.0
        self.dx = self.dy = 0
        self.scroll_until = None
        self.claim_at = None  # when to press the claim button
        self.claim_release = None
        self.system_at = None  # dashboard toggle: when to press the virtual system button
        self.system_release = None
        # Waking (or re-claiming after a pause) needs deliberate movement, so sensor
        # jitter from a mouse lying on a desk can't steal the laser from a controller.
        self.wake_counts = wake_counts
        self.pending = 0
        self.pending_since = 0.0

    def send(self, command):
        try:
            self.sock.sendto(command.encode(), "\0ft_pointer_helper")
        except OSError:
            pass  # helper not running (SteamVR not running)

    def wake(self, now):
        if not self.active:
            self.send("show")
            self.send("recenter")
            self.active = True
            self.claim_at = now + 0.3  # let SteamVR bind the freshly connected device first
            log("pointer on")
        elif now - self.last_used > self.RESUME_PAUSE and self.claim_at is None:
            self.claim_at = now  # another device may have taken the laser meanwhile
        self.last_used = now

    def motion(self, code, value, now):
        dormant = not self.active or now - self.last_used > self.RESUME_PAUSE
        if dormant and code in (REL_X, REL_Y):
            if now - self.pending_since > self.WAKE_WINDOW:
                self.pending, self.pending_since = 0, now
            self.pending += abs(value)
            if self.pending < self.wake_counts:
                return  # not yet deliberate movement
            self.pending = 0
        self.wake(now)
        if code == REL_X:
            self.dx += value
        elif code == REL_Y:
            self.dy += value
        elif code == REL_WHEEL and value:
            self.send(f"scroll 0 {1 if value > 0 else -1}")
            self.scroll_until = now + self.SCROLL_PULSE

    def action(self, name, value, now):
        """A mapped button: value 1 press, 0 release, 2 autorepeat (ignored)."""
        if value == 2:
            return
        driver = self.DRIVER_BUTTONS.get(name)
        if driver:
            self.wake(now)
            self.flush()
            self.send(f"btn {driver} {value}")
        elif value != 1:
            return  # the rest act on press
        elif name in ("scroll_up", "scroll_down"):
            self.wake(now)
            self.send(f"scroll 0 {1 if name == 'scroll_up' else -1}")
            self.scroll_until = now + self.SCROLL_PULSE
        elif name == "dashboard":
            self.dashboard(now)
        elif name == "recenter":
            self.wake(now)
            self.send("recenter")
        elif name == "pointer_toggle":
            if self.active:
                self.send("hide")
                self.active = False
                log("pointer off (toggle)")
            else:
                self.wake(now)
        elif name == "screens_toggle":
            try:
                self.sock.sendto(b"toggle", SCREENS)
            except OSError:
                pass  # ft-screens not running
        elif name == "layout_reset":
            # Runs a few seconds and borrows the pointer; ft-layout refuses a second copy.
            subprocess.Popen([FT_LAYOUT, "apply"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            log("layout reset")
        elif name in ("sens_up", "sens_down"):
            self.sensitivity *= 1.25 if name == "sens_up" else 0.8
            log(f"sensitivity {self.sensitivity:.4f} deg/count")

    def flush(self):
        if self.dx or self.dy:
            # Mouse right turns the ray right (negative yaw); mouse down tilts it down.
            self.send(f"move {-self.dx * self.sensitivity:.4f} {-self.dy * self.sensitivity:.4f}")
            self.dx = self.dy = 0

    def dashboard(self, now=None):
        """Toggle the SteamVR dashboard with the virtual controller's system button.

        SteamVR needs the button held for a frame or two (press and release in the
        same instant is ignored), and the virtual controller must be connected and
        bound, so wake it first when needed.
        """
        now = time.monotonic() if now is None else now
        woke = not self.active
        self.wake(now)
        self.system_at = now + (0.4 if woke else 0.0)

    def tick(self, now):
        if self.system_at is not None and now >= self.system_at:
            self.send("btn system 1")
            self.system_at = None
            self.system_release = now + 0.12
        elif self.system_release is not None and now >= self.system_release:
            self.send("btn system 0")
            self.system_release = None
        if self.claim_at is not None and now >= self.claim_at:
            self.send("btn a 1")
            self.claim_at = None
            self.claim_release = now + self.CLAIM_PULSE
        elif self.claim_release is not None and now >= self.claim_release:
            self.send("btn a 0")
            self.claim_release = None
        if self.scroll_until is not None and now >= self.scroll_until:
            self.send("scroll 0 0")
            self.scroll_until = None
        if self.active and now - self.last_used > self.idle:
            self.send("hide")
            self.active = False
            log("pointer off (idle)")

    def timeout(self):
        pending = (self.scroll_until, self.claim_at, self.claim_release, self.system_at, self.system_release)
        return 0.02 if any(t is not None for t in pending) else 0.5


class Node:
    """One input event node of a candidate device (mouse or keyboard, USB or Bluetooth)."""

    def __init__(self, path, fd, name, bus, vendor, product, uniq, is_mouse, is_keyboard):
        self.path, self.fd, self.name = path, fd, name
        self.bus, self.vendor, self.product, self.uniq = bus, vendor, product, uniq
        self.is_mouse, self.is_keyboard = is_mouse, is_keyboard
        # One physical device, whatever its node: Bluetooth address, else USB ids plus name.
        base = self.name.split(" Mouse")[0].split(" Keyboard")[0]
        self.id = uniq.lower() if uniq else f"usb:{vendor:04x}:{product:04x}:{base}"
        self.role = None
        self.grabbed = False
        self.held = set()  # keys and buttons currently down, released if the device vanishes
        self.last_watch = 0.0

    def describe(self):
        kinds = [k for k, on in (("mouse", self.is_mouse), ("keyboard", self.is_keyboard)) if on]
        return {"path": self.path, "name": self.name, "id": self.id, "uniq": self.uniq,
                "bus": {BUS_USB: "usb", BUS_BLUETOOTH: "bluetooth"}.get(self.bus, str(self.bus)),
                "kinds": kinds, "role": self.role, "grabbed": self.grabbed}


def probe(path):
    """Open a node if it is a USB or Bluetooth mouse or keyboard, else return None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(256)
        fcntl.ioctl(fd, eviocgname(len(buf)), buf)
        name = buf.split(b"\0", 1)[0].decode(errors="replace")
        ident = bytearray(8)
        fcntl.ioctl(fd, EVIOCGID, ident)
        bus, vendor, product, _ = struct.unpack("HHHH", ident)
        if name.startswith(VIRTUAL_PREFIX) or bus not in (BUS_USB, BUS_BLUETOOTH):
            raise ValueError
        uniq_buf = bytearray(64)
        try:
            fcntl.ioctl(fd, eviocguniq(len(uniq_buf)), uniq_buf)
            uniq = uniq_buf.split(b"\0", 1)[0].decode(errors="replace")
        except OSError:
            uniq = ""
        is_mouse = REL_X in bits(fd, EV_REL, REL_MAX + 1)
        is_keyboard = KEY_A in bits(fd, EV_KEY, KEY_MAX + 1)
        if not (is_mouse or is_keyboard):
            raise ValueError
        return Node(path, fd, name, bus, vendor, product, uniq, is_mouse, is_keyboard)
    except (OSError, ValueError):
        os.close(fd)
        return None


def main():
    can_grab = "--no-grab" not in sys.argv
    stored = stored_fds()
    mouse = Virtual(f"{VIRTUAL_PREFIX} mouse", 1,
                    keys=range(BTN_MISC, 0x118), rels=range(REL_MAX + 1), stored=stored)
    keyboard = Virtual(f"{VIRTUAL_PREFIX} keyboard", 2,
                       keys=range(1, BTN_MISC), rels=(), stored=stored)
    notify("READY=1")

    control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    control.bind("\0frametop_relay")
    control.setblocking(False)
    watchers = {}  # address -> watch end time

    state = {"pointer": None, "rules": {}}

    def load_config():
        conf = read_config()
        state["rules"] = read_rules()
        if conf.get("POINTER", "0") == "1":
            p = state["pointer"] or Pointer(0.03, 30)
            p.sensitivity = float(conf.get("POINTER_SENSITIVITY", "0.03"))
            p.idle = float(conf.get("POINTER_IDLE", "30"))
            p.wake_counts = int(conf.get("POINTER_WAKE_COUNTS", "40"))
            state["pointer"] = p
            log(f"pointer mode: {p.sensitivity} deg/count, idle {p.idle} s, wake {p.wake_counts} counts")
        else:
            state["pointer"] = None
            log("pointer mode off: pointer devices feed the virtual mouse and keyboard")

    load_config()
    meta_down = False  # Meta pressed with no other key yet: a tap toggles the dashboard
    screens_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)

    def to_screens(code, value):
        """A key for the desktop screens (ft-screens decides whether it types)."""
        if value in (0, 1) and code < BTN_MISC:
            try:
                screens_sock.sendto(f"key {code} {value}".encode(), SCREENS)
            except OSError:
                pass  # ft-screens not running
    nodes = {}  # fd -> Node
    # Nodes already probed (rejected or open): path -> inode. A device that disconnects and
    # reconnects between two scans often gets the same event numbers back, so the path alone
    # would hide it; the re-created node has a new inode.
    seen = {}
    next_scan = 0.0

    def role_of(node):
        rule = state["rules"]["devices"].get(node.id, {})
        if rule.get("role") in ("pointer", "passthrough", "ignore"):
            return rule["role"]
        has_mouse = any(n.is_mouse for n in nodes.values() if n.id == node.id) or node.is_mouse
        return "pointer" if has_mouse else "passthrough"

    def release_held(node):
        for code in node.held:
            (mouse if code >= BTN_MISC else keyboard).emit(EV_KEY, code, 0)
        node.held.clear()
        mouse.sync()
        keyboard.sync()

    def apply_roles():
        for node in nodes.values():
            role = role_of(node)
            want_grab = can_grab and role == "pointer"
            if want_grab != node.grabbed:
                try:
                    fcntl.ioctl(node.fd, EVIOCGRAB, 1 if want_grab else 0)
                    node.grabbed = want_grab
                except OSError as e:
                    log(f"{'grab' if want_grab else 'ungrab'} failed for {node.name}: {e}")
                if not node.grabbed:
                    release_held(node)
            if role != node.role:
                log(f"{node.name} ({node.path}, {node.id}): {role}{', grabbed' if node.grabbed else ''}")
                node.role = role

    def drop(node, reason):
        release_held(node)
        os.close(node.fd)
        del nodes[node.fd]
        seen.pop(node.path, None)
        log(f"released {node.name} ({node.path}): {reason}")

    def reply(addr, obj):
        try:
            control.sendto(json.dumps(obj).encode(), addr)
        except OSError:
            watchers.pop(addr, None)

    def handle_control(now):
        while True:
            try:
                data, addr = control.recvfrom(4096)
            except BlockingIOError:
                return
            if not addr:
                continue  # unbound sender, nowhere to reply
            words = data.decode(errors="replace").split()
            cmd = words[0] if words else ""
            if cmd == "devices":
                reply(addr, {"t": "devices", "pointer_mode": state["pointer"] is not None,
                             "actions": ACTIONS, "nodes": [n.describe() for n in nodes.values()]})
            elif cmd == "watch":
                seconds = float(words[1]) if len(words) > 1 else 30
                watchers[addr] = now + min(seconds, 600)
                reply(addr, {"t": "watching", "seconds": seconds})
            elif cmd == "reload":
                load_config()
                apply_roles()
                if state["pointer"]:
                    state["pointer"].send("reload")
                reply(addr, {"t": "reloaded"})
            else:
                reply(addr, {"t": "error", "error": f"unknown command {cmd!r}"})

    def broadcast(node, etype, code, value, now):
        if not watchers:
            return
        if etype == EV_REL and now - node.last_watch < 0.05:
            return  # motion: enough for an activity light
        node.last_watch = now
        msg = {"t": "event", "id": node.id, "path": node.path, "name": node.name,
               "type": EV_NAMES.get(etype, str(etype)), "code": code, "value": value}
        for addr, until in list(watchers.items()):
            if now > until:
                del watchers[addr]
            else:
                reply(addr, msg)

    while True:
        now = time.monotonic()
        pointer = state["pointer"]
        if now >= next_scan:
            next_scan = now + 1.0
            current = {}
            for name in os.listdir("/dev/input"):
                if name.startswith("event"):
                    try:
                        current[f"/dev/input/{name}"] = os.stat(f"/dev/input/{name}").st_ino
                    except OSError:
                        pass
            for path in [p for p in seen if p not in current]:
                del seen[path]
            added = False
            for path, ino in sorted(current.items()):
                if seen.get(path) == ino:
                    continue
                # New here: a new device, or one that came back in the same place.
                for old in [n for n in nodes.values() if n.path == path]:
                    drop(old, "replaced by a new device node")
                seen[path] = ino
                node = probe(path)
                if node:
                    nodes[node.fd] = node
                    added = True
            if added:
                apply_roles()

        ready, _, _ = select.select(list(nodes) + [control], [], [],
                                    pointer.timeout() if pointer else 0.5)
        now = time.monotonic()
        if pointer:
            pointer.tick(now)
        for fd in ready:
            if fd is control:
                handle_control(now)
                continue
            node = nodes[fd]
            try:
                data = os.read(fd, EVENT.size * 64)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                drop(node, os.strerror(e.errno))
                continue
            if not data:
                drop(node, "closed")
                continue
            buttons = state["rules"]["buttons"].get(node.id, {})
            for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, off)
                if etype in (EV_KEY, EV_REL):
                    broadcast(node, etype, code, value, now)
                if node.role != "pointer":
                    # Observed only. A Meta tap on any keyboard toggles the dashboard.
                    if node.role == "passthrough" and etype == EV_KEY:
                        to_screens(code, value)
                    if pointer and node.role == "passthrough" and etype == EV_KEY:
                        if code in (KEY_LEFTMETA, KEY_RIGHTMETA):
                            if value == 1:
                                meta_down = True
                            elif value == 0 and meta_down:
                                meta_down = False
                                pointer.dashboard()
                        elif value == 1:
                            meta_down = False  # Meta used as a modifier, not a tap
                    continue
                if etype == EV_KEY:
                    action = buttons.get(str(code), DEFAULT_BUTTONS.get(code, "key"))
                    if pointer and action not in ("key", "none"):
                        pointer.action(action, value, now)
                        continue
                    if action == "none":
                        continue
                    target = mouse if code >= BTN_MISC else keyboard
                    target.emit(etype, code, value)
                    to_screens(code, value)
                    if value:
                        node.held.add(code)
                    else:
                        node.held.discard(code)
                elif etype == EV_REL:
                    if pointer:
                        pointer.motion(code, value, now)
                    else:
                        mouse.emit(etype, code, value)
                elif etype == EV_SYN and code == SYN_REPORT:
                    if pointer:
                        pointer.flush()
                    mouse.sync()
                    keyboard.sync()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
