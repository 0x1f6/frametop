#!/usr/bin/env python3
"""Frametop Hand Recorder: record your hands for the open hand dataset (hands/rec/DESIGN.md).

A Kirigami (QML) app with a Python backend. It runs in the dev container:
  - Welcome: the consent text (CONSENT.md), shown the first time and again when its version
    changes. Agreeing writes profile.json with a random contributor id.
  - Before you start: the checklist (objects, controller straps, lighting, sleeves, privacy,
    free space) and what will happen. Start hands it to the session runner (session.py).
  - Session: the runner's live status, Start, Pause/Resume, Skip section and Stop (Space
    pauses and Esc stops while the window has focus). The prompts appear in the headset.
  - Review: sessions, their takes, and a viewer for one frame set at a time, where ranges,
    takes and sessions can be deleted (takes.py).
  - Export: compress what's kept into exports/<session>/ at nice 19 (takes.py), with a warning
    when the headset is worn.
  - Upload: UPLOAD.md with the export filled in and the upload command to copy.
Everything lives under ~/.local/share/frametop/hands/contrib (--base). Nothing is uploaded
from here. Launch with hands/rec/ft-handrec (host wrapper).
"""
import argparse
import datetime
import os
import re
import shlex
import signal
import sys
import threading
import uuid

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import takes  # noqa: E402  (next to this file)

# The dataset contributions go to: a placeholder until the maintainer decides (DESIGN.md).
HF_DATASET = "DeeJanuz/frametop-hands"
CONSENT_PATH = os.path.join(HERE, "CONSENT.md")
UPLOAD_PATH = os.path.join(HERE, "UPLOAD.md")
SCRIPT_PATH = os.path.join(HERE, "script.json")
# The headset counts as worn while vrcompositor runs and a display panel is lit: SteamVR turns
# the panels off 5 s after the headset comes off (frame-job's check; the proximity sensor's
# readings are too noisy). The dev container sees the host's processes and /sys.
BACKLIGHTS = "/sys/class/backlight"
ROUND_BYTES = 10 * 1000 ** 3  # about what one round of recording takes
# The checklist's choices; the keys are what session.json stores.
OBJECTS = [("pencil", "Pencil or pen"), ("phone", "Phone"), ("cup", "Cup or mug (empty)"),
           ("keyboard", "Keyboard"), ("mouse", "Mouse"), ("gamepad", "Gamepad"),
           ("small", "Something small (a coin, a key, a bottle cap)")]
LIGHTING = [("dim", "Dim: one lamp only"), ("room", "Normal room light"), ("daylight", "Daylight near a window")]
SLEEVES = [("short", "Short sleeves or bare arms"), ("long", "Long sleeves"), ("", "Rather not say")]
HANDEDNESS = [("", "Rather not say"), ("right", "Right-handed"), ("left", "Left-handed"),
              ("both", "Both (ambidextrous)")]
ACTIVE_STATES = ("starting", "intro", "running", "paused", "between")
# Shown side by side in the viewer at this height; thumbnails are smaller.
SET_HEIGHT = 480
THUMB_HEIGHT = 96


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def consent_version():
    """The "Version: ..." line of CONSENT.md: a new one asks everyone to agree again."""
    m = re.search(r"^Version:\s*(\S+)", read_text(CONSENT_PATH), re.M)
    return m.group(1) if m else "unknown"


def is_draft(path):
    return "DRAFT" in read_text(path).split("\n", 1)[0]


def process_running(name):
    """Like pgrep -x NAME, from /proc."""
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip() == name:
                        return True
            except OSError:
                pass
    return False


def headset_worn():
    """True while vrcompositor runs and any panel's backlight is on; False if unreadable."""
    lit = False
    try:
        for name in os.listdir(BACKLIGHTS):
            try:
                with open(os.path.join(BACKLIGHTS, name, "brightness")) as f:
                    lit = lit or int(f.read().strip()) > 0
            except (OSError, ValueError):
                pass
    except OSError:
        return False
    return lit and process_running("vrcompositor")


def gigabytes(n):
    return f"{n / 1000 ** 3:.1f} GB"


