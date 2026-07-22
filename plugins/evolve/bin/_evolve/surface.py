"""The evolvable surface: what a candidate is allowed to change, enforced mechanically.

Two modes, both language-agnostic:
  markers  — EVOLVE-BLOCK-START/END comment lines in declared files; only text strictly
             between a start/end pair may change. The candidate IS the git diff.
  registry — a genome (JSON dict selecting one impl + params per axis) over impl files
             in <registry.dir>/<axis>/; inventors ADD impl files, never modify existing
             ones (an archived genome must keep meaning what it meant when scored).

Agents violate boundaries just as API-generated patches do, so enforcement is post-hoc
and structural (ShinkaEvolve's marker validation, kept; its SEARCH/REPLACE patch
machinery, dropped — the agent's own edit tools subsume it).
"""

import fnmatch
import hashlib
import pathlib
import subprocess

from .config import CONFIG_NAME, STATE_DIR, state_dir

MARK_START = "EVOLVE-BLOCK-START"
MARK_END = "EVOLVE-BLOCK-END"


class SurfaceError(ValueError):
    pass


def _git(args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SurfaceError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout


# ---- markers ----------------------------------------------------------------------------

def parse_blocks(text, path="<text>"):
    """Return [(start_line, end_line)] pairs (0-based, marker lines themselves excluded
    from the mutable span). Raises on unpaired/nested markers."""
    blocks, open_at = [], None
    for i, line in enumerate(text.splitlines()):
        if MARK_START in line:
            if open_at is not None:
                raise SurfaceError(f"{path}:{i + 1}: nested {MARK_START} (previous opened at line {open_at + 1})")
            open_at = i
        elif MARK_END in line:
            if open_at is None:
                raise SurfaceError(f"{path}:{i + 1}: {MARK_END} without a matching start")
            blocks.append((open_at, i))
            open_at = None
    if open_at is not None:
        raise SurfaceError(f"{path}: unclosed {MARK_START} at line {open_at + 1}")
    if not blocks:
        raise SurfaceError(f"{path}: no {MARK_START}/{MARK_END} block found")
    return blocks


def split_regions(text, path="<text>"):
    """(frozen, mutable): frozen = everything outside blocks INCLUDING the marker lines
    (so moved/edited markers show up as frozen-region changes); mutable = the block bodies."""
    lines = text.splitlines()
    blocks = parse_blocks(text, path)
    frozen, mutable, cursor = [], [], 0
    for start, end in blocks:
        frozen.append("\n".join(lines[cursor:start + 1]))
        mutable.append("\n".join(lines[start + 1:end]))
        cursor = end
    frozen.append("\n".join(lines[cursor:]))
    return frozen, mutable


def mutable_text(text, path="<text>"):
    return "\n".join(split_regions(text, path)[1])


def mutable_sha(text, path="<text>"):
    norm = "\n".join(l.strip() for l in mutable_text(text, path).splitlines() if l.strip())
    return hashlib.sha256(norm.encode()).hexdigest()


def _matches_any(path, globs):
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.rstrip("/*") + "/*")
               for g in globs)


def changed_files(worktree, diff_filter=None):
    """Tracked paths changed vs HEAD in a candidate worktree. diff_filter narrows to git
    status letters (e.g. 'MD' = modified/deleted only, excluding staged additions)."""
    args = ["diff", "--name-only"]
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    out = _git([*args, "HEAD"], worktree)
    return [l for l in out.splitlines() if l.strip()]


def untracked_files(worktree):
    out = _git(["ls-files", "--others", "--exclude-standard"], worktree)
    return [l for l in out.splitlines() if l.strip()]


def guard_markers(worktree, cfg):
    """Validate a candidate worktree against the markers contract. Returns a report dict;
    report['ok'] is the verdict, report['violations'] the Reflexion feedback."""
    allowed = cfg["surface"]["files"]
    protected = cfg.get("protected", [])
    violations, warnings = [], []

    changed = changed_files(worktree)
    for path in changed:
        if _matches_any(path, protected):
            violations.append(f"protected path modified: {path}")
        elif path not in allowed:
            violations.append(f"file outside the evolvable surface modified: {path} (allowed: {allowed})")

    for path in [p for p in changed if p in allowed]:
        cand_file = pathlib.Path(worktree) / path
        if not cand_file.exists():
            violations.append(f"{path}: declared surface file was deleted")
            continue
        try:
            cand = cand_file.read_text()
        except (UnicodeDecodeError, OSError) as e:
            violations.append(f"{path}: surface file is unreadable as text ({e})")
            continue
        base = _git(["show", f"HEAD:{path}"], worktree)
        try:
            base_frozen, _ = split_regions(base, f"HEAD:{path}")
            cand_frozen, _ = split_regions(cand, path)
        except SurfaceError as e:
            violations.append(str(e))
            continue
        if len(base_frozen) != len(cand_frozen):
            violations.append(f"{path}: number of EVOLVE blocks changed")
        elif base_frozen != cand_frozen:
            violations.append(f"{path}: code outside EVOLVE blocks (or a marker line) was changed")

    extra = untracked_files(worktree)
    if extra:
        # Untracked files are structurally excluded from the candidate (only the diff is
        # scored, in a pristine clone), so they are a warning: the inventor probably
        # expected them to count.
        warnings.append(f"untracked files ignored (not part of the candidate): {extra}")
    if not changed:
        violations.append("no tracked changes vs HEAD — empty candidate")
    return {"ok": not violations, "violations": violations, "warnings": warnings, "changed": changed}


def extract_patch(worktree):
    return _git(["diff", "--binary", "HEAD"], worktree)


