"""Pre-registered promotion gates — committed BEFORE any candidate is scored, so "did it
win?" is decided by numbers fixed in advance rather than chosen after seeing results.

This is the discipline that made two consecutive NO-PROMOTION rounds stick in the reference
search: a striking proxy gain that inverted at full budget was rejected because the gates were
already on record. Three gates:
  - G1 — fitness NON-REGRESSION (sacred, auto-derived): a promotion may not drop the primary
         fitness below champion − noise_floor. Machine-checkable (`evolve gate`).
  - G2 — the round's OBJECTIVE: what this round is actually trying to move; the operator states
         a one-line predicate and (optionally) a numeric threshold. Judged by the operator/skill.
  - G3 — ADVISORY only: metrics worth watching, explicitly neither sufficient nor necessary.
Promotion requires G1 AND G2. A manifest is immutable once written (append a new round to
revise). Absent any manifest, the engine behaves exactly as before — this is opt-in.
"""

import datetime
import json
import re

from . import archive
from .config import state_dir

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def prereg_dir(root):
    return state_dir(root) / "prereg"


def _validate_tag(round_tag):
    """A round tag becomes a filename — reject anything that would traverse or nest."""
    if not isinstance(round_tag, str) or not _TAG_RE.match(round_tag):
        raise ValueError(f"invalid --round {round_tag!r}: use letters/digits/._- and no path "
                         "separators (it becomes a filename)")
    return round_tag


def path(root, round_tag):
    return prereg_dir(root) / f"{round_tag}.json"


def load_all(root):
    d = prereg_dir(root)
    if not d.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def active(root):
    """The most recently registered round's gates, or None. Ordered by the monotonic `seq`
    stamped at registration — NOT by filename, which sorts lexically (r10 < r2) and would
    surface a stale round's laxer G1 floor once tags pass r9."""
    allp = load_all(root)
    return max(allp, key=lambda m: m.get("seq", -1)) if allp else None


def register(root, cfg, *, round_tag, g2_desc, g2_threshold=None, g3_desc=None, split=None):
    """Write the immutable gate manifest for a round. Auto-derives G1 from the LIVE champion and
    the measured noise floor, so the sacred non-regression bar is a computed number, not a
    hand-typed one that can be fat-fingered or quietly relaxed later."""
    _validate_tag(round_tag)
    p = path(root, round_tag)
    if p.exists():
        raise ValueError(f"prereg for round {round_tag!r} already exists ({p}); manifests are "
                         "immutable — register a new round tag to revise gates")
    sel_env = cfg["fitness"].get("selection_env")
    champ = archive.best(root, sel_env)
    if not champ:
        if sel_env is not None and archive.best(root):
            raise ValueError(f"no scored champion in fitness.selection_env={sel_env!r} (records "
                             "exist under other envs) — re-baseline a candidate in this env, or fix "
                             "the tag, before pre-registering gates")
        raise ValueError("no scored champion yet — seed and score at least one candidate before "
                         "pre-registering gates (G1 is derived from the champion)")
    floor = cfg["fitness"].get("noise_floor")
    if floor is None:
        raise ValueError("fitness.noise_floor is unmeasured — run `evolve doctor --measure-noise` "
                         "first (G1 = champion fitness − noise_floor)")
    existing = load_all(root)
    rec = {
        "round": round_tag,
        "seq": max((m.get("seq", -1) for m in existing), default=-1) + 1,   # monotonic; orders active()
        "registered_at": datetime.date.today().isoformat(),
        "committed_at_generation": archive.budget_state(root, cfg["budget"], sel_env)["generations"],
        "champion": {"id": champ["id"], "fitness": champ["fitness"],
                     "mode": champ.get("mode"), "env": champ.get("env")},
        "noise_floor": floor,
        "g1_fitness_floor": round(champ["fitness"] - floor, 6),
        "g2": {"desc": g2_desc, "threshold": g2_threshold},
        "g3": {"desc": g3_desc} if g3_desc else None,
        "split": split or cfg["eval"]["splits"][0],
        "selection_env": sel_env,
    }
    prereg_dir(root).mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def check_gate(root, cfg, *, round_tag, candidate_id):
    """Evaluate the SACRED numeric gate G1 for a candidate against the round's manifest. G1 is
    engine-owned and auto-checkable (fitness non-regression); G2/G3 are echoed for the operator
    to judge. Returns a report; never decides promotion by itself (G2 is domain judgment)."""
    manifests = {m["round"]: m for m in load_all(root)}
    if round_tag not in manifests:
        raise ValueError(f"no prereg manifest for round {round_tag!r} (have {sorted(manifests)})")
    m = manifests[round_tag]
    cand = next((r for r in archive.best_per_id(archive.valid(archive.load(root),
                 cfg["fitness"].get("selection_env"))) if r["id"] == candidate_id), None)
    if cand is None:
        raise ValueError(f"candidate {candidate_id!r} has no scored (selectable) record to gate")
    floor = m["g1_fitness_floor"]
    g1_pass = cand["fitness"] >= floor
    proxy = cand.get("mode") != "full"
    verdict = ("G1 holds — now judge G2 (the objective) before promoting"
               if g1_pass else "G1 FAILS — fitness regressed; do not promote regardless of G2")
    if g1_pass and proxy:
        verdict += " — but this is a PROXY score, which can invert at full budget; re-score at " \
                   "full and re-check G1 before the win counts"
    return {
        "round": round_tag,
        "candidate": {"id": candidate_id, "fitness": cand["fitness"], "mode": cand.get("mode")},
        "g1": {"floor": floor, "pass": g1_pass,
               "note": "SACRED — fitness must not regress below champion − noise_floor"},
        "g2": {**m["g2"], "note": "the round objective — judge from the candidate's public metrics / text_feedback"},
        "g3": m.get("g3"),
        "proxy_only": proxy,
        "promotable_on_g1": g1_pass and not proxy,
        "verdict": verdict,
    }
