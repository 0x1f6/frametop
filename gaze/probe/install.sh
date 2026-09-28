#!/usr/bin/env bash
# Install (or remove) Frametop Gaze Probe in the desktop's app menu, and build ft-gaze.
# Usage: gaze/probe/install.sh [install|uninstall]
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$root/scripts/_env.sh"
"$root/scripts/sync.sh" >/dev/null
apps=.local/share/applications
case ${1:-install} in
  install)
    "$root/gaze/build.sh"
    fill_template "$root/gaze/probe/ft-gazeprobe.desktop" | on_frame "mkdir -p ~/$apps && cat > ~/$apps/ft-gazeprobe.desktop"
    on_frame "chmod +x gaze/probe/ft-gazeprobe"
    echo "installed: Frametop Gaze Probe" ;;
  uninstall)
    on_frame "rm -f ~/$apps/ft-gazeprobe.desktop; echo removed" ;;
  *) echo "usage: $0 [install|uninstall]" >&2; exit 2 ;;
esac
