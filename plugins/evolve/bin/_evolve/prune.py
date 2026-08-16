"""prune: the trait-coverage plan and its audit — remove redundant VEHICLES, never traits.

Owner-directed principle (measured on a live 333-candidate pool growing +60/round):
pruning targets candidates whose traits are carried at-least-as-well by a diverse set of
other live candidates. The score stays true; the candidate simply stops being offered to
selection. The engine computes the plan, the owner applies it in a separate explicit call —
an auto-pruning engine would recreate the crowned-set pattern this engine removed.

THE RULE. Pool candidate c is prunable iff, against the CURRENT survivor set:
  - every trait (axis, impl) of its genome has >= D other carriers with fitness >=
    c.fitness - noise_floor ("carried at least as well"), at least one of them at
    fitness >= c.fitness outright (the best-anchor — without it, repeated plans ratchet
    a trait's best surviving expression down one noise floor per pass, unboundedly),
    and some carrier PAIR differing on >= min(2, n_axes-1) axes (one tight sibling
    cluster is not a diverse carrying set);
  - every 2-trait PAIR of its genome has >= 1 other carrier at fitness >= c.fitness -
    noise_floor, at least one of them at fitness >= c.fitness (the anchor applies at
    pair granularity too — combinations ratchet exactly like single traits). Traits are
    per-axis but value lives in combinations (epistasis): the singles-only rule measured
    on the live pool pruned 9 of the top 30 and erased 167 two-axis combos — winning
    ones included — while every single trait stayed "covered". Pair coverage is what
    makes "prune candidates, not traits" true at the granularity where search value
    actually lives.
Greedy, worst-fitness-first, judged against the survivors as of each turn. Pruning only
removes carriers, so coverage only decreases: one pass is a fixpoint, and a certificate
that holds at the end of the pass held for every earlier decision too. Rootless records
(no parent — the partition baseline) are never planned: a re-measurement roster needs the
baseline whatever else it omits. Sole carriers are immortal by construction — a trait
with no other carrier can never be covered.

DETERMINISM IS PART OF THE CONTRACT: the certificate is a pure function of the archive —
that is what makes it re-checkable — so nothing here may depend on set/hash iteration
order. Carriers are always processed in (-fitness, id) order, and the diversity scan
dedupes carriers by genome signature first (a 200-sibling cluster is ONE signature, so a
diverse partner cannot hide behind a truncation cap), then examines up to 64 distinct
signatures — a deterministic, conservative bound (a miss keeps the candidate, never
prunes it).

The certificate is re-checkable: audit() re-derives every standing prune's justification
against the current pool — at the min_carriers recorded ON its prune record, not at
today's default — and reports the ones that no longer hold (a retraction wave replayed
from the field would have voided 9 prunes, one trait to zero carriers, with nothing
noticing). doctor runs audit and warns; nothing ever auto-reinstates.
"""

import itertools

from . import archive

DIVERSITY_SIG_CAP = 64   # distinct genome signatures the pair scan examines (deterministic)


def _traits(rec):
    chunks = (rec.get("genome") or {}).get("chunks") or {}
    return {(ax, (c or {}).get("impl")) for ax, c in chunks.items() if (c or {}).get("impl")}


def _axis_distance(ta, tb):
    ga, gb = dict(ta), dict(tb)
    return sum(1 for ax in set(ga) | set(gb) if ga.get(ax) != gb.get(ax))


def _diverse_pair(carriers, traits_by_id, div_need):
    """True iff some carrier pair differs on >= div_need axes. Carriers arrive in
    (-fitness, id) order; identical genomes collapse to one signature so a sibling
    cluster of any size cannot crowd a diverse partner out of the scan window."""
    sigs, seen = [], set()
    for o in carriers:
        sig = tuple(sorted(traits_by_id[o]))
        if sig not in seen:
            seen.add(sig)
            sigs.append(sig)
            if len(sigs) >= DIVERSITY_SIG_CAP:
                break
    return any(_axis_distance(set(a), set(b)) >= div_need
               for a, b in itertools.combinations(sigs, 2))


