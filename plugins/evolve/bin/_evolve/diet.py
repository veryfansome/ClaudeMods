"""The inventor information diet: a brief that grants exactly parent + baseline + mechanisms.

Transcribed from a field campaign's project-side filter, which post-processed the engine's
raw brief because the engine couldn't be changed. The engine CAN compose clean directly —
it holds the archive records, the chunk registry and the config as data — so this module
BUILDS the diet brief instead of parsing prose back apart. The field tool's assertion
layer survives as the emit gate: redact first, then assert — the assertion is the check on
the redaction, not a substitute for it. A brief that fails its assertions emits NOTHING.

The rule being enforced (field owner ruling): an inventor sees its parent's genome, its
parent's fitness, and the baseline's fitness. NOT a leaderboard, NOT any best-so-far, NOT
other candidates' scores. Prior work arrives as impl SOURCE with no outcome attached and
no ordering implied — the standard brief's worst→best inspiration order ranks candidates
even with every number stripped, and so does any outcome label (measured in the field over
3,297 realized slots: a child-vs-own-parent label reproduced fitness order with perfect
concordance on several label pairs). Source is shippable only when the registry carries no
inventor prose; prose hygiene is an intake concern, not a brief-time rewrite.

The diet is one half of a boundary. It makes the briefing CHANNEL clean; the jail
(jail.py) makes it the ONLY channel. They are only sufficient together.
"""

import hashlib
import json
import re

from . import archive, surface

FENCE = "-" * 80
REDACTED = "[withheld]"

# What a fitness figure looks like in prose. The signed 3-4 place decimal is the literal
# fitness/margin signature, kept separate from the catch-all so a leak is reported against
# the specific rule it breaks.
NUM_RE = re.compile(r"[+-]?\d*\.\d{3,}")
SEED_RE = re.compile(r"\b\d+\s*/\s*3\b")
ASSERT_RES = {
    "signed 3-4 place decimal (fitness/margin)": re.compile(r"[+-]\d*\.\d{3,4}\b"),
    "N/3 seed tally": SEED_RE,
    "any 3+ place decimal": NUM_RE,
}

# Relative-standing words the diet forbids even without a number attached — engine prose
# no longer contains them, but project-authored standing rules may.
STANDING_TERMS = [
    (re.compile(r"\bchampion-relative\b", re.I), "prior-genome-relative"),
    (re.compile(r"\bchampions?\b", re.I), "prior genome"),
    (re.compile(r"\bleaderboards?\b|\bboard[- ]tops?\b", re.I), "prior work"),
    (re.compile(r"\bfrontier\b", re.I), "prior work"),
    (re.compile(r"\b(?:beat|beats|beating)\s+the\s+best\b", re.I), "beat your parent"),
    # Prune status is relative standing too ("we pruned X" says X was judged redundant
    # against the pool). The engine never writes it into a brief; this catches
    # owner-authored notes/standing rules, the measured leak source. Accepts the false
    # positive on legitimate uses (decision-tree pruning) — the diet is conservative.
    (re.compile(r"\bprun(?:e|es|ed|ing)\b", re.I), "set aside"),
]


def fitness_forms(v):
    """Every spelling of an ALLOWED fitness the number regexes could match."""
    return {f"{v:+.4f}", f"{v:.4f}", f"{v:+.6f}", f"{v:.6f}", repr(v), str(v),
            f"{abs(v):.4f}", f"{-v:+.4f}"}


def _redact_prose(text, allowed):
    def sub_num(m):
        return m.group(0) if m.group(0) in allowed else REDACTED
    text = NUM_RE.sub(sub_num, text)
    text = SEED_RE.sub(REDACTED, text)
    for pat, repl in STANDING_TERMS:
        text = pat.sub(repl, text)
    return text


def _axes_order(cfg):
    return list((cfg["surface"]["registry"]["axes"] or {}).keys())


def _recipe(genome, axes_order):
    chunks = (genome or {}).get("chunks") or {}
    parts = []
    for ax in axes_order:
        c = chunks.get(ax) or {}
        p = c.get("params") or {}
        parts.append(f"  {ax:19s} {c.get('impl', '·')}"
                     + (f"   params {json.dumps(p, sort_keys=True)}" if p else ""))
    return "\n".join(parts)


def _impl_source(root, cfg, ax, impl):
    try:
        return surface.impl_path(root, cfg, ax, impl).read_text().rstrip()
    except surface.SurfaceError:
        return None


