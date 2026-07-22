"""Build the inventor brief context — the ONE channel every mutation operator receives.

Everything selection-relevant that should shape a proposal is injected here
deterministically (leaderboard, frontier, inspiration code worst→best, do-not-resubmit,
insights, standing rules, the parent's text_feedback), so heterogeneous inventors —
Claude subagents, external CLI agents — get identical grounding (that identical brief is
what makes mixing them genuine diversity rather than an apples-to-oranges comparison).
Private metrics never pass through here: this module is the anti-overfit choke point.
"""

import pathlib

from . import archive, surface
from .config import state_dir

OPERATOR_INSTRUCTIONS = {
    "diff": ("OPERATOR: TARGETED EDIT — make a focused change to the parent; do NOT rewrite "
             "everything. Keep what works, change one mechanism."),
    "rewrite": ("OPERATOR: REWRITE — replace the mutable code wholesale with a genuinely "
                "different design. A rewrite that lands near the parent is a wasted slot."),
    "cross": ("OPERATOR: CROSSOVER — combine the parent with the second program below into one "
              "coherent design that keeps the best mechanism of each."),
}

DEFAULT_STANDING_RULES = [
    "NOVELTY OVER SAFETY — a safe tweak is a wasted slot; invent a genuinely different mechanism "
    "or a novel recombination of archived ideas. Commit to ONE best design.",
    "RETRY FAILED TRAITS — a design that scored low before may win in a changed context "
    "(recombined with a newer winner); if you retry one, argue what changed.",
    "LOOK OUTSIDE THE DOMAIN — search the literature beyond this problem's field and translate "
    "ONE concrete mechanism into code (equations, not metaphor).",
    "NEVER touch the eval, the metric, the splits, or any protected path — the harness re-checks "
    "structurally and a violation scores as a failed candidate.",
]


def _fitness_str(r):
    return f"{r['fitness']:+.4f}" if isinstance(r.get("fitness"), (int, float)) else " fail "


def _one_liner(r, axis=None):
    tag = ""
    if axis and isinstance(r.get("genome"), dict):
        impl = (r["genome"].get("chunks") or {}).get(axis, {}).get("impl", "·")
        tag = f"{impl:>18s}  "
    return (f"  {_fitness_str(r)}  ({r.get('mode', '?'):5s})  {r['id']:26s}  {tag}"
            f"{(r.get('rationale') or '')[:56]}")


def leaderboard_block(root, axis=None, top=10, selection_env=None):
    lb = archive.leaderboard(root, top, selection_env)
    if not lb:
        return "LEADERBOARD: (no scored candidates yet — you are mutating the seed)"
    return "LEADERBOARD (top candidates by fitness; full-budget scores outrank proxy):\n" + \
        "\n".join(_one_liner(r, axis) for r in lb)


def frontier_block(root, selection_env=None):
    champ = archive.best(root, selection_env)
    if not champ:
        return "CURRENT FRONTIER: (archive empty)"
    recipe = surface.genome_recipe(champ["genome"]) if isinstance(champ.get("genome"), dict) \
        else champ["surface"].get("patch", "see archived diff") if isinstance(champ.get("surface"), dict) else ""
    lines = [f"CURRENT FRONTIER — the best candidate so far; a mutation replaces one mechanism and keeps the rest:",
             f"  {champ['id']} (fitness {_fitness_str(champ)}, {champ.get('mode')}): {recipe}"]
    if champ.get("text_feedback"):
        lines.append(f"  its eval feedback: {champ['text_feedback'][:400]}")
    return "\n".join(lines)


def _candidate_code(root, cfg, record, axis=None):
    """The code a record represents: registry -> the axis impl file; markers -> the archived
    mutable-region snapshot."""
    if cfg["surface"]["mode"] == "registry":
        impl = (record.get("genome") or {}).get("chunks", {}).get(axis or "", {}).get("impl")
        if not impl:
            return None
        header = f"# ==== {axis} impl: {impl} (fitness {_fitness_str(record)}) ===="
        try:
            return header + "\n" + surface.impl_path(root, cfg, axis, impl).read_text().rstrip()
        except surface.SurfaceError:
            pass
        # Working-tree impl is gone (branch switch / git clean); fall back to the copy the
        # archive kept at score time so the inspiration isn't silently lost.
        art = (record.get("surface") or {}).get("impls_artifact")
        if art:
            d = state_dir(root).parent / art if not str(art).startswith("archive") \
                else state_dir(root) / art
            for p in sorted(pathlib.Path(d).glob(f"{axis}__*")) if pathlib.Path(d).is_dir() else []:
                return header + "\n" + p.read_text().rstrip()
        return None
    rel = (record.get("surface") or {}).get("patch", "")
    region = state_dir(root) / "archive" / "regions" / (pathlib.Path(rel).stem + ".txt") if rel else None
    if region and region.exists():
        return f"# ==== {record['id']} (fitness {_fitness_str(record)}) — mutable region ====\n" + \
            region.read_text().rstrip()
    return None


