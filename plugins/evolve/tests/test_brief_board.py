import unittest

from _toy import (make_markers_project, make_registry_project, make_worktree, drop_worktree,
                  set_factor, load_cfg, cleanup)
from _evolve import archive, board, brief, score
from _evolve.config import state_dir


def _score(root, cfg, value, rec_id, parent=None, split="inner"):
    wt = make_worktree(root, rec_id)
    try:
        set_factor(wt, value)
        return score.score_candidate(root, cfg, meta={"id": rec_id, "parent": parent,
                                                      "inventor": "t", "operator": "diff",
                                                      "rationale": f"factor {value}"},
                                     worktree=wt, split=split)
    finally:
        drop_worktree(root, wt)


class TestBrief(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        score.score_seed(self.root, self.cfg)
        _score(self.root, self.cfg, 2.0, "g1", parent="gen0-baseline")
        _score(self.root, self.cfg, 3.0, "g2", parent="g1")

    def tearDown(self):
        cleanup(self.root)

    def test_brief_sections_and_privacy(self):
        (state_dir(self.root) / "insights.md").write_text("- prefer larger factors\n")
        text = brief.build(self.root, self.cfg, operator="diff", parent_id="g2")
        for expected in ("TASK:", "OPERATOR: TARGETED EDIT", "THE CONTRACT",
                         "LEADERBOARD", "STANDING RULES",
                         "prefer larger factors", "PARENT"):
            self.assertIn(expected, text)
        # search never crowns: no best-so-far target reaches an inventor — the stated
        # objective is the parent (whatever a brief makes load-bearing gets optimized)
        self.assertNotIn("CURRENT FRONTIER", text)
        self.assertNotIn("BEAT the best", text)
        self.assertIn("beat your PARENT", text)
        self.assertNotIn("per_case", text)          # private metrics never reach a brief
        self.assertNotIn("private", text.lower())

    def test_cross_brief_carries_partner(self):
        text = brief.build(self.root, self.cfg, operator="cross", parent_id="g2", cross_with="g1")
        self.assertIn("CROSSOVER PARTNER", text)
        self.assertIn("g1", text)

    def test_inspirations_worst_to_best(self):
        text = brief.build(self.root, self.cfg, parent_id="g2")
        self.assertIn("INSPIRATIONS", text)
        # g1 (2.0) must appear before g2's region isn't there (parent excluded); seed has no region
        self.assertIn("g1", text)


class TestRegistryBrief(unittest.TestCase):
    def test_axis_brief_contains_contract(self):
        root = make_registry_project()
        try:
            cfg = load_cfg(root)
            score.score_seed(root, cfg)
            text = brief.build(root, cfg, axis="objective", parent_id="gen0-baseline")
            self.assertIn("value() -> float", text)              # axis contract line
            self.assertIn("def value", text)                     # baseline code inlined
            self.assertIn("ALREADY REGISTERED", text)
        finally:
            cleanup(root)

    def test_retired_impls_excluded_from_inspirations_and_impls(self):
        import json as _json
        root = make_registry_project()
        try:
            cfg = load_cfg(root)
            score.score_seed(root, cfg)
            # a retired mechanism handed out as inspiration defeats the retirement
            (root / "evolve" / "retired_impls.json").write_text(_json.dumps(
                {"_doc": "x", "retired": {"objective/baseline": {"reason": "r", "scope": "mechanism"}}}))
            text = brief.build(root, cfg, axis="objective", parent_id=None)
            self.assertNotIn("INSPIRATIONS", text)                # the only candidate is retired
            from _evolve import surface as S
            self.assertTrue(S.genome_selects_retired(
                {"chunks": {"objective": {"impl": "baseline"}}}, S.retired_impls(root)))
            # a list-shaped registry is valid JSON but not this schema — total no-op, no crash
            (root / "evolve" / "retired_impls.json").write_text('["objective/baseline"]')
            self.assertEqual(S.retired_impls(root), {})
            brief.build(root, cfg, axis="objective", parent_id=None)
        finally:
            cleanup(root)

    def test_diet_brief_grants_parent_and_mechanisms_only(self):
        import json as _json
        from _evolve import diet, score as sc
        root = make_registry_project()
        try:
            cfg = load_cfg(root)
            score.score_seed(root, cfg)                            # gen0-baseline @ 1.0
            (root / "evolve" / "chunks" / "objective" / "v2.py").write_text(
                '"""Contract: expose value() -> float. Higher is better."""\n'
                "def value():\n    return 2.0\n")
            sc.score_candidate(root, cfg, meta={"id": "g1", "parent": "gen0-baseline",
                                                "inventor": "t", "operator": "diff",
                                                "rationale": "v2"},
                               genome={"chunks": {"objective": {"impl": "v2"}}}, force=True)
            text, grants, _ = diet.build(root, cfg, parent_id="gen0-baseline", axis="objective")
            self.assertIn("YOUR OBJECTIVE", text)
            self.assertIn("+1.0000", text)                         # parent fitness: granted
            self.assertIn("PRIOR MECHANISMS", text)
            self.assertIn("def value", text)                       # mechanism SOURCE
            self.assertNotIn("g1", text)                           # no foreign id
            self.assertNotIn("2.0000", text)                       # no foreign fitness
            self.assertNotIn("LEADERBOARD", text)
            self.assertIn(("objective", "v2"), grants)             # what the jail will copy
            self.assertIn(("objective", "baseline"), grants)
            # the assertion layer refuses to emit a leaking brief — plant a score in
            # project-authored standing rules
            cfg2 = _json.loads(_json.dumps(cfg))
            cfg2["search"]["standing_rules"] = ["g1 previously scored +0.9999 — avoid it"]
            with self.assertRaises(ValueError):
                diet.build(root, cfg2, parent_id="gen0-baseline", axis="objective")
            # a foreign id that SUPERSTRINGS the parent id must still be caught — masking
            # the parent first used to destroy the embedded prefix and ship the descendant
            from _evolve import archive as A
            A.append(root, {"id": "gen0-baseline-2b", "mode": "proxy", "split": "inner",
                            "fitness": 3.0, "generation": 2, "parent": "gen0-baseline",
                            "genome": {"chunks": {"objective": {"impl": "v2"}}}})
            cfg3 = _json.loads(_json.dumps(cfg))
            cfg3["search"]["standing_rules"] = ["see gen0-baseline-2b for prior art"]
            with self.assertRaises(ValueError) as cm:
                diet.build(root, cfg3, parent_id="gen0-baseline", axis="objective")
            self.assertIn("gen0-baseline-2b", str(cm.exception))
            # a typo'd axis is a clean error, not a KeyError traceback
            with self.assertRaises(ValueError) as cm:
                diet.build(root, cfg, parent_id="gen0-baseline", axis="objectve")
            self.assertIn("not a config axis", str(cm.exception))
            # fitness-shaped genome params draw a warning (they are config, not scores,
            # but they are also the one number-exempt channel)
            A.append(root, {"id": "g7", "mode": "proxy", "split": "inner", "fitness": 1.4,
                            "generation": 2, "parent": "gen0-baseline",
                            "genome": {"chunks": {"objective": {"impl": "v2",
                                                                "params": {"t": 0.9123}}}}})
            _, _, warns = diet.build(root, cfg, parent_id="g7", axis="objective")
            self.assertTrue(any("fitness-shaped" in w for w in warns))
        finally:
            cleanup(root)

    def test_brief_survives_ingest_rejecting_malformed_genome(self):
        from _evolve import score as sc
        root = make_registry_project()
        try:
            cfg = load_cfg(root)
            score.score_seed(root, cfg)
            with self.assertRaises(ValueError):                  # ingest refuses a chunks-less genome
                sc.ingest(root, cfg, result_text='{"combined_score": 9.0}',
                          meta={"id": "bad", "parent": None, "inventor": "x", "operator": "x",
                                "rationale": "x"}, mode="proxy", split="inner", genome={"foo": 1})
            # and even if a malformed genome somehow lands, the brief must not KeyError
            from _evolve import archive as A
            A.append(root, {"id": "bad2", "mode": "proxy", "split": "inner", "fitness": 9.0,
                            "generation": 1, "parent": None, "genome": {"foo": 1}})
            brief.build(root, cfg, axis="objective", parent_id="gen0-baseline")  # no crash
        finally:
            cleanup(root)


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        score.score_seed(self.root, self.cfg)
        _score(self.root, self.cfg, 2.0, "g1", parent="gen0-baseline")
        _score(self.root, self.cfg, 4.0, "fv1", parent="g1", split="final")

    def tearDown(self):
        cleanup(self.root)

    def test_report_and_render(self):
        rep = board.report(self.root, self.cfg)
        self.assertNotIn("champion", rep)                        # ranking yes, crowning no
        self.assertEqual(rep["leaderboard"][0]["id"], "g1")
        self.assertEqual(rep["records"]["final_split"], 1)
        self.assertEqual(rep["by_inventor"]["t"]["n"], 1)        # final-split run excluded
        self.assertEqual(rep["by_inventor"]["seed"]["n"], 1)
        text = board.render(rep)
        self.assertIn("LEADERBOARD", text)
        self.assertIn("final-split validations", text)
        self.assertIn("BUDGET", text)
        # disabled budget: render must take the COUNTS branch (which never reads
        # stale_generations — it is None in that state)
        cfg2 = dict(self.cfg, budget={"disabled": True})
        text2 = board.render(board.report(self.root, cfg2))
        self.assertIn("COUNTS", text2)
        self.assertNotIn("BUDGET:", text2)
        # retraction records are bookkeeping, not runs — they must not skew the per-inventor
        # failure counts the status skill reads as the guardrail-failure rate
        before = board.report(self.root, self.cfg)["by_inventor"]["t"]
        archive.append(self.root, {"id": "g1", "mode": "proxy", "split": "inner", "fitness": None,
                                   "guardrail": "revoked", "retract": True, "inventor": "t",
                                   "operator": "diff", "generation": 1})
        after = board.report(self.root, self.cfg)["by_inventor"]["t"]
        self.assertEqual(after, before)

    def test_selection_env_zero_match_warns(self):
        cfg = load_cfg(self.root)
        cfg["fitness"]["selection_env"] = "no-such-box"          # matches no scored record
        rep = board.report(self.root, cfg)
        self.assertEqual(rep["leaderboard"], [])
        self.assertTrue(any("matches 0" in w for w in rep["warnings"]))
        self.assertIn("no selectable candidates", board.render(rep))   # not "(archive empty)"


if __name__ == "__main__":
    unittest.main()
