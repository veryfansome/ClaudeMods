"""Round state: the engine-written slot-assignment record.

A round's assignments (parent, axis, operator, inspiration seed per slot) are DISPATCH
EVIDENCE: the archive's `parent` field records what a candidate later CLAIMS to derive
from, and when an inventor deviates, the divergence between claim and assignment is the
only proof — so the assignment must outlive `sample`'s stdout.
"""

import json

from . import archive
from .config import state_dir


def rounds_dir(root):
    return state_dir(root) / "rounds"


def save_slots(root, seed, slots):
    """Persist a sample invocation's slot table BEFORE dispatch. Same seed + same
    assignments is idempotent; same seed + different assignments is refused — the existing
    file is evidence, not a cache to overwrite."""
    d = rounds_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{archive._safe_id(str(seed))}-slots.json"
    text = json.dumps({"seed": str(seed), "slots": slots}, indent=1) + "\n"
    if p.exists():
        if p.read_text() == text:
            return p
        try:
            old_n = len(json.loads(p.read_text()).get("slots", []))
        except ValueError:
            old_n = None
        why = (f"{old_n} slots there vs {len(slots)} requested — a different --k?"
               if old_n is not None and old_n != len(slots)
               else "the sampling inputs changed since they were written — the archive grew, "
                    "or the resolved search.lambda / noise floor moved")
        raise ValueError(f"slot assignments for seed {seed!r} already exist at {p} and differ "
                         f"({why}) — sample with a fresh seed, or remove the file deliberately "
                         "if that round never dispatched")
    p.write_text(text)
    return p
