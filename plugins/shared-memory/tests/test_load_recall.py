#!/usr/bin/env python3
"""Tests for scripts/load-recall — M4 the SessionStart domain-recall loader (Layer 1).

Covers: a mapped repo injects its matching domain projections (union across the repo's
domains, deduped); an unmapped repo is silent (general-only); a subdir of a mapped repo
still resolves (repo-root keying); a mapped domain with no projection emits a VISIBLE in-band
note (not a silent drop); over-budget drops whole domains with a note; and fail-soft on
malformed / non-dict / no-cwd payloads. No model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
LOAD = os.path.join(PLUGIN, "scripts", "load-recall")
GEN = os.path.join(PLUGIN, "bin", "gen-projections")

import importlib.util
from importlib.machinery import SourceFileLoader
_ld = SourceFileLoader("ac_probe", os.path.join(PLUGIN, "bin", "_apply_common.py"))
_ac = _ld.load_module()  # for repo_root() to compute the map key the way the loader will


class LoadRecall(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lr4-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        os.makedirs(os.path.join(self.store, "local", "archive"))
        self.w("MEMORY.md", "# Index\n")
        self.w("MEMORY.local.md", "# Local\n")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def w(self, rel, text):
        p = os.path.join(self.store, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def mem(self, rel, name, title, desc, domains, local=False):
        self.w(("local/" if local else "") + rel,
               f"---\nname: {name}\ntitle: {title}\ndescription: {desc}\n"
               f"metadata:\n  type: reference\n  domains: {domains}\n  basis: observed\n---\nb\n")

    def generate(self):
        subprocess.run([sys.executable, GEN], env=dict(os.environ, HOME=self.home), capture_output=True)

    def git_repo(self, name):
        r = os.path.join(self.home, "work", name)
        os.makedirs(r)
        subprocess.run(["git", "-C", r, "init", "-q"])
        return r

    def map_repo(self, repo_path, domains):
        with open(os.path.join(self.store, ".domains.json"), "w") as f:
            json.dump({"version": 1, "repos": {_ac.repo_root(repo_path): domains}}, f)

    def load(self, cwd):
        payload = json.dumps({"cwd": cwd, "hook_event_name": "SessionStart", "source": "startup"})
        r = subprocess.run([sys.executable, LOAD], input=payload,
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_mapped_repo_injects_matching_domains(self):
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo alert suppression", "Accept-risk a Cosmo alert",
                 "[yahoo, security, gcp]")
        self.mem("reference_other.md", "reference-other", "Other", "unrelated fact", "[terraform]")
        self.generate()
        repo = self.git_repo("yahoo-repo"); self.map_repo(repo, ["gcp", "security"])
        ctx = self.load(repo)
        self.assertIsNotNone(ctx)
        self.assertIn("reference_cosmo.md", ctx)          # gcp/security match
        self.assertNotIn("reference_other.md", ctx)       # terraform not mapped
        self.assertEqual(ctx.count("reference_cosmo.md"), 1, "deduped across gcp+security")

    def test_unmapped_repo_is_silent_once_map_populated(self):
        # steady state: projections exist AND some repo is mapped → an unmapped repo is silent
        # (the fresh-clone onboarding hint only fires while NO repo is mapped — see below)
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo", "x", "[gcp]")
        self.generate()
        self.map_repo(self.git_repo("mapped"), ["gcp"])          # populate the map
        scratch = os.path.join(self.home, "work", "scratch"); os.makedirs(scratch)
        self.assertIsNone(self.load(scratch), "unmapped repo (populated map) should inject nothing")

    def test_fresh_clone_emits_onboarding_hint(self):
        # Unit 6: committed projections on disk + NO repo mapped (fresh clone / second machine) →
        # a one-line onboarding hint pointing at --map-repo-add, so the regression isn't silent
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo", "x", "[gcp]")
        self.generate()                                          # MEMORY.gcp.md exists; no .domains.json
        ctx = self.load(self.git_repo("anything"))
        self.assertIsNotNone(ctx, "fresh clone should surface the onboarding hint")
        self.assertIn("map-repo-add", ctx)
        self.assertNotIn("- [", ctx, "hint must not inject actual projection lines")

    def test_no_hint_without_projections(self):
        # unmapped repo, no projections on disk → nothing to onboard → silent
        self.assertIsNone(self.load(self.git_repo("empty")))

    def test_subdir_resolves_to_repo(self):
        self.mem("reference_cosmo.md", "reference-cosmo", "Cosmo", "x", "[gcp]")
        self.generate()
        repo = self.git_repo("r"); self.map_repo(repo, ["gcp"])
        sub = os.path.join(repo, "a", "b"); os.makedirs(sub)
        ctx = self.load(sub)  # launched from a subdir → must still resolve to the repo root key
        self.assertIsNotNone(ctx, "subdir did not resolve to the mapped repo root")
        self.assertIn("reference_cosmo.md", ctx)

    def test_local_domain_projection_injected(self):
        self.mem("reference_gcpfed.md", "reference-gcpfed", "gcpfed role", "use gcpfed", "[gcp]", local=True)
        self.generate()
        repo = self.git_repo("r"); self.map_repo(repo, ["gcp"])
        ctx = self.load(repo)
        self.assertIn("reference_gcpfed", ctx)

    def test_mapped_domain_without_projection_notes_it(self):
        # a repo mapped to a domain that has no projection file → VISIBLE in-band note, not silent
        self.generate()
        repo = self.git_repo("r"); self.map_repo(repo, ["nonexistent"])
        ctx = self.load(repo)
        self.assertIsNotNone(ctx)
        self.assertIn("no projection", ctx)
        self.assertIn("nonexistent", ctx)

    def test_over_budget_drops_with_note(self):
        # many large domain memories → exceed the budget → a domain dropped with a visible note
        for i in range(30):
            self.mem(f"reference_big{i}.md", f"reference-big{i}", f"Big {i}", "x" * 600, "[bulk]")
        self.generate()
        repo = self.git_repo("r"); self.map_repo(repo, ["bulk"])
        ctx = self.load(repo)
        self.assertIsNotNone(ctx)
        self.assertLessEqual(len(ctx), 10000 + 200)
        self.assertIn("over budget", ctx)

    def test_reserved_local_domain_not_loaded(self):
        # a repo mapped to the reserved domain 'local' must NOT make the loader read the flat
        # personal index MEMORY.local.md as a projection (double-load) — Unit-2 review
        self.w("MEMORY.local.md", "# Local\n- [gcpfed role](local/reference_gcpfed.md) — use gcpfed\n")
        self.generate()
        repo = self.git_repo("r"); self.map_repo(repo, ["local"])
        ctx = self.load(repo)
        if ctx is not None:
            self.assertNotIn("gcpfed", ctx, "loader injected the flat personal index for domain 'local'")

    def test_bad_byte_in_projection_is_fail_soft(self):
        # [17]/M4: a non-UTF-8 byte in a mapped projection must not crash the SessionStart hook —
        # the projection reads as missing → the VISIBLE mapped-but-no-projection note fires
        self.mem("reference_x.md", "reference-x", "X", "a fact", "[gcp]")
        self.generate()
        with open(os.path.join(self.store, "MEMORY.gcp.md"), "ab") as f:
            f.write(b"\xff\n")
        repo = self.git_repo("r"); self.map_repo(repo, ["gcp"])
        ctx = self.load(repo)   # load() asserts rc 0 (no traceback)
        self.assertIsNotNone(ctx)
        self.assertIn("no projection", ctx)

    def test_non_string_domain_is_fail_soft(self):
        # [14]: a non-string domain in a corrupt .domains.json must not TypeError render()'s header
        # — filter to valid slugs, load the valid ones, exit 0
        self.mem("reference_x.md", "reference-x", "X", "a fact", "[gcp]")
        self.generate()
        repo = self.git_repo("r")
        with open(os.path.join(self.store, ".domains.json"), "w") as f:
            json.dump({"version": 1, "repos": {_ac.repo_root(repo): ["gcp", 42, None]}}, f)
        ctx = self.load(repo)   # load() asserts rc 0 — no traceback
        self.assertIsNotNone(ctx)
        self.assertIn("reference_x.md", ctx)   # the valid 'gcp' domain still loads

    def test_domain_re_drops_non_slug_domains(self):
        # G3: a non-slug domain (contains '/', '.', or space) must be DROPPED by DOMAIN_RE, never
        # turned into a `MEMORY.<domain>.md` read. A bad-ONLY mapping stays silent (no injection, no
        # note). Fail-before: without DOMAIN_RE the loader tries `MEMORY.../evil.md` etc. and emits a
        # "domain '..' is mapped but has no projection" note (non-empty output). The projection base
        # is always STORE/MEMORY.<x>.md — the '..' is glued to the `MEMORY.` prefix and stays in-store
        # — so DOMAIN_RE's slug-filter (rejecting '/', '.', space uniformly) is the traversal defense.
        self.mem("reference_x.md", "reference-x", "X", "a fact", "[gcp]")
        self.generate()
        repo = self.git_repo("r")
        self.map_repo(repo, ["../evil", "has space"])   # only malformed domains → all filtered
        self.assertIsNone(self.load(repo), "a malformed domain reached a read instead of being filtered")

    def test_domain_re_filters_bad_but_loads_valid(self):
        # G3 companion: a mix of one valid + malformed/reserved domains injects ONLY the valid one,
        # and never emits a mapped-but-no-projection note for a filtered (bad) domain
        self.mem("reference_x.md", "reference-x", "X", "a fact", "[gcp]")
        self.generate()
        repo = self.git_repo("r")
        self.map_repo(repo, ["gcp", "../evil", "has space", "local"])
        ctx = self.load(repo)
        self.assertIsNotNone(ctx)
        self.assertIn("reference_x.md", ctx)          # gcp loaded
        self.assertNotIn("no projection", ctx)        # no bad domain reached the missing-projection note
        self.assertNotIn("evil", ctx)

    def test_malformed_payload_silent(self):
        r = subprocess.run([sys.executable, LOAD], input="not json",
                           env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_non_dict_and_no_cwd_silent(self):
        for payload in ("42", json.dumps({"hook_event_name": "SessionStart"})):
            r = subprocess.run([sys.executable, LOAD], input=payload,
                               env=dict(os.environ, HOME=self.home), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            # no-cwd falls back to os.getcwd() which won't be a mapped repo → silent
            self.assertEqual(r.stdout.strip(), "", f"payload {payload!r} was not silent")


if __name__ == "__main__":
    unittest.main()
