# Frametop design

Last updated: 2026-09-25.

## Goal

- Two or more desktop screens shown as panels in SteamVR, placed anywhere around the user (arc, stacked, tilted, behind).
- One physical mouse drives a cursor through that 3D layout. Moving the mouse moves the cursor across panels in the direction you expect from where they are in space.
- Works with a Bluetooth mouse. The keyboard should follow the focused panel.
- Desktops come from Linux on the Frame itself. PC streaming is out of scope for now.

## Approach: nested Plasma in ft-screens (2026-09-26; before: a PerWindow gamescope)

Since 2026-09-26 the nested KWin runs inside ft-screens (`screens/`), our own wlroots compositor, instead of gamescope: gamescope draws every window into one canvas of at most 1920x1080 pixels and leaves the panels to the dashboard, which caps their size. ft-screens sizes each KWin screen on its own and shows it as its own SteamVR overlay that we place and size (grab bar, resize handle, pin to a hand, hide/show). The findings log (2026-09-26) has the details. The gamescope notes below still describe `BACKEND=gamescope`.

Everything it uses ships with SteamOS.

- `session/frametop-session.sh` starts its own `gamescope --backend openvr --virtual-connector-strategy PerWindow`. Inside it runs a full Plasma session whose KWin has `--output-count N`. The nested KWin opens one window per output, and PerWindow makes each window its own SteamVR overlay.
- It's modeled on `/usr/bin/steamos-nested-desktop`, SteamOS's single-screen desktop in VR, and runs alongside it. It has its own `XDG_RUNTIME_DIR` (`/run/user/1000/frametop`), config (`~/.config/frametop`), and state (`~/.local/state/frametop`).
- It's controlled from the PC with `desktops.sh`.

Verified in the headset on 2026-09-25:
- Two 1920x1080 panels, each with wallpaper, sharp.
- A taskbar, and apps can be launched.
- Panels can be moved in VR.

Still open:
- Do windows drag between screens? This needs a mouse or keyboard connected.
- What overlay keys does gamescope give each window? Read gamescope's OpenVR backend source (ValveSoftware/gamescope).
- Does the `Gamescope WSI Layer Error: Creating swapchain for non-Gamescope swapchain` in the session log affect any apps? Apps inside the nested desktop inherit `ENABLE_GAMESCOPE_WSI=1`.

## Launcher, config, and panel placement

