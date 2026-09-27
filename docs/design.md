# Frametop design

Why Frametop is built the way it is, and what we learned about SteamVR on the Steam Frame while building it. [reference.md](reference.md) describes the parts and how to run them. Read the SteamVR notes here before changing how the screens, the pointer, or the input relay talk to SteamVR.

## Goals

- Several desktop screens in SteamVR, each a real monitor with its own resolution and shape, placed anywhere around you.
- One physical mouse that drives a cursor through that 3D arrangement, and through the rest of SteamVR too: the dashboard, Steam, and other overlays.
- Controllers stay fully usable, and VR games aren't disturbed.
- Everything runs on the Frame itself, installed from a terminal on the headset.

## Screens

### Why our own compositor

SteamOS already shows a single-screen Plasma desktop in VR by running KWin nested inside gamescope. The first version of Frametop did the same with gamescope's `PerWindow` mode and `kwin --output-count N`, which gives each KWin output its own SteamVR overlay. It worked, but gamescope has two limits that ruled it out:

- It draws every window into one shared canvas of a single size, letterboxed, and never tells a window what size to be. So every screen had the same resolution and shape; portrait screens had to be rotated outputs on rolled panels.
- Its OpenVR backend allocates an upload buffer of exactly 1920 × 1080 × 4 bytes, so anything larger, such as 3440 × 1440, aborts it at start.

It also leaves the panels to SteamVR's dashboard, which places them and doesn't expose their position to other programs.

ft-screens replaces it. It's a small wlroots compositor that hosts the nested KWin. KWin's nested Wayland backend opens one window per output and resizes an output when the host configures its window, so ft-screens can give each screen any size, live. KWin needs `wl_compositor` v4 or later, `wl_shm`, `wl_seat`, `xdg_wm_base`, and `zwp_linux_dmabuf_v1` v4 with feedback from its host; the rest is optional.

Frames go to SteamVR the same way gamescope sends them: OpenVR's `IVRIPCResourceManagerClient` imports each DMA-BUF (`ImportDmabuf`), and the overlay shows it as a `TextureType_SharedTextureHandle` texture. There's no copy and no size limit. The Frame's SteamVR supports this interface, but the OpenVR header bundled with SteamVR's samples predates it, so the build fetches a pinned header from Valve's openvr repository.

Before settling on this, we tried KWin's screencast virtual outputs. KWin 6.2.5 crashed (`std::out_of_range` in `textureForOutput`) streaming one while nested in gamescope, and its headless backend doesn't implement virtual outputs at all.

A few wlroots details worth knowing: `wlr_shm_create` wants DRM format codes, not `wl_shm` ones, and KWin asks for server-side decorations before its first commit, when setting the mode would assert, so ft-screens answers on the first commit.

### Placing and sizing

Because the overlays are ours, ft-screens places them exactly with `SetOverlayTransformAbsolute` and draws its own controls under each screen. OpenVR has no overlay-relative transforms, so the controls are repositioned whenever their screen moves.

The curved layout chains screens edge to edge, like monitors on a desk: the middle screen (or the seam between two) straight ahead, and each neighbour hinged at the previous screen's outer edge and turned to face you. An earlier version spaced screens by angle, which assumes every screen sits on the circle; a flat 3.6 m screen's edges are much farther away than its centre, so its neighbours landed in front of it. Solving for the hinge angle needs a scan and bisection, because a simple fixed-point iteration diverges for screens nearly as wide as twice their distance.

`SetOverlayCurvature` takes the fraction of a full cylinder that the overlay's width covers, so the radius is width / (2π × curvature). The stated width is the arc length, and the cylinder bends toward the viewer: at a 2 m radius, a 3.57 m wide screen's corners sit about 75 cm closer to you and 23 cm further in than a flat screen's would. Controls on a curved screen are placed on that cylinder, and the bar gets the same curvature.

A resize handle has to be able to shrink a screen from any direction, so the dragged corner follows the laser along the screen's diagonal rather than taking the larger of its horizontal and vertical reach. Pushing and pulling a carried screen moves it along the line from your head, because the 3D mouse's virtual controller sits just in front of the bar, below the screen's centre, so the line from the device points mostly upward.

`ComputeOverlayIntersection` ignores `SetOverlayIntersectionMask`, and a control can't be allowed to cover part of its screen, so the resize tab sits entirely outside the corner.

### Wrist pinning

