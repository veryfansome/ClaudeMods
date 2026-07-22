"""Run a candidate's evaluation in an ISOLATED EXPORT of HEAD — never a git worktree,
never the inventor's own worktree.

The clone is a `.git`-less export (`git archive HEAD | tar -x`) plus exactly the candidate
(a patch, or genome impl files). Two properties matter:
  - candidate INPUTS are pinned: anything an inventor planted outside the candidate —
    untracked helpers, a shadowing conftest, a patched dependency — is simply absent.
  - candidate EXECUTION cannot reach back to the real repo: with no `.git` link there is
    no `git rev-parse --git-common-dir` path to discover the project root, so evolved code
    running during eval cannot read the private holdout or rewrite the archive.
This is input/discovery isolation, NOT an OS sandbox: eval still runs candidate code with
the invoking user's privileges (network, $HOME, absolute paths). Run evolution in a trusted
environment, and treat eval-produced `text_feedback`/`public` as candidate-influenced.

Eval contract (ShinkaEvolve's, ported verbatim): the configured command runs with
{results_dir} {seed} {split} {mode} {genome} placeholders and reports metrics as
{results_dir}/metrics.json, or as the last JSON object on stdout. Keys: combined_score
(float, maximized — required), optional public / private / text_feedback / correct.
Any crash, timeout, NaN, missing score, or malformed metrics becomes a recorded failure
with a reason — never an engine crash. Infrastructure failures (export, setup) raise
InfraError and are NOT archived as candidate failures.
"""

import contextlib
import json
import math
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import uuid


class EvalFailure(Exception):
    """A candidate-attributable failure: recorded with fitness null + this reason."""


class InfraError(Exception):
    """An environment/harness failure (export, dependency setup): NOT the candidate's
    fault, so it must not be archived as a failed candidate."""


def _run(cmd, cwd, timeout):
    """Run under a fresh process group so a timeout kills the whole tree, not just the shell."""
    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as e:
        raise EvalFailure(f"could not launch eval: {e}")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise EvalFailure(f"timeout after {timeout}s: {cmd}")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _tail(text, n=400):
    text = (text or "").strip()
    return text[-n:] if len(text) > n else text


def last_json_object(text):
    """The last top-level JSON object in text (logs may surround it), at ANY nesting depth.
    Scans with the real JSON parser from each '{' rather than a fixed-depth regex, which would
    misparse deeply-nested public metrics."""
    text = text or ""
    dec = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                found = obj
                nxt = text.find("{", end)
            else:
                nxt = text.find("{", i + 1)
        except json.JSONDecodeError:
            nxt = text.find("{", i + 1)
        i = nxt
    return found


@contextlib.contextmanager
def git_worktree(root):
    """A throwaway git worktree (HEAD, detached) for OPERATIONS THAT NEED GIT but never run
    candidate code — extracting a --patch's mutable region and guarding it against HEAD. Eval
    never runs here (that is pristine_clone's git-less export), so the .git link is harmless."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="evolve-wt-"))
    target = tmp / "wt"
    r = subprocess.run(["git", "worktree", "add", "--detach", str(target), "HEAD"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise InfraError(f"could not create git worktree: {r.stderr.strip()}")
    try:
        yield target
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(target)],
                       cwd=root, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def pristine_clone(root):
    """A `.git`-less export of HEAD, cleaned up on exit. No worktree registration (nothing
    to leak or prune) and no link back to the real repo."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="evolve-clone-"))
    target = tmp / "clone"
    target.mkdir()
    try:
        # Stream git archive | tar so neither the archive nor the extraction is materialized
        # in memory (a multi-GB tracked tree would otherwise OOM). git archive omits submodule
        # content and export-ignore paths — doctor warns when .gitmodules is present.
        gp = subprocess.Popen(["git", "archive", "--format=tar", "HEAD"], cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        tp = subprocess.Popen(["tar", "-x", "-C", str(target)], stdin=gp.stdout,
                             stderr=subprocess.PIPE)
        gp.stdout.close()   # let git see tar's EOF / SIGPIPE
        tar_err = tp.communicate()[1]
        git_err = gp.stderr.read(); gp.stderr.close(); gp.wait()
        if gp.returncode != 0:
            raise InfraError(f"git archive HEAD failed: {git_err.decode('utf-8', 'replace').strip()}")
        if tp.returncode != 0:
            raise InfraError(f"could not unpack HEAD export: {tar_err.decode('utf-8', 'replace').strip()}")
        yield target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def apply_patch(clone, patch_text):
    if not patch_text.strip():
        return  # empty candidate = the seed program itself
    pf = clone / ".evolve-candidate.patch"
    pf.write_text(patch_text)
    # git apply works outside a repo (the export has no .git), applying to plain files.
    r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(pf.name)],
                       cwd=clone, capture_output=True, text=True)
    pf.unlink(missing_ok=True)
    if r.returncode != 0:
        raise EvalFailure(f"patch does not apply to HEAD: {_tail(r.stderr)}")


