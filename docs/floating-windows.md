# Floating windows (plan)

Status: plan only, on the `floating-windows` branch. Nothing here is built yet.

The goal is to let any desktop app float in VR in a panel of its own, like SteamVR's floating windows, while it stays part of the Frametop desktop. That means drag and drop, the clipboard, and focus keep working between floating windows and the screens.

- There are two ways to get a floating window. Launch the app floating, or drag a desktop window by its title bar off a screen and let go in the air.
- There are two ways to put one back. Drag it onto a screen, or press its "back to desktop" button.
- Files, text, and images drag between any two floating windows, and between floating windows and the screens.

## The approach: each floating window gets a KWin output of its own

Drag and drop and the clipboard only work between windows of the same compositor. A Wayland window can't move from one compositor to another. So a floating window has to stay a KWin window.

ft-screens already shows each KWin output as a panel. It sets the output's size with an `xdg_toplevel` configure, and KWin resizes the output to match. So a floating window can get an output of its own, sized to fit it, and ft-screens shows that output as a panel with its own controls. To KWin this is an ordinary desktop with more monitors. Dragging between two floating windows is the same as dragging between two monitors, which KWin already handles. ft-screens already moves the pointer between panels in the middle of a drag: `handle_vr_event` moves pointer focus to another KWin window even while a button is held.

Alternatives we considered:

- **Run floating apps directly on ft-screens.** It's a wlroots compositor, so apps could connect to it and get a panel per window. But they would get no drag and drop or clipboard with desktop apps unless we wrote a bridge. Also, a window that's already on the desktop could never be torn off, because a Wayland client can't change compositors. Rejected.
- **One large hidden "canvas" output.** Every floating window would sit on one big output, and each panel would show a crop of it (`SetOverlayTextureBounds`). That needs only one extra output, with no copies, and popups could extend past their window. But an 8K canvas uses about 128 MB per buffer, with two or three buffers in KWin's swapchain. It would also have to repack windows whenever one resized. Maximize and fullscreen would fill the whole canvas, and every window would share one scale. This is the fallback if per-window outputs don't work.
- **Screencast single windows** (`zkde_screencast` `stream_window`, over PipeWire). This adds copies and latency, and the window still needs a real place in KWin's layout to receive input. Rejected.
- **SteamOS's own floating windows** (Launch a program from the dashboard). Those apps run in gamescope, outside KWin, so they can't drag and drop with the desktop.

### Where the extra outputs come from

KWin's nested backend opens its outputs at start (`--output-count`). There are two ways to get more:

1. **Spare outputs (try first).** Start KWin with the screen count plus `FLOAT_SLOTS` outputs (default 6). Park each spare output until it's needed. If the nested backend supports it, disable the spare with `kscreen-doctor`. Otherwise make it tiny (64 × 64), put it far away in KWin's layout, and don't show its panel. Floating a window takes a spare, and docking the window gives the spare back. `FLOAT_SLOTS` limits how many windows can float at once, and changing it means restarting the desktop.
2. **Virtual outputs (fallback).** KWin 6.2.5's nested backend implements `createVirtualOutput` and `removeVirtualOutput` (checked in `libkwin.so`). A client reaches them through `zkde_screencast_unstable_v1`'s `stream_virtual_output` request. There's no limit on count, and the output gets whatever name we choose. But that request belongs to a screencast protocol, and only privileged clients may use it. A helper in the container can't be matched to a desktop file, so we would need `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1`. That turns off the permission checks for every client in the session. design.md also records a KWin 6.2.5 crash (`textureForOutput`) when such an output was streamed. It might be safe if nothing ever consumes the PipeWire stream, but that needs a test.

ft-screens has to tell a floating window's output from a screen's. KWin titles each nested output window `KDE Wayland Compositor %1`. If `%1` turns out to be the output name (to confirm), ft-screens can read the title. Otherwise it can go by creation order, since spare outputs come after the screens.

## How the parts fit together

```
  KWin script "frametop-float"                ft-floatd (host, Python)                 ft-screens
  window events, moves, menus  ── D-Bus ──▶   window ↔ output ↔ panel table   ── @ft_screens ──▶   panels, controls,
  runs commands                ◀─ long poll ─  spare outputs (kscreen-doctor)  ◀─ @frametop_float ─  lasers, ghost, catcher
```

