# Profiles (plan)

Status: design settled with the user on 2026-09-30; not built yet. Profiles come last in the build order in `docs/floating-windows.md`, after launching apps floating (phase 3 there) and hiding screens one at a time.

A profile is a named layout that also opens apps. It holds:

- where each screen goes, with its size in metres, curve, roll, and pin (what a named layout holds today);
- which screens show and which are hidden;
- the apps, one entry per window: on a screen at a place and size, or floating at a pose, size, and scale.

So a "Work" profile can put three screens around you with a browser, two terminals, and an editor on them, and a "Couch" profile can hide every screen and float one video player in front of you.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Profiles and named layouts | One list. Named layouts grow into profiles; a layout saved before profiles existed is a profile with no apps and every screen shown |
| 2 | Making one | Capture what's open: the screens and every app's windows. Display Settings lists a profile's apps, so one can be removed. No editor beyond that |
| 3 | Apps with several windows | One entry per window. The app is launched once; when its first window shows up, it's launched again for each window still missing. A browser that restores its own windows gets launched once, a terminal twice |
| 4 | What's recorded of an app | Its desktop file name, or its command line if it has none. Not what it had open: tabs, files, and folders are left to the app's own restore |
| 5 | Switching while apps are open | Additive: launch what's missing, move the windows that match into place, leave the rest alone. Nothing is ever closed |
| 6 | Saving changes | Only on an explicit save. Moving things after switching doesn't change the profile |
| 7 | Starting one | Four ways: a default profile when the desktop starts, Display Settings, a launcher entry for each profile, and a mappable action |
| 8 | Plasma's session restore | Off in the Frametop session, so a profile is the only thing that reopens apps |
| 9 | Screen count and resolution | Not part of a profile. They're global, because changing them restarts the desktop, which closes every window |

## Where profiles live

`~/.config/frametop-layout.json` keeps its `layouts` as they are (`{"Work": [screen places]}`), so older copies of ft-layout (the `main` checkout) still read it. What a profile adds goes in a parallel `profiles` map under the same names:

```
"layouts":  {"Work": [{"pos": ..., "face": ..., "roll": ..., "metres": ..., "curve": ..., "pin": ...}, ...]},
"profiles": {"Work": {"hidden": [3],
                      "windows": [
                        {"app": "org.kde.konsole", "screen": 2, "rect": [40, 60, 1200, 800], "maximized": false},
                        {"app": "com.brave.Browser", "screen": 1, "maximized": true},
                        {"cmd": ["/opt/tool/run"], "class": "tool", "screen": 1, "rect": [...]},
                        {"app": "org.kde.dolphin", "float": {"pos": [...], "face": [...], "roll": 0,
                                                             "pixels": [1400, 900], "scale": 1.2, "pin": ...}}]}},
"default_profile": "Work"
```

- `screen` is 1-based, as everywhere in Frametop. `rect` is the window's frame in KWin's logical units, relative to its screen's output, so it survives the screens being arranged differently.
- A floating window's place is relative to your head when the profile is applied, the same way the screens' places are (position, and the direction you face, yaw only).
- Renaming or deleting a layout renames or deletes its profile entry with it.

## How it works

- **Capture** (`ft-layout save NAME`, and Save current arrangement… in Display Settings). ft-layout captures the screens as today, then asks ft-floatd for the windows (`windows` on @frametop_float). ft-floatd answers from the KWin script's table: every normal window with its desktop file name, output, frame, and maximized state, and for floating windows their panel's pose from ft-screens, their size in pixels, and their scale. Windows with no desktop file name are kept by their process's command line (`/proc/<pid>/cmdline`) and window class. Windows of Plasma itself, the Frametop settings apps, and dialogs aren't recorded.
- **Apply** (`ft-layout use NAME`). ft-layout arranges the screens as today, hides and shows them (the per-screen hide in ft-screens), then hands the profile's windows to ft-floatd (`profile NAME`). ft-floatd goes through the entries app by app. It claims windows of that app already open (oldest first, each claimed once), and moves each to its entry's place: onto its screen at its rect (or maximized), or floating at its pose. For the entries left over, it launches the app (`ft-float launch`, the same path as Launch as Standalone), places windows as they show up, and launches again for each one still missing once the first has appeared. It gives up on an app after 30 seconds and notifies.
- **Default at start.** `default_profile` replaces today's "arrange when the desktop starts": the session's autostart runs `ft-layout start`, which applies the default profile (screens and apps), or just arranges the screens if there's none. Plasma's session restore is turned off in the session (`ksmserverrc`: `loginMode=emptySession`).
- **Launcher entries.** Each profile gets `~/.local/share/applications/frametop-profile-<name>.desktop` ("Frametop: Work"), written when it's saved and removed when it's deleted. They show in SteamVR's Launch a program list, the Application Launcher, and KRunner. Running one (`ft-layout open NAME`) switches to that profile if the desktop runs, or starts the desktop with `FT_PROFILE` set, which overrides `default_profile` for that start.
- **The action.** `profile:NAME` in the input relay, for key combinations, mouse buttons, and controller buttons. Input Settings lists one entry per profile.
- **Display Settings.** The Layout page becomes Profiles: the list (switch, rename, delete, save the current state over one), each profile's apps with a remove button, which screens it hides, and which profile the desktop starts with.

## Open for when it's built

- How long to wait for an app's first window before launching it again (slow apps would open twice). Start at waiting for the first window, up to 30 seconds, then 3 seconds more for windows it restores itself.
- Apps that are D-Bus activated or single-instance open their window from a process that was already running; matching them is phase 3's job (`docs/floating-windows.md`, Launching an app floating).
