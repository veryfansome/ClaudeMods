import copy
import json
import unittest

from _toy import make_markers_project, markers_config, registry_config, cleanup
from _evolve import config as cfgmod


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()

    def tearDown(self):
        cleanup(self.root)

    def test_load_and_defaults(self):
        cfg = cfgmod.load(self.root)
        self.assertEqual(cfg["surface"]["mode"], "markers")
        self.assertEqual(cfg["search"]["lambda"], "auto")       # from the toy config
        self.assertIsNone(cfg["eval"]["setup"])                 # default filled

    def test_missing_config(self):
        (self.root / "evolve" / "evolve.json").unlink()
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load(self.root)

    def _expect_invalid(self, mutate):
        cfg = copy.deepcopy(markers_config())
        mutate(cfg)
        (self.root / "evolve" / "evolve.json").write_text(json.dumps(cfg))
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load(self.root)

    def test_validation_errors(self):
        self._expect_invalid(lambda c: c.update(task=""))
        self._expect_invalid(lambda c: c["surface"].update(files=[]))
        self._expect_invalid(lambda c: c["eval"]["proxy"].update(cmd=None))
        self._expect_invalid(lambda c: c["search"].update(op_probs={"diff": 0.5}))
        self._expect_invalid(lambda c: c["search"].update(op_probs={"mutate": 1.0}))
        self._expect_invalid(lambda c: c["eval"].update(splits=["final"]))          # no inner
        self._expect_invalid(lambda c: c.update(protected=["eval.py"]))             # state dir uncovered
        self._expect_invalid(lambda c: c["fitness"].update(noise_floor=-1))

    def test_registry_validation(self):
        cfg = registry_config()
        del cfg["surface"]["registry"]["axes"]["objective"]["baseline"]
        (self.root / "evolve" / "evolve.json").write_text(json.dumps(cfg))
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load(self.root)

    def test_lambda_accepts_auto_null_and_numbers_only(self):
        cfg = cfgmod.load(self.root)
        for ok in ("auto", None, 0, 25.0):
            cfg["search"]["lambda"] = ok
            cfgmod.validate(cfg, self.root)
        for bad in ("bogus", -1, True, float("inf")):
            cfg["search"]["lambda"] = bad
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.validate(cfg, self.root)

    def test_inventor_files_must_be_repo_relative(self):
        cfg = cfgmod.load(self.root)
        cfg["surface"] = {"mode": "registry", "files": [], "registry": {
            "dir": "evolve/chunks",
            "axes": {"objective": {"baseline": "baseline"}},
            "inventor_files": ["../outside.py"]}}
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate(cfg, self.root)
        cfg["surface"]["registry"]["inventor_files"] = ["/abs/path.py"]
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate(cfg, self.root)
        cfg["surface"]["registry"]["inventor_files"] = ["ok/nested.py"]
        cfgmod.validate(cfg, self.root)

    def test_absent_budget_block_means_unlimited(self):
        p = self.root / "evolve" / "evolve.json"
        raw = json.loads(p.read_text())
        del raw["budget"]
        p.write_text(json.dumps(raw))
        self.assertEqual(cfgmod.load(self.root)["budget"], {})   # removal = no limits, not defaults
        raw["budget"] = {"max_generations": 7}
        p.write_text(json.dumps(raw))                            # a partial block is taken verbatim
        self.assertEqual(cfgmod.load(self.root)["budget"], {"max_generations": 7})

    def test_save_backs_up(self):
        cfg = cfgmod.load(self.root)
        cfg["budget"]["max_generations"] = 99
        cfgmod.save(cfg, self.root)
        self.assertEqual(cfgmod.load(self.root)["budget"]["max_generations"], 99)
        baks = list((self.root / "evolve").glob("*.json.bak.*"))
        self.assertEqual(len(baks), 1)


if __name__ == "__main__":
    unittest.main()
