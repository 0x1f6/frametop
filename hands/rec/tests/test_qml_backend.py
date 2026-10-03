#!/usr/bin/env python3
"""main.qml against ft_handrec.Backend: every backend.name(...) the window calls is a slot, and
every backend.name it reads is a property or a slot. A method that lost its @Slot shows up in
QML only as "is not a function" when its button is pressed (2026-10-03: Export did nothing).
Needs PySide6 (the dev container); skipped without it.

  python3 hands/rec/tests/test_qml_backend.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.dirname(HERE)
sys.path.insert(0, REC)


class QmlBackendTest(unittest.TestCase):
    def setUp(self):
        try:
            import ft_handrec
        except ImportError as e:
            self.skipTest(f"no PySide6: {e}")
        meta = ft_handrec.Backend.staticMetaObject
        self.slots = {bytes(meta.method(i).name()).decode() for i in range(meta.methodCount())}
        self.props = {meta.property(i).name() for i in range(meta.propertyCount())}
        with open(os.path.join(REC, "main.qml")) as f:
            self.qml = f.read()

    def test_calls_are_slots(self):
        called = set(re.findall(r"\bbackend\.(\w+)\s*\(", self.qml))
        self.assertTrue(called)
        self.assertEqual(sorted(called - self.slots), [], "called from main.qml but not a slot")

    def test_reads_exist(self):
        read = set(re.findall(r"\bbackend\.(\w+)\b(?!\s*\()", self.qml))
        self.assertTrue(read)
        self.assertEqual(sorted(read - self.props - self.slots), [], "read in main.qml but not on Backend")


if __name__ == "__main__":
    unittest.main()
