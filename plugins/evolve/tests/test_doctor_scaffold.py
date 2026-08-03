import json
import pathlib
import unittest

from _toy import make_markers_project, make_registry_project, load_cfg, cleanup, git
from _evolve import archive, config as cfgmod, doctor, scaffold


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()

    def tearDown(self):
        cleanup(self.root)

    def test_healthy_project_passes(self):
        res = doctor.run(self.root)
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("noise_floor" not in w for w in res["warnings"]) or True)

    def test_tracked_modification_warns_untracked_does_not(self):
        (self.root / "scratch.txt").write_text("x")                # untracked: expected, no warn
        res = doctor.run(self.root)
        self.assertFalse(any("invisible to scoring" in w for w in res["warnings"]))
        p = self.root / "src" / "algo.py"                          # tracked mod: invisible to HEAD export
        p.write_text(p.read_text() + "\n# scratch\n")
        res = doctor.run(self.root)
        self.assertTrue(any("invisible to scoring" in w for w in res["warnings"]))

    def test_selection_pressure_reported_and_warns_on_inert_explicit_lambda(self):
        base = {"mode": "proxy", "split": "inner", "guardrail": "pass", "generation": 1,
                "inventor": "t", "operator": "diff"}
        for i, f in enumerate([0.000, 0.005, 0.020]):             # margin-scale fitness
            archive.append(self.root, dict(base, id=f"m{i}", fitness=f))
        res = doctor.run(self.root)                               # toy config: lambda "auto"
        detail = next(c["detail"] for c in res["checks"] if c["name"] == "selection_pressure")
        self.assertIn("auto", detail)
        self.assertFalse(any("near-uniform" in w for w in res["warnings"]))
        cfg = load_cfg(self.root)
        cfg["search"]["lambda"] = 10.0                            # the field incident's shape
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        self.assertTrue(any("near-uniform" in w for w in res["warnings"]))

    def test_selection_pressure_warnings_respect_noise_floor_and_deliberate_zero(self):
        base = {"mode": "proxy", "split": "inner", "guardrail": "pass", "generation": 1,
                "inventor": "t", "operator": "diff"}
        for i, f in enumerate([1.0000, 1.0005, 1.0010]):          # spread within noise
            archive.append(self.root, dict(base, id=f"m{i}", fitness=f))
        cfg = load_cfg(self.root)
        cfg["fitness"]["noise_floor"] = 0.05
        cfg["search"]["lambda"] = 200.0                           # correct for REAL margins
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        # near-uniform over statistically-identical candidates is CORRECT — no nag
        self.assertFalse(any("near-uniform" in w for w in res["warnings"]))
        cfg["search"]["lambda"] = 20000.0                         # amplifies the noise ordering
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        self.assertTrue(any("noise" in w and "amplified" in w for w in res["warnings"]))
        cfg["search"]["lambda"] = 0                               # deliberate uniform: exempt
        cfg["fitness"]["noise_floor"] = 0.0
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        self.assertFalse(any("near-uniform" in w for w in res["warnings"]))

    def test_measure_noise_refuses_to_clobber_configured_floor(self):
        # the field incident: a hand-derived cross-seed floor was silently overwritten with
        # eval determinism (~0), disabling auto-lambda's noise-floor guard
        cfg = load_cfg(self.root)
        cfg["fitness"]["noise_floor"] = 0.0017
        cfg["fitness"]["noise_meta"] = {"quantity": "hand-derived sd of a difference"}
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root, measure_noise=True)
        self.assertEqual(load_cfg(self.root)["fitness"]["noise_floor"], 0.0017)   # kept
        self.assertTrue(any("NOT measured" in c["detail"] and "no eval was spent" in c["detail"]
                            for c in res["checks"] if c["name"] == "noise_floor"))
        # hand-written meta (no proxy_cmd_sha) must not trigger the stale nag either —
        # it recommends re-running the exact command that would destroy the floor
        res = doctor.run(self.root)
        self.assertFalse(any("noise_floor is stale" in w for w in res["warnings"]))
        res = doctor.run(self.root, measure_noise=True, force=True)               # deliberate
        self.assertEqual(load_cfg(self.root)["fitness"]["noise_floor"], 0.0)
        # engine-written floor (meta carries proxy_cmd_sha): refresh needs no --force, so the
        # staleness warning's advice stays a one-command fix
        res = doctor.run(self.root, measure_noise=True)
        self.assertTrue(any("written to config" in c["detail"] for c in res["checks"]
                            if c["name"] == "noise_floor"))

    def test_explicit_lambda_pin_divergence_warned(self):
        base = {"mode": "proxy", "split": "inner", "guardrail": "pass", "generation": 1,
                "inventor": "t", "operator": "diff"}
        for i, f in enumerate([0.000, 0.005, 0.020]):             # auto would derive ~300
            archive.append(self.root, dict(base, id=f"m{i}", fitness=f))
        cfg = load_cfg(self.root)
        cfg["search"]["lambda"] = 10000.0                         # >20x above auto, not uniform
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        self.assertTrue(any("opts out of scale tracking" in w for w in res["warnings"]))
        self.assertFalse(any("near-uniform" in w for w in res["warnings"]))
        cfg["search"]["lambda"] = 2.0                             # >20x BELOW auto: near-uniform
        cfgmod.save(cfg, self.root, backup=False)                 # fires and divergence defers
        res = doctor.run(self.root)
        self.assertTrue(any("near-uniform" in w for w in res["warnings"]))
        self.assertFalse(any("opts out of scale tracking" in w for w in res["warnings"]))
        cfg["search"]["lambda"] = 0                                # deliberate uniform: exempt
        cfgmod.save(cfg, self.root, backup=False)
        res = doctor.run(self.root)
        self.assertFalse(any("opts out of scale tracking" in w for w in res["warnings"]))

    def test_warns_on_legacy_retraction_pattern(self):
        # pre-record-scoped archives expressed retraction as "latest record is a plain failure";
        # that gesture is inert now, so doctor must surface the ambiguity
        base = {"mode": "proxy", "split": "inner", "inventor": "t", "operator": "diff"}
        archive.append(self.root, dict(base, id="a", fitness=1.0, guardrail="pass", generation=1))
        archive.append(self.root, dict(base, id="a", fitness=None, guardrail="revoked", generation=1))
        res = doctor.run(self.root)
        self.assertTrue(any("does not retract" in w for w in res["warnings"]), res["warnings"])
        # an explicit retraction is NOT ambiguous — no warning
        archive.append(self.root, dict(base, id="a", fitness=None, guardrail="revoked",
                                       generation=1, retract=True))
        res = doctor.run(self.root)
        self.assertFalse(any("does not retract" in w for w in res["warnings"]))

    def test_broken_markers_fail(self):
        p = self.root / "src" / "algo.py"
        p.write_text(p.read_text().replace("# EVOLVE-BLOCK-END", ""))
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "break markers"], self.root)
        res = doctor.run(self.root)
        self.assertFalse(res["ok"])

    def test_untracked_surface_file_fails(self):
        git(["rm", "-q", "--cached", "src/algo.py"], self.root)
        git(["commit", "-q", "-m", "untrack"], self.root)
        res = doctor.run(self.root)
        self.assertFalse(res["ok"])

    def test_measure_noise_writes_floor(self):
        cfg = load_cfg(self.root)
        cfg["fitness"]["noise_floor"] = None
        cfg["fitness"]["noise_runs"] = 2
        cfgmod.save(cfg, self.root, backup=False)
        git(["add", "-A"], self.root); git(["commit", "-q", "-m", "cfg"], self.root)
        res = doctor.run(self.root, measure_noise=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(cfgmod.load(self.root)["fitness"]["noise_floor"], 0.0)  # deterministic eval
        self.assertTrue(list((self.root / "evolve").glob("*.json.bak.*")))       # backed up

    def test_registry_placeholder_warning(self):
        root = make_registry_project()
        try:
            cfg = load_cfg(root)
            cfg["eval"]["proxy"]["cmd"] = "python3 eval.py {results_dir} genome.json {seed}"
            cfgmod.save(cfg, root, backup=False)
            res = doctor.run(root)
            self.assertTrue(any("{genome}" in w for w in res["warnings"]))
        finally:
            cleanup(root)


class TestScaffold(unittest.TestCase):
    def test_init_idempotent(self):
        import tempfile, subprocess
        root = pathlib.Path(tempfile.mkdtemp(prefix="evolve-scaffold-"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            out1 = scaffold.init(root, mode="markers")
            self.assertTrue((root / "evolve" / "evolve.json").exists())
            self.assertTrue((root / "evolve" / "EVOLVE.md").exists())
            self.assertIn(".cache/", (root / "evolve" / ".gitignore").read_text())
            self.assertIn("markers", (root / "evolve" / "EVOLVE.md").read_text())
            out2 = scaffold.init(root, mode="markers")
            self.assertTrue(all(a.startswith("kept") or a.startswith("created")
                                for a in out2["actions"]))
            self.assertFalse(any(a.startswith("wrote") for a in out2["actions"]))
        finally:
            cleanup(root)

    def test_registry_axis_dirs_created_on_rerun(self):
        import tempfile, subprocess
        root = pathlib.Path(tempfile.mkdtemp(prefix="evolve-scaffold-reg-"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            scaffold.init(root, mode="registry")
            cfg = json.loads((root / "evolve" / "evolve.json").read_text())
            cfg["surface"]["registry"]["axes"] = {"policy": {"baseline": "baseline"}}
            (root / "evolve" / "evolve.json").write_text(json.dumps(cfg))
            scaffold.init(root, mode="registry")
            self.assertTrue((root / "evolve" / "chunks" / "policy").is_dir())
        finally:
            cleanup(root)


if __name__ == "__main__":
    unittest.main()
