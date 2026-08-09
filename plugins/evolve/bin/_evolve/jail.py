"""Build one inventor's JAIL: an isolated workspace holding exactly what the diet grants.

WHY A JAIL AND NOT A FILTER. The diet brief (diet.py) strips other candidates' fitness and
every foreign id — and none of that matters if the inventor runs where it can read the
archive. Worktree isolation covers markers mode only; registry inventors otherwise run as
subagents in the project cwd, one read away from evolve/archive/genomes.jsonl, evolve.json,
and the project's auto-loaded agent context files (measured in the field: 58 files carried
a fitness figure or candidate id, and the auto-loaded context alone named 12 candidates
before the inventor did anything). The brief is a filter on one channel of many; the jail
makes it the only channel. The two are only sufficient together.

WHAT GOES IN (allowlist — a denylist grows with every doc the project writes):
  BRIEF.md                     the diet brief; its assertions gate the build, so a
                               leaking brief builds no jail
  <surface.registry.inventor_files>
                               the project-declared harness closure — what an impl may
                               import. The engine cannot derive a language-specific import
                               closure; the project measures and declares it
  evolve/chunks/<axis>/        exactly the diet's grants: every axis baseline, the
                               parent's impls, the partner's (cross), the inspirations
  PROPOSAL/                    where the inventor writes its impl and hypothesis
  JAIL_README.md               layout and the output contract (jail_notes ride inside
                               BRIEF.md, through the diet's redact-then-assert gate)

DELIBERATELY ABSENT: the archive, evolve.json, docs, agent context files, anything else.

Jails live OUTSIDE the repo (default ~/.cache/evolve-jails/<project>/) so one can never be
committed, and survive /tmp cleanup — a reboot has destroyed in-flight field state before.
Builds stage into <jail>.building and swap only after every gate passes: a --force rebuild
that then fails must not destroy an inventor's in-flight PROPOSAL.
"""

import os
import pathlib
import re
import shutil

from . import archive, diet, surface
from .config import state_dir

SIGNED_FIG = re.compile(r"[+-]\d*\.\d{3,4}\b")

README = """\
# Inventor workspace

You are one inventor in an evolutionary search. Everything you may know about the search
is in BRIEF.md — read it first. This directory is your whole world: do not read, list, or
reach for files outside it (any sanctioned exceptions are named under NOTES below).

## Layout

    BRIEF.md                  your slot: contract, parent, prior mechanisms, objective
    <project files>           the harness modules an impl may import
    evolve/chunks/<axis>/     impl sources the brief grants you
    PROPOSAL/                 write your output HERE

## Output contract

    PROPOSAL/{axis}__<your_impl_name>.py    one complete impl module honoring the axis
                                            contract in BRIEF.md (match the baseline's
                                            interface exactly; self-contained)
    PROPOSAL/hypothesis.txt                 one short paragraph: what you expect this
                                            change to do to the objective, and why

Write NO docstrings and NO comments in your impl — your intent belongs in hypothesis.txt,
which is recorded with your mutation. Name things so the code explains itself.
"""


def default_root(root):
    return pathlib.Path.home() / ".cache" / "evolve-jails" / archive._safe_id(str(root))