def candidate_mutable_text(worktree, cfg):
    parts = []
    for path in cfg["surface"]["files"]:
        p = pathlib.Path(worktree) / path
        if p.exists():
            parts.append(mutable_text(p.read_text(), path))
    return "\n".join(parts)


# ---- registry ---------------------------------------------------------------------------

def registry_dir(root, cfg):
    return pathlib.Path(root) / cfg["surface"]["registry"]["dir"]


def _visible_impl(p):
    return p.is_file() and not p.name.startswith((".", "_")) and p.stem != "__init__"


def list_impls(root, cfg, axis):
    d = registry_dir(root, cfg) / axis
    if not d.is_dir():
        return []
    return sorted({p.stem for p in d.iterdir() if _visible_impl(p)})   # dedupe same-stem files


def impl_path(root, cfg, axis, impl):
    d = registry_dir(root, cfg) / axis
    hits = sorted((p for p in d.iterdir() if _visible_impl(p) and p.stem == impl),
                  key=lambda p: p.name) if d.is_dir() else []
    if not hits:
        raise SurfaceError(f"axis {axis!r} has no impl {impl!r} (have {list_impls(root, cfg, axis)})")
    if len(hits) > 1:
        raise SurfaceError(f"axis {axis!r} impl {impl!r} is ambiguous — multiple files share the "
                           f"stem: {[p.name for p in hits]}; keep one file per impl")
    return hits[0]


def impl_shas(genome, root, cfg):
    """Content hash of every impl a genome references — the registry analog of markers'
    mutable_sha, so a re-scored id can be checked against the code it was scored with."""
    out = {}
    for axis, spec in sorted(genome.get("chunks", {}).items()):
        text = impl_path(root, cfg, axis, spec["impl"]).read_text()
        norm = "\n".join(l.rstrip() for l in text.splitlines() if l.strip())
        out[axis] = hashlib.sha256(norm.encode()).hexdigest()
    return out


def baseline_genome(cfg):
    reg = cfg["surface"]["registry"]
    if reg.get("baseline_genome"):
        return reg["baseline_genome"]
    return {"chunks": {axis: {"impl": spec["baseline"], "params": {}}
                       for axis, spec in reg["axes"].items()}}


def validate_genome(genome, root, cfg):
    """Cheap structural check before spending an eval on a genome (jepa's validate())."""
    reg = cfg["surface"]["registry"]
    chunks = genome.get("chunks")
    if not isinstance(chunks, dict) or not chunks:
        raise SurfaceError("genome must carry a non-empty 'chunks' dict")
    for axis in reg.get("required", []):
        if axis not in chunks:
            raise SurfaceError(f"genome missing required axis {axis!r}")
    for axis, spec in chunks.items():
        if axis not in reg["axes"]:
            raise SurfaceError(f"genome names unknown axis {axis!r} (config axes: {sorted(reg['axes'])})")
        if not isinstance(spec, dict) or not spec.get("impl"):
            raise SurfaceError(f"genome axis {axis!r} needs an 'impl' name")
        if spec["impl"] not in list_impls(root, cfg, axis):
            raise SurfaceError(f"unknown {axis} impl {spec['impl']!r} (have {list_impls(root, cfg, axis)})")
        if "params" in spec and not isinstance(spec["params"], dict):
            raise SurfaceError(f"genome axis {axis!r} params must be a dict")
    return True


def guard_registry(genome, root, cfg):
    """Registry candidates change nothing outside the registry dir, and never rewrite an
    existing tracked impl — impls are append-only so archived genomes stay reproducible."""
    violations, warnings = [], []
    try:
        validate_genome(genome, root, cfg)
    except SurfaceError as e:
        violations.append(str(e))
    reg_rel = cfg["surface"]["registry"]["dir"].rstrip("/")
    # Paths the ENGINE itself legitimately writes as tracked state (the archive it self-appends
    # every score; the config `doctor --measure-noise/--measure-env-offset` rewrites out of band).
    # These show as tracked-dirty through no fault of the candidate, so exempt exactly these two
    # from the protected-modified check — NOT the whole state dir, so any other protected path
    # (even one placed under evolve/) still gets guarded. A candidate cannot reach either at
    # score time (impls go to the registry dir; eval runs in an isolated export); the config is
    # additionally guarded against agent Write/Edit by the protect-paths hook.
    archive_rel = f"{STATE_DIR}/archive"
    config_rel = f"{STATE_DIR}/{CONFIG_NAME}"
    protected = cfg.get("protected", [])
    for path in changed_files(root, diff_filter="MD"):  # staged additions are legitimate
        if path.startswith(reg_rel + "/"):
            violations.append(f"existing registry impl modified/deleted: {path} — impls are "
                              "append-only (archived genomes must stay reproducible); "
                              "add a new impl file instead")
        elif path == config_rel or path == archive_rel or path.startswith(archive_rel + "/"):
            continue
        elif _matches_any(path, protected):
            violations.append(f"protected path modified: {path}")
        else:
            warnings.append(f"tracked change outside the registry ignored by scoring: {path}")
    return {"ok": not violations, "violations": violations, "warnings": warnings}


def genome_impl_files(genome, root, cfg):
    """Paths (repo-relative) of every impl the genome references — the file set copied
    into the pristine clone when an impl is not yet committed."""
    out = []
    for axis, spec in genome.get("chunks", {}).items():
        out.append(str(impl_path(root, cfg, axis, spec["impl"]).relative_to(root)))
    return out


def genome_recipe(genome):
    return ", ".join(f"{a}={s.get('impl')}" for a, s in sorted(genome.get("chunks", {}).items()))
