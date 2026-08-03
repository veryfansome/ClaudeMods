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
import statistics
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
    full re-score, another env) — best_per_id collapses at read time. What is NOT allowed
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
    if r.get("retract"):
        if fit is not None:
            raise ValueError("a retraction must carry fitness null — retract: true invalidates the "
                             "id's EARLIER records; a new score is a separate, later record")
        if not r.get("guardrail") or r.get("guardrail") == "pass":
            raise ValueError("a retraction needs a guardrail reason (it is the audit trail for "
                             "why the id's earlier scores stopped counting)")
    if "private" in r:
        raise ValueError("private metrics must not be embedded in archive records "
                         "(they are written to archive/private/ instead)")


def valid(records, selection_env=None):
    """Records that count as FITNESS: numerically scored, not on the firewalled split, and
    — when selection_env is set — scored in that environment only. Scores are comparable
    only within one environment, so a configured selection_env firewalls foreign-env
    (e.g. ingested) records out of sampling/leaderboard the same way final-split records are.

    RETRACTION (record-scoped, explicit): the archive is append-only, so invalidating a
    previously-passing candidate (e.g. a post-hoc eval-validity discovery) is done by
    appending a fitness-null record with `"retract": true` and a guardrail reason (via
    `ingest`). A retraction invalidates the id's records appended BEFORE it — never ones
    after — so a later rescore under a fixed eval reinstates the id on the NEW score only;
    the tainted pre-retraction score stays history and never re-enters selection. The
    marker must be explicit: an ordinary fitness-null failure (a flaky rerun timeout, an
    ingested remote crash) is inert search signal, not a retraction — otherwise one flake
    on a paired rerun of a top candidate would silently drop it. Scope = the same
    (non-final [, selection_env]) partition this call selects over; file order is append
    order and decides before/after."""
    scoped = [r for r in records if r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]
    if selection_env is not None:
        scoped = [r for r in scoped if r.get("env") == selection_env]
    last_retract = {}
    for i, r in enumerate(scoped):
        if r.get("retract") and r.get("id") is not None:
            last_retract[r["id"]] = i
    # id-less records (hand-edited/torn lines) are excluded outright: every consumer keys on
    # r["id"], so admitting one turns board/sample/brief into raw KeyError tracebacks before
    # doctor — whose job is REPORTING that corruption — can even be pointed at the file.
    return [r for i, r in enumerate(scoped)
            if r.get("id") is not None and isinstance(r.get("fitness"), (int, float))
            and i > last_retract.get(r["id"], -1)]


def latest_valid(records, selection_env=None):
    """id -> its LATEST record that still counts (valid()'s scope). Retracted ids are
    ABSENT rather than present-with-null, so a membership check fails closed instead of
    emitting a retracted genome as if it were live — an owner-revoked crossover partner
    once sailed through a raw last-record-wins lookup this way. The diet and the jail
    key on this."""
    out = {}
    for r in valid(records, selection_env):
        out[r["id"]] = r
    return out


def retracted_ids(records, selection_env=None, cross_partition=False):
    """id -> guardrail reason, for ids whose latest verdict is a retraction — the VALIDITY
    view, distinct from valid()'s fitness view. Fitness is partition-scoped (scores never
    cross environments), but a validity verdict is a property of the MECHANISM: a candidate
    revoked for reading post-event data is invalid on every dataset, so a regime change must
    not launder it clean — which a same-env check would do, since the revoked id may have no
    record at all in the new partition. cross_partition=True ignores env for exactly that;
    the final split still never evicts a verdict in either mode.

    An id is retracted iff its last retraction comes AFTER its last numerically-scored
    record, in append order. Keying on "is the LATEST record a retraction" is wrong in both
    directions (measured in the field): a clean rescore after a retraction correctly
    reinstates, but so would a single fitness-null FLAKE — silently un-retracting an
    owner-revoked candidate."""
    env = None if cross_partition else selection_env
    scoped = [r for r in records if r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]
    if env is not None:
        scoped = [r for r in scoped if r.get("env") == env]
    last_retract, last_score, reason = {}, {}, {}
    for i, r in enumerate(scoped):
        rid = r.get("id")
        if rid is None:
            continue
        if r.get("retract"):
            last_retract[rid] = i
            reason[rid] = str(r.get("guardrail") or "retracted")
        elif isinstance(r.get("fitness"), (int, float)):
            last_score[rid] = i
    return {rid: reason[rid] for rid, i in last_retract.items() if i > last_score.get(rid, -1)}


