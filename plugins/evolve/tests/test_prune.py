"""prune: the trait-coverage rule, the pruned view, the audit, and every surface the
design review measured a leak through (partner draw, doctor pressure, board stats,
retract env-pool, generation inheritance)."""

import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from _toy import PLUGIN, make_registry_project, cleanup, git
from _evolve import archive, board, config as cfgmod, diet, prune, rounds, score


def rec(rid, fitness, chunks, parent="p0", env="e1", generation=1):
    """A minimal valid scored record with a registry genome."""
    return {"id": rid, "parent": parent, "mode": "full", "split": "inner", "env": env,
            "fitness": fitness, "generation": generation,
            "genome": {"chunks": {ax: {"impl": impl} for ax, impl in chunks.items()}}}


def prune_rec(rid, on=True, reason="r", env="e1"):
    return {"id": rid, "prune": on, "reason": reason, "env": env}


def retract_rec(rid, env="e1"):
    return {"id": rid, "retract": True, "fitness": None, "guardrail": "void",
            "mode": "full", "split": "inner", "env": env}


def toy_cfg(noise_floor=0.01, selection_env="e1"):
    return {"fitness": {"noise_floor": noise_floor, "selection_env": selection_env},
            "surface": {"mode": "registry"}}


def write_archive(recs):
    d = pathlib.Path(tempfile.mkdtemp(prefix="evolve-prune-arch-"))
    (d / "evolve" / "archive").mkdir(parents=True)
    (d / "evolve" / "archive" / "genomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n")
    return d


class TestPrunedView(unittest.TestCase):
    def test_last_prune_shaped_record_wins(self):
        recs = [rec("a", 1.0, {"x": "i1"}), prune_rec("a"),
                prune_rec("a", on=False, reason="back"), prune_rec("a", reason="again")]
        self.assertEqual(archive.pruned_ids(recs, "e1"), {"a": "again"})
        recs.append(prune_rec("a", on=False, reason="final"))
        self.assertEqual(archive.pruned_ids(recs, "e1"), {})

    def test_new_score_does_not_reinstate(self):
        recs = [rec("a", 1.0, {"x": "i1"}), prune_rec("a"), rec("a", 2.0, {"x": "i1"})]
        self.assertIn("a", archive.pruned_ids(recs, "e1"))

    def test_partition_scoped_with_cross_partition_view(self):
        recs = [rec("a", 1.0, {"x": "i1"}), prune_rec("a", env="e1")]
        self.assertIn("a", archive.pruned_ids(recs, "e1"))
        self.assertEqual(archive.pruned_ids(recs, "e2"), {})   # another partition's pool
        self.assertIn("a", archive.pruned_ids(recs, "e2", cross_partition=True))

    def test_prune_survives_pinning_selection_env(self):
        # Review major: a prune made while selection_env was UNSET must keep biting after
        # the owner pins it. cmd_prune resolves the record's env from the id's own
        # measurement records (here "e1"), so both views agree.
        recs = [rec("a", 1.0, {"x": "i1"}), prune_rec("a", env="e1")]
        self.assertIn("a", archive.pruned_ids(recs, None))    # unpinned: prune bites
        self.assertIn("a", archive.pruned_ids(recs, "e1"))    # pinned to the pool's env: still bites

    def test_streams_are_per_partition(self):
        # Final-review major: prune records are per-(id, env) STREAMS. A reinstate scoped
        # to one partition must not erase another partition's standing prune from any view.
        recs = [rec("a", 1.0, {"x": "i1"}, env="e1"), rec("a", 0.8, {"x": "i1"}, env="e2"),
                prune_rec("a", env="e1"), prune_rec("a", env="e2"),
                prune_rec("a", on=False, reason="back in e1", env="e1")]
        self.assertNotIn("a", archive.pruned_ids(recs, "e1"))      # e1 stream reversed
        self.assertIn("a", archive.pruned_ids(recs, "e2"))         # e2 stream STANDS
        self.assertIn("a", archive.pruned_ids(recs, None))         # union sees it
        self.assertIn("a", archive.pruned_ids(recs, "e1", cross_partition=True))  # global too
        recs.append(prune_rec("a", on=False, reason="back in e2", env="e2"))
        self.assertEqual(archive.pruned_ids(recs, None), {})
        self.assertEqual(archive.pruned_ids(recs, "e2", cross_partition=True), {})
        self.assertEqual(prune.standing_streams(recs, "a"), {})

    def test_selection_pool_excludes_pruned_but_valid_keeps_them(self):
        recs = [rec("a", 1.0, {"x": "i1"}), rec("b", 2.0, {"x": "i1"}), prune_rec("a")]
        self.assertEqual({r["id"] for r in archive.selection_pool(recs, "e1")}, {"b"})
        self.assertEqual({r["id"] for r in archive.best_per_id(archive.valid(recs, "e1"))},
                         {"a", "b"})   # fitness view untouched: history, apply, dedup

    def test_check_record_prune_shapes(self):
        archive._check_record({"id": "a", "prune": True, "reason": "r"})
        archive._check_record({"id": "a", "prune": False, "reason": "r", "env": "e1"})
        archive._check_record({"id": "a", "prune": True, "reason": "r", "min_carriers": 3})
        for bad in ({"id": "a", "prune": 1, "reason": "r"},          # non-bool
                    {"prune": True, "reason": "r"},                  # no id
                    {"id": "a", "prune": True},                      # no reason
                    {"id": "a", "prune": True, "reason": "r", "fitness": 1.0},
                    {"id": "a", "prune": True, "reason": "r", "retract": True},
                    {"id": "a", "prune": True, "reason": "r", "min_carriers": 0},
                    {"id": "a", "prune": True, "reason": "r", "min_carriers": True}):
            with self.assertRaises(ValueError):
                archive._check_record(bad)

    def test_budget_state_ignores_prune_records(self):
        base = [rec("a", 1.0, {"x": "i1"}, generation=3)]
        d1 = write_archive(base)
        d2 = write_archive(base + [prune_rec("a")])
        try:
            self.assertEqual(archive.budget_state(d1, {}, "e1"),
                             archive.budget_state(d2, {}, "e1"))
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)


