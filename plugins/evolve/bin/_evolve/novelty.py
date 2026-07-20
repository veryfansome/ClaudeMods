"""Novelty gate: reject near-duplicates BEFORE spending an evaluation.

The paper's embedding gate was ablated as "substantial" while the LLM-as-judge added
only "marginal" gains, so this ships the zero-dependency approximation: a normalized
difflib ratio over the mutable text vs. every archived candidate. A rejection returns
the rival's id so the skill can re-brief the inventor with "too similar to X, differ
mechanistically" (the Reflexion loop). Projects wanting true embeddings can raise the
threshold to 1.0 here and gate inside their own eval instead.
"""

import difflib


def normalize(text):
    return "\n".join(l.strip() for l in (text or "").splitlines() if l.strip())


def similarity(a, b):
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def nearest(candidate_text, archived):
    """archived: iterable of (id, text). Returns (max_ratio, closest_id)."""
    best_ratio, best_id = 0.0, None
    for rid, text in archived:
        r = similarity(candidate_text, text)
        if r > best_ratio:
            best_ratio, best_id = r, rid
    return best_ratio, best_id