- **KWin script `frametop-float`** (JavaScript). A script keeps working across KWin updates. A C++ effect would have to match the host's exact KWin build, and our build container is Fedora, not SteamOS. The script watches windows (`windowAdded`/`windowRemoved`, `interactiveMoveResizeStarted`/`Stepped`/`Finished`, `outputChanged`, `minimizedChanged`, `windowActivated`). It runs commands: move a window to an output, maximize it, put it on all virtual desktops, and restore it. It adds "Float in VR" to the window menu (`registerUserActionsMenu`) and registers a shortcut (`registerShortcut`, Meta+Shift+F). KWin scripts can call D-Bus but can't serve it, so commands come back through a long poll. The script calls ft-floatd's `NextCommand`, which answers when a command is ready, and then the script calls it again. The fallback is loading one-shot scripts through `org.kde.kwin.Scripting`, the way kdotool does. All of these API names are present in the host's KWin 6.2.5.
- **ft-floatd** (Python). The host has dbus-python and PyGObject. It owns `org.frametop.Float` on the session's private bus, and it keeps the table of which window is on which output and panel. It hands out spare outputs and sets their size, scale, and position with `kscreen-doctor`, as ft-layout does. It tells ft-screens where each floating window goes and tells the script which window goes where. It also remembers each app's placement, keyed by desktop file name.
- **ft-screens.** `Screen` becomes a panel with a kind: screen or floating window. Floating panels get the same bar, curve, roll, resize tab, and wrist pin, plus close and "back to desktop" buttons. New parts are the tear-off ghost, the catcher, carrying a panel during a KWin move, and the dock target highlight. `MAX_SCREENS` goes from 8 to 16. Commands arrive on `@ft_screens`. Events go out to `@frametop_float` from an unbound socket, the same way ft-screens talks to the input relay.
- **Session script.** Adds the spare outputs to the count, starts ft-floatd, and enables the KWin script in the session's `kwinrc`.
- **ft-layout.** Arranges only the screens' outputs. Today it arranges everything in `kscreen-doctor -j`, so it has to skip floating windows' outputs.
- **ft-pointer.** Changes to the drag lock (see "Drag and drop between panels").
- **Frametop Display Settings.** Gets a Floating windows section.

Program names stay within 15 characters (`ft-floatd`). Overlay keys are `frametop.float.N` and `frametop.float.N.bar`, and so on.

## A floating window

- **Size and scale.** Its output is the window's size, at the scale of the screen it came from. The panel's width is its pixel width times that screen's metres per pixel, so text stays the same size in VR.
- **Window state.** The window is maximized on its output and set to show on all virtual desktops. It keeps its title bar: Breeze drops the borders of a maximized window but keeps the title bar. Apps that draw their own title bar (GTK, Chromium) keep theirs.
- **Moving.** Press the title bar. KWin starts an interactive move and the script reports it. ft-screens then stops forwarding pointer motion to KWin, so KWin's pointer stays at the press point, the window moves by nothing, and it doesn't come out of maximize. Meanwhile ft-screens carries the panel with the pressing device, the same way the bar does today: it follows rigidly, scroll pushes and pulls, and the 3D mouse's right-drag tilts. When the button comes up, KWin gets the release at the press point. The bar under the panel works too.
- **Resizing.** The corner tab changes the window's size in pixels at the same density, so the app lays itself out again. A screen's tab only scales the panel. Resizing is throttled to about 20 updates a second, with a minimum of 320 × 200, like screens.
- **Buttons.** The close button closes the window. "Back to desktop" docks it where it came from.
- **Minimize.** Minimizing, from the title bar or the taskbar, hides the panel, and restoring it shows the panel again. Floating windows stay in the desktop's taskbar and in Alt+Tab. If one is activated while it's out of view, it can come around in front of you (optional).
- **New windows.** A dialog of a floating window (`transientFor`) floats in front of its parent. Other new windows open on the screens: if KWin puts a new window on a floating window's output, the script moves it to the screen used last.
- **Popups.** KWin keeps menus and tooltips inside their output, so a menu can't reach past the window's edges. The first version accepts that. A later version pads the output and crops the panel to the window plus any open popups (`SetOverlayTextureBounds`, with mouse positions offset by the crop).

## Tearing a window off a screen