class TestCoverageRule(unittest.TestCase):
    """Constructed pools over axes x/y/z; noise_floor 0.01. Every pool includes a rootless
    baseline (fitness 0.0 — never a qualifying carrier for anyone above the margin)."""

    def _pool(self, *specs):
        recs = [{"id": "base", "parent": None, "mode": "full", "split": "inner", "env": "e1",
                 "fitness": 0.0, "generation": 0,
                 "genome": {"chunks": {"x": {"impl": "bx"}, "y": {"impl": "by"},
                                       "z": {"impl": "bz"}}}}]
        recs += [rec(i, f, c) for i, f, c in specs]
        return recs

    def _ids(self, plan):
        return [p["id"] for p in plan["prunable"]]

    def test_sole_carrier_is_immortal(self):
        recs = self._pool(
            ("a", 0.1, {"x": "i1", "y": "unique", "z": "bz"}),
            ("b", 1.0, {"x": "i1", "y": "by", "z": "bz"}),
            ("c", 1.0, {"x": "i1", "y": "by2", "z": "bz2"}),
            ("d", 1.0, {"x": "bx", "y": "by", "z": "bz"}))
        self.assertNotIn("a", self._ids(prune.plan(recs, toy_cfg(), min_carriers=2)))

    def test_fully_covered_candidate_prunes(self):
        recs = self._pool(
            ("a", 0.1, {"x": "i1", "y": "i2", "z": "i3"}),
            ("b", 1.0, {"x": "i1", "y": "i2", "z": "i3"}),
            ("c", 0.9, {"x": "i1", "y": "i2", "z": "bz"}),
            ("e", 0.85, {"x": "i1", "y": "by", "z": "i3"}),
            ("f", 0.8, {"x": "bx", "y": "i2", "z": "i3"}))
        self.assertIn("a", self._ids(prune.plan(recs, toy_cfg(), min_carriers=2)))

    def test_pair_coverage_protects_unique_combination(self):
        # Every SINGLE trait of 'a' is carried by two diverse, better candidates, but
        # nobody else combines i1 WITH i2 — epistasis: the combination is a's alone.
        specs = [("a", 0.1, {"x": "i1", "y": "i2", "z": "bz"}),
                 ("b", 1.0, {"x": "i1", "y": "by", "z": "bz"}),
                 ("c", 1.0, {"x": "i1", "y": "by2", "z": "bz2"}),
                 ("d", 1.0, {"x": "bx", "y": "i2", "z": "bz"}),
                 ("e", 1.0, {"x": "bx2", "y": "i2", "z": "bz2"})]
        self.assertNotIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                   min_carriers=2)))
        # ...and the moment one other candidate carries the combination, 'a' prunes:
        specs.append(("f", 1.0, {"x": "i1", "y": "i2", "z": "bz2"}))
        self.assertIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                min_carriers=2)))

    def test_best_anchor_blocks_subnoise_ratchet(self):
        # Carriers exist just inside the noise margin but none at-or-above 'a' itself:
        # 'a' is the traits' best expression and must stay.
        specs = [("a", 1.0, {"x": "i1", "y": "by2", "z": "bz2"}),
                 ("b", 0.995, {"x": "i1", "y": "by", "z": "bz2"}),
                 ("c", 0.995, {"x": "i1", "y": "by2", "z": "bz"}),
                 ("d", 0.995, {"x": "i1", "y": "by9", "z": "bz9"}),
                 ("e", 0.995, {"x": "bx", "y": "by2", "z": "bz2"})]
        self.assertNotIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                   min_carriers=2)))
        # An at-or-above carrier of the same design flips the verdict — the anchor was
        # the only thing keeping 'a'.
        specs.append(("f", 1.001, {"x": "i1", "y": "by2", "z": "bz2"}))
        self.assertIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                min_carriers=2)))

    def test_rootless_baseline_never_planned(self):
        # NON-VACUOUS: a 2-axis pool (div_need=1) where the baseline's every trait and
        # pair IS covered by better, diverse carriers — only the rootless guard keeps it.
        recs = [{"id": "base", "parent": None, "mode": "full", "split": "inner", "env": "e1",
                 "fitness": 0.0, "generation": 0,
                 "genome": {"chunks": {"x": {"impl": "bx"}, "y": {"impl": "by"}}}}]
        recs += [rec(i, f, c) for i, f, c in (
            ("c1", 1.0, {"x": "bx", "y": "by"}),
            ("c2", 1.0, {"x": "bx", "y": "by2"}),
            ("c3", 1.0, {"x": "bx2", "y": "by"}))]
        plan = prune.plan(recs, toy_cfg(), min_carriers=2)
        self.assertNotIn("base", self._ids(plan))
        # proof the guard is load-bearing: an identical PARENTED twin of the baseline prunes
        recs.append(rec("twin", 0.0, {"x": "bx", "y": "by"}))
        self.assertIn("twin", self._ids(prune.plan(recs, toy_cfg(), min_carriers=2)))

    def test_greedy_judges_against_survivors_not_the_original_pool(self):
        # a < b < c share the combination (i1, i2); helpers keep singles diverse. Judged
        # against the ORIGINAL pool, all three look covered (each has a same-combo peer
        # within margin above it) and the combination would vanish entirely. Greedy
        # worst-first against survivors prunes a and b, then KEEPS c — the combination's
        # last carrier.
        recs = self._pool(
            ("a", 0.100, {"x": "i1", "y": "i2", "z": "bz"}),
            ("b", 0.500, {"x": "i1", "y": "i2", "z": "bz"}),
            ("c", 0.505, {"x": "i1", "y": "i2", "z": "bz"}),
            ("g1", 1.0, {"x": "i1", "y": "by", "z": "bz2"}),
            ("g2", 1.0, {"x": "bx2", "y": "i2", "z": "bz3"}),
            ("g3", 1.0, {"x": "bx9", "y": "by9", "z": "bz"}))
        cfg = toy_cfg()
        plan = prune.plan(recs, cfg, min_carriers=2)
        self.assertEqual(self._ids(plan), ["a", "b"])
        # fixpoint: applying the plan leaves every certificate intact under audit
        aud = prune.audit(recs + [prune_rec(i) for i in self._ids(plan)], cfg, min_carriers=2)
        self.assertEqual(aud["voided"], [])

    def test_diversity_degrades_below_three_axes_with_warning(self):
        recs = [{"id": "base", "parent": None, "mode": "full", "split": "inner", "env": "e1",
                 "fitness": 0.0, "generation": 0,
                 "genome": {"chunks": {"x": {"impl": "bx"}, "y": {"impl": "by"}}}}]
        recs += [rec(i, f, c) for i, f, c in (
            ("a", 0.1, {"x": "i1", "y": "by"}),
            ("b", 1.0, {"x": "i1", "y": "by"}),
            ("c", 1.0, {"x": "i1", "y": "by2"}),
            ("d", 1.0, {"x": "bx", "y": "by"}))]
        plan = prune.plan(recs, toy_cfg(), min_carriers=2)
        self.assertTrue(any("axis(es)" in w for w in plan["warnings"]))
        self.assertIn("a", self._ids(plan))   # satisfiable at div_need=1

    def test_noise_floor_none_warns(self):
        recs = self._pool(("a", 1.0, {"x": "i1", "y": "by", "z": "bz"}))
        plan = prune.plan(recs, toy_cfg(noise_floor=None))
        self.assertTrue(any("noise_floor" in w for w in plan["warnings"]))

    def test_sibling_cluster_cannot_hide_a_diverse_partner(self):
        # Review major (determinism): with >25 identical-sibling carriers plus ONE
        # diverse carrier, the old first-25 window missed the diverse pair under some
        # hash seeds. Signature dedupe collapses the cluster to one entry, so the
        # verdict is a pure function of the archive.
        specs = [("a", 0.1, {"x": "i1", "y": "i2", "z": "i3"})]
        for k in range(30):   # one giant sibling cluster carrying all of a's traits
            specs.append((f"s{k:02d}", 1.0, {"x": "i1", "y": "i2", "z": "i3"}))
        specs.append(("div", 1.0, {"x": "i1", "y": "i2", "z": "bz9"}))
        specs.append(("dv2", 1.0, {"x": "i1", "y": "by9", "z": "i3"}))
        specs.append(("dv3", 1.0, {"x": "bx9", "y": "i2", "z": "i3"}))
        plan = prune.plan(self._pool(*specs), toy_cfg(), min_carriers=2)
        self.assertIn("a", self._ids(plan))

    def test_pair_best_anchor_blocks_combination_ratchet(self):
        # Review finding: the pair check needs the anchor too — a winning combination's
        # best expression must not prune when only strictly-worse carriers hold the pair.
        specs = [("a", 1.0, {"x": "i1", "y": "i2", "z": "bz"}),
                 ("w", 0.995, {"x": "i1", "y": "i2", "z": "bz"}),    # worse pair carrier
                 ("g1", 1.5, {"x": "i1", "y": "by", "z": "bz2"}),    # singles coverage,
                 ("g2", 1.5, {"x": "bx2", "y": "i2", "z": "bz3"}),   # diverse + anchored
                 ("g3", 1.5, {"x": "i1", "y": "by8", "z": "bz"}),
                 ("g4", 1.5, {"x": "bx7", "y": "i2", "z": "bz"})]
        self.assertNotIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                   min_carriers=2)))
        # an at-or-above carrier of the pair flips it
        specs.append(("f", 1.001, {"x": "i1", "y": "i2", "z": "bz"}))
        self.assertIn("a", self._ids(prune.plan(self._pool(*specs), toy_cfg(),
                                                min_carriers=2)))

    def test_audit_honors_the_recorded_min_carriers(self):
        # Review finding: doctor audits at default D=3; a prune made from a D=2 plan must
        # be re-checked at ITS bar (recorded on the record), not today's default.
        recs = self._pool(
            ("a", 0.1, {"x": "i1", "y": "i2", "z": "i3"}),
            ("b", 1.0, {"x": "i1", "y": "i2", "z": "i3"}),
            ("c", 0.9, {"x": "i1", "y": "i2", "z": "bz"}),
            ("e", 0.85, {"x": "i1", "y": "by", "z": "i3"}),
            ("f", 0.8, {"x": "bx", "y": "i2", "z": "i3"}))
        cfg = toy_cfg()
        self.assertEqual(self._ids(prune.plan(recs, cfg, min_carriers=2)), ["a"])
        recs.append({**prune_rec("a"), "min_carriers": 2})
        self.assertEqual(prune.audit(recs, cfg)["voided"], [])         # default 3 ignored
        recs[-1] = {**prune_rec("a"), "min_carriers": 5}               # a bar it can't meet
        self.assertEqual([v["id"] for v in prune.audit(recs, cfg)["voided"]], ["a"])

    def test_audit_detects_voided_certificate(self):
        recs = self._pool(
            ("a", 0.1, {"x": "i1", "y": "i2", "z": "i3"}),
            ("b", 1.0, {"x": "i1", "y": "i2", "z": "i3"}),
            ("c", 0.9, {"x": "i1", "y": "i2", "z": "bz"}),
            ("e", 0.85, {"x": "i1", "y": "by", "z": "i3"}),
            ("f", 0.8, {"x": "bx", "y": "i2", "z": "i3"}))
        cfg = toy_cfg()
        self.assertEqual(self._ids(prune.plan(recs, cfg, min_carriers=2)), ["a"])
        recs.append(prune_rec("a"))
        self.assertEqual(prune.audit(recs, cfg, min_carriers=2)["voided"], [])
        # Retracting one carrier THINS coverage (diversity collapses) but every trait and
        # pair is still carried somewhere: voided, trait_lost False — doctor warns only.
        recs.append(retract_rec("e"))
        aud = prune.audit(recs, cfg, min_carriers=2)
        self.assertEqual([(v["id"], v["trait_lost"]) for v in aud["voided"]], [("a", False)])
        # Retracting the rest of the (i1,i2) pair's carriers LOSES it from the pool
        # entirely: the "never traits" invariant is broken — doctor's check fails.
        recs += [retract_rec("b"), retract_rec("c")]
        aud = prune.audit(recs, cfg, min_carriers=2)
        self.assertEqual([(v["id"], v["trait_lost"]) for v in aud["voided"]], [("a", True)])

    def test_audit_skips_retracted_and_reports_unauditable(self):
        recs = self._pool(("a", 0.5, {"x": "i1", "y": "by", "z": "bz"}))
        recs += [{"id": "m", "parent": "p0", "mode": "full", "split": "inner", "env": "e1",
                  "fitness": 0.4, "generation": 1},          # no genome: explicit prune
                 prune_rec("m"), prune_rec("a"), retract_rec("a")]
        aud = prune.audit(recs, toy_cfg())
        self.assertEqual([u["id"] for u in aud["unauditable"]], ["m"])
        self.assertEqual(aud["voided"], [])   # 'a' retracted → out of selection regardless