def build(root, cfg, *, parent_id, axis, round_tag, slot, operator="diff", cross_with=None,
          seed=0, jail_root=None, force=False, baseline_id="gen0-baseline",
          baseline_fitness=None):
    """Build the jail; returns (path, warnings). Raises ValueError on any gate failure —
    a failed build leaves no partial jail and never touches an existing one."""
    if cfg["surface"]["mode"] != "registry":
        raise ValueError("jails are for registry-mode inventors; markers mode already "
                         "isolates per-inventor via worktrees (round skill)")
    inventor_files = (cfg["surface"]["registry"] or {}).get("inventor_files")
    if not inventor_files:
        raise ValueError("surface.registry.inventor_files is not set — declare the harness "
                         "files an impl may import (the transitive import closure, measured "
                         "from what your registry impls actually import); the engine cannot "
                         "derive a language-specific closure itself")

    base = (pathlib.Path(jail_root).resolve() if jail_root else default_root(root))
    if base.is_relative_to(pathlib.Path(root).resolve()):
        raise ValueError(f"--jail-root {base} resolves INSIDE the repo — a jail must live "
                         "outside it (a committed jail publishes the brief and its parent "
                         "fitness)")
    jail = base / str(round_tag) / f"slot{int(slot):02d}-{axis}"
    if jail.exists() and not force:
        raise ValueError(f"{jail} exists — a jail may hold an inventor's in-flight work; "
                         "pass --force to rebuild it")
    # pid-suffixed staging: two builds racing on one slot must not rmtree each other's
    # half-built staging; orphans from killed builds are inert files in the cache dir
    staging = jail.with_name(jail.name + f".building-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)

    # 1. The diet brief gates the whole build: a leaking brief builds no jail. Project
    # notes ride INSIDE it — through the same redact-then-assert gate as every other prose
    # segment — because owner-authored free text is the measured leak source, and a notes
    # channel that bypassed the gate would hand every jail unscanned ids and figures.
    notes = (cfg["surface"]["registry"] or {}).get("jail_notes")
    text, grants, warnings = diet.build(root, cfg, operator=operator, parent_id=parent_id,
                                        axis=axis, cross_with=cross_with, seed=seed,
                                        baseline_id=baseline_id,
                                        baseline_fitness=baseline_fitness, notes=notes)

    staging.mkdir(parents=True)
    try:
        (staging / "BRIEF.md").write_text(text)
        copied = []
        for name in inventor_files:
            src = pathlib.Path(root) / name
            if not src.is_file():
                raise ValueError(f"inventor file {name!r} missing from the project — fix "
                                 "surface.registry.inventor_files")
            # Preserve the repo-relative path: flattening to basenames silently drops one
            # of two same-named files and breaks package imports inside the jail.
            dst = staging / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            copied.append(dst)
        for ax, impl in sorted(grants):
            try:
                src = surface.impl_path(root, cfg, ax, impl)
            except surface.SurfaceError:
                warnings.append(f"{ax}/{impl} has no source on disk (retired?); not copied")
                continue
            dst = staging / "evolve" / "chunks" / ax / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            copied.append(dst)
        (staging / "PROPOSAL").mkdir()
        readme = README.format(axis=axis)
        if notes:
            readme += ("\n## NOTES\n\nProject-specific notes (dataset roots, budgets, "
                       "testing) are at the end of BRIEF.md.\n")
        (staging / "JAIL_README.md").write_text(readme)
        copied.append(staging / "JAIL_README.md")   # belt: scan it like everything else

        # 2. Leak scan over everything copied (BRIEF.md carries its own assertions).
        # Allowed ids are masked longest-first before the scan — ids nest as substrings.
        recs = archive.load(root)
        allowed = {parent_id, baseline_id}
        bad_ids = {r["id"] for r in recs if r.get("id")} - allowed
        # ONE longest-first pass over allowed ∪ bad per file: ids nest as substrings in
        # both directions (see diet._assert_clean for the two failure modes).
        all_ids = sorted({i for i in (allowed | bad_ids) if i}, key=len, reverse=True)
        leaks = []
        for f in copied:
            text = f.read_text(errors="ignore")
            for cid in all_ids:
                if cid not in text:
                    continue
                if cid in bad_ids:
                    leaks.append(f"{f.relative_to(staging)}: candidate id {cid!r}")
                text = text.replace(cid, "\x00ID\x00")
            n = len(SIGNED_FIG.findall(text))
            if n:
                warnings.append(f"{f.relative_to(staging)}: {n} signed 3-4dp figure(s) — "
                                "verify they are thresholds, not scores")
        if leaks:
            raise ValueError(f"{len(leaks)} candidate-id leak(s) in copied sources — no "
                             "jail built:\n  ! " + "\n  ! ".join(leaks))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # 3. Swap only now — every gate passed.
    if jail.exists():
        shutil.rmtree(jail)
    staging.rename(jail)
    return jail, warnings
