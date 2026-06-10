#!/usr/bin/env python3
"""Tests for distill-scan — Layer 1: the read-only mechanical front of /memory-distill.

Covers the summary ledger, the project-dir walk (MEMORY.md and worktree dirs skipped),
fail-soft frontmatter, frontmatter extraction, RETIRED.md exact-name flagging, and the
queue's file-vs-passthrough classification. Read-only; no model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(os.path.dirname(HERE), "bin", "distill-scan")

GOOD = (
    "---\n"
    "name: {name}\n"
    "description: {desc}\n"
    "metadata:\n"
    "  type: feedback\n"
    "  basis: observed\n"
    "---\n\n"
    "{body}\n"
)


class DistillScan(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ds-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.projects = os.path.join(self.home, ".claude", "projects")
        os.makedirs(self.store)
        os.makedirs(self.projects)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    # helpers
    def mem(self, project, name, content):
        path = os.path.join(self.projects, project, "memory", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def scan(self, *flags):
        r = subprocess.run([sys.executable, SCAN, *flags], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def by_name(self, out, name):
        return next((c for c in out["candidates"] if c["name"] == name), None)

    # tests
    def test_summary_walk_and_memory_md_skipped(self):
        self.mem("projA", "feedback_a.md", GOOD.format(name="feedback-a", desc="A", body="a"))
        self.mem("projA", "reference_b.md", GOOD.format(name="reference-b", desc="B", body="b"))
        self.mem("projA", "MEMORY.md", "# Index\n- [a](feedback_a.md) — A\n")  # must be skipped
        self.mem("projB", "feedback_c.md", GOOD.format(name="feedback-c", desc="C", body="c"))
        out = self.scan()
        self.assertEqual(out["summary"]["total_candidates"], len(out["candidates"]))
        self.assertEqual(out["summary"]["total_candidates"], 3)  # MEMORY.md not counted
        self.assertEqual(out["summary"]["by_project"], {"projA": 2, "projB": 1})
        self.assertIsNone(self.by_name(out, "MEMORY.md"))

    def test_worktree_dirs_skipped(self):
        self.mem("realproj", "feedback_a.md", GOOD.format(name="feedback-a", desc="A", body="a"))
        self.mem("repo--claude-worktrees-feat", "feedback_w.md",
                 GOOD.format(name="feedback-w", desc="W", body="w"))
        out = self.scan()
        self.assertEqual(out["summary"]["by_project"], {"realproj": 1})
        self.assertIsNone(self.by_name(out, "feedback-w"))

    def test_failsoft_parse_flag(self):
        self.mem("projA", "broken.md", "no frontmatter here, just prose\n")
        out = self.scan()
        cand = self.by_name(out, "broken.md")  # name falls back to basename
        self.assertIsNotNone(cand, "malformed file dropped instead of flagged")
        self.assertIn("parse_flag", cand)
        self.assertEqual(out["summary"]["parse_flags"], 1)

    def test_frontmatter_extracted(self):
        self.mem("projA", "feedback_good.md", GOOD.format(name="feedback-good", desc="A good lesson", body="x"))
        cand = self.by_name(self.scan(), "feedback-good")
        self.assertEqual(cand["description"], "A good lesson")
        self.assertEqual(cand["type"], "feedback")
        self.assertEqual(cand["basis"], "observed")
        self.assertNotIn("parse_flag", cand)

    def test_bad_byte_anywhere_does_not_crash_scan(self):
        # [17]: a non-UTF-8 byte in a project candidate / store body / RETIRED.md / queue must
        # degrade (flag or skip), never abort the whole scan
        def wb(path, data):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        self.mem("projA", "feedback_ok.md",
                 "---\nname: feedback-ok\nmetadata:\n  type: feedback\n  basis: observed\n---\nb\n")
        wb(os.path.join(self.projects, "projA", "memory", "feedback_bad.md"),
           b"---\nname: feedback-bad\n---\n\xff\n")                      # bad candidate body
        wb(os.path.join(self.store, "reference_storebad.md"), b"---\nname: x\n---\n\xff\n")  # promoted_set read
        wb(os.path.join(self.store, "RETIRED.md"), b"# Retired\n- \xff\n")                   # RETIRED read
        wb(os.path.join(self.store, ".candidates.log"), b"\xff not a path\n")                # queue read
        out = self.scan()   # asserts rc 0 — must not traceback
        self.assertIsNotNone(self.by_name(out, "feedback-ok"), "clean candidate lost")
        bad = self.by_name(out, "feedback-bad")
        self.assertTrue(bad is None or "error" in bad or "parse_flag" in bad, "bad candidate not flagged/skipped")

    def test_retired_name_match_flagged(self):
        self.write(os.path.join(self.store, "RETIRED.md"),
                   "# Retired\n- feedback-retired-thing 2026-01-01 superseded\n")
        self.mem("projA", "feedback_retired_thing.md",
                 GOOD.format(name="feedback-retired-thing", desc="old", body="x"))
        self.mem("projA", "feedback_live.md", GOOD.format(name="feedback-live", desc="new", body="y"))
        out = self.scan()
        self.assertTrue(self.by_name(out, "feedback-retired-thing").get("retired_name_match"))
        self.assertNotIn("retired_name_match", self.by_name(out, "feedback-live"))
        self.assertIn("feedback-retired-thing", out["retired_ledger"])

    def test_queue_three_kinds_and_offsets(self):
        path = self.mem("projA", "feedback_a.md", GOOD.format(name="feedback-a", desc="A", body="a"))
        pointer = "reflect-pointer:/Users/me/.claude/projects/-repo/abc.jsonl:42:concession"
        stale = "/gone/nowhere/dead.md"
        self.write(os.path.join(self.store, ".candidates.log"), path + "\n" + pointer + "\n" + stale + "\n")
        out = self.scan()
        self.assertEqual(out["summary"]["queue_entries"], 3)
        by_kind = {e["kind"]: e for e in out["queue"]}
        self.assertEqual(set(by_kind), {"file", "passthrough", "stale"})
        self.assertEqual(by_kind["file"]["offsets"], [0])
        self.assertEqual(by_kind["passthrough"]["entry"], pointer)
        self.assertEqual(by_kind["passthrough"]["offset"], 1)
        self.assertEqual(by_kind["stale"]["entry"], stale)
        self.assertEqual(by_kind["stale"]["offset"], 2)

    def test_queue_consumed_offsets_filtered(self):
        p1 = self.mem("projA", "feedback_a.md", GOOD.format(name="feedback-a", desc="A", body="a"))
        p2 = self.mem("projA", "feedback_b.md", GOOD.format(name="feedback-b", desc="B", body="b"))
        self.write(os.path.join(self.store, ".candidates.log"), p1 + "\n" + p2 + "\n")
        self.write(os.path.join(self.store, ".candidates.consumed"), "0\n")  # offset 0 (p1) consumed
        out = self.scan()
        self.assertEqual(out["summary"]["queue_entries"], 1)
        self.assertEqual(out["queue"][0]["path"], p2)
        self.assertEqual(out["queue"][0]["offsets"], [1])

    def test_queue_dedup_carries_all_offsets(self):
        # a path re-captured after a consume re-appears at a new offset; a de-duped file
        # entry carries every live offset that collapsed into it
        p = self.mem("projA", "feedback_a.md", GOOD.format(name="feedback-a", desc="A", body="a"))
        self.write(os.path.join(self.store, ".candidates.log"), p + "\n" + p + "\n")
        out = self.scan()
        self.assertEqual(out["summary"]["queue_entries"], 1)
        self.assertEqual(out["queue"][0]["offsets"], [0, 1])

    def test_queue_only_ingests_files_and_skips_glob(self):
        queued = self.mem("projA", "feedback_q.md", GOOD.format(name="feedback-q", desc="Q", body="q"))
        self.mem("projA", "feedback_glob.md", GOOD.format(name="feedback-glob", desc="G", body="g"))  # glob-only
        self.write(os.path.join(self.store, ".candidates.log"), queued + "\n")
        out = self.scan("--queue-only")
        names = {c["name"] for c in out["candidates"]}
        self.assertIn("feedback-q", names)        # queued file ingested as a candidate
        self.assertNotIn("feedback-glob", names)  # glob skipped under --queue-only

    def test_similarity_hint_on_near_duplicates(self):
        self.mem("projA", "feedback_v1.md",
                 GOOD.format(name="feedback-verify-claims", desc="Verify claims before acting on them", body="x"))
        self.mem("projB", "feedback_v2.md",
                 GOOD.format(name="feedback-verify-claims-too", desc="Verify claims before acting on them", body="y"))
        out = self.scan()
        self.assertTrue(any("similar" in c for c in out["candidates"]),
                        "near-duplicate pair produced no similarity hint")


if __name__ == "__main__":
    unittest.main()
