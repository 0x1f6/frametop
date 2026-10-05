#!/usr/bin/env python3
"""Tests for hands/ft-camwatch: its decision logic with made-up inputs (worn or not, VR apps,
remote viewers, cooldown, once per failure), following a growing log, and its settings. It
never notifies or restarts anything here.

  python3 hands/tests/test_camwatch.py
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HANDS = os.path.dirname(HERE)
sys.path.insert(0, HANDS)
sys.path.insert(0, HERE)
from test_camcheck import CLOSE, FAIL, GOOD_OPEN, RESUME, RESUME_OK, START  # noqa: E402

loader = importlib.machinery.SourceFileLoader("camwatch", os.path.join(HANDS, "ft-camwatch"))
spec = importlib.util.spec_from_loader("camwatch", loader)
cw = importlib.util.module_from_spec(spec)
loader.exec_module(cw)

ON = dict(cw.DEFAULTS, CAMWATCH_AUTO_RESTART="1", CAMWATCH_IDLE_S="60", CAMWATCH_COOLDOWN_MIN="30")
OFF = dict(cw.DEFAULTS)
IDLE = {"steamvr": True, "worn": False, "vr_apps": [], "remote": []}
WORN = dict(IDLE, worn=True)
F1, F2 = "a.log@17:02:47", "b.log@18:10:00"


def kinds(acts):
    return [k for k, _ in acts]


class Decide(unittest.TestCase):
    def setUp(self):
        self.mem = cw.new_memory()

    def d(self, now, failure, env=IDLE, conf=ON):
        return cw.decide(now, failure, env, self.mem, conf)

    def test_nothing_wrong(self):
        self.assertEqual(self.d(0, "", {}), [])
        self.assertEqual(self.mem["pending"], "")

    def test_notify_once(self):
        acts = self.d(0, F1, WORN, OFF)
        self.assertEqual(kinds(acts), ["log", "notify", "log"])
        self.assertIn("CAMWATCH_AUTO_RESTART=1", acts[2][1])
        self.assertEqual(self.d(2, F1, WORN, OFF), [])          # same failure, same reason: quiet
        self.assertEqual(self.d(4, F1, IDLE, OFF), [])          # off: never restarts, whatever the state
        self.assertEqual(self.d(1000, F1, IDLE, OFF), [])

    def test_notify_off(self):
        acts = self.d(0, F1, WORN, dict(OFF, CAMWATCH_NOTIFY="0"))
        self.assertNotIn("notify", kinds(acts))

    def test_never_while_worn(self):
        self.d(0, F1, WORN)
        for t in range(2, 4000, 2):
            self.assertNotIn("restart", kinds(self.d(t, F1, WORN)))
        self.assertIn("worn", self.mem["why"])

    def test_restart_after_idle_then_once(self):
        self.d(0, F1, WORN)
        self.assertNotIn("restart", kinds(self.d(10, F1, IDLE)))   # just taken off: wait
        self.assertIn("waiting", self.mem["why"])
        self.assertNotIn("restart", kinds(self.d(69, F1, IDLE)))
        acts = self.d(70, F1, IDLE)
        self.assertEqual(kinds(acts), ["log", "restart"])
        self.assertEqual(acts[1][1], F1)
        # the same failure again (the new XRService hasn't logged yet, or failed the same way)
        acts = self.d(72, F1, IDLE)
        self.assertNotIn("restart", kinds(acts))
        self.assertIn("restart the headset", self.mem["why"])
        for t in range(74, 10000, 500):
            self.assertNotIn("restart", kinds(self.d(t, F1, IDLE)))

    def test_put_on_resets_the_wait(self):
        self.d(0, F1, IDLE)
        self.d(50, F1, WORN)                                        # back on at 50 s
        self.assertNotIn("restart", kinds(self.d(70, F1, IDLE)))   # off again: the 60 s start over
        self.assertNotIn("restart", kinds(self.d(129, F1, IDLE)))
        self.assertIn("restart", kinds(self.d(131, F1, IDLE)))

    def test_cooldown(self):
        self.d(0, F1, IDLE)
        self.assertIn("restart", kinds(self.d(60, F1, IDLE)))
        # The restart's new XRService fails too: a new failure, but within the cooldown.
        self.d(90, "", IDLE)
        acts = self.d(120, F2, IDLE)
        self.assertIn("notify", kinds(acts))
        self.assertNotIn("restart", kinds(acts))
        self.assertNotIn("restart", kinds(self.d(60 + 30 * 60 - 2, F2, IDLE)))
        self.assertIn("cooldown", self.mem["why"])
        self.assertIn("restart", kinds(self.d(60 + 30 * 60, F2, IDLE)))

    def test_vr_app_remote_and_no_steamvr(self):
        for env, word in ((dict(IDLE, vr_apps=["AppId=450390"]), "VR app"),
                          (dict(IDLE, remote=["0A00000B:D2F0"]), "remote desktop"),
                          (dict(IDLE, steamvr=False), "SteamVR isn't running")):
            with self.subTest(word=word):
                self.mem = cw.new_memory()
                for t in range(0, 600, 2):
                    self.assertNotIn("restart", kinds(self.d(t, F1, env)))
                self.assertIn(word, self.mem["why"])
                # once it's gone (and the headset still off), the restart happens
                self.assertIn("restart", kinds(self.d(600, F1, IDLE)))

    def test_resolved(self):
        self.d(0, F1, WORN)
        acts = self.d(10, "", {})
        self.assertEqual(kinds(acts), ["log"])
        self.assertIn("no longer current", acts[0][1])
        self.assertEqual(self.mem["pending"], "")
        self.assertEqual(self.d(12, "", {}), [])

    def test_memory_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="camwatch-test-")
        try:
            path = os.path.join(tmp, "state", "camwatch.json")
            self.d(0, F1, IDLE)
            self.d(60, F1, IDLE)
            cw.save_memory(self.mem, path)
            mem = cw.load_memory(path)
            self.assertEqual(mem["restarted"], {F1: 60})
            self.assertEqual(mem["notified"], [F1])
            # the watcher restarted: the same failure neither notifies nor restarts again
            acts = cw.decide(200, F1, IDLE, mem, ON)
            self.assertNotIn("notify", kinds(acts))
            self.assertNotIn("restart", kinds(acts))
            self.assertEqual(cw.load_memory(os.path.join(tmp, "missing.json"))["restarted"], {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Follow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="camwatch-follow-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_growing_log(self):
        path = os.path.join(self.tmp, "x.log")
        f = cw.Follower(path)
        text = "\n".join(START + GOOD_OPEN) + "\n"
        with open(path, "w") as out:
            out.write(text)
        self.assertEqual(cw.current_failure(f.poll()), "")
        self.assertEqual(f.state.verdict()[0], "ok")
        # half a line, then the rest: read as one line
        more = "\n".join(CLOSE + RESUME + FAIL) + "\n"
        cut = more.index("Failed to load VCINT") + 5
        with open(path, "a") as out:
            out.write(more[:cut])
        self.assertEqual(cw.current_failure(f.poll()), "")
        with open(path, "a") as out:
            out.write(more[cut:])
        self.assertTrue(cw.current_failure(f.poll()).endswith("@11:30:01"))
        self.assertEqual(cw.current_failure(f.state, steamvr_running=False), "")
        with open(path, "a") as out:
            out.write("\n".join(CLOSE + RESUME_OK) + "\n")
        self.assertEqual(cw.current_failure(f.poll()), "")

    def test_new_log_starts_afresh(self):
        a, b = os.path.join(self.tmp, "a.log"), os.path.join(self.tmp, "b.log")
        with open(a, "w") as out:
            out.write("\n".join(START + GOOD_OPEN + CLOSE + RESUME + FAIL) + "\n")
        f = cw.Follower(a)
        self.assertTrue(cw.current_failure(f.poll()))
        with open(b, "w") as out:
            out.write("\n".join(START + GOOD_OPEN) + "\n")
        f.fixed = b   # as when xrservice.txt points at a new XRService's log
        self.assertEqual(cw.current_failure(f.poll()), "")
        self.assertEqual(f.path, b)


class Settings(unittest.TestCase):
    def test_read_conf(self):
        tmp = tempfile.mkdtemp(prefix="camwatch-conf-")
        try:
            path = os.path.join(tmp, "frametop.conf")
            with open(path, "w") as f:
                f.write("# Frametop\nREMOTE=1   # comment\nCAMWATCH_AUTO_RESTART=1\nCAMWATCH_IDLE_S='90'\n"
                        "CAMWATCH_IGNORE_APPIDS=\"1, 2\"\nnot a setting\n")
            conf = cw.read_conf(path)
            self.assertEqual(conf["CAMWATCH_AUTO_RESTART"], "1")
            self.assertEqual(cw.conf_int(conf, "CAMWATCH_IDLE_S"), 90)
            self.assertEqual(conf["CAMWATCH_IGNORE_APPIDS"], "1, 2")
            self.assertEqual(cw.conf_int(conf, "CAMWATCH_COOLDOWN_MIN"), 30)   # the default
            self.assertEqual(cw.read_conf(os.path.join(tmp, "none"))["CAMWATCH_AUTO_RESTART"], "0")
            self.assertEqual(cw.conf_int({"CAMWATCH_IDLE_S": "soon"}, "CAMWATCH_IDLE_S"), 60)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_headset_worn(self):
        tmp = tempfile.mkdtemp(prefix="camwatch-bl-")
        orig = cw.process_running
        try:
            os.makedirs(os.path.join(tmp, "panel0"))
            bright = os.path.join(tmp, "panel0", "brightness")
            for value, compositor, worn in (("95", True, True), ("0", True, False), ("95", False, False)):
                with open(bright, "w") as f:
                    f.write(value + "\n")
                cw.process_running = lambda name, c=compositor: c and name == "vrcompositor"
                self.assertEqual(cw.headset_worn(tmp), worn, (value, compositor))
            self.assertFalse(cw.headset_worn(os.path.join(tmp, "none")))
        finally:
            cw.process_running = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remote_viewers(self):
        tmp = tempfile.mkdtemp(prefix="camwatch-tcp-")
        try:
            path = os.path.join(tmp, "tcp")
            with open(path, "w") as f:
                f.write("  sl  local_address rem_address   st tx_queue rx_queue\n"
                        "   0: 0100007F:170C 00000000:0000 0A 00000000:00000000\n"      # listening
                        "   1: 0B00640A:170C 0C00640A:D2F0 01 00000000:00000000\n"      # a viewer
                        "   2: 0B00640A:0016 0C00640A:D2F1 01 00000000:00000000\n")     # ssh
            self.assertEqual(cw.remote_viewers(5900, (path,)), ["0C00640A:D2F0"])
            self.assertEqual(cw.remote_viewers(5901, (path,)), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