class TestPoolConsumers(unittest.TestCase):
    def setUp(self):
        self.recs = [rec("a", 1.0, {"x": "i1"}), rec("b", 0.9, {"x": "i2"}),
                     rec("c", 0.8, {"x": "i3"}), prune_rec("a")]

    def test_selection_pool_is_the_choke_point(self):
        self.assertEqual({r["id"] for r in archive.selection_pool(self.recs, "e1")},
                         {"b", "c"})

    def test_sampling_surfaces_never_offer_pruned(self):
        d = write_archive(self.recs)
        try:
            for seed in range(6):
                parent = archive.sample_parent(d, seed=seed, selection_env="e1")
                self.assertNotEqual(parent["id"], "a")
            insp = archive.sample_inspirations(d, "b", n_top=2, n_archive=2,
                                               selection_env="e1")
            self.assertNotIn("a", [r["id"] for r in insp])
            self.assertEqual([r["id"] for r in archive.leaderboard(d, 10, "e1")], ["b", "c"])
            self.assertEqual([r["id"] for r in archive.leaderboard(d, 10, "e1",
                                                                   include_pruned=True)],
                             ["a", "b", "c"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCliFlows(unittest.TestCase):
    """End-to-end on the toy registry project: hand-built pool → prune → every surface."""

    @classmethod
    def setUpClass(cls):
        cls.root = make_registry_project()
        cls.cfg = cfgmod.load(cls.root)
        impl_dir = cls.root / "evolve" / "chunks" / "objective"
        for impl in ("i1", "i2"):
            (impl_dir / f"{impl}.py").write_text("def value():\n    return 2.0\n")
        recs = [
            {"id": "seed0", "parent": None, "mode": "full", "split": "inner", "env": None,
             "fitness": 1.0, "generation": 0, "inventor": "t",
             "genome": {"chunks": {"objective": {"impl": "baseline"}}}},
            {**rec("k1", 2.0, {"objective": "i1"}, parent="seed0", env=None), "inventor": "t"},
            {**rec("k2", 1.9, {"objective": "i1"}, parent="seed0", env=None), "inventor": "t"},
            {**rec("k3", 1.8, {"objective": "i2"}, parent="seed0", env=None), "inventor": "t"},
        ]
        ap = archive.archive_path(cls.root)
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        git(["add", "-A"], cls.root)          # clones export HEAD: impls must be committed
        git(["commit", "-q", "-m", "pool"], cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup(cls.root)

    def _cli(self, *argv, expect=0):
        r = subprocess.run([str(PLUGIN / "bin" / "evolve"), "--root", str(self.root), *argv],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, msg=r.stderr + r.stdout)
        return r.stdout, r.stderr

    def test_1_prune_refusals_are_batch_atomic(self):
        self._cli("prune", "--ids", "nope", "--reason", "r", expect=1)         # unknown id
        self._cli("prune", "--ids", "seed0", "--reason", "r", expect=1)        # rootless
        self._cli("prune", "--ids", "k2,seed0", "--reason", "r", expect=1)     # batch atomic
        _, err = self._cli("prune", "--ids", "k2", "--reason", "r",
                           "--min-carriers", "0", expect=1)                    # graceful, not a traceback
        self.assertIn("min-carriers", err)
        self._cli("prune", "--plan", "--min-carriers", "-2", expect=1)
        self.assertEqual(archive.pruned_ids(archive.load(self.root)), {})

    def test_2_prune_apply_effect_and_board(self):
        out, _ = self._cli("prune", "--ids", "k2", "--reason", "dominated by k1")
        self.assertEqual(json.loads(out)["pruned"], ["k2"])
        self.assertIn("k2", archive.pruned_ids(archive.load(self.root)))
        rep = board.report(self.root, self.cfg)
        self.assertNotIn("k2", [r["id"] for r in rep["leaderboard"]])
        self.assertEqual(rep["pruned"]["n"], 1)
        rep_all = board.report(self.root, self.cfg, include_pruned=True)
        marked = {r["id"]: r.get("pruned") for r in rep_all["leaderboard"]}
        self.assertTrue(marked["k2"])
        # prune records are bookkeeping, not runs: stats count only the 4 measurements
        self.assertEqual(sum(s["n"] for s in rep["by_inventor"].values()), 4)
        self.assertIn("PRUNED", board.render(rep_all))

    def test_3_double_prune_refused_and_diet_fails_closed(self):
        self._cli("prune", "--ids", "k2", "--reason", "again", expect=1)
        with self.assertRaises(ValueError) as cm:
            diet.build(self.root, self.cfg, parent_id="k2", axis="objective")
        self.assertIn("pruned", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            diet.build(self.root, self.cfg, operator="cross", parent_id="k1",
                       cross_with="k2", axis="objective")
        self.assertIn("pruned", str(cm.exception))

    def test_4_retract_still_works_on_pruned_id(self):
        # Review M-integration: a prune record must not manufacture a phantom env in
        # retract's scope resolution.
        payload = json.dumps({"retract": True, "fitness": None, "guardrail": "post-hoc"})
        rec_ = score.ingest(self.root, self.cfg, result_text=payload, meta={"id": "k2"},
                            mode="full", split="inner")
        self.assertTrue(rec_.get("retract"))
        self.assertIn("k2", archive.retracted_ids(archive.load(self.root)))
        # retraction OUTRANKS prune: the pruned-then-retracted parent gets the validity
        # error (accurate), never "score stays valid, reinstate the prune" (a dead end)
        with self.assertRaises(ValueError) as cm:
            diet.build(self.root, self.cfg, parent_id="k2", axis="objective")
        self.assertIn("VALID", str(cm.exception))
        self.assertNotIn("is pruned", str(cm.exception))

    def test_5_generation_inherits_from_measurement_not_prune_record(self):
        self._cli("prune", "--ids", "k3", "--reason", "covered")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rec_ = score.ingest(self.root, self.cfg,
                                result_text=json.dumps({"fitness": 1.85}),
                                meta={"id": "k3"}, mode="full", split="inner")
        self.assertEqual(rec_["generation"], 1)          # not 0 from the prune record
        self.assertIn("reinstate", err.getvalue())       # the does-not-reinstate note
        self.assertIn("k3", archive.pruned_ids(archive.load(self.root)))   # score ≠ reinstate

    def test_6_reinstate(self):
        self._cli("prune", "--id", "k3", "--reinstate", expect=1)   # needs --reason
        out, _ = self._cli("prune", "--id", "k3", "--reinstate", "--reason", "carriers thinned")
        self.assertEqual(json.loads(out)["reinstated"], "k3")
        self.assertNotIn("k3", archive.pruned_ids(archive.load(self.root)))
        self._cli("prune", "--id", "k3", "--reinstate", "--reason", "again", expect=1)

    def test_6b_reinstate_of_retracted_id_says_so(self):
        # k2 is pruned (test_2) AND retracted (test_4): reversing the prune must not
        # claim a selection re-entry the retraction still blocks.
        out, _ = self._cli("prune", "--id", "k2", "--reinstate", "--reason", "undo prune")
        payload = json.loads(out)
        self.assertIn("retracted", payload["note"])
        self.assertNotIn("k2", archive.pruned_ids(archive.load(self.root)))

    def test_7_plan_respects_pool_and_protections(self):
        out, _ = self._cli("prune", "--plan")
        plan = json.loads(out)
        ids = [p["id"] for p in plan["prunable"]]
        self.assertNotIn("k2", ids)      # already pruned+retracted — not in the pool
        self.assertNotIn("seed0", ids)   # rootless

    def test_8_slots_file_warning_on_in_flight_parent(self):
        rounds.save_slots(self.root, "toyround", [
            {"slot": 0, "parent": "k1", "operator": "diff", "cross_with": None, "seed": "s"}])
        out, _ = self._cli("prune", "--ids", "k1", "--reason", "test in-flight warning")
        payload = json.loads(out)
        # One AGGREGATED warning per id (a campaign's dead rounds must not flood the ceremony)
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertIn("persisted parent/partner", payload["warnings"][0])
        self.assertIn("slot0", payload["warnings"][0])
        self._cli("prune", "--id", "k1", "--reinstate", "--reason", "undo")

    def test_9a_multi_env_id_refused_on_unpinned_project(self):
        # Final-review major: on an unpinned project a prune's partition is resolved from
        # the id's measurement records; several envs = ambiguous evidence = refuse.
        archive.append(self.root, rec("k4", 1.5, {"objective": "i1"}, parent="seed0", env="x1"))
        archive.append(self.root, rec("k4", 1.4, {"objective": "i1"}, parent="seed0", env="x2"))
        _, err = self._cli("prune", "--ids", "k4", "--reason", "r", expect=1)
        self.assertIn("span several envs", err)
        self.assertEqual(archive.pruned_ids(archive.load(self.root)), {})

    def test_9b_reinstate_refuses_ambiguous_streams(self):
        for env in ("x1", "x2"):
            archive.append(self.root, {"id": "k4", "prune": True, "reason": "r", "env": env})
        _, err = self._cli("prune", "--id", "k4", "--reinstate", "--reason", "r", expect=1)
        self.assertIn("several partitions", err)
        # both streams still stand — the refusal reversed nothing
        self.assertEqual(set(prune.standing_streams(archive.load(self.root), "k4")),
                         {"x1", "x2"})
        for env in ("x1", "x2"):   # clean up both streams explicitly
            archive.append(self.root, {"id": "k4", "prune": False, "reason": "undo", "env": env})

    def test_9c_score_paths_note_the_standing_prune(self):
        archive.append(self.root, rec("k5", 1.7, {"objective": "i1"}, parent="seed0", env=None))
        self._cli("prune", "--ids", "k5", "--reason", "covered")
        # retract, then recover via ingest --reinstate: the un-retract must still say the
        # id stays pruned (final-review: the note was dead on exactly this path)
        score.ingest(self.root, self.cfg,
                     result_text=json.dumps({"retract": True, "fitness": None,
                                             "guardrail": "post-hoc"}),
                     meta={"id": "k5"}, mode="full", split="inner")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            score.ingest(self.root, self.cfg, result_text=json.dumps({"fitness": 1.71}),
                         meta={"id": "k5"}, mode="full", split="inner", reinstate=True)
        self.assertIn("does NOT reinstate", err.getvalue())
        # rescore (the other documented recovery path) says it too
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            score.rescore(self.root, self.cfg, rec_id="k5", mode="proxy")
        self.assertIn("does NOT reinstate", err.getvalue())
        self.assertIn("k5", archive.pruned_ids(archive.load(self.root)))


if __name__ == "__main__":
    unittest.main()
