#!/usr/bin/env python3
"""Tests for log-recall — Layer 1: the PostToolUse/Read telemetry logger (M3 Signal 2).

Covers the positive body predicate (only a genuine non-doctrine memory body is logged —
NOT an index, RETIRED.md, an archive original, a dot-file, and crucially NOT a doctrine
body — logging those is the inversion trap), the {path, ts, session} record grammar, and
fail-soft on malformed / non-dict / no-path payloads. No model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "scripts", "log-recall")


class LogRecall(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lr-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def w(self, rel, text):
        p = os.path.join(self.store, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def mem(self, rel, name, mtype="feedback", doctrine=False):
        d = "  doctrine: true\n" if doctrine else "  basis: observed\n"
        self.w(rel, f"---\nname: {name}\nmetadata:\n  type: {mtype}\n{d}---\nbody\n")

    def read(self, rel, session="s1"):
        payload = json.dumps({"tool_input": {"file_path": os.path.join(self.store, rel)}, "session_id": session})
        r = subprocess.run([sys.executable, LOG], input=payload,
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def logged(self):
        p = os.path.join(self.store, ".recall.log")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_feedback_body_logged(self):
        self.mem("feedback_a.md", "feedback-a")
        self.read("feedback_a.md")
        recs = self.logged()
        self.assertEqual([r["path"] for r in recs], ["feedback_a.md"])
        self.assertEqual(set(recs[0]), {"path", "ts", "session"})
        self.assertEqual(recs[0]["session"], "s1")

    def test_reference_body_logged(self):
        self.mem("reference_b.md", "reference-b", mtype="reference")
        self.read("reference_b.md")
        self.assertEqual([r["path"] for r in self.logged()], ["reference_b.md"])

    def test_doctrine_body_NOT_logged(self):
        # the inversion trap: distill/review read doctrine bodies by instruction every run
        self.mem("framework_gate.md", "framework-gate", mtype="framework", doctrine=True)
        self.read("framework_gate.md")
        self.assertEqual(self.logged(), [], "a doctrine body read was logged — the §2 inversion")

    def test_non_memory_paths_not_logged(self):
        self.w("MEMORY.md", "# idx\n")
        self.w("MEMORY.local.md", "# local\n")
        self.w("RETIRED.md", "# r\n")
        self.w("local/archive/old.md", "---\nname: old\n---\nb\n")
        self.w(".candidates.log", "x\n")
        for rel in ("MEMORY.md", "MEMORY.local.md", "RETIRED.md", "local/archive/old.md", ".candidates.log"):
            self.read(rel)
        self.assertEqual(self.logged(), [])

    def test_local_memory_logged(self):
        self.mem("local/pref.md", "pref-x")
        self.read("local/pref.md")
        self.assertEqual([r["path"] for r in self.logged()], [os.path.join("local", "pref.md")])

    def test_malformed_payload_silent(self):
        r = subprocess.run([sys.executable, LOG], input="not json",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.logged(), [])

    def test_non_dict_payload_silent(self):
        r = subprocess.run([sys.executable, LOG], input="42",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.logged(), [])

    def test_no_file_path_silent(self):
        r = subprocess.run([sys.executable, LOG], input=json.dumps({"session_id": "s"}),
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.logged(), [])

    def test_append_only_accumulates(self):
        self.mem("feedback_a.md", "feedback-a")
        self.read("feedback_a.md"); self.read("feedback_a.md")
        self.assertEqual(len(self.logged()), 2, "reads should accumulate, not overwrite")


if __name__ == "__main__":
    unittest.main()
