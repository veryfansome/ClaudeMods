#!/usr/bin/env python3
"""Tests for review-scan — Layer 1: the read-only mechanical front of /memory-review.

Covers enumeration from the live indexes, staleness (doctrine-exempt), the observed/
contradicted flags, tombstone consistency (contradicted-not-tombstoned / tombstoned-not-
contradicted), guard-2 supersedes_live, orphan-body + dangling-index reconciliation,
desc_drift (with NO false positive on a well-formed framework or doctrine), and the
archive-reconciliation stages. No model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(os.path.dirname(HERE), "bin", "review-scan")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rs-test-")
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

    def append(self, rel, text):
        with open(self.sp(rel), "a") as f:
            f.write(text)

    def mem(self, fname, name, desc, index="MEMORY.md", mtype="feedback", basis="observed",
            last_verified=None, doctrine=False, applies_when=None, supersedes=None,
            index_title=None, index_desc=None, body="the body\n", write_index=True):
        fm = f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: {mtype}\n  basis: {basis}\n"
        if last_verified:
            fm += f"  last_verified: {last_verified}\n"
        if doctrine:
            fm += "  doctrine: true\n"
        if applies_when:
            fm += f"  applies_when: {applies_when}\n"
        if supersedes:
            fm += f"  supersedes: [{', '.join(supersedes)}]\n"
        fm += f"---\n{body}"
        rel = ("local/" + fname) if index == "MEMORY.local.md" else fname
        self.write(rel, fm)
        if write_index:
            d = index_desc if index_desc is not None else (f"{applies_when}: {desc}" if mtype == "framework" else desc)
            self.append_index(index, index_title or name, rel, d)
        return rel

    def append_index(self, index, title, link, desc):
        if not os.path.exists(self.sp(index)):
            self.write(index, "# Index\n")
        line = f"- [{title}]({link})" + (f" — {desc}" if desc else "")  # tombstone: no trailing guidance
        self.append(index, line + "\n")

    def scan(self):
        r = subprocess.run([sys.executable, SCAN], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def rec(self, out, name):
        return next((m for m in out["memories"] if m["name"] == name), None)


class DuplicateIndexLine(Base):
    def test_duplicate_flat_index_line_is_flagged(self):
        # [R9] D6: the same body linked twice in the flat tier (belief-channel append or a git
        # merge — neither passes an apply-script dedup) loads twice on every session yet the M3
        # dedup enumerates it once, so nothing surfaced it. Now reconcile.duplicate_index_line does.
        self.mem("feedback_a.md", "feedback-a", "lesson A")                  # one MEMORY.md line
        self.append_index("MEMORY.md", "feedback-a", "feedback_a.md", "lesson A")   # a duplicate line
        out = self.scan()
        dup = out["reconcile"]["duplicate_index_line"]
        self.assertEqual(len(dup), 1, dup)
        self.assertEqual(dup[0]["name"], "feedback-a")
        self.assertEqual(dup[0]["index"], "MEMORY.md")
        self.assertEqual(sum(1 for m in out["memories"] if m["name"] == "feedback-a"), 1,
                         "M3 single-enumeration must still hold")

    def test_cross_projection_repeat_is_not_flagged(self):
        # scope guard: a memory tagged with 2 domains has an identical GENERATED line in each of its
        # projections — by-design (deduped to one enumeration), NOT a duplicate_index_line
        self.write("reference_x.md",
                   "---\nname: reference-x\ntitle: X\ndescription: y\n"
                   "metadata:\n  type: reference\n  domains: [gcp, security]\n  basis: observed\n---\nb\n")
        gen = os.path.join(os.path.dirname(HERE), "bin", "gen-projections")
        subprocess.run([sys.executable, gen], env=dict(os.environ, HOME=self.home), capture_output=True)
        out = self.scan()
        self.assertEqual(out["reconcile"]["duplicate_index_line"], [],
                         "a cross-projection generated repeat was mis-flagged as a duplicate")
        self.assertEqual(sum(1 for m in out["memories"] if m["name"] == "reference-x"), 1)

    def test_domain_tagged_stray_flat_line_is_not_review_scans_signal(self):
        # [R9 W-4] scope boundary: a domain-tagged body that wrongly KEEPS a flat line (loads twice:
        # flat + projection) is NOT review-scan's duplicate_index_line — that stray belongs to
        # gen-projections --check ("projected memory still has a flat-index line" → exit 3). Pin
        # BOTH halves so the ownership boundary can't silently move.
        self.write("reference_x.md",
                   "---\nname: reference-x\ntitle: X\ndescription: y\n"
                   "metadata:\n  type: reference\n  domains: [gcp]\n  basis: observed\n---\nb\n")
        gen = os.path.join(os.path.dirname(HERE), "bin", "gen-projections")
        subprocess.run([sys.executable, gen], env=dict(os.environ, HOME=self.home), capture_output=True)
        self.append_index("MEMORY.md", "X", "reference_x.md", "y")   # re-introduce the stray flat line
        out = self.scan()
        self.assertEqual(out["reconcile"]["duplicate_index_line"], [],
                         "review-scan wrongly flagged a flat+projection stray (that is --check's boundary)")
        r = subprocess.run([sys.executable, gen, "--check"], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, "gen-projections --check must flag the flat+projection stray as drift")


class ReviewScan(Base):
    def test_enumerate_and_basic_flags(self):
        self.mem("feedback_a.md", "feedback-a", "lesson A", basis="observed")
        self.mem("reference_b.md", "reference-b", "fact B", mtype="reference", basis="verified",
                 last_verified="2026-07-01", body="body\n**Verified:** ran it\n")
        self.mem("local_pref.md", "pref-c", "a preference", index="MEMORY.local.md")
        out = self.scan()
        self.assertEqual({m["name"] for m in out["memories"]}, {"feedback-a", "reference-b", "pref-c"})
        self.assertIn("feedback-a", out["flags"]["observed"])
        self.assertIn("pref-c", out["flags"]["observed"])
        self.assertEqual(self.rec(out, "pref-c")["index"], "MEMORY.local.md")
        self.assertEqual(self.rec(out, "reference-b")["basis"], "verified")

    def test_staleness_doctrine_exempt(self):
        self.mem("feedback_old.md", "feedback-old", "old", basis="verified",
                 last_verified="2000-01-01", body="b\n**Verified:** x\n")
        self.mem("feedback_new.md", "feedback-new", "new", basis="verified",
                 last_verified="2026-07-01", body="b\n**Verified:** x\n")
        self.mem("framework_doc.md", "framework-doc", "doctrine one", mtype="framework",
                 basis="verified", last_verified="n/a", doctrine=True, applies_when="always")
        out = self.scan()
        self.assertTrue(self.rec(out, "feedback-old")["stale"])
        self.assertFalse(self.rec(out, "feedback-new")["stale"])
        self.assertFalse(self.rec(out, "framework-doc")["stale"], "doctrine (n/a) flagged stale")

    def test_contradicted_not_tombstoned_is_urgent(self):
        # a contradicted body whose index line still carries guidance is still steering
        self.mem("feedback_bad.md", "feedback-bad", "wrong guidance", basis="contradicted")
        out = self.scan()
        self.assertIn("feedback-bad", out["flags"]["contradicted"])
        self.assertIn("feedback-bad", out["reconcile"]["contradicted_not_tombstoned"])

    def test_tombstoned_contradicted_is_consistent(self):
        # body contradicted + index line tombstoned → consistent (not flagged either way)
        self.write("feedback_bad.md",
                   "---\nname: feedback-bad\ndescription: wrong\nmetadata:\n  type: feedback\n  basis: contradicted\n---\nb\n")
        self.append_index("MEMORY.md", "contradicted — pending review", "feedback_bad.md", "")
        # a tombstoned line carries no trailing guidance; append_index adds a trailing " — " only
        out = self.scan()
        self.assertNotIn("feedback-bad", out["reconcile"]["contradicted_not_tombstoned"])
        self.assertNotIn("feedback-bad", out["reconcile"]["tombstoned_not_contradicted"])

    def test_tombstoned_not_contradicted_flagged(self):
        # index line tombstoned but the body isn't contradicted → neutralized but unadjudicated
        self.write("feedback_x.md",
                   "---\nname: feedback-x\ndescription: fine\nmetadata:\n  type: feedback\n  basis: observed\n---\nb\n")
        self.append_index("MEMORY.md", "contradicted — pending review", "feedback_x.md", "")
        out = self.scan()
        self.assertIn("feedback-x", out["reconcile"]["tombstoned_not_contradicted"])

    def test_supersedes_live_guard2(self):
        self.mem("feedback_a.md", "feedback-a", "A")
        self.mem("framework_fold.md", "framework-fold", "folds", mtype="framework",
                 applies_when="x", supersedes=["feedback-a"])
        out = self.scan()
        self.assertEqual(out["flags"]["supersedes_live"],
                         [{"framework": "framework-fold", "still_live": ["feedback-a"]}])

    def test_orphan_body_and_dangling_index(self):
        self.mem("feedback_indexed.md", "feedback-indexed", "in the index")
        self.write("feedback_orphan.md",  # a body on disk, never indexed
                   "---\nname: feedback-orphan\ndescription: d\nmetadata:\n  type: feedback\n  basis: observed\n---\nb\n")
        self.append_index("MEMORY.md", "Ghost", "feedback_ghost.md", "points at nothing")  # dangling
        out = self.scan()
        self.assertIn("feedback_orphan.md", out["reconcile"]["orphan_bodies"])
        self.assertTrue(any(d["missing_body"] == "feedback_ghost.md" for d in out["reconcile"]["dangling_index"]))

    def test_bad_byte_index_and_body_degrade(self):
        # [17]: a non-UTF-8 byte in an index (parse_index) or a body (read_or_empty) degrades to
        # empty/skip — never crashes the scan
        def wb(rel, data):
            p = self.sp(rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        self.mem("feedback_a.md", "feedback-a", "lesson A")          # a clean memory in MEMORY.md
        wb("reference_bad.md", b"---\nname: reference-bad\n---\n\xff\n")   # good line -> bad body (read_or_empty)
        self.append_index("MEMORY.md", "Bad", "reference_bad.md", "d")
        wb("MEMORY.tech.md", b"# Domain index\n- [Z](reference_z.md) \xff\n")   # bad index (parse_index)
        out = self.scan()   # asserts rc 0 — must not traceback
        self.assertIsNotNone(self.rec(out, "feedback-a"), "clean memory lost when a sibling had a bad byte")

    def test_multi_domain_memory_enumerated_once(self):
        # M3: a memory tagged with >=2 domains has an identical generated line in each of its
        # projections; every review signal must count it ONCE (not once per projection)
        self.write("reference_x.md",
                   "---\nname: reference-x\ntitle: X\ndescription: the real description\n"
                   "metadata:\n  type: reference\n  domains: [alpha, beta]\n  basis: observed\n---\nb\n")
        for dom in ("alpha", "beta"):   # two projection indexes carrying the same line, desc drifted
            self.append_index(f"MEMORY.{dom}.md", "X", "reference_x.md", "a drifted description")
        out = self.scan()
        self.assertEqual([m["name"] for m in out["memories"]].count("reference-x"), 1, out["memories"])
        self.assertEqual(out["flags"]["observed"].count("reference-x"), 1, out["flags"]["observed"])
        self.assertEqual(sum(1 for d in out["reconcile"]["desc_drift"] if d["name"] == "reference-x"), 1,
                         out["reconcile"]["desc_drift"])
        self.assertFalse(any(c.get("cluster") == ["reference-x", "reference-x"] for c in out["flags"]["similar"]),
                         out["flags"]["similar"])

    def test_title_drift_flagged_and_clean(self):
        # M4: a flat index-line title that differs from the body `title` → title_drift; a match → not
        self.write("reference_cosmo.md",
                   "---\nname: reference-cosmo\ntitle: Cosmo alert suppression\ndescription: d\n"
                   "metadata:\n  type: reference\n  basis: observed\n---\nb\n")
        self.append_index("MEMORY.md", "Cosmo", "reference_cosmo.md", "d")   # title differs from body
        self.write("reference_ok.md",
                   "---\nname: reference-ok\ntitle: All Good\ndescription: d\n"
                   "metadata:\n  type: reference\n  basis: observed\n---\nb\n")
        self.append_index("MEMORY.md", "All Good", "reference_ok.md", "d")   # title matches body
        out = self.scan()
        drifted = {d["name"] for d in out["reconcile"]["title_drift"]}
        self.assertIn("reference-cosmo", drifted)
        self.assertNotIn("reference-ok", drifted)

    def test_desc_drift_no_false_positive_on_framework_or_doctrine(self):
        # plain memory with a genuinely different index description → drift
        self.mem("feedback_drift.md", "feedback-drift", "the true description",
                 index_desc="a completely different index line")
        # framework rendered as "<applies_when>: <description>" (lowercased) → NOT drift
        self.mem("framework_ok.md", "framework-ok", "Information saved should be grounded",
                 mtype="framework", applies_when="storing-a-memory",
                 index_desc="storing-a-memory: information saved should be grounded")
        # doctrine with a mismatched index line → exempt, NOT drift
        self.mem("framework_doc.md", "framework-doc", "the canonical wording", mtype="framework",
                 doctrine=True, applies_when="always", basis="verified",
                 index_desc="always: totally different wording")
        out = self.scan()
        drifted = {d["name"] for d in out["reconcile"]["desc_drift"]}
        self.assertIn("feedback-drift", drifted)
        self.assertNotIn("framework-ok", drifted, "well-formed framework index flagged as drift")
        self.assertNotIn("framework-doc", drifted, "doctrine flagged as drift")

    def test_archive_reconcile_stages(self):
        def stamp(name, extra=""):
            return f"---\nretired_from: {name}\nretired_on: 2026-07-01\nreason: r\n{extra}---\nbody\n"
        # pre-ledger: archived, source gone, no ledger line
        self.write("local/archive/feedback_gone.md", stamp("feedback-gone"))
        # pre-delete: archived, source still on disk, de-indexed, no ledger line
        self.write("local/archive/feedback_orphan.md", stamp("feedback-orphan"))
        self.write("feedback_orphan.md", "leftover\n")
        # pre-deindex: archived, source on disk, index line still present, no ledger line
        self.write("local/archive/feedback_stuck.md", stamp("feedback-stuck"))
        self.write("feedback_stuck.md", "leftover\n")
        self.append_index("MEMORY.md", "Stuck", "feedback_stuck.md", "still here")
        # finished: archived + ledger line → NOT flagged
        self.write("local/archive/feedback_done.md", stamp("feedback-done"))
        self.write("RETIRED.md", "# Retired\n- feedback-done · 2026-07-01 · r\n")
        # a promote archive (promoted_to, no retired_from) → NOT flagged
        self.write("local/archive/feedback_promo.md",
                   "---\n  promoted_to: feedback-promo\n---\nbody\n")
        out = self.scan()
        stages = {a["archived"]: a["stage"] for a in out["reconcile"]["archive"]}
        self.assertEqual(stages.get("local/archive/feedback_gone.md"), "pre-ledger")
        self.assertEqual(stages.get("local/archive/feedback_orphan.md"), "pre-delete")
        self.assertEqual(stages.get("local/archive/feedback_stuck.md"), "pre-deindex")
        self.assertNotIn("local/archive/feedback_done.md", stages)
        self.assertNotIn("local/archive/feedback_promo.md", stages)


if __name__ == "__main__":
    unittest.main()