def build(root, cfg, *, operator="diff", parent_id=None, axis=None, cross_with=None,
          seed=0, baseline_id="gen0-baseline", baseline_fitness=None, notes=None):
    """-> (markdown, grants) where grants is the set of (axis, impl) sources the diet
    hands the inventor — the jail copies exactly these. Raises ValueError (and emits
    nothing) on any assertion failure. Registry mode only: the diet's grant unit is an
    impl source; markers inventors take the standard brief in an isolated worktree."""
    from . import brief as briefmod
    if cfg["surface"]["mode"] != "registry":
        raise ValueError("--diet needs a registry-mode project (the grant unit is an impl "
                         "source); markers inventors take the standard brief in an "
                         "isolated worktree")
    if not axis:
        raise ValueError("--diet needs --axis (the slot's assigned axis)")
    if not parent_id:
        raise ValueError("--diet needs --parent — the diet's whole content is the parent's "
                         "genome and fitness (a seed slot briefs against the baseline id)")
    axes_cfg = cfg["surface"]["registry"]["axes"] or {}
    if axis not in axes_cfg:
        raise ValueError(f"axis {axis!r} is not a config axis (have {sorted(axes_cfg)})")
    axes_order = _axes_order(cfg)
    sel_env = cfg["fitness"].get("selection_env")
    recs = archive.load(root)
    in_scope = archive.latest_valid(recs, sel_env)

    # Pruned parent/partner fails closed FIRST — before any other resolution — so a stale
    # slot assignment surfaces as this message, not as a downstream lookup error. The text
    # reaches the ORCHESTRATOR (stderr; no brief is emitted), so naming the status is not
    # an inventor leak. Distinct from retraction on purpose: the score is still true — the
    # candidate just left selection, and the remedy is resample-or-reinstate, not rescore.
    # Retraction OUTRANKS prune (a pruned-then-retracted id is not in in_scope): it falls
    # through to the validity error below — telling that owner to reinstate a prune would
    # prescribe a dead end, since the retraction still blocks.
    pruned = archive.pruned_ids(recs, sel_env)
    for role, rid in (("parent", parent_id), ("crossover partner", cross_with)):
        if rid and rid in pruned and rid in in_scope:
            raise ValueError(f"{role} {rid!r} is pruned ({pruned[rid]}) — its score stays "
                             "valid but the engine no longer offers it to selection. Resample "
                             "the slot; if this candidate specifically must be bred, reinstate "
                             "it first (`evolve prune --id ... --reinstate --reason ...`)")
    parent = in_scope.get(parent_id)
    if parent is None:
        raise ValueError(f"parent {parent_id!r} has no VALID record"
                         + (f" in {sel_env!r}" if sel_env else "")
                         + " — never scored there, or retracted. The engine does not sample "
                           "such parents; check the slot assignment")
    if not isinstance(parent.get("fitness"), (int, float)):
        raise ValueError(f"parent {parent_id!r} has no numeric fitness — the objective "
                         "('beat your parent') would be undefined")
    if not ((parent.get("genome") or {}).get("chunks")):
        raise ValueError(f"parent {parent_id!r}'s valid record carries no genome — the "
                         "diet's whole grant is the parent's genome")
    parent_fit = float(parent["fitness"])
    if baseline_fitness is None:
        b = in_scope.get(baseline_id) or {}
        if not isinstance(b.get("fitness"), (int, float)):
            raise ValueError(f"no VALID record for baseline id {baseline_id!r}"
                             + (f" in {sel_env!r}" if sel_env else "")
                             + " — pass --baseline-fitness")
        baseline_fitness = float(b["fitness"])

    parent_impls = {ax: (c or {}).get("impl") for ax, c in
                    ((parent.get("genome") or {}).get("chunks") or {}).items()}
    grants = {(ax, (spec or {}).get("baseline")) for ax, spec in
              (cfg["surface"]["registry"]["axes"] or {}).items()}
    grants |= {(ax, impl) for ax, impl in parent_impls.items() if impl}

    segs = [{"kind": "prose", "text": f"TASK: {cfg['task']}"},
            {"kind": "prose", "text": briefmod.OPERATOR_INSTRUCTIONS.get(
                operator, briefmod.OPERATOR_INSTRUCTIONS["diff"])}]

    # Contract: prose + the authoritative baseline source. Diet-safe by construction, but
    # the code segment is still byte-verified below like every other one.
    base_impl = cfg["surface"]["registry"]["axes"][axis]["baseline"]
    base_src = _impl_source(root, cfg, axis, base_impl)
    if base_src is None:
        raise ValueError(f"axis {axis!r} baseline {base_impl!r} has no source on disk")
    spec = cfg["surface"]["registry"]["axes"][axis]
    segs += [{"kind": "prose", "text":
              f"THE CONTRACT — axis {axis!r}: {spec.get('contract', '(see the baseline below)')}\n"
              "The reference baseline below is authoritative — match its interface exactly, "
              f"keep your module self-contained:\n{FENCE}"},
             {"kind": "code", "tight": True, "axis": axis, "impl": base_impl, "text": base_src},
             {"kind": "prose", "tight": True, "text": FENCE}]

    # Parent: id + fitness + FULL genome recipe + its impl on the slot's axis + feedback.
    segs += [{"kind": "prose", "text":
              "PARENT — you are mutating this candidate.\n"
              f"  id                {parent_id}\n"
              f"  its fitness       {parent_fit:+.4f}   (this is YOUR target)\n"
              "  its full genome (the diet grants you your parent's genome):"},
             {"kind": "genome", "tight": True, "source_id": parent_id,
              "text": _recipe(parent.get("genome"), axes_order)}]
    p_impl = parent_impls.get(axis)
    p_src = _impl_source(root, cfg, axis, p_impl) if p_impl else None
    if p_src is not None:
        segs += [{"kind": "prose", "text":
                  f"YOUR PARENT'S CURRENT {axis} IMPL — {p_impl} (this is the code you are "
                  f"mutating):\n{FENCE}"},
                 {"kind": "code", "tight": True, "axis": axis, "impl": p_impl, "text": p_src},
                 {"kind": "prose", "tight": True, "text": FENCE}]
    if parent.get("text_feedback"):
        segs.append({"kind": "prose",
                     "text": f"PARENT'S EVAL FEEDBACK: {parent['text_feedback'][:600]}"})

    # Crossover partner: a second design, which the diet grants by MECHANISM only —
    # recipe kept, identity and fitness withheld.
    if operator == "cross" and cross_with:
        partner = in_scope.get(cross_with)
        if partner is None:
            raise ValueError(f"crossover partner {cross_with!r} has no VALID record in "
                             "scope — an owner-voided or unverifiable design must not be "
                             "offered as a genome to combine with")
        if not ((partner.get("genome") or {}).get("chunks")):
            raise ValueError(f"crossover partner {cross_with!r}'s valid record carries no genome")
        segs += [{"kind": "prose", "text":
                  "CROSSOVER PARTNER GENOME — combine your parent with this design. Its "
                  "identity and its fitness are withheld by the information diet; judge "
                  "it as a mechanism."},
                 {"kind": "genome", "tight": True, "source_id": cross_with,
                  "text": _recipe(partner.get("genome"), axes_order)}]
        grants |= {(ax, (c or {}).get("impl"))
                   for ax, c in ((partner.get("genome") or {}).get("chunks") or {}).items()
                   if (c or {}).get("impl")}

    # Prior mechanisms: impl SOURCE only. No fitness, no outcome label, and the engine's
    # worst→best order is destroyed with a slot-keyed shuffle (reproducible, carries no
    # standing). Retired impls are never offered.
    retired = surface.retired_impls(root)
    insp = archive.sample_inspirations(root, parent_id,
                                       n_top=cfg["search"]["inspirations"]["top_k"],
                                       n_archive=cfg["search"]["inspirations"]["archive"],
                                       seed=seed, selection_env=sel_env)
    picked, seen = [], set()
    for r in insp:
        if retired and surface.genome_selects_retired(r.get("genome") or {}, retired):
            continue
        impl = ((r.get("genome") or {}).get("chunks") or {}).get(axis, {}).get("impl")
        if not impl or impl in seen or parent_impls.get(axis) == impl:
            continue
        src = _impl_source(root, cfg, axis, impl)
        if src is None:
            continue   # source gone (retired) — grant what exists
        seen.add(impl)
        picked.append((axis, impl, src))
    picked.sort(key=lambda t: hashlib.blake2b(
        f"{parent_id}|{axis}|{t[0]}/{t[1]}".encode(), digest_size=8).hexdigest())
    if picked:
        segs.append({"kind": "prose", "text":
                     "PRIOR MECHANISMS — the engine sampled these as relevant to your "
                     "slot, shown as SOURCE. No outcome is attached to any of them, and "
                     "no ordering is implied. There is no instruction to beat any of "
                     "them; your objective is your own parent."})
        for ax, impl, src in picked:
            segs.append({"kind": "prose", "text": f"--- {impl} (axis {ax})"})
            segs.append({"kind": "code", "tight": True, "axis": ax, "impl": impl, "text": src})
            grants.add((ax, impl))

    segs.append({"kind": "prose", "text":
                 "STANDING RULES (every inventor, every round):\n- "
                 + "\n- ".join(cfg["search"].get("standing_rules")
                               or briefmod.DEFAULT_STANDING_RULES)})
    if notes:
        segs.append({"kind": "prose", "text": notes})
    segs.append({"kind": "prose", "text":
                 "YOUR OBJECTIVE\n"
                 f"Beat your parent's fitness of {parent_fit:+.4f} "
                 f"({parent_id}, {parent.get('mode', '?')} budget"
                 + (f", {sel_env}" if sel_env else "") + f", {parent.get('split', '?')} split).\n"
                 f"The unmodified baseline scores {baseline_fitness:+.4f} in the same "
                 "measurement — a floor, not a target.\n"
                 "No other candidate's score is shown to you, by design: parents are "
                 "sampled by fitness, so beating YOUR parent is the whole bar. Report "
                 "which axes you changed (axes_changed) with your proposal."})

    # Redact, THEN assert. The assertion is the check on the redaction.
    allowed = fitness_forms(parent_fit) | fitness_forms(baseline_fitness)
    allowed |= {"1.349"}   # the Gaussian IQR→sd constant in auto-λ prose — not a score
    for seg in segs:
        if seg["kind"] == "prose":
            seg["text"] = _redact_prose(seg["text"], allowed)

    leaks = _assert_clean(root, cfg, segs, allowed,
                          allowed_ids={parent_id, baseline_id},
                          bad_ids={r["id"] for r in recs if r.get("id")} - {parent_id, baseline_id},
                          genomes=in_scope)
    if leaks:
        raise ValueError("the diet assertion tripped; NOTHING was emitted:\n  ! "
                         + "\n  ! ".join(leaks))

    # Genome params are number-exempt by design (a parent's beta3=0.9999 is configuration,
    # not a score) — but that also makes them a channel: an inventor legitimately told its
    # parent's fitness can write it into params, and a later slot's brief then carries it.
    # Warn on fitness-shaped values rather than refuse; the owner judges.
    warnings = []
    for seg in segs:
        if seg["kind"] == "genome":
            hits = [x for x in NUM_RE.findall(seg["text"]) if x not in allowed]
            if hits:
                warnings.append(f"genome params for {seg.get('source_id')!r} carry "
                                f"fitness-shaped value(s) {hits[:3]} — params are emitted "
                                "verbatim (configuration, not scores); verify they are not "
                                "smuggled outcomes")

    md = segs[0]["text"]
    for seg in segs[1:]:
        md += ("\n" if seg.get("tight") else "\n\n") + seg["text"]
    return md.rstrip() + "\n", grants, warnings


