"""board: leaderboard + budget + neutral per-inventor/per-operator stats.

Stats, not verdicts — the numbers are reported raw so the next round's judgment isn't
biased by an earlier round's editorializing. Budget state comes purely from the archive,
so it survives sessions and machines with zero process memory.
"""

from . import archive
from .config import FINAL_SPLIT, DEFAULT_SPLIT


def _by(records, key):
    groups = {}
    for r in records:
        groups.setdefault(r.get(key) or "?", []).append(r)
    out = {}
    for name, recs in sorted(groups.items()):
        scored = [r["fitness"] for r in recs if isinstance(r.get("fitness"), (int, float))]
        out[name] = {
            "n": len(recs),
            "scored": len(scored),
            "failed": len(recs) - len(scored),
            "mean_fitness": round(sum(scored) / len(scored), 4) if scored else None,
            "best_fitness": round(max(scored), 4) if scored else None,
        }
    return out


def report(root, cfg, top=10):
    recs = archive.load(root)
    sel_env = cfg["fitness"].get("selection_env")
    selection = [r for r in recs if r.get("split", DEFAULT_SPLIT) != FINAL_SPLIT]
    final = [r for r in recs if r.get("split") == FINAL_SPLIT]
    champ = archive.best(root, sel_env)
    envs = archive.selection_envs(recs)
    commits = sorted({r.get("commit") for r in archive.valid(recs, sel_env) if r.get("commit")})
    warnings = []
    if sel_env is None and len(envs) > 1:
        warnings.append(f"selection spans multiple environments {envs} but fitness.selection_env "
                        "is unset — cross-env scores are not comparable; set selection_env to partition")
    if sel_env is not None and not archive.valid(recs, sel_env) and archive.valid(recs):
        warnings.append(f"fitness.selection_env={sel_env!r} matches 0 of {len(archive.valid(recs))} "
                        f"scored records (envs present: {envs}) — no parent can be selected; "
                        "re-score/re-baseline a candidate in this env or fix the tag")
    if len(commits) > 1:
        warnings.append(f"scored candidates span {len(commits)} HEAD commits — the frozen baseline "
                        "shifted mid-campaign; fitnesses from different commits are not comparable")
    return {
        "leaderboard": [
            {"id": r["id"], "fitness": r["fitness"], "mode": r.get("mode"),
             "generation": r.get("generation"), "inventor": r.get("inventor"),
             "operator": r.get("operator"), "env": r.get("env"), "rationale": r.get("rationale")}
            for r in archive.leaderboard(root, top, sel_env)
        ],
        "champion": champ["id"] if champ else None,
        "budget": archive.budget_state(root, cfg["budget"], sel_env),
        "noise_floor": cfg["fitness"].get("noise_floor"),
        "selection_env": sel_env,
        "environments": envs,
        "records": {"total": len(recs), "selection_split": len(selection), "final_split": len(final)},
        "by_inventor": _by(selection, "inventor"),
        "by_operator": _by(selection, "operator"),
        "final_split_runs": [
            {"id": r["id"], "fitness": r.get("fitness"), "mode": r.get("mode"), "env": r.get("env")}
            for r in final
        ],
        "warnings": warnings,
    }


def render(rep):
    lines = ["LEADERBOARD (full-budget scores outrank proxy):"]
    if not rep["leaderboard"]:
        total = rep["records"]["total"]
        lines.append("  (archive empty)" if total == 0 else
                     f"  (no selectable candidates — {total} records archived but none in the "
                     f"selection pool; see warnings)")
    for r in rep["leaderboard"]:
        fit = f"{r['fitness']:+.4f}" if isinstance(r["fitness"], (int, float)) else " fail "
        lines.append(f"  {fit}  {r['mode'] or '?':5s}  gen{r['generation'] or 0:<3} "
                     f"{r['id']:26s}  [{r.get('inventor') or '?'}/{r.get('operator') or '?'}]  "
                     f"{(r.get('rationale') or '')[:48]}")
    b = rep["budget"]
    lines.append(f"\nBUDGET: gen {b['generations']}, full evals {b['full_evals']}, "
                 f"stale {b['stale_generations']} gens"
                 + (f" — EXHAUSTED: {'; '.join(b['exhausted'])}" if b["exhausted"] else ""))
    lines.append(f"noise floor: {rep['noise_floor']}   champion: {rep['champion']}"
                 + (f"   selection_env: {rep['selection_env']}" if rep.get("selection_env") else ""))
    for w in rep.get("warnings", []):
        lines.append(f"⚠ {w}")
    if rep["final_split_runs"]:
        lines.append("final-split validations (never used for selection): "
                     + ", ".join(f"{r['id']}={r['fitness']}" for r in rep["final_split_runs"]))
    for key, title in (("by_inventor", "BY INVENTOR"), ("by_operator", "BY OPERATOR")):
        lines.append(f"\n{title}:")
        for name, s in rep[key].items():
            lines.append(f"  {name:22s} n={s['n']:<3} scored={s['scored']:<3} failed={s['failed']:<3} "
                         f"mean={s['mean_fitness']} best={s['best_fitness']}")
    return "\n".join(lines)
