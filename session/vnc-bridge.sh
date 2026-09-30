#!/bin/bash
# Runs on the Frame host. Serves the Frametop desktop's primary screen (the one with the
# taskbar) over VNC for clients like RealVNC Viewer or macOS Screen Sharing. No VNC server
# here can capture KWin directly, so this bridges through krdp: Xvnc (a virtual X screen
# served over VNC) runs a FreeRDP client connected to krdpserver on 127.0.0.1. Both run in
# the dev container. VNC listens on the tailnet address only.
# Started by frametop-session.sh when REMOTE=1, after remote-desktop.sh.
#
# krdp streams the whole workspace (every screen). Its --monitor would stream one screen,
# but krdp 6.7 then maps the pointer as if that screen sat at 0,0, so clicks miss on a
# screen placed lower or further right. Instead the VNC screen is the primary's size, and
# the workspace-sized RDP window inside it is shifted so the primary fills it. The pointer
# maps 1:1. When the layout changes, the VNC screen resizes and the RDP client reconnects.
set -eu

here=$(dirname "$(readlink -f "$0")")
vnc_port=${VNC_PORT:-5900}
rdp_port=${RDP_PORT:-3390}
display=:20
creds=$HOME/.config/frametop-remote

addr=$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
if [ -z "$addr" ]; then
  echo "tailscale0 has no address, not starting VNC" >&2
  exit 1
fi

# The VNC password is limited to 8 characters by the protocol. The traffic is
# still encrypted by the tailnet (WireGuard).
if [ ! -s "$creds/vnc-password" ]; then
  (umask 077; head -c 12 /dev/urandom | base64 | tr -d '/+=' | cut -c1-8 > "$creds/vnc-password")
fi

# Wait for krdpserver (started by remote-desktop.sh).
for _ in $(seq 60); do
  ss -ltn | grep -q "127.0.0.1:$rdp_port " && break
  sleep 1
done

# "x y width height workspace_width workspace_height" of the primary screen, once Plasma is up.
view() { "$here/../layout/ft-layout" remote-view 2>/dev/null | grep -xE '[0-9]+( [0-9]+){5}'; }
v=
for _ in $(seq 90); do
  v=$(view) && [ -n "$v" ] && break
  v=
  sleep 1
done
if [ -z "$v" ]; then
  echo "couldn't read the desktop's screens, not starting VNC" >&2
  exit 1
fi
read -r _ _ w h _ _ <<< "$v"

export XDG_RUNTIME_DIR=/run/user/$(id -u)
box() { "$HOME/.local/bin/distrobox" enter dev -- "$@"; }
stop_rdp() { pkill -f "[x]freerdp /v:127.0.0.1:$rdp_port " 2>/dev/null || true; }
trap 'stop_rdp; pkill -f "[X]vnc $display " 2>/dev/null || true' EXIT

box bash -c 'vncpasswd -f < "$1/vnc-password" > "$1/vnc-passwd.bin" && chmod 600 "$1/vnc-passwd.bin"' - "$creds"
box Xvnc "$display" -geometry "${w}x${h}" -depth 24 \
  -interface "$addr" -rfbport "$vnc_port" \
  -SecurityTypes VncAuth -PasswordFile "$creds/vnc-passwd.bin" \
  -AlwaysShared -desktop "Steam Frame (Frametop)" &
xvnc=$!
sleep 2

# Keep an RDP connection open inside the VNC screen. Reconnect if it drops or the layout changes.
# /cert:ignore is fine here: the connection never leaves this host.
while kill -0 $xvnc 2>/dev/null; do
  read -r x y w h ww wh <<< "$v"
  box env DISPLAY=$display bash -c '
    size=$1 x=$2 y=$3 ww=$4 wh=$5 creds=$6 rdp_port=$7
    if [ "$(xrandr | sed -n "s/.*current \([0-9]*\) x \([0-9]*\),.*/\1x\2/p")" != "$size" ]; then
      xrandr --newmode "$size" 0 "${size%x*}" 0 0 0 "${size#*x}" 0 0 0 2>/dev/null || true
      xrandr --addmode VNC-0 "$size" 2>/dev/null || true
      xrandr --fb "$size" --output VNC-0 --mode "$size"
    fi
    xfreerdp /v:127.0.0.1:"$rdp_port" /u:"$(id -un)" /p:"$(cat "$creds/password")" \
      /cert:ignore /size:"${ww}x${wh}" -decorations +clipboard >/dev/null 2>&1 &
    rdp=$!
    # FreeRDP takes no negative position, so move its window once it is up.
    for _ in $(seq 60); do
      win=$(xdotool search --class xfreerdp 2>/dev/null | tail -1)
      [ -n "$win" ] && break
      sleep 0.5
    done
    [ -n "$win" ] && xdotool windowmove "$win" "$((-x))" "$((-y))"
    wait $rdp
  ' vnc-rdp "${w}x$h" "$x" "$y" "$ww" "$wh" "$creds" "$rdp_port" || true &
  rdp=$!
  while kill -0 $rdp 2>/dev/null; do
    sleep 5
    now=$(view) || continue
    [ -n "$now" ] && [ "$now" != "$v" ] || continue
    echo "layout changed: $v -> $now"
    v=$now
    stop_rdp
  done
  wait $rdp 2>/dev/null || true
  sleep 2
done
