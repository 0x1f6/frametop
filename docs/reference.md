# Frametop

Several desktop screens floating in SteamVR on the Steam Frame, driven by a physical mouse (Bluetooth or USB) whose cursor moves through 3D space. The cursor crosses from one screen to the next based on where the screens actually sit around you, not on a flat monitor layout.

How it works: a nested Plasma desktop runs inside ft-screens (`screens/`), our own small Wayland compositor. KWin opens a window per screen; ft-screens gives each one its own size, so every screen is a real monitor of any resolution and shape (ultrawide, portrait, 4K), and shows each as its own SteamVR panel, with KWin's frames passed to SteamVR as they are (no copy). The panels are ours: any size in metres, placed exactly by the layout, with a grab bar to move them, a handle to resize them, and pinning to a hand. See `docs/design.md`. (The older gamescope backend is still there: `BACKEND=gamescope`.)

Status: the multi-screen desktop works on ft-screens (acceptance test: a 3440 × 1440 ultrawide between two 1080 × 1920 portrait screens, wallpaper on each, the taskbar on the ultrawide). The universal 3D mouse works with the SteamVR dashboard, Steam, overlays, and the screens.

## Run

From the headset: open "Launch a program", then "Desktop". After `install`, that entry starts Frametop instead of the stock single-screen desktop.

From a terminal, on the Frame or from a PC over SSH:

```
desktops.sh install        # launcher "Desktop" starts Frametop (writes ~/.local/share/applications/deckard-nested-desktop.desktop)
desktops.sh uninstall      # launcher gets the stock SteamOS desktop back
desktops.sh screens 3      # default screen count
desktops.sh start [screens] | stop | restart | status | log [lines]
```

Settings: the screens (resolution, width in metres, scale, taskbar screen) and the layout are in `~/.config/frametop-layout.json`; `BACKEND`, `REMOTE`, and the pointer settings in `~/.config/frametop.conf` (see `session/frametop.conf.example`). Frametop Display Settings (below) edits both.

The session script is `session/frametop-session.sh`. It runs on the Frame host and starts ft-screens in the `dev` container (`/tmp/frametop-screens.log`), then KWin and Plasma on the host inside it. Only one instance runs at a time, and `desktops.sh start` runs it in its own systemd unit (`frametop-desktop`). It keeps its own Plasma config in `~/.config/frametop`, separate from the stock desktop.

Restarting the desktop (`desktops.sh restart`, or Restart desktop in Frametop Display Settings) closes its windows, but programs started in it keep running when they can: `session/keep-apps.sh` moves them out of the desktop's systemd unit first. Background work like servers, tmux, and builds survives. An app that stops its own helpers when its window closes still loses them; for work that must survive, run it outside the desktop, for example as a systemd user service.

## ft-screens (the compositor)

`screens/compositor.c` (wlroots 0.20) hosts the nested KWin; `screens/vr.cpp` is the SteamVR side. Build: `screens/build.sh` (the installer does it).

