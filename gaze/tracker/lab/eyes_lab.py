"""What the lab tools share: where recordings are kept, and the tracker's modules.

Recordings of the eye cameras are biometric data. They're kept outside the repo, in
~/.local/share/frametop/eyes/captures (FT_EYES_CAPTURES overrides), one folder each, 0700,
and never leave the Frame except for the 7i's copies frame-job makes for offline jobs.
"""
import os
import sys
from pathlib import Path

TRACKER = Path(__file__).resolve().parents[1]  # gaze/tracker: ft-eyes, eyes_model, eyes_pupil
LAB = Path(__file__).resolve().parent
CAPTURES = Path(os.environ.get("FT_EYES_CAPTURES", Path.home() / ".local/share/frametop/eyes/captures"))
sys.path.insert(0, str(TRACKER))


def capture(arg):
    """A recording's folder: a path as given, or a bare name in CAPTURES."""
    p = Path(arg).expanduser()
    return p if p.exists() or os.sep in arg else CAPTURES / arg


def new_capture(name):
    """A new, private recording folder in CAPTURES; exits if it's already there."""
    out = CAPTURES / name
    if out.exists():
        sys.exit(f"{out} exists")
    out.mkdir(parents=True)
    for d in (CAPTURES, out):
        d.chmod(0o700)
    return out
