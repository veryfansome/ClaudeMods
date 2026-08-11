"""score: the one path from candidate to archived record.

Pipeline: guard (structural, exit 4) -> novelty dedup (exit 5) -> isolated HEAD export ->
setup -> smoke/guardrails -> tier eval -> record. Guard and novelty rejections are Reflexion
feedback, not archive signal — they exit without recording so the skill can re-brief the
inventor. INFRASTRUCTURE failures (export, dependency setup) also don't archive — they aren't
the candidate's fault. Everything else downstream of the clone IS the candidate's own code
failing (patch won't apply, smoke fails, timeout, NaN, malformed metrics) and records fitness
null with a reason (failures teach the search).
"""

import hashlib
import json
import platform
import shutil
import subprocess
import sys

from . import archive, novelty, runner, surface
from .config import DEFAULT_SPLIT, FINAL_SPLIT


class Rejection(Exception):
    def __init__(self, kind, detail):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def default_env():
    return f"{platform.system().lower()}-{platform.machine()}"


def _head_commit(root):
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _regions_dir(root):
    return archive.archive_dir(root) / "regions"


def _diffs_dir(root):
    return archive.archive_dir(root) / "diffs"


def _impls_artifact_dir(root):
    return archive.archive_dir(root) / "impls"


def _scored_safe_ids(root):
    """safe_ids of candidates that have at least one numerically-scored record — the only
    ones dedup compares against (a near-dup of a FAILED candidate is worth letting through:
    it might be the fix). Also keeps init's sabotage probe from poisoning the corpus.
    Uses scored_any, not valid(): a RETRACTED id stays in dedup memory."""
    return {archive._safe_id(r["id"]) for r in archive.scored_any(archive.load(root))}


def _archived_regions(root):
    """(real_id, region_text) for each SCORED candidate that has a region on disk. Keyed by the
    human-facing id (not the mangled _safe_id filename), so a rejection names a real record."""
    d = _regions_dir(root)
    if not d.is_dir():
        return []
    scored = _scored_safe_ids(root)
    by_safe = {archive._safe_id(r["id"]): r["id"] for r in archive.scored_any(archive.load(root))}
    return [(by_safe.get(p.stem, p.stem), p.read_text()) for p in sorted(d.iterdir())
            if p.suffix == ".txt" and p.stem in scored]


def _dedup_markers(root, cfg, mutable, force, self_id=None):
    thr = cfg["search"]["dedup_similarity"]
    rivals = [(rid, text) for rid, text in _archived_regions(root) if rid != self_id]
    ratio, rival = novelty.nearest(mutable, rivals)
    if not force and rival is not None and ratio > thr:
        raise Rejection("duplicate", f"mutable region is {ratio:.3f} similar to archived candidate "
                                     f"{rival!r} (threshold {thr}) — differ mechanistically or rescore with --force")


def _dedup_registry(root, cfg, genome, force):
    """Dedup bites on NEW impl code vs. the axis's existing impls (a genome that only
    re-selects existing impls is either novel recombination or an exact resubmission)."""
    thr = cfg["search"]["dedup_similarity"]
    for axis, spec in genome.get("chunks", {}).items():
        p = surface.impl_path(root, cfg, axis, spec["impl"])
        rel = str(p.relative_to(root))
        if rel in surface.changed_files(root) or rel in surface.untracked_files(root):
            rivals = [(name, surface.impl_path(root, cfg, axis, name).read_text())
                      for name in surface.list_impls(root, cfg, axis) if name != spec["impl"]]
            ratio, rival = novelty.nearest(p.read_text(), rivals)
            if not force and rival is not None and ratio > thr:
                raise Rejection("duplicate", f"new {axis} impl {spec['impl']!r} is {ratio:.3f} similar "
                                             f"to existing impl {rival!r} (threshold {thr})")


