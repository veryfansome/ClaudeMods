# evolve/ — this project's evolutionary search (contract + round manual)

An LLM-driven evolutionary search over a marked part of this repo ({{MODE}} mode), run by the `evolve` plugin: inventor agents propose candidates, a deterministic engine decides what enters the archive and which parent seeds the next generation. This file is the project-local manual; the config in `evolve.json` is the machine-readable contract.

## The contract (fill these before anything evolves; `evolve doctor` enforces them)

- **task** — one line: what is being maximized, in plain words.
- **surface** — what a candidate may change. `markers`: only text between `EVOLVE-BLOCK-START`/`END` comment lines in the declared files. `registry`: a genome selecting one impl (+ params) per axis from `evolve/chunks/<axis>/`; impls are append-only (never edit an archived impl — genomes must keep meaning what they meant when scored).
- **eval** — commands that score a candidate. Each writes `{results_dir}/metrics.json` (or prints one JSON object) with `combined_score` (float, maximized — required), optional `public` (shown to inventors), `private` (recorded aside, never shown), `text_feedback` (fed to descendants), `correct` (false = failed candidate). Placeholders available: `{results_dir}` `{seed}` `{split}` `{mode}` `{genome}`.
- **proxy vs full** — proxy is the cheap screen every candidate gets; full (more seeds, bigger budget) is for candidates that beat the champion by more than the measured noise floor. Trust only full-budget results.
- **splits** — fitness is scored on `inner`. If you declare a `final` split (a held-out-of-held-out eval), the engine firewalls it from selection structurally: only a promoted champion is ever scored there, once, as validation — never to pick parents.
- **protected** — paths no candidate may touch: the config (`evolve/evolve.json`), the archive (`evolve/archive/**`), and every eval asset you author (adapter, harnesses, holdout data). Do NOT protect the registry dir or `insights.md` (the round writes those). Enforced three ways: a tool-layer hook (disarmed until the first `evolve seed`; new files under the registry dir are always allowed), the structural guard, and isolated-export scoring (candidates run in a `.git`-less export of HEAD plus exactly the candidate — planted files never reach the eval, and eval code can't reach back to this directory). Isolation is input/discovery-level, not an OS sandbox: the eval runs candidate code with your privileges, so run evolution in a trusted environment. The eval runs in a git-less export, so an adapter that shells out to `git` won't find a repo — read commit info before scoring, not inside the eval.
- **noise floor** — measured, not asserted: `evolve doctor --measure-noise` runs the baseline k times and writes the spread. Proxy deltas under the floor are noise; don't promote on them.

## How a round runs (the `/evolve:round` skill drives this)

1. Re-ground: `evolve board`, read `insights.md`.
2. `evolve sample --k N` — parents (fitness+novelty weighted, offspring-penalized), operators, inspirations per slot. Refuses when the budget is spent.
3. `evolve brief` per slot → dispatch inventor agents (heterogeneous, high effort).
4. `evolve guard` each proposal → fix-or-re-brief on violations (bounded retries).
5. `evolve score --mode proxy` each survivor — guard, dedup, pristine clone, smoke, eval, record. Failures are recorded with reasons; they are search signal.
6. Promote proxy winners beating the champion by > noise floor to `--mode full`; for noisy evals, rerun the champion back-to-back first (paired comparison).
7. Recombine winners (cross operator / stacked genome) and score the combination — epistasis is real in both directions; always test, never assume.
8. Every few rounds: distill neutral stats into `insights.md` (≤5 recommendations; record numbers, not verdicts).
9. Before any external claim: score the champion once on the `final` split.

## Culture

- **Record stats, not verdicts.** Negative results get archived with the same weight as wins.
- **The eval is sacred.** No candidate, ever, edits the metric, the splits, or this directory. Deliberate harness maintenance is a normal reviewed change, made outside a round (set `EVOLVE_ALLOW_PROTECTED=1` for the session doing it), followed by `evolve doctor` and a fresh `--measure-noise`.
- **Scores compare only within one environment.** Remote/offloaded results come in via `evolve ingest --env <tag>`. If the archive mixes environments (e.g. local proxy runs plus ingested remote scores), set `fitness.selection_env` to the one env selection should trust — records from other envs stay archived and reportable but are firewalled out of parent sampling, the leaderboard, and the champion (like the final split). Set it to the env you actually score in; note the seed is scored in the host env, so if you pin `selection_env` to a remote box, re-baseline a candidate there or selection will be empty (`board`/`doctor` warn when that happens).
