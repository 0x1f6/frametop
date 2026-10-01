# Frametop

Frametop puts a multi-monitor KDE Plasma desktop into SteamVR on the Valve Steam Frame, and lets a Bluetooth mouse drive all of SteamVR. It installs and runs on the headset itself.

Each screen is its own monitor with its own resolution, so you can have an ultrawide in the middle and two portrait screens beside it, at whatever size and distance you like. The screens come back to your saved layout when the desktop starts. You can move, resize, curve, and roll them, pin one to your wrist, and put them all back with a shortcut.

The mouse shows up as a small dot anchored in the room. It works on the SteamVR dashboard, Steam, overlays, and the desktop, and it hands the laser back to your controllers when you pick one up.

It comes with two settings apps, Frametop Display Settings for the screens and Frametop Input Settings for mice, keyboards, and button mappings, plus fixes that let Bluetooth LE mice and keyboards like the Swiftpoint Z3 reconnect after they sleep.

When you're not wearing the headset, Frametop can turn its displays off and keep it awake on the charger, so you can still reach it remotely. This works even on a stand or mount that makes the headset seem worn.

Frametop is an independent project, not made by or affiliated with Valve.

## Install on the headset

You need a Steam Frame with an internet connection, a keyboard (Bluetooth, or the on-screen one), and about 3 GB of free space.

