#!/usr/bin/env python3
"""Tests for session.py's step mode (dry runs, no processes): the ready, countdown and hold
timeline in prompts.jsonl, R (redo), the timed flow (--auto), the pose pictures' display rule,
the length estimates, and a take recorded in many parts through review, export and validate.

  python3 hands/rec/tests/test_session.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import session  # noqa: E402
import takes  # noqa: E402
import validate  # noqa: E402
from test_validate import fhset, make_session, SESSION  # noqa: E402

SCRIPT = {
    "version": 1, "intro_s": 1, "between_s": 1,
    "welcome": {"title": "Test", "seconds": 1, "text": "A test."},
    "done": {"title": "Done", "seconds": 0, "text": "Done."},
    "stopped": {"title": "Stopped", "seconds": 0, "text": "Stopped."},
    "sections": [
        {"id": "poses", "title": "Poses", "intro": "Some poses.", "go": "Hold",
         "prompts": [{"text": "Fist.", "seconds": 4, "hands": "left", "pose": "fist", "distance": "near",
                      "position": "left"},
                     {"text": "Open.", "seconds": 4, "hands": "both", "pose": "open"}]}]}


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="handrec-session-test-")
        self.script = os.path.join(self.tmp, "script.json")
        with open(self.script, "w") as f:
            json.dump(SCRIPT, f)
        self.panel = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self, **kw):
        kw.setdefault("speed", 10)
        s = session.Session(os.path.join(self.tmp, "base"), {}, {}, "room", self.script, dry_run=True,
                            poses_dir=os.path.join(self.tmp, "no-poses"), **kw)
        s.print = self.panel.append
        return s

    def wait_for(self, s, pred, what, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = dict(s._status)
            if pred(st):
                return st
            time.sleep(0.005)
        self.fail("timed out waiting for %s: %s" % (what, s._status))

    def waiting(self, s, state, prompt=None):
        return self.wait_for(s, lambda st: st["state"] == state and st["waiting"]
                             and (prompt is None or st["prompt"] == prompt), "%s %s" % (state, prompt or ""))

    def events(self, s):
        with open(os.path.join(s.session_dir, "takes", "01-poses", "prompts.jsonl")) as f:
            return [json.loads(line) for line in f]

    def test_step_flow(self):
        s = self.session(next_after=0.0)
        s.start()
        s.join(20)
        self.assertEqual(s.state, "done")
        ev = self.events(s)
        self.assertEqual([e["event"] for e in ev],
                         ["take", "ready", "prompt", "wait", "ready", "prompt", "wait", "end"])
        self.assertEqual(ev[1]["id"], ev[2]["id"])
        self.assertEqual(ev[1]["seconds"], session.COUNTDOWN_S)
        self.assertTrue(ev[1]["t"] < ev[2]["t"] < ev[3]["t"] < ev[4]["t"])
        # the countdown is recorded (3 s at 10x speed), and the hold after it
        self.assertGreater(ev[2]["t"] - ev[1]["t"], 0.25e9)
        with open(os.path.join(s.session_dir, "session.json")) as f:
            self.assertEqual(json.load(f)["mode"], "step")
        # the panel: Ready?, then the countdown, then the section's word for the hold, the diagram
        self.assertIn("panel: action " + session.READY_TEXT, self.panel)
        self.assertLess(self.panel.index("panel: big 3"), self.panel.index("panel: big 1"))
        self.assertIn("panel: big Hold", self.panel)
        self.assertIn("panel: where left near", self.panel)
        self.assertIn("panel: rec on", self.panel)

    def test_redo(self):
        s = self.session()
        s.start()
        self.waiting(s, "starting")
        s.next_step()
        self.waiting(s, "intro")
        s.redo()   # nothing to do again yet: dropped
        s.next_step()
        st = self.waiting(s, "ready", "Fist.")
        self.assertFalse(st["can_redo"])
        s.next_step()
        self.wait_for(s, lambda st: st["state"] == "running", "the hold")
        s.redo()   # during the hold: it starts again
        self.waiting(s, "ready", "Fist.")
        s.next_step()
        st = self.waiting(s, "ready", "Open.")
        self.assertTrue(st["can_redo"])
        s.redo()   # at the next step's ready screen: the one before goes again
        self.waiting(s, "ready", "Fist.")
        s.next_step()
        self.waiting(s, "ready", "Open.")
        s.next_step()
        s.join(20)
        self.assertEqual(s.state, "done")
        ev = self.events(s)
        names = [e["event"] for e in ev]
        self.assertEqual(names, ["take", "ready", "prompt", "redo", "wait", "ready", "prompt", "wait", "redo",
                                 "ready", "prompt", "wait", "ready", "prompt", "wait", "end"])
        first, second = ev[3], ev[8]
        self.assertEqual(first["id"], "poses/fist/left/near/left")
        self.assertEqual(first["from"], ev[1]["t"])          # from its countdown
        self.assertTrue(ev[2]["t"] < first["to"] <= ev[4]["t"])
        self.assertEqual((second["from"], second["id"]), (ev[5]["t"], first["id"]))
        self.assertTrue(ev[6]["t"] < second["to"] <= ev[7]["t"])
        # what labels keep: prompts outside every redo range
        kept = [e["id"] for e in ev if e["event"] == "prompt"
                and not any(r["from"] <= e["t"] <= r["to"] for r in ev if r["event"] == "redo")]
        self.assertEqual(kept, ["poses/fist/left/near/left", "poses/open/both"])

    def test_pause_while_waiting(self):
        s = self.session()
        s.start()
        self.waiting(s, "starting")
        s.next_step()
        self.waiting(s, "intro")
        s.next_step()
        self.waiting(s, "ready", "Fist.")
        s.pause()
        self.wait_for(s, lambda st: st["state"] == "paused", "paused")
        s.next_step()   # ignored while paused
        s.resume()
        st = self.waiting(s, "ready", "Fist.")
        s.stop(wait=10)
        self.assertEqual(s.state, "stopped")
        # nothing recorded: no take
        self.assertEqual(os.listdir(os.path.join(s.session_dir, "takes")), [])

    def test_auto(self):
        s = self.session(auto=True, speed=20)
        s.start()
        s.join(20)
        self.assertEqual(s.state, "done")
        ev = self.events(s)
        self.assertEqual([e["event"] for e in ev], ["take", "prompt", "prompt", "prompt", "end"])
        self.assertEqual(ev[1]["id"], "poses/intro")

    def test_auto_redo_after_pause(self):
        s = self.session(auto=True)
        s.start()
        self.wait_for(s, lambda st: st["state"] == "running" and st["prompt"] == "Fist.", "the first prompt")
        s.pause()
        self.wait_for(s, lambda st: st["state"] == "paused", "paused")
        s.redo()   # ends the pause; the prompt starts again, recording again
        s.join(20)
        self.assertEqual(s.state, "done")
        names = [e["event"] for e in self.events(s)]
        self.assertEqual(names, ["take", "prompt", "prompt", "pause", "redo", "resume", "prompt", "prompt", "end"])

    def test_pose_view(self):
        d = os.path.join(self.tmp, "poses")
        os.makedirs(d)
        for name in ("fist.png", "cross.png"):
            open(os.path.join(d, name), "wb").close()
        with open(os.path.join(d, "poses.json"), "w") as f:
            json.dump({"fist": {"file": "fist.png", "two_hands": False, "caption": "A fist"},
                       "cross": {"file": "../cross.png", "two_hands": True, "caption": ""},
                       "ok": {"file": "ok.png", "two_hands": False}}, f)
        poses = session.load_poses(d)
        fist = os.path.join(d, "fist.png")
        self.assertEqual(session.pose_view(poses, {"pose": "fist", "hands": "right"}), (fist, "", "A fist"))
        self.assertEqual(session.pose_view(poses, {"pose": "fist", "hands": "left"})[1], "mirror")
        self.assertEqual(session.pose_view(poses, {"pose": "fist", "hands": "both"})[1], "both")
        self.assertEqual(session.pose_view(poses, {"pose": "fist", "hands": "any"})[1], "")
        # two hands drawn already; the file stays in the folder
        self.assertEqual(session.pose_view(poses, {"pose": "cross", "hands": "both"})[:2],
                         (os.path.join(d, "cross.png"), ""))
        self.assertEqual(session.pose_view(poses, {"pose": "ok", "hands": "both"}), ("", "", ""))   # no file
        self.assertEqual(session.pose_view(poses, {"pose": "claw", "hands": "both"}), ("", "", ""))
        self.assertEqual(session.load_poses(os.path.join(self.tmp, "none")), {})

    def test_plan(self):
        plan, _ = session.build_plan(SCRIPT, {})
        self.assertEqual(session.plan_steps(plan), 2)
        self.assertEqual(session.plan_seconds(SCRIPT, plan, auto=False), 2 * session.COUNTDOWN_S + 8)
        self.assertEqual(session.plan_seconds(SCRIPT, plan, auto=True), 1 + 1 + 8)
        self.assertIn("2 steps", session.plan_summary(SCRIPT, plan))


class ManyPartsTest(unittest.TestCase):
    """A take recorded in 40 parts (step mode stops the recording between steps): review reads
    them in order, export makes one stream, validate passes."""

    def setUp(self):
        if not takes.find_zstd():
            self.skipTest("no zstd")
        self.tmp = tempfile.mkdtemp(prefix="handrec-parts-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parts(self):
        make_session(self.tmp)
        tdir = os.path.join(self.tmp, "sessions", SESSION, "takes", "01-hand-size")
        t0 = 5 * 10 ** 12
        for part in range(1, 41):
            name = "sets.bin" if part == 1 else "sets-%d.bin" % part
            with open(os.path.join(tdir, name), "wb") as f:
                for i in range(3):   # 3 sets a part, 10 s apart between parts
                    f.write(fhset(t0 + part * 10 ** 10 + i * 10 ** 8, part))
        store = takes.Store(self.tmp)
        index = takes.take_index(tdir)
        self.assertEqual(len(index.parts), 40)
        self.assertEqual(len(index), 120)
        times = [index.time_ns(i) for i in range(len(index))]
        self.assertEqual(times, sorted(times))   # sets-10.bin after sets-9.bin
        self.assertAlmostEqual(index.duration_s(), 40 * 0.2)   # the gaps between parts don't count
        path = store.export(SESSION, low_priority=False)
        r = validate.validate(path)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.summary["sets"], 120 + 30)


if __name__ == "__main__":
    unittest.main()
