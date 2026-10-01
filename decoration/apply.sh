#!/usr/bin/env bash
# Switch the running Frametop desktop's window decoration without restarting it: Frametop's
# own (this folder, with the float button) or back to Breeze. The session script does the
# same at every desktop start (session/frametop-session.sh), so this is for trying changes.
#   decoration/apply.sh        install this folder's copy and use it
#   decoration/apply.sh --off  back to Breeze (until the next desktop start)
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
deco=kwin4_decoration_qml_frametop
cfg=$HOME/.config/frametop
kwinrc=$cfg/kwinrc

# The desktop's KWin: the one using Frametop's config folder. Its D-Bus is the session's.
bus=
for pid in $(pgrep -x kwin_wayland); do
  env=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null) || continue
  grep -qx "XDG_CONFIG_HOME=$cfg" <<<"$env" || continue
  bus=$(sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' <<<"$env")
done
[ -n "$bus" ] || { echo "the Frametop desktop isn't running" >&2; exit 1; }

if [ "${1:-}" = --off ]; then
  kwriteconfig6 --file "$kwinrc" --group org.kde.kdecoration2 --key library --delete
  kwriteconfig6 --file "$kwinrc" --group org.kde.kdecoration2 --key theme --delete
else
  dir=${XDG_DATA_HOME:-$HOME/.local/share}/kwin/decorations/$deco
  rm -rf "$dir"
  mkdir -p "$(dirname "$dir")"
  cp -r "$here" "$dir"
  rm -f "$dir/apply.sh"
  kwriteconfig6 --file "$kwinrc" --group org.kde.kdecoration2 --key library org.kde.kwin.aurorae
  kwriteconfig6 --file "$kwinrc" --group org.kde.kdecoration2 --key theme "$deco"
fi
DBUS_SESSION_BUS_ADDRESS=$bus gdbus call --session -d org.kde.KWin -o /KWin -m org.kde.KWin.reconfigure >/dev/null
echo "decoration: $(kreadconfig6 --file "$kwinrc" --group org.kde.kdecoration2 --key theme --default Breeze)"