1. In the launcher, choose Launch a program → Desktop.
2. In the application menu, open System → Konsole.
3. Clone the repo and run the installer:

   ```
   git clone https://github.com/DeeJanuz/frametop.git ~/frametop
   cd ~/frametop
   ./install.sh
   ```

   The installer sets up distrobox in your home folder (the system files aren't touched), a Fedora build container, and everything else. The first run downloads 1–2 GB. It asks you three things along the way: whether to install the Bluetooth fixes, whether to install hand tracking (experimental), and whether to restart SteamVR. The Bluetooth fixes and hand tracking need your `sudo` password; if you've never set one, run `passwd` first, or skip them for now. SteamVR has to restart once at the end, which closes everything open in VR, including the terminal. Rebooting the headset works too.

After the restart, Launch a program → Desktop opens the multi-screen desktop, with its screens arranged around where you're facing. Frametop Display Settings and Frametop Input Settings are in the desktop's application menu, under Settings.

If you work in the desktop for long stretches, or leave the headset on a stand, open Frametop Display Settings → Power. Turn on Stay awake while plugged in: by default Steam puts the Frame to sleep after an hour without input, even while it charges. And choose when the displays turn off while the headset isn't used. SteamVR turns them off a few seconds after you take the headset off, but a stand or mount that covers the proximity sensor inside it makes the headset seem worn, and its displays stay on all night.

### Add a Bluetooth mouse or keyboard

1. Pair it in Steam, under Settings → Bluetooth.
2. If you installed the Bluetooth fixes, apply them once for the new device with Frametop Input Settings → Bluetooth → Apply Bluetooth fixes (or `setup/bluetooth/install.sh run`). After that it reconnects on its own.
3. Move the mouse, and the dot appears where you're looking.

## Use

| Do this | To get this |
| --- | --- |
| Move the mouse | The dot moves around you and snaps onto whatever panel it's over |
| Click, right-click, scroll | Acts on the panel under the dot |
| Pick up a controller | The controller gets its laser back; move the mouse to take over again |
| Point near the bottom of a screen | Its controls fade in: the bar, the curve and roll buttons, and the resize tab on the corner |
| Drag the bar under a screen | Moves the screen; scroll while dragging to push it away or pull it closer. With the mouse, hold right while dragging to tilt it |
| Drag the tab on a screen's bottom right corner | Resizes the screen |
| Click the curve button (next to the bar) | Curves the screen around you, or flattens it |
| Drag the roll button sideways, or scroll on it | Rolls the screen; it snaps level near straight |
| While carrying a screen, sweep its laser across your other controller's ring, then let go | Pins it to that wrist, at its size and distance, as you hold it when you let go; it shows while you see its front. Grab its bar to adjust it (it stays pinned); sweep across the ring again to take it off |
| Set a screen to On your head (Frametop Display Settings, Visibility & pins) | Pins it to your head where it is, like a HUD. Grab its bar to move it; it stays on your head |
| Save as profile… (Frametop Display Settings, Layout & profiles) | Saves where the screens are, with their sizes and pins, which ones are hidden, and the open apps and where their windows are (on a screen or floating), under a name. Pick it under Arrangement and press Open profile to switch to it: the screens move, open windows of its apps go to their places, and the apps that aren't open start. Nothing closes |
| Pick a profile under Start in profile, or run its entry (Frametop: NAME) from SteamVR's Launch a program list | The desktop starts in that profile, or switches to it if it's running. A profile can also go on a key combination, mouse button, or controller button in Frametop Input Settings |
| Meta+Shift+R in the desktop | Puts the screens back in their layout (also in the menu as Reset Screen Layout, and mappable to a mouse button) |
| Meta+Shift+F over a desktop window | Floats that window in VR as a panel of its own, or puts it back on its screen if it floats. It acts on the window under the pointer, or the active one if the pointer is over the wallpaper. Rebind it, or map it to a mouse or controller button, in Frametop Input Settings (Keyboard page, or Buttons and Controllers as Float window in VR). Float in VR is also in every window's menu (Alt+F3), and the button left of Close in a window's title bar does the same (apps that draw their own title bar, like Chromium and Electron apps, don't have it) |
| Right-click an app in the Application Launcher (or the taskbar) and pick Launch as Standalone | Starts the app with its window floating in VR, where that app last floated, or in front of you the first time. From a terminal: `float/ft-float launch org.kde.dolphin`, or `float/ft-float run <command>` |
| Meta+Shift+H in the desktop | Hides or shows all screens (also in the menu as Hide/Show Screens, and mappable). The Visibility & pins tab of Frametop Display Settings can instead show them only with the dashboard open, or while you look at your wrist |
| Switch a screen to Hidden (Frametop Display Settings, Visibility & pins → Screens shown) | Hides just that screen until you switch it back, whatever the other visibility settings say; new windows that would open on it float instead |
| Leave the headset on a stand | Its displays turn off once it has gone unused for the time set in Frametop Display Settings → Power, even if the stand covers its proximity sensor. Pick it up, or use any mouse, keyboard, or button, and they come back on |
| Play a VR game | The screens hide and your controllers stay in the game. Open the SteamVR dashboard, or press Meta+Shift+H, to see and use them. To keep them visible over games, change During VR games on the Visibility & pins tab; the controllers still stay in the game, and you use the screens with the mouse or the dashboard |

You can map the mouse's extra buttons to actions such as Toggle SteamVR dashboard, Recenter pointer, or Head follow on/off on the Buttons page of Frametop Input Settings, and the Frame controllers' buttons on its Controllers page. Pointer speed, dot size, and the rest are on its Pointer page and take effect immediately. If a panel you only look at, such as a performance overlay that follows your view, keeps catching the dot, tick it (or its whole app) on the Ignored panels page, and the pointer passes through it. Head follow, which is experimental and off by default, makes the pointer come along when you turn your head: it stays put until your head turns past the leash angle, then glides back to its place in your view, and a leash of 0 keeps it fixed in your view. It's only lightly tested and not polished; tuning its settings, or improving how it feels, is open to anyone who wants to take it further.

Restarting the desktop (Restart desktop in Frametop Display Settings) closes its windows, but background work you started in it, such as servers, tmux sessions, or builds, keeps running.

### Experimental: gaze and hand tracking

Gaze mode makes the pointer go where you look. The installer doesn't set it up: run `gaze/run.sh install`, then turn it on and calibrate on the Gaze page of Frametop Input Settings. Meta+J left-clicks and Meta+K right-clicks where you look; hold the key and turn your head to correct the aim, then let go. The mouse's buttons work the same way, with the mouse doing the correcting, and the corrections teach the gaze tracker.

Hand tracking, which the installer offers, shows your hands through the screens. Turn it on with `ft-handsctl on` and off with `ft-handsctl off`. With `POINTER_HANDS=1` in `~/.config/frametop.conf`, a pinch clicks and a grip drags. [docs/reference.md](docs/reference.md) has the details of both.

### Leave the headset on a stand and reach it remotely

To keep the Frame on and connected while you're not wearing it, for SSH, remote desktop, or anything else running on it, open the Power tab in Frametop Display Settings:

- Turn off when unused for: how long the headset can go unused before its displays turn off (Never by default). Unused means the headset and controllers haven't moved and no mouse, keyboard, or button was used. SteamVR normally turns the displays off when its proximity sensor says the headset came off, but a stand or mount that covers the sensor makes the headset seem worn, so the displays stay on all night. This setting doesn't depend on the sensor. Pick the headset up or use any input, and the displays come back on.
- Stay awake while plugged in: stops Steam from putting the Frame to sleep while it charges. By default Steam puts it to sleep after an hour without input, even on the charger, which ends remote sessions. This is Steam's own Settings → Power → When Plugged In and Idle setting, so the power button still puts the Frame to sleep, and Steam's battery setting still applies.

With the displays off, the headset keeps tracking and rendering, so it uses about as much power as in use. Leave it on a charger that keeps up with that: a USB-C PD charger, not a 5 V one.

## Known limitations

This is an early release, tested on one Steam Frame (SteamOS 0.3.0 build 20260922, SteamVR 2.17.10).

- A SteamOS or SteamVR update can break parts of it until Frametop catches up. After an update, run `cd ~/frametop && scripts/doctor.sh` in a terminal. It checks what Frametop needs from SteamOS, and says what changed since the versions you last marked as working and what to try. Once everything works, `scripts/doctor.sh --mark-good` records the versions. If something stops working, please report it.
- The first install downloads 1–2 GB for the build container and compiles everything on the headset, which takes several minutes.
- During a VR game you can't show the screens with a controller button, because the game owns the buttons. Open the SteamVR dashboard, press Meta+Shift+H, or use a mapped mouse button instead.
- Flatscreen games aren't detected as games. If your controllers end up working the screens instead of the game, set Controllers on the screens to "Only with the SteamVR dashboard open" (Frametop Display Settings, Visibility & pins tab).
- Typing follows your last click. A controller click on a panel other than the screens (the dashboard, a Steam app) doesn't move typing there; click it with the mouse, or click a screen to bring typing back.
- The screens don't draw a mouse cursor of their own. The 3D mouse's dot or SteamVR's laser shows where you're pointing.
- On SteamVR's Settings page, the 3D mouse shows a laser beam and a larger hit dot, like a controller. SteamVR doesn't tell other programs where that page is (unlike Steam's pages, such as Library), so the mouse used to miss most of it: clicks went through to a desktop screen behind, and the dot disappeared. As a workaround, on that page only, the laser starts near your eye and SteamVR finds the page itself. See docs/design.md.
- Remote desktop over VNC (Frametop Remote Access in the app menu, or `./desktops.sh remote on`) needs Tailscale on the Frame. It shows the primary screen only. The app turns it on and off, shows the address, and shows, copies, or changes the VNC password. The password is made at random on the Frame and kept in `~/.config/frametop-remote` (only you can read it); VNC limits it to 8 characters, and the tailnet encrypts the connection. Turning it on in a desktop that started with it off takes a desktop restart.
- Turning the displays off on a stand only turns their backlight off. SteamVR has no way for other programs to put the headset in standby, so tracking and rendering keep running, and the headset draws nearly its full power.

## Reporting problems

In a terminal on the headset, run:

```
cd ~/frametop && scripts/report.sh
```

This writes `frametop-report-<date>.txt` with version numbers, service states, settings, and recent logs. Bluetooth addresses and the headset's serial number are masked. Then [open an issue](https://github.com/DeeJanuz/frametop/issues), describe what you did, what you expected, and what happened, and attach the file.

## Update

```
cd ~/frametop && git pull && ./install.sh
```

## Uninstall

```
./desktops.sh uninstall                # the launcher's Desktop entry goes back to the stock desktop
./desktops.sh relay uninstall
pointer/helper/run.sh uninstall
power/run.sh uninstall
pointer/driver/install.sh uninstall    # then restart SteamVR
input-settings/install.sh uninstall
display-settings/install.sh uninstall
remote/install.sh uninstall
setup/bluetooth/install.sh uninstall   # if you installed the Bluetooth fixes
hands/run.sh uninstall                 # if you installed hand tracking
gaze/run.sh uninstall                  # if you installed the gaze service
gaze/tracker/install.sh uninstall      # if you installed our own eye tracker's frame grabber
gaze/probe/install.sh uninstall        # if you installed the gaze probe
```

## How it works

A Plasma session runs nested inside ft-screens (`screens/`), a small Wayland compositor. KWin opens one window per screen, ft-screens sets each window's size, and each frame goes to SteamVR as an overlay without being copied. An input relay (`input/`) keeps Bluetooth mice working in SteamVR and feeds the mouse to the 3D pointer, which drives a virtual SteamVR controller (`pointer/`). A power service (`power/`) turns the displays off while the headset isn't used. [docs/reference.md](docs/reference.md) covers each piece, and [docs/design.md](docs/design.md) explains the design and what we learned about SteamVR on the Frame. [docs/hazards.md](docs/hazards.md) lists known ways the input handling can go wrong.

| Folder | What it is |
| --- | --- |
| `install.sh` | The one-step installer. Safe to re-run. |
| `desktops.sh` | Start, stop, and configure the desktop, and install the input relay. |
| `screens/` | ft-screens, the compositor (wlroots and OpenVR). |
| `session/` | The desktop session script and its config example. |
| `layout/` | ft-layout: where the screens float, and their sizes. |
| `float/` | Floating windows: ft-floatd and the KWin script that float a desktop window in VR. |
| `decoration/` | The desktop's window decoration: Breeze's look plus the float button. |
| `input/` | The input relay (Bluetooth mice and keyboards, button maps). |
| `pointer/` | The 3D mouse: SteamVR driver, helper service, and a probe tool. |
| `power/` | ft-powerd: turns the displays off while the headset isn't used. |
| `gaze/` | Gaze mode (experimental): the gaze service, its calibration panel, our own eye tracker, and the gaze probe. See [gaze/README.md](gaze/README.md). |
| `hands/` | Hand tracking (experimental): the camera broker and the tracker, for the hands over the screens and pinch clicks. See [hands/README.md](hands/README.md). |
| `display-settings/`, `input-settings/` | The two settings apps (Kirigami, Python). |
| `remote/` | Frametop Remote Access, the app that turns remote desktop over VNC on and off. |
| `setup/` | The build container and the Bluetooth fixes. See [setup/README.md](setup/README.md). |
| `scripts/` | Helpers the installers use. They run commands locally on the Frame, or over SSH from a PC. |

## Developing from a PC

The scripts also work from a Linux or WSL PC over SSH, which is easier for editing code. On the Frame they use the local checkout; on a PC they sync the repo to `~/dev/frametop` on the Frame and run there.

1. On the Frame, turn on developer mode, set a password with `passwd`, and enable SSH with `sudo systemctl enable --now sshd`. Add your public key to `~/.ssh/authorized_keys`. [deck-tailscale](https://github.com/tailscale-dev/deck-tailscale) lets you reach it from anywhere.
2. On the PC, add the Frame to `~/.ssh/config` as host `frame`, or set `FRAME_HOST`:

   ```
   Host frame
       HostName <the Frame's address>
       User steamos
       IdentityFile ~/.ssh/<your-key>
   ```

3. The Bluetooth fixes need `sudo`, and there's no terminal on the Frame to type the password into, so put it in `.env` at the repo root. It's gitignored and never synced:

   ```
   steamos_root_pwd="<password>"
   ```

Then run `./install.sh` from the PC. Daily use:

```
scripts/doctor.sh                  # is the Frame reachable and ready?
scripts/doctor.sh --mark-good      # and record the versions Frametop works with
scripts/sync.sh                    # copy the repo to ~/dev/frametop on the Frame
scripts/frame.sh '<cmd>'           # run in the dev container, in the Frame's copy
scripts/frame.sh -C <dir> '<cmd>'  # same, in a folder of the repo
scripts/frame.sh --host '<cmd>'    # run on the SteamOS host
```

The sync only goes one way. It makes the Frame's copy match your checkout, deleting files there that you've removed, and skips `.git`, `build/`, `.env`, and anything gitignored. Edit on the PC only, since the next sync overwrites changes made in `~/dev/frametop` on the Frame.

Programs built in the `dev` container link against its glibc, which is newer than the host's, so they run inside the container. The SteamVR driver is the exception and is built to run on the host (see `pointer/driver/build.sh`). [AGENTS.md](AGENTS.md) has the working rules, including what not to restart while someone is using the headset.

## License

MIT. See [LICENSE](LICENSE).
