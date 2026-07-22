#!/usr/bin/env python3
"""Tests for bin/gen-projections — M4 the projection generator (Layer 1, no model calls).

Covers: rendering a projection line from the body (title + description); union (a [a,b]
memory in both projections); the doctrine guard (a doctrine memory is NEVER projected and
its flat line is kept, even if it carries a domain tag); basis-aware tombstone rendering; the
local tier (MEMORY.local.<domain>.md); flat-index slim (tagged lines removed, []-lines and
doctrine lines kept verbatim); the framework applies_when render; the missing-title refusal;
idempotency; stale-projection cleanup; --check drift; and the --flatten inverse.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(os.path.dirname(HERE), "bin", "gen-projections")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="gp-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def w(self, rel, text):
        p = os.path.join(self.store, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def mem(self, rel, name, title, desc, domains="[]", mtype="reference", basis="observed",
            doctrine=False, applies_when=None, local=False, with_title=True):
        fm = f"---\nname: {name}\n"
        if with_title:
            fm += f"title: {title}\n"
        fm += f"description: {desc}\nmetadata:\n  type: {mtype}\n  domains: {domains}\n  basis: {basis}\n"
        if doctrine:
            fm += "  doctrine: true\n"
        if applies_when:
            fm += f"  applies_when: {applies_when}\n"
        fm += "---\nbody\n"
        self.w(("local/" if local else "") + rel, fm)

    def flat_line(self, rel, title, desc, local=False):
        return f"- [{title}]({'local/' if local else ''}{rel}) — {desc}"

    def gen(self, *args):
        r = subprocess.run([sys.executable, GEN, *args], env=dict(os.environ, HOME=self.home),
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def read(self, base):
        p = os.path.join(self.store, base)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    def exists(self, base):
        return os.path.exists(os.path.join(self.store, base))


class Generate(Base):
    def seed(self):
        self.mem("feedback_general.md", "feedback-general", "General lesson", "always applies")
        self.mem("framework_gate.md", "framework-gate", "Memory gate", "Information saved should be grounded",
                 domains="[gcp]", mtype="framework", doctrine=True, applies_when="[storing-a-memory]")
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo alert suppression", "Accept-risk a Cosmo alert",
                 domains="[yahoo, security, gcp]")
        self.mem("reference_gcpfed.md", "reference-gcpfed", "gcpfed role", "use gcpfed not gcloud login",
                 domains="[gcp]", local=True)
        self.w("MEMORY.md", "# Index\n"
               + self.flat_line("feedback_general.md", "General lesson", "always applies") + "\n"
               + "- [Memory gate](framework_gate.md) — fires on storing-a-memory: information saved should be grounded\n"
               + self.flat_line("reference_cosmo.md", "Cosmo alert suppression", "Accept-risk a Cosmo alert") + "\n")
        self.w("MEMORY.local.md", "# Local\n"
               + self.flat_line("reference_gcpfed.md", "gcpfed role", "use gcpfed not gcloud login", local=True) + "\n")

    def test_generate_projections_and_slim(self):
        self.seed()
        rc, out, err = self.gen()
        self.assertEqual(rc, 0, err)
        flat = self.read("MEMORY.md")
        # []-general and doctrine (gcp-tagged) stay flat; the tagged non-doctrine line is removed
        self.assertIn("feedback_general.md", flat)
        self.assertIn("framework_gate.md", flat, "doctrine dropped from flat despite its tag")
        self.assertNotIn("reference_cosmo.md", flat, "tagged line not slimmed from flat")
        self.assertEqual(self.read("MEMORY.local.md").count("reference_gcpfed"), 0, "local tagged line not slimmed")

    def test_union_projection(self):
        self.seed()
        self.gen()
        for dom in ("gcp", "yahoo", "security"):
            self.assertIn("reference_cosmo.md", self.read(f"MEMORY.{dom}.md"), f"cosmo missing from {dom}")

    def test_doctrine_never_projected(self):
        self.seed()
        self.gen()
        self.assertNotIn("framework_gate.md", self.read("MEMORY.gcp.md") or "")

    def test_local_tier_projection(self):
        self.seed()
        self.gen()
        self.assertIn("reference_gcpfed", self.read("MEMORY.local.gcp.md"))
        self.assertNotIn("reference_gcpfed", self.read("MEMORY.gcp.md") or "")

    def test_framework_applies_when_render(self):
        # a domain-tagged framework renders with the "fires on ...:" prefix, lowercased desc
        self.mem("framework_x.md", "framework-x", "Do the thing", "Always Verify before writing",
                 domains="[gcp]", mtype="framework", applies_when="[stating-a-conclusion, writing-code]")
        self.w("MEMORY.md", "# Index\n")
        self.gen()
        self.assertIn("fires on stating-a-conclusion / writing-code: always Verify before writing",
                      self.read("MEMORY.gcp.md"))

    def test_contradicted_renders_as_tombstone(self):
        self.mem("feedback_bad.md", "feedback-bad", "Bad one", "wrong guidance", domains="[gcp]", basis="contradicted")
        self.w("MEMORY.md", "# Index\n")
        self.gen()
        self.assertIn("- [contradicted — pending review](feedback_bad.md)", self.read("MEMORY.gcp.md"))
        self.assertNotIn("wrong guidance", self.read("MEMORY.gcp.md"))

    def test_missing_title_refused(self):
        self.mem("reference_notitle.md", "reference-notitle", "", "some fact", domains="[gcp]", with_title=False)
        self.w("MEMORY.md", "# Index\n")
        rc, out, err = self.gen()
        self.assertEqual(rc, 2)
        self.assertIn("no `title`", err)
        self.assertFalse(self.exists("MEMORY.gcp.md"), "generated a projection despite the refusal")

    def test_idempotent_and_check(self):
        self.seed()
        self.gen()
        before = {b: self.read(b) for b in ("MEMORY.gcp.md", "MEMORY.yahoo.md", "MEMORY.security.md", "MEMORY.local.gcp.md", "MEMORY.md")}
        self.assertEqual(self.gen("--check")[0], 0, "check drift right after generate")
        self.gen()
        after = {b: self.read(b) for b in before}
        self.assertEqual(before, after, "second generate changed output")

    def test_check_detects_drift(self):
        self.seed()
        self.gen()
        # hand-corrupt a projection → --check must fail
        self.w("MEMORY.gcp.md", "# Domain index (generated by gen-projections — do not hand-edit)\n\n- tampered\n")
        self.assertEqual(self.gen("--check")[0], 3)

    def test_check_skips_when_lock_held(self):
        # Unit 9 run-window exclusion: a held store lock (a generate in flight) makes --check
        # report SKIPPED (exit 4), not spurious drift — with a live pid so it isn't stale-recovered
        self.seed()
        self.gen()  # in sync
        lock = os.path.join(self.store, ".distill.lock")
        with open(lock, "w") as f:
            f.write(json.dumps({"pid": os.getpid()}) + "\n")
        try:
            rc, out, _ = self.gen("--check")
            self.assertEqual(rc, 4)
            self.assertIn("skipped", out)
        finally:
            os.remove(lock)

    def test_check_recovers_aged_ownerless_lock(self):
        # M4-tail review m1: a 0-byte / owner-less lock (a crash between os.open and the pid write)
        # must not wedge --check forever. Fresh → still treated as held (avoids the create/write
        # race); aged past the grace → recovered so --check runs and can surface real drift.
        import time as _t
        self.seed()
        self.gen()  # in sync
        lock = os.path.join(self.store, ".distill.lock")
        open(lock, "w").close()  # empty: no pid recorded
        self.assertEqual(self.gen("--check")[0], 4, "a fresh owner-less lock is treated as held")
        old = _t.time() - 120
        os.utime(lock, (old, old))
        self.assertEqual(self.gen("--check")[0], 0, "an aged owner-less lock must be recovered")
        self.assertFalse(os.path.exists(lock), "the recovered lock is released after the check")

    def test_generate_loads_memories_under_the_lock(self):
        # [9]: the mutating paths must load_memories() INSIDE the store lock. Assert the lock file
        # exists at the moment load_memories runs — a revert that loads before acquire records False.
        import sys as _sys
        from importlib.machinery import SourceFileLoader
        self.seed()
        old_home, old_argv = os.environ.get("HOME"), _sys.argv
        os.environ["HOME"] = self.home
        try:
            mod = SourceFileLoader("gen_probe", GEN).load_module()
            real_load, seen = mod.load_memories, {}
            def wrapped():
                seen["lock_existed"] = os.path.exists(mod.LOCK)
                return real_load()
            mod.load_memories = wrapped
            _sys.argv = ["gen-projections"]   # generate
            mod.main()
        finally:
            _sys.argv = old_argv
            if old_home is not None:
                os.environ["HOME"] = old_home
        self.assertTrue(seen.get("lock_existed"), "load_memories ran before the lock was acquired ([9] reverted)")

    def test_repeated_domain_tag_not_duplicated(self):
        # [8]: `domains: [gcp, gcp]` must render ONE projection line, and --check stays in sync
        self.mem("reference_x.md", "reference-x", "X", "a fact", domains="[gcp, gcp]")
        self.gen()
        self.assertEqual(self.read("MEMORY.gcp.md").count("reference_x.md"), 1,
                         self.read("MEMORY.gcp.md"))
        self.assertEqual(self.gen("--check")[0], 0, "a repeated domain left --check out of sync")

    def test_check_flags_unparseable_body(self):
        # Unit-4 review m1: --check must report an unparseable body as drift (exit 3), matching
        # what a real generate would refuse — so the doctor can't flash green on a store its own
        # remediation would reject.
        self.seed()
        self.gen()
        self.w("junk.md", "no frontmatter here, just text\n")
        rc, out, _ = self.gen("--check")
        self.assertEqual(rc, 3)
        self.assertIn("unparseable", out)

    def test_stale_projection_cleanup(self):
        self.seed()
        self.gen()
        self.assertTrue(self.exists("MEMORY.security.md"))
        # retag cosmo to drop security+yahoo → those projections should vanish
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo alert suppression", "Accept-risk a Cosmo alert", domains="[gcp]")
        self.gen()
        self.assertFalse(self.exists("MEMORY.security.md"), "stale projection not removed")
        self.assertFalse(self.exists("MEMORY.yahoo.md"))
        self.assertTrue(self.exists("MEMORY.gcp.md"))

    def test_invalid_domain_refused(self):
        # a domain with a space or '/' can't round-trip through INDEX_RE / a filename → refuse (exit 2),
        # never crash or mint an unmanageable projection (Unit-1 review MF-1)
        for dom in ("code review", "a/b", "dot.ted"):
            self.setUp()
            self.mem("reference_x.md", "reference-x", "X", "some fact", domains=f"[{dom}]")
            self.w("MEMORY.md", "# Index\n")
            rc, out, err = self.gen()
            self.assertEqual(rc, 2, f"domain {dom!r} not refused")
            self.assertIn("invalid domain", err)
            self.assertFalse(self.exists(f"MEMORY.{dom}.md"))

    def test_reserved_local_domain_refused(self):
        # 'local' would collide with / overwrite the flat personal index — reserved (Unit-2 review)
        self.mem("reference_x.md", "reference-x", "X", "fact", domains="[local]")
        self.w("MEMORY.md", "# Index\n")
        self.w("MEMORY.local.md", "# Local\n")
        rc, out, err = self.gen()
        self.assertEqual(rc, 2)
        self.assertIn("local", err)
        self.assertEqual(self.read("MEMORY.local.md"), "# Local\n", "flat personal index clobbered")

    def test_unparseable_body_refused(self):
        # a body with no frontmatter can't be classified → refuse, don't silently mis-scope
        self.w("broken.md", "just prose, no frontmatter\n")
        self.w("MEMORY.md", "# Index\n")
        rc, out, err = self.gen()
        self.assertEqual(rc, 2)
        self.assertIn("unparseable", err)

    def test_backfill_titles(self):
        # a domain-tagged body without title + a flat line carrying the title → backfill adds it,
        # then generate succeeds; idempotent; contradicted body exempt
        self.mem("reference_cosmo.md", "reference-cosmo", "", "Accept-risk a Cosmo alert", domains="[gcp]", with_title=False)
        self.w("MEMORY.md", "# Index\n- [Cosmo alert suppression](reference_cosmo.md) — Accept-risk a Cosmo alert\n")
        rc, out, err = self.gen("--backfill-titles")
        self.assertEqual(rc, 0, err)
        body = self.read("reference_cosmo.md")
        self.assertIn("title: Cosmo alert suppression", body)
        self.assertEqual(self.gen("--backfill-titles")[0], 0)   # idempotent
        self.assertEqual(self.gen()[0], 0, "generate still refuses after backfill")
        self.assertIn("reference_cosmo.md", self.read("MEMORY.gcp.md"))

    def test_backfill_reports_unrecoverable_title(self):
        # a tagged body with no title AND no flat line to recover it from → report + exit 3
        self.mem("reference_orphan.md", "reference-orphan", "", "orphan fact", domains="[gcp]", with_title=False)
        self.w("MEMORY.md", "# Index\n")  # no line for it
        rc, out, err = self.gen("--backfill-titles")
        self.assertEqual(rc, 3)
        self.assertIn("reference_orphan.md", err)

    def test_flatten_roundtrip(self):
        self.seed()
        self.gen()
        rc, out, err = self.gen("--flatten")
        self.assertEqual(rc, 0, err)
        flat = self.read("MEMORY.md")
        self.assertIn("reference_cosmo.md", flat, "tagged line not re-inlined")
        self.assertIn("reference_gcpfed", self.read("MEMORY.local.md"))
        self.assertFalse(self.exists("MEMORY.gcp.md"), "projection files not removed")
        # no duplicate of the []-general line
        self.assertEqual(flat.count("feedback_general.md"), 1)


if __name__ == "__main__":
    unittest.main()
