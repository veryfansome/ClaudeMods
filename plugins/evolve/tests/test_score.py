import contextlib
import io
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

    def test_retracted_seed_reinstates_via_rescore(self):
        # valid()'s docstring promises "a later rescore under a fixed eval reinstates the id" —
        # that must hold for the markers seed too, whose record carries no patch (a retraction
        # appended after it must not mask the seed-shaped record from rescore's replay).
        score.score_seed(self.root, self.cfg)
        score.ingest(self.root, self.cfg,
                     result_text='{"retract": true, "fitness": null, "guardrail": "eval invalid"}',
                     meta=meta("gen0-baseline"), mode="proxy", split="inner",
                     env=score.default_env())
        self.assertIsNone(archive.best(self.root))
        rec = score.rescore(self.root, self.cfg, rec_id="gen0-baseline", mode="proxy")
        self.assertEqual(rec["fitness"], 1.0)
        self.assertEqual(archive.best(self.root)["id"], "gen0-baseline")   # reinstated

    def test_final_split_firewalled(self):
        score.score_seed(self.root, self.cfg)
        self._candidate(5.0, "fv1", split="final")
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

    def test_rescore_to_full_reuses_id(self):
        self._candidate(2.0, "g1")                                 # proxy
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
        # NOT silently score the seed under another candidate's id.
        text = json.dumps({"fitness": 0.9}) + "\n"
        score.ingest(self.root, self.cfg, result_text=text, meta=meta("remoteX"),
                     mode="full", split="inner", env="box")
        with self.assertRaises(ValueError):
            score.rescore(self.root, self.cfg, rec_id="remoteX", mode="proxy")

    def test_empty_parent_rejected_on_all_paths(self):
        # --parent "" is falsy: next_generation guards it, but explicit --generation and
        # known-id ingests skip that lookup — the bogus lineage field must not archive
        m = meta("k1", parent="")
        m["generation"] = 3
        with self.assertRaises(ValueError):
            score.ingest(self.root, self.cfg, result_text='{"fitness": 0.5}', meta=m,
                         mode="full", split="inner")

    def test_final_split_ingest_allowed_for_retracted_id(self):
        # the holdout never feeds selection and never evicts a verdict — refusing it would
        # block report-only validation while promising a reinstatement that can't happen
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("r1"),
                     mode="full", split="inner", env="box")
        score.ingest(self.root, self.cfg,
                     result_text='{"retract": true, "fitness": null, "guardrail": "revoked"}',
                     meta=meta("r1"), mode="full", split="inner", env="box")
        rec = score.ingest(self.root, self.cfg, result_text='{"fitness": 0.7}', meta=meta("r1"),
                           mode="full", split="final", env="box")
        self.assertEqual(rec["fitness"], 0.7)                      # report-only: fine
        self.assertIn("r1", archive.retracted_ids(archive.load(self.root), cross_partition=True))

    def test_ingest_unknown_parent_errors_unless_generation_given(self):
        # The silent max+1 fallback once stamped a 74-record bulk ingest as generations 66-81
        # ("stale for 74 generations", sampling refused) — an unknown parent is now an error,
        # with --generation as the deliberate escape for cross-archive lineage.
        with self.assertRaises(ValueError):
            score.ingest(self.root, self.cfg, result_text='{"fitness": 0.5}',
                         meta=meta("kid", parent="ghost"), mode="full", split="inner")
        m = meta("kid", parent="ghost")
        m["generation"] = 5
        rec = score.ingest(self.root, self.cfg, result_text='{"fitness": 0.5}',
                           meta=m, mode="full", split="inner")
        self.assertEqual(rec["generation"], 5)
        self.assertEqual(rec["parent"], "ghost")

    def test_ingest_survives_log_braces_and_multiple_objects(self):
        # A remote scoring log with braces and a leading progress object must not defeat the
        # last-object extraction (the greedy-regex bug).
        text = '{"progress": {"pct": 50}}\nrunning... {not json}\n' + \
               json.dumps({"combined_score": 1.5}) + "\n"
        rec = score.ingest(self.root, self.cfg, result_text=text, meta=meta("remote2"),
                           mode="proxy", split="inner", env="box")
        self.assertEqual(rec["fitness"], 1.5)

    def test_ingest_retraction(self):
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("r1", parent=None),
                     mode="full", split="inner", env="box")
        m = meta("r1")
        m["generation"] = 7
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.95}', meta=m,
                     mode="full", split="inner", env="box")
        rec = score.ingest(self.root, self.cfg,
                           result_text='{"retract": true, "fitness": null, "guardrail": "causality violation"}',
                           meta=meta("r1"), mode="full", split="inner", env="box")
        self.assertTrue(rec["retract"])
        # bookkeeping, not search work: stamps the id's LATEST in-scope generation, not max+1
        self.assertEqual(rec["generation"], 7)
        self.assertIsNone(archive.best(self.root, "box"))          # out of selection

    def test_ingest_retraction_env_scope_resolution(self):
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("c1"),
                     mode="full", split="inner", env="box")
        score.ingest(self.root, self.cfg, result_text='{"fitness": 1.1}', meta=meta("c1"),
                     mode="full", split="inner", env="gpu")
        retract = '{"retract": true, "fitness": null, "guardrail": "bad"}'
        with self.assertRaises(ValueError):                        # two envs: must say which scope
            score.ingest(self.root, self.cfg, result_text=retract, meta=meta("c1"),
                         mode="full", split="inner")
        with self.assertRaises(ValueError):                        # out-of-scope env cleans nothing
            score.ingest(self.root, self.cfg, result_text=retract, meta=meta("c1"),
                         mode="full", split="inner", env="nope")
        score.ingest(self.root, self.cfg, result_text=retract, meta=meta("c1"),
                     mode="full", split="inner", env="box")
        recs = archive.load(self.root)
        self.assertEqual(archive.valid(recs, selection_env="box"), [])
        self.assertEqual([r["fitness"] for r in archive.valid(recs, selection_env="gpu")], [1.1])

    def test_ingest_retraction_inherits_single_env(self):
        # forgetting --env must not land the retraction in "external" where it cleans nothing
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("c1"),
                     mode="full", split="inner", env="box")
        rec = score.ingest(self.root, self.cfg,
                           result_text='{"retract": true, "fitness": null, "guardrail": "bad"}',
                           meta=meta("c1"), mode="full", split="inner")
        self.assertEqual(rec["env"], "box")
        self.assertIsNone(archive.best(self.root, "box"))

    def test_ingest_known_id_reuses_generation_and_hints_on_failure(self):
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.5}', meta=meta("k1"),
                     mode="full", split="inner", env="box")        # gen 0 in an empty archive
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rec = score.ingest(self.root, self.cfg, result_text='{"fitness": null}',
                               meta=meta("k1"), mode="full", split="inner", env="box")
        self.assertEqual(rec["generation"], 0)                     # re-observation: no max+1 tick
        self.assertIn("does NOT retract", buf.getvalue())          # and the operator is told why
        self.assertEqual([r["fitness"] for r in archive.valid(archive.load(self.root))], [0.5])

    def test_ingest_rejects_falsy_correct_with_fitness(self):
        # the eval contract's correct is a hard failure on live evals for ANY falsy value
        # (runner._score_of: false, 0, null); ingest must match, not just literal false —
        # a remote adapter serializing correctness as 0/1 must not record a passing score
        for payload in ('{"fitness": 0.5, "correct": false}',
                        '{"fitness": 0.5, "correct": 0}',
                        '{"fitness": 0.5, "correct": null}'):
            with self.assertRaises(ValueError):
                score.ingest(self.root, self.cfg, result_text=payload,
                             meta=meta("r1"), mode="full", split="inner")
        rec = score.ingest(self.root, self.cfg, result_text='{"fitness": null, "correct": false}',
                           meta=meta("r1"), mode="full", split="inner")
        self.assertIsNone(rec["fitness"])                          # inert failure, not retraction
        self.assertNotIn("retract", rec)

    def test_ingest_refuses_numeric_for_retracted_id_unless_reinstate(self):
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("r1"),
                     mode="full", split="inner", env="box")
        score.ingest(self.root, self.cfg,
                     result_text='{"retract": true, "fitness": null, "guardrail": "revoked"}',
                     meta=meta("r1"), mode="full", split="inner", env="box")
        with self.assertRaises(ValueError):                        # a wave must not launder it
            score.ingest(self.root, self.cfg, result_text='{"fitness": 1.1}', meta=meta("r1"),
                         mode="full", split="inner", env="other-box")
        rec = score.ingest(self.root, self.cfg, result_text='{"fitness": null}', meta=meta("r1"),
                           mode="full", split="inner", env="box")   # inert failure: fine
        self.assertIsNone(rec["fitness"])
        rec = score.ingest(self.root, self.cfg, result_text='{"fitness": 1.1}', meta=meta("r1"),
                           mode="full", split="inner", env="box", reinstate=True)
        self.assertEqual(rec["fitness"], 1.1)                      # deliberate reinstatement
        self.assertEqual(archive.retracted_ids(archive.load(self.root), cross_partition=True), {})

    def test_ingest_retraction_guards(self):
        with self.assertRaises(ValueError):                        # unknown id retracts nothing
            score.ingest(self.root, self.cfg, result_text='{"retract": true, "fitness": null}',
                         meta=meta("nope"), mode="full", split="inner")
        score.ingest(self.root, self.cfg, result_text='{"fitness": 0.9}', meta=meta("r1"),
                     mode="full", split="inner")
        with self.assertRaises(ValueError):                        # retraction can't carry a score
            score.ingest(self.root, self.cfg, result_text='{"retract": true, "fitness": 0.5}',
                         meta=meta("r1"), mode="full", split="inner")


if __name__ == "__main__":
    unittest.main()
