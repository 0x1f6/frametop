#!/usr/bin/env bash
# Build ft-gaze in the dev container on the Frame (gaze/build/ft-gaze; it also runs there).
# The eye tracking API (IVRInput::GetEyeTrackingDataRelativeToNow) is newer than the header
# shipped with SteamVR's samples, so this uses the pinned public header ft-screens fetches.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$root/scripts/sync.sh" >/dev/null
exec "$root/scripts/frame.sh" -C gaze 'set -e; mkdir -p build/include
openvr=v2.15.6
[ -f build/include/openvr-$openvr ] || { curl -fsSL "https://raw.githubusercontent.com/ValveSoftware/openvr/$openvr/headers/openvr.h" -o build/include/openvr.h && touch build/include/openvr-$openvr; }
g++ -std=c++17 -O2 -Wall -Wno-unused-parameter -Wno-missing-field-initializers -Ibuild/include -I../pointer/common \
  -o build/ft-gaze ft-gaze.cpp -L/opt/steamvr/bin/linuxarm64 -lopenvr_api -Wl,-rpath,/opt/steamvr/bin/linuxarm64 -lpthread
echo "built build/ft-gaze"'
