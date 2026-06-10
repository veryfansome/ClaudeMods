#!/usr/bin/env python3
"""Tests for review-apply — Layer 1: the mechanical back of /memory-review.

Covers retire (archive → de-index → delete → RETIRED.md), per-source CAS, the crash-safe
ordering + full-ledger stamp, consolidation (framework-hash CAS + supersedes binding),
the guard (resolve-then-verify under the store, doctrine invariant, archive-dest
confinement), and never-writes-beliefs. No model calls.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APPLY = os.path.join(os.path.dirname(HERE), "bin", "review-apply")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ra-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def sp(self, rel):
        return os.path.join(self.store, rel)

    def write(self, rel, text):
        p = self.sp(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return text

    def read(self, rel):
        with open(self.sp(rel)) as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(self.sp(rel))

    def ra(self, plan, *flags):
        pp = os.path.join(self.home, "plan.json")
        with open(pp, "w") as f:
            json.dump(plan, f)
        r = subprocess.run([sys.executable, APPLY, *flags, pp],
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        return r.returncode, out, r.stderr

    def store_mem(self, fname, name, desc="a lesson", body="the body\n", doctrine=False):
        d = "  doctrine: true\n" if doctrine else ""
        content = (f"---\nname: {name}\ndescription: {desc}\n"
                   f"metadata:\n  type: feedback\n{d}  basis: observed\n---\n{body}")
        return self.write(fname, content)


class Validate(Base):
    def v(self, plan):
        return self.ra(plan, "--validate")

    def retire_op(self, **kw):
        base = {"name": "feedback-x", "path": "feedback_x.md", "sha256": "h",
                "reason": "r", "archive_dest": "local/archive/feedback_x.md", "remove_index_lines": []}
        base.update(kw)
        return base

    def test_retire_doctrine_file_rejected(self):
        self.store_mem("framework_gate.md", "framework-gate", doctrine=True)
        c = self.read("framework_gate.md")
        rc, out, _ = self.v({"version": 1, "retire": [self.retire_op(
            name="framework-gate", path="framework_gate.md", sha256=sha(c),
            archive_dest="local/archive/framework_gate.md")]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("cannot retire a doctrine file" in e for e in out["errors"]), out["errors"])

    def test_retire_path_escape_rejected(self):
        rc, out, _ = self.v({"version": 1, "retire": [self.retire_op(path="../../../etc/x.md")]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("resolve under the store" in e for e in out["errors"]), out["errors"])

    def test_archive_dest_traversal_rejected(self):
        rc, out, _ = self.v({"version": 1, "retire": [self.retire_op(
            archive_dest="local/archive/../../escape.md")]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("archive_dest must resolve under local/archive/" in e for e in out["errors"]), out["errors"])

    def test_remove_doctrine_index_line_rejected(self):
        self.store_mem("framework_gate.md", "framework-gate", doctrine=True)
        rc, out, _ = self.v({"version": 1, "retire": [self.retire_op(remove_index_lines=[
            {"index": "MEMORY.md", "lines": ["- [Gate](framework_gate.md) — the gate"]}])]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("doctrine memory's index line" in e for e in out["errors"]), out["errors"])


class Apply(Base):
    def test_retire_skips_delete_when_flat_index_op_omitted(self):
        # [15]: a plan that FORGOT to de-index a flat memory (remove_index_lines omits its MEMORY.md
        # line) must NOT delete the body — the authoritative flat-index scan finds the dangling line
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.write("MEMORY.md", "# Index\n- [Old](feedback_old.md) — a lesson\n")
        rc, out, err = self.ra({"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha(c),
            "reason": "r", "archive_dest": "local/archive/feedback_old.md",
            "remove_index_lines": []}]})   # omits the MEMORY.md op — the bug
        self.assertTrue(self.exists("feedback_old.md"), "body deleted despite a dangling flat-index line")
        self.assertTrue(any("still references" in s.get("reason", "") for s in out.get("skipped", [])), out)

    def test_retire_domain_memory_proceeds_with_no_flat_line(self):
        # [15]: a domain memory has NO flat line (only a generated projection); its retire de-indexes
        # nothing and the delete must still PROCEED — the projection index must NOT block it
        c = self.store_mem("reference_dom.md", "reference-dom")
        self.write("MEMORY.md", "# Index\n")                      # no flat line for it
        self.write("MEMORY.gcp.md", "# Domain index\n- [Dom](reference_dom.md) — a lesson\n")  # projection only
        rc, out, err = self.ra({"version": 1, "retire": [{
            "name": "reference-dom", "path": "reference_dom.md", "sha256": sha(c),
            "reason": "r", "archive_dest": "local/archive/reference_dom.md",
            "remove_index_lines": []}]})
        self.assertFalse(self.exists("reference_dom.md"), "domain retire wrongly blocked by its projection line")
        self.assertEqual(rc, 0, out)

    def test_retire_archives_deindexes_deletes_ledgers(self):
        c = self.store_mem("feedback_old.md", "feedback-old")
        line = "- [Old](feedback_old.md) — a lesson"
        self.write("MEMORY.md", "# Index\n" + line + "\n- [Keep](feedback_keep.md) — keep\n")
        rc, out, err = self.ra({"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha(c),
            "remove_index_lines": [{"index": "MEMORY.md", "lines": [line]}],
            "reason": "obsolete", "archive_dest": "local/archive/feedback_old.md"}]})
        self.assertEqual(rc, 0, err or out)
        self.assertFalse(self.exists("feedback_old.md"), "source not deleted")
        arch = self.read("local/archive/feedback_old.md")
        self.assertIn("retired_from: feedback-old", arch)
        self.assertIn("reason: obsolete", arch)
        self.assertIn("retired_on:", arch)
        self.assertIn("the body", arch)  # original body preserved verbatim
        idx = self.read("MEMORY.md")
        self.assertNotIn(line, idx)
        self.assertIn("- [Keep](feedback_keep.md) — keep", idx)  # other lines untouched
        retired = self.read("RETIRED.md")
        self.assertTrue(retired.startswith("- feedback-old · "), retired)
        self.assertIn("· obsolete", retired)

    def test_stale_source_skips_preserves(self):
        self.store_mem("feedback_old.md", "feedback-old")
        rc, out, _ = self.ra({"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha("STALE"),
            "remove_index_lines": [], "reason": "x", "archive_dest": "local/archive/feedback_old.md"}]})
        self.assertEqual(rc, 3, out)
        self.assertTrue(self.exists("feedback_old.md"), "deleted despite stale hash")
        self.assertFalse(self.exists("local/archive/feedback_old.md"))
        self.assertTrue(out["skipped"])

    def test_consolidate_retires_sources_under_framework(self):
        # the framework already exists (the skill wrote it via file tools); review-apply only retires
        fw = ("---\nname: framework-fold\ndescription: folds them\n"
              "metadata:\n  type: framework\n  applies_when: x\n  basis: observed\n"
              "  supersedes: [feedback-a, feedback-b]\n---\nframework body\n")
        self.write("framework_fold.md", fw)
        ca = self.store_mem("feedback_a.md", "feedback-a")
        cb = self.store_mem("feedback_b.md", "feedback-b")
        la, lb = "- [A](feedback_a.md) — a", "- [B](feedback_b.md) — b"
        self.write("MEMORY.md", "# Index\n" + la + "\n" + lb + "\n- [Fold](framework_fold.md) — folds them\n")
        rc, out, err = self.ra({"version": 1, "consolidate": [{
            "framework": "framework_fold.md", "framework_sha256": sha(fw),
            "supersedes": ["feedback-a", "feedback-b"],
            "sources": [
                {"name": "feedback-a", "path": "feedback_a.md", "sha256": sha(ca),
                 "remove_index_lines": [{"index": "MEMORY.md", "lines": [la]}],
                 "archive_dest": "local/archive/feedback_a.md"},
                {"name": "feedback-b", "path": "feedback_b.md", "sha256": sha(cb),
                 "remove_index_lines": [{"index": "MEMORY.md", "lines": [lb]}],
                 "archive_dest": "local/archive/feedback_b.md"}]}]})
        self.assertEqual(rc, 0, err or out)
        self.assertEqual(self.read("framework_fold.md"), fw, "review-apply altered the framework belief")
        self.assertFalse(self.exists("feedback_a.md"))
        self.assertFalse(self.exists("feedback_b.md"))
        idx = self.read("MEMORY.md")  # BOTH lines dropped from the shared index (batched de-index)
        self.assertNotIn(la, idx)
        self.assertNotIn(lb, idx)
        self.assertIn("- [Fold](framework_fold.md) — folds them", idx)  # the framework's own line stays
        retired = self.read("RETIRED.md")
        self.assertEqual(retired.count("superseded by framework-fold"), 2, retired)
        self.assertIn("retired_from: feedback-a", self.read("local/archive/feedback_a.md"))
        self.assertIn("superseded_by: framework-fold", self.read("local/archive/feedback_a.md"))

    def test_consolidate_framework_hash_mismatch_aborts(self):
        fw = ("---\nname: framework-fold\ndescription: d\nmetadata:\n  type: framework\n"
              "  applies_when: x\n  basis: observed\n  supersedes: [feedback-a]\n---\nbody\n")
        self.write("framework_fold.md", fw)
        ca = self.store_mem("feedback_a.md", "feedback-a")
        rc, out, _ = self.ra({"version": 1, "consolidate": [{
            "framework": "framework_fold.md", "framework_sha256": sha("STALE-DIFFERENT"),
            "supersedes": ["feedback-a"],
            "sources": [{"name": "feedback-a", "path": "feedback_a.md", "sha256": sha(ca),
                         "remove_index_lines": [], "archive_dest": "local/archive/feedback_a.md"}]}]})
        self.assertEqual(rc, 3, out)
        self.assertTrue(self.exists("feedback_a.md"), "source retired despite framework hash mismatch")
        self.assertTrue(any("framework" in str(s.get("reason", "")) for s in out["skipped"]), out["skipped"])

    def test_consolidate_source_not_in_supersedes_skipped(self):
        fw = ("---\nname: framework-fold\ndescription: d\nmetadata:\n  type: framework\n"
              "  applies_when: x\n  basis: observed\n  supersedes: [feedback-a]\n---\nbody\n")
        self.write("framework_fold.md", fw)
        ca = self.store_mem("feedback_a.md", "feedback-a")
        cx = self.store_mem("feedback_x.md", "feedback-x")  # NOT in the framework's supersedes
        rc, out, _ = self.ra({"version": 1, "consolidate": [{
            "framework": "framework_fold.md", "framework_sha256": sha(fw),
            "supersedes": ["feedback-a", "feedback-x"],
            "sources": [
                {"name": "feedback-a", "path": "feedback_a.md", "sha256": sha(ca),
                 "remove_index_lines": [], "archive_dest": "local/archive/feedback_a.md"},
                {"name": "feedback-x", "path": "feedback_x.md", "sha256": sha(cx),
                 "remove_index_lines": [], "archive_dest": "local/archive/feedback_x.md"}]}]})
        self.assertEqual(rc, 3, out)
        self.assertFalse(self.exists("feedback_a.md"), "named source not retired")
        self.assertTrue(self.exists("feedback_x.md"), "unlisted source retired anyway")

    def test_archive_no_clobber_refuses_different(self):
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.write("local/archive/feedback_old.md", "a DIFFERENT prior archive\n")  # collision
        rc, out, _ = self.ra({"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha(c),
            "remove_index_lines": [], "reason": "r", "archive_dest": "local/archive/feedback_old.md"}]})
        self.assertEqual(rc, 3, out)
        self.assertTrue(self.exists("feedback_old.md"), "source deleted despite archive collision")
        self.assertEqual(self.read("local/archive/feedback_old.md"), "a DIFFERENT prior archive\n")

    def test_lockfile_blocks(self):
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.write(".distill.lock", "")
        rc, out, _ = self.ra({"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha(c),
            "remove_index_lines": [], "reason": "r", "archive_dest": "local/archive/feedback_old.md"}]})
        self.assertEqual(rc, 2, out)
        self.assertIn("lockfile", out.get("error", ""))
        self.assertTrue(self.exists("feedback_old.md"), "acted despite a held lock")


class CrashRecovery(Base):
    """The archive is the commit point: a retire killed at any boundary, recovered on any
    later day, completes on replay by reading the ledger payload back from the stamp."""

    LINE = "- [Old](feedback_old.md) — a lesson"

    def stamped_archive(self, content, date="2026-07-10", reason="obsolete"):
        self.write("local/archive/feedback_old.md",
                   f"---\n  retired_from: feedback-old\n  retired_on: {date}\n  reason: {reason}\n---\n"
                   + content)

    def retire_plan(self, c, ops=None):
        return {"version": 1, "retire": [{
            "name": "feedback-old", "path": "feedback_old.md", "sha256": sha(c),
            "remove_index_lines": ops if ops is not None else [{"index": "MEMORY.md", "lines": [self.LINE]}],
            "reason": "obsolete", "archive_dest": "local/archive/feedback_old.md"}]}

    def test_pre_ledger_recovery_completes_from_stamp(self):
        # killed after archive+de-index+delete, before RETIRED.md: source gone, index cleared
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.stamped_archive(c)
        os.remove(self.sp("feedback_old.md"))
        self.write("MEMORY.md", "# Index\n")
        rc, out, err = self.ra(self.retire_plan(c))
        self.assertEqual(rc, 0, err or out)
        self.assertIn("- feedback-old · 2026-07-10 · obsolete", self.read("RETIRED.md"))  # stamp's date

    def test_cross_day_pre_delete_recovery_uses_stamped_date(self):
        # killed after archive, before delete, recovered a later day: stamp's date wins, so
        # the no-clobber archive doesn't refuse and the retire completes
        c = self.store_mem("feedback_old.md", "feedback-old")  # source still present
        self.stamped_archive(c)
        self.write("MEMORY.md", "# Index\n")                    # already de-indexed
        rc, out, err = self.ra(self.retire_plan(c))
        self.assertEqual(rc, 0, err or out)
        self.assertFalse(self.exists("feedback_old.md"), "source not deleted on cross-day recovery")
        self.assertIn("- feedback-old · 2026-07-10 · obsolete", self.read("RETIRED.md"))

    def test_replay_of_completed_retire_is_idempotent(self):
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.write("MEMORY.md", "# Index\n" + self.LINE + "\n")
        rc1, _, _ = self.ra(self.retire_plan(c))
        rc2, out2, _ = self.ra(self.retire_plan(c))  # replay the same plan
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0, out2)  # idempotent success, not a partial-failure
        self.assertEqual(self.read("RETIRED.md").count("- feedback-old · "), 1, "ledger doubled on replay")

    def test_recovery_refuses_recreated_source(self):
        # a NEW same-name file now sits at the path; a stale replay must not delete it
        c = self.store_mem("feedback_old.md", "feedback-old", body="ORIGINAL\n")
        self.stamped_archive(c)
        self.write("feedback_old.md",
                   "---\nname: feedback-old\ndescription: d\nmetadata:\n  type: feedback\n  basis: observed\n---\nRECREATED\n")
        self.write("MEMORY.md", "# Index\n")
        rc, out, _ = self.ra(self.retire_plan(c, ops=[]))  # OLD sha in the plan
        self.assertEqual(rc, 3, out)
        self.assertTrue(self.exists("feedback_old.md"), "recreated source deleted on stale replay")
        self.assertIn("RECREATED", self.read("feedback_old.md"))

    def test_deindex_mismatch_does_not_delete_body(self):
        # a plan line that doesn't byte-match the on-disk line de-indexes nothing; deleting
        # the body would strand the still-loading line, so the retire must refuse
        c = self.store_mem("feedback_old.md", "feedback-old")
        self.write("MEMORY.md", "# Index\n  " + self.LINE + "\n")  # leading whitespace on disk
        rc, out, _ = self.ra(self.retire_plan(c))                  # plan carries the stripped line
        self.assertEqual(rc, 3, out)
        self.assertTrue(self.exists("feedback_old.md"), "body deleted while its index line still stands")
        self.assertIn("feedback_old.md", self.read("MEMORY.md"))
        self.assertTrue(any("still references" in str(s.get("reason", "")) for s in out["skipped"]), out["skipped"])

    def test_consolidate_block_list_supersedes_binds(self):
        # supersedes authored as a YAML block list (not inline [a, b]) must still bind the retire
        fw = ("---\nname: framework-fold\ndescription: folds\n"
              "metadata:\n  type: framework\n  applies_when: x\n  basis: observed\n"
              "supersedes:\n  - feedback-a\n  - feedback-b\n---\nframework body\n")
        self.write("framework_fold.md", fw)
        ca = self.store_mem("feedback_a.md", "feedback-a")
        la = "- [A](feedback_a.md) — a lesson"
        self.write("MEMORY.md", "# Index\n" + la + "\n")
        rc, out, err = self.ra({"version": 1, "consolidate": [{
            "framework": "framework_fold.md", "framework_sha256": sha(fw),
            "supersedes": ["feedback-a", "feedback-b"],
            "sources": [{"name": "feedback-a", "path": "feedback_a.md", "sha256": sha(ca),
                         "remove_index_lines": [{"index": "MEMORY.md", "lines": [la]}],
                         "archive_dest": "local/archive/feedback_a.md"}]}]})
        self.assertEqual(rc, 0, err or out)
        self.assertFalse(self.exists("feedback_a.md"), "block-list supersedes didn't bind the retire")
        self.assertIn("superseded_by: framework-fold", self.read("local/archive/feedback_a.md"))


if __name__ == "__main__":
    unittest.main()
