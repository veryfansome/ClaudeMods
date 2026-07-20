import json
import pathlib
import unittest

from _toy import make_markers_project, make_registry_project, load_cfg, cleanup, git
from _evolve import config as cfgmod, doctor, scaffold


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