def _coverage(cid, fc, traits_by_id, fit_by_id, ordered_ids, survivors, *,
              min_carriers, floor, div_need):
    """(why_kept, evidence): why_kept is None when the candidate is fully covered
    (prunable); evidence carries the weakest links so the owner reviews margins, not
    just a verdict. ordered_ids is the pool in (-fitness, id) order; survivors is the
    membership set — the split keeps every scan deterministic."""
    mine = traits_by_id[cid]
    others = [o for o in ordered_ids if o != cid and o in survivors]
    min_trait, min_pair = None, None
    for t in sorted(mine):
        carr = [o for o in others if t in traits_by_id[o] and fit_by_id[o] >= fc - floor]
        min_trait = len(carr) if min_trait is None else min(min_trait, len(carr))
        if len(carr) < min_carriers:
            return (f"trait {t[0]}/{t[1]}: {len(carr)} carrier(s) at fitness >= own - "
                    f"noise_floor (need {min_carriers})", None)
        if fit_by_id[carr[0]] < fc:   # ordered by -fitness: [0] is the best carrier
            return (f"trait {t[0]}/{t[1]}: no carrier at or above own fitness "
                    "(best-anchor)", None)
        if not _diverse_pair(carr, traits_by_id, div_need):
            return (f"trait {t[0]}/{t[1]}: carriers are one sibling cluster (need a pair "
                    f"differing on >= {div_need} axis(es))", None)
    for pa, pb in itertools.combinations(sorted(mine), 2):
        carr = [o for o in others
                if pa in traits_by_id[o] and pb in traits_by_id[o] and fit_by_id[o] >= fc - floor]
        min_pair = len(carr) if min_pair is None else min(min_pair, len(carr))
        if not carr:
            return (f"pair {pa[0]}/{pa[1]} + {pb[0]}/{pb[1]}: no other carrier at fitness "
                    ">= own - noise_floor", None)
        if fit_by_id[carr[0]] < fc:
            return (f"pair {pa[0]}/{pa[1]} + {pb[0]}/{pb[1]}: no carrier at or above own "
                    "fitness (best-anchor)", None)
    return None, {"min_trait_carriers": min_trait, "min_pair_carriers": min_pair}


def _pool_maps(pool):
    traits_by_id = {r["id"]: _traits(r) for r in pool}
    fit_by_id = {r["id"]: r["fitness"] for r in pool}
    n_axes = len({ax for t in traits_by_id.values() for ax, _ in t})
    # min(2, n_axes-1), floored at 1: carriers of trait (A, x) agree on axis A by
    # definition, so demanding a pair differing on 2 axes is UNSATISFIABLE below 3 axes —
    # the plan would be silently, permanently vacuous.
    div_need = min(2, max(1, n_axes - 1))
    ordered_ids = [r["id"] for r in sorted(pool, key=lambda r: (-r["fitness"], r["id"]))]
    return traits_by_id, fit_by_id, n_axes, div_need, ordered_ids


def plan(records, cfg, min_carriers=3):
    """Compute the prunable set. Writes nothing — applying is `evolve prune --ids`,
    a separate owner decision."""
    if not isinstance(min_carriers, int) or isinstance(min_carriers, bool) or min_carriers < 1:
        raise ValueError(f"min_carriers must be a positive integer, got {min_carriers!r} — "
                         "at 0 the sole-carrier immortality guarantee would be vacuous")
    sel_env = cfg["fitness"].get("selection_env")
    floor = cfg["fitness"].get("noise_floor")
    warnings = []
    if floor is None:
        warnings.append("fitness.noise_floor is unmeasured — coverage margins use 0.0 "
                        "(strictest); run `evolve doctor --measure-noise` for a "
                        "calibrated plan")
    pool = archive.selection_pool(records, sel_env)
    traits_by_id, fit_by_id, n_axes, div_need, ordered_ids = _pool_maps(pool)
    if n_axes < 3:
        warnings.append(f"pool genomes span only {n_axes} axis(es) — the carrier-diversity "
                        f"test degrades to a pair differing on >= {div_need} axis(es)")
    no_genome = sorted(r["id"] for r in pool if not traits_by_id[r["id"]])
    if no_genome:
        warnings.append(f"{len(no_genome)} pool candidate(s) carry no genome and cannot be "
                        f"planned (prune them explicitly if warranted): {no_genome[:5]}")
    survivors = {r["id"] for r in pool}
    prunable = []
    for c in sorted(pool, key=lambda r: (r["fitness"], r["id"])):
        if not c.get("parent") or not traits_by_id[c["id"]]:
            continue   # rootless (the baseline) and genome-less are never planned
        why, ev = _coverage(c["id"], c["fitness"], traits_by_id, fit_by_id, ordered_ids,
                            survivors, min_carriers=min_carriers, floor=floor or 0.0,
                            div_need=div_need)
        if why is None:
            survivors.discard(c["id"])
            prunable.append({"id": c["id"], "fitness": c["fitness"], **ev})
    return {"params": {"min_carriers": min_carriers, "noise_floor": floor,
                       "selection_env": sel_env, "pair_coverage": 1,
                       "diversity_axes": div_need},
            "pool": len(pool), "kept": len(survivors), "prunable": prunable,
            "warnings": warnings}


