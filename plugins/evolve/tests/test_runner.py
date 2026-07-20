import json
import unittest

from _toy import make_markers_project, load_cfg, cleanup
from _evolve import runner


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)

    def tearDown(self):
        cleanup(self.root)

    def test_pristine_clone_lifecycle(self):
        with runner.pristine_clone(self.root) as clone:
            self.assertTrue((clone / "src" / "algo.py").exists())
            kept = clone
        self.assertFalse(kept.exists())

    def test_apply_patch_rejects_garbage(self):
        with runner.pristine_clone(self.root) as clone:
            with self.assertRaises(runner.EvalFailure):
                runner.apply_patch(clone, "not a patch\n")

    def test_stdout_json_fallback(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["proxy"]["cmd"] = (
            "python3 -c \"import json; print('log line'); "
            "print(json.dumps({'combined_score': 3.5, 'public': {'k': 1}}))\"")
        with runner.pristine_clone(self.root) as clone:
            res = runner.evaluate_tier(clone, cfg, "proxy", "inner")
        self.assertEqual(res["fitness"], 3.5)

    def test_missing_metrics_fails(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["proxy"]["cmd"] = "true"
        with runner.pristine_clone(self.root) as clone:
            with self.assertRaises(runner.EvalFailure):
                runner.evaluate_tier(clone, cfg, "proxy", "inner")

    def test_nonzero_exit_correct_false_and_nan(self):
        cases = [
            "exit 3",
            "python3 -c \"import json; print(json.dumps({'combined_score': 1.0, 'correct': False, 'error': 'bad'}))\"",
            "python3 -c \"import json; print(json.dumps({'combined_score': float('nan')}))\"",
        ]
        for cmd in cases:
            cfg = json.loads(json.dumps(self.cfg))
            cfg["eval"]["proxy"]["cmd"] = cmd
            with runner.pristine_clone(self.root) as clone:
                with self.assertRaises(runner.EvalFailure):
                    runner.evaluate_tier(clone, cfg, "proxy", "inner")

    def test_timeout(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["proxy"].update(cmd="sleep 5", timeout_s=1)
        with runner.pristine_clone(self.root) as clone:
            with self.assertRaises(runner.EvalFailure) as cm:
                runner.evaluate_tier(clone, cfg, "proxy", "inner")
        self.assertIn("timeout", str(cm.exception))

    def test_multi_seed_mean_and_private(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["proxy"].update(
            cmd="python3 -c \"import json,sys; s=int(sys.argv[1]); "
                "print(json.dumps({'combined_score': 1.0 + s, 'private': {'s%d' % s: True}, "
                "'text_feedback': 'seed %d' % s}))\" {seed}",
            seeds=[0, 2])
        with runner.pristine_clone(self.root) as clone:
            res = runner.evaluate_tier(clone, cfg, "proxy", "inner")
        self.assertEqual(res["fitness"], 2.0)                    # mean of 1.0 and 3.0
        self.assertEqual(res["seeds"], [0, 2])
        self.assertEqual(res["private"], {"s0": True, "s2": True})
        self.assertIn("seed 2", res["text_feedback"])
        self.assertNotIn("_private", json.dumps(res["per_seed"]))

    def test_last_json_object_deep_nesting(self):
        # Deeply-nested public metrics must not defeat extraction (the fixed-depth-regex bug).
        text = 'log line\n{"combined_score": 3.0, "public": {"a": {"b": {"c": {"d": 1}}}}}\n'
        obj = runner.last_json_object(text)
        self.assertEqual(obj["combined_score"], 3.0)
        self.assertIsNone(runner.last_json_object("no json here"))

    def test_gate_failure(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["guardrails"] = ["python3 -c \"raise SystemExit('nope')\""]
        with runner.pristine_clone(self.root) as clone:
            with self.assertRaises(runner.EvalFailure) as cm:
                runner.run_gates(clone, cfg)
        self.assertIn("guardrail", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
