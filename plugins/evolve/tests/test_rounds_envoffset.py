import json
import unittest

from _toy import (make_markers_project, make_worktree, drop_worktree, set_factor, load_cfg,
                  cleanup, git)
from _evolve import archive, doctor, rounds, score
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


class TestRounds(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)                 # noise_floor 0.0 in the toy
        score.score_seed(self.root, self.cfg)

    def tearDown(self):
        cleanup(self.root)

    def test_save_slots_idempotent_but_never_silently_rewritten(self):
        slots = [{"slot": 0, "parent": "gen0-baseline", "operator": "diff", "seed": "r1:0"}]
        p = rounds.save_slots(self.root, "r1", slots)
        self.assertTrue(p.exists())
        self.assertEqual(rounds.save_slots(self.root, "r1", slots), p)     # same content: no-op
        changed = [dict(slots[0], parent="other")]                         # archive grew: refuse —
        with self.assertRaises(ValueError):                                # the file is evidence
            rounds.save_slots(self.root, "r1", changed)
        rounds.save_slots(self.root, "r1 v2/x", changed)                   # odd seeds still get a file


class TestEnvOffset(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        score.score_seed(self.root, self.cfg)
        _score(self.root, self.cfg, 2.0, "ref1", parent="gen0-baseline")

    def tearDown(self):
        cleanup(self.root)

    def test_same_env_offset_is_zero_and_comparable(self):
        res = doctor.measure_env_offset(self.root)                # top-scoring record re-run in host env
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
        # The SAME candidate 'ref1' (scored locally with a patch) is also ingested as scored on
        # a GPU box at 5.0. best_per_id -> the GPU record (ref); re-running ref1's local code
        # here yields 2.0 -> offset -3.0, and the reproducible-record selection finds the code
        # even though the latest 'ref1' record (the ingest) carries none.
        score.ingest(self.root, self.cfg, result_text='{"fitness": 5.0}',
                     meta=_meta("ref1", parent="gen0-baseline"), mode="full", split="inner",
                     env="gpu-box")
        res = doctor.measure_env_offset(self.root, ref_id="ref1")
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
