#!/bin/bash
# Runs on the Frame host. Starts the dev container, if it isn't running, in a systemd scope
# of its own. Whoever starts a podman container owns its monitor (conmon): started from one
# of our services (distrobox enter starts it on demand), the container, and everything in it
# like the desktop's compositor, would be killed when that service stops. Call this before
# `distrobox enter`.
box=${FRAME_BOX:-dev}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
[ "$(podman container inspect -f '{{.State.Running}}' "$box" 2>/dev/null)" = true ] && exit 0
podman container exists "$box" 2>/dev/null || exit 0  # not created yet: setup/dev-container.sh does that
exec systemd-run --user --scope --quiet --collect --description="$box container (started for Frametop)" \
  podman start "$box" >/dev/null
