"""doctor: read-only health pass over the whole contract, plus the one measurement that
must not be folklore — the noise floor.

Doctor never self-heals (the fix for drift is fixing the contract); it emits JSON and
exits 2 on any failure. --measure-noise is the exception to read-only: it runs the
proxy eval k times on the unmodified baseline and writes the measured floor into the
config (with backup), because a gate calibrated by assertion instead of
measurement is how noise gets mistaken for fitness.
"""

import datetime
import hashlib
import json
import pathlib
import subprocess

from . import archive, config as cfgmod, prune, runner, score, surface
from .score import default_env


def _noise_signature(cfg):
    """What the measured noise floor is valid for: the proxy command + the environment."""
    cmd = (cfg["eval"].get("proxy") or {}).get("cmd") or ""
    return {"proxy_cmd_sha": hashlib.sha256(cmd.encode()).hexdigest()[:12], "env": default_env()}


def _check(checks, name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


def run(root, run_eval=False, measure_noise=False, dry=False, force=False):
    checks, warnings = [], []
    root = pathlib.Path(root)

    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                       capture_output=True, text=True)
    if not _check(checks, "git_repo", r.returncode == 0,
                  "candidates are scored from HEAD in pristine clones; a git repo is required"):
        return _result(checks, warnings)

    # Only TRACKED modifications matter: pristine clones export HEAD, so a modified tracked
    # file (surface code, committed eval adapter) is invisible to scoring. Untracked files
    # (the archive, registry impls added this campaign) are expected state, not drift.
    # Exclude the engine's own state dir: the archive is tracked-and-appended by design, so
    # it is legitimately dirty after every score — warning about it would be permanent noise.
    # Surface source and the (project-side) eval adapter live outside evolve/, and those ARE
    # what "invisible to scoring" is about.
    tracked_mods = [p for p in subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=root,
                                              capture_output=True, text=True).stdout.split()
                    if not (p == cfgmod.STATE_DIR or p.startswith(cfgmod.STATE_DIR + "/"))]
    if tracked_mods:
        warnings.append(f"tracked files modified vs HEAD {tracked_mods[:8]} — clones export HEAD, "
                        "so these changes are invisible to scoring; commit them before evolving")
    if (root / ".gitmodules").exists():
        warnings.append("this repo has submodules — `git archive HEAD` (used to build the scoring "
                        "clone) omits submodule content, so a candidate depending on submodule code "
                        "will fail eval; vendor the needed code or supply it via eval.setup")

    try:
        cfg = cfgmod.load(root)
        _check(checks, "config", True, str(cfgmod.config_path(root)))
    except cfgmod.ConfigError as e:
        _check(checks, "config", False, str(e))
        return _result(checks, warnings)

    if cfg["surface"]["mode"] == "markers":
        for path in cfg["surface"]["files"]:
            p = root / path
            if not _check(checks, f"markers:{path}", p.exists(),
                          "" if p.exists() else "declared file missing"):
                continue
            try:
                blocks = surface.parse_blocks(p.read_text(), path)
                _check(checks, f"markers:{path}", True, f"{len(blocks)} EVOLVE block(s)")
            except surface.SurfaceError as e:
                _check(checks, f"markers:{path}", False, str(e))
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=root,
                                     capture_output=True, text=True)
            if tracked.returncode != 0:
                _check(checks, f"tracked:{path}", False,
                       "surface file is not tracked by git — clones score HEAD, commit it first")
    else:
        reg = cfg["surface"]["registry"]
        for axis, spec in reg["axes"].items():
            impls = surface.list_impls(root, cfg, axis)
            _check(checks, f"registry:{axis}", spec["baseline"] in impls,
                   f"baseline {spec['baseline']!r} " +
                   ("present" if spec["baseline"] in impls else f"missing (have {impls})"))
        try:
            surface.validate_genome(surface.baseline_genome(cfg), root, cfg)
            _check(checks, "registry:baseline_genome", True)
        except surface.SurfaceError as e:
            _check(checks, "registry:baseline_genome", False, str(e))
        for tier in ("proxy", "full"):
            if "{genome}" not in cfg["eval"][tier]["cmd"]:
                warnings.append(f"eval.{tier}.cmd has no {{genome}} placeholder — a registry eval "
                                "usually needs the genome path")

    # Read the raw file for TRUE physical line numbers (archive.load() silently drops
    # unparseable lines, so enumerating its output would misreport which line is broken).
    bad, n_records = 0, 0
    apath = archive.archive_path(root)
    raw = apath.read_text().splitlines() if apath.exists() else []
    for i, line in enumerate(raw, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            bad += 1
            warnings.append(f"archive line {i}: malformed JSON ({e})")
            continue
        n_records += 1
        try:
            archive._check_record(rec)
            rel = (rec.get("surface") or {}).get("patch")
            if rel and not (cfgmod.state_dir(root) / rel).exists():
                raise ValueError(f"referenced patch missing: {rel}")
        except ValueError as e:
            bad += 1
            warnings.append(f"archive line {i}: {e}")
    _check(checks, "archive", bad == 0,
           f"{n_records} records" + (f", {bad} malformed" if bad else ""))

    floor = cfg["fitness"].get("noise_floor")
    if floor is None and not measure_noise:
        warnings.append("fitness.noise_floor is unmeasured — run `evolve doctor --measure-noise` "
                        "before trusting any comparison built on it")
    meta = cfg["fitness"].get("noise_meta")
    # Staleness applies only to a floor THIS tool measured (engine-written meta carries
    # proxy_cmd_sha). A hand-derived floor with hand-written provenance is the project's own
    # quantity — nagging "re-run --measure-noise" there recommends destroying it.
    if floor is not None and meta and meta.get("proxy_cmd_sha") and not measure_noise:
        now = _noise_signature(cfg)
        if meta.get("proxy_cmd_sha") != now["proxy_cmd_sha"] or meta.get("env") != now["env"]:
            warnings.append("noise_floor is stale — the proxy eval command or environment changed "
                            "since it was measured; re-run `evolve doctor --measure-noise`")

    all_recs = archive.load(root)
    envs = archive.selection_envs(all_recs)
    sel_env = cfg["fitness"].get("selection_env")
    if sel_env is None and len(envs) > 1:
        warnings.append(f"scored records span environments {envs} but fitness.selection_env is unset "
                        "— set it to one env so cross-env scores don't corrupt selection")
    if sel_env is not None and not archive.valid(all_recs, sel_env) and archive.valid(all_recs):
        warnings.append(f"fitness.selection_env={sel_env!r} matches 0 scored records (envs present: "
                        f"{envs}) — selection is empty; re-baseline a candidate in this env or fix the tag")

    retired = surface.retired_impls(root)
    if retired:
        live = [r["id"] for r in archive.selection_pool(all_recs, sel_env)
                if surface.genome_selects_retired(r.get("genome") or {}, retired)]
        if live:
            warnings.append(f"{len(live)} live selectable candidate(s) select retired impls "
                            f"({live[:5]}) — a retired mechanism stays reachable as a PARENT "
                            "until its carriers are retracted (`evolve retract`), pruned "
                            "(`evolve prune`) or the impl is un-retired")

    # Pre-record-scoped-retraction archives: an id whose latest non-final record is a plain
    # failure AFTER a scored one used to mean "retracted" (latest-verdict-wins). That gesture is
    # inert now, so the old score silently re-entered selection — surface the ambiguity rather
    # than let a revoked top-scorer keep driving sampling unnoticed.
    latest, ever_scored = {}, set()
    for r in all_recs:
        rid = r.get("id")
        if rid is None or r.get("split", cfgmod.DEFAULT_SPLIT) == cfgmod.FINAL_SPLIT:
            continue
        if isinstance(r.get("fitness"), (int, float)):
            ever_scored.add(rid)
        latest[rid] = r
    legacy = sorted(rid for rid, r in latest.items()
                    if rid in ever_scored and r.get("fitness") is None
                    and not r.get("retract") and r.get("guardrail") not in (None, "pass"))
    if legacy:
        warnings.append(f"ids whose latest record is a failure after earlier scores: {legacy} — "
                        "their scores still count (a plain failure does not retract). If any was a "
                        'pre-upgrade revocation, re-ingest {"retract": true, "fitness": null, '
                        '"guardrail": "<reason>"}; if it was a routine flake, rescore or ignore')

    # Standing prunes: re-derive every coverage certificate against the CURRENT pool. The
    # certificate is a pure function of the archive, and its inputs are mutable — a
    # retraction wave or a re-score can thin a pruned candidate's carriers after the fact
    # (replayed from the field: one wave would have silently voided 9 prunes, one trait to
    # zero carriers). Doctor never nags you TO prune and never reinstates; it checks that
    # what WAS pruned still keeps its promise.
    pruned_view = archive.pruned_ids(all_recs, sel_env)
    if pruned_view:
        aud = prune.audit(all_recs, cfg)
        # THINNED coverage is the owner's judgment (warn); a LOST trait/pair — zero
        # surviving carriers at any fitness — breaks the "never traits" invariant itself
        # and FAILS the check. Killing a mechanism on purpose is retirement's job.
        lost = [v for v in aud["voided"] if v.get("trait_lost")]
        _check(checks, "prune_coverage", not lost,
               f"{aud['pruned']} pruned; {len(aud['voided'])} certificate(s) no longer hold "
               f"({len(lost)} with a trait/pair LOST from the pool); "
               f"{len(aud['unauditable'])} unauditable (explicit prunes without genomes)")
        for v in aud["voided"]:
            warnings.append(f"prune of {v['id']} no longer holds — {v['why']}; a later "
                            "retraction/re-score/prune thinned its carriers. Reinstate it "
                            "(`evolve prune --id ... --reinstate --reason ...`) or accept "
                            "the thinner coverage deliberately"
                            + ("" if not v.get("trait_lost") else
                               " — this one LOST a trait/pair from the pool entirely; if "
                               "the mechanism itself should die, that is retirement "
                               "(evolve/retired_impls.json), not prune"))
    # Standing prunes scoped to ANOTHER partition whose ids are nonetheless live here: they
    # do not bite this pool (prune records are per-partition streams), which is surprising
    # exactly when the id is selectable. Old-partition prunes for ids with no valid record
    # here are the normal post-cutover state and stay silent.
    if sel_env is not None:
        cross = archive.pruned_ids(all_recs, sel_env, cross_partition=True)
        live_here = {r["id"] for r in archive.selection_pool(all_recs, sel_env)}
        strays = sorted((set(cross) - set(pruned_view)) & live_here)
        if strays:
            warnings.append(f"{len(strays)} candidate(s) carry a standing prune in ANOTHER "
                            f"partition but are live in {sel_env!r} ({strays[:5]}) — a prune "
                            "bites only the partition whose pool justified it; re-plan and "
                            "prune here too if they are redundant in this pool")

    # Realized parent-selection pressure. λ is in units of 1/fitness, so a mis-scaled explicit
    # value silently degrades sampling to uniform while everything else looks healthy — a
    # field campaign ran 8 rounds of statistically-uniform parent picks before measuring it.
    # This prints the numbers sampling actually uses (same helper + same pruned-excluding
    # pool), fitness pressure only, so the offspring penalty can't mask an inert λ.
    per_id = archive.selection_pool(all_recs, sel_env)
    if len(per_id) >= 2:
        lam_cfg = cfg["search"].get("lambda", "auto")
        lam, fit_w = archive.selection_weights(per_id, lam_cfg, {}, noise_floor=floor)
        fits_sp = sorted(r["fitness"] for r in per_id)
        med = fits_sp[len(fits_sp) // 2]
        scale = archive.fitness_scale(fits_sp)
        ratio = (max(fit_w) / min(fit_w)) if min(fit_w) > 0 else float("inf")
        total = sum(fit_w)
        below = sum(w for r, w in zip(per_id, fit_w) if r["fitness"] < med) / total if total else 0.0
        auto = lam_cfg in (None, "auto")
        _check(checks, "selection_pressure", True,
               f"λ={lam:.4g} ({'auto' if auto else 'explicit'}) over {len(per_id)} candidates, "
               f"fitness scale={scale:.4g}: P(best)/P(worst)={ratio:.3g}, "
               f"P(below median)={below:.2f} (fitness pressure, before the novelty penalty)")
        # Two failure directions, both keyed on the measured noise floor. Near-uniform is only
        # a problem when there is REAL signal to discriminate (spread above the floor) — over
        # statistically-identical candidates, near-uniform is correct, and nagging there
        # pushes the operator to churn a protected config field in response to noise.
        # Deliberate λ=0 (pure novelty-penalty sampling) is exempt.
        near_uniform = ratio < 5 and scale > (floor or 0.0) and lam_cfg != 0
        if near_uniform:
            warnings.append(f"parent sampling is near-uniform (P(best)/P(worst)={ratio:.2g}) "
                            "despite fitness spread above the noise floor — "
                            + (f"the distribution may be outlier-dominated (scale {scale:.3g})"
                               if auto else
                               f"search.lambda={lam_cfg} is likely mis-scaled (λ is in units of "
                               '1/fitness); set it to "auto" or rescale it'))
        # A pin that drifted far from what auto would derive. Deliberate λ=0 is exempt here
        # too, and when near-uniform already fired this advice would be a duplicate.
        if not auto and lam_cfg != 0 and not near_uniform:
            auto_lam = archive.resolve_lambda("auto", fits_sp, floor)
            if auto_lam > 0 and (lam > 20 * auto_lam or lam < auto_lam / 20):
                warnings.append(f"explicit search.lambda={lam_cfg} diverges from the auto-derived "
                                f"value ({auto_lam:.4g}) by more than 20x — a pin silently opts "
                                "out of scale tracking as the archive moves; re-derive it or set "
                                '"auto"')
        # The opposite direction: strong pressure applied to an ordering that is pure eval
        # noise. Auto can't do this (its scale is floored at the noise floor), so only an
        # explicit λ earns the warning.
        if not auto and floor and scale <= floor and ratio >= 5:
            warnings.append(f"population fitness spread ({scale:.3g}) is within the measured "
                            f"noise floor ({floor}) but parent sampling pressure is "
                            f"P(best)/P(worst)={ratio:.2g} — the ordering being amplified is "
                            'noise; set search.lambda to "auto" (its pressure floors at the '
                            "noise floor)")

    if measure_noise:
        # Refuse BEFORE paying for eval runs. An engine-written floor (noise_meta carries
        # proxy_cmd_sha — this tool's own provenance) may be refreshed freely, keeping the
        # staleness warning's advice a one-command fix; a hand-derived floor (no sha) may be a
        # cross-seed quantity that this fixed-seed determinism measurement is NOT — clobbering
        # it would also disable auto-λ's noise-floor guard, so that takes --force.
        engine_written = bool((cfg["fitness"].get("noise_meta") or {}).get("proxy_cmd_sha"))
        if cfg["fitness"].get("noise_floor") is not None and not engine_written and not force:
            _check(checks, "noise_floor", True,
                   f"NOT measured: fitness.noise_floor={cfg['fitness']['noise_floor']} is "
                   "configured with hand-written provenance — possibly a cross-seed quantity "
                   "this fixed-seed determinism measurement is not; pass --force to overwrite "
                   "(no eval was spent)")
            measure_noise = False
    if run_eval or measure_noise:
        runs = cfg["fitness"].get("noise_runs", 5) if measure_noise else 1
        try:
            scores = _baseline_runs(root, cfg, runs)
            spread = round(max(scores) - min(scores), 6)
            _check(checks, "eval_contract", True,
                   f"baseline proxy scores over {runs} run(s): {scores} (spread {spread})")
            if measure_noise:
                cfg["fitness"]["noise_floor"] = spread
                cfg["fitness"]["noise_meta"] = _noise_signature(cfg)  # provenance for staleness detection
                if not dry:
                    cfgmod.save(cfg, root)
                _check(checks, "noise_floor", True,
                       f"measured {spread} over {runs} runs" + (" (dry — not written)" if dry else " — written to config"))
        except (runner.EvalFailure, surface.SurfaceError) as e:
            _check(checks, "eval_contract", False, str(e))

    return _result(checks, warnings)


def _baseline_runs(root, cfg, runs):
    """k proxy runs of the unmodified baseline in ONE clone at a fixed seed: measures
    eval nondeterminism itself, with code and environment held constant."""
    genome = surface.baseline_genome(cfg) if cfg["surface"]["mode"] == "registry" else None
    scores = []
    with runner.pristine_clone(root) as clone:
        genome_path = runner.write_genome(clone, genome) if genome else None
        runner.run_setup(clone, cfg)
        runner.run_gates(clone, cfg, genome_path=genome_path, split=cfgmod.DEFAULT_SPLIT, mode="proxy")
        tier = dict(cfg["eval"]["proxy"])
        one_seed = {**cfg, "eval": {**cfg["eval"], "proxy": {**tier, "seeds": [tier.get("seeds", [0])[0]]}}}
        for _ in range(runs):
            res = runner.evaluate_tier(clone, one_seed, "proxy", cfgmod.DEFAULT_SPLIT,
                                       genome_path=genome_path)
            scores.append(res["fitness"])
    return scores


def _result(checks, warnings):
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "warnings": warnings}


def measure_env_offset(root, ref_id=None, dry=False):
    """Measure this environment's fitness offset vs. the environment a reference candidate was
    scored in — the 'measure-then-decide' upgrade to the plugin's assume-incomparable-and-warn
    stance. Re-runs the reference (default: the top-scoring record) here, diffs against its recorded
    fitness, and writes fitness.env_offsets[<this env>] with the same provenance shape as the
    noise floor. Informs whether environments may be folded (offset within noise) or must stay
    partitioned via selection_env; never gates on its own."""
    cfg = cfgmod.load(root)
    sel_env = cfg["fitness"].get("selection_env")
    if ref_id:
        scored = [r for r in archive.load(root)
                  if r.get("id") == ref_id and isinstance(r.get("fitness"), (int, float))]
        # the id's authoritative recorded score (full outranks proxy, then higher fitness)
        ref = max(scored, key=lambda r: (r.get("mode") == "full", r["fitness"])) if scored else None
    else:
        ref = archive.best(root, sel_env)
    if ref is None:
        return {"ok": False, "error": f"no reference candidate to measure against "
                f"({'id ' + repr(ref_id) if ref_id else 'no scored record — score something first'})"}
    if ref.get("fitness") is None:
        return {"ok": False, "error": f"reference {ref['id']!r} has no numeric fitness to compare against"}

    here = default_env()
    ref_env, ref_fitness, ref_mode = ref.get("env"), ref["fitness"], ref.get("mode", "proxy")
    ref_split = ref.get("split", cfgmod.DEFAULT_SPLIT)
    warnings = []
    if ref_env == here:
        warnings.append(f"reference was already scored in this env ({here}) — the offset just "
                        "re-measures run-to-run noise, not a cross-env gap")

    try:
        rerun = score.rescore(root, cfg, rec_id=ref["id"], mode=ref_mode, split=ref_split,
                              env=here, no_archive=True)
    except (ValueError, score.Rejection, runner.EvalFailure, runner.InfraError,
            surface.SurfaceError) as e:
        return {"ok": False, "error": f"could not re-run reference {ref['id']!r} here: {e}"}
    if rerun.get("fitness") is None:
        return {"ok": False, "error": f"reference {ref['id']!r} failed to score in this env: "
                f"{rerun.get('guardrail')}"}

    offset = round(rerun["fitness"] - ref_fitness, 6)
    measured_floor = cfg["fitness"].get("noise_floor")
    floor = measured_floor if measured_floor is not None else 0.0
    if measured_floor is None:
        warnings.append("fitness.noise_floor is unmeasured — the comparability verdict below uses "
                        "0.0 (strictest) and is UNCALIBRATED; run `evolve doctor --measure-noise` first")
    comparable = abs(offset) <= floor
    verdict = ("environments are comparable within the noise floor — you may leave "
               "fitness.selection_env unset and fold their scores"
               if comparable else
               "offset EXCEEDS the noise floor — scores are not comparable; set "
               "fitness.selection_env to one env so cross-env records don't corrupt selection")
    entry = {"offset": offset, "ref_id": ref["id"], "ref_env": ref_env, "mode": ref_mode,
             "split": ref_split, "noise_floor": measured_floor,
             "measured_at": datetime.date.today().isoformat(),
             **_noise_signature(cfg)}
    cfg["fitness"].setdefault("env_offsets", {})[here] = entry
    if not dry:
        cfgmod.save(cfg, root)
    return {"ok": True, "current_env": here, "ref_env": ref_env, "ref_id": ref["id"],
            "ref_fitness": ref_fitness, "fitness_here": rerun["fitness"], "offset": offset,
            "noise_floor": floor, "comparable": comparable, "verdict": verdict,
            "stored": (None if dry else f"fitness.env_offsets[{here!r}]"), "warnings": warnings}