def score_candidate(root, cfg, *, mode="proxy", split=DEFAULT_SPLIT, meta,
                    worktree=None, patch=None, genome=None, force=False, env=None,
                    no_archive=False):
    """Score one candidate and append the record. Exactly one of (worktree|patch|genome)
    describes the candidate. no_archive runs the full pipeline but returns the record
    WITHOUT persisting it (init's sabotage/boundary probes). Returns the record.
    Raises runner.InfraError (not archived) on export/setup failure."""
    surf_mode = cfg["surface"]["mode"]
    rec = {k: meta.get(k) for k in archive.META_KEYS}
    if not rec.get("id"):
        raise ValueError("meta.id is required")
    if rec.get("parent") == "":
        raise ValueError('--parent "" is empty — pass a real archived id or omit the flag')
    # Lazy on purpose: an explicit generation (rescore replays, --generation) must not run
    # next_generation's parent lookup at all — its unknown-parent error is for NEW records.
    gen = meta.get("generation")
    rec["generation"] = archive.next_generation(root, rec.get("parent")) if gen is None else gen
    rec.update({"mode": mode, "split": split, "env": env or default_env(),
                "commit": _head_commit(root)})

    mutable = None
    if surf_mode == "markers":
        if genome is not None:
            raise ValueError("this project is markers-mode; --genome does not apply")
        if worktree is not None:
            report = surface.guard_markers(worktree, cfg)
            if not report["ok"]:
                raise Rejection("guard", "; ".join(report["violations"]))
            patch = surface.extract_patch(worktree)
            mutable = surface.candidate_mutable_text(worktree, cfg)
        elif patch is None:
            raise ValueError("markers mode needs --candidate <worktree> or --patch <file> (or --seed)")
        rec["surface"] = {"mode": "markers"}
    else:
        if worktree is not None or patch is not None:
            raise ValueError("this project is registry-mode; score with --genome")
        if genome is None:
            raise ValueError("registry mode needs --genome <file> (or --seed)")
        report = surface.guard_registry(genome, root, cfg)
        if not report["ok"]:
            raise Rejection("guard", "; ".join(report["violations"]))
        rec["surface"] = {"mode": "registry"}
        rec["genome"] = genome

    if surf_mode == "markers" and patch and patch.strip():
        if mutable is None:
            # --patch flow (no worktree): extract the mutable region for dedup/payload. A
            # non-applying patch is a candidate failure — skip the check and let the main
            # scoring below archive it, rather than crashing the pre-pass.
            try:
                mutable = _mutable_from_patch(root, cfg, patch)
            except runner.EvalFailure:
                mutable = None
        if mutable is not None:
            _check_id_payload(root, rec["id"], mutable_sha=surface_sha(mutable))
            _dedup_markers(root, cfg, mutable, force, self_id=rec["id"])
    elif surf_mode == "registry":
        _check_id_payload(root, rec["id"], impl_shas=surface.impl_shas(genome, root, cfg))
        _dedup_registry(root, cfg, genome, force)

    result, failure = None, None
    with runner.pristine_clone(root) as clone:          # InfraError here propagates (not archived)
        genome_path = None
        if surf_mode == "registry":
            runner.copy_files(clone, root, surface.genome_impl_files(genome, root, cfg))
            genome_path = runner.write_genome(clone, genome)
        try:
            if surf_mode == "markers":
                runner.apply_patch(clone, patch or "")
            runner.run_setup(clone, cfg)                # InfraError (setup) still propagates
            runner.run_gates(clone, cfg, genome_path=genome_path, split=split, mode=mode)
            result = runner.evaluate_tier(clone, cfg, mode, split, genome_path=genome_path)
        except runner.EvalFailure as e:
            failure = str(e)

    if failure:
        rec.update({"fitness": None, "guardrail": failure})
    else:
        rec.update({"fitness": result["fitness"], "guardrail": "pass",
                    "seeds": result["seeds"], "per_seed": result["per_seed"],
                    "public": result["public"]})
        if result["text_feedback"]:
            rec["text_feedback"] = result["text_feedback"]

    scored = rec["fitness"] is not None
    if not no_archive:
        _persist_artifacts(root, cfg, rec, patch, mutable, genome, scored)
        archive.append(root, rec)
        if scored and result and result.get("private"):
            archive.write_private(root, rec["id"], result["private"])
    return rec


