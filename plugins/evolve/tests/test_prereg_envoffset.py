import unittest

from _toy import (make_markers_project, make_worktree, drop_worktree, set_factor, load_cfg,
                  cleanup, git)
from _evolve import archive, doctor, prereg, score
from _evolve.config import state_dir


def _meta(i, **kw):
    return {"id": i, "parent": kw.get("parent"), "inventor": "t", "operator": "diff",
            "rationale": kw.get("rationale", "t")}


def _score(root, cfg, value, rec_id, parent=None):
    wt = make_worktree(root, rec_id)
    try:
        set_factor(wt, value)
        return score.score_candidate(root, cfg, meta=_meta(rec_id, parent=parent), worktree=wt)
    finally:
        drop_worktree(root, wt)


class TestPrereg(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)                 # noise_floor 0.0 in the toy
        score.score_seed(self.root, self.cfg)          # champion = gen0-baseline @ 1.0

    def tearDown(self):
        cleanup(self.root)

    def test_register_derives_g1_and_is_immutable(self):
        rec = prereg.register(self.root, self.cfg, round_tag="r1", g2_desc="factor up",
                              g2_threshold=0.5, g3_desc="no NaN")
        self.assertEqual(rec["g1_fitness_floor"], 1.0)             # champion 1.0 − noise_floor 0.0
        self.assertEqual(rec["champion"]["id"], "gen0-baseline")
        self.assertEqual(rec["g2"], {"desc": "factor up", "threshold": 0.5})
        self.assertEqual(prereg.active(self.root)["round"], "r1")
        with self.assertRaises(ValueError):                       # immutable
            prereg.register(self.root, self.cfg, round_tag="r1", g2_desc="x")

    def test_register_requires_measured_noise_floor(self):
        cfg = load_cfg(self.root)
        cfg["fitness"]["noise_floor"] = None
        with self.assertRaises(ValueError):
            prereg.register(self.root, cfg, round_tag="r2", g2_desc="x")

    def test_gate_checks_g1_numerically(self):
        prereg.register(self.root, self.cfg, round_tag="r1", g2_desc="factor up", g2_threshold=0.5)
        _score(self.root, self.cfg, 2.0, "win", parent="gen0-baseline")     # 2.0 >= 1.0 floor
        _score(self.root, self.cfg, 0.5, "regress", parent="gen0-baseline") # 0.5 < 1.0 floor
        good = prereg.check_gate(self.root, self.cfg, round_tag="r1", candidate_id="win")
        self.assertTrue(good["g1"]["pass"])
        self.assertFalse(good["promotable_on_g1"])   # G1 holds but it's a proxy score — not yet clinchable
        bad = prereg.check_gate(self.root, self.cfg, round_tag="r1", candidate_id="regress")
        self.assertFalse(bad["g1"]["pass"])
        self.assertIn("G1 FAILS", bad["verdict"])

    def test_gate_unknown_round_or_candidate(self):
        with self.assertRaises(ValueError):
            prereg.check_gate(self.root, self.cfg, round_tag="nope", candidate_id="x")

    def test_active_orders_by_registration_not_filename(self):
        # r10 must be active after r1..r10 despite sorting lexically before r2.
        for n in range(1, 11):
            _score(self.root, self.cfg, 1.0 + n, f"c{n}", parent="gen0-baseline")   # spaced: no dedup
            prereg.register(self.root, load_cfg(self.root), round_tag=f"r{n}", g2_desc=f"round {n}")
        self.assertEqual(prereg.active(self.root)["round"], "r10")   # not "r9"

    def test_round_tag_sanitized(self):
        for bad in ("../escape", "a/b", "", "  ", ".."):
            with self.assertRaises(ValueError):
                prereg.register(self.root, self.cfg, round_tag=bad, g2_desc="x")

    def test_gate_flags_proxy_only_candidate(self):
        prereg.register(self.root, self.cfg, round_tag="r1", g2_desc="up")
        _score(self.root, self.cfg, 2.0, "prox", parent="gen0-baseline")   # proxy mode
        rep = prereg.check_gate(self.root, self.cfg, round_tag="r1", candidate_id="prox")
        self.assertTrue(rep["g1"]["pass"])
        self.assertTrue(rep["proxy_only"])
        self.assertFalse(rep["promotable_on_g1"])                          # proxy can't clinch it
        self.assertIn("PROXY", rep["verdict"])

    def test_register_env_message_when_selection_env_unmatched(self):
        cfg = load_cfg(self.root)
        cfg["fitness"]["selection_env"] = "no-box"
        with self.assertRaises(ValueError) as cm:
            prereg.register(self.root, cfg, round_tag="rX", g2_desc="x")
        self.assertIn("selection_env", str(cm.exception))                  # not "no scored champion yet"


class TestEnvOffset(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        score.score_seed(self.root, self.cfg)
        _score(self.root, self.cfg, 2.0, "champ", parent="gen0-baseline")

    def tearDown(self):
        cleanup(self.root)

    def test_same_env_offset_is_zero_and_comparable(self):
        res = doctor.measure_env_offset(self.root)                # champion re-run in host env
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["offset"], 0.0)                      # deterministic toy eval
        self.assertTrue(res["comparable"])
        self.assertTrue(any("already scored in this env" in w for w in res["warnings"]))
        # stored with provenance
        cfg = load_cfg(self.root)
        env = res["current_env"]
        self.assertIn(env, cfg["fitness"]["env_offsets"])
        self.assertIn("proxy_cmd_sha", cfg["fitness"]["env_offsets"][env])

    def test_offset_against_other_env_via_same_id_ingest(self):
        # The SAME candidate 'champ' (scored locally with a patch) is also ingested as scored on
        # a GPU box at 5.0. best_per_id -> the GPU record (ref); re-running champ's local code
        # here yields 2.0 -> offset -3.0, and the reproducible-record selection finds the code
        # even though the latest 'champ' record (the ingest) carries none.
        score.ingest(self.root, self.cfg, result_text='{"fitness": 5.0}',
                     meta=_meta("champ", parent="gen0-baseline"), mode="full", split="inner",
                     env="gpu-box")
        res = doctor.measure_env_offset(self.root, ref_id="champ")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ref_env"], "gpu-box")               # best record is the GPU one
        self.assertEqual(res["fitness_here"], 2.0)                # reproduced from the local patch
        self.assertEqual(res["offset"], -3.0)
        self.assertFalse(res["comparable"])                       # |−3| > noise_floor 0.0

    def test_dry_does_not_write(self):
        before = load_cfg(self.root)["fitness"].get("env_offsets") or {}
        doctor.measure_env_offset(self.root, dry=True)
        after = load_cfg(self.root)["fitness"].get("env_offsets") or {}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