1. Press a desktop window's title bar and drag it. KWin starts a move, and the script tells ft-floatd, which tells ft-screens: `move-start <output> <window> <rect>`.
2. While the button is held, the laser leaves every Frametop panel. A second trigger is also possible: scrolling toward you while dragging pulls the window out of the screen.
3. ft-screens shows a ghost: an overlay showing the screen's live buffer cropped to the window (`SetOverlayTextureBounds`, no copy). It's at the screen's pixel density and distance, on the laser, facing you, with the point you grabbed under the laser. The ghost takes mouse input, so SteamVR's laser lands on it and the release comes to ft-screens.
4. Go back onto a screen before letting go, and the ghost disappears. It's an ordinary move again.
5. Let go on the ghost, and ft-screens releases the button in KWin, which ends the move. It then reports the tear-off to ft-floatd, with the window, the ghost's pose, and the density. ft-floatd takes a spare output and has ft-screens size it to the window and put its panel at the ghost's pose. Then it has the script move the window onto that output and maximize it. The ghost stays until the new panel's first frame at the right size arrives, so nothing blinks.

## Putting it back

- **Button.** "Back to desktop" returns the window to the screen, position, and size it had before it was torn off (saved at tear-off).
- **Dragging.** Carry the floating window, by its title bar or its bar, until the spot you're pointing at is on a screen. Then push it flush with the screen, within about 10 cm of its surface: scroll away with the mouse, or move the controller forward. The screen shows where the window will land, and letting go docks it. A carried panel keeps its distance, so moving a floating window in front of a screen never docks it by accident.
- Docking gives the output back (disabled or parked) and removes the panel.

## Launching an app floating

- **In the desktop.** Use "Float in VR" in any window's menu, or press Meta+Shift+F for the active window.
- **From a command.** `ft-float run <command>` and `ft-float launch <app.desktop>` start an app and float its first window. ft-floatd records the process it started, and the script matches new windows by PID, including child processes. Some single-instance apps (Firefox, D-Bus-activated apps) open the window from a process that was already running. Those are matched by desktop file name within a few seconds, or by `XDG_ACTIVATION_TOKEN` where the app honors it.
- **From the headset without the desktop open.** Add one new launcher entry, "Frametop Apps", instead of one entry per app, since per-app entries would need setting up for every new app. The entry starts the Frametop session with the screens hidden (floats-only mode) and opens an app picker as a floating window. Picking an app launches it floating. The screens are still there: Meta+Shift+H shows them. Everything runs in one session, so dragging between a standalone app and a desktop app works. If the desktop is already running, the entry just opens the picker.
- **The picker.** Either KRunner, floated, or a small Kirigami app like the settings apps, with a grid of apps and their icons.
- **Visibility in floats-only mode.** The screens stay hidden and floating windows show. See open question 2 for how floating windows follow the visibility modes otherwise.

## Drag and drop between panels

KWin handles the protocols: Wayland, X11 through Xwayland, and the portal's file transfer. Frametop has to get the pointer right between panels.

- **Crossing panels.** When the laser moves onto another panel mid-drag, ft-screens gives that panel's KWin window pointer focus. KWin puts its cursor at that output's position, and the drop target gets enter and motion events. Floating windows add nothing new here, but they make gaps between panels the normal case.
- **Gaps.** While the laser is between panels, none of our overlays get its events. If you let go in empty space, the release never reaches KWin, and the drag or move stays stuck until the next click. The fix: while a button is held on a Frametop panel and the laser leaves all of them, ft-screens puts an invisible catcher overlay on the laser (the tear-off ghost is the same thing with a picture on it). A release on the catcher releases in KWin wherever the pointer last was. Dropping in a gap cancels, just as dropping outside any window does. The pointer helper also tells ft-screens when the mouse's left button comes up, in case the catcher misses it. This should also fix window moves that end off a panel today (to check).
- **The 3D mouse's drag lock.** While the button is held, the drag lock keeps the cursor at its distance and stops hit tests. So a drag onto a nearer panel passes behind it, and a farther panel works only if SteamVR's laser happens to reach it. The change: while the button is held, keep testing the other panels (not the one pressed on) and move onto a panel the ray meets. Off the edge of the pressed panel, keep that panel's plane, as now, so moves and resizes past the edge still work.
- **Drag icon.** KWin 6 draws the drag icon as part of its scene, so it should show on the panel under the laser (to check). In a gap it stops at the source panel's edge. A later version can show a small drag proxy on the catcher.
- **Flatpak apps.** Dropping files into a sandboxed app goes through the document portal, the same path that the session script's file-picker fix covers. Test it explicitly, for example Dolphin to Brave.

## Things that must keep working

