#!/usr/bin/env bash
# Frametop's one-line installer. In a terminal on the Steam Frame (Konsole in the desktop, or
# over SSH):
#
#   curl -fsSL https://deejanuz.github.io/frametop/get.sh | bash
#
# It asks which version to install, clones the repo into ~/frametop (or updates the clone
# that's there), and runs its install.sh. Run it again to update, or to switch versions.
# Options (piped, they go after "bash -s --"):
#   --stable        the main branch: tested releases (the default for a new install)
#   --experimental  the experimental branch: the newest features, less tested
#   --dir DIR       where the repo goes (default ~/frametop)
#   --clone-only    get or update the repo, but don't run install.sh
#   --yes, --no-bluetooth   passed to install.sh (--yes also answers this script's question:
#                   the version already there, or stable)
set -euo pipefail

usage() {
  cat <<'EOF'
usage: get.sh [--stable | --experimental] [--dir DIR] [--clone-only] [--yes] [--no-bluetooth]
piped: curl -fsSL https://deejanuz.github.io/frametop/get.sh | bash -s -- [options]
EOF
}

# Everything happens in main, called on the last line, so a download cut short runs nothing.
main() {
  local repo=https://github.com/DeeJanuz/frametop.git dir=$HOME/frametop branch= clone_only=0
  local yes=0 tty=0 current= def answer
  local pass=()
  while [ $# -gt 0 ]; do
    case $1 in
      --stable) branch=main ;;
      --experimental) branch=experimental ;;
      --dir) dir=${2:?--dir needs a folder}; shift ;;
      --clone-only) clone_only=1 ;;
      --yes) yes=1; pass+=("$1") ;;
      --no-bluetooth) pass+=("$1") ;;
      -h|--help) usage; return 0 ;;
      *) echo "unknown option: $1" >&2; usage >&2; return 2 ;;
    esac
    shift
  done

  if ! { grep -qx 'ID=steamos' /etc/os-release && grep -qE '^VARIANT_ID="?vr"?$' /etc/os-release; } 2>/dev/null; then
    echo "Frametop installs on a Steam Frame (SteamOS, VR variant). Run this in a terminal on the headset." >&2
    return 1
  fi
  # Piped into bash, stdin is this script: the questions (here and install.sh's) read the terminal.
  { : </dev/tty; } 2>/dev/null && tty=1
  if [ "$tty" = 0 ] && [ "$yes" = 0 ]; then
    echo "This asks questions, and there's no terminal to ask in: run it in one, or add --yes." >&2
    return 1
  fi

  if [ -e "$dir/.git" ]; then
    git -C "$dir" remote get-url origin 2>/dev/null | grep -qi 'frametop' ||
      { echo "$dir is a git repo, but not Frametop's. Pick another folder with --dir." >&2; return 1; }
    current=$(git -C "$dir" branch --show-current)
  elif [ -e "$dir" ]; then
    echo "$dir is there and isn't Frametop's repo. Move it, or pick another folder with --dir." >&2
    return 1
  fi

  if [ -z "$branch" ]; then
    def=main
    [ "$current" = experimental ] && def=experimental
    if [ "$yes" = 1 ]; then
      branch=$def
    else
      echo "Which version of Frametop?"
      echo "  1) stable: the main branch, tested releases"
      echo "  2) experimental: the newest features, less tested"
      [ -n "$current" ] && echo "(installed now: $current)"
      read -r -p "Choose 1 or 2 [$([ "$def" = main ] && echo 1 || echo 2)]: " answer </dev/tty || answer=
      case ${answer:-$def} in
        1|main|s*) branch=main ;;
        2|experimental|e*) branch=experimental ;;
        *) echo "not 1 or 2: $answer" >&2; return 2 ;;
      esac
    fi
  fi

  if [ ! -e "$dir" ]; then
    echo "Cloning Frametop ($branch) into $dir"
    git clone --branch "$branch" "$repo" "$dir"
  else
    if ! git -C "$dir" diff --quiet || ! git -C "$dir" diff --cached --quiet; then
      echo "$dir has changes of its own. Commit or stash them first (git -C $dir status)." >&2
      return 1
    fi
    echo "Updating $dir to the latest $branch"
    git -C "$dir" fetch --quiet origin
    if [ "$current" != "$branch" ]; then
      if git -C "$dir" show-ref --verify --quiet "refs/heads/$branch"; then
        git -C "$dir" switch --quiet "$branch"
      else
        git -C "$dir" switch --quiet --track -c "$branch" "origin/$branch"
      fi
    fi
    git -C "$dir" merge --ff-only --quiet "origin/$branch" ||
      { echo "$dir has commits of its own on $branch, so it can't just move to the latest. Update it by hand." >&2; return 1; }
  fi
  echo "Frametop $branch: $(git -C "$dir" log -1 --format='%h %s')"

  if [ "$clone_only" = 1 ]; then
    echo "Install with: cd $dir && ./install.sh"
    return 0
  fi
  cd "$dir"
  if [ "$tty" = 1 ]; then
    ./install.sh "${pass[@]}" </dev/tty
  else
    ./install.sh "${pass[@]}" </dev/null
  fi
}

main "$@"
