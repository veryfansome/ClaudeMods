"""The program archive: append-only genomes.jsonl of every scored candidate, plus the
leaderboard and ShinkaEvolve-style fitness+novelty weighted parent sampling.

Two rules are enforced HERE, structurally, because prose rules erode under automation:
  - final-split firewall: records scored on the reserved 'final' split never enter
    selection or the leaderboard — the holdout can't leak into the optimization.
  - budget: sampling refuses once the configured budget is spent, so an unattended
    /loop halts on engine exit codes, not on a skill remembering to check.
Failed candidates are recorded with fitness null + a reason (they are search signal),
and are excluded from selection by the same validity filter.
"""

import contextlib
import hashlib
import json
import math
import os
import pathlib
import random
import sys

with contextlib.suppress(ImportError):   # POSIX only; append degrades to non-locked on others
    import fcntl

from . import RECORD_SCHEMA
from .config import FINAL_SPLIT, DEFAULT_SPLIT, state_dir

META_KEYS = ("id", "parent", "generation", "inventor", "operator", "axis_changed", "rationale")


def _sigmoid(z):
    """Overflow-free logistic. math.exp(-z) overflows once z < ~-709 (which happens with an
    unnormalized fitness scale — raw latency, token counts — times λ=10), crashing sample."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def archive_dir(root):
    return state_dir(root) / "archive"


def archive_path(root):
    return archive_dir(root) / "genomes.jsonl"


def private_dir(root):
    # Private metrics live OUTSIDE genomes.jsonl: briefs quote the archive to inventors,
    # so the anti-overfit metrics must not be one `cat` away inside it.
    return archive_dir(root) / "private"


def load(root):
    """Parse genomes.jsonl, skipping (and warning about) any unparseable line rather than
    letting one torn line — a crash/disk-full during append — brick every verb."""
    p = archive_path(root)
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"evolve: skipping malformed archive line {i} in {p}", file=sys.stderr)
    return out


def append(root, record):
    """Append-only, re-scoring allowed: the same id may be recorded again (proxy rerun,
    full promotion, another env) — best_per_id collapses at read time. What is NOT allowed
    is reusing an id for a DIFFERENT candidate; score_candidate enforces that payload check."""
    record = dict(record)
    record.setdefault("schema", RECORD_SCHEMA)
    _check_record(record)
    p = archive_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record) + "\n"
    with open(p, "a") as f:
        if "fcntl" in globals():
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # serialize concurrent score processes
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())                    # the real write must land WHILE the lock is held
        finally:
            if "fcntl" in globals():
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return record


def write_private(root, rec_id, private):
    if not private:
        return None
    d = private_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_safe_id(rec_id)}.json"
    p.write_text(json.dumps(private, indent=1) + "\n")
    return p


def _safe_id(rec_id):
    """Filesystem-safe, INJECTIVE artifact key: sanitized id plus a hash suffix of the raw
    id, so distinct ids ('run:1' vs 'run 1') never collide on one artifact filename."""
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in rec_id)[:60]
    suffix = hashlib.sha256(rec_id.encode()).hexdigest()[:8]
    return f"{keep}-{suffix}" if keep else suffix


def _check_record(r):
    for k in ("id", "mode", "split"):
        if not r.get(k):
            raise ValueError(f"record missing required field {k!r}")
    if r["mode"] not in ("proxy", "full"):
        raise ValueError(f"record mode must be proxy|full, got {r['mode']!r}")
    fit = r.get("fitness")
    if fit is not None and (not isinstance(fit, (int, float)) or isinstance(fit, bool)):
        raise ValueError("record fitness must be a number or null (null = guardrail failure); "
                         "a bool is not a valid score")
    if fit is not None and (math.isnan(fit) or math.isinf(fit)):
        raise ValueError("record fitness must be finite (use null + guardrail reason for failures)")
    if "private" in r:
        raise ValueError("private metrics must not be embedded in archive records "
                         "(they are written to archive/private/ instead)")


def valid(records, selection_env=None):
    """Records that count as FITNESS: numerically scored, not on the firewalled split, and
    — when selection_env is set — scored in that environment only. Scores are comparable
    only within one environment, so a configured selection_env firewalls foreign-env
    (e.g. ingested) records out of sampling/leaderboard the same way final-split records are."""
    out = [r for r in records
           if isinstance(r.get("fitness"), (int, float)) and r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]
    if selection_env is not None:
        out = [r for r in out if r.get("env") == selection_env]
    return out


def selection_envs(records):
    """Distinct environments among selectable (non-final, scored) records — for a drift warning."""
    return sorted({r.get("env") for r in valid(records) if r.get("env")})


def best_per_id(records):
    """One record per candidate id, preferring full-mode then higher fitness."""
    by_id = {}
    for r in records:
        key = (r.get("mode") == "full", r["fitness"])
        if r["id"] not in by_id or key > by_id[r["id"]][0]:
            by_id[r["id"]] = (key, r)
    return [v[1] for v in by_id.values()]


def leaderboard(root, top=10, selection_env=None):
    items = best_per_id(valid(load(root), selection_env))
    items.sort(key=lambda r: (r.get("mode") == "full", r["fitness"]), reverse=True)
    return items[:top]


def best(root, selection_env=None):
    lb = leaderboard(root, 1, selection_env)
    return lb[0] if lb else None


def offspring_counts(records):
    """Distinct child ids per parent. Counting records (not distinct children) would let a
    proxy->full promotion or a paired rerun — re-scores of the SAME child — inflate the
    parent's novelty penalty on every re-score."""
    children = {}
    for r in records:
        p = r.get("parent")
        if p:
            children.setdefault(p, set()).add(r["id"])
    return {p: len(kids) for p, kids in children.items()}


