#!/usr/bin/env python3
"""Tests for distill-scan's recall_miss flag — Layer 1 (M3 Signal 1).

recall_miss = an already-promoted memory re-appearing on the queue ⇒ its index line didn't
fire. Covers: a queued re-capture matching a live-store memory flags; a queued re-capture
matching an ARCHIVED original flags (solo); a glob-only match does NOT flag (queue-path-only —
the false-positive M8 fix); a consumed offset does NOT flag; a below-floor near-miss does NOT
flag; and RETIRED.md is NOT in the promoted set (a retired re-capture is retired_name_match,
not recall_miss). Emits enrich/investigate, never retire. No model calls.
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
DESC = "don't suppress stderr with 2>/dev/null — a silenced error makes a check report a false all-clear"


class RecallMiss(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rm-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.mem = os.path.join(self.home, ".claude", "projects", "-proj", "memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))
        os.makedirs(self.mem)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def w(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def body(self, name, desc):
        return f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: feedback\n  basis: observed\n---\nb\n"

    def project_mem(self, fname, name, desc):
        p = os.path.join(self.mem, fname)
        self.w(p, self.body(name, desc))
        return p

    def queue(self, *paths):
        self.w(os.path.join(self.store, ".candidates.log"), "".join(p + "\n" for p in paths))

    def consume(self, *offsets):
        self.w(os.path.join(self.store, ".candidates.consumed"), "".join(f"{o}\n" for o in offsets))

    def scan(self):
        r = subprocess.run([sys.executable, SCAN], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        return {c["path"]: c for c in out["candidates"]}

    def test_queued_recapture_of_live_memory_flags(self):
        self.w(os.path.join(self.store, "feedback_stderr.md"), self.body("feedback-no-silent-stderr", DESC))
        self.w(os.path.join(self.store, "MEMORY.md"), f"# idx\n- [s](feedback_stderr.md) — {DESC}\n")
        p = self.project_mem("feedback_relearn.md", "feedback-no-silent-stderr", DESC)
        self.queue(p)
        c = self.scan()[p]
        self.assertIn("recall_miss", c)
        self.assertEqual(c["recall_miss"]["matched"], "feedback-no-silent-stderr")

    def test_queued_recapture_of_archived_original_flags(self):
        # solo: the promoted original lives only in local/archive/ (source was deleted). A
        # PROMOTION archive carries `promoted_to` (distill-apply's stamp), NOT `retired_from`
        # — so it IS in the promoted set and a re-capture flags. (A retired archive carries
        # retired_from and is excluded — see the retired tests below.)
        self.w(os.path.join(self.store, "local", "archive", "feedback_stderr.md"),
               "---\nname: feedback-no-silent-stderr\ndescription: " + DESC +
               "\n  promoted_to: feedback-no-silent-stderr\n---\nb\n")
        self.w(os.path.join(self.store, "MEMORY.md"), "# idx\n")
        p = self.project_mem("feedback_relearn.md", "feedback-no-silent-stderr", DESC)
        self.queue(p)
        self.assertIn("recall_miss", self.scan()[p])

    def test_glob_only_match_does_not_flag(self):
        # the M8 false-positive: a matching candidate with NO live queue occurrence
        self.w(os.path.join(self.store, "feedback_stderr.md"), self.body("feedback-no-silent-stderr", DESC))
        self.w(os.path.join(self.store, "MEMORY.md"), f"# idx\n- [s](feedback_stderr.md) — {DESC}\n")
        p = self.project_mem("feedback_globonly.md", "feedback-no-silent-stderr", DESC)
        # no queue → candidate comes from the glob only
        self.assertNotIn("recall_miss", self.scan()[p])

    def test_consumed_offset_does_not_flag(self):
        self.w(os.path.join(self.store, "feedback_stderr.md"), self.body("feedback-no-silent-stderr", DESC))
        self.w(os.path.join(self.store, "MEMORY.md"), f"# idx\n- [s](feedback_stderr.md) — {DESC}\n")
        p = self.project_mem("feedback_relearn.md", "feedback-no-silent-stderr", DESC)
        self.queue(p)
        self.consume(0)  # the occurrence is already consumed → not live
        self.assertNotIn("recall_miss", self.scan()[p])

    def test_below_floor_does_not_flag(self):
        self.w(os.path.join(self.store, "feedback_stderr.md"), self.body("feedback-no-silent-stderr", DESC))
        self.w(os.path.join(self.store, "MEMORY.md"), f"# idx\n- [s](feedback_stderr.md) — {DESC}\n")
        p = self.project_mem("feedback_unrelated.md", "feedback-branch-first",
                             "create a branch before committing on the default branch")
        self.queue(p)
        self.assertNotIn("recall_miss", self.scan()[p])

    def retired_archive(self, name):
        # retirement writes the original body (stamped retired_from) into local/archive/ and
        # never removes it — this is the file that must NOT be in the promoted set
        self.w(os.path.join(self.store, "local", "archive", "feedback_stderr.md"),
               f"---\nname: {name}\ndescription: {DESC}\n  retired_from: {name}\n  retired_on: 2026-07-01\n---\nb\n")
        self.w(os.path.join(self.store, "RETIRED.md"),
               f"# Retired\n- {name} · 2026-07-01 · obsolete\n")
        self.w(os.path.join(self.store, "MEMORY.md"), "# idx\n")

    def test_retired_recapture_same_name_is_retired_match_not_recall_miss(self):
        # a retired memory has no index line — a same-name re-capture is retired_name_match only.
        # The retirement archive is present, so this exercises promoted_set's retired_from skip
        # AND the retired_names gate.
        self.retired_archive("feedback-no-silent-stderr")
        p = self.project_mem("feedback_relearn.md", "feedback-no-silent-stderr", DESC)
        self.queue(p)
        c = self.scan()[p]
        self.assertTrue(c.get("retired_name_match"))
        self.assertNotIn("recall_miss", c)

    def test_retired_recapture_different_name_still_not_recall_miss(self):
        # cross-project re-capture under a DIFFERENT name: the retired_names gate misses it
        # (name differs), so ONLY promoted_set's retired_from exclusion prevents matching the
        # retirement archive by description — the load-bearing fix.
        self.retired_archive("feedback-no-silent-stderr")
        p = self.project_mem("feedback_stderr_variant.md", "feedback-stderr-in-checks", DESC)
        self.queue(p)
        c = self.scan()[p]
        self.assertNotIn("recall_miss", c, "matched a retirement archive by description")


if __name__ == "__main__":
    unittest.main()
