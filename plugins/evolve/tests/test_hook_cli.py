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
        out = json.loads(cli(self.root, "sample", "--k", "2", "--seed", "r1", check=True).stdout)
        slots = out["slots"]
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]["parent"], "gen0-baseline")
        # assignments persisted before dispatch — and re-sampling the same seed on the same
        # archive is idempotent, not an error
        saved = json.loads((self.root / out["slots_file"]).read_text())
        self.assertEqual(saved["slots"], slots)
        cli(self.root, "sample", "--k", "2", "--seed", "r1", check=True)
        # a second round needs its own seed (each round's assignments are immutable evidence);
        # --seed is required so a bare re-sample can't collide with round 1's file
        cli(self.root, "sample", "--k", "2", "--seed", "r2", check=True)
        r = cli(self.root, "sample", "--k", "3", "--seed", "r1")   # same seed, different --k
        self.assertEqual(r.returncode, 1)
        self.assertIn("different --k", r.stderr)
        text = cli(self.root, "brief", "--parent", "gen0-baseline", check=True).stdout
        self.assertIn("TASK:", text)
        self.assertIn("LEADERBOARD", cli(self.root, "board", check=True).stdout)

    def test_score_via_patch_and_apply(self):
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
        # --dry is view-only: prints the artifact ITSELF (diff inline), applies nothing.
        # Retrieval carries no ceremony: no records, no gates — shipping is outside the engine.
        dry = json.loads(cli(self.root, "apply", "--id", "g1", "--dry", check=True).stdout)
        self.assertEqual(dry["patch"], "evolve/" + rec["surface"]["patch"])   # root-relative
        self.assertIn("return 2.0", dry["diff"])
        self.assertNotIn("return 2.0", (self.root / "src" / "algo.py").read_text())
        self.assertFalse((self.root / "evolve" / "adoption").exists())        # nothing recorded
        out = json.loads(cli(self.root, "apply", "--id", "g1", check=True).stdout)
        self.assertIn("return 2.0", (self.root / "src" / "algo.py").read_text())
        self.assertEqual(out["applied"], rec["surface"]["patch"])
        self.assertFalse((self.root / "evolve" / "adoption").exists())        # still nothing
        git(["checkout", "--", "."], self.root)
        # a retracted id still materializes, with a factual note that its scores are untrusted
        rf = self.root / "retract.json"
        rf.write_text('{"retract": true, "fitness": null, "guardrail": "revoked"}')
        cli(self.root, "ingest", "--result", str(rf), "--id", "g1", "--mode", "proxy", check=True)
        dry2 = json.loads(cli(self.root, "apply", "--id", "g1", "--dry", check=True).stdout)
        self.assertIn("not trusted", dry2["note"])
        self.assertIn("return 2.0", dry2["diff"])                             # artifact still served
        # the seed is viewable too — nothing to apply, says so
        out3 = json.loads(cli(self.root, "apply", "--id", "gen0-baseline", "--dry", check=True).stdout)
        self.assertIn("unmodified baseline", out3["note"])
        # ... and stays viewable after its own retraction: the retraction record carries no
        # surface, so the seed must be identified from ANY of the id's records, not the newest
        rf.write_text('{"retract": true, "fitness": null, "guardrail": "eval invalid"}')
        cli(self.root, "ingest", "--result", str(rf), "--id", "gen0-baseline", "--mode", "proxy",
            check=True)
        out4 = json.loads(cli(self.root, "apply", "--id", "gen0-baseline", "--dry", check=True).stdout)
        self.assertIn("not trusted", out4["note"])
        self.assertIn("unmodified baseline", out4["note"])

    def test_retract_verb_and_reinstate_flow(self):
        cli(self.root, "seed", check=True)
        out = json.loads(cli(self.root, "retract", "--id", "gen0-baseline", "--reason",
                             "eval invalid", check=True).stdout)
        self.assertEqual(out["reason"], "eval invalid")
        self.assertIn("no selectable candidates", cli(self.root, "board", check=True).stdout)
        rf = self.root / "res.json"
        rf.write_text('{"fitness": 0.9}')
        r = cli(self.root, "ingest", "--result", str(rf), "--id", "gen0-baseline", "--mode", "proxy")
        self.assertEqual(r.returncode, 1)                          # a wave can't launder it
        self.assertIn("retracted", r.stderr)
        cli(self.root, "ingest", "--result", str(rf), "--id", "gen0-baseline", "--mode", "proxy",
            "--reinstate", check=True)                             # reinstating is a decision
        self.assertIn("gen0-baseline", cli(self.root, "board", check=True).stdout)

    def test_retract_split_guards(self):
        cli(self.root, "seed", check=True)
        r = cli(self.root, "retract", "--id", "gen0-baseline", "--reason", "x", "--split", "fnal")
        self.assertEqual(r.returncode, 2)                          # typo'd split: refused, not
        self.assertIn("unknown --split", r.stderr)                 # silently mis-partitioned
        wt = make_worktree(self.root, "fv")
        try:
            set_factor(wt, 2.0)
            cli(self.root, "score", "--candidate", str(wt), "--id", "c1", "--mode", "full",
                "--split", "final", check=True)
        finally:
            drop_worktree(self.root, wt)
        r = cli(self.root, "retract", "--id", "c1", "--reason", "x", "--split", "final")
        self.assertEqual(r.returncode, 1)                          # registers in NO view: refused
        self.assertIn("final split", r.stderr)

    def test_impls_omits_retired(self):
        from _toy import make_registry_project
        root = make_registry_project()
        try:
            cli(root, "seed", check=True)
            out = json.loads(cli(root, "impls", check=True).stdout)
            self.assertIn("baseline", out["objective"])
            (root / "evolve" / "retired_impls.json").write_text(json.dumps(
                {"retired": {"objective/baseline": {"reason": "r"}}}))
            out = json.loads(cli(root, "impls", check=True).stdout)
            self.assertNotIn("baseline", out["objective"])         # not a live option
        finally:
            cleanup(root)

    def test_sample_dry_persists_nothing(self):
        cli(self.root, "seed", check=True)
        out = json.loads(cli(self.root, "sample", "--k", "2", "--seed", "diag", "--dry",
                             check=True).stdout)
        self.assertTrue(out["dry"])
        self.assertFalse((self.root / "evolve" / "rounds").exists())   # diagnostics leave no trace

    def test_jail_build_and_leak_refusal(self):
        from _toy import make_registry_project
        import shutil, tempfile
        root = make_registry_project()
        jail_root = pathlib.Path(tempfile.mkdtemp(prefix="evolve-test-jails-"))
        try:
            cli(root, "seed", check=True)
            # inventor_files is required and declared, not derived
            r = cli(root, "jail", "--parent", "gen0-baseline", "--axis", "objective",
                    "--round", "r1", "--slot", "0", "--jail-root", str(jail_root))
            self.assertEqual(r.returncode, 1)
            self.assertIn("inventor_files", r.stderr)
            (root / "helper.py").write_text("def helper():\n    return 41\n")
            cfg = json.loads((root / "evolve" / "evolve.json").read_text())
            (root / "sub").mkdir()
            (root / "sub" / "helper2.py").write_text("def h2():\n    return 42\n")
            cfg["surface"]["registry"]["inventor_files"] = ["helper.py", "sub/helper2.py"]
            cfg["surface"]["registry"]["jail_notes"] = "MACHINE BUDGET: toy."
            (root / "evolve" / "evolve.json").write_text(json.dumps(cfg))
            # a jail root inside the repo is refused — a committed jail publishes the brief
            r = cli(root, "jail", "--parent", "gen0-baseline", "--axis", "objective",
                    "--round", "r1", "--slot", "0", "--jail-root", str(root / "jails"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("INSIDE the repo", r.stderr)
            out = json.loads(cli(root, "jail", "--parent", "gen0-baseline", "--axis",
                                 "objective", "--round", "r1", "--slot", "0",
                                 "--jail-root", str(jail_root), check=True).stdout)
            j = pathlib.Path(out["jail"])
            self.assertTrue((j / "BRIEF.md").exists())
            self.assertTrue((j / "helper.py").exists())
            self.assertTrue((j / "sub" / "helper2.py").exists())   # repo-relative path kept
            self.assertTrue((j / "evolve" / "chunks" / "objective" / "baseline.py").exists())
            self.assertTrue((j / "PROPOSAL").is_dir())
            # jail_notes ride INSIDE the gated brief, never around it
            self.assertIn("MACHINE BUDGET: toy.", (j / "BRIEF.md").read_text())
            self.assertNotIn("MACHINE BUDGET", (j / "JAIL_README.md").read_text())
            # the whole point: no archive, no config, no docs in the jail
            self.assertFalse((j / "evolve" / "archive").exists())
            self.assertFalse((j / "evolve" / "evolve.json").exists())
            # a leak in a copied source refuses the build AND preserves in-flight work
            (j / "PROPOSAL" / "work.py").write_text("in flight")
            (root / "helper.py").write_text("# see candidate gen0-baseline and g9\n")
            wt = make_worktree(root, "g9w")                       # archive a candidate id g9
            r = cli(root, "jail", "--parent", "gen0-baseline", "--axis", "objective",
                    "--round", "r1", "--slot", "0", "--jail-root", str(jail_root))
            self.assertEqual(r.returncode, 1)                     # exists, no --force
            drop_worktree(root, wt)
            (root / "evolve" / "chunks" / "objective" / "v9.py").write_text("x = 1\n")
            from _evolve import archive as A
            A.append(root, {"id": "g9", "mode": "proxy", "split": "inner", "fitness": 1.5,
                            "generation": 1, "parent": None,
                            "genome": {"chunks": {"objective": {"impl": "v9"}}}})
            r = cli(root, "jail", "--parent", "gen0-baseline", "--axis", "objective",
                    "--round", "r1", "--slot", "0", "--jail-root", str(jail_root), "--force")
            self.assertEqual(r.returncode, 1)                     # id leak in helper.py
            self.assertIn("candidate-id leak", r.stderr)
            self.assertTrue((j / "PROPOSAL" / "work.py").exists())   # staging swap: in-flight
            self.assertTrue((j / "BRIEF.md").exists())               # work survived the refusal
        finally:
            shutil.rmtree(jail_root, ignore_errors=True)
            cleanup(root)

    def test_apply_prefers_authoritative_artifact(self):
        # ingest has no payload check, so a later ingested record can attach a DIFFERENT
        # genome to an id — apply must serve the artifact of the record whose fitness it
        # claims, not whichever record was appended last
        from _toy import make_registry_project
        root = make_registry_project()
        try:
            cli(root, "seed", check=True)
            foreign = root / "foreign_genome.json"
            foreign.write_text(json.dumps(
                {"chunks": {"objective": {"impl": "baseline", "params": {"x": 1}}}}))
            rf = root / "res.json"
            rf.write_text('{"fitness": null}')
            cli(root, "ingest", "--result", str(rf), "--id", "gen0-baseline",
                "--genome", str(foreign), "--mode", "proxy", check=True)
            out = json.loads(cli(root, "apply", "--id", "gen0-baseline", "--dry", check=True).stdout)
            # the scored record's genome (empty params), not the foreign ingested one ({"x": 1})
            self.assertEqual(out["genome"]["chunks"]["objective"].get("params", {}), {})
        finally:
            cleanup(root)

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
        r = cli(self.root, "sample", "--seed", "r1")
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
