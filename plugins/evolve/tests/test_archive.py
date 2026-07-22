import unittest

from _toy import make_markers_project, cleanup
from _evolve import archive


def rec(i, fitness, mode="proxy", split="inner", parent=None, gen=1, **kw):
    return {"id": i, "parent": parent, "generation": gen, "inventor": "t", "operator": "diff",
            "axis_changed": None, "rationale": "", "mode": mode, "split": split,
            "fitness": fitness, "guardrail": "pass" if fitness is not None else "boom", **kw}


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()

    def tearDown(self):
        cleanup(self.root)

    def test_roundtrip_and_validity(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("b", None))                 # failure: recorded, not selectable
        archive.append(self.root, rec("c", 9.0, split="final"))   # firewalled
        recs = archive.load(self.root)
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["id"] for r in archive.valid(recs)], ["a"])

    def test_rejects_nonfinite_and_embedded_private(self):
        with self.assertRaises(ValueError):
            archive.append(self.root, rec("x", float("inf")))
        with self.assertRaises(ValueError):
            archive.append(self.root, rec("x", 1.0, private={"secret": 1}))

    def test_best_per_id_full_beats_proxy(self):
        archive.append(self.root, rec("a", 5.0, mode="proxy"))
        archive.append(self.root, rec("a", 2.0, mode="full"))
        best = archive.best_per_id(archive.valid(archive.load(self.root)))
        self.assertEqual(len(best), 1)
        self.assertEqual(best[0]["mode"], "full")     # full outranks a higher proxy score
        self.assertEqual(archive.best(self.root)["fitness"], 2.0)

    def test_sample_parent_offspring_penalty(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("b", 1.0))
        for i in range(8):                            # 'a' has 8 children, 'b' none
            archive.append(self.root, rec(f"kid{i}", 0.5, parent="a", gen=2))
        picks = [archive.sample_parent(self.root, seed=s)["id"] for s in range(300)]
        # equal fitness, so the offspring penalty should make the unexploited parent
        # strictly more likely (a: weight w, b: 9w among the two; kids compete too)
        self.assertGreater(picks.count("b"), picks.count("a"))

    def test_sample_parent_empty(self):
        self.assertIsNone(archive.sample_parent(self.root))

    def test_sample_parent_survives_wide_unnormalized_fitness(self):
        # Unnormalized fitness (raw latency, token counts) with λ=10 must not overflow the sigmoid.
        for i, f in enumerate([500.0, 100.0, -500.0]):
            archive.append(self.root, rec(f"g{i}", f))
        pick = archive.sample_parent(self.root, seed=1, lam=10.0)   # must not raise OverflowError
        self.assertIn(pick["id"], {"g0", "g1", "g2"})

    def test_reject_boolean_fitness(self):
        with self.assertRaises(ValueError):
            archive.append(self.root, rec("b", True))

    def test_inspirations_exclude_parent(self):
        for i, f in enumerate([1.0, 2.0, 3.0, 4.0]):
            archive.append(self.root, rec(f"g{i}", f))
        insp = archive.sample_inspirations(self.root, "g3", n_top=2, n_archive=1)
        self.assertNotIn("g3", [r["id"] for r in insp])
        self.assertGreaterEqual(len(insp), 2)

    def test_budget_state(self):
        budget = {"max_generations": 3, "max_full_evals": 1, "stop_after_stale_rounds": 2}
        archive.append(self.root, rec("a", 1.0, gen=1))
        state = archive.budget_state(self.root, budget)
        self.assertEqual(state["exhausted"], [])
        archive.append(self.root, rec("f", 2.0, mode="full", gen=2))
        archive.append(self.root, rec("z", 0.1, gen=4))           # stale: champ at gen 2
        state = archive.budget_state(self.root, budget)
        self.assertEqual(state["champion"], "f")
        self.assertEqual(state["stale_generations"], 2)
        self.assertEqual(len(state["exhausted"]), 3)              # gens, fulls, staleness

    def test_rescoring_same_id_allowed(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", 1.1))                  # paired rerun
        self.assertEqual(len(archive.load(self.root)), 2)
        self.assertEqual(len(archive.best_per_id(archive.valid(archive.load(self.root)))), 1)


if __name__ == "__main__":
    unittest.main()
