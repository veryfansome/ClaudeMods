"""evolve init — deterministic scaffolding of the project-side state (scripts mutate,
skills converse: the /evolve:init skill interviews and generates the eval adapter; THIS
creates the fixed structure). Idempotent: existing files are never overwritten, re-running
after the config is filled in creates whatever the config newly implies (registry axis
dirs), and reports what it did."""

import json
import os
import pathlib

from . import config as cfgmod


def plugin_root():
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parent.parent.parent


def templates_dir():
    return plugin_root() / "templates"


def init(root, mode="markers"):
    root = pathlib.Path(root)
    sd = cfgmod.state_dir(root)
    actions = []

    def ensure_dir(p):
        if not p.exists():
            p.mkdir(parents=True)
            actions.append(f"created {p.relative_to(root)}/")

    def ensure_file(p, content):
        if p.exists():
            actions.append(f"kept existing {p.relative_to(root)}")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            actions.append(f"wrote {p.relative_to(root)}")

    ensure_dir(sd)
    ensure_dir(sd / "archive")

    tmpl = json.loads((templates_dir() / "evolve.json").read_text())
    tmpl["surface"]["mode"] = mode
    if mode == "markers":
        tmpl["surface"]["registry"] = None
    else:
        tmpl["surface"]["files"] = []
    ensure_file(sd / cfgmod.CONFIG_NAME, json.dumps(tmpl, indent=2) + "\n")

    manual = (templates_dir() / "EVOLVE.md").read_text().replace("{{MODE}}", mode)
    ensure_file(sd / "EVOLVE.md", manual)
    ensure_file(sd / "insights.md", "")
    # Per-dir gitignore: the caches are rebuildable and private metrics stay out of the
    # repo (they are the anti-overfit holdout; committing them widens the leak surface).
    ensure_file(sd / ".gitignore", ".cache/\narchive/private/\n*.json.bak.*\n*.json.tmp\n")

    # If the config already declares registry axes (a re-run after the skill filled it
    # in), create the axis directories so impls have a home.
    try:
        cfg = json.loads((sd / cfgmod.CONFIG_NAME).read_text())
        reg = (cfg.get("surface") or {}).get("registry") or {}
        if cfg.get("surface", {}).get("mode") == "registry" and reg.get("dir"):
            for axis in (reg.get("axes") or {}):
                ensure_dir(root / reg["dir"] / axis)
    except (json.JSONDecodeError, OSError):
        pass

    return {"state_dir": str(sd), "actions": actions,
            "next": "fill in evolve/evolve.json (task, surface, eval, protected), then run "
                    "`evolve doctor` until it passes — nothing evolves before the contract holds"}
