#!/usr/bin/env bash
# Check that the Steam Frame is ready for these projects: reachable (from a PC), the
# distrobox tool and the dev container present, and disk space. Works on the Frame too.
set -uo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

fail=0
check() {
  local label=$1; shift
  if out=$("$@" 2>&1); then
    printf 'ok    %s%s\n' "$label" "${out:+: $out}"
  else
    printf 'FAIL  %s%s\n' "$label" "${out:+: $out}"
    fail=1
  fi
}

if [ "$FRAME_LOCAL" = 1 ]; then
  echo "running on the Frame: $FRAME_REPO"
else
  check "ssh key loaded" bash -c 'ssh-add -l | grep -c . | sed "s/$/ key(s)/"'
  check "ssh to $FRAME_HOST" ssh -o BatchMode=yes -o ConnectTimeout=8 "$FRAME_HOST" true
  if [ "$fail" = 1 ]; then
    echo "Skipping device checks. Is the headset on and awake, and on the network?"
    exit 1
  fi
fi
check "SteamOS" on_frame '. /etc/os-release; echo "$PRETTY_NAME $VERSION_ID build $BUILD_ID, $(uname -m)"'
check "distrobox" on_frame 'test -x ~/.local/bin/distrobox && ~/.local/bin/distrobox version | head -1'
check "container $FRAME_BOX" on_frame "podman ps -a --filter name=^$FRAME_BOX\$ --format '{{.Image}} {{.Status}}' | grep ."
check "repo on the Frame" on_frame 'pwd'
check "free space in ~" on_frame "df -h ~ | awk 'NR==2{print \$4\" free\"}'"
exit "$fail"
