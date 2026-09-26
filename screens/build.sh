#!/usr/bin/env bash
# Build ft-screens in the dev container on the Frame (screens/build/ft-screens).
# compositor.c is the wlroots side (C; wlroots headers aren't C++), vr.cpp the OpenVR side.
# vr.cpp needs OpenVR's IVRIPCResourceManagerClient (ImportDmabuf), which the header
# shipped with SteamVR on the Frame predates, so the build uses the public header from
# Valve's openvr repo (pinned; the Frame's runtime supports its interface versions).
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$root/scripts/sync.sh" >/dev/null
exec "$root/scripts/frame.sh" -C screens 'set -e; mkdir -p build/include
openvr=v2.15.6
[ -f build/include/openvr-$openvr ] || { curl -fsSL "https://raw.githubusercontent.com/ValveSoftware/openvr/$openvr/headers/openvr.h" -o build/include/openvr.h && touch build/include/openvr-$openvr; }
gcc -std=c11 -O2 -Wall -Wno-unused-parameter -c -o build/compositor.o compositor.c \
  $(pkg-config --cflags wlroots-0.20 wayland-server xkbcommon libdrm pixman-1)
g++ -std=c++17 -O2 -Wall -Wno-missing-field-initializers -Ibuild/include -c -o build/vr.o vr.cpp
g++ -o build/ft-screens build/compositor.o build/vr.o \
  $(pkg-config --libs wlroots-0.20 wayland-server xkbcommon) \
  -L/opt/steamvr/bin/linuxarm64 -lopenvr_api -Wl,-rpath,/opt/steamvr/bin/linuxarm64
echo "built build/ft-screens"'
