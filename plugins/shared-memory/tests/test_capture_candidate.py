#!/usr/bin/env python3
"""Tests for capture-candidate — Layer 1: the PostToolUse capture handler.

Feeds documented Write/Edit payload shapes on stdin against a throwaway HOME and
asserts the memory path is appended to .candidates.log; non-memory paths, MEMORY.md,
worktree dirs, and malformed payloads are silent no-ops (fail-soft, exit 0).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(os.path.dirname(HERE), "scripts", "capture-candidate")


class Capture(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cap-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.projects = os.path.join(self.home, ".claude", "projects")
        os.makedirs(self.projects)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_hook(self, payload):
        return subprocess.run([sys.executable, CAP], input=json.dumps(payload),
                              env=dict(os.environ, HOME=self.home), capture_output=True, text=True)

    def queue(self):
        p = os.path.join(self.store, ".candidates.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()

    def mempath(self, project, name):
        d = os.path.join(self.projects, project, "memory")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write("x")
        return p

    def test_write_payload_enqueues(self):
        p = self.mempath("projA", "feedback_a.md")
        r = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.queue(), [p])

    def test_edit_payload_enqueues(self):
        p = self.mempath("projA", "feedback_b.md")
        r = self.run_hook({"tool_name": "Edit",
                           "tool_input": {"file_path": p, "old_string": "a", "new_string": "b", "replace_all": False}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.queue(), [p])

    def test_memory_md_index_skipped(self):
        p = self.mempath("projA", "MEMORY.md")
        r = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.queue(), [])

    def test_non_memory_path_skipped(self):
        p = os.path.join(self.home, "some", "repo", "src.py")
        r = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.queue(), [])

    def test_worktree_dir_skipped(self):
        p = self.mempath("repo--claude-worktrees-feat", "feedback_w.md")
        r = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.queue(), [])

    def test_newline_in_project_path_is_not_enqueued(self):
        # [R9 W-5/D7]: MEMORY_RE matches a newline in the project segment ([^/]+ allows it), so an
        # embedded-newline path would forge a SECOND queue line without the guard. Mirror of
        # reflect-scan's test_path_with_newline_emits_no_forged_line. No FS write needed.
        p = os.path.join(self.projects, "projA\nEVIL", "memory", "x.md")
        r = self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.queue(), [], "an embedded-newline path was enqueued (could forge a queue line)")

    def test_malformed_payload_is_noop(self):
        r = subprocess.run([sys.executable, CAP], input="not json",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.queue(), [])

    def test_captured_path_round_trips_to_scan_as_file(self):
        # the appended path must classify as a `file` queue entry in distill-scan
        p = self.mempath("projA", "feedback_rt.md")
        self.run_hook({"tool_name": "Write", "tool_input": {"file_path": p, "content": "x"}})
        scan = os.path.join(os.path.dirname(HERE), "bin", "distill-scan")
        r = subprocess.run([sys.executable, scan], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        files = [e for e in out["queue"] if e["kind"] == "file"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["path"], p)
        self.assertEqual(files[0]["offsets"], [0])


if __name__ == "__main__":
    unittest.main()
