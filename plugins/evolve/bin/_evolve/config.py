"""Load + validate evolve/evolve.json — the whole project-facing contract in one file.

The config is the boundary: the engine never needs per-project edits, everything
project-specific flows through here. Validation is strict and runs before any verb
that could spend an eval, so a malformed contract fails in milliseconds, not after
a training run.
"""

import copy
import json
import pathlib

from . import CONFIG_SCHEMA

STATE_DIR = "evolve"          # project-relative; holds config, archive, manual, chunks
CONFIG_NAME = "evolve.json"
FINAL_SPLIT = "final"         # reserved: records on this split never drive selection
DEFAULT_SPLIT = "inner"

OPERATORS = ("diff", "rewrite", "cross")

DEFAULTS = {
    "schema": CONFIG_SCHEMA,
    "task": "",
    "surface": {"mode": "markers", "files": [], "registry": None},
    "protected": [],
    "eval": {
        "setup": None,          # run once per pristine clone (deps install etc.)
        "smoke": None,          # fast free gate (compile/unit); nonzero exit -> guardrail fail
        "guardrails": [],       # ordered cmds; any nonzero exit -> fitness null with reason
        "proxy": {"cmd": None, "seeds": [0], "timeout_s": 600},
        "full": {"cmd": None, "seeds": [0], "timeout_s": 3600},
        "splits": [DEFAULT_SPLIT],   # 'final' may be appended to enable the holdout rail
    },
    "fitness": {"noise_floor": None, "noise_runs": 5, "noise_meta": None,
                "selection_env": None},
    "search": {
        "lambda": 10.0,
        "inspirations": {"archive": 2, "top_k": 2},
        "op_probs": {"diff": 0.5, "rewrite": 0.4, "cross": 0.1},
        "dedup_similarity": 0.95,
        "insights_interval": 8,
        "standing_rules": [],
    },
    "budget": {"max_generations": 40, "max_full_evals": 15, "stop_after_stale_rounds": 6},
}


class ConfigError(ValueError):
    pass


def _merge(defaults, override):
    """Deep-merge override onto defaults; dict-typed defaults recurse, everything else replaces."""
    out = copy.deepcopy(defaults)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def state_dir(root):
    return pathlib.Path(root) / STATE_DIR


def config_path(root):
    return state_dir(root) / CONFIG_NAME


def load(root):
    """Read, default-fill, and validate the project config. Raises ConfigError."""
    p = config_path(root)
    if not p.exists():
        raise ConfigError(f"no config at {p} — run `evolve init` (or /evolve:init) first")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p} is not valid JSON: {e}")
    cfg = _merge(DEFAULTS, raw)
    # op_probs is a distribution, not a set of overrides: a partial map must replace the
    # default wholesale (silently topping it up from defaults would re-enable operators
    # the project chose to exclude) — and then fail validation loudly if it doesn't sum to 1.
    if isinstance(raw.get("search"), dict) and "op_probs" in raw["search"]:
        cfg["search"]["op_probs"] = copy.deepcopy(raw["search"]["op_probs"])
    validate(cfg, root)
    return cfg


def validate(cfg, root=None):
    if cfg.get("schema") != CONFIG_SCHEMA:
        raise ConfigError(f"config schema {cfg.get('schema')!r} != supported {CONFIG_SCHEMA}")
    if not isinstance(cfg.get("task"), str) or not cfg["task"].strip():
        raise ConfigError("config.task must be a non-empty one-line objective statement")

    surf = cfg["surface"]
    mode = surf.get("mode")
    if mode not in ("markers", "registry"):
        raise ConfigError(f"surface.mode must be 'markers' or 'registry', got {mode!r}")
    if mode == "markers":
        files = surf.get("files")
        if not files or not isinstance(files, list):
            raise ConfigError("markers mode requires surface.files (non-empty list of repo-relative paths)")
    else:
        reg = surf.get("registry")
        if not isinstance(reg, dict):
            raise ConfigError("registry mode requires surface.registry")
        for key in ("dir", "axes"):
            if not reg.get(key):
                raise ConfigError(f"surface.registry.{key} is required")
        for axis, spec in reg["axes"].items():
            if not isinstance(spec, dict) or not spec.get("baseline"):
                raise ConfigError(f"registry axis {axis!r} needs a 'baseline' impl name")
        req = reg.get("required", [])
        unknown = [a for a in req if a not in reg["axes"]]
        if unknown:
            raise ConfigError(f"registry.required names unknown axes: {unknown}")

    ev = cfg["eval"]
    for tier in ("proxy", "full"):
        t = ev.get(tier) or {}
        if not t.get("cmd"):
            raise ConfigError(f"eval.{tier}.cmd is required")
        seeds = t.get("seeds", [0])
        if not isinstance(seeds, list) or not seeds or not all(isinstance(s, int) for s in seeds):
            raise ConfigError(f"eval.{tier}.seeds must be a non-empty list of ints")
    splits = ev.get("splits", [DEFAULT_SPLIT])
    if DEFAULT_SPLIT not in splits:
        raise ConfigError(f"eval.splits must include {DEFAULT_SPLIT!r} (fitness is scored there)")
    for s in splits:
        if not isinstance(s, str) or not s:
            raise ConfigError("eval.splits entries must be non-empty strings")

    probs = cfg["search"]["op_probs"]
    unknown = [o for o in probs if o not in OPERATORS]
    if unknown:
        raise ConfigError(f"search.op_probs has unknown operators {unknown}; valid: {OPERATORS}")
    total = sum(probs.values())
    if not probs or abs(total - 1.0) > 1e-6:
        raise ConfigError(f"search.op_probs must sum to 1.0 (got {total})")

    floor = cfg["fitness"].get("noise_floor")
    if floor is not None and (not isinstance(floor, (int, float)) or floor < 0):
        raise ConfigError("fitness.noise_floor must be null or a non-negative number")

    # The config and the archive must be untouchable by a candidate mutation — corrupting
    # either forges selection. Require coverage of exactly those two (NOT the whole state
    # dir: the registry dir and insights.md/EVOLVE.md are written during normal operation).
    protected = cfg.get("protected", [])
    must_cover = [f"{STATE_DIR}/{CONFIG_NAME}", f"{STATE_DIR}/archive/genomes.jsonl"]
    for target in must_cover:
        if not _covered(protected, target):
            raise ConfigError(f"protected must cover {target!r} (the contract + archive are "
                              f"sacred); add \"{STATE_DIR}/evolve.json\" and \"{STATE_DIR}/archive/**\"")

    se = cfg["fitness"].get("selection_env")
    if se is not None and not isinstance(se, str):
        raise ConfigError("fitness.selection_env must be null or a string env tag")
    return cfg


def _covered(globs, target):
    """True if some protected glob covers target. Uses EXACTLY the predicate the guard
    (surface._matches_any) and the protect-paths hook use, so 'config says protected' is by
    construction identical to 'guard/hook actually block it' — no over/under-accept drift."""
    import fnmatch
    return any(fnmatch.fnmatch(target, g) or fnmatch.fnmatch(target, g.rstrip("/*") + "/*")
               for g in globs)


def save(cfg, root, backup=True):
    """Write config atomically; back up the previous version first (memory-init style:
    a botched write degrades visibly rather than corrupting the contract)."""
    validate(cfg, root)
    p = config_path(root)
    if backup and p.exists():
        n = 1
        while (bak := p.with_suffix(f".json.bak.{n}")).exists():
            n += 1
        bak.write_text(p.read_text())
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(p)
    return p
