# Sourced by the other scripts. Works out where the Steam Frame is and how to reach it.
#
# On the Frame itself (SteamOS, VR variant), FRAME_LOCAL=1: commands run locally, and the
# Frame's copy of the repo (FRAME_REPO) is this checkout, wherever it was cloned.
# On a PC, FRAME_LOCAL=0: commands run over SSH on $FRAME_HOST (default "frame"), and the
# Frame's copy is the one scripts/sync.sh keeps at ~/dev/frametop.
# FRAME_LOCAL, FRAME_HOST, FRAME_REPO, and FRAME_BOX (container, default "dev") can be
# set in the environment to override.

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [ -z "${FRAME_LOCAL:-}" ]; then
  FRAME_LOCAL=0
  if grep -qx 'ID=steamos' /etc/os-release 2>/dev/null && grep -qE '^VARIANT_ID="?vr"?$' /etc/os-release; then
    FRAME_LOCAL=1
  fi
fi

if [ "$FRAME_LOCAL" = 1 ]; then
  FRAME_REPO=$REPO_ROOT
  # A terminal inside a Plasma session in VR (Frametop or the stock desktop) has that
  # session's private XDG_RUNTIME_DIR and D-Bus. systemctl --user and podman need the
  # real ones.
  export XDG_RUNTIME_DIR=/run/user/$(id -u)
  export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
else
  FRAME_REPO=${FRAME_REPO:-/home/steamos/dev/frametop}
  # Agent and IDE shells often inherit gpg-agent's socket while the keys are loaded
  # into keychain's ssh-agent at login. Use keychain's when the current one has no keys.
  if ! ssh-add -l >/dev/null 2>&1; then
    kc="$HOME/.keychain/$(hostname)-sh"
    # shellcheck disable=SC1090
    [ -f "$kc" ] && . "$kc" >/dev/null
  fi
fi
FRAME_HOST=${FRAME_HOST:-frame}
FRAME_BOX=${FRAME_BOX:-dev}

# on_frame '<command>': run a shell command on the Frame host, in FRAME_REPO.
on_frame() {
  if [ "$FRAME_LOCAL" = 1 ]; then
    (cd "$FRAME_REPO" && bash -c "$1")
  else
    ssh -o BatchMode=yes "$FRAME_HOST" "cd $(printf %q "$FRAME_REPO") && $1"
  fi
}

# on_frame_script [args...] < script: run a bash script from stdin on the Frame host.
on_frame_script() {
  if [ "$FRAME_LOCAL" = 1 ]; then
    bash -s -- "$@"
  else
    ssh -o BatchMode=yes "$FRAME_HOST" "bash -s -- $(printf '%q ' "$@")"
  fi
}

# fill_template <file>: print a file with @REPO@ replaced by the Frame's repo path.
fill_template() {
  sed "s|@REPO@|$FRAME_REPO|g" "$1"
}
