#!/usr/bin/env bash
# Install (or remove) Frametop Display Settings and the Reset Screen Layout and Hide/Show Screens
# entries on the Frame, with global shortcuts in the Frametop desktop: Meta+Shift+R and
# Meta+Shift+H (they take effect the next time the desktop starts).
# Usage: display-settings/install.sh [install|uninstall]
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. "$root/scripts/_env.sh"
"$root/scripts/sync.sh" >/dev/null
apps=.local/share/applications
shortcuts=.config/frametop/kglobalshortcutsrc
case ${1:-install} in
  install)
    for f in ft-display-settings ft-layout-reset ft-screens-toggle; do
      fill_template "$root/display-settings/$f.desktop" | on_frame "mkdir -p ~/$apps && cat > ~/$apps/$f.desktop"
    done
    on_frame "chmod +x display-settings/ft-display-settings layout/ft-layout layout/ft_layout.py
mkdir -p ~/.config/frametop
kwriteconfig6 --file ~/$shortcuts --group services --group ft-layout-reset.desktop --key _launch 'Meta+Shift+R'
kwriteconfig6 --file ~/$shortcuts --group services --group ft-screens-toggle.desktop --key _launch 'Meta+Shift+H'"
    echo "installed: Frametop Display Settings, Reset Screen Layout (Meta+Shift+R), Hide/Show Screens (Meta+Shift+H)" ;;
  uninstall)
    on_frame "rm -f ~/$apps/ft-display-settings.desktop ~/$apps/ft-layout-reset.desktop ~/$apps/ft-screens-toggle.desktop
[ -f ~/$shortcuts ] && for f in ft-layout-reset ft-screens-toggle; do kwriteconfig6 --file ~/$shortcuts --group services --group \$f.desktop --key _launch --delete; done
echo removed" ;;
  *) echo "usage: $0 [install|uninstall]" >&2; exit 2 ;;
esac