- Screens: each KWin window is a screen. ft-screens sizes it (`xdg_toplevel` configure) and KWin resizes that screen to match, live. Frames arrive as DMA-BUFs and go to SteamVR with `ImportDmabuf` (OpenVR's `IVRIPCResourceManagerClient`), no copy, no size limit.
- Panels: `frametop.screen.N`, with `.bar` (move), `.curve` (the round button next to it), `.roll` (the next one: drag it sideways like a knob to roll the screen, snapping level within 2.5°, or scroll on it for 5° steps), and `.resize` (the tab on the bottom right corner; screens go down to 15 cm wide). The controls are sized from both the screen's width and its distance from you, sit on its surface when it's curved, and are translucent like SteamVR's own until a laser is on them. They're invisible until a laser or the 3D mouse's cursor comes very close to one of them (about 1.5 times a button's size), and fade out a moment after it leaves. Drag the bar with any laser (a controller, or the 3D mouse, whose right-drag tilt works too); scroll while dragging to push it away or pull it closer (along the line from your head). The curve button bends the screen into a cylinder around you (radius: your distance to it), or flat again. Pin to a wrist: while carrying a screen, sweep the laser (the line from whatever carries it to its bar) across your other controller. A ring around that controller shows the target and a dot shows where the laser passes; entering the ring arms the pin (ring and bar turn blue), entering it again disarms it. Let go while armed and the screen rides on that controller as it is then, at its size and distance (a 3.6 m screen 5 m away works; so does pinning all screens), so you can arm it first and then turn it the way you want. Grab a pinned screen's bar to adjust it: it comes back to the same wrist when you let go, unless you sweep across the ring to disarm. A pinned screen shows only while you see its front within the wrist angle, fading over the last 10°.
- Visibility (Frametop Display Settings → Visibility & wrist tab): always (Meta+Shift+H, the Hide/Show Screens menu entry, or a mapped mouse button hides them), only with the SteamVR dashboard open, while you look at a chosen controller (the wrist gesture), or only when shown with the hotkey. In the last three, the hotkey shows them anyway. Visible screens keep SteamVR's laser on; hidden, VR games get their triggers back. (A controller button to show them isn't there yet: in a game the game owns the buttons.)
- Input: pointer from the panels to KWin through our seat; keys from the input relay (every keyboard it doesn't grab, and keys a pointer device passes through) to the screen that was clicked last, but not while the SteamVR dashboard is open.
- Control socket `@ft_screens` (datagrams, replies to the sender): `place N x y z yaw pitch roll`, `width N metres`, `curve N radius|on|off`, `pin N|all left|right [12 numbers]`, `unpin N|all`, `size N w h` (live resolution), `get N`, `screens`, `head`, `visibility always|dashboard|gesture|toggle`, `wrist degrees`, `gesture left|right degrees`, `hide | show | toggle`, `state`, `key code value`.

## Input relay (Bluetooth mice and keyboards)

SteamVR opens input devices only when it starts. A Bluetooth mouse that sleeps and reconnects gets new device nodes, and SteamVR keeps reading the dead ones, so the mouse stops working until SteamVR restarts. `input/input-relay.py` fixes that:

- It creates `frametop virtual mouse` and `frametop virtual keyboard` through `/dev/uinput` before SteamVR starts.
- It grabs every USB or Bluetooth mouse and keyboard as they come and go, and forwards their events. SteamVR only sees the virtual devices, which never go away.
- Service: `frametop-input-relay.service` (user unit, `Before=steamvr.service`, wanted by `steamvr.service` and `default.target`).

```
desktops.sh relay install     # enable (starts on the next reboot or SteamVR start)
desktops.sh relay status | log | uninstall
```

- The first time, start it before SteamVR (reboot, or restart SteamVR after `relay install`), so SteamVR opens its virtual devices. After that, restarting the relay is safe: systemd keeps the virtual devices in its file descriptor store (`FileDescriptorStorePreserve=yes`), so SteamVR keeps the same devices.
- Test without disturbing SteamVR: `input-relay.py --no-grab`.
- This is where the 3D mouse will hook in.

## Universal 3D mouse

A Bluetooth mouse drives SteamVR like a controller laser, but it looks like a small dot floating in the room. It snaps onto panels and works on the dashboard, Steam, overlays, and this desktop. Details and findings are in `docs/design.md`, "The universal 3D mouse".

- `input/input-relay.py` (pointer mode, `POINTER=1`) sends mouse motion, clicks, and scroll to the helper. Deliberate movement or a click wakes it; 30 s idle releases it.
- `pointer/helper/ft-pointer` (`frametop-pointer.service`, runs in the `dev` container, starts with SteamVR) holds the room-anchored cursor. It does collision against every visible overlay, draws the white dot, and sends the driver an exact pose.
- `pointer/driver/` (`ft_pointer`, loaded by SteamVR) is an invisible virtual right-hand controller whose laser follows the cursor.
- Last used wins: picking up a controller hands the laser back at once, and moving the mouse takes it again. While you hold a controller the mouse steps aside.
- Moving panels: left-drag a floating panel's grab bar, and it follows the pointer around you (SteamVR's own move). The scroll wheel during a drag pushes and pulls it. **Tilt**: while left-dragging, hold the right button and move the mouse to rotate the panel around the grab point. The right press isn't sent as a right-click. The tilt stays for the rest of the drag: release right and keep moving the tilted panel, press right again to tilt further. Releasing left drops the panel as it is.
- Toggle dashboard (a mapped button or a Meta tap) wakes the pointer if needed and holds the virtual system button for 0.12 s. SteamVR ignores a press and release in the same instant.

