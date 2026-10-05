---
title: Install the Frametop Hand Recorder
---

# Install the Frametop Hand Recorder

The Hand Recorder records your hands with the Steam Frame's tracking cameras for Frametop's open hand dataset ([DeeJanuz/frametop-hands](https://huggingface.co/datasets/DeeJanuz/frametop-hands) on Hugging Face). These are the Konsole commands to install it.

For now the recorder runs inside Frametop's desktop, so these steps install Frametop first. A standalone recorder that runs from the SteamVR dashboard without Frametop is planned.

## Before you start

- You must be 18 or older, and for now you can't take part if you live in Illinois, Texas or Washington (USA). The [consent text](https://github.com/DeeJanuz/frametop/blob/main/hands/rec/CONSENT.md) explains what's recorded and what you agree to. The recorder shows it again before your first session.
- You need a Steam Frame on the stable SteamOS release (not the beta), an internet connection, and a keyboard (Bluetooth, or the on-screen one).
- You need a `sudo` password. If you've never set one, run `passwd` in Konsole first.
- Recordings are several gigabytes per round, and uploading one needs about the same again free while it runs. `df -h ~` shows your free space.

## Open Konsole

1. In the launcher, choose Launch a program → Desktop.
2. In the application menu, open System → Konsole.

## 1. Install Frametop

```
curl -fsSL https://deejanuz.github.io/frametop/get.sh | bash -s -- --stable
```

This clones Frametop into `~/frametop` and runs its installer. The first run downloads 1–2 GB. The installer asks a few questions (gaze mode, the eye tracker, the Bluetooth fixes); the defaults are fine. At the end SteamVR restarts, which closes Konsole. If Frametop is already installed, this updates it.

## 2. Install the Hand Recorder

After SteamVR restarts, choose Launch a program → Desktop again, open Konsole, and run:

```
~/frametop/hands/rec/install.sh
```

This builds the camera broker, the hand tracker and the headset panel (the first build takes a few minutes), asks for your `sudo` password once to let the camera broker read the cameras, and adds Frametop Hand Recorder to the application menu.

## 3. Record

Open Frametop Hand Recorder from the desktop's application menu. It walks you through the consent text, a short checklist, the recording, a review of what you recorded, and the upload to Hugging Face. Nothing leaves the headset until you press Upload.

## Update

```
curl -fsSL https://deejanuz.github.io/frametop/get.sh | bash -s -- --stable
~/frametop/hands/rec/install.sh
```

Run the second command after SteamVR has restarted, as in the install.

## Uninstall

```
~/frametop/hands/rec/install.sh uninstall
```

This removes the menu entry. Your recordings stay in `~/.local/share/frametop/hands/contrib`; delete that folder to remove them. To remove Frametop as well, follow [Uninstall](https://github.com/DeeJanuz/frametop#uninstall) in the README.

## Help

Ask in the [Frametop issues](https://github.com/DeeJanuz/frametop/issues) or on the dataset's [discussion page](https://huggingface.co/datasets/DeeJanuz/frametop-hands/discussions). Both are public.
