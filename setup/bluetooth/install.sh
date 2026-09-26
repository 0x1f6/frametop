#!/usr/bin/env bash
# Install (or remove) the persistent Bluetooth LE workarounds on the Frame.
# Needs host sudo. On the Frame, sudo asks for the password in the terminal.
# From a PC (or with no terminal), the password comes from steamos_root_pwd in the
# repo's .env and is sent to sudo -S on stdin, never on a command line.
# Usage: setup/bluetooth/install.sh [install|uninstall|run]
#   run   re-apply now without restarting bluetooth (after pairing a new device)
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$root/scripts/_env.sh"
src=$FRAME_REPO/setup/bluetooth

sudo_run() {
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
    "$root/scripts/sync.sh" >/dev/null
    sudo_run "set -e
install -D -m 0755 -o root -g root $src/bt-fixups.sh /etc/steamframe/bt-fixups.sh
install -D -m 0644 -o root -g root $src/steamframe-bt-fixups.service /etc/systemd/system/steamframe-bt-fixups.service
rm -f /etc/systemd/system/bluetooth.service.d/steamframe.conf
rmdir /etc/systemd/system/bluetooth.service.d 2>/dev/null || true
systemctl daemon-reload
systemctl enable steamframe-bt-fixups.service
echo installed" ;;
  uninstall)
    sudo_run "systemctl disable steamframe-bt-fixups.service 2>/dev/null
rm -f /etc/systemd/system/steamframe-bt-fixups.service /etc/systemd/system/bluetooth.service.d/steamframe.conf /etc/steamframe/bt-fixups.sh
rmdir /etc/systemd/system/bluetooth.service.d /etc/steamframe 2>/dev/null; systemctl daemon-reload; echo removed" ;;
  run) sudo_run "/etc/steamframe/bt-fixups.sh" ;;
  *) echo "usage: $0 [install|uninstall|run]" >&2; exit 2 ;;
esac