def _assert_clean(root, cfg, segs, allowed, allowed_ids, bad_ids, genomes):
    """Violations -> leak list. Code segments are exempt from the number scan ONLY when
    byte-identical to their registry file (an impl's own hyperparameters — 0.999, 1e-8 —
    are not scores and must not be redacted); genome segments only when they regenerate
    exactly from the archive record they claim to describe. Everything else is scanned."""
    axes_order = _axes_order(cfg)
    leaks = []
    for seg in segs:
        text = seg["text"]
        if seg["kind"] == "genome":
            src = genomes.get(seg.get("source_id", ""))
            if src is None or _recipe(src.get("genome"), axes_order) != text:
                leaks.append(f"genome segment for {seg.get('source_id')!r} is not the "
                             "archive record's recipe — it cannot be exempted")
            continue
        if seg["kind"] == "code":
            src = _impl_source(root, cfg, seg.get("axis"), seg.get("impl"))
            if src is None or src != text.rstrip():
                leaks.append(f"code segment for {seg.get('axis')}/{seg.get('impl')} does "
                             "not match the chunk registry file — it cannot be exempted "
                             "from the number scan")
            continue
        for name, pat in ASSERT_RES.items():
            for m in pat.finditer(text):
                if m.group(0) not in allowed:
                    leaks.append(f"{name}: {m.group(0)!r} in a kept prose segment "
                                 f"(...{text[max(0, m.start() - 60):m.end() + 40]}...)")
    whole = "\n".join(s["text"] for s in segs)
    # ONE longest-first pass over allowed ∪ bad ids: archive ids nest as substrings in BOTH
    # directions. Masking allowed ids first would destroy a foreign DESCENDANT id that
    # embeds the parent (the field convention names children by extending the parent id, so
    # descendants are exactly the foreign ids likeliest to sit in parent-adjacent prose);
    # scanning bad ids first without masking would false-positive on a foreign id embedded
    # inside the parent's own mentions. Longest-first over the union does both correctly.
    masked = whole
    for cid in sorted({i for i in (set(allowed_ids) | set(bad_ids)) if i},
                      key=len, reverse=True):
        if cid not in masked:
            continue
        if cid in bad_ids:
            where = next((f" (segment starting {s['text'][:48]!r})" for s in segs
                          if cid in s["text"]), "")
            leaks.append(f"archived candidate id {cid!r} (not the parent) appears in the "
                         f"output{where}")
        masked = masked.replace(cid, "\x00ID\x00")
    return leaks
