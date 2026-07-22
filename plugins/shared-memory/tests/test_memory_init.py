#!/usr/bin/env python3
"""Tests for memory-init — Layer 1: deterministic, no model calls, CI-ready.

Exercises install/uninstall mechanics: seeding, wiring, idempotency, foreign-content
preservation, malformed-input handling, the orphaned-sentinel guard, backup safety.
Most run memory-init as a subprocess against a throwaway HOME; the backup test imports
it for unit-level inspection. Run via `tests/run` or `python3 -m unittest discover -s tests`.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
INIT = os.path.join(PLUGIN, "bin", "memory-init")
PLUGIN_ID = importlib.machinery.SourceFileLoader("memory_init_probe", INIT).load_module().PLUGIN_ID

DOCTRINE = [
    "framework_memory_gate.md",
    "framework_a_guess_is_not_a_fact.md",
    "framework_write_for_the_cold_reader.md",
    "reference_store_memory_format.md",
    "framework_reflect_memories_back.md",
]
OWNED_ALLOW = [
    "Read(~/.claudemods/shared-memory/**)",
    "Edit(~/.claudemods/shared-memory/**)",
    "Read(~/.claude/projects/**)",
    "Read(//tmp/distill.*/**)",
    "Edit(//tmp/distill.*/**)",
    "Read(//private/tmp/distill.*/**)",
    "Edit(//private/tmp/distill.*/**)",
    "Read(//tmp/review.*/**)",
    "Edit(//tmp/review.*/**)",
    "Read(//private/tmp/review.*/**)",
    "Edit(//private/tmp/review.*/**)",
]
OWNED_ASK = []
# the redundant Write(...) rules the installer used to write and now strips (Edit covers Write)
OWNED_RETIRED = [
    "Write(~/.claudemods/shared-memory/**)",
    "Write(//tmp/distill.*/**)",
    "Write(//private/tmp/distill.*/**)",
    "Write(//tmp/review.*/**)",
    "Write(//private/tmp/review.*/**)",
]


class MemoryInit(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mi-test-")
        self.claude = os.path.join(self.home, ".claude")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")
        self.claude_md = os.path.join(self.claude, "CLAUDE.md")
        self.settings = os.path.join(self.claude, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    # helpers ("init", not "run" — TestCase.run is reserved by unittest)
    def init(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run([sys.executable, INIT, *args], env=env,
                              capture_output=True, text=True)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def perms(self):
        return json.loads(self.read(self.settings)).get("permissions", {})

    def count_backups(self):
        if not os.path.isdir(self.claude):
            return 0
        return len([f for f in os.listdir(self.claude) if ".smbak." in f])

    def snapshot(self):  # content of every non-backup file under .claude
        snap = {}
        for root, _, files in os.walk(self.claude):
            for fn in files:
                if ".smbak." in fn:
                    continue
                p = os.path.join(root, fn)
                with open(p, "rb") as f:
                    snap[os.path.relpath(p, self.claude)] = f.read()
        return snap

    # tests
    def test_install_seeds_store(self):
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stderr)
        for f in DOCTRINE + ["MEMORY.md", "MEMORY.local.md", ".gitignore"]:
            self.assertTrue(os.path.isfile(os.path.join(self.store, f)), f)
        self.assertTrue(os.path.isdir(os.path.join(self.store, "local", "archive")))

    def test_gitignore_merge_adds_missing_template_line(self):
        # an older store's .gitignore predates a template line (e.g. .candidates.consumed);
        # a re-run MERGES the missing line without disturbing the user's own entries
        os.makedirs(self.store)
        self.write(os.path.join(self.store, ".gitignore"),
                   "MEMORY.local.md\nlocal/\n.candidates.log\nmystuff/\n")  # missing .candidates.consumed
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stderr)
        gi = self.read(os.path.join(self.store, ".gitignore")).splitlines()
        self.assertIn(".candidates.consumed", gi)  # template line merged in
        self.assertIn("mystuff/", gi)              # user's own line preserved
        self.assertEqual(gi.count(".candidates.log"), 1, "existing line duplicated on merge")

    def test_install_wires_imports(self):
        self.init()
        text = self.read(self.claude_md)
        self.assertIn("<!-- shared-memory:begin -->", text)
        self.assertIn("<!-- shared-memory:end -->", text)
        self.assertIn("@~/.claudemods/shared-memory/MEMORY.md", text)
        self.assertIn("@~/.claudemods/shared-memory/MEMORY.local.md", text)

    def test_install_adds_exactly_the_owned_rules(self):
        self.init()
        perms = self.perms()
        for rule in OWNED_ALLOW:
            self.assertIn(rule, perms["allow"])
        for rule in OWNED_ASK:
            self.assertIn(rule, perms["ask"])

    def test_idempotent_second_install_is_noop(self):
        self.init()
        before, n = self.snapshot(), self.count_backups()
        self.init()
        self.assertEqual(before, self.snapshot(), "second install changed files")
        self.assertEqual(n, self.count_backups(), "no-op install left a backup")

    def test_install_strips_retired_write_rules(self):
        # a settings.json written before the Write rules were retired must self-heal on --install
        # (install is append-only otherwise, so this is the load-bearing convergence fix)
        self.write(self.settings, json.dumps({"permissions": {
            "allow": OWNED_RETIRED + ["Bash(ls:*)"], "ask": []}}))
        self.init()
        allow = self.perms()["allow"]
        for r in OWNED_RETIRED:
            self.assertNotIn(r, allow, f"retired rule {r} survived --install")
        self.assertIn("Bash(ls:*)", allow, "a foreign rule was collateral-stripped")
        for r in OWNED_ALLOW:
            self.assertIn(r, allow, "the current owned rules weren't wired")

    def test_uninstall_removes_retired_rules_too(self):
        # ours() covers RETIRED_ALLOW, so uninstall cleans a Write rule an older version wrote
        self.init()
        s = json.loads(self.read(self.settings))
        s["permissions"]["allow"].append("Write(~/.claudemods/shared-memory/**)")  # simulate an old rule
        self.write(self.settings, json.dumps(s))
        self.init("--uninstall")
        # nothing foreign was in the file, so uninstall removes it entirely ([11] sibling); either
        # way the retired rule must be gone (absent file → empty allow)
        allow = (json.loads(self.read(self.settings)).get("permissions", {}).get("allow", [])
                 if os.path.exists(self.settings) else [])
        self.assertNotIn("Write(~/.claudemods/shared-memory/**)", allow, "uninstall orphaned a retired rule")

    def test_foreign_content_preserved(self):
        self.write(self.claude_md, "# my notes\n\nbe concise\n")
        self.write(self.settings, json.dumps(
            {"model": "foreign", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(./.env)"]}}))
        self.init()
        self.assertIn("# my notes", self.read(self.claude_md))
        s = json.loads(self.read(self.settings))
        self.assertEqual(s["model"], "foreign")
        self.assertIn("Bash(ls:*)", s["permissions"]["allow"])
        self.assertIn("Read(./.env)", s["permissions"]["deny"])

        self.init("--uninstall")
        self.assertIn("# my notes", self.read(self.claude_md))
        self.assertNotIn("shared-memory:begin", self.read(self.claude_md))
        s = json.loads(self.read(self.settings))
        self.assertEqual(s["model"], "foreign")
        self.assertIn("Bash(ls:*)", s["permissions"]["allow"])
        for rule in OWNED_ALLOW:
            self.assertNotIn(rule, s["permissions"].get("allow", []))

    def test_uninstall_spares_foreign_rules_sharing_our_substrings(self):
        # C1 regression: exact-match ownership, never a substring test
        self.write(self.settings, json.dumps({"permissions": {"allow": [
            "Read(~/work/shared-memory-notes/**)", "Read(/tmp/distill.mine/**)"]}}))
        self.init()
        self.init("--uninstall")
        allow = self.perms()["allow"]
        self.assertIn("Read(~/work/shared-memory-notes/**)", allow)
        self.assertIn("Read(/tmp/distill.mine/**)", allow)

    def test_malformed_settings_aborts_clean(self):
        self.write(self.settings, "{bad json")
        r = self.init()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not valid JSON", r.stderr)
        self.assertFalse(os.path.exists(self.store), "store created despite abort")
        self.assertEqual(self.read(self.settings), "{bad json", "settings touched")

    def test_install_creates_no_empty_ask_key(self):
        # [11]: installing into a foreign settings with no "ask" must NOT create an empty "ask": []
        self.write(self.settings, json.dumps({"model": "x", "permissions": {"allow": ["Foo(bar)"]}}))
        self.assertEqual(self.init().returncode, 0)
        perms = self.perms()
        self.assertNotIn("ask", perms, f"empty ask key created: {perms}")
        self.assertIn("Foo(bar)", perms["allow"], "foreign allow rule dropped")

    def test_settings_roundtrip_leaves_no_residual(self):
        # [11]: a foreign settings.json survives install+uninstall byte-identical (no ask residual)
        orig = {"model": "x", "permissions": {"allow": ["Foo(bar)"], "deny": ["Bad(*)"]}}
        self.write(self.settings, json.dumps(orig, indent=2))
        self.assertEqual(self.init().returncode, 0)
        self.assertEqual(self.init("--uninstall").returncode, 0)
        self.assertEqual(json.loads(self.read(self.settings)), orig, "round-trip changed foreign settings")

    def test_uninstall_warns_when_plugin_still_enabled(self):
        # [R9 W-6/H-uninstall]: --uninstall is wiring-only; the plugin's hooks stay live until it is
        # disabled. When the plugin still appears enabled, uninstall must WARN + print the two-step
        # teardown. cwd=self.home so an ambient project settings.json can't flip plugin_appears_enabled.
        self.write(self.settings, json.dumps({"enabledPlugins": {PLUGIN_ID: True},
                                              "permissions": {"allow": [], "ask": []}}))
        self.write(self.claude_md, "# user\n")
        r = subprocess.run([sys.executable, INIT, "--uninstall"],
                           env=dict(os.environ, HOME=self.home), cwd=self.home, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("still appears ENABLED", r.stdout)
        self.assertIn("disable/uninstall the plugin", r.stdout)

    def test_uninstall_no_enabled_warning_when_plugin_absent(self):
        # plugin not enabled → no "still ENABLED" warning, but the two-step teardown still prints
        self.write(self.settings, json.dumps({"permissions": {"allow": [], "ask": []}}))
        self.write(self.claude_md, "# user\n")
        r = subprocess.run([sys.executable, INIT, "--uninstall"],
                           env=dict(os.environ, HOME=self.home), cwd=self.home, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("still appears ENABLED", r.stdout)
        self.assertIn("To fully remove shared-memory", r.stdout)

    def test_wire_preserves_symlinked_claudemd(self):
        # [R4-A]: a symlinked CLAUDE.md (dotfiles) must be written THROUGH, not detached into a
        # fresh regular file — the block must reach the symlink's real target
        os.makedirs(self.claude, exist_ok=True)
        target = os.path.join(self.home, "dotfiles_claude.md")
        self.write(target, "# dotfiles config\n")
        os.symlink(target, self.claude_md)
        self.assertEqual(self.init().returncode, 0)
        self.assertTrue(os.path.islink(self.claude_md), "symlink detached into a regular file")
        self.assertIn("shared-memory:begin", self.read(target), "import block did not reach the symlink target")
        self.assertIn("# dotfiles config", self.read(target))

    def test_atomic_write_preserves_file_on_failed_replace(self):
        # [13] fault injection: if the rename fails mid-write, the live CLAUDE.md must be UNCHANGED
        # (a direct open('w') would already have truncated it) — proves atomicity, not just mechanism
        from importlib.machinery import SourceFileLoader
        self.write(self.claude_md, "# important user notes\n")
        old_home = os.environ.get("HOME"); os.environ["HOME"] = self.home
        try:
            mod = SourceFileLoader("mi_atomic_probe", INIT).load_module()
            real_replace = os.replace
            def boom(*a):
                raise OSError("injected mid-write failure")
            os.replace = boom
            try:
                mod.wire_imports()
            except OSError:
                pass
            finally:
                os.replace = real_replace
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
        self.assertEqual(self.read(self.claude_md), "# important user notes\n",
                         "CLAUDE.md truncated/changed by a failed atomic write")

    def test_uninstall_removes_settings_we_created(self):
        # [11] sibling: install+uninstall starting from NO settings.json must leave NO settings.json
        # (not a residual {} the user never had) — mirrors the CLAUDE.md path
        self.assertFalse(os.path.exists(self.settings))
        self.assertEqual(self.init().returncode, 0)
        self.assertTrue(os.path.exists(self.settings), "install did not create settings.json")
        self.assertEqual(self.init("--uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.settings), "uninstall left a residual empty settings.json")

    def test_claudemd_written_atomically_no_tmp_leftover(self):
        # [13]: wire/unwire write CLAUDE.md via tmp+os.replace — content correct, no .tmp sibling
        # left. (Crash-atomicity itself has no deterministic Layer-1 test; this guards the mechanism.)
        self.write(self.claude_md, "# my notes\n")
        self.assertEqual(self.init().returncode, 0)
        wired = self.read(self.claude_md)
        self.assertIn("# my notes", wired)
        self.assertIn("shared-memory:begin", wired)
        self.assertFalse(os.path.exists(self.claude_md + ".tmp"), "atomic-write .tmp left behind (install)")
        self.assertEqual(self.init("--uninstall").returncode, 0)
        self.assertEqual(self.read(self.claude_md).strip(), "# my notes", "uninstall didn't restore cleanly")
        self.assertFalse(os.path.exists(self.claude_md + ".tmp"), "atomic-write .tmp left behind (uninstall)")

    def test_non_object_settings_aborts_clean(self):
        # [12]: valid JSON but not an object (list / null) must abort BEFORE seeding, no traceback
        for content in ("[1, 2, 3]", "null", '"a string"'):
            self.write(self.settings, content)
            r = self.init()
            self.assertNotEqual(r.returncode, 0, content)
            self.assertNotIn("Traceback", r.stderr, content)
            self.assertIn("not an object", r.stderr, content)
            self.assertFalse(os.path.exists(self.store), f"store created despite bad settings {content!r}")

    def test_unreadable_claudemd_aborts_clean_before_seeding(self):
        # [7]: a directory at CLAUDE.md is caught by the install preflight BEFORE any mutation
        os.makedirs(self.claude_md)   # CLAUDE.md is a directory → unreadable
        r = self.init()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not readable", r.stderr)
        self.assertFalse(os.path.exists(self.store), "store seeded despite an unreadable CLAUDE.md")

    def test_orphaned_begin_block_left_untouched(self):
        text = "# notes\n<!-- shared-memory:begin -->\n@x\nimportant tail\n"  # no END
        self.write(self.claude_md, text)
        r = self.init("--uninstall")
        self.assertEqual(self.read(self.claude_md), text, "orphaned-begin file was edited")
        self.assertIn("refusing to edit", r.stderr)


class Backup(unittest.TestCase):
    """Unit-level: backup() never clobbers an earlier backup (C3 regression)."""
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("memory_init", INIT)
        spec = importlib.util.spec_from_loader("memory_init", loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)
        self.dir = tempfile.mkdtemp(prefix="mi-bak-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_clobber_within_one_second(self):
        p = os.path.join(self.dir, "settings.json")
        self.write(p, "v1"); self.mod.backup(p)   # -> .smbak.TS
        self.write(p, "v2"); self.mod.backup(p)   # same TS -> .smbak.TS.1
        baks = [f for f in os.listdir(self.dir) if ".smbak." in f]
        self.assertEqual(len(baks), 2, baks)
        contents = []
        for b in baks:
            with open(os.path.join(self.dir, b)) as fh:
                contents.append(fh.read())
        self.assertEqual(sorted(contents), ["v1", "v2"], "an earlier backup was clobbered")

    def write(self, p, t):
        with open(p, "w") as f:
            f.write(t)


class WiringSourceOfTruth(unittest.TestCase):
    """OWNED_ALLOW/OWNED_ASK are a deliberate second witness of the installer's wiring — but
    they must never silently diverge from the real ALLOW/ASK the installer (and the doctor)
    use. This test is that guard: edit ALLOW without updating OWNED_ALLOW and it fails."""
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("memory_init", INIT)
        spec = importlib.util.spec_from_loader("memory_init", loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

    def test_owned_allow_matches_installer(self):
        self.assertEqual(list(self.mod.ALLOW), OWNED_ALLOW, "ALLOW drifted from the test's OWNED_ALLOW witness")
        self.assertEqual(list(self.mod.ASK), OWNED_ASK, "ASK drifted from the test's OWNED_ASK witness")
        self.assertEqual(list(self.mod.RETIRED_ALLOW), OWNED_RETIRED, "RETIRED_ALLOW drifted from its witness")

    def test_no_write_rule_in_allow(self):
        # regression: Write(path) allow rules are no-ops the CLI warns about — ALLOW must carry none
        self.assertFalse([r for r in self.mod.ALLOW if r.startswith("Write(")],
                         "a Write(...) allow rule crept back into ALLOW (Edit covers Write)")

    def test_retired_rules_have_edit_counterparts(self):
        # every retired Write(path) must keep its Edit(path) in ALLOW, or write coverage regresses
        for w in self.mod.RETIRED_ALLOW:
            e = w.replace("Write(", "Edit(", 1)
            self.assertIn(e, self.mod.ALLOW, f"retiring {w} left no {e} to cover the write")


if __name__ == "__main__":
    unittest.main()
