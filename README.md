# Frametop

A multi-monitor desktop and a universal 3D mouse for the Valve Steam Frame, installed and run on the headset itself.

- **Several desktop screens floating in SteamVR.** A full KDE Plasma desktop where every screen is a real monitor of its own: any resolution and shape (ultrawide, portrait, 4K) and any size in the room. They appear in your saved layout when the desktop starts. You move, resize, curve, and roll them by hand, or pin one to your wrist, and one shortcut puts them all back.
- **A universal 3D mouse.** A Bluetooth mouse runs all of SteamVR (the dashboard, Steam, overlays, the desktop) as a small dot anchored in the room. It snaps onto panels, drags and tilts them, and hands the laser back to your controllers when you pick one up.
- **Frametop Display Settings**, an app for the screens: how many, each one's resolution, size, scale, and curve, which has the taskbar, the layout they float in, and when they show.
- **Frametop Input Settings**, an app to choose devices, map mouse buttons (for example to open the SteamVR dashboard), and tune the pointer.
- **Bluetooth fixes** so LE mice and keyboards, like the Swiftpoint Z3, reconnect after they sleep or the headset reboots.

Frametop is an independent project. It isn't made by or affiliated with Valve.

## Install on the headset

You need a Steam Frame with an internet connection, a keyboard (Bluetooth, or the on-screen one), and about 3 GB of free space.

1. **Open a desktop.** In the launcher, choose **Launch a program → Desktop**.
2. **Open a terminal.** In the application menu, open **System → Konsole**.
3. **Clone and install:**

   ```
   git clone https://github.com/DeeJanuz/frametop.git ~/frametop
   cd ~/frametop
   ./install.sh
   ```

   The installer sets up distrobox (in your home folder; the system files stay untouched), a Fedora build container, and everything below. The first run downloads about 1–2 GB. It asks before the two steps that affect you:

   - **Bluetooth fixes.** These need your password for `sudo`. If you've never set one, run `passwd` first. You can also skip them and install later.
   - **Restarting SteamVR.** This is needed once, and it closes everything open in VR, including the terminal. Rebooting the headset works too.

After the restart:

- **Launch a program → Desktop** opens the multi-screen desktop. Its screens arrange themselves around where you're facing.
- **Frametop Display Settings** and **Frametop Input Settings** are in the desktop's application menu, under Settings.

### Add a Bluetooth mouse or keyboard

1. Pair it in Steam: **Settings → Bluetooth**.
2. If you installed the Bluetooth fixes, apply them once for the new device: **Frametop Input Settings → Bluetooth → Apply Bluetooth fixes**. Or, in the repo: `setup/bluetooth/install.sh run`. After that it reconnects on its own.
3. Move the mouse. The dot appears where you're looking.

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
| Meta+Shift+R in the desktop | Puts the screens back in their layout (also the **Reset Screen Layout** menu entry, and a button you can map) |
| Meta+Shift+H in the desktop | Hides or shows all screens (also **Hide/Show Screens** and a mappable button). **Frametop Display Settings → Visibility & wrist** can instead show them only with the dashboard open, or while you look at your wrist |
| Play a VR game | The screens hide and your controllers stay in the game. Open the SteamVR dashboard (or press Meta+Shift+H) to see and use them. **Visibility & wrist → During VR games** can keep them visible over the game instead; the controllers still stay in the game, and the 3D mouse or the dashboard works the screens. |

Map the mouse's extra buttons to actions such as **Toggle SteamVR dashboard** or **Recenter pointer** in **Frametop Input Settings → Buttons**. Speed, dot size, and the rest are on its **Pointer** page and apply immediately.

Restarting the desktop (**Frametop Display Settings → Restart desktop**) closes its windows, but background work started in it, like servers, tmux, and builds, keeps running.

## Known limitations

This is an early release, tested on one Steam Frame (SteamOS 0.3.0 build 20260922, SteamVR 2.17.10).

- A SteamOS or SteamVR update can break parts of it until Frametop catches up. If something stops working after an update, please report it (below).
- The first install downloads 1–2 GB (a Fedora build container) and builds everything on the headset. It takes several minutes.
- During a VR game, a controller button can't show the screens (the game owns the buttons). Open the SteamVR dashboard, press Meta+Shift+H, or use a mapped mouse button.
- Flatscreen games don't count as VR games. If the controllers work the screens instead of such a game, set **Frametop Display Settings → Visibility & wrist → Controllers on the screens → Only with the SteamVR dashboard open**.
- The screens show no mouse cursor of their own: the 3D mouse's dot, or SteamVR's laser, is the cursor.
- Remote desktop over VNC (`./desktops.sh remote on`) needs Tailscale on the Frame.

## Reporting problems

In a terminal on the headset, run:

```
cd ~/frametop && scripts/report.sh
```

It writes `frametop-report-<date>.txt` with the versions, service states, settings, and recent logs (Bluetooth addresses and the headset's serial number are masked). [Open an issue](https://github.com/DeeJanuz/frametop/issues) with what you did, what you expected, and what happened, and attach the file.

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

A nested Plasma session runs inside ft-screens (`screens/`), a small Wayland compositor. KWin opens a window per screen, ft-screens gives each its own size, and hands every frame to SteamVR as its own overlay without copying it. An input relay (`input/`) keeps Bluetooth mice working in SteamVR and turns the mouse into the 3D pointer, which drives a virtual SteamVR controller (`pointer/`). The details, and everything we learned about SteamVR on the Frame, are in [docs/reference.md](docs/reference.md) and [docs/design.md](docs/design.md).

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

Everything also works from a Linux (or WSL) PC over SSH, which is handier for editing code. The scripts detect where they're running: on the Frame they work on the local checkout, and on a PC they sync the repo to `~/dev/frametop` on the Frame and run there.

1. **On the Frame:** turn on developer mode, set a password (`passwd`), and enable SSH (`sudo systemctl enable --now sshd`). Add your public key to `~/.ssh/authorized_keys`. [deck-tailscale](https://github.com/tailscale-dev/deck-tailscale) gives access from anywhere.
2. **On the PC:** add the Frame to `~/.ssh/config` as host `frame` (or set `FRAME_HOST`):

   ```
   Host frame
       HostName <the Frame's address>
       User steamos
       IdentityFile ~/.ssh/<your-key>
   ```

3. For the Bluetooth fixes, which need `sudo` without a terminal on the Frame, put the password in `.env` at the repo root. It's gitignored and never synced:

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

The sync is one-way: it makes the Frame's copy match this repo and deletes files there that no longer exist here. It skips `.git`, `build/`, `.env`, and anything gitignored. **Edit on the PC only.** Changes made in `~/dev/frametop` on the Frame are overwritten by the next sync.

Programs built in the `dev` container use its glibc, which is newer than the host's, so they run in the container. The SteamVR driver is the exception: it's built to run on the host (see `pointer/driver/build.sh`). See [AGENTS.md](AGENTS.md) for the working rules, including what not to restart on a headset someone is using.

## License

MIT. See [LICENSE](LICENSE).
