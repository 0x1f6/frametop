#!/usr/bin/env bash
# Update the lines of ~/.config/frametop.conf that still read exactly as an older
# frametop.conf.example wrote them. A line you changed stays as it is. install.sh and
# hands/rec/install.sh run this on every install and update.
#   HANDS_SWAP_SIDES=0: the example's value until 2026-10-05. It made ft-hands keep ft-camd's side
#   camera names even when they're backwards (some SteamVR restarts swap them), so hands landed
#   beside their cutouts and the hand recorder labelled the side cameras wrong. auto tells from
#   the hands.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. "$root/scripts/_env.sh"

on_frame_script <<'EOF'
f=~/.config/frametop.conf
[ -f "$f" ] || exit 0
old="HANDS_SWAP_SIDES=0         # hand tracking (hands/run.sh install): 1 = the side cameras' names are swapped, which some SteamVR restarts cause (hands/tools/check_sides.py --ring tells)"
new="HANDS_SWAP_SIDES=auto      # hand tracking: which side camera is which. auto = ft-hands tells from the hands and fixes ft-camd's names, which some SteamVR restarts swap | 0 = keep ft-camd's | 1 = exchange them"
if grep -qxF "$old" "$f"; then
  tmp=$(mktemp)
  awk -v old="$old" -v new="$new" '$0 == old { print new; next } { print }' "$f" > "$tmp"
  cat "$tmp" > "$f"   # in place: the file keeps its owner and mode
  rm -f "$tmp"
  echo "settings: HANDS_SWAP_SIDES=0 (the old default) is now auto: hand tracking tells the side cameras apart itself"
fi
EOF