def _persist_artifacts(root, cfg, rec, patch, mutable, genome, scored):
    """Reproduction + brief corpus. Diffs/impl copies are kept for ALL candidates (debug +
    `apply` survives `git clean`); the region/impl-hash corpus that feeds dedup + inspirations
    is kept only for SCORED candidates."""
    sid = archive._safe_id(rec["id"])
    if rec["surface"]["mode"] == "markers" and patch and patch.strip():
        d = _diffs_dir(root); d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.patch").write_text(patch)
        rec["surface"]["patch"] = f"archive/diffs/{sid}.patch"
        if mutable is not None and scored:
            rd = _regions_dir(root); rd.mkdir(parents=True, exist_ok=True)
            (rd / f"{sid}.txt").write_text(mutable)
            rec["surface"]["mutable_sha"] = surface_sha(mutable)
    elif rec["surface"]["mode"] == "registry" and genome is not None:
        rec["surface"]["impl_shas"] = surface.impl_shas(genome, root, cfg)
        # Copy referenced impl code so brief/apply/re-score survive a working-tree change
        # (branch switch, `git clean`) that removes an untracked impl an archived genome names.
        dest = _impls_artifact_dir(root) / sid
        dest.mkdir(parents=True, exist_ok=True)
        for axis, spec in genome.get("chunks", {}).items():
            src = surface.impl_path(root, cfg, axis, spec["impl"])
            shutil.copy2(src, dest / f"{axis}__{src.name}")
        rec["surface"]["impls_artifact"] = f"archive/impls/{sid}"


def surface_sha(mutable):
    norm = novelty.normalize(mutable)
    return hashlib.sha256(norm.encode()).hexdigest()


def _check_id_payload(root, rec_id, mutable_sha=None, impl_shas=None):
    """An id may be re-scored (rerun, full re-score), but never reused for a DIFFERENT candidate
    — an id that silently changes meaning corrupts every comparison built on it. Both surfaces
    compare content hashes: markers via mutable_sha, registry via per-axis impl content hashes
    (so editing an untracked impl in place and re-scoring the same id is refused)."""
    for r in archive.load(root):
        if r.get("id") != rec_id:
            continue
        surf = r.get("surface") or {}
        prior_m = surf.get("mutable_sha")
        if mutable_sha is not None and prior_m and prior_m != mutable_sha:
            raise ValueError(f"id {rec_id!r} is already archived with DIFFERENT mutable code — pick a new id")
        prior_i = surf.get("impl_shas")
        if impl_shas is not None and prior_i and prior_i != impl_shas:
            raise ValueError(f"id {rec_id!r} is already archived with DIFFERENT impl code — pick a new id "
                             f"(impls a genome names are content-checked so an archived genome stays reproducible)")


def _mutable_from_patch(root, cfg, patch):
    """Mutable text after applying an explicit --patch, plus a guard check. Uses a throwaway
    GIT worktree (guard_markers diffs against HEAD, which needs git) — this never runs the
    candidate's code, so the .git link is harmless; eval still happens in the git-less export.
    Raises Rejection on a guard violation, EvalFailure if the patch won't apply."""
    with runner.git_worktree(root) as wt:
        runner.apply_patch(wt, patch)
        report = surface.guard_markers(wt, cfg)
        if not report["ok"]:
            raise Rejection("guard", "; ".join(report["violations"]))
        return surface.candidate_mutable_text(wt, cfg)


def score_seed(root, cfg, *, mode="proxy", split=DEFAULT_SPLIT, env=None, seed_id="gen0-baseline",
               no_archive=False):
    """Score the unmodified project (markers) or the baseline genome (registry) as gen 0."""
    meta = {"id": seed_id, "parent": None, "generation": 0, "inventor": "seed",
            "operator": "seed", "rationale": "unmodified baseline"}
    if cfg["surface"]["mode"] == "markers":
        return score_candidate(root, cfg, mode=mode, split=split, meta=meta, patch="", env=env,
                               no_archive=no_archive)
    return score_candidate(root, cfg, mode=mode, split=split, meta=meta,
                           genome=surface.baseline_genome(cfg), env=env, no_archive=no_archive)