```
pointer/driver/build.sh && pointer/driver/install.sh install   # then restart SteamVR
pointer/helper/build.sh && pointer/helper/run.sh install        # user service
pointer/helper/run.sh status | log | restart
pointer/driver/install.sh probe     # devices, hand roles, who owns the dashboard pointer
```

Settings are in `~/.config/frametop.conf`: `POINTER_SENSITIVITY`, `POINTER_IDLE`, `POINTER_WAKE_COUNTS`, `POINTER_DISTANCE`, `POINTER_CURSOR_DEG`, `POINTER_ORIGIN_FRACTION`, `POINTER_ORIGIN_MARGIN`, `POINTER_SCENE_RADIUS`, `POINTER_EDGE_REACH`, and `POINTER_LASER_WIDTH`. See `session/frametop.conf.example`. Restart the relay or the helper after changing them.

## Frametop Input Settings (app)

A Plasma app (Kirigami, Python backend) to choose and map input devices. It's in the Plasma menu under Settings on the Frametop desktop (and the stock desktop). It runs in the `dev` container and talks to the relay's control socket `@frametop_relay`.

- **Devices**: every USB or Bluetooth mouse and keyboard, with a live activity light (move or press a device to find its row). Roles: **3D pointer** (grabbed, drives the pointer; default for anything with a mouse node), **Pass through** (not grabbed; default for keyboards; a Meta tap still toggles the dashboard), **Ignore**. A physical device is identified by its Bluetooth address (or USB ids and name), so all its nodes share a role.
- **Buttons**: pick a pointer device (devices with saved bindings are listed even while asleep or disconnected, marked "not connected"; **Forget** on the Devices page drops all of a device's saved settings), press **Capture a button**, press the button or key, then choose an action: left, right, or middle click, back, scroll up or down, toggle dashboard, recenter, pointer on or off, faster or slower, pass through as key, or do nothing. The Z3's extra buttons arrive as keys from its keyboard node. Each row has **Remove** (your own binding; the button passes through again), **Reset** (a changed built-in button goes back to its default), or **Unbind** (a built-in button does nothing). **Remove all** clears the device's custom bindings. `FT_INPUT_PAGE=buttons` opens the app on that page.
- **Pointer**: sliders for the `POINTER_*` settings, applied live (the relay and helper reload), plus Recenter.
- **Bluetooth**: paired devices, and **Apply Bluetooth fixes** (runs `/etc/steamframe/bt-fixups.sh` through `pkexec`) after pairing an LE device. Pair new devices in Steam.

Rules are saved to `~/.config/frametop-input.json` and pointer settings to `~/.config/frametop.conf`.

```
input-settings/install.sh     # menu entry (ft-input-settings.desktop) -> host launcher ft-input-settings
```

The launcher gives podman the real `XDG_RUNTIME_DIR` and user bus, and gives the app the session's Wayland socket (absolute path) and bus. Without the real user bus, podman fails with `crun: ... cgroup.procs: Permission denied`, because the Frametop session runs on a private bus from `dbus-run-session`.

## Screens and layout (Frametop Display Settings, ft-layout)

When the desktop starts, its screens arrange themselves around where you're facing. Move them by hand any time; put them back with **Meta+Shift+R** in the desktop, the **Reset Screen Layout** menu entry, **Arrange now** in the app, or a mouse button mapped to **Reset desktop screen layout** (Frametop Input Settings → Buttons).

**Frametop Display Settings** (Plasma menu, Settings; Kirigami app in the `dev` container, like Frametop Input Settings):

- **Screens**: add and remove screens; each has a resolution (presets from 1080p to 4K, ultrawide, super ultrawide, portrait, or custom), a width in VR in metres (0.5 to 6), a scale, **Curved**, and **Taskbar here**. Resolution, width, and curve apply at once; adding or removing a screen when the desktop starts again (the app offers the restart).
- **Layout**: curved around you (screens hinged edge to edge like monitors on a desk, each turned to face you) or a flat wall, with rows, distance, gap, and height; or **Save current arrangement** to keep where you put the screens by hand (and their sizes). A preview shows it from above and from the front. **When the desktop starts** turns auto-arrange on or off.

`layout/ft-layout` does the work (Python standard library, on the host):

```
layout/ft-layout apply      # arrange every screen (instant with ft-screens)
layout/ft-layout capture    # save the current arrangement (and sizes) as the layout
layout/ft-layout plan       # the arrangement as JSON (no VR needed)
layout/ft-layout scale      # per-screen scale, side-by-side positions, taskbar screen to KWin
layout/ft-layout toggle     # hide or show all screens
display-settings/install.sh      # menu entries and the Meta+Shift+R / Meta+Shift+H shortcuts
```

The layout is saved in `~/.config/frametop-layout.json`, relative to your head when it's applied. `/tmp/frametop-layout.log` has the startup run. With `BACKEND=gamescope`, `ft-layout` floats each dashboard panel with `vrcmd --dock-overlay` and the pointer helper carries it into place (`place`), since SteamVR's dashboard owns those panels.

## Remote desktop (VNC)

With `REMOTE=1` in the config (`desktops.sh remote on`), the session also serves the VR desktop over VNC. Use RealVNC Viewer or macOS Screen Sharing.

- Address: the Frame's tailnet name or address, port 5900 (`desktops.sh remote info` prints it). It listens on the tailnet address only, not the LAN. It needs Tailscale on the Frame ([deck-tailscale](https://github.com/tailscale-dev/deck-tailscale)).
- Password: in `~/.config/frametop-remote/vnc-password` on the Frame. VNC limits it to 8 characters. `desktops.sh remote info` prints it.
- Encryption: VNC auth has none of its own, so viewers warn about an unencrypted connection. The traffic is still encrypted by the tailnet (WireGuard), which is why it listens only there.
- How it works: no VNC server can capture KWin on SteamOS. `krfb` needs `xdg-desktop-portal-kde`, which SteamOS lacks, and `wayvnc` is wlroots-only. So `session/remote-desktop.sh` captures the desktop with KDE's `krdpserver --plasma` on `127.0.0.1:3390` (never reachable from outside). `session/vnc-bridge.sh` runs TigerVNC's `Xvnc` on display `:20` with a full-screen FreeRDP client connected to it, and serves that over VNC. Everything runs in the `dev` container. The extra hop adds some latency.
- Security trade-off: with `REMOTE=1` the nested KWin runs with `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1`, so any app inside the Frametop desktop can capture its screen or inject input. This applies to that nested session only, not the stock desktop.
- To rotate the password, delete `~/.config/frametop-remote/` on the Frame and restart the desktop.
- Port 3389 is SteamOS's own `xrdp`, which starts a separate X11 session, not the VR desktop.
- Check what a viewer sees: `import -window root -display :20 /tmp/vnc.png` in the container.

## Limits

- Keyboard typing into the screens is wired (relay → ft-screens) but not yet tested with a real keyboard.
- There's no pin to the head (HUD) yet, and no controller button to show hidden screens (a mapped mouse or keyboard button works).
- The KWin cursor isn't drawn on the screens (KWin draws it as a host cursor, which ft-screens ignores); the 3D mouse's dot and SteamVR's laser dot show where you point.
- With `BACKEND=gamescope`: one resolution for all screens, at most 1920 × 1080 worth of pixels; arranging borrows the pointer for a few seconds; a SteamVR update that moves the floating window's grab bar would break arranging (`LAYOUT_GRAB_OFFSET`, the helper's `grabprobe`).
