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

    def test_sample_parent_auto_lambda_restores_pressure_on_margin_scale(self):
        # the field incident: margin-scale fitness under a fixed λ=10 is statistically
        # uniform; "auto" derives λ=2.5/sd and the best candidate actually dominates
        for i, f in enumerate([0.000, 0.005, 0.020]):
            archive.append(self.root, rec(f"m{i}", f))
        auto = [archive.sample_parent(self.root, seed=s)["id"] for s in range(300)]
        self.assertGreater(auto.count("m2"), 150)                 # ~59% under auto
        fixed = [archive.sample_parent(self.root, seed=s, lam=10.0)["id"] for s in range(300)]
        self.assertLess(fixed.count("m2"), 150)                   # ~35% under inert λ=10
        self.assertGreater(auto.count("m2"), fixed.count("m2"))

    def test_sample_parent_auto_lambda_uniform_on_equal_fitness(self):
        for i in range(3):
            archive.append(self.root, rec(f"e{i}", 1.0))
        pick = archive.sample_parent(self.root, seed=1)           # sd=0 → λ=0: no crash
        self.assertIn(pick["id"], {"e0", "e1", "e2"})

    def test_auto_lambda_scale_is_robust_to_one_outlier(self):
        # one wrongly-scaled ingest (or a breakthrough) must not collapse the pressure for
        # the rest of the population — pstdev would put λ near zero here
        cluster = [{"id": f"c{i}", "fitness": f}
                   for i, f in enumerate([0.000, 0.005, 0.010, 0.015, 0.020])]
        outlier = [{"id": "big", "fitness": 5.0}]
        lam, w = archive.selection_weights(cluster + outlier, "auto", {})
        lo = w[0]                                                  # worst cluster member
        hi = w[4]                                                  # best cluster member
        self.assertGreater(hi / lo, 10)                            # cluster still discriminated

    def test_auto_lambda_floors_pressure_at_the_noise_floor(self):
        # two candidates a coin-flip apart used to get a constant ~75:1 — spread within
        # eval noise must sample ~uniformly instead of amplifying the luckier run
        items = [{"id": "a", "fitness": 1.0000}, {"id": "b", "fitness": 1.0001}]
        _, w_raw = archive.selection_weights(items, "auto", {})
        self.assertGreater(max(w_raw) / min(w_raw), 50)            # no floor: noise amplified
        _, w = archive.selection_weights(items, "auto", {}, noise_floor=0.05)
        self.assertLess(max(w) / min(w), 1.01)                     # floored: honest uniform

    def test_lambda_non_finite_degrades_to_uniform_not_nan(self):
        # config.validate blocks inf from the file; the runtime guard covers API callers —
        # sigmoid(inf·0) is NaN for the median item and random.choices rejects NaN totals
        for i, f in enumerate([0.1, 0.2, 0.3]):
            archive.append(self.root, rec(f"n{i}", f))
        pick = archive.sample_parent(self.root, seed=1, lam=float("inf"))
        self.assertIn(pick["id"], {"n0", "n1", "n2"})

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
        archive.append(self.root, rec("z", 0.1, gen=4))           # last above-median at gen 2
        state = archive.budget_state(self.root, budget)
        self.assertEqual(state["stale_generations"], 2)   # gen 4 vs last above-median (f, gen 2)
        self.assertEqual(len(state["exhausted"]), 3)              # gens, fulls, staleness

    def test_budget_empty_block_never_exhausts(self):
        archive.append(self.root, rec("f", 2.0, mode="full", gen=1))
        archive.append(self.root, rec("z", 0.1, gen=99))
        state = archive.budget_state(self.root, {})
        self.assertEqual(state["exhausted"], [])                  # unlimited
        self.assertEqual(state["generations"], 99)                # but still informative
        self.assertEqual(state["stale_generations"], 98)

    def test_budget_disabled(self):
        budget = {"max_generations": 1, "max_full_evals": 1, "stop_after_stale_rounds": 1,
                  "disabled": True}
        archive.append(self.root, rec("f", 2.0, mode="full", gen=1))
        archive.append(self.root, rec("z", 0.1, gen=9))          # would be stale + over both caps
        state = archive.budget_state(self.root, budget)
        self.assertEqual(state["exhausted"], [])                 # nothing ever exhausts
        self.assertIsNone(state["stale_generations"])            # staleness not computed
        self.assertTrue(state["disabled"])
        self.assertEqual(state["generations"], 9)                # counts still reported
        # same archive WITHOUT the flag trips every limit
        live = archive.budget_state(self.root, {k: v for k, v in budget.items() if k != "disabled"})
        self.assertEqual(len(live["exhausted"]), 3)

    def test_staleness_is_append_order_recency_not_lineage_depth(self):
        # a fresh best bred from a shallow parent must reset the clock even when a deep
        # (failing) lineage has pushed max(generation) far ahead — anchoring to the best
        # record's own generation would read "stale for 6" here
        archive.append(self.root, rec("a", 1.0, gen=1))
        archive.append(self.root, rec("deep-fail", None, gen=8))
        archive.append(self.root, rec("b", 2.0, gen=2))            # new best, appended last
        state = archive.budget_state(self.root, {})
        self.assertEqual(state["stale_generations"], 0)

    def test_staleness_grows_on_equal_fitness_plateau(self):
        # a plateau of equal scores is stagnation — a median-anchored reset would read 0
        # forever and the stop could never fire in exactly the regime it exists to detect
        for g in (1, 2, 3, 4):
            archive.append(self.root, rec(f"p{g}", 1.0, gen=g))
        self.assertEqual(archive.budget_state(self.root, {})["stale_generations"], 3)

    def test_staleness_immune_to_retraction(self):
        # "new best" is judged at append time: retracting the best later must not
        # retro-date the clock and spike staleness by the whole generation gap
        archive.append(self.root, rec("a", 1.0, gen=1))
        archive.append(self.root, rec("b", 2.0, gen=2))
        archive.append(self.root, rec("c", 0.5, gen=6))
        before = archive.budget_state(self.root, {})["stale_generations"]
        archive.append(self.root, rec("b", None, retract=True, guardrail="revoked", gen=2))
        after = archive.budget_state(self.root, {})["stale_generations"]
        self.assertEqual((before, after), (4, 4))

    def test_latest_valid_fails_closed_on_retraction(self):
        # retracted ids are ABSENT, not present-with-null: a membership check must fail
        # closed instead of emitting a retracted genome as if it were live
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", 1.2))                  # latest, not best, wins
        archive.append(self.root, rec("b", 2.0))
        archive.append(self.root, rec("b", None, retract=True, guardrail="revoked"))
        lv = archive.latest_valid(archive.load(self.root))
        self.assertEqual(lv["a"]["fitness"], 1.2)
        self.assertNotIn("b", lv)

    def test_retracted_ids_validity_view(self):
        # fitness is partition-scoped; a VALIDITY verdict is not — a candidate revoked for a
        # mechanism defect must not be laundered clean by a regime change into a partition
        # where it has no records at all
        archive.append(self.root, rec("bad", 1.0, env="old-env"))
        archive.append(self.root, rec("bad", None, env="old-env", retract=True, guardrail="causality"))
        recs = archive.load(self.root)
        self.assertEqual(archive.retracted_ids(recs, selection_env="new-env"), {})   # per-partition view
        self.assertEqual(archive.retracted_ids(recs, cross_partition=True), {"bad": "causality"})

    def test_retracted_ids_ordering_rule(self):
        # retracted iff the last retraction FOLLOWS the last scored record: a fitness-null
        # flake after a retraction must not silently un-retract; a real score reinstates
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", None, retract=True, guardrail="revoked"))
        archive.append(self.root, rec("a", None, guardrail="CUDA OOM"))              # flake
        recs = archive.load(self.root)
        self.assertIn("a", archive.retracted_ids(recs, cross_partition=True))
        archive.append(self.root, rec("a", 0.9))                                     # deliberate rescore
        recs = archive.load(self.root)
        self.assertEqual(archive.retracted_ids(recs, cross_partition=True), {})

    def test_retracted_ids_final_split_never_evicts(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", None, retract=True, guardrail="revoked"))
        archive.append(self.root, rec("a", 2.0, split="final"))                      # holdout run
        recs = archive.load(self.root)
        self.assertIn("a", archive.retracted_ids(recs, cross_partition=True))

    def test_empty_string_parent_errors(self):
        # "" is falsy — it used to take the silent max+1 fallback ('always pass --parent'
        # was satisfiable by --parent "")
        archive.append(self.root, rec("a", 1.0))
        with self.assertRaises(ValueError):
            archive.next_generation(self.root, "")

    def test_next_generation_unknown_parent_errors(self):
        archive.append(self.root, rec("a", 1.0, gen=3))
        self.assertEqual(archive.next_generation(self.root, "a"), 4)
        self.assertEqual(archive.next_generation(self.root, None), 4)   # rootless: new front
        with self.assertRaises(ValueError):
            archive.next_generation(self.root, "ghost")

    def test_rescoring_same_id_allowed(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", 1.1))                  # paired rerun
        self.assertEqual(len(archive.load(self.root)), 2)
        self.assertEqual(len(archive.best_per_id(archive.valid(archive.load(self.root)))), 1)

    def test_retraction_is_record_scoped(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("b", 0.5))
        # post-hoc eval-validity discovery: append-only, explicit retraction of a passing id
        archive.append(self.root, rec("a", None, retract=True, guardrail="post-hoc causality violation"))
        recs = archive.load(self.root)
        self.assertEqual(len(recs), 3)                            # history intact
        self.assertEqual([r["id"] for r in archive.valid(recs)], ["b"])
        # a later rescore under the fixed eval reinstates the id on the NEW score ONLY —
        # the tainted pre-retraction 1.0 must never become the id's fitness again
        archive.append(self.root, rec("a", 0.9))
        v = archive.valid(archive.load(self.root))
        self.assertEqual(sorted((r["id"], r["fitness"]) for r in v), [("a", 0.9), ("b", 0.5)])
        best = {r["id"]: r["fitness"] for r in archive.best_per_id(v)}
        self.assertEqual(best["a"], 0.9)

    def test_routine_failure_is_not_a_retraction(self):
        # a flaky paired rerun (timeout) or an ingested remote crash is inert search signal —
        # without the explicit retract marker it must not drop the top candidate from selection
        archive.append(self.root, rec("top1", 5.0))
        archive.append(self.root, rec("top1", None, guardrail="timeout after 60s"))
        self.assertEqual([r["fitness"] for r in archive.valid(archive.load(self.root))], [5.0])

    def test_retraction_record_must_be_fitness_null_with_reason(self):
        with self.assertRaises(ValueError):
            archive.append(self.root, rec("a", 1.0, retract=True))
        with self.assertRaises(ValueError):
            archive.append(self.root, rec("a", None, retract=True, guardrail="pass"))

    def test_retracted_id_stays_in_dedup_memory(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", None, retract=True, guardrail="revoked"))
        recs = archive.load(self.root)
        self.assertEqual(archive.valid(recs), [])                 # out of selection
        self.assertEqual([r["id"] for r in archive.scored_any(recs)], ["a"])   # still remembered

    def test_valid_survives_record_missing_id(self):
        # doctor's whole job is reporting corruption in files it didn't write — an id-less
        # line (fitness-null OR scored) must be excluded, not crash valid()'s consumers
        # (best_per_id, sample_parent, budget_state all key on r["id"]) before doctor can
        # even be pointed at the file
        archive.append(self.root, rec("a", 1.0))
        with open(archive.archive_path(self.root), "a") as f:
            f.write('{"mode": "proxy", "split": "inner", "fitness": null}\n')
            f.write('{"mode": "full", "split": "inner", "fitness": 2.0}\n')
        recs = archive.load(self.root)
        self.assertEqual([r["id"] for r in archive.valid(recs)], ["a"])
        self.assertEqual([r["id"] for r in archive.scored_any(recs)], ["a"])
        self.assertEqual(archive.best(self.root)["id"], "a")               # not a KeyError
        self.assertEqual(archive.sample_parent(self.root)["id"], "a")
        state = archive.budget_state(self.root, {})
        self.assertEqual(state["stale_generations"], 0)   # the id-less line contributes nothing

    def test_retraction_scoped_to_selection_env(self):
        archive.append(self.root, rec("a", 1.0, env="env-x"))
        archive.append(self.root, rec("a", 1.2, env="env-y"))
        archive.append(self.root, rec("a", None, env="env-x", retract=True, guardrail="bad in env-x"))
        recs = archive.load(self.root)
        self.assertEqual(archive.valid(recs, selection_env="env-x"), [])
        self.assertEqual([r["fitness"] for r in archive.valid(recs, selection_env="env-y")], [1.2])

    def test_retraction_does_not_leak_from_final_split(self):
        archive.append(self.root, rec("a", 1.0))
        archive.append(self.root, rec("a", None, split="final", retract=True, guardrail="failed on holdout"))
        # final-split records are firewalled out of the selection scope entirely,
        # so a final-split failure never retracts inner-split fitness
        self.assertEqual([r["fitness"] for r in archive.valid(archive.load(self.root))], [1.0])


if __name__ == "__main__":
    unittest.main()
