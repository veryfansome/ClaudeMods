import json
import os
import pathlib
import subprocess
import unittest

from _toy import (PLUGIN, make_markers_project, make_worktree, drop_worktree, set_factor,
                  load_cfg, cleanup, git)
from _evolve import archive, config as cfgmod, score

HOOK = PLUGIN / "scripts" / "protect-paths"
CLI = PLUGIN / "bin" / "evolve"


def run_hook(root, file_path, env=None):
    payload = json.dumps({"tool_name": "Edit", "cwd": str(root),
                          "tool_input": {"file_path": str(file_path)}})
    return subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True,
                          env={**os.environ, **(env or {})})


def cli(root, *args, check=False):
    r = subprocess.run([str(CLI), "--root", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"evolve {' '.join(args)} -> {r.returncode}\n{r.stderr}")
    return r


class TestHook(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        # Arm the hook: it is deliberately disarmed until a campaign starts (so /evolve:init
        # can write the config it generates). Create the archive to simulate a live campaign.
        (self.root / "evolve" / "archive").mkdir(parents=True, exist_ok=True)
        (self.root / "evolve" / "archive" / "genomes.jsonl").write_text("")

    def tearDown(self):
        cleanup(self.root)

    def test_blocks_protected(self):
        r = run_hook(self.root, self.root / "eval.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("protected path", r.stderr)
        r = run_hook(self.root, self.root / "evolve" / "archive" / "genomes.jsonl")
        self.assertEqual(r.returncode, 2)

    def test_allows_unprotected_and_override(self):
        self.assertEqual(run_hook(self.root, self.root / "src" / "algo.py").returncode, 0)
        r = run_hook(self.root, self.root / "eval.py", env={"EVOLVE_ALLOW_PROTECTED": "1"})
        self.assertEqual(r.returncode, 0)

    def test_disarmed_before_campaign(self):
        (self.root / "evolve" / "archive" / "genomes.jsonl").unlink()   # no campaign yet
        self.assertEqual(run_hook(self.root, self.root / "eval.py").returncode, 0)

    def test_allows_new_registry_impl(self):
        from _toy import registry_config
        import json as _json
        (self.root / "evolve" / "evolve.json").write_text(_json.dumps(registry_config()))
        # a NEW file under the registry dir is the round's normal work, not tampering
        target = self.root / "evolve" / "chunks" / "objective" / "fresh.py"
        self.assertEqual(run_hook(self.root, target).returncode, 0)

    def test_noop_without_config(self):
        (self.root / "evolve" / "evolve.json").unlink()
        self.assertEqual(run_hook(self.root, self.root / "eval.py").returncode, 0)

    def test_noop_on_garbage_stdin(self):
        r = subprocess.run([str(HOOK)], input="not json", text=True, capture_output=True)
        self.assertEqual(r.returncode, 0)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()

    def tearDown(self):
        cleanup(self.root)

    def test_seed_sample_brief_board(self):
        out = cli(self.root, "seed", check=True).stdout
        self.assertEqual(json.loads(out)["fitness"], 1.0)
        slots = json.loads(cli(self.root, "sample", "--k", "2", check=True).stdout)["slots"]
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]["parent"], "gen0-baseline")
        text = cli(self.root, "brief", "--parent", "gen0-baseline", check=True).stdout
        self.assertIn("TASK:", text)
        self.assertIn("LEADERBOARD", cli(self.root, "board", check=True).stdout)

    def test_score_via_patch_and_adopt(self):
        cli(self.root, "seed", check=True)
        wt = make_worktree(self.root, "cli")
        try:
            set_factor(wt, 2.0)
            patch = git(["diff", "HEAD"], wt)
        finally:
            drop_worktree(self.root, wt)
        pf = self.root / "cand.patch"
        pf.write_text(patch)
        rec = json.loads(cli(self.root, "score", "--patch", str(pf), "--id", "g1",
                             "--parent", "gen0-baseline", check=True).stdout)
        self.assertEqual(rec["fitness"], 2.0)
        pf.unlink()
        git(["add", "-A"], self.root); git(["commit", "-q", "-m", "archive"], self.root)
        cli(self.root, "adopt", "--id", "g1", check=True)
        self.assertIn("return 2.0", (self.root / "src" / "algo.py").read_text())

    def test_guard_and_duplicate_exit_codes(self):
        cli(self.root, "seed", check=True)
        wt = make_worktree(self.root, "viol")
        try:
            (wt / "eval.py").write_text("gamed\n")
            set_factor(wt, 9.0)
            r = cli(self.root, "score", "--candidate", str(wt), "--id", "bad")
            self.assertEqual(r.returncode, 4)
        finally:
            drop_worktree(self.root, wt)
        for i, expect in (("d1", 0), ("d2", 5)):                  # second is a near-dup
            wt = make_worktree(self.root, i)
            try:
                set_factor(wt, 3.0)
                r = cli(self.root, "score", "--candidate", str(wt), "--id", i)
                self.assertEqual(r.returncode, expect, r.stderr)
            finally:
                drop_worktree(self.root, wt)

    def test_budget_exhaustion_exit_3(self):
        cfg = load_cfg(self.root)
        cfg["budget"]["max_generations"] = 0
        cfgmod.save(cfg, self.root, backup=False)
        cli(self.root, "seed", check=True)
        r = cli(self.root, "sample")
        self.assertEqual(r.returncode, 3)
        self.assertIn("budget exhausted", r.stderr)

    def test_rescore_verb(self):
        cli(self.root, "seed", check=True)
        wt = make_worktree(self.root, "r")
        try:
            set_factor(wt, 2.0)
            cli(self.root, "score", "--candidate", str(wt), "--id", "g1", "--parent",
                "gen0-baseline", check=True)
        finally:
            drop_worktree(self.root, wt)
        rec = json.loads(cli(self.root, "rescore", "--id", "g1", "--mode", "full", check=True).stdout)
        self.assertEqual(rec["mode"], "full")
        self.assertEqual(rec["id"], "g1")

    def test_rescore_guard_rejection_exit_4(self):
        # A rescore that can't reproduce (ingested markers record) must exit as a clean error,
        # not an uncaught traceback. (cmd_rescore has no local Rejection handler — main() owns it.)
        cli(self.root, "seed", check=True)
        r = cli(self.root, "rescore", "--id", "nonexistent", "--mode", "full")
        self.assertEqual(r.returncode, 1)                          # graceful {"error"}, no traceback
        self.assertIn("error", r.stdout + r.stderr)

    def test_unknown_split_rejected(self):
        cli(self.root, "seed", check=True)
        r = cli(self.root, "seed", "--id", "typo", "--split", "finaL")   # typo defeats firewall
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown --split", r.stderr)

    def test_doctor_exit_codes(self):
        self.assertEqual(cli(self.root, "doctor").returncode, 0)
        p = self.root / "src" / "algo.py"
        p.write_text("def factor():\n    return 1.0\n")           # markers gone
        git(["add", "-A"], self.root); git(["commit", "-q", "-m", "strip"], self.root)
        self.assertEqual(cli(self.root, "doctor").returncode, 2)


if __name__ == "__main__":
    unittest.main()
