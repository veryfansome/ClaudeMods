#!/usr/bin/env python3
"""Tests for distill-apply — Layer 1: the mechanical back of /memory-distill.

Covers validation (schema, content, doctrine defense) and apply (compare-and-swap,
archive-before-delete, lockfile, never-writes-beliefs, exit codes 0/2/3). No model
calls. Each run executes distill-apply as a subprocess against a throwaway HOME with
a fabricated store and project-memory dir.
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
APPLY = os.path.join(os.path.dirname(HERE), "bin", "distill-apply")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


VALID_BELIEF = (
    "---\n"
    "name: feedback-x-lesson\n"
    "description: When X happens, do Y\n"
    "metadata:\n"
    "  type: feedback\n"
    "  domains: []\n"
    "  basis: observed\n"
    "  last_verified: 2026-06-15\n"
    "---\n\n"
    "When X happens, do Y.\n"
)


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="da-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.proj = os.path.join(self.home, ".claude", "projects", "proj", "memory")  # a project-memory root (the allowlist)
        os.makedirs(os.path.join(self.store, "local", "archive"))
        os.makedirs(self.proj)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def sp(self, rel):  # store-relative absolute path
        return os.path.join(self.store, rel)

    def da(self, plan, *flags):
        plan_path = os.path.join(self.home, "plan.json")
        with open(plan_path, "w") as f:
            json.dump(plan, f)
        r = subprocess.run([sys.executable, APPLY, *flags, plan_path],
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        return r.returncode, out, r.stderr

    def good_belief(self, target_rel="feedback_x_lesson.md", index="MEMORY.md", content=VALID_BELIEF):
        return {"target": self.sp(target_rel), "index": index,
                "index_line": f"- [X]({target_rel}) — When X happens, do Y", "content": content}


class Validate(Base):
    def v(self, plan):
        return self.da(plan, "--validate")

    def test_belief_target_traversal_rejected(self):
        # [5]: a `../` traversal target must be rejected (resolve-then-verify, not raw startswith)
        b = self.good_belief()
        b["target"] = os.path.join(self.sp(""), "..", "evil.md")
        rc, out, _ = self.v({"version": 1, "beliefs": [b]})
        self.assertEqual(rc, 1, out)
        self.assertTrue(any("under the store" in e for e in out.get("errors", [])), out)

    def test_valid_plan_passes(self):
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig\n")
        rc, out, _ = self.v({
            "version": 1,
            "beliefs": [self.good_belief()],
            "archives": [{"source": src, "source_sha256": sha("orig\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("orig\n")}],
            "queue_remove": [0],
        })
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["valid"], out.get("errors"))

    def test_target_declaring_doctrine_rejected(self):
        tgt = self.sp("framework_memory_gate.md")
        self.write(tgt, "---\nname: framework-memory-gate\ndoctrine: true\n"
                        "metadata:\n  type: framework\n  basis: verified\n---\nx\n")
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief("framework_memory_gate.md")]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("out of distill's scope" in e for e in out["errors"]), out["errors"])

    def test_content_minting_doctrine_rejected(self):
        c = VALID_BELIEF.replace("  domains: []\n", "  domains: []\n  doctrine: true\n")
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief(content=c)]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("mint doctrine" in e for e in out["errors"]), out["errors"])

    def test_verified_requires_section(self):
        c = VALID_BELIEF.replace("basis: observed", "basis: verified")
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief(content=c)]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("Verified" in e for e in out["errors"]), out["errors"])

    def test_name_must_mirror_filename(self):
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief("feedback_other_name.md")]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("mirror" in e for e in out["errors"]), out["errors"])

    def test_last_verified_na_rejected_in_plan(self):
        c = VALID_BELIEF.replace("last_verified: 2026-06-15", "last_verified: n/a")
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief(content=c)]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("n/a" in e for e in out["errors"]), out["errors"])

    def test_domain_tagged_belief_needs_no_index_line(self):
        # M4: a domain-tagged belief carries no flat index line (the generator renders its
        # projection); it needs `title` and must OMIT index/index_line.
        c = ("---\nname: feedback-x-lesson\ntitle: X Lesson\ndescription: When X happens, do Y\n"
             "metadata:\n  type: feedback\n  domains: [gcp]\n  basis: observed\n  last_verified: 2026-06-15\n---\n\nbody\n")
        rc, out, _ = self.v({"version": 1, "beliefs": [
            {"target": self.sp("feedback_x_lesson.md"), "content": c}]})  # no index/index_line
        self.assertEqual(rc, 0, out.get("errors"))
        self.assertTrue(out["valid"], out.get("errors"))

    def test_domain_tagged_belief_with_index_line_rejected(self):
        c = ("---\nname: feedback-x-lesson\ntitle: X\ndescription: d\n"
             "metadata:\n  type: feedback\n  domains: [gcp]\n  basis: observed\n  last_verified: 2026-06-15\n---\n\nb\n")
        rc, out, _ = self.v({"version": 1, "beliefs": [self.good_belief(content=c)]})  # good_belief adds index_line
        self.assertEqual(rc, 1)
        self.assertTrue(any("drop index" in e for e in out["errors"]), out["errors"])

    def test_domain_tagged_belief_without_title_rejected(self):
        c = ("---\nname: feedback-x-lesson\ndescription: d\n"
             "metadata:\n  type: feedback\n  domains: [gcp]\n  basis: observed\n  last_verified: 2026-06-15\n---\n\nb\n")
        rc, out, _ = self.v({"version": 1, "beliefs": [
            {"target": self.sp("feedback_x_lesson.md"), "content": c}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("title" in e for e in out["errors"]), out["errors"])

    def test_source_op_cannot_touch_store(self):
        # a store path is not under any project-memory root, so the allowlist rejects it
        rc, out, _ = self.v({"version": 1, "source_ops": [
            {"op": "delete", "path": self.sp("feedback_x_lesson.md"), "sha256": "x"}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("not under an allowed" in e for e in out["errors"]), out["errors"])

    DOCTRINE = ("---\nname: framework-gate\ndoctrine: true\n"
                "metadata:\n  type: framework\n  basis: verified\n---\nx\n")

    def test_delete_doctrine_file_rejected(self):
        doc = os.path.join(self.proj, "framework_gate.md")
        self.write(doc, self.DOCTRINE)
        rc, out, _ = self.v({"version": 1, "source_ops": [
            {"op": "delete", "path": doc, "sha256": sha(self.DOCTRINE)}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("doctrine" in e for e in out["errors"]), out["errors"])

    def test_remove_doctrine_index_line_rejected(self):
        doc = os.path.join(self.proj, "framework_gate.md")
        self.write(doc, self.DOCTRINE)
        idx = os.path.join(self.proj, "MEMORY.md")
        self.write(idx, "# Index\n- [Gate](framework_gate.md) — the gate\n")
        rc, out, _ = self.v({"version": 1, "source_ops": [
            {"op": "remove_index_lines", "path": idx, "sha256": sha(self.read(idx)),
             "lines": ["- [Gate](framework_gate.md) — the gate"]}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("doctrine memory's index line" in e for e in out["errors"]), out["errors"])

    def test_source_op_traversal_escape_rejected(self):
        # a ../ escape from a project root resolves outside it → rejected (resolve-then-verify)
        escape = os.path.join(self.proj, "..", "..", "..", "..", "etc", "x.md")
        rc, out, _ = self.v({"version": 1, "source_ops": [{"op": "delete", "path": escape, "sha256": "h"}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("not under an allowed" in e for e in out["errors"]), out["errors"])

    def test_non_memory_under_project_rejected(self):
        # under the project dir but NOT under /memory/ → not an allowed memory root
        p = os.path.join(self.home, ".claude", "projects", "proj", "notes.md")
        self.write(p, "x\n")
        rc, out, _ = self.v({"version": 1, "source_ops": [{"op": "delete", "path": p, "sha256": sha("x\n")}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("not under an allowed" in e for e in out["errors"]), out["errors"])

    def test_worktree_memory_rejected(self):
        wt = os.path.join(self.home, ".claude", "projects", "repo--claude-worktrees-feat", "memory")
        os.makedirs(wt)
        p = os.path.join(wt, "feedback_w.md")
        self.write(p, "x\n")
        rc, out, _ = self.v({"version": 1, "source_ops": [{"op": "delete", "path": p, "sha256": sha("x\n")}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("not under an allowed" in e for e in out["errors"]), out["errors"])

    def test_archive_dest_traversal_rejected(self):
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig\n")
        rc, out, _ = self.v({"version": 1, "archives": [
            {"source": src, "source_sha256": sha("orig\n"),
             "dest": "local/archive/../../escape.md", "promoted_to": "feedback-x-lesson"}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("resolve under local/archive/" in e for e in out["errors"]), out["errors"])

    def test_remove_index_lines_must_target_memory_md(self):
        rc, out, _ = self.v({"version": 1, "source_ops": [
            {"op": "remove_index_lines", "path": os.path.join(self.proj, "feedback_x.md"),
             "sha256": "h", "lines": ["- [x](x.md) — X"]}]})
        self.assertEqual(rc, 1)
        self.assertTrue(any("MEMORY.md" in e for e in out["errors"]), out["errors"])


class Apply(Base):
    def test_archive_then_delete_no_belief_written(self):
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig memory\n")
        rc, out, err = self.da({
            "version": 1,
            "beliefs": [self.good_belief()],
            "archives": [{"source": src, "source_sha256": sha("orig memory\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("orig memory\n")}],
        })
        self.assertEqual(rc, 0, err or out)
        self.assertFalse(os.path.exists(src), "source not deleted")
        arch = self.sp("local/archive/feedback_x_lesson.md")
        self.assertTrue(os.path.exists(arch), "archive copy missing")
        self.assertIn("promoted_to: feedback-x-lesson", self.read(arch))
        self.assertIn("orig memory", self.read(arch))
        # scripts never author beliefs — apply must NOT create the belief target file
        self.assertFalse(os.path.exists(self.sp("feedback_x_lesson.md")), "apply wrote a belief")

    def test_delete_skipped_when_archive_did_not_land(self):
        # M1: if the archive is SKIPPED at apply (dest already holds different content), the delete
        # must NOT run — the source survives, no backup lost (archive-before-delete cascade)
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig memory\n")
        self.write(self.sp("local/archive/feedback_x_lesson.md"), "different prior content\n")
        rc, out, err = self.da({"version": 1,
            "archives": [{"source": src, "source_sha256": sha("orig memory\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("orig memory\n")}]})
        self.assertEqual(rc, 3, out)
        self.assertTrue(os.path.exists(src), "source destroyed though its archive did not land")
        self.assertTrue(any(s.get("path") == src and s.get("op") == "delete" for s in out.get("skipped", [])), out)

    def test_bad_byte_source_is_protected_fail_closed(self):
        # [17]/M4: a source with a non-UTF-8 byte makes is_doctrine_file fail CLOSED (treated as
        # doctrine/protected), so validate REFUSES to delete/archive it — never a traceback, never
        # a destroyed unreadable file
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        with open(src, "wb") as f:
            f.write(b"---\nname: feedback-x-lesson\n---\n\xff corrupt\n")
        rc, out, _ = self.da({"version": 1,
            "archives": [{"source": src, "source_sha256": "deadbeef",
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": "deadbeef"}]}, "--validate")
        self.assertEqual(rc, 1, out)
        self.assertTrue(any("doctrine" in e for e in out.get("errors", [])), out)
        self.assertTrue(os.path.exists(src), "corrupt source not preserved")

    def test_apply_guard_skips_unarchived_delete_bypassing_validate(self):
        # M1 defense-in-depth: even if a no-archive delete reaches apply() (bypassing validate,
        # which now rejects it), the dropped-`any()`-conjunct guard skips it. Reachable only by
        # calling apply() directly, since main() runs validate first.
        import io, contextlib
        from importlib.machinery import SourceFileLoader
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig memory\n")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        try:
            mod = SourceFileLoader("distill_apply_probe", APPLY).load_module()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = mod.apply({"version": 1, "source_ops": [
                    {"op": "delete", "path": src, "sha256": sha("orig memory\n")}]})
            out = json.loads(buf.getvalue()) if buf.getvalue().strip() else {}
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
        self.assertTrue(os.path.exists(src), "apply destroyed an unarchived source")
        self.assertTrue(any(s.get("path") == src for s in out.get("skipped", [])), out)

    def test_stale_hash_skips_and_preserves_source(self):
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig\n")
        rc, out, _ = self.da({
            "version": 1,
            "archives": [{"source": src, "source_sha256": sha("STALE"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("STALE")}],
        })
        self.assertEqual(rc, 3, out)  # partial: skipped items reported
        self.assertTrue(os.path.exists(src), "source deleted despite stale hash")
        self.assertFalse(os.path.exists(self.sp("local/archive/feedback_x_lesson.md")))
        self.assertTrue(out["skipped"], "skips not reported")

    def test_double_apply_does_not_corrupt(self):
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig\n")
        plan = {
            "version": 1,
            "archives": [{"source": src, "source_sha256": sha("orig\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("orig\n")}],
        }
        rc1, _, _ = self.da(plan)
        self.assertEqual(rc1, 0)
        arch = self.sp("local/archive/feedback_x_lesson.md")
        first = self.read(arch)
        rc2, out2, _ = self.da(plan)  # source is gone now
        self.assertEqual(rc2, 3, out2)  # all items skip
        self.assertEqual(self.read(arch), first, "second apply corrupted the archive")

    def test_lockfile_blocks(self):
        self.write(self.sp(".distill.lock"), "")   # fresh (un-aged) lock → held, not recovered
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "orig\n")
        rc, out, _ = self.da({"version": 1,   # a VALID (archived) delete so it reaches apply()'s lock check
            "archives": [{"source": src, "source_sha256": sha("orig\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "delete", "path": src, "sha256": sha("orig\n")}]})
        self.assertEqual(rc, 2, out)
        self.assertIn("lockfile", out.get("error", ""))
        self.assertTrue(os.path.exists(src), "acted despite a held lock")

    def test_remove_index_lines_batches_one_file(self):
        # regression: several line-removals on one MEMORY.md apply in a single op (one
        # read-modify-write), so no intra-run hash drift skips the rest (the exit-3 bug)
        idx = os.path.join(self.proj, "MEMORY.md")
        self.write(idx, "# Index\n- [a](a.md) — A\n- [b](b.md) — B\n- [c](c.md) — C\n")
        rc, out, err = self.da({"version": 1, "source_ops": [
            {"op": "remove_index_lines", "path": idx, "sha256": sha(self.read(idx)),
             "lines": ["- [a](a.md) — A", "- [c](c.md) — C"]}]})
        self.assertEqual(rc, 0, err or out)
        txt = self.read(idx)
        self.assertNotIn("- [a](a.md) — A", txt)
        self.assertNotIn("- [c](c.md) — C", txt)
        self.assertIn("- [b](b.md) — B", txt)
        self.assertIn("# Index", txt)

    def test_remove_index_lines_skips_whole_op_when_index_changed(self):
        # if the index file changed since scan, the whole removal skips (atomic) — file untouched
        idx = os.path.join(self.proj, "MEMORY.md")
        self.write(idx, "# Index\n- [a](a.md) — A\n- [b](b.md) — B\n")
        rc, out, _ = self.da({"version": 1, "source_ops": [
            {"op": "remove_index_lines", "path": idx, "sha256": sha("STALE"),
             "lines": ["- [a](a.md) — A"]}]})
        self.assertEqual(rc, 3, out)
        self.assertIn("- [a](a.md) — A", self.read(idx), "removed despite stale hash")
        self.assertTrue(out["skipped"])

    def test_remove_index_lines_phantom_line_skips_whole_op(self):
        # a plan line that doesn't match the file verbatim (CAS still passes) is a plan bug,
        # not a race: skip the whole op without writing — all-or-nothing, distinct reason
        idx = os.path.join(self.proj, "MEMORY.md")
        self.write(idx, "# Index\n- [a](a.md) — A\n- [b](b.md) — B\n")
        rc, out, _ = self.da({"version": 1, "source_ops": [
            {"op": "remove_index_lines", "path": idx, "sha256": sha(self.read(idx)),
             "lines": ["- [a](a.md) — A", "- [z](z.md) — never here"]}]})
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.read(idx), "# Index\n- [a](a.md) — A\n- [b](b.md) — B\n",
                         "wrote despite a phantom line — must be all-or-nothing")
        self.assertTrue(any("absent from the file" in s.get("reason", "") for s in out["skipped"]), out["skipped"])

    def test_slim_overwrites(self):
        # M1: a slim consumes a source, so it must be archived first (every consumed source is
        # archived, whether deleted or slimmed) — the plan carries the matching archives entry
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "full body\n")
        rc, out, err = self.da({"version": 1,
            "archives": [{"source": src, "source_sha256": sha("full body\n"),
                          "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}],
            "source_ops": [{"op": "slim", "path": src, "sha256": sha("full body\n"), "slim_content": "slim remainder\n"}]})
        self.assertEqual(rc, 0, err or out)
        self.assertEqual(self.read(src), "slim remainder\n")

    def test_slim_or_delete_without_archive_rejected_by_validate(self):
        # M1 primary guard: a delete/slim with NO matching archives entry is rejected at validate
        # time (would otherwise destroy the source with no backup)
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "full body\n")
        for op in ({"op": "delete", "path": src, "sha256": sha("full body\n")},
                   {"op": "slim", "path": src, "sha256": sha("full body\n"), "slim_content": "x\n"}):
            rc, out, _ = self.da({"version": 1, "source_ops": [op]}, "--validate")
            self.assertEqual(rc, 1, out)
            self.assertTrue(any("requires a matching archives entry" in e for e in out.get("errors", [])), out)
        self.assertEqual(self.read(src), "full body\n", "source touched during a rejected validate")

    def test_archive_stamps_source_without_frontmatter(self):
        # the else branch: a source with no frontmatter gets a fresh stamped block prepended
        src = os.path.join(self.proj, "feedback_x_lesson.md")
        self.write(src, "plain body, no frontmatter\n")
        rc, out, err = self.da({"version": 1, "archives": [
            {"source": src, "source_sha256": sha("plain body, no frontmatter\n"),
             "dest": "local/archive/feedback_x_lesson.md", "promoted_to": "feedback-x-lesson"}]})
        self.assertEqual(rc, 0, err or out)
        arch = self.read(self.sp("local/archive/feedback_x_lesson.md"))
        self.assertTrue(arch.startswith("---\n  promoted_to: feedback-x-lesson\n---\n"), arch[:80])
        self.assertIn("plain body, no frontmatter", arch)

    def test_queue_consume_by_offset(self):
        # consumed offsets are appended to .candidates.consumed; .candidates.log is never rewritten
        log = self.sp(".candidates.log")
        self.write(log, "a\nb\nc\n")
        before = self.read(log)
        rc, out, err = self.da({"version": 1, "queue_remove": [0, 2]})
        self.assertEqual(rc, 0, err or out)
        self.assertEqual(self.read(log), before, ".candidates.log was rewritten — must be append-only")
        consumed = self.read(self.sp(".candidates.consumed"))
        self.assertEqual(sorted(int(x) for x in consumed.split()), [0, 2])
        self.assertTrue(any(a.get("queue_consumed") == 2 for a in out["applied"]), out["applied"])

    def test_queue_consume_idempotent_replay(self):
        # replaying a plan whose offsets are already consumed is a no-op: no double-record, no error
        self.write(self.sp(".candidates.consumed"), "3\n")
        rc, out, _ = self.da({"version": 1, "queue_remove": [3, 7]})
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(int(x) for x in self.read(self.sp(".candidates.consumed")).split()), [3, 7])
        self.assertTrue(any(a.get("queue_consumed") == 1 for a in out["applied"]), out["applied"])
        rc2, out2, _ = self.da({"version": 1, "queue_remove": [3, 7]})  # replay
        self.assertEqual(rc2, 0, out2)
        self.assertEqual(sorted(int(x) for x in self.read(self.sp(".candidates.consumed")).split()), [3, 7],
                         "replay double-recorded")
        self.assertTrue(any(a.get("queue_consumed") == 0 for a in out2["applied"]), out2["applied"])

    def test_queue_remove_rejects_non_int(self):
        rc, out, _ = self.da({"version": 1, "queue_remove": ["/a/path.md"]}, "--validate")
        self.assertEqual(rc, 1, out)
        self.assertTrue(any("integer queue-line offsets" in e for e in out["errors"]), out["errors"])

    def test_apply_refuses_invalid_plan(self):
        # bare apply (no --validate) must run the validator and short-circuit, touching nothing
        bad = {"version": 1, "source_ops": [
            {"op": "delete", "path": self.sp("x.md"), "sha256": "h"}]}  # path under the store = invalid
        rc, out, _ = self.da(bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("failed validation", out.get("error", ""))
        self.assertFalse(os.path.exists(self.sp(".distill.lock")), "apply ran despite an invalid plan")


class AtomicRouting(unittest.TestCase):
    def test_project_file_writes_route_through_atomic_write(self):
        # [R9 W-1] distill-apply's two in-place rewrites (slim, remove_index_lines) are inline in
        # apply() — not standalone funcs a subprocess test can fault-inject — so this pins routing
        # at the source level: the atomic helper must be present and NO bare truncating open() may
        # write a project file (the recurring failure mode is a site silently reverting to open('w')).
        src = open(APPLY, encoding="utf-8").read()
        self.assertIn("ac.atomic_write(path,", src, "a project-file rewrite no longer routes through atomic_write")
        import re as _re
        offenders = _re.findall(r"open\([^,]+,\s*['\"](?:w|x)", src)
        self.assertEqual(offenders, [], f"a truncating open() reappeared in distill-apply: {offenders}")


if __name__ == "__main__":
    unittest.main()
