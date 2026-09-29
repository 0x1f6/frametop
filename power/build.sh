#!/usr/bin/env bash
# Build ft-powerd on the Frame, in the dev container (it also runs there).
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$root/scripts/sync.sh" >/dev/null
exec "$root/scripts/frame.sh" -C power 'set -e; mkdir -p build
g++ -std=c++17 -O2 -Wall -Wno-unused-parameter -I/opt/steamvr/tools/hellovr_vulkan_linux/src/openvr/headers \
  -o build/ft-powerd ft-powerd.cpp -L/opt/steamvr/bin/linuxarm64 -lopenvr_api -Wl,-rpath,/opt/steamvr/bin/linuxarm64
echo "built build/ft-powerd"'
