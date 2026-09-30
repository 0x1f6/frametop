#!/usr/bin/env bash
# Install (or remove) the frame grabber our own eye tracker needs: ft-eyegrab, as the system
# service frametop-eyegrab.service. It copies the eye-camera frames, read-only, out of
# SteamVR's eyetracking process into /dev/shm/frametop-eyes-cams for ft-eyes, and only while
# ft-eyes wants them. The gaze service (gaze/ft-gazed) runs ft-eyes itself, when Eye tracker
# is Own tracker or the gaze probe uses it.
# Needs host sudo, for the binary (/etc/frametop/ft-eyegrab, root's) and the unit. On the
# Frame, sudo asks for the password in the terminal, or runs SUDO_ASKPASS when that's set.
# From a PC (or with no terminal), the password comes from steamos_root_pwd in the repo's .env
# and is sent to sudo -S on stdin, never on a command line.
# Usage: gaze/tracker/install.sh [install|uninstall|status|log [lines]]
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$root/scripts/_env.sh"
src=$FRAME_REPO/gaze/tracker
unit=frametop-eyegrab.service

sudo_run() {
  if [ "$FRAME_LOCAL" = 1 ] && [ -n "${SUDO_ASKPASS:-}" ]; then
    sudo -A bash -c "$1"  # SUDO_ASKPASS supplies the password
    return
  fi
  if [ "$FRAME_LOCAL" = 1 ] && [ -t 0 ]; then
    sudo bash -c "$1"  # asks for the password here
    return
  fi
  local pw
  pw=$(sed -n 's/^steamos_root_pwd=//p' "$root/.env" 2>/dev/null)
  pw=${pw#[\"\']}; pw=${pw%[\"\']}  # .env values may be quoted
  [ -n "$pw" ] || { echo "no terminal for sudo, and steamos_root_pwd is missing from $root/.env" >&2; exit 1; }
  printf '%s\n' "$pw" | on_frame "sudo -S -p '' bash -c $(printf %q "$1")"
}

case ${1:-install} in
  install)
    "$root/gaze/tracker/build.sh"
    ids=$(on_frame 'echo "$(id -u):$(id -g)"')
    fill_template "$root/gaze/tracker/$unit" | sed "s|@UID@|${ids%:*}|g; s|@GID@|${ids#*:}|g" |
      on_frame "cat > /tmp/$unit"
    sudo_run "set -e
install -D -m 0755 -o root -g root $src/build/ft-eyegrab /etc/frametop/ft-eyegrab
install -D -m 0644 -o root -g root /tmp/$unit /etc/systemd/system/$unit
rm -f /tmp/$unit
systemctl daemon-reload
systemctl enable $unit
systemctl restart $unit
sleep 1
echo \"$unit: \$(systemctl is-active $unit)\""
    ;;
  uninstall)
    sudo_run "systemctl disable --now $unit 2>/dev/null
rm -f /etc/systemd/system/$unit /etc/frametop/ft-eyegrab
rmdir /etc/frametop 2>/dev/null; systemctl daemon-reload; echo removed" ;;
  status) on_frame "systemctl is-active $unit; ls -l /dev/shm/frametop-eyes-cams 2>/dev/null" || true ;;
  log) on_frame "journalctl -u $unit --no-pager -o cat -n ${2:-20}" ;;
  *) echo "usage: $0 [install|uninstall|status|log [lines]]" >&2; exit 2 ;;
esac