def sample_parent(root, seed=0, lam=10.0, selection_env=None):
    """p_i ∝ σ(λ·(F_i − median F)) · 1/(1 + offspring_i) — performance × novelty
    (ShinkaEvolve §3.1 weighted sampling, the paper's largest ablated gain). Seeded and
    deterministic given the archive state. Returns a record or None on an empty archive."""
    all_recs = load(root)
    items = best_per_id(valid(all_recs, selection_env))
    if not items:
        return None
    fits = sorted(r["fitness"] for r in items)
    med = fits[len(fits) // 2]
    # Penalize only offspring in the pool being sampled (same env, non-final), so the diversity
    # bonus matches the population the board/brief show.
    offspring = offspring_counts(valid(all_recs, selection_env))
    weights = [_sigmoid(lam * (r["fitness"] - med)) / (1.0 + offspring.get(r["id"], 0))
               for r in items]
    return random.Random(f"parent:{seed}").choices(items, weights=weights, k=1)[0]


def sample_inspirations(root, parent_id, n_top=2, n_archive=2, seed=0, selection_env=None):
    """Top-K performers + random archive members, excluding the parent — the context that
    makes the archive reach the prompt (AlphaEvolve's 'no context' ablation: an archive
    that never reaches the prompt is dead weight)."""
    items = [r for r in best_per_id(valid(load(root), selection_env)) if r["id"] != parent_id]
    items.sort(key=lambda r: (r.get("mode") == "full", r["fitness"]), reverse=True)
    top = items[:n_top]
    rest = [r for r in items[n_top:]]
    rng = random.Random(f"insp:{seed}")
    extra = rng.sample(rest, min(n_archive, len(rest))) if rest else []
    return top + extra


def next_generation(root, parent_id=None):
    recs = load(root)
    if parent_id:
        for r in recs:
            if r["id"] == parent_id:
                return int(r.get("generation") or 0) + 1
    return max((int(r.get("generation") or 0) for r in recs), default=-1) + 1


def budget_state(root, budget, selection_env=None):
    """Spend vs. the configured budget, from the archive alone (no process state). Final-split
    records are excluded from generation/staleness math: validation runs and re-scores must
    not tick the search toward a spurious stop."""
    recs = load(root)
    # Count generations/full_evals over the SAME env-partitioned, non-final population the
    # champion is drawn from, so a foreign-env record (e.g. an ingest) can't burn this env's
    # budget or trip its staleness gate.
    selectable = [r for r in recs if r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT
                  and (selection_env is None or r.get("env") == selection_env)]
    scored = valid(recs, selection_env)
    generations = max((int(r.get("generation") or 0) for r in selectable), default=0)
    full_evals = len({r["id"] for r in selectable if r.get("mode") == "full"})
    champ = None
    if scored:
        champ = max(best_per_id(scored), key=lambda r: (r.get("mode") == "full", r["fitness"]))
    stale = generations - int(champ.get("generation") or 0) if champ else 0
    exhausted = []
    if generations >= budget.get("max_generations", 10**9):
        exhausted.append(f"max_generations reached ({generations})")
    if full_evals >= budget.get("max_full_evals", 10**9):
        exhausted.append(f"max_full_evals reached ({full_evals})")
    if champ and stale >= budget.get("stop_after_stale_rounds", 10**9):
        exhausted.append(f"stale for {stale} generations (champion {champ['id']} unbeaten)")
    return {"generations": generations, "full_evals": full_evals,
            "stale_generations": stale, "champion": champ["id"] if champ else None,
            "exhausted": exhausted}
