#!/usr/bin/env bash
# Install (or remove) the Frametop Input Settings menu entry on the Frame.
# Usage: input-settings/install.sh [install|uninstall]
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. "$root/scripts/_env.sh"
"$root/scripts/sync.sh" >/dev/null
case ${1:-install} in
  install)
    fill_template "$root/input-settings/ft-input-settings.desktop" |
      on_frame "chmod +x input-settings/ft-input-settings && mkdir -p ~/.local/share/applications && cat > ~/.local/share/applications/ft-input-settings.desktop"
    echo "installed: Frametop Input Settings (Plasma menu, Settings)" ;;
  uninstall) on_frame 'rm -f ~/.local/share/applications/ft-input-settings.desktop; echo removed' ;;
  *) echo "usage: $0 [install|uninstall]" >&2; exit 2 ;;
esac