def rescore(root, cfg, *, rec_id, mode, split=DEFAULT_SPLIT, env=None, no_archive=False):
    """Re-run an already-archived candidate under a (possibly) new mode/split, reusing its id
    — the primitive for proxy->full re-scores, paired reruns of a reference candidate, and
    final-split validation. Replays from the stored artifact (patch or genome), so it needs no worktree
    and self-excludes from dedup. The id-payload check guarantees the same code runs.
    no_archive replays it WITHOUT recording (e.g. an env-offset measurement)."""
    matches = [r for r in archive.load(root) if r.get("id") == rec_id]
    if not matches:
        raise ValueError(f"no archived candidate with id {rec_id!r} to re-score")
    if not no_archive and split != FINAL_SPLIT:
        # The reinstate-by-rescore path un-RETRACTS; it never un-prunes. Say so here —
        # this is the documented recovery flow for a retracted id, and a silent success
        # reads as "back in selection" when the standing prune still excludes it.
        pruned_view = archive.pruned_ids(archive.load(root), cross_partition=True)
        if rec_id in pruned_view:
            print(f"evolve: note — id {rec_id!r} is pruned ({pruned_view[rec_id]}); this "
                  "rescore records a fresh score but does NOT reinstate it into selection; "
                  "reinstate deliberately: `evolve prune --id ... --reinstate --reason ...`",
                  file=sys.stderr)
    # Prefer the most-recent record that actually carries reproducible CODE — an id can also
    # have an ingested record (score only, no artifact), e.g. after a cross-env ingest, and
    # that one can't be replayed. Fall back to the latest record (handles the empty-patch seed).
    if cfg["surface"]["mode"] == "markers":
        repro = [r for r in matches if (r.get("surface") or {}).get("patch")]
    else:
        repro = [r for r in matches if isinstance(r.get("genome"), dict)]
    if repro:
        src = repro[-1]
    else:
        # No reproducible artifact — maybe the markers seed (empty patch, never persisted).
        # Scan for a seed-shaped record instead of keying on matches[-1]: an ingested record
        # or a retraction appended later must not mask the seed and break the documented
        # reinstate-by-rescore path.
        seedish = [r for r in matches
                   if (r.get("surface") or {}).get("mode") == "markers" and r.get("parent") is None]
        src = seedish[-1] if seedish else matches[-1]
    surf = src.get("surface") or {}
    meta = {"id": rec_id, "parent": src.get("parent"), "generation": src.get("generation"),
            "inventor": src.get("inventor"), "operator": src.get("operator"),
            "axis_changed": src.get("axis_changed"),
            "rationale": (src.get("rationale") or "") + f" [rescore {mode}/{split}]"}
    if cfg["surface"]["mode"] == "markers":
        rel = surf.get("patch")
        if rel:
            patch = (archive.state_dir(root) / rel).read_text()
        elif surf.get("mode") == "markers" and src.get("parent") is None:
            patch = ""   # genuinely the seed (empty-patch baseline)
        else:
            raise ValueError(f"id {rec_id!r} has no reproducible patch to re-score — an ingested "
                             "record carries only its score, not its code; re-score it where it was "
                             "produced, or score the candidate locally under a fresh id")
        return score_candidate(root, cfg, mode=mode, split=split, meta=meta, patch=patch,
                               env=env, force=True, no_archive=no_archive)
    genome = src.get("genome")
    if not isinstance(genome, dict):
        raise ValueError(f"record {rec_id!r} carries no genome to re-score")
    _restore_impls(root, cfg, genome, surf.get("impls_artifact"))
    return score_candidate(root, cfg, mode=mode, split=split, meta=meta, genome=genome,
                           env=env, force=True, no_archive=no_archive)


def _restore_impls(root, cfg, genome, artifact_rel):
    """Before re-scoring a registry genome, restore any impl the working tree lost (branch
    switch / git clean of an untracked impl) from the copy the archive kept at score time —
    so 'rescore survives working-tree loss' actually holds. Restores only what's missing, and
    only from the archived content the id was scored with (the id-payload check re-verifies)."""
    if not artifact_rel:
        return
    art = archive.state_dir(root) / artifact_rel
    if not art.is_dir():
        return
    for axis, spec in genome.get("chunks", {}).items():
        try:
            surface.impl_path(root, cfg, axis, spec["impl"])
            continue   # still present in the working tree
        except surface.SurfaceError:
            pass
        hits = sorted(art.glob(f"{axis}__*"))
        if not hits:
            continue
        dest_dir = surface.registry_dir(root, cfg) / axis
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = hits[0].name.split("__", 1)[1]   # strip the "<axis>__" prefix
        shutil.copy2(hits[0], dest_dir / name)


