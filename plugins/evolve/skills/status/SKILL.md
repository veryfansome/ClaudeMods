---
name: status
description: Report the state of the project's evolutionary search — leaderboard, champion, budget spend, staleness, per-inventor/operator stats, and what to do next. Invoke when the user asks how the evolution is going, what the current champion is, or whether the search should continue.
---

# evolve:status — where the search stands

Run `evolve board` (add `--json` if you need to compute), read `evolve/insights.md`, and report:

1. **The board** — champion (id, fitness, mode) and top candidates, and whether the top scores are full-budget or proxy-only (proxy-only leaders are unconfirmed; say so). `board` reports ids, not code; to show the champion's actual change, run `evolve adopt --id <champion>` (registry: prints the genome; markers: applies the archived diff to the working tree for review).
2. **Budget** — generations and full evals spent vs configured caps, staleness (generations since the champion changed), and the noise floor. If `exhausted` is non-empty, the engine will refuse further sampling — present the options: raise the budget in `evolve/evolve.json`, accept the champion, or change the search's shape (new axes, different operators mix).
3. **Signal quality** — the archived guardrail-failure rate (records with `fitness: null`, shown as board's per-inventor/per-operator `failed` counts); a high rate usually means the brief's contract section or the smoke gate needs sharpening. Guard and dedup rejections are session-local Reflexion feedback and are NOT in the archive, so don't look for them here. Report per-inventor and per-operator stats as numbers, without editorializing. Surface any `board` warnings (multi-environment or multi-commit selection) prominently — those mean the leaderboard is mixing incomparable scores.
4. **Validation state** — has the current champion been scored on the `final` split? If promoted but unvalidated, recommend that single validation run before any external claim. If validated, report both numbers side by side. If `board` shows an **active prereg**, report its gates (the G1 fitness floor and the G2 objective) so the next promotion decision is anchored to them; if it shows **env offsets**, report whether environments are comparable or should be partitioned.
5. **Next step** — one concrete recommendation: another round, a recombination round, a noise re-measure (`evolve doctor --measure-noise` after any eval/harness change), or stop.

Keep it to the numbers the archive actually holds — no verdicts the records don't support.
