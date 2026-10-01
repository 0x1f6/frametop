# Profiles (plan)

Status: design settled with the user on 2026-09-30, and built the same day on the branch `profiles`, which builds on `screen-hide` (screens hidden one at a time). Tried on the live desktop the same day (headset off). A profile with a maximized Dolphin on screen 1, a floating Konsole, and screen 3 hidden saved correctly. Opened from nothing, it launched both into place. Opened over moved windows, it moved them back without launching anything. `ft-layout start` with `FT_PROFILE` and `ft-layout open` worked as well. Not yet tried: starting the desktop in a profile from its launcher entry or `default_profile` (needs a desktop restart), and the relay's `profile:NAME` action (the running relay is another branch's).

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
                        {"app": "org.kde.dolphin", "float": {"rel": [12 numbers], "pixels": [1400, 900],
                                                             "scale": 1.2, "mpp": 0.00097}}]}},
"default_profile": "Work"
```

- `screen` is 1-based, as everywhere in Frametop. `rect` is the window's frame in KWin's logical units, relative to its screen's output, so it survives the screens being arranged differently.
- A floating window's place (`rel`) is its panel's centre and axes in the frame of the primary screen's panel, the same way ft-floatd remembers each app's place. The screens go relative to your head when the profile is applied, and the floating windows follow them. `mpp` is its density in metres per pixel. Its scale is kept but not yet applied (see Known problems in `docs/floating-windows.md`).
- Renaming or deleting a layout renames or deletes its profile entry with it.

## How it works

- **Capture** (`ft-layout save NAME`, and Save as profile… in Display Settings). ft-layout captures the screens as before, then asks ft-floatd for the windows (`windows` on @frametop_float). ft-floatd has the KWin script report every window as it is now (`report-all`), then answers with every normal window: its desktop file name, the screen it's on, its rectangle there, and whether it's maximized. For floating windows it gives their panel's place, their size in pixels, and their scale. Windows with no desktop file name are kept by their process's command line (`/proc/<pid>/cmdline`) and window class. Windows of Plasma itself, the Frametop settings apps, and dialogs aren't recorded. If ft-floatd doesn't answer, the profile keeps the apps it had.
- **Apply** (`ft-layout use NAME`, Open profile in Display Settings). ft-layout makes the profile's hidden screens the screens' own setting, arranges the screens (which hides and shows them: ft-screens' `conceal` and `reveal`), then has ft-floatd open the apps (`profile NAME`; ft-floatd reads the windows from the layout file). ft-floatd goes through the entries app by app. It claims windows of that app already open (oldest first, each claimed once), and moves each to its entry's place: onto its screen at its rect (or maximized), or floating at its pose. For the entries left over, it launches the app (`ft-float launch`, the same path as Launch as Standalone), places windows as they show up, and launches again for each one still missing once the first has appeared. It stops waiting for an app's windows after 30 seconds.
- **Default at start.** The session script runs `ft-layout start --wait 90` (it used to run `apply --wait 90`). That opens the profile in `FT_PROFILE` or `default_profile` (screens, then the apps once ft-floatd is up), or does what `apply --wait` did if there's none. Start in profile on the Layout & profiles page sets `default_profile` (`ft-layout default NAME|none`). Plasma's session restore is turned off in the session (`ksmserverrc`: `loginMode=emptySession`).
- **Launcher entries.** Each profile gets `~/.local/share/applications/frametop-profile-<name>.desktop` ("Frametop: Work"), written when it's saved and removed when it's deleted. They show in SteamVR's Launch a program list, the Application Launcher, and KRunner. Running one (`ft-layout open NAME`) switches to that profile if the desktop runs. Otherwise it starts the desktop with `FT_PROFILE` set (`systemd-run`, as `desktops.sh start` does), which overrides `default_profile` for that start. That needs SteamVR to be running.
- **The action.** `profile:NAME` in the input relay (it runs `ft-layout use NAME`) for key combinations, mouse buttons, and controller buttons, with or without pointer mode. Input Settings lists one "Open profile NAME" action per profile.
- **Display Settings.** The Layout page becomes Layout & profiles. The arrangement list has the profiles (rename and delete as before). Open profile and Save as profile… are the page's actions. A profile's apps are listed with where each goes and a button to leave one out, plus which screens it hides. Start in profile picks the one the desktop starts with. The Visibility tab's Screens shown switches hide screens one at a time.

## Open for when it's built

- How long to wait for an app's first window before launching it again (slow apps would open twice). Start at waiting for the first window, up to 30 seconds, then 3 seconds more for windows it restores itself.
- Apps that are D-Bus activated or single-instance open their window from a process that was already running; matching them is phase 3's job (`docs/floating-windows.md`, Launching an app floating).