def copy_files(clone, root, rel_paths):
    """Bring not-yet-committed candidate files (registry impls) into the export. Each is
    copied under its own repo-relative path; the source is confined to the project tree."""
    root = pathlib.Path(root).resolve()
    for rel in rel_paths:
        src = (root / rel).resolve()
        if not str(src).startswith(str(root) + os.sep):
            raise EvalFailure(f"refusing to copy candidate file outside the project: {rel}")
        dst = clone / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def write_genome(clone, genome):
    d = clone / "evolve" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "candidate-genome.json"
    p.write_text(json.dumps(genome, indent=1) + "\n")
    return p


def _fill(cmd, **vars):
    out = cmd
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def run_setup(clone, cfg):
    setup = cfg["eval"].get("setup")
    if not setup:
        return
    r = _run(setup, clone, timeout=cfg["eval"].get("setup_timeout_s", 1800))
    if r.returncode != 0:
        # Setup (dependency install etc.) is infrastructure, not the candidate's code.
        raise InfraError(f"eval.setup failed: {_tail(r.stderr or r.stdout)}")


def run_gates(clone, cfg, genome_path=None, split=None, mode=None):
    """smoke + guardrails: free candidate-code checks before the paid eval."""
    ev = cfg["eval"]
    gates = ([("smoke", ev["smoke"])] if ev.get("smoke") else []) + \
            [("guardrail", g) for g in ev.get("guardrails", [])]
    for idx, (kind, cmd) in enumerate(gates):
        results_dir = clone / "evolve" / ".cache" / "gate" / f"{kind}-{idx}"
        results_dir.mkdir(parents=True, exist_ok=True)
        filled = _fill(cmd, genome=genome_path or "", split=split or "", mode=mode or "",
                       seed=0, results_dir=results_dir)
        r = _run(filled, clone, timeout=ev.get("gate_timeout_s", 600))
        if r.returncode != 0:
            raise EvalFailure(f"{kind} failed ({cmd}): {_tail(r.stderr or r.stdout)}")


def _parse_metrics(results_dir, stdout):
    mfile = pathlib.Path(results_dir) / "metrics.json"
    if mfile.exists():
        try:
            metrics = json.loads(mfile.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise EvalFailure(f"metrics.json is not valid JSON: {e}")
        cfile = pathlib.Path(results_dir) / "correct.json"
        if cfile.exists():
            try:
                c = json.loads(cfile.read_text())
            except (json.JSONDecodeError, OSError) as e:
                raise EvalFailure(f"correct.json is not valid JSON: {e}")
            if not c.get("correct", True):
                raise EvalFailure(f"eval reported correct=false: {c.get('error', '(no error given)')}")
        return metrics
    metrics = last_json_object(stdout)
    if metrics is None:
        raise EvalFailure("eval produced neither {results_dir}/metrics.json nor a JSON object on stdout")
    return metrics


def _score_of(metrics):
    if not metrics.get("correct", True):
        raise EvalFailure(f"eval reported correct=false: {metrics.get('error', '(no error given)')}")
    score = metrics.get("combined_score", metrics.get("fitness"))
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise EvalFailure(f"metrics carry no numeric combined_score (keys: {sorted(metrics)})")
    if math.isnan(score) or math.isinf(score):
        raise EvalFailure(f"combined_score is {score} — NaN/inf is a failed candidate")
    return float(score)


def evaluate_tier(clone, cfg, mode, split, genome_path=None):
    """Run the tier's command once per seed and aggregate: fitness = mean combined_score.
    Multi-seed averaging is the noise defense the paper itself uses (AIME ran 3x)."""
    tier = cfg["eval"][mode]
    per_seed, publics, feedback = [], {}, []
    for seed in tier.get("seeds", [0]):
        results_dir = clone / "evolve" / ".cache" / "results" / f"{split}-{seed}-{uuid.uuid4().hex[:6]}"
        results_dir.mkdir(parents=True, exist_ok=True)
        cmd = _fill(tier["cmd"], results_dir=results_dir, seed=seed, split=split, mode=mode,
                    genome=genome_path or "")
        r = _run(cmd, clone, timeout=tier.get("timeout_s", 3600))
        if r.returncode != 0:
            raise EvalFailure(f"eval exited {r.returncode} (seed {seed}): {_tail(r.stderr or r.stdout)}")
        metrics = _parse_metrics(results_dir, r.stdout)
        score = _score_of(metrics)
        entry = {"seed": seed, "combined_score": round(score, 6), "public": metrics.get("public") or {}}
        if metrics.get("private"):
            entry["_private"] = metrics["private"]  # stripped + written aside before archiving
        per_seed.append(entry)
        publics = metrics.get("public") or publics
        if metrics.get("text_feedback"):
            feedback.append(str(metrics["text_feedback"]))
    fitness = round(sum(p["combined_score"] for p in per_seed) / len(per_seed), 6)
    private = {}
    for p in per_seed:
        private.update(p.pop("_private", {}) or {})
    return {"fitness": fitness, "per_seed": per_seed, "public": publics,
            "text_feedback": "\n".join(feedback) or None, "private": private or None,
            "seeds": [p["seed"] for p in per_seed]}