def scored_any(records):
    """Every numerically-scored non-final record, INCLUDING retracted ids. The dedup corpus
    uses this instead of valid(): retraction removes an id from selection, not from memory —
    a byte-identical resubmission of a retracted design under a fresh id is still a duplicate
    (rescore of the id itself self-excludes from dedup, so reinstatement is unaffected)."""
    return [r for r in records if r.get("id") is not None
            and isinstance(r.get("fitness"), (int, float))
            and r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]


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
    proxy->full re-score or a paired rerun — re-scores of the SAME child — inflate the
    parent's novelty penalty on every re-score."""
    children = {}
    for r in records:
        p = r.get("parent")
        if p and r.get("id") is not None:
            children.setdefault(p, set()).add(r["id"])
    return {p: len(kids) for p, kids in children.items()}


def fitness_scale(fitnesses):
    """Robust spread estimate: IQR/1.349 (≈ sd under normality), falling back to pstdev when
    the IQR degenerates (>50% ties). Robust on purpose — the scale must describe the
    population being discriminated, so one wrongly-scaled ingest or a genuine breakthrough
    outlier cannot set (and thereby collapse) the pressure for everyone else."""
    if len(fitnesses) < 2:
        return 0.0
    q = statistics.quantiles(fitnesses, n=4, method="inclusive")
    iqr = q[2] - q[0]
    return iqr / 1.349 if iqr > 0 else statistics.pstdev(fitnesses)


def resolve_lambda(lam, fitnesses, noise_floor=None):
    """λ is in units of 1/fitness, so a FIXED default silently goes inert on the wrong scale:
    measured in the field, margin-scale fitness (sd ≈ 0.014) under the old fixed λ=10 made
    parent sampling statistically indistinguishable from uniform for eight straight rounds,
    and nothing surfaced it. "auto" (or null) derives λ = 2.5/scale over the population
    being sampled — candidates one scale-unit above vs one below the median then differ by
    ~12× in weight on ANY fitness scale — recomputed every call so the pressure tracks the
    archive. The scale is robust (see fitness_scale) and FLOORED at the measured noise
    floor: spread within eval noise is a coin-flip ordering, and amplifying it would
    tunnel-vision the search onto whichever candidate got the luckier run (two candidates a
    hair apart used to get a constant 75:1). All-equal fitness degrades to uniform. An
    explicit finite number is honored as-is; non-finite degrades to uniform."""
    if lam in (None, "auto"):
        sd = max(fitness_scale(fitnesses), noise_floor or 0.0)
        lam = 2.5 / sd if sd > 0 else 0.0
    else:
        lam = float(lam)
    return lam if math.isfinite(lam) else 0.0