def inspirations_block(root, cfg, parent_id, axis=None, n_top=2, n_archive=2, seed=0, selection_env=None):
    """Inspiration exemplars, worst→best (FunSearch best-shot: the prompt itself encodes an
    improvement gradient), with scores and eval feedback."""
    recs = archive.sample_inspirations(root, parent_id, n_top=n_top, n_archive=n_archive,
                                       seed=seed, selection_env=selection_env)
    blocks, seen = [], set()
    for r in recs:
        code = _candidate_code(root, cfg, r, axis)
        if code and code not in seen:
            seen.add(code)
            if r.get("text_feedback"):
                code += f"\n# eval feedback: {r['text_feedback'][:300]}"
            blocks.append((r["fitness"], code))
    if not blocks:
        return ""
    blocks.sort(key=lambda b: b[0])  # worst -> best
    sep = "-" * 80
    return ("INSPIRATIONS — archived candidates, worst to best. Study them; your job is to BEAT "
            f"the best (build on, recombine, or out-invent — do NOT re-derive them):\n{sep}\n"
            + "\n\n".join(b[1] for b in blocks) + f"\n{sep}")


def tried_block(root, cfg, axis=None):
    if cfg["surface"]["mode"] == "registry" and axis:
        return f"ALREADY REGISTERED for axis {axis!r} (do NOT resubmit): {surface.list_impls(root, cfg, axis)}"
    ids = [f"{r['id']} ({_fitness_str(r)}: {(r.get('rationale') or '')[:48]})"
           for r in archive.load(root)][-12:]
    return "ALREADY TRIED (recent candidates — do NOT resubmit an equivalent):\n  " + \
        "\n  ".join(ids) if ids else ""


def insights_block(root):
    p = state_dir(root) / "insights.md"
    if not p.exists() or not p.read_text().strip():
        return ""
    return f"INSIGHTS (accumulated across rounds — recommendations, not orders):\n{p.read_text().strip()}"


def parent_block(root, cfg, parent_record, axis=None):
    if parent_record is None:
        return "PARENT: the seed program (unmodified baseline)."
    lines = [f"PARENT — you are mutating this candidate ({parent_record['id']}, "
             f"fitness {_fitness_str(parent_record)}):"]
    code = _candidate_code(root, cfg, parent_record, axis)
    if code:
        lines.append(code)
    elif isinstance(parent_record.get("genome"), dict):
        lines.append(f"  recipe: {surface.genome_recipe(parent_record['genome'])}")
    if parent_record.get("text_feedback"):
        lines.append(f"PARENT'S EVAL FEEDBACK: {parent_record['text_feedback'][:600]}")
    return "\n".join(lines)


def contract_block(root, cfg, axis=None):
    if cfg["surface"]["mode"] == "registry":
        if not axis:
            raise ValueError("registry briefs need --axis")
        spec = cfg["surface"]["registry"]["axes"][axis]
        base = surface.impl_path(root, cfg, axis, spec["baseline"])
        sep = "-" * 80
        return (f"THE CONTRACT — axis {axis!r}: {spec.get('contract', '(see the baseline below)')}\n"
                f"The reference baseline below is authoritative — match its interface exactly, keep "
                f"your module self-contained:\n{sep}\n{base.read_text().rstrip()}\n{sep}")
    parts = [f"THE CONTRACT — edit ONLY between {surface.MARK_START} and {surface.MARK_END} marker "
             f"lines in these files (everything else, marker lines included, is frozen and "
             f"structurally enforced):"]
    for path in cfg["surface"]["files"]:
        p = pathlib.Path(root) / path
        if p.exists():
            sep = "-" * 80
            parts.append(f"--- {path} (current content) ---\n{sep}\n{p.read_text().rstrip()}\n{sep}")
    return "\n".join(parts)


def build(root, cfg, *, operator="diff", parent_id=None, axis=None, cross_with=None, seed=0):
    sel_env = cfg["fitness"].get("selection_env")
    recs = {r["id"]: r for r in archive.best_per_id(archive.valid(archive.load(root), sel_env))}
    parent = recs.get(parent_id) if parent_id else None
    sections = [
        f"TASK: {cfg['task']}",
        OPERATOR_INSTRUCTIONS.get(operator, OPERATOR_INSTRUCTIONS["diff"]),
        contract_block(root, cfg, axis),
        parent_block(root, cfg, parent, axis),
    ]
    if operator == "cross" and cross_with and cross_with in recs:
        partner = recs[cross_with]
        code = _candidate_code(root, cfg, partner, axis)
        sections.append(f"CROSSOVER PARTNER ({partner['id']}, fitness {_fitness_str(partner)}):\n"
                        + (code or f"  recipe: {surface.genome_recipe(partner.get('genome', {}))}"))
    sections += [
        leaderboard_block(root, axis, selection_env=sel_env),
        frontier_block(root, selection_env=sel_env),
        inspirations_block(root, cfg, parent_id, axis,
                           n_top=cfg["search"]["inspirations"]["top_k"],
                           n_archive=cfg["search"]["inspirations"]["archive"], seed=seed,
                           selection_env=sel_env),
        tried_block(root, cfg, axis),
        insights_block(root),
        "STANDING RULES (every inventor, every round):\n- "
        + "\n- ".join(cfg["search"].get("standing_rules") or DEFAULT_STANDING_RULES),
    ]
    return "\n\n".join(s for s in sections if s)
