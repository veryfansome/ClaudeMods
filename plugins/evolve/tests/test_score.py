import json
import subprocess
import unittest

from _toy import (make_markers_project, make_registry_project, make_worktree, drop_worktree,
                  set_factor, load_cfg, cleanup, git, BASELINE_IMPL)
from _evolve import archive, score
from _evolve.config import state_dir


def meta(i, **kw):
    return {"id": i, "parent": kw.get("parent"), "inventor": kw.get("inventor", "test"),
            "operator": kw.get("operator", "diff"), "rationale": kw.get("rationale", "t")}


class TestScoreMarkers(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        self.commit = git(["rev-parse", "HEAD"], self.root).strip()

    def tearDown(self):
        cleanup(self.root)

    def _candidate(self, value, rec_id, **kw):
        wt = make_worktree(self.root, rec_id)
        try:
            set_factor(wt, value)
            return score.score_candidate(self.root, self.cfg, meta=meta(rec_id, **kw), worktree=wt, **{
                k: v for k, v in kw.items() if k in ("mode", "split", "force")})
        finally:
            drop_worktree(self.root, wt)

    def test_seed_then_candidate(self):
        seed = score.score_seed(self.root, self.cfg)
        self.assertEqual(seed["fitness"], 1.0)
        self.assertEqual(seed["generation"], 0)
        rec = self._candidate(2.0, "g1", parent="gen0-baseline")
        self.assertEqual(rec["fitness"], 2.0)
        self.assertEqual(rec["guardrail"], "pass")
        self.assertEqual(rec["generation"], 1)
        self.assertEqual(rec["public"], {"factor": 2.0})
        self.assertEqual(rec.get("text_feedback"), "factor=2.0")
        # artifacts: patch + region archived, private kept OUT of the record but written aside
        sd = state_dir(self.root)
        sid = archive._safe_id("g1")
        self.assertTrue((sd / rec["surface"]["patch"]).exists())
        self.assertTrue((sd / "archive" / "regions" / f"{sid}.txt").exists())
        self.assertNotIn("private", rec)
        self.assertTrue((sd / "archive" / "private" / f"{sid}.json").exists())
        self.assertEqual(rec["commit"], self.commit)                # HEAD recorded for reproducibility
        self.assertEqual(archive.best(self.root)["id"], "g1")

    def test_duplicate_rejected_then_forced(self):
        self._candidate(2.0, "g1")
        with self.assertRaises(score.Rejection) as cm:
            self._candidate(2.0, "g2")
        self.assertEqual(cm.exception.kind, "duplicate")
        rec = self._candidate(2.0, "g2", force=True)
        self.assertEqual(rec["fitness"], 2.0)

    def test_rescore_same_id_ok_but_different_payload_rejected(self):
        self._candidate(2.0, "g1")
        self._candidate(2.0, "g1", force=True)                     # paired rerun: fine
        self.assertEqual(len([r for r in archive.load(self.root) if r["id"] == "g1"]), 2)
        with self.assertRaises(ValueError):
            self._candidate(7.0, "g1", force=True)                 # same id, different code

    def test_guard_violation_not_recorded(self):
        wt = make_worktree(self.root, "bad")
        try:
            (wt / "eval.py").write_text("print('gamed')\n")
            set_factor(wt, 9.0)
            with self.assertRaises(score.Rejection) as cm:
                score.score_candidate(self.root, self.cfg, meta=meta("bad"), worktree=wt)
            self.assertEqual(cm.exception.kind, "guard")
        finally:
            drop_worktree(self.root, wt)
        self.assertEqual(archive.load(self.root), [])

    def test_broken_candidate_recorded_as_failure(self):
        rec = self._candidate("1.0 +", "broken")                   # syntax error -> smoke fails
        self.assertIsNone(rec["fitness"])
        self.assertIn("smoke", rec["guardrail"])
        self.assertEqual(archive.valid(archive.load(self.root)), [])

    def test_bad_patch_archives_failure_not_traceback(self):
        pf = self.root / "bad.patch"
        pf.write_text("this is not a valid patch\n")
        try:
            rec = score.score_candidate(self.root, self.cfg, meta=meta("badpatch"),
                                        patch=pf.read_text())
        finally:
            pf.unlink()
        self.assertIsNone(rec["fitness"])                          # recorded failure, no crash
        self.assertIn("patch does not apply", rec["guardrail"])

    def test_final_split_firewalled(self):
        score.score_seed(self.root, self.cfg)
        self._candidate(5.0, "champ", split="final")
        self.assertEqual(archive.best(self.root)["id"], "gen0-baseline")  # final never selects

    def test_full_mode_multi_seed(self):
        rec = self._candidate(2.0, "g1", mode="full")
        self.assertEqual(rec["mode"], "full")
        self.assertEqual(rec["seeds"], [0, 1])

    def test_untracked_file_never_reaches_eval(self):
        # An inventor plants a file the eval would import if scoring ran in its worktree;
        # isolated-export scoring must be blind to it.
        wt = make_worktree(self.root, "sneaky")
        try:
            set_factor(wt, 2.0)
            (wt / "src" / "sitecustomize.py").write_text("raise RuntimeError('planted')\n")
            rec = score.score_candidate(self.root, self.cfg, meta=meta("sneaky"), worktree=wt)
        finally:
            drop_worktree(self.root, wt)
        self.assertEqual(rec["fitness"], 2.0)                      # planted file had no effect

    def test_clone_has_no_git_link_to_parent(self):
        # The isolation rail: eval code must not be able to discover the real repo via .git
        # and reach the archive / private holdout. The export has no .git at all.
        from _evolve import runner
        with runner.pristine_clone(self.root) as clone:
            self.assertFalse((clone / ".git").exists())
            r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=clone,
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)                   # not a git work tree

    def test_malformed_metrics_recorded_not_crash(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["eval"]["proxy"]["cmd"] = (
            "python3 -c \"import pathlib,sys; "
            "pathlib.Path(sys.argv[1],'metrics.json').write_text('{\\\"combined_score\\\": 1.0')\" {results_dir}")
        wt = make_worktree(self.root, "trunc")
        try:
            set_factor(wt, 2.0)
            rec = score.score_candidate(self.root, cfg, meta=meta("trunc"), worktree=wt)
        finally:
            drop_worktree(self.root, wt)
        self.assertIsNone(rec["fitness"])                          # archived as failure, no crash
        self.assertIn("not valid JSON", rec["guardrail"])

    def test_rescore_promotes_reusing_id(self):
        self._candidate(2.0, "g1", parent="gen0-baseline")         # proxy
        full = score.rescore(self.root, self.cfg, rec_id="g1", mode="full")
        self.assertEqual(full["mode"], "full")
        self.assertEqual(full["id"], "g1")
        self.assertEqual(full["seeds"], [0, 1])
        # best_per_id prefers the full record
        self.assertEqual(archive.best(self.root)["mode"], "full")

    def test_final_validation_does_not_inflate_budget(self):
        score.score_seed(self.root, self.cfg)
        self._candidate(2.0, "g1", parent="gen0-baseline")
        before = archive.budget_state(self.root, self.cfg["budget"])["generations"]
        score.rescore(self.root, self.cfg, rec_id="g1", mode="full", split="final")
        after = archive.budget_state(self.root, self.cfg["budget"])["generations"]
        self.assertEqual(before, after)                            # final-split rescore didn't tick generations

    def test_rescore_does_not_inflate_offspring_penalty(self):
        from _evolve.archive import offspring_counts, load
        score.score_seed(self.root, self.cfg)
        self._candidate(2.0, "g1", parent="gen0-baseline")
        score.rescore(self.root, self.cfg, rec_id="g1", mode="full")          # same child, re-scored
        score.rescore(self.root, self.cfg, rec_id="g1", mode="full", split="final")
        # gen0-baseline has exactly ONE distinct child (g1) despite three g1 records
        self.assertEqual(offspring_counts(load(self.root)).get("gen0-baseline"), 1)


class TestScoreRegistry(unittest.TestCase):
    def setUp(self):
        self.root = make_registry_project()
        self.cfg = load_cfg(self.root)

    def tearDown(self):
        cleanup(self.root)

    def test_seed_and_new_impl(self):
        seed = score.score_seed(self.root, self.cfg)
        self.assertEqual(seed["fitness"], 1.0)
        self.assertEqual(seed["genome"]["chunks"]["objective"]["impl"], "baseline")
        impl = self.root / "evolve" / "chunks" / "objective" / "gain.py"
        impl.write_text('"""Contract: value() -> float."""\ndef value():\n    return 4.0\n')
        g = {"chunks": {"objective": {"impl": "gain", "params": {}}}}
        rec = score.score_candidate(self.root, self.cfg, meta=meta("g1", parent="gen0-baseline"),
                                    genome=g)
        self.assertEqual(rec["fitness"], 4.0)                      # untracked impl copied into clone
        self.assertEqual(archive.best(self.root)["id"], "g1")

    def test_unknown_impl_is_guard_rejection(self):
        with self.assertRaises(score.Rejection) as cm:
            score.score_candidate(self.root, self.cfg, meta=meta("g1"),
                                  genome={"chunks": {"objective": {"impl": "ghost"}}})
        self.assertEqual(cm.exception.kind, "guard")

    def test_inplace_impl_rewrite_rejected_on_rescore(self):
        # The untracked-impl integrity rail: an impl referenced by an archived genome must
        # not be silently rewritten and re-scored under the same id.
        impl = self.root / "evolve" / "chunks" / "objective" / "shifty.py"
        impl.write_text('"""v1"""\ndef value():\n    return 3.0\n')
        g = {"chunks": {"objective": {"impl": "shifty", "params": {}}}}
        rec = score.score_candidate(self.root, self.cfg, meta=meta("g1"), genome=g)
        self.assertEqual(rec["fitness"], 3.0)
        impl.write_text('"""v2 — cheating"""\ndef value():\n    return 99.0\n')
        with self.assertRaises(ValueError):                        # id payload changed
            score.score_candidate(self.root, self.cfg, meta=meta("g1"), genome=g, force=True)

    def test_impl_artifact_survives_worktree_loss(self):
        impl = self.root / "evolve" / "chunks" / "objective" / "keep.py"
        impl.write_text('"""keep"""\ndef value():\n    return 5.0\n')
        g = {"chunks": {"objective": {"impl": "keep", "params": {}}}}
        rec = score.score_candidate(self.root, self.cfg, meta=meta("g1"), genome=g)
        art = state_dir(self.root) / rec["surface"]["impls_artifact"]
        self.assertTrue(art.is_dir() and any(art.iterdir()))       # impl code copied for reproducibility

    def test_registry_rescore_survives_impl_deletion(self):
        impl = self.root / "evolve" / "chunks" / "objective" / "gone.py"
        impl.write_text('"""gone"""\ndef value():\n    return 5.0\n')
        g = {"chunks": {"objective": {"impl": "gone", "params": {}}}}
        score.score_candidate(self.root, self.cfg, meta=meta("g1"), genome=g)   # proxy
        impl.unlink()                                              # simulate branch switch / git clean
        full = score.rescore(self.root, self.cfg, rec_id="g1", mode="full")     # must restore + score
        self.assertEqual(full["fitness"], 5.0)
        self.assertTrue(impl.exists())                            # restored from the archived artifact

    def test_near_duplicate_impl_rejected(self):
        d = self.root / "evolve" / "chunks" / "objective"
        (d / "copy1.py").write_text(BASELINE_IMPL.replace("1.0", "1.01"))
        with self.assertRaises(score.Rejection) as cm:
            score.score_candidate(self.root, self.cfg, meta=meta("g1"),
                                  genome={"chunks": {"objective": {"impl": "copy1"}}})
        self.assertEqual(cm.exception.kind, "duplicate")


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)

    def tearDown(self):
        cleanup(self.root)

    def test_ingest_external_result(self):
        text = "cache notes...\n" + json.dumps({"fitness": 0.42, "public": {"k": 1},
                                                "per_seed": [{"seed": 0}]}) + "\n"
        rec = score.ingest(self.root, self.cfg, result_text=text, meta=meta("remote1"),
                           mode="full", split="inner", env="gpu-box-1")
        self.assertEqual(rec["fitness"], 0.42)
        self.assertEqual(rec["env"], "gpu-box-1")
        self.assertEqual(archive.best(self.root, "gpu-box-1")["id"], "remote1")

    def test_ingest_rejects_bool_and_malformed_genome(self):
        with self.assertRaises(ValueError):                        # bool is not a score
            score.ingest(self.root, self.cfg, result_text='{"combined_score": true}',
                         meta=meta("b1"), mode="proxy", split="inner")

    def test_rescore_of_ingested_markers_record_refused(self):
        # An ingested markers record carries only its score, not a patch — rescoring it must
        # NOT silently score the seed under the champion's id.
        text = json.dumps({"fitness": 0.9}) + "\n"
        score.ingest(self.root, self.cfg, result_text=text, meta=meta("remoteX", parent="gen0-baseline"),
                     mode="full", split="inner", env="box")
        with self.assertRaises(ValueError):
            score.rescore(self.root, self.cfg, rec_id="remoteX", mode="proxy")

    def test_ingest_survives_log_braces_and_multiple_objects(self):
        # A remote scoring log with braces and a leading progress object must not defeat the
        # last-object extraction (the greedy-regex bug).
        text = '{"progress": {"pct": 50}}\nrunning... {not json}\n' + \
               json.dumps({"combined_score": 1.5}) + "\n"
        rec = score.ingest(self.root, self.cfg, result_text=text, meta=meta("remote2"),
                           mode="proxy", split="inner", env="box")
        self.assertEqual(rec["fitness"], 1.5)


if __name__ == "__main__":
    unittest.main()
