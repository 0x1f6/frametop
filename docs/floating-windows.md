# Floating windows (plan)

Status: design settled 2026-09-29 (see "Decisions"); being built on the `floating-windows` branch.

Built so far (2026-09-29; the KWin side tested on the headless test desktop, `screens/test/headless.sh`; nothing yet tried in the headset):

- The catcher (a release off every panel still reaches KWin), and the pointer helper's "up" backstop.
- `float/frametop-float.js` (the KWin script), `float/ft-floatd`, and `float/ft-float`. "Float in VR" is in the window menu under Extensions, and Meta+Shift+F toggles the active window. Floating, docking (back where it came from), closing, full screen, per-window scale (Meta+scroll), popups reported with their rectangles, windows of a floating app floating too, and the notification when every spare is in use.
- ft-screens: a panel per spare output (`frametop.float.N`) with the crop, density, popups and dialogs as small panels over it, title-bar carrying, the corner tab resizing the window, and dock and close buttons. `--spares`, and the commands `float`, `unfloat`, `pose`, `sub`, `minimized`, `carry`.
- The session adds `FLOAT_SLOTS` spares and starts ft-floatd from the desktop's autostart; ft-layout leaves the spares alone.
- The 3D mouse's drag lock crosses onto other Frametop panels (not while carrying one).
- The float key (2026-09-30): the input relay's `float_toggle` (Meta+Shift+F by default) and `dock_all`, through `float pointer` and `dock all` on ft-floatd. Tested on the headless desktop.
- The title bar button (2026-09-30, branch `float-titlebar`): `decoration/` (Frametop's QML window decoration, installed by the session script; `decoration/apply.sh` switches the running desktop to it or back to Breeze), and keep-below as the floating flag in the script. Tried on the live desktop: the button floats Dolphin and docks it again, the float key and `dock all` keep the flag in step, and maximized windows look right.

Not built yet: phase 2 (the ghost, tear-off by dragging, push-flush docking), phase 3 (launching floating, Launch as Standalone, remembered placement), and phase 4.

Build order from 2026-09-30, each merged into `experimental` when done: (1) the float key and docking everything, (2) the title bar button, (3) phase 3, (4) hiding screens one at a time, (5) profiles (`docs/profiles.md`).

The goal is to let any desktop app float in VR in a panel of its own, like SteamVR's floating windows, while it stays part of the Frametop desktop. That means drag and drop, the clipboard, and focus keep working between floating windows and the screens.

- There are two ways to get a floating window. Launch the app floating, or drag a desktop window by its title bar off a screen and let go in the air.
- There are two ways to put one back. Push it flush against a screen and let go, or press its "back to desktop" button.
- Files, text, and images drag between any two floating windows, and between floating windows and the screens.

## Decisions

Settled with the user on 2026-09-29 (1 to 21) and 2026-09-30 (22 to 27). The sections below follow them.

| # | Question | Decision |
|---|---|---|
| 1 | How many windows can float at once | 8 spare outputs by default, configurable (`FLOAT_SLOTS`, and Display Settings); a change needs a desktop restart |
| 2 | Menus and dropdowns | Each floating output has a margin around the window. The panel shows only the window, and each open popup gets a small overlay of its own, cut from the same buffer |
| 3 | Tearing off | Drag the title bar past a screen's edge and let go in the air, with a small dead zone past the edge |
| 4 | Docking by dragging | Push the window flush against a screen (within about 10 cm), with the landing spot highlighted, and let go |
| 5 | Visibility | Floating windows follow the same rules as the screens: the hide hotkey, the visibility modes, and the games rule |
| 6 | Windows a floating app opens | They float too |
| 7 | Launching floating from the headset | ~~One "Frametop Apps" launcher entry with a picker~~ Replaced by 26 and profiles (27) |
| 8 | Build order | The catcher first, as a fix that stands on its own; `pointer-ignore` and `layouts-headpin` merged before phase 1; the hand cutouts stay out |
| 9 | Show Desktop (Meta+D) | Floating windows stay |
| 10 | Frametop Apps and visibility | ~~The entry starts the desktop with each screen hidden on its own, so only floating windows show~~ Replaced: a profile can hide screens (27) |
| 11 | Window frame | KWin's title bar and border stay. Frametop's bar, close, and "back to desktop" are extras |
| 12 | Resizing | The window's own edges and Frametop's corner tab both change the size in pixels at the same density; the output follows |
| 13 | Margin | 300 px on each side, configurable |
| 14 | All spares in use | The window opens on the screens, with a notification |
| 15 | Where a floating app's new windows go | Where that app's windows went last time; otherwise to the parent's right, curving around you |
| 16 | Where the code is written | A branch in the PC's clone of the repo |
| 17 | Size when docked by dragging | The current floating size in pixels, shrunk to fit the screen |
| 18 | Bigger text | A scale for each window (KWin's output scale): Meta+scroll over the window, or +/- on its bar. Remembered for each app |
| 19 | Switching to a window you can't see | It's focused, and a glow at the edge of your view points to it. Moving it in front of you is a setting |
| 20 | Full screen | The window fills its own panel. The margin drops to zero while it's full screen, and the panel keeps its size and place |
| 21 | Named layouts | ~~They cover the screens only~~ Replaced by profiles (27). Outside a profile, floating windows use the placement remembered for each app |
| 22 | The float key | One toggle: it floats a window, or docks it if it already floats. The input relay owns it (`float_toggle`), Meta+Shift+F by default, rebindable in Frametop Input Settings and mappable to mouse and controller buttons. KWin has no shortcut of its own for it, so one press can't fire twice |
| 23 | Which window the key acts on | The window under the desktop's pointer; the active window if there's none there (the wallpaper, the taskbar) |
| 24 | Docking everything | A `dock_all` action, with no default binding |
| 25 | A button on every window | A float button left of Close in the title bar, from Frametop's own QML window decoration, made to look like Breeze. It shows a dock icon on floating windows. Apps that draw their own title bar (Chromium, Electron, GTK) use the key |
| 26 | Launching one app floating | "Launch as Standalone" in the right-click menu of every app in the Application Launcher and the taskbar, from copies of the apps' desktop files that only the Frametop desktop reads. It replaces the Frametop Apps entry (7, 10) |
| 27 | Profiles | Named layouts become profiles: the screens' places, which screens show, and the apps and their windows, floating or not. See `docs/profiles.md` |

Also assumed: floating windows get the wrist pin, the head pin, and pass-through (`pointer-ignore`) like screens. Every gesture works with the controllers as well as the 3D mouse. A window launched floating uses the primary screen's density. VNC shows only the primary screen, as now. Anything that restarts the live desktop waits for the user's OK.

## The approach: each floating window gets a KWin output of its own

Drag and drop and the clipboard only work between windows of the same compositor. A Wayland window can't move from one compositor to another. So a floating window has to stay a KWin window.

ft-screens already shows each KWin output as a panel. It sets the output's size with an `xdg_toplevel` configure, and KWin resizes the output to match. So a floating window can get an output of its own, sized to fit it, and ft-screens shows that output as a panel with its own controls. To KWin this is an ordinary desktop with more monitors. Dragging between two floating windows is the same as dragging between two monitors, which KWin already handles. ft-screens already moves the pointer between panels in the middle of a drag: `handle_vr_event` moves pointer focus to another KWin window even while a button is held.

Alternatives we considered:

- **Run floating apps directly on ft-screens.** It's a wlroots compositor, so apps could connect to it and get a panel per window. But they would get no drag and drop or clipboard with desktop apps unless we wrote a bridge. Also, a window that's already on the desktop could never be torn off, because a Wayland client can't change compositors. Rejected.
- **One large hidden "canvas" output.** Every floating window would sit on one big output, and each panel would show a crop of it (`SetOverlayTextureBounds`). That needs only one extra output, with no copies. But an 8K canvas uses about 128 MB per buffer, with two or three buffers in KWin's swapchain. It would also have to repack windows whenever one resized, full screen would fill the whole canvas, and every window would share one scale. This is the fallback if per-window outputs don't work.
- **Screencast single windows** (`zkde_screencast` `stream_window`, over PipeWire). This adds copies and latency, and the window still needs a real place in KWin's layout to receive input. Rejected.
- **SteamOS's own floating windows** (Launch a program from the dashboard). Those apps run in gamescope, outside KWin, so they can't drag and drop with the desktop.

### Where the extra outputs come from: spare outputs

KWin's nested backend opens its outputs at start (`--output-count`). The session starts KWin with the screen count plus `FLOAT_SLOTS` outputs (default 8). Each spare is disabled until it's needed, with `kscreen-doctor` (or in the session's `kwinoutputconfig.json`, so it starts disabled). Floating a window enables a spare, and docking the window disables it again. `FLOAT_SLOTS` limits how many windows can float at once, and changing it means restarting the desktop.

Checked in KWin 6.2.5's source (`src/backends/wayland/`, 2026-09-29):

- Disabling a nested output keeps its host window. `Output::applyChanges` only flips `enabled`, KWin stops rendering it, and Plasma drops its desktop view. So ft-screens keeps the same toplevel, and its screen numbers stay put.
- Each output's host window is titled `KDE Wayland Compositor WL-<n>`, with `- Output disabled` appended while it's disabled (`WaylandOutput::updateWindowTitle`, on every `enabledChanged`). ft-screens reads the title to tell screens (`WL-0` to `WL-<SCREENS-1>`) from spares, and to see a spare turn on and off.
- Pointer positions reach KWin only through motion events: the output's position in the layout plus the position on its window. When ft-screens stops sending motion, KWin's pointer stays put.
- **Virtual outputs don't work.** `createVirtualOutput` makes an output window but never adds it to the backend's `m_outputs`, so `findOutput()` returns null when the pointer enters it, and the next line dereferences it (`Q_ASSERT` is compiled out). A click on such a panel would crash KWin. This rules out the virtual-output fallback (`stream_virtual_output`) without a patched KWin.

ft-screens creates a `screen` for each toplevel in the order they appear, and indexes its settings by that order. Spares come after the screens, so they get indices `SCREENS` and up. Their panels are hidden while their output is disabled.

## How the parts fit together

```
  KWin script "frametop-float"                ft-floatd (host, Python)                 ft-screens
  window events, moves, menus  ── D-Bus ──▶   window ↔ output ↔ panel table   ── @ft_screens ──▶   panels, controls,
  runs commands                ◀─ long poll ─  spare outputs (kscreen-doctor)  ◀─ @frametop_float ─  lasers, ghost, catcher
```

- **KWin script `frametop-float`** (JavaScript). A script keeps working across KWin updates. A C++ effect would have to match the host's exact KWin build, and our build container is Fedora, not SteamOS. The script watches windows (`windowAdded`/`windowRemoved`, `interactiveMoveResizeStarted`/`Stepped`/`Finished`, `outputChanged`, `minimizedChanged`, `windowActivated`, `fullScreenChanged`). It runs commands: move a window to an output, set its geometry, put it on all virtual desktops, and restore it. It adds "Float in VR" to the window menu (`registerUserActionsMenu`) and registers a shortcut (`registerShortcut`, Meta+Shift+F). KWin scripts can call D-Bus but can't serve it, so commands come back through a long poll. The script calls ft-floatd's `NextCommand`, which answers when a command is ready, and then the script calls it again. The fallback is loading one-shot scripts through `org.kde.kwin.Scripting`, the way kdotool does. All of these API names are present in the host's KWin 6.2.5.
- **ft-floatd** (Python). The host has dbus-python and PyGObject. It owns `org.frametop.Float` on the session's private bus, and it keeps the table of which window is on which output and panel. It enables and disables spare outputs and sets their size, scale, and position with `kscreen-doctor`, as ft-layout does. It tells ft-screens where each floating window goes and tells the script which window goes where. It also remembers each app's placement and scale, keyed by desktop file name.
- **ft-screens.** `Screen` becomes a panel with a kind: screen or floating window. Floating panels get the same bar, curve, roll, resize tab, and wrist and head pins, plus close, "back to desktop", and scale buttons. New parts are the tear-off ghost, the catcher, popup overlays, carrying a panel during a KWin move, and the dock target highlight. `MAX_SCREENS` goes from 8 to 16. Commands arrive on `@ft_screens`. Events go out to `@frametop_float` from an unbound socket, the same way ft-screens talks to the input relay.
- **Session script.** Adds `FLOAT_SLOTS` to the output count, starts ft-floatd, and enables the KWin script in the session's `kwinrc`.
- **ft-layout.** Arranges only the screens' outputs. Today it arranges everything in `kscreen-doctor -j`, so it has to skip the spares (`WL-<SCREENS>` and up), enabled or not.
- **ft-pointer.** Changes to the drag lock (see "Drag and drop between panels").
- **Frametop Display Settings.** Gets a Floating windows section: slots, margin, and "bring a window in front of you when it's activated".

Program names stay within 15 characters (`ft-floatd`). Overlay keys are `frametop.float.N` and `frametop.float.N.bar`, and so on.

## A floating window

- **Output and margin.** Its output is the window's frame plus a margin on each side (`FLOAT_MARGIN`, default 300 px). KWin keeps a Wayland popup inside its parent's output (`XdgPopupWindow::updateRelativePlacement` uses the output's placement area), so the margin gives menus and dropdowns room past the window's edges. X11 apps place their own menus within the monitor, so the same applies. Enabled spares sit apart from the screens and from each other in KWin's layout, so nothing spills from one to the next. Memory: a 1600 × 1000 window with a 300 px margin is about 14 MB per buffer, 42 MB for three.
- **What the panel shows.** Only the window's frame: ft-screens crops the output's buffer with `SetOverlayTextureBounds` and maps mouse positions through the crop. Each open popup gets a small overlay of its own, cut from the same buffer and placed a few millimetres in front of the window, so the main panel never changes size. KWin tells scripts about popups as windows of their own (`windowAdded` with `popupWindow`), so the script reports their rectangles.
- **Size and scale.** The panel's width is the window's pixel width times the source screen's metres per pixel, so text stays the same size in VR. A window launched floating uses the primary screen's density. Each window also has a scale (KWin's output scale), changed with Meta+scroll over the window or +/- on its bar and remembered for each app. A bigger scale makes the content bigger at the same panel size.
- **Window state.** An ordinary window, not maximized, placed inside its output with the margin around it, and set to show on all virtual desktops. It keeps its title bar and border. Apps that draw their own title bar (GTK, Chromium) keep theirs.
- **Moving.** Press the title bar. KWin starts an interactive move and the script reports it. ft-screens then stops forwarding pointer motion to KWin, so KWin's pointer stays at the press point and the window moves by nothing. Meanwhile ft-screens carries the panel with the pressing device, the same way the bar does today: it follows rigidly, scroll pushes and pulls, and the 3D mouse's right-drag tilts. When the button comes up, KWin gets the release at the press point. The bar under the panel works too.
- **Resizing.** The window's own edges (inside the margin, so KWin's resize works as on the desktop) and Frametop's corner tab both change the window's size in pixels at the same density, so the app lays itself out again. ft-floatd resizes the output to keep the margin, and the panel grows or shrinks around the window's top-left corner. A screen's tab only scales the panel. Resizing is throttled to about 20 updates a second, with a minimum of 320 × 200, like screens.
- **Full screen.** The window fills its own panel: the margin drops to zero while it's full screen, and the output is the panel's size in pixels. The panel keeps its size and place. On leaving full screen, the margin comes back.
- **Buttons.** The close button closes the window. "Back to desktop" docks it where it came from.
- **Minimize.** Minimizing, from the title bar or the taskbar, hides the panel, and restoring it shows the panel again. Floating windows stay in the desktop's taskbar and in Alt+Tab.
- **Activated out of view.** When a floating window is activated (taskbar, Alt+Tab, a notification) and it's more than about 60° from where you're looking, it's focused and a glow at the edge of your view points to it. A setting moves it in front of you instead.
- **New windows.** A dialog of a floating window (`transientFor`) floats in front of its parent. Other new windows of a floating app float too: where that app's windows went last time, otherwise to the parent's right at the same distance, curving around you, and to its left if that's taken. When every spare is in use, the window opens on the screen used last and a notification says so.
- **Show Desktop.** Meta+D leaves floating windows alone.

## Tearing a window off a screen

1. Press a desktop window's title bar and drag it. KWin starts a move, and the script tells ft-floatd, which tells ft-screens: `move-start <output> <window> <rect>`.
2. While the button is held, the laser leaves every Frametop panel by more than a small dead zone (a few centimetres past the edge). Letting go inside the dead zone is an ordinary drop.
3. ft-screens shows a ghost: an overlay showing the screen's live buffer cropped to the window (`SetOverlayTextureBounds`, no copy). It's at the screen's pixel density and distance, on the laser, facing you, with the point you grabbed under the laser. The ghost takes mouse input, so SteamVR's laser lands on it and the release comes to ft-screens.
4. Go back onto a screen before letting go, and the ghost disappears. It's an ordinary move again.
5. Let go on the ghost, and ft-screens releases the button in KWin, which ends the move. It then reports the tear-off to ft-floatd, with the window, the ghost's pose, and the density. ft-floatd enables a spare output and has ft-screens size it to the window plus the margin and put its panel at the ghost's pose. Then it has the script move the window onto that output. The ghost stays until the new panel's first frame at the right size arrives, so nothing blinks.

## Putting it back

- **Button.** "Back to desktop" returns the window to the screen, position, and size it had before it was torn off (saved at tear-off).
- **Dragging.** Carry the floating window, by its title bar or its bar, until the spot you're pointing at is on a screen. Then push it flush with the screen, within about 10 cm of its surface: scroll away with the mouse, or move the controller forward. The screen shows where the window will land, and letting go docks it there at its current size in pixels, shrunk to fit if the screen is smaller. A carried panel keeps its distance, so moving a floating window in front of a screen never docks it by accident.
- Docking disables the output and removes the panel.

## Getting at it: the float key and the title bar button

Settled on 2026-09-30 (decisions 22 to 25).

- **The float key.** The input relay owns it: the action `float_toggle`, bound to Meta+Shift+F unless the rules file says otherwise (a rules file with no `key_bindings` gets that default; one with its own list, even an empty one, doesn't). It can be rebound or removed in Frametop Input Settings, and mapped to a mouse button or a Frame controller button like any other action. The relay takes the combination before it reaches the desktop and sends `float pointer` to ft-floatd, which asks the script for the window under KWin's pointer (`workspace.cursorPos`, top of `workspace.stackingOrder`, popups and dialogs counting as their parent). With none there, the wallpaper or the taskbar, it's the active window. KWin's pointer is where the 3D mouse or a laser last was on a Frametop panel. A window that floats docks; any other floats. The KWin script no longer registers a shortcut of its own, so one press can't float a window and dock it again.
- **Docking everything.** `dock_all` (no default binding) sends `dock all`, which docks every floating window where it came from.
- **The title bar button.** Breeze can't take a button of its own, and a C++ fork of it would have to match SteamOS's exact KDecoration build (Plasma 6.3 replaces KDecoration2 with KDecoration3). So the Frametop desktop gets its own window decoration, written in QML for KWin's Aurorae engine, which loads it without compiling (`decoration/`, installed to `~/.local/share/kwin/decorations/kwin4_decoration_qml_frametop`, chosen in the session's `kwinrc` only, so Desktop Mode keeps Breeze). It's drawn to look like Breeze, with a float button left of Close. The button calls `requestToggleKeepBelow()`, the one window request a decoration can make that has no visible effect here, and the KWin script reads the change: keep-below set on a window on the screens floats it, cleared on a floating window docks it. The script keeps keep-below set on every floating window, however it was floated, so the button shows its dock icon there. A window alone on its own output loses nothing by being kept below (only the wallpaper is under it). If the window can't float (every spare is in use), the script clears the flag again. Keep Below Others in a window's menu does the same as the button.
- **Apps that draw their own title bar** (Chromium and Electron apps, GTK apps) never show KWin's decoration, so they don't get the button. They use the key, or the window menu (Alt+F3).

## Launching an app floating

- **In the desktop.** Use "Float in VR" in any window's menu, its title bar button, or the float key.
- **From the menu.** Right-click an app in the Application Launcher, or in the taskbar (where it starts another window of that app), and pick "Launch as Standalone" (decision 26). The launcher has no way to add an entry to every app's menu, but its menu shows each app's own desktop actions. So the Frametop desktop reads copies of the apps' desktop files with one more action added. They're written to `~/.local/share/frametop/apps/applications` from every desktop file in `XDG_DATA_DIRS`: by the session script before Plasma starts, and by ft-floatd whenever an app is installed, changed, or removed. The session puts `~/.local/share/frametop/apps` first in `XDG_DATA_DIRS`. Plasma's app cache is keyed by those directories, so Desktop Mode never sees the copies. Desktop files in `~/.local/share/applications` come before every data dir, so an app you've customized there keeps your copy and has no Launch as Standalone. The action runs `ft-float launch <desktop file name>`.
- **From a command.** `ft-float run <command>` and `ft-float launch <app.desktop>` start an app and float its first window. ft-floatd records the process it started, and the script matches new windows by PID, including child processes. Some single-instance apps (Firefox, D-Bus-activated apps) open the window from a process that was already running. Those are matched by desktop file name within a few seconds, or by `XDG_ACTIVATION_TOKEN` where the app honors it.
- **From the headset with the desktop off.** A profile's launcher entry starts the desktop in that profile, and a profile can hide every screen and hold only floating apps (`docs/profiles.md`). There's no separate Frametop Apps entry or picker.
- **Remembered placement.** Each app's last floating pose, size, and scale, keyed by desktop file name. A profile's own placement wins when the profile opens the app.

## Drag and drop between panels

KWin handles the protocols: Wayland, X11 through Xwayland, and the portal's file transfer. Frametop has to get the pointer right between panels.

- **Crossing panels.** When the laser moves onto another panel mid-drag, ft-screens gives that panel's KWin window pointer focus. KWin puts its cursor at that output's position, and the drop target gets enter and motion events. Floating windows add nothing new here, but they make gaps between panels the normal case.
- **Gaps (the catcher).** While the laser is between panels, none of our overlays get its events, and ft-screens clears pointer focus on `FT_LEAVE` even with a button held. If you let go in empty space, the release never reaches KWin, and the drag or move stays stuck until the next click. The fix: while a button is held on a Frametop panel and the laser leaves all of them, ft-screens puts an invisible catcher overlay on the laser (the tear-off ghost is the same thing with a picture on it). A release on the catcher releases in KWin wherever the pointer last was. Dropping in a gap cancels, just as dropping outside any window does. The pointer helper also tells ft-screens when the mouse's left button comes up, in case the catcher misses it. This also fixes window moves and drags that end off a panel today.
- **The 3D mouse's drag lock.** While the button is held, the drag lock keeps the cursor at its distance and stops hit tests. So a drag onto a nearer panel passes behind it, and a farther panel works only if SteamVR's laser happens to reach it. The change: while the button is held, keep testing the other panels (not the one pressed on) and move onto a panel the ray meets. Off the edge of the pressed panel, keep that panel's plane, as now, so moves and resizes past the edge still work.
- **Drag icon.** KWin 6 draws the drag icon as part of its scene, so it should show on the panel under the laser (to check). In a gap it stops at the source panel's edge. A later version can show a small drag proxy on the catcher.
- **Flatpak apps.** Dropping files into a sandboxed app goes through the document portal, the same path that the session script's file-picker fix covers. Test it explicitly, for example Dolphin to Brave.

## Things that must keep working

- **Typing follows the last click.** A click on a floating panel counts as a click on the desktop, since the window is a KWin window.
- **Visibility.** Floating windows follow the screens' rules: the hide hotkey, the visibility modes, and hiding during a VR game unless the dashboard is open. Controllers' lasers are off in games.
- **Headset standby.** Nothing new may poll SteamVR with new clients, so no new `vrcmd` loops.
- **The pointer helper's overlay list.** The helper learns about overlays by running `vrcmd --overlays` in the background, so a new floating panel appears in its next listing. Check the delay after a tear-off. If it's too long, ft-screens can send the helper new overlay keys directly.
- **Plasma.** An enabled floating output gets a desktop view (wallpaper) under its window, hidden by the crop. Plasma doesn't add panels to new outputs by default. A floating output must never become primary. With spare outputs, the output count stays the same, which avoids the lost-taskbar problem in design.md's open questions.
- **Restarting the desktop** closes every window, floating ones included. Each app's placement is remembered, so an app launched floating again comes back where it was.

## Plan

### Step 1: the catcher and the branches

- The catcher (see "Gaps"), as a fix that stands on its own, so it can go to `main` by itself.
- Merge `pointer-ignore` and `layouts-headpin`.

### Phase 0: find out

Each item has a pass condition. Items that need a desktop restart with extra outputs wait until the headset is free, or run in the headless test mode from 0.1.

- **0.1 Headless test mode.** `ft-screens --no-vr` runs the compositor without SteamVR. It logs toplevels, titles, and sizes, and answers commands on a separate control socket name. This lets KWin and output experiments run without the headset, and without touching the running desktop.
- **0.2 Outputs.** Start KWin with spare outputs, then disable and re-enable one with `kscreen-doctor`. Pass: the toplevel stays and its title changes (as the source says), a disabled output costs no frames, resizing a spare through configure works, and outputs with gaps between them are accepted. Also: an output larger than its window with the window placed inside, and a popup placed in the margin.
- **0.3 KWin script API on 6.2.5.** Move signals fire for moves from both KWin's title bars and apps' own. `sendClientToScreen` and `frameGeometry` work on another output, the `callDBus` long poll works, and `registerUserActionsMenu` works. Popups show up in `windowAdded` with their geometry. The observers can load into the running desktop through `org.kde.kwin.Scripting` and move nothing, so this is safe while the headset is in use.
- **0.4 Frozen-pointer move.** Pass: a KWin move with no pointer movement doesn't shift the window.
- **0.5 SteamVR's laser.** Find out which overlay gets MouseMove and ButtonUp when a held laser moves from overlay A to overlay B, and when it's released over nothing, for both a controller and the 3D mouse. Pass: an interactive overlay placed on the laser reliably catches the release.
- **0.6 Texture bounds.** Pass: `SetOverlayTextureBounds` crops a DMA-BUF (`SharedTextureHandle`) overlay correctly, mouse positions map to the cropped area, and two overlays can show different crops of one buffer.

#### Results (2026-09-29, headless, KWin 6.2.5)

`screens/test/headless.sh` runs these: ft-screens `--no-vr` with a bare nested KWin next to the running desktop, with an `input` command that feeds pointer events as if from a panel, KWin scripts loaded over D-Bus, and screenshots through ScreenShot2.

- **0.1 passes.** `--no-vr`, `--control`, `toplevels`, and `input` are in ft-screens.
- **0.2 passes.** Disabling a spare with `kscreen-doctor` keeps its toplevel, its title gets `- Output disabled`, and it stops committing; enabling it resumes on the same toplevel. A spare resized (`size`) while disabled comes up at the new size on its first frame, so a tear-off needn't blink. Live resizing works, output scale works (1.5: KWin lays out 1067 × 667 on a 1600 × 1000 buffer), and outputs with gaps between them (x = 5000, 8000, 10000) are accepted. A window placed inside a 1600 × 1200 output with a 300 px margin opens a context menu past its bottom and right edges, into the margin.
- **0.3 mostly passes.** `workspace.screens`, `sendClientToScreen`, `windowList`, `frameGeometry` (set; it applies asynchronously), `callDBus`, `registerShortcut`, `registerUserActionsMenu`, and `readConfig` exist. `windowAdded` reports popups (`popupWindow` true, `transient` true) with their geometry. Move signals fire for KWin's title bars (`interactiveMoveResizeStarted` with `move` true, `Stepped` with the geometry, `Finished`). Not yet checked: apps' own title bars, the `callDBus` long poll, and the window menu entry. `globalThis` isn't defined in KWin's script engine. `print` goes to the journal unless `QT_FORCE_STDERR_LOGGING=1`.
- **0.4: KWin starts the move on the press itself,** before any motion. So ft-screens freezes pointer motion as soon as a press lands in a floating window's title bar band (from the frame and client rectangles ft-floatd sends it), with no round trip. For apps that draw their own title bars, the script reports the move and ft-screens freezes then; the script puts back any few pixels the window slipped before that.
- **Found and fixed:** KWin's nested backend ignores the position in `wl_pointer.enter`, and wlroots drops a motion to the position it entered at, so the first click after crossing onto another screen landed where KWin's pointer had been. ft-screens now enters one unit off.
- **0.5 and 0.6** need SteamVR and the headset.

### Phase 1: float a window from its menu

Build the KWin script, ft-floatd, and floating panels in ft-screens: the margin and popup overlays, controls, moving by the title bar, resizing (edges and tab), scale, full screen, close, and back to desktop. Add drag and drop across panels, with the pointer helper change.

Done when:

- Dolphin and Kate float from "Float in VR".
- A file drags from the floating Dolphin to the floating Kate, to a screen, and back.
- The clipboard works between them.
- A menu near a floating window's edge opens past the edge.
- Closing and docking give the output back.
- Frame pacing and GPU memory are measured with several floating windows.

### Phase 2: tear off and dock by dragging

The ghost and tear-off, and the dock highlight with push-flush docking.

### Phase 3: launch floating

`ft-float run` and `ft-float launch`, window matching, new windows of floating apps, Launch as Standalone, remembered placement, and the notification when every spare is in use.

### Phase 4: polish

The drag proxy, the glow toward a window activated out of view (and the setting to bring it in front), the Display Settings section, and docs (design.md, reference.md, and the Use table in the README).

## Risks

- A SteamOS update can change KWin's script API or its nested backend. The script and the output handling are the parts to recheck after one.
- GPU memory: each floating output has its own swapchain of two or three buffers, including the margin. The Frame has 16 GB shared, with about 4 GB free in normal use (2026-09-29).
- Frame pacing with many panels hasn't been measured (already an open question in design.md). Each output is a separate render pass in KWin.
