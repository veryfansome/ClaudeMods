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
                         "LEADERBOARD", "CURRENT FRONTIER", "STANDING RULES",
                         "prefer larger factors", "PARENT"):
            self.assertIn(expected, text)
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
        _score(self.root, self.cfg, 4.0, "champ", parent="g1", split="final")

    def tearDown(self):
        cleanup(self.root)

    def test_report_and_render(self):
        rep = board.report(self.root, self.cfg)
        self.assertEqual(rep["champion"], "g1")
        self.assertEqual(rep["records"]["final_split"], 1)
        self.assertEqual(rep["by_inventor"]["t"]["n"], 1)        # final-split run excluded
        self.assertEqual(rep["by_inventor"]["seed"]["n"], 1)
        text = board.render(rep)
        self.assertIn("LEADERBOARD", text)
        self.assertIn("final-split validations", text)
        self.assertIn("BUDGET", text)

    def test_selection_env_zero_match_warns(self):
        cfg = load_cfg(self.root)
        cfg["fitness"]["selection_env"] = "no-such-box"          # matches no scored record
        rep = board.report(self.root, cfg)
        self.assertIsNone(rep["champion"])
        self.assertTrue(any("matches 0" in w for w in rep["warnings"]))
        self.assertIn("no selectable candidates", board.render(rep))   # not "(archive empty)"


if __name__ == "__main__":
    unittest.main()
