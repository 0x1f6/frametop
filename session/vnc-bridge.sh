#!/bin/bash
# Runs on the Frame host. Serves the Frametop desktop over VNC for clients
# like RealVNC Viewer or macOS Screen Sharing. No VNC server here can capture
# KWin directly, so this bridges through krdp: Xvnc (a virtual X screen served
# over VNC) runs a full-screen FreeRDP client connected to krdpserver on
# 127.0.0.1. Both run in the dev container. VNC listens on the tailnet address only.
# Started by frametop-session.sh when REMOTE=1, after remote-desktop.sh.
set -eu

width=${1:-1920}
height=${2:-1080}
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

export XDG_RUNTIME_DIR=/run/user/$(id -u)
exec ~/.local/bin/distrobox enter dev -- bash -c '
set -eu
creds=$1 addr=$2 vnc_port=$3 rdp_port=$4 display=$5 width=$6 height=$7
vncpasswd -f < "$creds/vnc-password" > "$creds/vnc-passwd.bin"
chmod 600 "$creds/vnc-passwd.bin"
Xvnc "$display" -geometry "${width}x${height}" -depth 24 \
  -interface "$addr" -rfbport "$vnc_port" \
  -SecurityTypes VncAuth -PasswordFile "$creds/vnc-passwd.bin" \
  -AlwaysShared -desktop "Steam Frame (Frametop)" &
xvnc=$!
trap "kill $xvnc 2>/dev/null" EXIT
sleep 2
# Keep an RDP connection open inside the VNC screen. Reconnect if it drops.
# /cert:ignore is fine here: the connection never leaves this host.
while kill -0 $xvnc 2>/dev/null; do
  DISPLAY=$display xfreerdp /v:"127.0.0.1:$rdp_port" /u:steamos /p:"$(cat "$creds/password")" \
    /cert:ignore /size:"${width}x${height}" -decorations /f +clipboard >/dev/null 2>&1 || true
  sleep 2
done
' vnc-bridge "$creds" "$addr" "$vnc_port" "$rdp_port" "$display" "$width" "$height"