def selection_weights(items, lam, offspring, noise_floor=None):
    """(resolved λ, per-item weights) for p_i ∝ σ(λ·(F_i − median F)) · 1/(1 + offspring_i).
    Shared by sample_parent (the decision) and doctor's realized-pressure diagnostic, so what
    doctor reports is by construction what sampling actually does. Pass offspring={} to see
    the fitness pressure alone."""
    fits = sorted(r["fitness"] for r in items)
    med = fits[len(fits) // 2]
    lam = resolve_lambda(lam, fits, noise_floor)
    return lam, [_sigmoid(lam * (r["fitness"] - med)) / (1.0 + offspring.get(r["id"], 0))
                 for r in items]


def sample_parent(root, seed=0, lam="auto", selection_env=None, noise_floor=None):
    """p_i ∝ σ(λ·(F_i − median F)) · 1/(1 + offspring_i) — performance × novelty
    (ShinkaEvolve §3.1 weighted sampling, the paper's largest ablated gain). Seeded and
    deterministic given the archive state and λ. Returns a record or None on an empty archive."""
    all_recs = load(root)
    items = best_per_id(valid(all_recs, selection_env))
    if not items:
        return None
    # Penalize only offspring in the pool being sampled (same env, non-final), so the diversity
    # bonus matches the population the board/brief show.
    offspring = offspring_counts(valid(all_recs, selection_env))
    _, weights = selection_weights(items, lam, offspring, noise_floor)
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
    """Parent's generation + 1; with no parent, max(generation) + 1 (a rootless record starts
    a new front). An UNKNOWN parent is an error, not a fallback: the silent max+1 fallback
    once stamped a 74-record bulk ingest as generations 66-81 against a population whose
    real work stopped at 7 — 'stale for 74 generations', sampling refused — and
    mis-recorded lineage besides."""
    recs = load(root)
    if parent_id == "":
        # "" is falsy, so it used to take the silent max+1 fallback below — the third spelling
        # of the parentless-bulk-ingest bug ('always pass --parent' is satisfiable by --parent "").
        raise ValueError('--parent "" is empty — pass a real archived id, omit the flag, or pass '
                         "an explicit --generation")
    if parent_id:
        for r in recs:
            if r.get("id") == parent_id:
                return int(r.get("generation") or 0) + 1
        raise ValueError(f"parent {parent_id!r} is not in the archive — name an archived id, or "
                         "pass an explicit generation (score/ingest take --generation; e.g. for "
                         "a bulk ingest whose parents live in another archive)")
    return max((int(r.get("generation") or 0) for r in recs), default=-1) + 1


def budget_state(root, budget, selection_env=None):
    """Spend vs. the configured budget, from the archive alone (no process state). Final-split
    records are excluded from generation/staleness math: validation runs and re-scores must
    not tick the search toward a spurious stop.

    Staleness is a progress-recency measure with no crowned target: generations of search
    work since a NEW BEST fitness was recorded, in APPEND order — the clock resets when a
    record arrives that strictly beats every score appended before it, and "work since" is
    how far max(generation) has advanced past that point. Judged at append time, so a later
    retraction of the best does not retro-date the clock (staleness is activity recency,
    not current validity — retro-dating would spike it by the whole generation gap right
    when a retraction means the search should re-explore). Two rejected alternatives, both
    measured to fail: anchoring to the top record's own GENERATION confuses lineage depth
    with recency (a fresh winner bred from a shallow parent looks ancient next to a deep
    failing lineage); a softer "new above-median candidate" bar resets ~half the time under
    pure stagnation and always on an equal-fitness plateau, so a stop built on it cannot
    fire in exactly the regime it exists to detect.

    Stop conditions are opt-in: an absent/empty budget block means UNLIMITED — counts and
    staleness are still reported, nothing ever "exhausts". `{"budget": {"disabled": true}}`
    goes one step further and also suppresses the staleness readout, for protocols where
    stopping is entirely the owner's call."""
    recs = load(root)
    # Count generations/full_evals over the SAME env-partitioned, non-final population that
    # selection draws from, so a foreign-env record (e.g. an ingest) can't burn this env's
    # budget or trip its staleness gate. load() preserves append order, which decides "since".
    selectable = [r for r in recs if r.get("id") is not None
                  and r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT
                  and (selection_env is None or r.get("env") == selection_env)]
    full_evals = len({r["id"] for r in selectable if r.get("mode") == "full"})
    best, gen_seen, gen_at_best = None, 0, 0
    for r in selectable:
        gen_seen = max(gen_seen, int(r.get("generation") or 0))
        f = r.get("fitness")
        if isinstance(f, (int, float)) and (best is None or f > best):
            best, gen_at_best = f, gen_seen
    generations = gen_seen
    if budget.get("disabled"):
        return {"generations": generations, "full_evals": full_evals, "stale_generations": None,
                "exhausted": [], "disabled": True}
    stale = generations - gen_at_best if best is not None else 0
    exhausted = []
    if generations >= budget.get("max_generations", 10**9):
        exhausted.append(f"max_generations reached ({generations})")
    if full_evals >= budget.get("max_full_evals", 10**9):
        exhausted.append(f"max_full_evals reached ({full_evals})")
    if best is not None and stale >= budget.get("stop_after_stale_rounds", 10**9):
        exhausted.append(f"stale for {stale} generations (no new best fitness)")
    return {"generations": generations, "full_evals": full_evals,
            "stale_generations": stale, "exhausted": exhausted}
