"""Shared fixtures: tiny throwaway git projects with a fast, deterministic eval.

The markers toy evolves `factor()` in src/algo.py; the registry toy evolves an
'objective' axis. Both evals finish in ~50ms, so the whole suite exercises the real
pipeline (worktrees, pristine clones, subprocess evals) without being slow.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "bin"))

from _evolve import config as cfgmod  # noqa: E402


def git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout


ALGO = '''def factor():
    # EVOLVE-BLOCK-START
    return 1.0
    # EVOLVE-BLOCK-END
'''

MARKERS_EVAL = '''import json, pathlib, sys
sys.path.insert(0, "src")
import algo
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
v = float(algo.factor())
(out / "metrics.json").write_text(json.dumps({
    "combined_score": v,
    "public": {"factor": v},
    "private": {"per_case": [v, v]},
    "text_feedback": f"factor={v}",
}))
'''

REGISTRY_EVAL = '''import json, pathlib, sys
genome = json.loads(pathlib.Path(sys.argv[2]).read_text())
impl = genome["chunks"]["objective"]["impl"]
ns = {}
exec((pathlib.Path("evolve/chunks/objective") / (impl + ".py")).read_text(), ns)
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
(out / "metrics.json").write_text(json.dumps({"combined_score": float(ns["value"]())}))
'''

BASELINE_IMPL = '''"""Contract: expose value() -> float. Higher is better."""
def value():
    return 1.0
'''


def _init_repo(root):
    git(["init", "-q"], root)
    git(["config", "user.email", "test@example.invalid"], root)
    git(["config", "user.name", "evolve-tests"], root)


def _commit_all(root, msg="setup"):
    git(["add", "-A"], root)
    git(["commit", "-q", "-m", msg], root)


def markers_config():
    return {
        "schema": 1,
        "task": "maximize factor() without breaking the module",
        "surface": {"mode": "markers", "files": ["src/algo.py"], "registry": None},
        "protected": ["evolve/evolve.json", "evolve/archive/**", "eval.py"],
        "eval": {
            "setup": None,
            "smoke": "python3 -c \"import sys; sys.path.insert(0,'src'); import algo\"",
            "guardrails": [],
            "proxy": {"cmd": "python3 eval.py {results_dir} {seed} {split}", "seeds": [0], "timeout_s": 60},
            "full": {"cmd": "python3 eval.py {results_dir} {seed} {split}", "seeds": [0, 1], "timeout_s": 60},
            "splits": ["inner", "final"],
        },
        "fitness": {"noise_floor": 0.0, "noise_runs": 3, "noise_meta": None, "selection_env": None},
        "search": {"lambda": 10.0, "inspirations": {"archive": 2, "top_k": 2},
                   "op_probs": {"diff": 0.5, "rewrite": 0.4, "cross": 0.1},
                   "dedup_similarity": 0.95, "insights_interval": 8, "standing_rules": []},
        "budget": {"max_generations": 40, "max_full_evals": 15, "stop_after_stale_rounds": 6},
    }


def registry_config():
    cfg = markers_config()
    cfg["surface"] = {"mode": "registry", "files": [], "registry": {
        "dir": "evolve/chunks",
        "axes": {"objective": {"baseline": "baseline", "contract": "value() -> float, higher better"}},
        "required": ["objective"],
        "baseline_genome": None,
    }}
    cfg["eval"]["smoke"] = None
    for tier in ("proxy", "full"):
        cfg["eval"][tier]["cmd"] = "python3 eval.py {results_dir} {genome} {seed}"
    return cfg


def make_markers_project(tmp=None):
    root = pathlib.Path(tmp or tempfile.mkdtemp(prefix="evolve-toy-"))
    _init_repo(root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "algo.py").write_text(ALGO)
    (root / "eval.py").write_text(MARKERS_EVAL)
    sd = root / "evolve"
    sd.mkdir(exist_ok=True)
    (sd / "evolve.json").write_text(json.dumps(markers_config(), indent=2))
    (sd / ".gitignore").write_text(".cache/\narchive/private/\n*.json.bak.*\n*.json.tmp\n")
    _commit_all(root)
    return root


def make_registry_project(tmp=None):
    root = pathlib.Path(tmp or tempfile.mkdtemp(prefix="evolve-toy-reg-"))
    _init_repo(root)
    (root / "eval.py").write_text(REGISTRY_EVAL)
    impl_dir = root / "evolve" / "chunks" / "objective"
    impl_dir.mkdir(parents=True)
    (impl_dir / "baseline.py").write_text(BASELINE_IMPL)
    (root / "evolve" / "evolve.json").write_text(json.dumps(registry_config(), indent=2))
    (root / "evolve" / ".gitignore").write_text(".cache/\narchive/private/\n*.json.bak.*\n*.json.tmp\n")
    _commit_all(root)
    return root


def make_worktree(root, name="cand"):
    wt = pathlib.Path(tempfile.mkdtemp(prefix=f"evolve-wt-{name}-")) / "wt"
    git(["worktree", "add", "--detach", "-q", str(wt), "HEAD"], root)
    return wt


def drop_worktree(root, wt):
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=root, capture_output=True)
    shutil.rmtree(wt.parent, ignore_errors=True)


def set_factor(tree, value):
    p = pathlib.Path(tree) / "src" / "algo.py"
    lines = p.read_text().splitlines()
    for i, l in enumerate(lines):
        if l.strip().startswith("return"):
            lines[i] = f"    return {value}"
            break
    p.write_text("\n".join(lines) + "\n")


def load_cfg(root):
    return cfgmod.load(root)


def cleanup(root):
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)
    shutil.rmtree(root, ignore_errors=True)
