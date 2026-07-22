"""Engine package for the evolve plugin — deterministic search control, no LLM calls.

The division of labor (the papers' central lesson, kept structural): the LLM proposes,
these modules decide. Everything selection-critical — parent sampling, the leaderboard,
the final-split firewall, surface enforcement, dedup, budget accounting — is deterministic
code here; skills orchestrate inventors and call `evolve` verbs, never re-implement them.
"""

ENGINE_VERSION = "0.1.0"
RECORD_SCHEMA = 1
CONFIG_SCHEMA = 1
