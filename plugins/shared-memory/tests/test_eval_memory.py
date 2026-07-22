#!/usr/bin/env python3
"""Tests for eval-memory — Layer 1: the post-write evaluation hook.

Covers the positive predicate (only an actual live memory file is linted — never an index,
RETIRED.md, an archive original, or a dot-file), the two mechanical checks (missing `basis`,
near-duplicate against the index), self-exclusion (a clean edit isn't a self-dup), the
intent-aware suppression (an antenna that `cites` its match and a consolidation framework
that `supersedes` its sources are silent — including block-list frontmatter), the two-channel
output on a conflict (systemMessage + hookSpecificOutput with hookEventName, and NEVER a
decision/updatedToolOutput), and fail-soft on a malformed payload. No model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.join(os.path.dirname(HERE), "scripts", "eval-memory")

# an existing memory the tests write duplicates / antennas / consolidations against
SEED_DESC = ("don't suppress stderr with 2>/dev/null — a silenced error makes a check "
             "report a false all-clear")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ev-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))
        self.mem("feedback_stderr.md", "feedback-no-silent-stderr", SEED_DESC)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def sp(self, rel):
        return os.path.join(self.store, rel)

    def write(self, rel, text):
        p = self.sp(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def append_index(self, index, title, link, desc):
        if not os.path.exists(self.sp(index)):
            self.write(index, "# Index\n")
        with open(self.sp(index), "a", encoding="utf-8") as f:
            f.write(f"- [{title}]({link})" + (f" — {desc}" if desc else "") + "\n")

    def mem(self, rel, name, desc, extra_fm="", basis="observed", index="MEMORY.md", write_index=True):
        b = f"  basis: {basis}\n" if basis else ""
        self.write(rel, f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: feedback\n{b}---\n{extra_fm}body\n"
                   if not extra_fm else
                   f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: feedback\n{b}{extra_fm}---\nbody\n")
        if write_index:
            self.append_index(index, name, ("local/" + os.path.basename(rel)) if index == "MEMORY.local.md" else rel, desc)

    def run_eval(self, rel):
        payload = json.dumps({"tool_input": {"file_path": self.sp(rel)}})
        r = subprocess.run([sys.executable, EVAL], input=payload,
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)  # always exit 0 — never blocks
        return json.loads(r.stdout) if r.stdout.strip() else None


class EvalMemory(Base):
    def test_clean_distinct_write_is_silent(self):
        self.mem("feedback_new.md", "feedback-branch-first",
                 "create a branch before committing on the default branch")
        self.assertIsNone(self.run_eval("feedback_new.md"))

    def test_near_duplicate_is_flagged_on_both_channels(self):
        # a second memory restating the seeded one, well below the self path
        self.mem("feedback_dup.md", "feedback-stderr-again", SEED_DESC, basis="verified")
        out = self.run_eval("feedback_dup.md")
        self.assertIsNotNone(out, "near-duplicate not flagged")
        self.assertIn("near-duplicate", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("near-duplicate", out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("decision", out)          # never gates the write
        self.assertNotIn("updatedToolOutput", out)

    def test_missing_basis_is_flagged(self):
        self.mem("feedback_nb.md", "feedback-unrelated",
                 "a completely unrelated lesson about widgets and gadgets entirely", basis="")
        out = self.run_eval("feedback_nb.md")
        self.assertIsNotNone(out)
        self.assertIn("basis", out["systemMessage"])

    def test_clean_edit_of_existing_is_silent(self):
        # the seeded memory itself — a clean edit must not flag as a duplicate of itself
        self.assertIsNone(self.run_eval("feedback_stderr.md"))

    def test_antenna_edit_not_flagged_as_self_dup(self):
        # [10]: editing memory X that is CITED BY an antenna A must NOT flag X as a near-dup of A —
        # the deliberate link is symmetric (skip when either cites/supersedes the other)
        self.mem("feedback_x.md", "feedback-x",
                 "the original lesson about verifying before concluding from primary evidence")
        self.mem("feedback_x_antenna.md", "feedback-x-antenna",
                 "the original lesson about verifying before concluding from primary evidence",
                 extra_fm="  cites: [feedback-x]\n")   # A cites X (X does not cite A)
        self.assertIsNone(self.run_eval("feedback_x.md"), "X flagged as a near-dup of its own antenna")

    def test_superseded_edit_not_flagged_as_self_dup(self):
        # [10] reverse-supersedes: editing X that is SUPERSEDED BY a consolidation B must not flag X
        # as a near-dup of B (mirrors the antenna/cites test; guards the `name in other_supersedes` branch)
        self.mem("feedback_x.md", "feedback-x",
                 "the original lesson about verifying before concluding from primary evidence")
        self.mem("framework_b.md", "framework-b",
                 "the original lesson about verifying before concluding from primary evidence",
                 extra_fm="  supersedes: [feedback-x]\n")   # B supersedes X (X does not supersede B)
        self.assertIsNone(self.run_eval("feedback_x.md"), "X flagged as a near-dup of its own consolidation")

    def test_bad_byte_in_written_file_is_soft(self):
        # M4: a non-UTF-8 byte in the just-written file must exit 0 (skip), not traceback
        with open(self.sp("feedback_bad.md"), "wb") as f:
            f.write(b"---\nname: feedback-bad\nmetadata:\n  type: feedback\n  basis: observed\n---\n\xff body\n")
        self.assertIsNone(self.run_eval("feedback_bad.md"))   # run_eval asserts rc 0

    def test_bad_byte_in_indexed_sibling_does_not_disable_eval(self):
        # M4: one corrupt indexed body must not crash eval for every OTHER write
        with open(self.sp("reference_corrupt.md"), "wb") as f:
            f.write(b"---\nname: reference-corrupt\n---\n\xff\n")
        self.append_index("MEMORY.md", "reference-corrupt", "reference_corrupt.md", "corrupt")
        self.mem("feedback_new.md", "feedback-branch-first",
                 "create a branch before committing on the default branch")
        self.assertIsNone(self.run_eval("feedback_new.md"))   # clean + distinct; corrupt sibling skipped, rc 0

    def test_antenna_citing_its_match_is_silent(self):
        # an antenna deliberately shares the trigger of the memory it cites
        self.mem("feedback_ant.md", "feedback-stderr-antenna", SEED_DESC,
                 extra_fm="cites: [feedback-no-silent-stderr]\n")
        self.assertIsNone(self.run_eval("feedback_ant.md"))

    def test_antenna_citing_via_block_list_is_silent(self):
        # cites authored as a YAML block list (depends on the block-list-aware parser)
        self.mem("feedback_ant2.md", "feedback-stderr-antenna2", SEED_DESC,
                 extra_fm="cites:\n  - feedback-no-silent-stderr\n")
        self.assertIsNone(self.run_eval("feedback_ant2.md"))

    def test_consolidation_superseding_its_source_is_silent(self):
        self.write("framework_fold.md",
                   "---\nname: framework-fold\ndescription: " + SEED_DESC + "\n"
                   "metadata:\n  type: framework\n  applies_when: running-a-check\n  basis: observed\n"
                   "supersedes: [feedback-no-silent-stderr]\n---\nbody\n")
        self.append_index("MEMORY.md", "framework-fold", "framework_fold.md", SEED_DESC)
        self.assertIsNone(self.run_eval("framework_fold.md"))

    def test_doctrine_body_is_near_dup_checked_and_basis_exempt(self):
        # [R9] D5: the deliberate cross-consumer asymmetry — log-recall SKIPS doctrine, eval-memory
        # INCLUDES it (near-dup checks doctrine + exempts it from the basis requirement). Only the
        # log-recall half was test-guarded; this pins the eval half, so a regression that makes eval
        # skip doctrine (symmetric with log-recall) can no longer pass the whole suite.
        self.write("framework_doc.md",
                   "---\nname: framework-doc\ndescription: " + SEED_DESC + "\n"
                   "metadata:\n  type: framework\n  doctrine: true\n---\nbody\n")   # no basis: doctrine is exempt
        out = self.run_eval("framework_doc.md")
        self.assertIsNotNone(out, "doctrine body was not near-dup checked (eval wrongly skipped doctrine)")
        self.assertIn("near-duplicate", out["systemMessage"])
        self.assertNotIn("basis", out["systemMessage"], "doctrine must be exempt from the missing-basis note")

    def test_non_memory_store_paths_are_silent(self):
        # the positive predicate: none of these is a memory to lint
        self.write("MEMORY.local.md", "# Local\n")
        self.write("RETIRED.md", "# Retired\n")
        self.write("local/archive/feedback_old.md", "---\nname: feedback-old\n---\nb\n")
        self.write(".candidates.log", "x\n")
        for rel in ("MEMORY.md", "MEMORY.local.md", "RETIRED.md",
                    "local/archive/feedback_old.md", ".candidates.log"):
            self.assertIsNone(self.run_eval(rel), f"{rel} was linted as a memory")

    def test_malformed_payload_is_silent_exit_zero(self):
        r = subprocess.run([sys.executable, EVAL], input="not json at all",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
