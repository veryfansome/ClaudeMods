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
        self.assertEqual(cfg["search"]["lambda"], 10.0)         # explicit
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

    def test_save_backs_up(self):
        cfg = cfgmod.load(self.root)
        cfg["budget"]["max_generations"] = 99
        cfgmod.save(cfg, self.root)
        self.assertEqual(cfgmod.load(self.root)["budget"]["max_generations"], 99)
        baks = list((self.root / "evolve").glob("*.json.bak.*"))
        self.assertEqual(len(baks), 1)


if __name__ == "__main__":
    unittest.main()
