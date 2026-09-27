# Frametop

Frametop puts a multi-monitor KDE Plasma desktop into SteamVR on the Valve Steam Frame, and lets a Bluetooth mouse drive all of SteamVR. It installs and runs on the headset itself.

Each screen is its own monitor with its own resolution, so you can have an ultrawide in the middle and two portrait screens beside it, at whatever size and distance you like. The screens come back to your saved layout when the desktop starts. You can move, resize, curve, and roll them, pin one to your wrist, and put them all back with a shortcut.

The mouse shows up as a small dot anchored in the room. It works on the SteamVR dashboard, Steam, overlays, and the desktop, and it hands the laser back to your controllers when you pick one up.

It comes with two settings apps, Frametop Display Settings for the screens and Frametop Input Settings for mice, keyboards, and button mappings, plus fixes that let Bluetooth LE mice and keyboards like the Swiftpoint Z3 reconnect after they sleep.

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

   The installer sets up distrobox in your home folder (the system files aren't touched), a Fedora build container, and everything else. The first run downloads 1–2 GB. It asks you two things along the way. The Bluetooth fixes need your `sudo` password; if you've never set one, run `passwd` first, or skip them for now. SteamVR has to restart once at the end, which closes everything open in VR, including the terminal. Rebooting the headset works too.

After the restart, Launch a program → Desktop opens the multi-screen desktop, with its screens arranged around where you're facing. Frametop Display Settings and Frametop Input Settings are in the desktop's application menu, under Settings.

If you work in the desktop for long stretches, stop Steam from putting the headset to sleep while it's plugged in: in Steam, open Settings → Power, and under When Plugged In and Idle set Sleep after to Never. By default Steam suspends the Frame after an hour without input, even while it charges. The displays still turn off a few seconds after you take the headset off.

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
| Meta+Shift+R in the desktop | Puts the screens back in their layout (also in the menu as Reset Screen Layout, and mappable to a mouse button) |
| Meta+Shift+H in the desktop | Hides or shows all screens (also in the menu as Hide/Show Screens, and mappable). The Visibility & wrist tab of Frametop Display Settings can instead show them only with the dashboard open, or while you look at your wrist |
| Play a VR game | The screens hide and your controllers stay in the game. Open the SteamVR dashboard, or press Meta+Shift+H, to see and use them. To keep them visible over games, change During VR games on the Visibility & wrist tab; the controllers still stay in the game, and you use the screens with the mouse or the dashboard |

You can map the mouse's extra buttons to actions such as Toggle SteamVR dashboard or Recenter pointer on the Buttons page of Frametop Input Settings. Pointer speed, dot size, and the rest are on its Pointer page and take effect immediately.

Restarting the desktop (Restart desktop in Frametop Display Settings) closes its windows, but background work you started in it, such as servers, tmux sessions, or builds, keeps running.

## Known limitations

This is an early release, tested on one Steam Frame (SteamOS 0.3.0 build 20260922, SteamVR 2.17.10).

- A SteamOS or SteamVR update can break parts of it until Frametop catches up. If something stops working after an update, please report it.
- The first install downloads 1–2 GB for the build container and compiles everything on the headset, which takes several minutes.
- During a VR game you can't show the screens with a controller button, because the game owns the buttons. Open the SteamVR dashboard, press Meta+Shift+H, or use a mapped mouse button instead.
- Flatscreen games aren't detected as games. If your controllers end up working the screens instead of the game, set Controllers on the screens to "Only with the SteamVR dashboard open" (Frametop Display Settings, Visibility & wrist tab).
- Typing follows your last click. A controller click on a panel other than the screens (the dashboard, a Steam app) doesn't move typing there; click it with the mouse, or click a screen to bring typing back.
- The screens don't draw a mouse cursor of their own. The 3D mouse's dot or SteamVR's laser shows where you're pointing.
- Remote desktop over VNC (`./desktops.sh remote on`) needs Tailscale on the Frame.

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
pointer/driver/install.sh uninstall    # then restart SteamVR
input-settings/install.sh uninstall
display-settings/install.sh uninstall
setup/bluetooth/install.sh uninstall   # if you installed the Bluetooth fixes
```

## How it works

A Plasma session runs nested inside ft-screens (`screens/`), a small Wayland compositor. KWin opens one window per screen, ft-screens sets each window's size, and each frame goes to SteamVR as an overlay without being copied. An input relay (`input/`) keeps Bluetooth mice working in SteamVR and feeds the mouse to the 3D pointer, which drives a virtual SteamVR controller (`pointer/`). [docs/reference.md](docs/reference.md) covers each piece, and [docs/design.md](docs/design.md) explains the design and what we learned about SteamVR on the Frame.

| Folder | What it is |
| --- | --- |
| `install.sh` | The one-step installer. Safe to re-run. |
| `desktops.sh` | Start, stop, and configure the desktop, and install the input relay. |
| `screens/` | ft-screens, the compositor (wlroots and OpenVR). |
| `session/` | The desktop session script and its config example. |
| `layout/` | ft-layout: where the screens float, and their sizes. |
| `input/` | The input relay (Bluetooth mice and keyboards, button maps). |
| `pointer/` | The 3D mouse: SteamVR driver, helper service, and a probe tool. |
| `display-settings/`, `input-settings/` | The two settings apps (Kirigami, Python). |
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
scripts/sync.sh                    # copy the repo to ~/dev/frametop on the Frame
scripts/frame.sh '<cmd>'           # run in the dev container, in the Frame's copy
scripts/frame.sh -C <dir> '<cmd>'  # same, in a folder of the repo
scripts/frame.sh --host '<cmd>'    # run on the SteamOS host
```

The sync only goes one way. It makes the Frame's copy match your checkout, deleting files there that you've removed, and skips `.git`, `build/`, `.env`, and anything gitignored. Edit on the PC only, since the next sync overwrites changes made in `~/dev/frametop` on the Frame.

Programs built in the `dev` container link against its glibc, which is newer than the host's, so they run inside the container. The SteamVR driver is the exception and is built to run on the host (see `pointer/driver/build.sh`). [AGENTS.md](AGENTS.md) has the working rules, including what not to restart while someone is using the headset.

## License

MIT. See [LICENSE](LICENSE).