- **Typing follows the last click.** A click on a floating panel counts as a click on the desktop, since the window is a KWin window.
- **VR games.** Floating windows follow the screens' rules: they're hidden during a game unless the dashboard is open, and controllers' lasers are off in games.
- **Headset standby.** Nothing new may poll SteamVR with new clients, so no new `vrcmd` loops.
- **The pointer helper's overlay list.** The helper learns about overlays by running `vrcmd --overlays` in the background, so a new floating panel appears in its next listing. Check the delay after a tear-off. If it's too long, ft-screens can send the helper new overlay keys directly.
- **Plasma.** Each enabled floating output gets a desktop view (wallpaper) under its window. Plasma doesn't add panels to new outputs by default. A floating output must never become primary. With spare outputs, the output count stays the same, which avoids the lost-taskbar problem in design.md's open questions.
- **Show Desktop.** Meta+D would hide floating windows. The script can leave them out, or we accept it.
- **Restarting the desktop** closes every window, floating ones included. Each app's placement is remembered, so an app launched floating again comes back where it was.

## Plan

### Phase 0: find out

Each item has a pass condition. Items that need a desktop restart with extra outputs wait until the headset is free, or run in the headless test mode from 0.1.

- **0.1 Headless test mode.** `ft-screens --no-vr` runs the compositor without SteamVR. It logs toplevels, titles, and sizes, and answers commands on a separate control socket name. This lets KWin and output experiments run without the headset, and without touching the running desktop.
- **0.2 Outputs.** Start KWin with spare outputs, then disable and re-enable one with `kscreen-doctor`. Pass: we know whether its toplevel is destroyed and recreated or stays. Also confirm that the title carries the output name, that resizing a spare through configure works, and that outputs with gaps between them are accepted. Fallback test: `stream_virtual_output` with no PipeWire consumer creates a toplevel and doesn't crash.
- **0.3 KWin script API on 6.2.5.** Move signals fire for moves from both KWin's title bars and apps' own. `sendClientToScreen` and `setMaximize` work on another output, the `callDBus` long poll works, and `registerUserActionsMenu` works. Also check whether popups show up in `windowAdded` (needed for the crop later). The observers can load into the running desktop through `org.kde.kwin.Scripting` and move nothing, so this is safe while the headset is in use.
- **0.4 Frozen-pointer move.** Pass: a KWin move on a maximized window with no pointer movement neither unmaximizes nor shifts it.
- **0.5 SteamVR's laser.** Find out which overlay gets MouseMove and ButtonUp when a held laser moves from overlay A to overlay B, and when it's released over nothing, for both a controller and the 3D mouse. Pass: an interactive overlay placed on the laser reliably catches the release.
- **0.6 Texture bounds.** Pass: `SetOverlayTextureBounds` crops a DMA-BUF (`SharedTextureHandle`) overlay correctly, and mouse positions map to the cropped area.

### Phase 1: float a window from its menu

Build the KWin script, ft-floatd, and floating panels in ft-screens: controls, moving by the title bar, resizing, close, and back to desktop. Add drag and drop across panels, with the catcher and the pointer helper change.

Done when:

- Dolphin and Kate float from "Float in VR".
- A file drags from the floating Dolphin to the floating Kate, to a screen, and back.
- The clipboard works between them.
- Closing and docking give the output back.

### Phase 2: tear off and dock by dragging

The ghost and tear-off, and the dock highlight with push-flush docking.

### Phase 3: launch floating

`ft-float run` and `ft-float launch`, window matching, the Frametop Apps launcher entry, floats-only mode, the picker, and remembered placement.

### Phase 4: polish

Popups past the window's edges (pad and crop), the drag proxy, bringing a window into view when it's activated, the Display Settings section, and docs (design.md, reference.md, and the Use table in the README).

## Open questions

1. **Dock gesture.** Push flush (recommended), hover and wait, or the button only?
2. **Visibility.** Should floating windows follow the hide hotkey and the visibility modes like the screens, or have their own setting?
3. **Title bars.** Keep them on floating windows (recommended), or go borderless with only Frametop's controls?
4. **Standalone entry.** One Frametop Apps entry (recommended), or add each app to Launch a program?
5. **Count.** How many windows may float at once? This matters only if virtual outputs don't work out (default 6 spares).
6. **Windows opened by a floating app.** Float them too, or open them on the screens? Dialogs float either way.

## Risks

- If the nested backend can't disable outputs, parked spares still work, but each one keeps a Plasma desktop view.
- A SteamOS update can change KWin's script API or its nested backend. The script and the output handling are the parts to recheck after one.
- GPU memory: each floating output has its own swapchain of two or three buffers. A 1600 × 1000 window is about 6.4 MB per buffer.
- Frame pacing with many panels hasn't been measured (already an open question in design.md). Each output is a separate render pass in KWin.
