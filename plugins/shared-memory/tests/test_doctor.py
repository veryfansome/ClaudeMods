#!/usr/bin/env python3
"""Tests for `memory-init --doctor` — Layer 1: the read-only health pass (M3).

Covers the offline checks (wiring-drift's four import-block states + missing settings rules;
plugin-health enabled/stale/hooks-absent; automemdir-coverage inert-skip / per-project-
override warn / user-override-under-store; version-drift skip/ok/warn; check-ignore skip on
solo) against a throwaway HOME with a controlled cwd, the JSON schema, and exit codes. The
authed recall-health probe is Layer 2 (recall-probes.sh doctor-recall) — here it is --no-probe
(skip). No model calls.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
INIT = os.path.join(os.path.dirname(HERE), "bin", "memory-init")
GEN = os.path.join(os.path.dirname(HERE), "bin", "gen-projections")

# reuse the installer's own wiring constants (single source of truth) — memory-init is
# extensionless, so load it by path. Only its pure data (BEGIN/IMPORT_BLOCK/ALLOW/PLUGIN_ID)
# is used; its path constants (computed from the real ~) are irrelevant here.
_loader = SourceFileLoader("memory_init", INIT)
_spec = importlib.util.spec_from_loader("memory_init", _loader)
mi = importlib.util.module_from_spec(_spec)
_loader.exec_module(mi)

CLI_VERSION = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout
import re
CLI_VERSION = (re.search(r"(\d+\.\d+\.\d+)", CLI_VERSION) or [None, "0.0.0"])[1] if CLI_VERSION else "0.0.0"


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="doc-test-")
        self.cwd = tempfile.mkdtemp(prefix="doc-cwd-")  # controlled project scope for plugin-health
        self.claude = os.path.join(self.home, ".claude")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.plugins = os.path.join(self.claude, "plugins")
        os.makedirs(os.path.join(self.store, "local", "archive"))
        os.makedirs(self.plugins)
        self._write_healthy()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.cwd, ignore_errors=True)

    def w(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _write_healthy(self):
        # wiring: import block + all owned allow rules + plugin enabled (user scope)
        self.w(os.path.join(self.claude, "CLAUDE.md"), "# user\n\n" + mi.IMPORT_BLOCK)
        self.w(os.path.join(self.claude, "settings.json"), json.dumps({
            "permissions": {"allow": list(mi.ALLOW), "ask": list(mi.ASK)},
            "enabledPlugins": {mi.PLUGIN_ID: True},
        }))
        # installed build with hooks/ present AND carrying the M4 SessionStart→load-recall entry
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.2.0")
        self.w(os.path.join(cache, "hooks", "hooks.json"), json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write|Edit", "hooks": [
                        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/capture-candidate"},
                        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/eval-memory"}]},
                    {"matcher": "Read", "hooks": [
                        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/log-recall"}]}],
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/load-recall"}]}],
                "SessionEnd": [{"hooks": [
                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/reflect-end"}]}]}
        }))
        self.w(os.path.join(self.plugins, "installed_plugins.json"), json.dumps({
            "plugins": {mi.PLUGIN_ID: [{"version": "0.2.0", "installPath": cache}]}
        }))
        # a memory index so recall/coverage have something (coverage looks at ~/.claude/projects)

    def doctor(self, *extra):
        r = subprocess.run([sys.executable, INIT, "--doctor", "--no-probe", *extra],
                           env=dict(os.environ, HOME=self.home), cwd=self.cwd,
                           capture_output=True, text=True)
        out = json.loads(r.stdout)
        return r.returncode, out, {c["check"]: c for c in out["checks"]}

    def status(self, extra=(), check=None):
        _, _, checks = self.doctor(*extra)
        return checks[check]["status"]


class Schema(Base):
    def test_healthy_all_green_or_skip(self):
        rc, out, checks = self.doctor()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["version"], 1)
        self.assertIn("store", out)
        self.assertEqual(checks["wiring-drift"]["status"], "ok")
        self.assertEqual(checks["plugin-health"]["status"], "ok", checks["plugin-health"])
        self.assertEqual(checks["recall-health"]["status"], "skip")  # --no-probe
        for c in out["checks"]:
            self.assertEqual(set(c), {"check", "status", "detail", "findings", "caveats"})
            self.assertIn(c["status"], ("ok", "warn", "fail", "skip"))

    def test_check_names_present(self):
        _, _, checks = self.doctor()
        self.assertEqual(set(checks), {"wiring-drift", "plugin-health", "automemdir-coverage",
                                       "projection-integrity", "unmapped-with-projections",
                                       "recall-health", "version-drift", "check-ignore"})


class WiringDrift(Base):
    def test_missing_allow_rule_fails(self):
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        s["permissions"]["allow"] = [r for r in s["permissions"]["allow"] if "review" not in r]
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        rc, _, checks = self.doctor()
        self.assertEqual(checks["wiring-drift"]["status"], "fail")
        self.assertEqual(rc, 2)
        self.assertTrue(any("missing" in f for f in checks["wiring-drift"]["findings"]))

    def test_absent_block_fails(self):
        self.w(os.path.join(self.claude, "CLAUDE.md"), "# user, no import block\n")
        _, _, checks = self.doctor()
        self.assertEqual(checks["wiring-drift"]["status"], "fail")
        self.assertTrue(any(f.get("import_block") == "absent" for f in checks["wiring-drift"]["findings"]))

    def test_unreadable_claudemd_degrades_not_crash(self):
        # [7]: a directory at CLAUDE.md → 'unreadable' wiring-drift finding, not a doctor traceback;
        # the settings/allow checks still run (a full JSON report is still emitted)
        cm = os.path.join(self.claude, "CLAUDE.md")
        os.remove(cm)
        os.makedirs(cm)   # now a directory → open() raises OSError
        rc, out, checks = self.doctor()   # doctor() parses stdout as JSON → asserts no crash
        self.assertTrue(any(f.get("import_block") == "unreadable" for f in checks["wiring-drift"]["findings"]),
                        checks["wiring-drift"])

    def test_non_utf8_claudemd_degrades_not_crash(self):
        # [7] R2-review M-1: a non-UTF-8 CLAUDE.md (UnicodeDecodeError, NOT OSError) must also
        # degrade to an 'unreadable' finding, not traceback the whole --doctor pass
        with open(os.path.join(self.claude, "CLAUDE.md"), "wb") as f:
            f.write(b"\xff\xfe not utf-8\n")
        rc, out, checks = self.doctor()   # parses JSON → asserts no crash
        self.assertTrue(any(f.get("import_block") == "unreadable" for f in checks["wiring-drift"]["findings"]),
                        checks["wiring-drift"])

    def test_orphaned_begin_fails_distinctly(self):
        self.w(os.path.join(self.claude, "CLAUDE.md"), "# user\n" + mi.BEGIN + "\n@~/.claudemods/shared-memory/MEMORY.md\n")
        _, _, checks = self.doctor()
        self.assertTrue(any(f.get("import_block") == "orphaned-begin" for f in checks["wiring-drift"]["findings"]))

    def test_block_modified_flagged_as_its_own_state(self):
        # BEGIN+END present but the @import lines hand-corrupted — --install can't repair this
        self.w(os.path.join(self.claude, "CLAUDE.md"),
               "# user\n" + mi.BEGIN + "\n@~/.claudemods/shared-memory/WRONG.md\n" + mi.END + "\n")
        _, _, checks = self.doctor()
        f = checks["wiring-drift"]["findings"]
        self.assertTrue(any(x.get("import_block") == "block-modified" for x in f), f)
        self.assertTrue(any("--install" in x.get("fix", "") and "won't" in x.get("fix", "") for x in f), f)

    def test_stale_retired_write_rule_flagged(self):
        # the doctor blind spot the review found: a leftover Write() rule warns every session,
        # but append-only --install used to miss it; wiring-drift must flag it (fix: --install)
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        s["permissions"]["allow"].append("Write(~/.claudemods/shared-memory/**)")
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        rc, _, checks = self.doctor()
        self.assertEqual(checks["wiring-drift"]["status"], "fail")
        self.assertTrue(any("stale_retired" in f for f in checks["wiring-drift"]["findings"]),
                        checks["wiring-drift"]["findings"])
        self.assertEqual(rc, 2)

    def test_block_modified_catches_substring_preserving_corruption(self):
        # the M1 false-clear: a dead path that still CONTAINS the canonical string as a substring,
        # and an injected extra @import alongside the valid lines — both must trip block-modified
        for corrupt in (
            mi.BEGIN + "\n@~/.claudemods/shared-memory/MEMORY.md-DISABLED\n"
            "@~/.claudemods/shared-memory/MEMORY.local.md-DISABLED\n" + mi.END + "\n",
            mi.BEGIN + "\n@~/.claudemods/shared-memory/MEMORY.md\n"
            "@~/.claudemods/shared-memory/MEMORY.local.md\n@~/.evil/EVIL.md\n" + mi.END + "\n",
        ):
            self.w(os.path.join(self.claude, "CLAUDE.md"), "# user\n" + corrupt)
            _, _, checks = self.doctor()
            self.assertEqual(checks["wiring-drift"]["status"], "fail",
                             f"substring-preserving corruption reported clean: {corrupt!r}")


class PluginHealth(Base):
    def test_not_enabled_fails(self):
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        del s["enabledPlugins"]
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any(f.get("plugin") == "not enabled" for f in checks["plugin-health"]["findings"]))

    def test_enabled_at_project_scope_ok(self):
        # remove user-scope enablement, add it at cwd project scope — still enabled
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        del s["enabledPlugins"]
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        self.w(os.path.join(self.cwd, ".claude", "settings.json"),
               json.dumps({"enabledPlugins": {mi.PLUGIN_ID: True}}))
        self.assertEqual(self.status(check="plugin-health"), "ok")

    def test_hooks_absent_build_fails(self):
        # the frozen-M0-cache case: installed build's installPath has no hooks/
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.1.0")
        os.makedirs(cache)
        self.w(os.path.join(self.plugins, "installed_plugins.json"), json.dumps({
            "plugins": {mi.PLUGIN_ID: [{"version": "0.1.0", "installPath": cache}]}}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any(f.get("hooks") == "absent" for f in checks["plugin-health"]["findings"]))

    def test_hooks_json_without_loader_fails(self):
        # existence isn't enough — a build whose hooks.json lacks SessionStart→load-recall means
        # domain recall is silently off (Unit-4: plugin-health reads hooks.json CONTENT)
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.2.0")
        self.w(os.path.join(cache, "hooks", "hooks.json"), json.dumps({
            "hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [{"command": "x/capture"}]}]}}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any("load-recall" in str(f.get("hooks", "")) for f in checks["plugin-health"]["findings"]))

    def test_hooks_json_missing_capture_or_reflect_fails(self):
        # G1a: a build with SessionStart→load-recall but MISSING the capture / reflect-end entries
        # must fail plugin-health — the pre-G1a check only looked for load-recall and passed this
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.2.0")
        self.w(os.path.join(cache, "hooks", "hooks.json"), json.dumps({
            "hooks": {"SessionStart": [{"hooks": [
                {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/load-recall"}]}]}}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any("capture-candidate" in str(f.get("hooks", ""))
                            for f in checks["plugin-health"]["findings"]), checks["plugin-health"])

    def test_hooks_json_missing_eval_fails(self):
        # G1a: eval-memory dark (load-recall + capture + reflect-end present, eval-memory missing)
        # must fail plugin-health — the eval gate is a guarded pipeline stage
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.2.0")
        self.w(os.path.join(cache, "hooks", "hooks.json"), json.dumps({
            "hooks": {
                "PostToolUse": [{"matcher": "Write|Edit", "hooks": [
                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/capture-candidate"}]}],
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/load-recall"}]}],
                "SessionEnd": [{"hooks": [
                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/reflect-end"}]}]}}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any("eval-memory" in str(f.get("hooks", "")) for f in checks["plugin-health"]["findings"]),
                        checks["plugin-health"])

    def test_wrong_typed_installed_plugins_json_no_traceback(self):
        # [6]: a wrong-typed (foreign / future-format) installed_plugins.json must yield a finding,
        # not traceback the whole doctor + install tail (installed_plugins.json already carries a
        # version field, so a CLI format bump is exactly this shape)
        ip = os.path.join(self.plugins, "installed_plugins.json")
        for shape in ('[1, 2, 3]', '{"plugins": "not-a-dict"}',
                      '{"plugins": {"' + mi.PLUGIN_ID + '": "not-a-list"}}',
                      '{"plugins": {"' + mi.PLUGIN_ID + '": [42]}}'):   # list, non-dict element
            self.w(ip, shape)
            r = subprocess.run([sys.executable, INIT, "--doctor", "--no-probe"],
                               env=dict(os.environ, HOME=self.home), cwd=self.cwd, capture_output=True, text=True)
            self.assertNotIn("Traceback", r.stderr, shape)
            out = json.loads(r.stdout)   # a full JSON report is still emitted
            self.assertIn("plugin-health", {c["check"] for c in out["checks"]}, shape)

    def test_wrong_typed_project_enabledplugins_no_traceback(self):
        # [6]: a project settings.json with a non-dict enabledPlugins must not crash plugin-health.
        # Disable USER-scope enablement first, else the `or` short-circuits before the project arm
        # is ever evaluated (the guard being tested).
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        s["enabledPlugins"] = {}
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        self.w(os.path.join(self.cwd, ".claude", "settings.json"), '{"enabledPlugins": [1, 2]}')
        _, _, checks = self.doctor()   # doctor() asserts stdout is valid JSON → no crash
        self.assertIn("plugin-health", checks)

    def test_wrong_typed_known_marketplaces_no_traceback(self):
        # [6] R2-review M-2: the third JSON read in check_plugin_health (known_marketplaces.json)
        # must isinstance-guard too — a foreign/corrupt shape must not traceback the doctor
        km = os.path.join(self.plugins, "known_marketplaces.json")
        for shape in ('[1, 2, 3]', '42', '{"claudemods": ["a"]}', '{"claudemods": {"source": "x"}}'):
            self.w(km, shape)
            r = subprocess.run([sys.executable, INIT, "--doctor", "--no-probe"],
                               env=dict(os.environ, HOME=self.home), cwd=self.cwd, capture_output=True, text=True)
            self.assertNotIn("Traceback", r.stderr, shape)
            out = json.loads(r.stdout)
            self.assertIn("plugin-health", {c["check"] for c in out["checks"]}, shape)

    def test_hooks_json_wrong_typed_shape_fails_cleanly(self):
        # Unit-4 review M1: a stale/foreign build can be valid JSON of the WRONG shape. The content
        # parse must isinstance-guard every level → a clean plugin-health fail (load-recall missing),
        # NEVER an AttributeError traceback that destroys the whole doctor report.
        cache = os.path.join(self.plugins, "cache", "claudemods", "shared-memory", "0.2.0")
        for shape in (
            {"hooks": ["load-recall"]},                                  # hooks a list
            {"hooks": "load-recall"},                                    # hooks a string
            {"hooks": {"SessionStart": {"cmd": "load-recall"}}},         # SessionStart a dict
            {"hooks": {"SessionStart": ["load-recall"]}},                # group a string
            {"hooks": {"SessionStart": [{"hooks": "load-recall"}]}},     # inner hooks a string
            {"hooks": {"SessionStart": [{"hooks": ["load-recall"]}]}},   # inner hook a string
            [1, 2, 3],                                                   # top-level array
        ):
            self.w(os.path.join(cache, "hooks", "hooks.json"), json.dumps(shape))
            r = subprocess.run([sys.executable, INIT, "--doctor", "--no-probe"],
                               env=dict(os.environ, HOME=self.home), cwd=self.cwd,
                               capture_output=True, text=True)
            self.assertNotIn("Traceback", r.stderr, f"traceback on shape {shape!r}")
            out = json.loads(r.stdout)   # a full JSON report must still be emitted
            checks = {c["check"]: c for c in out["checks"]}
            self.assertEqual(checks["plugin-health"]["status"], "fail", shape)
            self.assertTrue(any("load-recall" in str(f.get("hooks", ""))
                                for f in checks["plugin-health"]["findings"]), shape)

    def test_enabled_but_no_installed_build_fails(self):
        # enabled but no record in installed_plugins.json → hooks can't be live (pipeline dead)
        os.remove(os.path.join(self.plugins, "installed_plugins.json"))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any(f.get("hooks") == "unverifiable" for f in checks["plugin-health"]["findings"]))

    def test_stale_build_vs_directory_source_fails(self):
        # installed 0.1.0 but the local marketplace source is 0.3.0 → stale
        src = os.path.join(self.cwd, "src-marketplace")
        self.w(os.path.join(src, "plugins", "shared-memory", ".claude-plugin", "plugin.json"),
               json.dumps({"version": "0.3.0"}))
        self.w(os.path.join(self.plugins, "known_marketplaces.json"),
               json.dumps({"claudemods": {"source": {"source": "directory", "path": src}}}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["plugin-health"]["status"], "fail")
        self.assertTrue(any(f.get("source") == "0.3.0" for f in checks["plugin-health"]["findings"]))


class Coverage(Base):
    def _project(self, enc, cwd, records):
        d = os.path.join(self.claude, "projects", enc)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s.jsonl"), "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_inert_dir_skipped_not_flagged(self):
        os.makedirs(os.path.join(self.claude, "projects", "-inert-proj"))  # no transcripts
        self.assertEqual(self.status(check="automemdir-coverage"), "ok")

    def test_per_project_override_warns(self):
        proj = os.path.join(self.cwd, "myrepo")
        self._project("-enc-myrepo", proj, [{"cwd": proj, "type": "user"}])
        self.w(os.path.join(proj, ".claude", "settings.json"),
               json.dumps({"autoMemoryDirectory": "/somewhere/else/mem"}))
        _, _, checks = self.doctor()
        self.assertEqual(checks["automemdir-coverage"]["status"], "warn")
        self.assertTrue(any(f.get("scope") == "settings.json" for f in checks["automemdir-coverage"]["findings"]))

    def test_user_override_under_store_warns(self):
        s = json.load(open(os.path.join(self.claude, "settings.json")))
        s["autoMemoryDirectory"] = os.path.join(self.store, "sneaky")
        self.w(os.path.join(self.claude, "settings.json"), json.dumps(s))
        _, _, checks = self.doctor()
        self.assertEqual(checks["automemdir-coverage"]["status"], "warn")
        self.assertTrue(any("under the store" in f.get("issue", "") for f in checks["automemdir-coverage"]["findings"]))


class VersionDrift(Base):
    def test_no_stamp_skips(self):
        self.assertEqual(self.status(check="version-drift"), "skip")

    def test_current_version_in_set_ok(self):
        self.w(os.path.join(self.store, ".probe-stamp.json"),
               json.dumps({"passed_versions": [CLI_VERSION]}))
        self.assertEqual(self.status(check="version-drift"), "ok")

    def test_current_version_not_in_set_warns(self):
        self.w(os.path.join(self.store, ".probe-stamp.json"),
               json.dumps({"passed_versions": ["0.0.1"]}))
        self.assertEqual(self.status(check="version-drift"), "warn")


class ProjectionIntegrity(Base):
    def test_ok_when_no_tagged_memories(self):
        # empty/[]-only store → no projections desired → in sync
        self.assertEqual(self.status(check="projection-integrity"), "ok")

    def test_fail_on_drift(self):
        # [R9] H-drift: a domain-tagged memory with a flat line but no projection → out of sync →
        # FAIL (was 'warn'), so run_doctor exits 2 and CI/pre-commit catches an apply that mutated
        # a domain body without regenerating its projection (the stale line keeps steering recall)
        self.w(os.path.join(self.store, "reference_cosmo.md"),
               "---\nname: reference-cosmo\ntitle: Cosmo\ndescription: d\n"
               "metadata:\n  type: reference\n  domains: [gcp]\n  basis: observed\n---\nb\n")
        self.w(os.path.join(self.store, "MEMORY.md"), "# Index\n- [Cosmo](reference_cosmo.md) — d\n")
        rc, _, checks = self.doctor()
        self.assertEqual(checks["projection-integrity"]["status"], "fail")
        self.assertEqual(rc, 2, "real projection drift must fail the doctor (exit 2)")

    def test_skip_when_generate_in_flight(self):
        # Unit 9: a held store lock (generate in flight) → --check exits 4 → skip, NOT a drift warn,
        # even with a store that would otherwise show drift
        self.w(os.path.join(self.store, "reference_cosmo.md"),
               "---\nname: reference-cosmo\ntitle: Cosmo\ndescription: d\n"
               "metadata:\n  type: reference\n  domains: [gcp]\n  basis: observed\n---\nb\n")
        self.w(os.path.join(self.store, "MEMORY.md"), "# Index\n- [Cosmo](reference_cosmo.md) — d\n")
        with open(os.path.join(self.store, ".distill.lock"), "w") as f:
            f.write(json.dumps({"pid": os.getpid()}) + "\n")   # live pid → not stale-recovered
        try:
            _, _, checks = self.doctor()
            self.assertEqual(checks["projection-integrity"]["status"], "skip")
        finally:
            os.remove(os.path.join(self.store, ".distill.lock"))


class UnmappedWithProjections(Base):
    def _projection(self):
        # [R9] build an IN-SYNC projection (a real generate), so projection-integrity is 'ok' and
        # unmapped-with-projections is the only non-ok check — an orphan hand-written projection
        # line would now trip the stricter (fail) drift check and mask what this class tests.
        self.w(os.path.join(self.store, "reference_x.md"),
               "---\nname: reference-x\ntitle: X\ndescription: y\n"
               "metadata:\n  type: reference\n  domains: [gcp]\n  basis: observed\n---\nb\n")
        subprocess.run([sys.executable, GEN], env=dict(os.environ, HOME=self.home), capture_output=True)

    def test_ok_when_no_projections(self):
        self.assertEqual(self.status(check="unmapped-with-projections"), "ok")

    def test_warn_when_projections_present_but_no_repo_mapped(self):
        # Unit 6: the fresh-clone / second-machine state — committed projections, empty map
        self._projection()
        rc, _, checks = self.doctor()
        self.assertEqual(checks["unmapped-with-projections"]["status"], "warn")
        self.assertEqual(rc, 0, "an onboarding warn must not fail the doctor (only 'fail' → exit 2)")

    def test_ok_when_a_repo_is_mapped(self):
        self._projection()
        self.w(os.path.join(self.store, ".domains.json"),
               json.dumps({"version": 1, "repos": {"/some/repo": ["gcp"]}}))
        self.assertEqual(self.status(check="unmapped-with-projections"), "ok")

    def test_ok_when_map_has_only_empty_entries(self):
        # a repo key present but with an empty domain list is not "mapped" → still warn
        self._projection()
        self.w(os.path.join(self.store, ".domains.json"),
               json.dumps({"version": 1, "repos": {"/some/repo": []}}))
        self.assertEqual(self.status(check="unmapped-with-projections"), "warn")


class CheckIgnore(Base):
    def test_solo_store_skips(self):
        self.assertEqual(self.status(check="check-ignore"), "skip")

    def _remote_store(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git not available")
        self.w(os.path.join(self.store, ".gitignore"),
               "MEMORY.local.md\nMEMORY.local.*.md\nlocal/\n.candidates.log\n.candidates.consumed\n"
               ".recall.log\n.runs.log\n.reflect/\n.probe-stamp.json\n.domains.json\n")
        subprocess.run([git, "-C", self.store, "init", "-q"])
        subprocess.run([git, "-C", self.store, "remote", "add", "origin", "x"])
        return git

    def test_fresh_remote_clone_with_absent_dirs_is_ok(self):
        # local/ and .reflect/ don't exist on a fresh clone — but they're gitignored, so OK.
        # (the M2 false-fail: a trailing-slash dir pattern against an absent bare name.)
        self._remote_store()
        rc, _, checks = self.doctor()
        self.assertEqual(checks["check-ignore"]["status"], "ok", checks["check-ignore"]["findings"])

    def test_tracked_machine_local_path_fails(self):
        git = self._remote_store()
        self.w(os.path.join(self.store, "MEMORY.local.md"), "personal\n")
        subprocess.run([git, "-C", self.store, "add", "-f", "MEMORY.local.md"])
        subprocess.run([git, "-C", self.store, "-c", "user.email=x@x", "-c", "user.name=x",
                        "commit", "-qm", "t"])
        _, _, checks = self.doctor()
        self.assertEqual(checks["check-ignore"]["status"], "fail")
        self.assertTrue(any("TRACKED" in f.get("issue", "") for f in checks["check-ignore"]["findings"]))

    def test_tracked_personal_domain_projection_fails(self):
        # the glob INVENTORY entry MEMORY.local.*.md must catch a tracked personal projection
        git = self._remote_store()
        self.w(os.path.join(self.store, "MEMORY.local.gcp.md"), "- [x](local/x.md) — d\n")
        subprocess.run([git, "-C", self.store, "add", "-f", "MEMORY.local.gcp.md"])
        subprocess.run([git, "-C", self.store, "-c", "user.email=x@x", "-c", "user.name=x",
                        "commit", "-qm", "t"])
        _, _, checks = self.doctor()
        self.assertEqual(checks["check-ignore"]["status"], "fail")
        self.assertTrue(any("MEMORY.local.*.md" in f.get("path", "") for f in checks["check-ignore"]["findings"]))


class MalformedSettings(Base):
    def test_malformed_settings_clean_fail_not_traceback(self):
        self.w(os.path.join(self.claude, "settings.json"), "{ not json")
        r = subprocess.run([sys.executable, INIT, "--doctor", "--no-probe"],
                           env=dict(os.environ, HOME=self.home), cwd=self.cwd,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not valid JSON", r.stderr)


if __name__ == "__main__":
    unittest.main()