def prune_streams(records):
    """(id, env) -> the stream's LAST prune-shaped record. Prune records form
    independent per-partition streams (see archive.pruned_ids); certificate parameters
    (min_carriers) ride on the record, so the audit re-checks each prune against the
    bar it was actually made with."""
    out = {}
    for r in records:
        if isinstance(r.get("prune"), bool) and r.get("id") is not None:
            out[(r["id"], r.get("env"))] = r
    return out


def standing_streams(records, rid):
    """env -> the standing (prune: true) record for one id, across all partitions."""
    return {env: r for (i, env), r in prune_streams(records).items()
            if i == rid and r["prune"]}


def audit(records, cfg, min_carriers=3):
    """Re-derive every standing prune's coverage certificate against the CURRENT pool,
    at the min_carriers recorded on its own prune record (min_carriers here is only the
    fallback for records that predate the field). A prune whose carriers were since
    retracted, re-scored downward or themselves pruned no longer keeps its promise
    ('traits carried at-least-as-well elsewhere') — report it; reinstating is the
    owner's call, never this function's. Each voided entry carries trait_lost: True when
    a trait or pair has ZERO surviving carriers — the hard "never traits" invariant is
    broken, not merely thinned — which is the distinction doctor gates on."""
    sel_env = cfg["fitness"].get("selection_env")
    pruned = archive.pruned_ids(records, sel_env)
    if not pruned:
        return {"pruned": 0, "voided": [], "unauditable": []}
    floor = cfg["fitness"].get("noise_floor") or 0.0
    pool = archive.selection_pool(records, sel_env)
    per_id = {r["id"]: r for r in archive.best_per_id(archive.valid(records, sel_env))}
    traits_by_id, fit_by_id, _, div_need, ordered_ids = _pool_maps(pool)
    survivor_ids = {r["id"] for r in pool}
    retracted = archive.retracted_ids(records, sel_env)
    streams = prune_streams(records)
    voided, unauditable = [], []
    for rid in sorted(pruned):
        if rid in retracted:
            continue   # retraction outranks — the id is out of selection regardless
        rec = per_id.get(rid)
        if rec is None:
            unauditable.append({"id": rid, "why": "no valid record in the current partition"})
            continue
        mine = _traits(rec)
        if not mine:
            unauditable.append({"id": rid, "why": "no genome traits (explicit prune, no "
                                                  "coverage certificate)"})
            continue
        # Each standing prune is judged independently against the survivors — the same
        # question the plan asked, re-asked now: would the current pool still cover it?
        # The bar: the id's standing stream in this partition; unpinned, the STRICTEST
        # standing bar across partitions (conservative — flags more, hides nothing).
        if sel_env is not None:
            bars = [(streams.get((rid, sel_env)) or {}).get("min_carriers")]
        else:
            bars = [r.get("min_carriers") for (i, _), r in streams.items()
                    if i == rid and r["prune"]]
        d = max((int(b) for b in bars if b), default=min_carriers)
        tb = dict(traits_by_id, **{rid: mine})
        fb = dict(fit_by_id, **{rid: rec["fitness"]})
        why, _ = _coverage(rid, rec["fitness"], tb, fb, ordered_ids, survivor_ids,
                           min_carriers=d, floor=floor, div_need=div_need)
        if why is not None:
            # trait_lost is ABSOLUTE (any-fitness carriage), not margin-relative: a trait
            # carried only far below the pruned candidate is thinned, not gone.
            lost = (any(not any(t in traits_by_id[o] for o in ordered_ids) for t in mine)
                    or any(not any(pa in traits_by_id[o] and pb in traits_by_id[o]
                                   for o in ordered_ids)
                           for pa, pb in itertools.combinations(sorted(mine), 2)))
            voided.append({"id": rid, "why": why, "trait_lost": lost})
    return {"pruned": len(pruned), "voided": voided, "unauditable": unauditable}