def ingest(root, cfg, *, result_text, meta, mode, split, env=None, genome=None, reinstate=False):
    """Append an externally-scored result (e.g. a remote scoring box) without re-running.
    Scores are only comparable within one environment — the env tag is the audit trail.

    RETRACTION: a result of `{"retract": true, "fitness": null, "guardrail": "<reason>"}`
    invalidates the id's earlier records in the same selection scope (see archive.valid).
    The marker must be explicit — an ordinary failure result stays inert search signal.
    A retracted id REFUSES further numeric ingests unless reinstate=True: a validity verdict
    is a property of the mechanism, not the dataset, so a routine re-measurement (e.g. a
    regime-change wave) must not launder a revoked candidate clean by appending a fresh
    score after its retraction. Reinstatement is a decision, not a side effect."""
    res = runner.last_json_object(result_text)
    if res is None:
        raise ValueError("no JSON object found in the result file")
    fitness = res.get("fitness", res.get("combined_score"))
    if fitness is not None and (not isinstance(fitness, (int, float)) or isinstance(fitness, bool)):
        raise ValueError("ingested result carries a non-numeric fitness/combined_score "
                         "(a bool is not a valid score)")
    if not res.get("correct", True) and fitness is not None:
        # Mirror runner._score_of exactly, which fails on ANY falsy correct (false, 0, null, "")
        # — a remote adapter that serializes correctness as 0/1 must not ingest as a PASSING
        # score; that exact mismatch once turned an attempted revocation into a passing duplicate.
        raise ValueError("ingested result has a falsy 'correct' alongside a numeric fitness — "
                         "refusing to record a passing score for a failed run; drop the fitness "
                         "(records an inert failure) or ingest a retraction "
                         '({"retract": true, "fitness": null, "guardrail": "<reason>"})')
    if genome is not None and not isinstance((genome or {}).get("chunks"), dict):
        raise ValueError("ingested --genome must be a registry genome with a 'chunks' object")
    rec = {k: meta.get(k) for k in archive.META_KEYS}
    if not rec.get("id"):
        raise ValueError("meta.id is required")
    if rec.get("parent") == "":
        raise ValueError('--parent "" is empty — pass a real archived id or omit the flag')
    retract = bool(res.get("retract"))
    all_recs = archive.load(root)
    # Per-id bookkeeping (retraction scope, generation inheritance) reasons over the id's
    # MEASUREMENT history. Prune/reinstate records are selection-surface bookkeeping that
    # carry no generation and whose env is a view scope, not a measurement site — folding
    # them in stamps generation 0 on the next re-ingest and manufactures phantom envs that
    # make `evolve retract` refuse a single-env id.
    prior = [r for r in all_recs if r.get("id") == rec["id"] and "prune" not in r]
    env_given = env or res.get("env")
    rec_env = env_given or "external"
    if fitness is not None and not reinstate and split != FINAL_SPLIT:
        # final-split ingests can't launder anything: the holdout never feeds selection and
        # never evicts a verdict, so only selection-side ingests need the reinstate ceremony
        verdict = archive.retracted_ids(all_recs, cross_partition=True).get(rec["id"])
        if verdict is not None:
            raise ValueError(f"id {rec['id']!r} is retracted ({verdict}) — its scores are invalid "
                             "by verdict, in every partition. Reinstating is a decision, not a "
                             "side effect of a re-measurement: pass --reinstate to deliberately "
                             "append this score (append order then reinstates the id; on a "
                             "selection_env-pinned project also pass --env with the id's own "
                             "partition so the reinstating record lands where selection looks), "
                             "or use `evolve rescore` to replay it under the engine's own eval")
    # OUTSIDE the not-reinstate gate: an `ingest --reinstate` (un-retracting a pruned id)
    # is exactly the flow where an operator most plausibly assumes the id returned to
    # selection — reviewed as the one path where silence was measured misleading.
    if fitness is not None and split != FINAL_SPLIT:
        pruned_view = archive.pruned_ids(all_recs, cross_partition=True)
        if rec["id"] in pruned_view:
            print(f"evolve: note — id {rec['id']!r} is pruned ({pruned_view[rec['id']]}); this "
                  "score records but does NOT reinstate it into selection. Prune is a redundancy "
                  "status, not a validity verdict — new data says nothing about redundancy; "
                  "reinstate deliberately: `evolve prune --id ... --reinstate --reason ...`",
                  file=sys.stderr)
    if retract:
        if fitness is not None:
            raise ValueError("retract: true must come with fitness null — a retraction invalidates "
                             "earlier records; a corrected score is a separate, later ingest")
        if not prior:
            raise ValueError(f"no archived record with id {rec['id']!r} — a typo'd id would "
                             "retract nothing, silently")
        if split == FINAL_SPLIT:
            raise ValueError("a retraction on the final split registers in NO view — holdout "
                             "records neither evict nor carry verdicts; retract on the id's "
                             "selection split instead")
        # A retraction only bites inside its (split-class, env) scope — archive.valid partitions
        # BEFORE deciding what a retraction invalidates. Resolve the scope against the records it
        # is meant to clean, or a forgotten/typo'd --env yields a retraction that is accepted,
        # prints success, and invalidates nothing (the leaderboard keeps the tainted score).
        scope = [r for r in prior if r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]
        if not scope:
            raise ValueError(f"id {rec['id']!r} has only final-split records — there is nothing on "
                             "the selection side to retract")
        env_pool = {r.get("env") for r in scope}
        names = sorted((e or "<none>") for e in env_pool)
        if env_given is not None and env_given not in env_pool:
            raise ValueError(f"retraction env {env_given!r} matches none of {rec['id']!r}'s records "
                             f"(envs: {names}) — an out-of-scope retraction would invalidate "
                             "nothing, silently")
        if env_given is None and len(env_pool) > 1:
            raise ValueError(f"id {rec['id']!r} has records in several envs ({names}) — pass --env "
                             "to say which scope this retraction cleans")
        rec_env = env_given if env_given is not None else next(iter(env_pool))  # inherit, like generation
        scoped_prior = [r for r in scope if r.get("env") == rec_env]
    gen = meta.get("generation")
    if gen is None:
        if retract:
            # Bookkeeping, not search work: stamp the retracted id's own generation so it never
            # advances max(generation) (which feeds the budget/staleness math).
            gen = int(scoped_prior[-1].get("generation") or 0)
        elif prior:
            # Re-observation of a known candidate (a cross-env re-ingest, a remote failure
            # record): generation is a property of the candidate, not of the record — reuse it
            # rather than ticking max+1 into the budget/staleness math.
            gen = int(prior[-1].get("generation") or 0)
        else:
            gen = archive.next_generation(root, rec.get("parent"))
    rec["generation"] = gen
    rec.update({"mode": mode, "split": split, "env": rec_env,
                "commit": res.get("commit"),
                "fitness": fitness,
                "guardrail": res.get("guardrail", "pass" if fitness is not None else "ingested failure")})
    if retract:
        rec["retract"] = True
    elif fitness is None and any(isinstance(r.get("fitness"), (int, float)) for r in prior):
        print(f"evolve: note — this failure record does NOT retract {rec['id']!r}'s earlier scores; "
              'to invalidate them, ingest {"retract": true, "fitness": null, "guardrail": "<reason>"}',
              file=sys.stderr)
    for k in ("seeds", "per_seed", "public", "text_feedback"):
        if res.get(k) is not None:
            rec[k] = res[k]
    if genome is not None:
        rec["surface"] = {"mode": "registry", "genome_ingested": True}
        rec["genome"] = genome
    archive.append(root, rec)
    if res.get("private"):
        archive.write_private(root, rec["id"], res["private"])
    return rec
