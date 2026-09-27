#!/usr/bin/env bash
# Install, remove, or poke the ft_pointer SteamVR driver on the Frame.
# Usage: pointer/driver/install.sh install     # copy to ~/.local/share/frametop/ft_pointer and register
#        pointer/driver/install.sh uninstall   # unregister and delete
#        pointer/driver/install.sh send '<cmd>' # e.g. 'btn trigger 1', 'aim 20 -5', 'gaze'
#        pointer/driver/install.sh aimhere     # pin the ray (room-anchored) where the head points now
#        pointer/driver/install.sh probe       # devices, roles, dashboard pointer (pointer/probe)
#        pointer/driver/install.sh log         # ft_pointer lines from vrserver.txt
# SteamVR loads drivers only at startup: restart it after install or uninstall.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$root/scripts/_env.sh"
frame="$root/scripts/frame.sh"
src=$FRAME_REPO/pointer/driver
dest=/home/steamos/.local/share/frametop/ft_pointer
reg='/opt/steamvr/bin/linuxarm64/vrpathreg'

case ${1:-install} in
  install)
    "$root/scripts/sync.sh" >/dev/null
    # Replacing the files of a driver SteamVR has loaded leaves its input bindings in a bad
    # state (the 3D mouse no longer gets the laser) until SteamVR restarts, so an unchanged
    # driver is left alone.
    "$frame" --host "set -e; test -f $src/build/driver_ft_pointer.so
rm -rf $dest.new; mkdir -p $dest.new/bin/linuxarm64
cp -r $src/ft_pointer/. $dest.new/
cp $src/build/driver_ft_pointer.so $dest.new/bin/linuxarm64/
if [ -d $dest ] && diff -r -q $dest $dest.new >/dev/null; then
  rm -rf $dest.new; echo 'driver unchanged'
else
  rm -rf $dest; mv $dest.new $dest
  echo 'installed; restart SteamVR to load it'
fi
LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 $reg adddriver $dest
LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 $reg show | grep -A3 -i 'external'" ;;
  uninstall)
    "$frame" --host "LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 $reg removedriver $dest; rm -rf $dest; echo 'removed; restart SteamVR to unload it'" ;;
  send)
    "$frame" --host "python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM); s.sendto(sys.argv[1].encode(), \"\\0ft_pointer\")' $(printf %q "${2:?command}")" ;;
  probe) "$frame" -C pointer/probe 'LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 ./build/vrprobe' ;;
  aimhere)
    read -r yaw pitch < <("$frame" -C pointer/probe 'LD_LIBRARY_PATH=/opt/steamvr/bin/linuxarm64 ./build/vrprobe' | sed -n 's/^head yaw = \([-0-9.]*\) pitch = \([-0-9.]*\)$/\1 \2/p')
    [ -n "${yaw:-}" ] || { echo "head pose not valid (headset off?)" >&2; exit 1; }
    "$0" send "aim $yaw $pitch" && echo "aimed at yaw $yaw pitch $pitch" ;;
  log) "$frame" --host "grep -iE 'ft_pointer' ~/.local/share/Steam/logs/vrserver.txt | tail -n ${2:-20}" ;;
  *) echo "usage: $0 install|uninstall|send '<cmd>'|aimhere|probe|log" >&2; exit 2 ;;
esac