def session_label(sid):
    """20261002-101500 -> 2026-10-02 10:15."""
    try:
        return datetime.datetime.strptime(sid, "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return sid


def grey_image(cam):
    """A camera's raw 8-bit pixels as a QImage that owns its data."""
    img = QImage(cam["pixels"], cam["width"], cam["height"], cam["width"], QImage.Format_Grayscale8)
    return img.copy()


class FrameProvider(QQuickImageProvider):
    """image://frames/set/SESSION/TAKE/INDEX: one frame set, every camera side by side.
    image://frames/thumb/SESSION/TAKE: the take's first slam_left image, small.
    Anything after "?" is ignored (it makes QML load the image again)."""

    def __init__(self, store):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.store = store

    def requestImage(self, ident, size, requested):
        parts = ident.split("?", 1)[0].split("/")
        try:
            if parts[0] == "thumb" and len(parts) == 3:
                return self._thumb(parts[1], parts[2])
            if parts[0] == "set" and len(parts) == 4:
                return self._set(parts[1], parts[2], int(parts[3]),
                                 requested.height() if requested.height() > 0 else SET_HEIGHT)
        except (OSError, ValueError, IndexError, KeyError) as e:
            print(f"ft-handrec: image {ident}: {e}", file=sys.stderr)
        img = QImage(4, 3, QImage.Format_Grayscale8)
        img.fill(0)
        return img

    def _index(self, session, take):
        return takes.take_index(self.store.take_dir(session, take))

    def _thumb(self, session, take):
        index = self._index(session, take)
        if not len(index):
            raise ValueError("no sets")
        names = [c["name"] for c in index.cams]
        cams = index.read_set(0, only="slam_left" if "slam_left" in names else names[0])
        return grey_image(cams[0]).scaledToHeight(THUMB_HEIGHT, Qt.SmoothTransformation)

    def _set(self, session, take, i, height):
        cams = self._index(session, take).read_set(i)
        scaled = [grey_image(c).scaledToHeight(height, Qt.SmoothTransformation) for c in cams]
        gap = 8
        out = QImage(sum(s.width() for s in scaled) + gap * (len(scaled) - 1), height, QImage.Format_RGB32)
        out.fill(QColor(30, 30, 30))
        p = QPainter(out)
        font = QFont()
        font.setPixelSize(max(12, height // 28))
        p.setFont(font)
        x = 0
        for cam, img in zip(cams, scaled):
            p.drawImage(x, 0, img)
            p.setPen(QColor(255, 200, 80))
            p.drawText(x + 6, 6 + font.pixelSize(), cam["name"])
            x += img.width() + gap
        p.end()
        return out


class Backend(QObject):
    profileChanged = Signal()
    diskChanged = Signal()
    lightingChanged = Signal()
    statusChanged = Signal()
    sessionsChanged = Signal()
    exportChanged = Signal()
    message = Signal(str, bool)  # text, is error
    # From other threads (the session runner, export, the lighting check): queued to this one.
    _statusArrived = Signal(dict)
    _lightingArrived = Signal(str)
    _exportProgress = Signal(float, str)
    _exportFinished = Signal(str, str)  # path, error ("" when it worked; "cancelled")

    def __init__(self, store, session_options=None):
        super().__init__()
        self.store = store
        self._session_options = session_options or {}  # test hooks for Session: dry_run, speed
        os.makedirs(store.base, mode=0o700, exist_ok=True)
        self._session_mod = None
        self._session_error = ""
        self._session = None
        self._session_id = ""
        self._status = {}
        self._lighting_note = ""
        self._export_cancel = None
        self._export_thread = None
        self._export_fraction = 0.0
        self._export_text = ""
        self._export_session = ""
        self._statusArrived.connect(self._on_status)
        self._lightingArrived.connect(self._on_lighting)
        self._exportProgress.connect(self._on_export_progress)
        self._exportFinished.connect(self._on_export_finished)
        self.disk_timer = QTimer(interval=30000, timeout=self.diskChanged.emit)
        self.disk_timer.start()

    def _thread(self, fn):
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()
        return thread

    # --- the session runner, imported when first needed (it's written separately)
    def _runner(self):
        if self._session_mod is None and not self._session_error:
            try:
                import session as mod
                self._session_mod = mod
            except Exception as e:  # missing, or broken: say so, the rest of the app still works
                self._session_error = f"The session runner (hands/rec/session.py) can't be loaded: {e}"
        return self._session_mod

    @Property(str, notify=statusChanged)
    def runnerError(self):
        self._runner()
        return self._session_error

    # --- consent and profile
    @Property(str, constant=True)
    def consentText(self):
        return read_text(CONSENT_PATH) or "CONSENT.md is missing."

    @Property(str, constant=True)
    def consentVersion(self):
        return consent_version()

    @Property(bool, constant=True)
    def textsDraft(self):
        return is_draft(CONSENT_PATH) or is_draft(UPLOAD_PATH)

    @Property(bool, notify=profileChanged)
    def needsConsent(self):
        profile = self.store.profile()
        consent = profile.get("consent") or {}
        return not (profile.get("contributor") and consent.get("adult") is True
                    and consent.get("version") == consent_version())

    @Property(str, notify=profileChanged)
    def contributor(self):
        return self.store.profile().get("contributor", "")

    @Property(str, notify=profileChanged)
    def consentAccepted(self):
        return (self.store.profile().get("consent") or {}).get("accepted", "")

    @Property(str, notify=profileChanged)
    def handedness(self):
        return (self.store.profile().get("optional") or {}).get("handedness", "")

    @Property("QVariantList", constant=True)
    def handednessChoices(self):
        return [{"value": k, "text": v} for k, v in HANDEDNESS]

    @Slot(bool, bool, str)
    def acceptConsent(self, adult, agree, handedness):
        """Write profile.json. A contributor id, once made, stays (a new consent version keeps it)."""
        if not (adult and agree):
            self.message.emit("Both boxes need ticking to take part", True)
            return
        profile = self.store.profile()
        optional = profile.get("optional") if isinstance(profile.get("optional"), dict) else {}
        optional["handedness"] = handedness if handedness in dict(HANDEDNESS) else ""
        optional.setdefault("notes", "")
        profile = {"schema": 1, "contributor": profile.get("contributor") or str(uuid.uuid4()),
                   "consent": {"version": consent_version(),
                               "accepted": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                               "adult": True},
                   "optional": optional}
        takes.write_json(self.store.profile_path, profile)
        self.profileChanged.emit()
        self.message.emit("Thank you. Your contributor id: " + profile["contributor"], False)

    # --- the checklist
    @Property("QVariantList", constant=True)
    def objects(self):
        return [{"value": k, "text": v} for k, v in OBJECTS]

    @Property("QVariantList", constant=True)
    def lightingChoices(self):
        return [{"value": k, "text": v} for k, v in LIGHTING]

    @Property("QVariantList", constant=True)
    def sleeveChoices(self):
        return [{"value": k, "text": v} for k, v in SLEEVES]

    @Property(str, notify=diskChanged)
    def freeText(self):
        return gigabytes(takes.free_bytes(self.store.base))

    @Property(bool, notify=diskChanged)
    def diskOk(self):
        return takes.free_bytes(self.store.base) >= ROUND_BYTES

    @Property(str, notify=lightingChanged)
    def lightingNote(self):
        return self._lighting_note

    @Slot(str)
    def checkLighting(self, chosen):
        """Compare the cameras' brightness now with earlier rounds (session.similar_lighting):
        a round in light like an earlier one adds less to the dataset."""
        mod = self._runner()
        if not mod:
            return
        base = self.store.base
        labels = dict(LIGHTING)

        def run():
            try:
                ring = mod.ring_lighting()
                if ring is None:
                    note = ("The cameras aren't running yet, so the light can't be compared with your "
                            "earlier rounds now. The session checks it when it starts.")
                else:
                    match = mod.similar_lighting(base, {"chosen": chosen, "ring": ring})
                    note = "" if not match else (
                        f"The cameras see about the same light as in your round of {session_label(match[0])} "
                        f"({labels.get(match[1], match[1] or 'no choice')}). A different light helps the "
                        "dataset more: change the lighting if you can, or go ahead anyway.")
            except Exception as e:
                note = f"Couldn't check the light: {e}"
            self._lightingArrived.emit(note)
        self._thread(run)

    def _on_lighting(self, note):
        self._lighting_note = note
        self.lightingChanged.emit()

    # --- the session
    @Property("QVariantMap", notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=statusChanged)
    def sessionActive(self):
        return self._session is not None and self._status.get("state", "starting") in ACTIVE_STATES

    @Property(str, notify=statusChanged)
    def sessionId(self):
        return self._session_id

    @Slot("QVariantMap", str, result=bool)
    def startSession(self, checklist, lighting):
        if self.sessionActive:
            return False
        mod = self._runner()
        if not mod:
            self.message.emit(self._session_error, True)
            return False
        if self.needsConsent:
            self.message.emit("Agree to the consent text first (Welcome page)", True)
            return False
        checklist = dict(checklist)
        checklist["objects"] = [o for o in checklist.get("objects", []) if o in dict(OBJECTS)]
        checklist["own_objects"] = [o.strip() for o in checklist.get("own_objects", []) if str(o).strip()]
        try:
            self._session = mod.Session(self.store.base, self.store.profile(), checklist, lighting, SCRIPT_PATH,
                                        on_status=lambda s: self._statusArrived.emit(dict(s)),
                                        **self._session_options)
        except Exception as e:
            self.message.emit(f"Couldn't set up the session: {e}", True)
            return False
        self._session_id = ""
        self._status = {"state": "starting"}
        self.statusChanged.emit()
        session = self._session

        def run():
            try:
                session.start()
            except Exception as e:
                self._statusArrived.emit({"state": "error", "error": str(e)})
        self._thread(run)
        return True

    def _on_status(self, status):
        self._status = status
        if self._session is not None and not self._session_id:
            self._session_id = os.path.basename(str(getattr(self._session, "session_dir", "") or ""))
        self.statusChanged.emit()
        if status.get("state") in ("done", "stopped", "error"):
            self.sessionsChanged.emit()

    def _control(self, name):
        if self._session is None:
            return
        try:
            getattr(self._session, name)()
        except Exception as e:
            self.message.emit(f"{name}: {e}", True)

    @Slot()
    def togglePause(self):
        if self._status.get("state") == "paused":
            self._control("resume")
        elif self.sessionActive:
            self._control("pause")

    @Slot()
    def skipSection(self):
        if self.sessionActive:
            self._control("skip")

    @Slot()
    def stopSession(self):
        """Stop: the take in progress is kept, as far as it got. It can block while the
        recording is written out, so it runs off this thread."""
        if self.sessionActive:
            session = self._session
            self._thread(lambda: session.stop())

    def shutdown(self):
        """The window closes: end a running session (blocking, so its files are complete)."""
        if self.sessionActive:
            try:
                self._session.stop()
            except Exception:
                pass
        if self._export_cancel:
            self._export_cancel.set()  # and wait, so export can remove its half-written copy
            self._export_thread.join(15)

    # --- review
    @Property("QVariantList", notify=sessionsChanged)
    def sessions(self):
        out = []
        for s in self.store.sessions():
            s["label"] = session_label(s["id"])
            s["sizeText"] = takes.human_bytes(s["bytes"])
            s["exportText"] = takes.human_bytes(s["export_bytes"]) if s["exported"] else ""
            s["lightingText"] = dict(LIGHTING).get(s["lighting"], "")
            s["active"] = self.sessionActive and s["id"] == self._session_id
            s["statusText"] = {"recording": "" if s["active"] else "interrupted", "error": "ended with an error",
                               "stopped": "stopped early"}.get(s["status"], "")
            out.append(s)
        return out

    @Slot()
    def refreshSessions(self):
        self.sessionsChanged.emit()
        self.diskChanged.emit()

    @Slot(str, result="QVariantList")
    def takeList(self, session):
        try:
            rows = self.store.takes(session)
        except ValueError:
            return []
        for t in rows:
            t["durationText"] = f"{int(t['duration_s'] // 60)}:{int(t['duration_s'] % 60):02d}"
            t["sizeText"] = takes.human_bytes(t["bytes"])
            t.pop("ranges")
        return rows

    @Slot(str, str, result="QVariantMap")
    def takeInfo(self, session, take):
        """For the viewer: {count, title, cams, ranges: [[first, last] set indexes]}."""
        try:
            index = takes.take_index(self.store.take_dir(session, take))
            ranges = self.store.ranges(session, take)
        except ValueError:
            return {"count": 0, "title": take, "cams": [], "ranges": []}
        marks = []
        for a, b in ranges:
            inside = [i for i in range(len(index)) if a <= index.time_ns(i) <= b]
            marks.append([inside[0], inside[-1]] if inside else [-1, -1])
        return {"count": len(index), "title": self.store.take_meta(session, take).get("title") or take,
                "cams": [c["name"] for c in index.cams], "ranges": marks}

    @Slot(str, str, int, result=str)
    def setTime(self, session, take, i):
        """Set i's time from the take's first set, m:ss.s."""
        try:
            index = takes.take_index(self.store.take_dir(session, take))
            t = (index.time_ns(i) - index.time_ns(0)) / 1e9
        except (ValueError, IndexError):
            return ""
        return f"{int(t // 60)}:{t % 60:04.1f}"

    def _active_guard(self, session):
        if self.sessionActive and session == self._session_id:
            self.message.emit("This session is still recording: stop it first", True)
            return True
        return False

    @Slot(str, str, int, int)
    def deleteRange(self, session, take, first, last):
        """Leave sets first..last (indexes, either order) out of the export."""
        if self._active_guard(session):
            return
        index = takes.take_index(self.store.take_dir(session, take))
        first, last = sorted((max(0, first), min(len(index) - 1, last)))
        if first > last:
            return
        self.store.delete_range(session, take, index.time_ns(first), index.time_ns(last))
        self.message.emit(f"Sets {first + 1} to {last + 1} won't be exported", False)
        self.sessionsChanged.emit()

    @Slot(str, str, int)
    def restoreRange(self, session, take, k):
        self.store.restore_range(session, take, k)
        self.message.emit("Range restored", False)
        self.sessionsChanged.emit()

    @Slot(str, str)
    def deleteTake(self, session, take):
        if self._active_guard(session):
            return
        try:
            self.store.delete_take(session, take)
            self.message.emit(f"Deleted {take}", False)
        except (OSError, ValueError) as e:
            self.message.emit(f"Couldn't delete {take}: {e}", True)
        self.refreshSessions()

    @Slot(str)
    def deleteSession(self, session):
        if self._active_guard(session):
            return
        try:
            self.store.delete_session(session)
            self.message.emit(f"Deleted the session of {session_label(session)}", False)
        except (OSError, ValueError) as e:
            self.message.emit(f"Couldn't delete the session: {e}", True)
        self.refreshSessions()

    # --- export
    @Slot(result=bool)
    def headsetWorn(self):
        return headset_worn()

    @Property(bool, constant=True)
    def zstdFound(self):
        return takes.find_zstd() is not None

    @Property(bool, notify=exportChanged)
    def exporting(self):
        return self._export_cancel is not None

    @Property(float, notify=exportChanged)
    def exportFraction(self):
        return self._export_fraction

    @Property(str, notify=exportChanged)
    def exportText(self):
        return self._export_text

    @Property(str, notify=exportChanged)
    def exportSessionId(self):
        return self._export_session

    @Slot(str, bool)
    def exportSession(self, session, keep_notes):
        if self.exporting or self._active_guard(session):
            return
        cancel = threading.Event()
        self._export_cancel = cancel
        self._export_session = session
        self._export_fraction = 0.0
        self._export_text = "Starting"
        self.exportChanged.emit()

        def run():
            try:
                path = self.store.export(session, progress=lambda f, text: self._exportProgress.emit(f, text),
                                         cancel=cancel, keep_notes=keep_notes)
                self._exportFinished.emit(path, "")
            except takes.Cancelled:
                self._exportFinished.emit("", "cancelled")
            except Exception as e:
                self._exportFinished.emit("", str(e) or type(e).__name__)
        self._export_thread = self._thread(run)

    @Slot()
    def cancelExport(self):
        if self._export_cancel:
            self._export_cancel.set()
            self._export_text = "Cancelling"
            self.exportChanged.emit()

    def _on_export_progress(self, fraction, text):
        if self._export_cancel and not self._export_cancel.is_set():
            self._export_fraction, self._export_text = fraction, text
            self.exportChanged.emit()

    def _on_export_finished(self, path, error):
        self._export_cancel = None
        if error == "cancelled":
            self._export_text = "Cancelled: nothing was kept"
        elif error:
            self._export_text = "Failed: " + error
            self.message.emit("Export failed: " + error, True)
        else:
            self._export_fraction = 1.0
            self._export_text = f"Exported to {path} ({takes.human_bytes(takes.tree_bytes(path))})"
            self.message.emit("Export ready", False)
        self.exportChanged.emit()
        self.refreshSessions()

    @Slot(str)
    def deleteExport(self, session):
        try:
            self.store.delete_export(session)
            self.message.emit("Export deleted; the session stays", False)
        except (OSError, ValueError) as e:
            self.message.emit(f"Couldn't delete the export: {e}", True)
        self.refreshSessions()

    @Property(str, constant=True)
    def exportsDir(self):
        return self.store.exports_dir

    # --- upload
    @Property(str, constant=True)
    def dataset(self):
        return HF_DATASET

    @Slot(str, result=str)
    def uploadCommand(self, session):
        try:
            path = self.store.export_dir(session)
        except ValueError:
            return ""
        contributor = self.contributor or "CONTRIBUTOR"
        return " ".join(["huggingface-cli", "upload", HF_DATASET, shlex.quote(path),
                         f"contributions/{contributor}/{session}", "--repo-type", "dataset", "--create-pr",
                         "--commit-message", shlex.quote(f"Hands: session {session} from {contributor}")])

    @Slot(str, result=str)
    def uploadText(self, session):
        """UPLOAD.md with this export's path, size and command filled in."""
        try:
            path = self.store.export_dir(session)
        except ValueError:
            return ""
        values = {"EXPORT_PATH": path, "EXPORT_SIZE": takes.human_bytes(takes.tree_bytes(path)),
                  "CONTRIBUTOR": self.contributor or "CONTRIBUTOR", "SESSION": session, "DATASET": HF_DATASET,
                  "COMMAND": self.uploadCommand(session)}
        text = read_text(UPLOAD_PATH) or "UPLOAD.md is missing."
        return re.sub(r"@([A-Z_]+)@", lambda m: values.get(m.group(1), m.group(0)), text)

    @Slot(QColor)
    def setLinkColor(self, color):
        palette = QGuiApplication.palette()
        palette.setColor(QPalette.Link, color)
        QGuiApplication.setPalette(palette)

    @Slot(str)
    def copy(self, text):
        QGuiApplication.clipboard().setText(text)
        self.message.emit("Copied", False)


def main():
    ap = argparse.ArgumentParser(description="Frametop Hand Recorder")
    ap.add_argument("--base", default=takes.DEFAULT_BASE, help="where profile.json, sessions/ and exports/ go")
    ap.add_argument("--page", default="", help="open on this page: welcome, checklist, session, review, export, upload")
    ap.add_argument("--dry-run", action="store_true",
                    help="test: sessions start no processes and print the panel's commands")
    ap.add_argument("--speed", type=float, default=1.0, help="test, with --dry-run: run sessions this much faster")
    a, qt_args = ap.parse_known_args()
    app = QGuiApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("ft-handrec")
    app.setApplicationDisplayName("Frametop Hand Recorder")
    app.setDesktopFileName("ft-handrec")
    if not QIcon.themeName():
        QIcon.setThemeName("breeze")
    QQuickStyle.setStyle("org.kde.desktop")
    store = takes.Store(a.base)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("frames", FrameProvider(store))
    backend = Backend(store, {"dry_run": True, "speed": a.speed} if a.dry_run else {})
    app.aboutToQuit.connect(backend.shutdown)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("startPage", a.page)
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "main.qml")))
    if not engine.rootObjects():
        sys.exit(1)

    # SIGTERM and Ctrl+C quit as closing does, so a running session still stops cleanly. Python
    # runs signal handlers between bytecodes: the timer gives it some while Qt waits.
    def on_signal(*_):
        engine.rootObjects()[0].setProperty("quitting", True)
        app.quit()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    tick = QTimer(interval=500, timeout=lambda: None)
    tick.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
