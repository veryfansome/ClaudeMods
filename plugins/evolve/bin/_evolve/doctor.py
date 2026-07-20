"""doctor: read-only health pass over the whole contract, plus the one measurement that
must not be folklore — the noise floor.

Doctor never self-heals (the fix for drift is fixing the contract); it emits JSON and
exits 2 on any failure. --measure-noise is the exception to read-only: it runs the
proxy eval k times on the unmodified baseline and writes the measured floor into the
config (with backup), because a promotion gate calibrated by assertion instead of
measurement is how noise gets promoted as fitness.
"""

import hashlib
import json
import pathlib
import subprocess

from . import archive, config as cfgmod, runner, surface
from .score import default_env


def _noise_signature(cfg):
    """What the measured noise floor is valid for: the proxy command + the environment."""
    cmd = (cfg["eval"].get("proxy") or {}).get("cmd") or ""
    return {"proxy_cmd_sha": hashlib.sha256(cmd.encode()).hexdigest()[:12], "env": default_env()}


def _check(checks, name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


def run(root, run_eval=False, measure_noise=False, dry=False):
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
    tracked_mods = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=root,
                                  capture_output=True, text=True).stdout.split()
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
            if not _check(checks, f"markers:{path}", p.exists(), "declared file missing"):
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
                        "before trusting any promotion decision")
    meta = cfg["fitness"].get("noise_meta")
    if floor is not None and meta and not measure_noise:
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