- Launcher: `desktops.sh install` writes `~/.local/share/applications/deckard-nested-desktop.desktop`, which overrides the stock `/usr/share/applications/deckard-nested-desktop.desktop` (`Exec=steamos-nested-desktop`) by filename. It keeps `X-Steam-Special=Desktop`, which the Frame's non-Steam app launcher uses to pick out the Desktop entry. `uninstall` removes it.
- Config: `~/.config/frametop.conf` (`SCREENS`, `WIDTH`, `HEIGHT`, `PHYS_WIDTH`).
- Placement (researched 2026-09-25): gamescope's OpenVR backend (upstream `src/Backends/OpenVRBackend.cpp`) creates every panel with `CreateDashboardOverlay`, keyed `gamescope.<wl_display>.window.<n>` for non-Steam windows, and never sets a transform. The SteamVR dashboard (web UI, `resources/webinterface/dashboard`) owns placement. Each panel is a "frame" with a dock location: `Dashboard`, `Theater`, `World` (the "Float In World" menu action), `LeftHand`, `RightHand`, or `Boot`. No `/settings/dashboard/*` key sets the initial dock location, and dock state and transforms aren't persisted, only `lastAccessedExternalOverlayKey`.
- Default floating and a layout (decided 2026-09-26: automating the dashboard won over a patched gamescope, which would have lost SteamVR's window controls): `layout/ft-layout` floats each screen with `vrcmd --dock-overlay` and the pointer helper carries it into place with the invisible controller. See the 2026-09-26 findings and `README.md`. With `--virtual-connector-strategy PerWindow` and `--vr-overlay-key frametop`, the keys are `frametop.app.<window seq>`; `.app.0` is gamescope's default connector and never has a window.
- Resolution per screen isn't possible with stock gamescope: it never sets its windows' sizes (no `xdg_toplevel` configure sizes), and draws each window into the one `-W`×`-H` composition, letterboxed. KWin 6.2 would resize a nested output on a configure, and `kscreen-doctor` offers only the one mode. Per-screen scale (KWin output scale, kept in the session's `kwinoutputconfig.json`) stands in.

## The universal 3D mouse

Goal (decided 2026-09-25): run all of SteamVR from inside the headset with a Bluetooth mouse and keyboard. That covers the dashboard, Steam, overlays, the Frametop desktop, and flatscreen apps. It works like the Apple Vision Pro's mouse: a small cursor floats in the room and "collides" with any panel, then acts like a controller laser on it.

Decisions:
- Universal, not only Frametop panels.
- The cursor is anchored in the room (world space), with recenter on demand.
- Controllers stay fully usable. Last used wins.
- Hands off VR games. A scene app with the dashboard closed gets nothing from the pointer. Flatscreen mouse games in Theater get the raw mouse passed through.

### Cursor model

1. The relay grabs the mouse (done: `input/input-relay.py`).
2. Relative motion becomes yaw and pitch of a direction anchored in the room. Recenter puts it straight ahead of the current head pose. Sensitivity is in degrees per count, with optional acceleration.
3. The pointer ray starts at the head (or a point just below the eyes) and runs along that direction. Anything it hits is the target.
4. The cursor sits on the hit surface when there is one. Otherwise it sits on a sphere at a set distance (1.5 m default).
5. Settings: distance, cursor size, snap margin (with hysteresis so it doesn't flicker at panel edges), sensitivity and acceleration, recenter key.

### Delivery: a virtual SteamVR controller

SteamVR's dashboard, and every overlay it hosts, is driven by the vrcompositor "lasermouse" action set:

- `Pointer`: the `/pose/tip` pose.
- `leftclick`, `rightclick`, `middleclick`, `back`, and `home`.
- `scroll_discrete` and `scroll_smooth`: `scroll`.
- `system`: `ToggleDashboard`.
- `quickrecenter`: `Recenter`.

The Frame controller's defaults are in `/opt/steamvr/drivers/frame_controller/resources/input/vrcompositor_bindings_frame_controller.json`.

A small OpenVR driver (`ft_pointer`) adds a virtual controller with no render model or laser of its own:
- Pose: the pointer ray (origin at the head, aimed at the cursor). A laser that starts at the eye and runs along your line of sight shows up as a dot, so SteamVR's own hit cursor becomes the floating mouse on panels.
- Inputs: mouse left to `trigger` (leftclick), right to rightclick, middle to middleclick, wheel to scroll, side buttons to back. A keyboard shortcut maps to system (ToggleDashboard) and to recenter.
- Its own `controller_type` (`ft_pointer`) with default bindings for `openvr.component.vrcompositor` and `steam.client`.

Open questions (spike 1):
- Role. The right hand collides with the real controller. The stylus role (`TrackedControllerRole_Stylus`, path `/user/stylus`) might be bindable without taking a hand.
- Does the Frame's dashboard follow a third pointer device? Is last used wins automatic (`lasermouse_secondary/switchlaserhand` exists)?
- Hiding the device from VR games: report the pose as invalid, or deactivate, whenever a scene app has focus and the dashboard is closed.

### Processes

- `input-relay.py` (host, user service): in pointer mode it sends mouse deltas and buttons to the helper over a Unix socket. In passthrough mode (pointer off, or a flatscreen game in Theater) it forwards to the virtual uinput devices as now.
- `ft_pointer` driver (inside vrserver, host): the virtual controller. It takes its pose and buttons from the helper over a Unix socket. It's built for the host ABI (glibc 2.39; the `dev` container has 2.43) in a Fedora 40 build container.
- `ft-pointer` helper (OpenVR client, in the container): cursor state, recenter, mode switching (IVROverlay `IsDashboardVisible` and the scene app's focus), the free-space cursor overlay, and settings.

Keyboard: still open. SteamVR opens keyboards (vrserver held the Z3 Keyboard), but routing physical keys into dashboard text fields and gamescope panels needs its own spike.

### Spikes

1. Driver: a minimal `ft_pointer` virtual controller with a fixed pose in front of the HMD and a scripted trigger. Does the dashboard laser follow it, and do clicks work? Try the right-hand and stylus roles.
2. Mouse-driven pose: the relay feeds the helper, the helper feeds the driver. Room-anchored cursor, recenter.
3. Free-space cursor overlay, and hiding it when SteamVR's hit dot is on a panel.
4. Modes: VR game focus (off), Theater flatscreen (passthrough), dashboard and overlays (pointer).
5. Keyboard routing.

## Phases

1. MVP: two panels. The mouse cursor crosses between them in 3D, with click and scroll working.
2. Layout: N panels, save and restore panel placement, recenter.
3. Daily use: keyboard focus follows the cursor, launch apps onto a chosen panel, cursor visuals, sensitivity settings, autostart.

## Rejected approaches

- WayVR (wayvr-org/wayvr), tried 2026-09-25 and removed. It built for aarch64 in the `dev` container and connected to SteamVR, but we couldn't see or open it in the headset. It has no bindings for `frame_controller`, so it relies on SteamVR's Oculus remap, and its KDE screen capture needs `xdg-desktop-portal-kde`, which the Frame lacks. A GitHub fork, `DeeJanuz/wayvr`, was created for it.
- A custom capture app (headless KWin, then `zkde_screencast`, then DMA-BUF, then OpenVR overlays). It's workable, but gamescope PerWindow does the same job with no code.

## Findings log

- 2026-09-26: Programs that stop their own helpers when their window closes still lose them on a desktop restart, even after keep-apps.sh moved them out of the unit (22 processes moved, all gone 7 s later when the compositor went): nothing outside the app can keep them. Run such work somewhere independent of the desktop, like a systemd user service. Controls: shown only while some controller ray passes within max(1.5 x button, 12% of the bar) of a control (5 points along the bar, each button, the tab), 0.4 s linger; a zone covering the lower quarter of the screen showed them whenever a screen was in use.

- 2026-09-26: Restarting the desktop killed everything started in it, including background servers and the jobs they ran: `systemctl --user stop frametop-desktop` kills the unit's whole cgroup, and apps launched in the nested session never get scopes of their own (KIO uses systemd scopes only when systemd is on the session bus, and the session runs on a private dbus-run-session bus). Now `desktops.sh stop` first runs session/keep-apps.sh, which moves every process in the unit except the session's own (session script, dbus, KWin, Plasma, the session services it starts; matched by comm name) into a new transient scope (StartTransientUnit with PIDs, via busctl). GUI apps still exit when the compositor goes; whether an app's children outlive it is up to the app. Controls now fade in only while some controller-class device's ray (the 3D mouse's virtual controller included) meets the screen's plane in its lower quarter or just below it, and linger ~0.8 s.

- 2026-09-26: Roll button and quieter controls (feedback: tilting screens sideways was hard; the new corner tab was too big; controls should be smaller and translucent like SteamVR's). Roll is a knob: at the press the laser's angle around the screen's centre (in the plane of the pose at the press) is recorded, and the screen turns by how far that angle has moved (RollZ about its own front axis; pinned screens roll their controller->screen transform). Within 2.5° of level (the right edge's slope) it snaps level; scrolling on the button steps 5°. Controls: bar sqrt(0.012 x distance x width), buttons and tab max(13% of the bar, 1.8% of the distance); textures are a translucent light pill and dark translucent discs with white glyphs (3x supersampled), and every control sits at 55% overlay alpha until a laser is on it (VREvent_MouseMove / FocusEnter / FocusLeave on the control) or it's being dragged.

- 2026-09-26: Screen controls and pointer depth (feedback: the wrist ring was too big; the bar and corner handle only followed distance, looked detached from flat screens; the pointer was unreliable between things close together in view at different depths). Wrist ring 6 cm (leave at 9 cm). Controls: bar width = sqrt(0.02 x distance x width), at least 4% of the distance, at most 60% of the width; the resize control is a quarter-disc tab whose corner sits on the screen's corner (the old L floated 5 cm off it); on curved screens the bar gets the screen's radius and the button and tab are placed on the cylinder (angle u/r, facing the axis). They're re-sized every half second when the distance changes by more than 8%. OpenVR's intersection mask (SetOverlayIntersectionMask) doesn't affect ComputeOverlayIntersection, so it can't be checked from code; the tab stays entirely outside the screen instead. ft-pointer: the cursor's ray starts at the recenter anchor, not the eye, so after leaning it could land on a panel that a nearer one covers from the eye; a second test along the eye's line of sight to the cursor point now takes the nearer thing.

- 2026-09-26: Wrist pinning, third version (in testing, the target wasn't visible, and a screen that pinned mid-carry was stuck at the angle the carrying hand had while pointing at the wrist). Now the laser entering a controller's 10 cm ring (leaving it past 14 cm, so it doesn't flicker) toggles an armed state, and the pin happens on release with the pose at that moment. Grabbing a pinned screen starts armed for its wrist, so moving it re-pins it. While a screen is carried, every other hand controller gets a ring overlay (its zone, facing the head, blue when armed) and a dot at the laser's closest point to it (within 35 cm, blue inside the ring); both are non-interactive overlays above the screens (sort order 20/21). Frametop Display Settings' pages moved from a collapsed side drawer, which was easy to miss, to tabs.

- 2026-09-26: Wrist pinning, second version (feedback: pinning should work by aiming, not touching, so big and far screens can ride on a wrist, and a pinned screen should only show from the front). While a screen is carried, the segment from the carrying device to its bar is tested against the other hand controllers (not ft_pointer); within 10 cm for 0.3 s, the screen pins as it is (controller->screen transform kept). A pinned screen's alpha follows the angle between its front and the direction to the head: 1 inside the wrist angle minus 10°, 0 beyond it (then hidden). Visibility modes (always / dashboard / gesture: gaze within N° of a controller / toggle) in ft-screens, with g_manual as "hidden" in always and "shown anyway" in the others. Poses are read once per tick. `get` reports the pin (hand and transform) so `ft-layout capture` keeps pins and `apply` restores them.

- 2026-09-26: ft-screens fixes after the first session in the headset (moving windows between screens worked; text is as sharp as the headset allows, so screens need to be bigger and nearer).
  - The curved preset spaced screens by angle (2 atan(w/2d)), which assumes every screen sits on the circle. A flat 3.6 m screen's edges are 2.7 m away when its centre is 2 m, so its neighbours landed in front of its edges. Now each row is chained edge to edge like monitors on a desk: the middle screen (or seam) straight ahead at the distance, each neighbour hinged at the previous one's outer edge plus the gap and turned until it faces the eye (dot(centre, right) = 0, solved by scan and bisection; a fixed-point iteration diverges for screens nearly as wide as twice their distance).
  - Resize took the larger of the ray's reach in x and y (y scaled by the aspect). The handle sits below the bottom edge, so moving inward without moving up never shrank a screen. Now the corner follows the ray along the diagonal, keeping the grab offset; minimum 15 cm.
  - Push/pull scaled the device-to-screen offset. The 3D mouse's device sits just in front of the bar (the laser origin is near the cursor), below the screen's centre, so that offset points mostly up. Now it moves along the head-to-screen line.
  - Pinning checked the screen's centre within 35 cm of a controller, but a big screen's centre is far from the edge you bring to your wrist. Now it's the nearest point of the screen's rectangle within 20 cm; the pinned screen shrinks to 32 cm, 12 cm from the controller, and gets its width back when grabbed.
  - Curvature: `SetOverlayCurvature` (the fraction of a full cylinder the width covers, width / 2πr). The curve button uses the head's distance as the radius, so the screen wraps around you; the width can change and the radius stays. ComputeOverlayIntersection and the mouse coordinates follow the curve.
  - A button release is delivered to the panel under the laser, maybe another screen's, so any release on any of our panels ends that device's drags.

- 2026-09-26: ft-screens, a gamescope replacement (decided: our own panels, always visible with a hide hotkey, switch as soon as it works; acceptance test: an ultrawide between two portrait screens). Spike works: three native panels, 1080×1920 / 3440×1440 / 1080×1920.
  - OpenVR's public `IVRIPCResourceManagerClient_003` (`VRIPCResourceManager()`: `GetDmabufModifiers`, `ImportDmabuf`, `UnrefResource`) is how gamescope hands SteamVR its frames; a texture of type `TextureType_SharedTextureHandle` then shows it. No size limit. The Frame's runtime supports it (and `IVROverlay_028`), but SteamVR's bundled `hellovr` header predates it, so builds fetch Valve's public header (v2.15.6). SteamVR imports XRGB8888/ARGB8888 with `LINEAR` and `0x0500000000000001` (Qualcomm compressed).
  - First try, KWin screencast virtual outputs (`zkde_screencast_unstable_v1.stream_virtual_output`, v3 in KWin 6.2.5): KWin crashed in `WorkspaceSceneOpenGL::textureForOutput` (std::out_of_range) streaming one while nested in gamescope, three times (the wrapper restarted it, plasmashell didn't come back until restarted by hand). In 6.2.5 only the DRM and nested Wayland backends implement `createVirtualOutput`, not `--virtual`. The nested backend's version is just another host window: `createOutput(name, size * scale, scale)`.
  - So ft-screens is a minimal wlroots 0.20 compositor (`screens/compositor.c`, C) that hosts the nested KWin, plus an OpenVR side (`screens/vr.cpp`). KWin's nested backend needs `wl_compositor` v4+, `wl_shm`, `wl_seat`, `xdg_wm_base`, and `zwp_linux_dmabuf_v1` v4 (feedback: main device, format table); pointer constraints/gestures, relative pointer, and xdg-decoration are optional. We configure each KWin window's size on its first commit (`wlr_xdg_toplevel_set_size`) and KWin sizes that screen to match; each committed DMA-BUF goes straight to SteamVR, held until the next one. Frame callbacks at 90 Hz. Idle cost: ft-screens 1% CPU, KWin 1%.
  - wlroots details: `wlr_shm_create` wants DRM format codes; KWin asks for server-side decorations before its first commit, and setting the mode then asserts (`surface->initialized`), so it's answered on the first commit.

- 2026-09-26: Three screens (3440×1440 centre, portrait sides) meet two gamescope limits.
  - One size and shape for every screen: a 540×960 test window opened straight in Frametop's gamescope (`WAYLAND_DISPLAY=/run/user/1000/gamescope-1`) became a landscape 16:9 panel (0.59 × 0.33 m): gamescope composes each window into the shared `-W`×`-H` canvas, letterboxed. Portrait screens are rotated KWin outputs (`kscreen-doctor output.N.rotation.left`) with their panels rolled 90° in VR (`place ... roll`); a rolled placement lands exactly (it took two moves).
  - At most 1920×1080 worth of pixels: `upload_buffer_size = 1920 * 1080 * 4` (rendervulkan.hpp), and the OpenVR backend uploads a flat texture of the whole output at start (`vulkan_create_flat_texture(g_nOutputWidth, g_nOutputHeight, ...)`). 3440×1440 aborted gamescope at start (`uploadBufferData: Assertion 'size <= upload_buffer_size' failed`). The largest 21:9-ish size is about 2224×928. Beyond that needs a patched gamescope.
  - A new window starts docked in the dashboard, where `--dock-overlay dashboard` is ignored as redundant and doesn't open the dashboard, so `world` then fails. `float_screen` docks to theater first.
  - For scale: a 1.18 m panel at 1.2 m spans about 52°, and the Frame resolves roughly 20-25 pixels per degree, so about 1,200 pixels across; more pixels only help on bigger or nearer panels.

- 2026-09-26: Screens float and arrange themselves (tested with one screen; two screens pending).
  - `vrcmd --dock-overlay <dashboard|world|theater|lefthand|righthand> <key>` sends the dashboard `vrcmd_dock_overlay`. An unknown key moves the dashboard's active frame instead (`GetFramesWithAssociatedSummonKeys(key)[0] ?? activeFrame`), and a key already at that location is ignored ("redundant"). `world` takes its first transform from the dashboard's position (`setInitialTransformForLocation` → `requestSGTransform(GetDockLocationTransformID(Dashboard))`), which fails while the dashboard is closed ("Invalid transform ID"): the panel floats with no position and shows only with the dashboard. Working order: dock `dashboard` (opens the dashboard), dock `world`, `--hidedashboard`.
  - Floating panels' transforms can't be read (DashboardTab, type 5), but `ComputeOverlayIntersection` works on them and returns UVs (v bottom to top). Casting rays from the head over the sphere and fitting point = O + u·U + v·V gives centre, size, and frame exactly; 230,000 rays take 0.1 s (`md::ScanPanel` in `pointer/common/vrmath.h`, `vrprobe --scan`). A floating 16:9 panel measured 1.181 × 0.664 m with `PHYS_WIDTH=1.6`.
  - The grab bar: below a floating panel SteamVR's laser hits three bands, 2-4, 6-9, and 14-26 cm below the bottom edge; 7.5 cm is the grab bar (`UndockedOverlay`: `onMouseDown: startFloatingWindowMove` on a 350 px bar at `{y: -0.26}` from the frame controls). `valve.steam.gamepadui.floatingfooter` never became visible there.
  - The move (`startFloatingWindowMove`): the panel is parented to the device that clicked (head, left, or right hand by the mouse event's input path) with the relative transform at the press, and at the release it's `device × relative × pushTransform` (push = scroll, along the panel normal, whole notches of ~7 cm; fractional scroll does nothing). Drops within 0.3 m of the open dashboard or 0.4 m of the other hand snap there, so the dashboard is closed first. Rotations about the device origin carry the panel exactly (±20° yaw, ±8° pitch: 0.0° error). Slow translations do too (0.1 m in 1.5 s: 9.9 cm, no rotation). The first try, 0.1 m in 12 jerky 25 ms steps right after the press, moved it 0.19 m and turned it 8.5°. With a hover before the press, rotation first, then a smooth 60 Hz slide, placement was exact (0.0 cm, 0.0°) at every slide speed tried, 0.07 to 1 m/s, including a 66° swing and a panel facing away from the eye. `place` takes 2.5-3.5 s for a floating screen and about 8 s from docked.
  - The helper answers `place`/`measure`/`head` by datagram to the sender's abstract address (`recvfrom`). The first version reset the sender length before replying, so replies went nowhere.

- 2026-09-26: The pointer with the dashboard closed (user-tested: "exactly how I want the pointer to work").
  - gamescope's app panels (`frametop.app.N`, the desktops) report a 0x0 texture like scene-graph overlays, but their transform type is DashboardTab (5), so `GetOverlayTransformAbsolute` fails and the plane test skipped them. `ComputeOverlayIntersection` hits them normally. Only absolutely placed 0x0 overlays are scene-graph now. They stay visible when the dashboard closes.
  - Off a panel, the cursor jumped to `POINTER_DISTANCE` (1.5 m), behind the panel (~1 m), and the laser started behind the panel's resize margins and window controls. It now stays on the last panel's plane within `POINTER_EDGE_REACH` (0.3 m). The floating-window controls only show while the panel is hovered, so the overlay list includes hidden overlays, and visibility is re-read every 50 ms.
  - With the dashboard closed, SteamVR's laser mouse is off until a click, even while our device is the primary dashboard device (`GetPrimaryDashboardDevice` stayed ours; `system.pointer` stayed hidden until a trigger press). So the first click on a panel only turned the laser on, and leaving every panel turned it off again. A held Frame controller keeps it on by itself. Fix: `VROverlayFlags_MakeOverlaysInteractiveIfVisible` ("the system-wide laser mouse mode will be activated whenever this overlay is visible") on a transparent 1 mm overlay, `frametop.pointer.lasermode`, 50 m below the head, shown only while the pointer is awake. vrcompositor's strings (`overlaysForcingLaserMouseOn`, `force_activate_laser_mouse`) led to it. Re-sending the claim pulse when the primary device went invalid didn't help and was removed.

- 2026-09-25: The tilt was also lost on the left release. SteamVR's dashboard finishes a floating move up to 150 ms after mouseup (`endFloatingWindowMove` races `updatePushDistance` against `s_flFinalPushMeasurementMS` = 150) and re-reads the controller pose, which had already gone back to plain pointing. The helper now holds the drag pose (tilt and frozen distance) for 0.5 s after the left release.

- 2026-09-25: Tilt works (user-tested), but it reverted when the right button was released: the device pose went back to plain pointing and the still-grabbed panel followed. Now the tilt angles accumulate per drag and stay applied (about the current cursor point) until left is released. They reset on each left press and release.

- 2026-09-25: Small controls explained by the helper debug log (`debug` command). Over the undock-type controls the helper saw FREE space, put the catcher at 1.5 m and the laser origin at 1.39 m, while the controls were about 1.1-1.2 m away. `valve.steam.gamepadui.bar` (dock) and `valve.steam.gamepadui.floatingfooter` (floating-window controls) are visible absolute overlays with texture 0x0 and a placeholder width of 1.0 m. The dashboard draws them through its scene graph, so `ComputeOverlayIntersection` never hits them. The helper now plane-tests texture-less overlays within `POINTER_SCENE_RADIUS` (0.5 m) and puts the catcher 5 cm behind that plane. The drag lock (freeze the cursor distance while left is held) fixed resize snap-back; the user confirmed it.

- 2026-09-25: User feedback round.
  - The Buttons page hid bindings whenever the Z3 slept, because it listed only connected devices. The app now also lists devices with saved rules or bindings.
  - "Toggle dashboard" did nothing: the relay sent the system button's press and release together. It now wakes the pointer and holds the button for 0.12 s.
  - Small floating controls (undock, frame buttons) sit a few cm in front of their panel. With the laser origin at 0.95-0.98 of the way, the laser started behind them. `POINTER_ORIGIN_MARGIN` (0.15 m) keeps the origin in front.
  - Tilt: the driver takes `posq` (full quaternion), and the helper rotates the device pose around the grab point while left and right are held. SteamVR's floating move (`UndockedOverlay.startFloatingWindowMove`) keeps the panel rigid with the controller (`m_sMoveDevicePath`), so this should turn the panel. Not yet tested; it needs a SteamVR restart to load the driver.

- 2026-09-25: Input relay v2 and the settings app. The relay has per-device roles (pointer, passthrough, ignore) keyed by Bluetooth address (EVIOCGUNIQ) or USB ids. It no longer grabs keyboards by default: they used to be swallowed into the virtual keyboard, which nothing types from. It has per-device button maps to named actions and a control socket `@frametop_relay` (devices, watch, reload; reload also reaches the helper, which now re-reads `POINTER_DISTANCE`, `POINTER_CURSOR_DEG`, and `POINTER_ORIGIN_FRACTION` live). `input-settings` (Kirigami and PySide6, in the container) was tested headless against the live relay with fake uinput devices: listing, roles, capture, mapping, role change, settings, and Bluetooth all work. The UI was checked through screenshots of the VNC display. Clicking through VNC, then RDP, then KWin fake input is too lossy for scripted UI tests.

- 2026-09-25: Controller handoff works for all three devices (left, right, mouse). The driver switches its role hint (Right while connected, OptOut while not), because SteamVR keeps a hand role reserved for a disconnected device that still hints it. The helper releases the pointer when a real controller moves, or when ours hasn't got the hand role within 1 s: SteamVR gives a contested role to the most recently used device, and a held Frame controller counts as used through its touch sensors.
- 2026-09-25: The beam width can't be switched live. `dashboard.laserRayWidthScale` set by any client (IVRSettings, `vrcmd`, even followed by a `laserLength` nudge or `VREvent_DashboardSectionSettingChanged`) is saved but not applied. Only the dashboard's own Settings screen, or a SteamVR restart, applies it. So the width stays at whatever it was set to. The helper starts the laser at 0.95 of the eye-to-cursor line (a few cm of beam along the line of sight, and SteamVR's hit dot is tiny), and draws its own white dot everywhere: `frametop.pointer.marker` (not interactive) on panels, `frametop.pointer.cursor` (interactive, catches the laser) in free space.

- 2026-09-25, spike 3 (helper) works. It looks right: only a dot, anchored to panel surfaces, with a floating white dot in free space.
  - Bug fixed: driver poses are in raw tracking space, and client math is in the standing universe (on the Frame, standing is ~1.6 m above raw). Sending standing coordinates put the laser origin 1.6 m above the head, which also inflated SteamVR's hit dot. The helper now converts via the HMD pose in both universes each frame (`TrackingUniverseRawAndUncalibrated`).
  - SteamVR's hit dot (`system.pointer`, transform type 4, not readable as absolute) is sized by distance from the laser origin. The origin sits `POINTER_ORIGIN_FRACTION` (0.5) along the eye-to-cursor line, which stays invisible and halves the dot.
  - Controller laser not returning: the dashboard pointer flipped 2 → 1. Suspected cause: the relay re-claimed the laser on tiny mouse movement after a pause (sensor jitter). Fix: `POINTER_WAKE_COUNTS` (40 counts in 1 s) before waking or re-claiming. Clicks and scroll wake immediately.

- 2026-09-25, spike 2 (the mouse drives the pointer): it works end to end. The Z3's motion aims SteamVR's laser, and left click, right click, and scroll act on the dashboard. The invisible render model works: `{ft_pointer}/rendermodels/ft_pointer_invisible` is one tiny triangle with a transparent texture. The claim button (`/input/a` bound to `lasermouse_secondary/switchlaserhand`) takes the laser without clicking.
  - Laser looks: `dashboard.laserRayWidthScale` (not in the Settings UI; default 1.0) = 0 hides the beam while it hits a panel. `dashboard.laserLength` is the Settings UI's "Laser Pointer Length" (0.5 = 50%, default). Neither hides the laser when it hits nothing. With an eye-origin ray the beam is still visible, because of stereo (each eye is ~31 mm off the ray).
  - `vrcmd --overlays` lists every overlay (key, visibility, type, flags, handle): `system.systemui` (Steam UI), `valve.steam.desktopgame.*`, `gamescope.*`, `system.pointer` (the laser hit dot), and so on. `vrcmd --compositorcmd dump_laser_overlays` isn't handled by this build.
  - Next (spike 3): the pointer helper. Relay → helper → driver. The helper does collision (enumerate visible overlays, `ComputeOverlayIntersection` from the anchor, cursor snaps onto the surface). It adds a laser-catching dot overlay at the cursor point in empty space, so the laser always hits something. It toggles the laser width by who owns the dashboard pointer.

- 2026-09-25, spike 1 results:
  - Holding the right-hand role while SteamVR starts leaves the Steam UI stuck on its loading icon. So the driver starts disconnected (`deviceIsConnected=false`) and only connects on `show`.
  - Once connected it becomes SteamVR's right hand (`GetTrackedDeviceIndexForControllerRole(Right)` = our device, even with the real controllers on). The real controllers still had their lasers.
  - The dashboard's pointer device (`IVROverlay::GetPrimaryDashboardDevice`) goes to whichever device summoned the dashboard or last pressed its trigger. The headset's side button selects the HMD head pointer (device 0).
  - With `/pose/raw` bound as `lasermouse/Pointer` and `lasermouse_secondary/switchlaserhand` on the trigger: a virtual trigger press moved the pointer to our device, and our system button closed and reopened the dashboard with ours as the pointer. `/pose/tip` didn't work, because tip comes from a render model and ours has none.
  - Still to see in the headset: our dot anchored in the room (`install.sh aimhere`), and a click landing on a target.
- `pointer/probe/vrprobe` (OpenVR background client) prints devices, roles, the dashboard pointer, and head yaw and pitch. `vrcmd --info` also prints "Dashboard pointer device".

- 2026-09-25: Spike 1 prep. `pointer/driver/` holds the `ft_pointer` driver: a virtual controller with room-anchored `aim`/`gaze` pose from the head, buttons over the abstract datagram socket `@ft_pointer`, and default vrcompositor bindings to the lasermouse actions for both hands. It builds in `dev` for the host with `-static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL -fno-math-errno`: libm's `sqrtf` is versioned `GLIBC_2.43` in the container, which is newer than the host's 2.39. Its only export is `HmdDriverFactory`. Installed to `~/.local/share/frametop/ft_pointer` and registered with `vrpathreg adddriver`. Not loaded yet: that needs a SteamVR restart.

- 2026-09-25: The Z3 stopped reaching the desktop after it reconnected at 18:00:20 (sleep), seven minutes after SteamVR started. `vrserver` and `vrcompositor` held `/dev/input/event5`-`event8` as `(deleted)`. Restarting SteamVR fixed it once. The durable fix is `input/input-relay.py`: uinput virtual mouse and keyboard, created before SteamVR, plus EVIOCGRAB relay of USB and Bluetooth mice and keyboards with hotplug. Tested on the Frame with `--no-grab` and a fake USB uinput mouse: motion and BTN_LEFT relayed, unplug released cleanly, Z3 Mouse and Z3 Keyboard picked up, and the Z3 joystick nodes ignored. Enabled as a user service, not yet started.

- 2026-09-25: Chromium (Flatpak `org.chromium.Chromium`, system install) didn't launch from the taskbar. Plasma ran `kde-open appstream://org.chromium.Chromium`, which opens Discover, because the nested session had no `XDG_DATA_DIRS` and so no Flatpak exports. Fix: the session script sources `/etc/profile.d/flatpak.sh` (with a default `XDG_DATA_DIRS` first, since the script runs `set -u`). With the right env, Chromium runs fine in the nested session over Xwayland. Harmless log noise: `vaInitialize failed` (no VA-API) and a GCM `DEPRECATED_ENDPOINT`.

- 2026-09-25: VNC only. RealVNC Viewer can't speak RDP. Bridge: krdp on `127.0.0.1:3390`, then `xfreerdp` full screen inside `Xvnc :20`, then VNC on `<tailnet ip>:5900`. Verified with a screenshot of `:20`.
- 2026-09-25: Changing `SCREENS` can orphan Plasma panels. With 2 screens the taskbar panels were saved with `lastScreen=1`. After switching to 1 screen, no taskbar showed. Fixed by moving `~/.config/frametop/plasma-org.kde.plasma.desktop-appletsrc` and `plasmashellrc` aside (`*.bak-2screens`). TODO: handle this in the session script when the screen count shrinks.
- 2026-09-25: Remote desktop. `krfb` needs `xdg-desktop-portal-kde` (plugins `pw` and `xdp` only on Wayland), and `wayvnc` is wlroots-only. `krdpserver --plasma` (krdp 6.7.5, Fedora) uses KWin's screencast and fake-input protocols directly. It works against the nested KWin 6.2.5 once `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1` is set, which is needed because KWin can't match a container binary to a desktop file. Tested with `xvfb-run xfreerdp`: the right password connects and streams H.264 (OpenH264, no VA-API), and a wrong password is rejected at PostConnect. Reachable from the Mac over the tailnet.
- 2026-09-25: gamescope doesn't always exit on SIGTERM when started from the Steam launcher. `desktops.sh stop` waits 10 s, then sends SIGKILL.
- 2026-09-25: A test of gamescope PerWindow with a bare nested `kwin_wayland --output-count 2` produced two separate overlays. The full Plasma version then showed two sharp 1080p desktops with wallpaper, a taskbar, and movable panels.
- 2026-09-25: A headless `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 kwin_wayland --virtual --output-count 2` runs next to the VR session. With the env var it exposes `zkde_screencast_unstable_v1` and `org_kde_kwin_fake_input`. Without it, KWin hides them. That's useful if the helper injects input through fake_input.
- 2026-09-25: SteamOS runs its own nested Plasma session inside gamescope (`/run/user/1000/nested_plasma`, socket `wayland-0`, X display `:2`). This is the built-in single-screen desktop in VR. Don't touch it.
- 2026-09-25: On the Frame, `gamescope --virtual-connector-strategy` accepts `SingleApplication`, `SteamControlled`, `PerAppId`, and `PerWindow`. The main VR session uses `PerAppId`.