Pinning started as "bring the screen to your wrist", which doesn't work for big screens, because their centre is far from the edge you bring close. It became aiming: while a screen is carried, the line from the carrying device to its bar is tested against the other hand controllers. Crossing a controller's 6 cm ring arms the pin (leaving past 9 cm, so it doesn't flicker), and crossing it again disarms it. The pin happens on release, with the screen's pose at that moment, so you can arm it and then turn the screen. An earlier version pinned the moment the laser touched the wrist, which left the screen at whatever angle the carrying hand had while pointing there.

A pinned screen's alpha follows the angle between its front and the direction to your head, fully visible inside the wrist angle and fading over the last 10°.

### Visibility and VR games

`VROverlayFlags_MakeOverlaysInteractiveIfVisible` keeps SteamVR's laser mouse on while an overlay with that flag is visible. Without it, the laser is off whenever the dashboard is closed: the first click on a panel only turns it on, and the laser turns off again as soon as it leaves every panel. With it, controllers work the screens normally, but the laser also takes the controllers away from a VR game.

`IVRApplications::GetCurrentSceneProcessId()` is 0 when no game is running (the Frame's home environment isn't a scene app) and the game's process ID while one is. ft-screens checks it twice a second, turns the flag off while a game runs, and by default hides the screens unless the dashboard is open. Flatscreen games run inside Steam's gamescope overlay and aren't scene apps, which is why "only with the dashboard open" is offered as a controller setting.

## The 3D mouse

The mouse works like the pointer on the Apple Vision Pro: a small cursor floats in the room, lands on whatever panel it meets, and acts on it like a controller's laser.

### A virtual controller

SteamVR's dashboard and every overlay it hosts are driven by the vrcompositor `lasermouse` action set: a pointer pose, left, right, and middle click, back, home, scrolling, and a system button that toggles the dashboard. So the 3D mouse is a virtual controller. The `ft_pointer` driver adds an invisible controller (its render model is a single transparent triangle) with its own controller type and default bindings for vrcompositor and the Steam client. Its `/input/a` button is bound to `lasermouse_secondary/switchlaserhand`, which moves the laser to it without clicking. `/pose/tip` didn't work for the laser, because tip is defined by a render model; `/pose/raw` does.

Driver poses are in SteamVR's raw tracking space, and client programs work in the standing universe, which on the Frame is about 1.6 m above raw. Mixing them up put the laser's origin 1.6 m above your head. The helper converts using the headset's pose in both spaces every frame.

The driver starts disconnected, because holding the right-hand role while SteamVR starts leaves the Steam UI stuck on its loading icon. It connects when the mouse is used and claims the right hand. SteamVR keeps a hand role reserved for a disconnected device that still asks for it, so the driver switches its role hint between right hand (connected) and opt-out (not connected).

### The cursor

Mouse motion turns into yaw and pitch around an anchor, the head position at the last recenter. A ray from the anchor is tested against every visible overlay with `ComputeOverlayIntersection`. On a hit, the cursor sits on that surface; otherwise it floats at `POINTER_DISTANCE`. Since the anchor isn't your current eye position, a second test runs along your line of sight to the cursor point, and anything nearer wins, so the cursor always lands on what you see under it.

OpenVR has no call to list other programs' overlays, so the helper runs `vrcmd --overlays` in the background. It includes hidden overlays, because a floating window's controls only appear while something hovers the window, and the cursor has to find them immediately.

The laser starts partway along your line of sight to the cursor rather than at your eye. SteamVR sizes its hit dot by distance from the laser's origin, and a laser from the eye still shows a beam in each eye. Starting it close to the target makes the beam and the dot tiny, while `POINTER_ORIGIN_MARGIN` keeps the origin in front of the small window controls, which float a few centimetres in front of their panels. The helper's own white dot is the visible cursor. In empty space it's an interactive overlay that the laser lands on, so SteamVR never draws a laser into nothing.

A few overlays need special handling:

- The dashboard's dock and the floating windows' controls are scene-graph overlays with no texture (0 × 0) and a placeholder width, so `ComputeOverlayIntersection` never hits them. For those the helper tests the overlay's plane within `POINTER_SCENE_RADIUS` of its origin.
- Just off a panel, the cursor stays on that panel's plane within `POINTER_EDGE_REACH`, so resize margins and window controls just outside the panel are reachable.
- While the left button is held, the cursor keeps the distance it had at the press and stops re-testing collisions, so dragging past a panel's edge doesn't make it jump.

`dashboard.laserRayWidthScale` controls the beam's width, but SteamVR only applies a change from its own settings screen or at restart, so it can't be switched per device while running.

### Handing the laser back and forth

The dashboard follows whichever device summoned it or last pressed its trigger. Frametop adds "last used wins": moving a real controller releases the pointer at once, and the next mouse movement takes the laser back. Small movements don't count; waking needs `POINTER_WAKE_COUNTS` of mouse motion within a second, so desk jitter doesn't steal the laser. While the pointer is awake, a tiny transparent overlay with `MakeOverlaysInteractiveIfVisible` keeps SteamVR's laser mouse on, since otherwise the first click would only switch the laser on.

When the headset comes off, SteamVR reports its activity level as idle at once and turns the displays off 5 seconds later (`power.turnOffScreensTimeout`), unless something keeps it awake. An awake pointer did, and so did the helper's `vrcmd` runs: each is a new SteamVR client, and a new client every second kept SteamVR out of standby. The helper now releases the pointer as soon as the headset is idle, ignores the mouse until you're wearing it again, and pauses the overlay list whenever the pointer is off.

### Moving floating windows

For SteamVR's own floating windows, the dashboard does the moving. A press on a window's grab bar, 7.5 cm below its bottom edge, parents the window to the pressing device with the relative transform at the press. The scroll wheel pushes it along its normal in steps of about 7 cm. The dashboard finishes the move up to 150 ms after the release and reads the device's pose again then, so the helper holds the drag pose for half a second after the button comes up. Tilting works by rotating the virtual controller around the grab point while both buttons are held.

## Input relay

SteamVR opens every input device only when it starts. When a Bluetooth mouse sleeps and reconnects, it gets new device nodes, and SteamVR keeps holding the old, deleted ones, so the mouse stops working until SteamVR restarts. The relay creates permanent virtual devices through uinput before SteamVR starts and forwards the real devices' events into them. systemd keeps the virtual devices' file descriptors across relay restarts, so SteamVR never sees them disappear.

Keyboards aren't grabbed by default, because a grabbed keyboard's keys went into a virtual keyboard nothing typed from; the relay forwards them to ft-screens instead.

## The desktop session

The session is modeled on SteamOS's `steamos-nested-desktop` and runs beside it. It has its own runtime directory, config (`~/.config/frametop`), and state, so it never disturbs the stock desktop's layout or panels. It runs on a private D-Bus from `dbus-run-session`, which has two consequences. KDE only launches apps in systemd scopes when systemd is on the session bus, so everything started in the desktop lands in its systemd unit, and stopping the unit would kill all of it; `session/keep-apps.sh` moves those programs out first. And tools that need the real user bus, like podman and `distrobox-host-exec`, have to be pointed at it explicitly.

Flatpak apps need `XDG_DATA_DIRS` to include Flatpak's exports, or Plasma opens Discover instead of launching them, so the session sources `/etc/profile.d/flatpak.sh`.

A podman container's monitor process (conmon) stays in the cgroup of whatever started the container, and `distrobox enter` starts it on demand. When a Frametop service happened to start the `dev` container, stopping that service stopped the container and everything in it, including the desktop's compositor. `scripts/container-up.sh` starts the container in a systemd scope of its own before anything enters it.

Program names stay within 15 characters, because Linux truncates process names there and the scripts find programs with `pgrep -x` and `pkill -x`. That's why the prefix is `ft-`.

## Approaches we dropped

- WayVR, an existing Wayland desktop for VR. It built and connected to SteamVR on the Frame, but nothing showed in the headset. It has no bindings for the Frame's controllers, and its KDE screen capture needs `xdg-desktop-portal-kde`, which SteamOS doesn't ship.
- gamescope in `PerWindow` mode, for the reasons above. Frametop still supports it as `BACKEND=gamescope`. In that mode SteamVR's dashboard owns the panels, so `ft-layout` floats each one with `vrcmd --dock-overlay` and the pointer helper carries it into place with the virtual controller, hovering first and then sliding at 0.5 m/s, because the dashboard exaggerates fast movements.
- A capture pipeline from a headless KWin through KWin's screencast protocol. It's workable, but ft-screens gets the frames directly with less code.

## Open questions

- A head-locked screen, like a HUD.
- A controller button that shows the screens during a game. Games own the controllers, so this needs SteamVR input actions for ft-screens.
- Drawing KWin's cursor on the screens.
- Plasma can lose its panels when the number of screens goes down, because they're saved against a screen that no longer exists. Removing `plasma-org.kde.plasma.desktop-appletsrc` and `plasmashellrc` from `~/.config/frametop` brings the default panels back.
- Frame pacing and GPU cost with several busy screens haven't been measured.
