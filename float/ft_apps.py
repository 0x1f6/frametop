#!/usr/bin/env python3
"""Launch as Standalone: an action on every app in the Frametop desktop's menus.

Plasma's Application Launcher has no way to add an entry to every app's right-click menu,
but its menu (and the taskbar's) shows each app's own desktop actions. So the Frametop
desktop reads copies of the apps' desktop files with one more action, "Launch as
Standalone", which runs `ft-float launch <desktop file name>`: the app starts and its first
window floats in VR (docs/floating-windows.md, decision 26).

The copies go in OUT (~/.local/share/frametop/apps/applications), and the session puts
OUT_ROOT first in XDG_DATA_DIRS, so a copy wins over the app's own file. Plasma's app cache
is keyed by those directories, so Desktop Mode never sees the copies. Files in
XDG_DATA_HOME/applications come before every data dir, so an app customized there keeps
its own file and has no Launch as Standalone. A copy drops DBusActivatable: a D-Bus
activated app would be asked to run the action itself, and it doesn't know ours.

  ft-float-apps        write the copies (the session script runs it before Plasma starts;
                       ft-floatd again whenever an app's desktop file changes)
"""
import os
import sys

from gi.repository import GLib

ACTION = "frametop-standalone"
OUT_ROOT = os.path.expanduser("~/.local/share/frametop/apps")
OUT = os.path.join(OUT_ROOT, "applications")
FT_FLOAT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ft-float")
GROUP = "Desktop Entry"


def data_dirs():
    """(XDG_DATA_HOME, the XDG_DATA_DIRS other than ours)."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    ours = os.path.realpath(OUT_ROOT)
    dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
            if d and os.path.realpath(d) != ours]
    return home, dirs


def app_dirs():
    """Every applications folder that holds the apps' own desktop files, home first."""
    home, dirs = data_dirs()
    return [os.path.join(d, "applications") for d in [home] + dirs]


def scan():
    """Desktop file name -> (its path, whether it's in XDG_DATA_HOME), the first one found
    winning, as the desktop spec has it."""
    found = {}
    home_apps = app_dirs()[0]
    for root in app_dirs():
        for dirpath, _dirs, files in os.walk(root, followlinks=True):
            for name in files:
                if name.endswith(".desktop"):
                    path = os.path.join(dirpath, name)
                    found.setdefault(os.path.relpath(path, root).replace("/", "-"), (path, root == home_apps))
    return found


def standalone(path, desktop_id):
    """The desktop file with Launch as Standalone added, or None for one that isn't a
    visible app."""
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(path, GLib.KeyFileFlags.KEEP_TRANSLATIONS | GLib.KeyFileFlags.KEEP_COMMENTS)
    except GLib.Error:
        return None

    def get(key):
        try:
            return kf.get_string(GROUP, key)
        except GLib.Error:
            return None
    if get("Type") != "Application" or not get("Exec"):
        return None
    if (get("NoDisplay") or "").lower() == "true" or (get("Hidden") or "").lower() == "true":
        return None
    actions = [a for a in (get("Actions") or "").split(";") if a and a != ACTION]
    kf.set_string(GROUP, "Actions", ";".join(actions + [ACTION]) + ";")
    try:
        kf.remove_key(GROUP, "DBusActivatable")
    except GLib.Error:
        pass
    group = "Desktop Action " + ACTION
    kf.set_string(group, "Name", "Launch as Standalone")
    kf.set_string(group, "Icon", "window-new")
    exe = FT_FLOAT if " " not in FT_FLOAT else '"' + FT_FLOAT + '"'
    kf.set_string(group, "Exec", f"{exe} launch {desktop_id}")
    return kf.to_data()[0]


def write_all():
    """Write the copies, and remove the ones whose app is gone. Returns how many there are."""
    os.makedirs(OUT, exist_ok=True)
    keep = set()
    for desktop_id, (path, in_home) in sorted(scan().items()):
        if in_home:
            continue  # XDG_DATA_HOME's own file wins over a copy anyway
        data = standalone(path, desktop_id)
        if data is None:
            continue
        keep.add(desktop_id)
        out = os.path.join(OUT, desktop_id)
        try:
            with open(out) as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        with open(out + ".tmp", "w") as f:
            f.write(data)
        os.replace(out + ".tmp", out)
    for name in os.listdir(OUT):
        if name.endswith(".desktop") and name not in keep:
            os.remove(os.path.join(OUT, name))
    return len(keep)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(__doc__)
    print(f"{write_all()} apps with Launch as Standalone in {OUT}")
