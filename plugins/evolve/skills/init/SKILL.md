---
name: init
description: Set up a ShinkaEvolve-style evolutionary search in the current project — interview the owner, analyze the repo, choose the evolvable surface (markers or chunk registry), generate the eval adapter, and verify the whole contract before declaring it live. Invoke when the user wants to "evolve" some part of a codebase, optimize a heuristic/algorithm/prompt against a measurable objective, or asks to set up the evolve plugin.
---

# evolve:init — set up the contract, then prove it holds

You are setting up an evolutionary search whose trustworthiness depends entirely on the contract you create here. The engine (`evolve` CLI, on PATH while this plugin is enabled) is deterministic and never needs editing; everything project-specific flows through `evolve/evolve.json` and the eval adapter you generate. **Do not declare setup done until the verification suite at the end passes — a contract that is documented but not enforced is how fitness gets gamed.**

## 1. Interview (ask, don't guess)

Establish, in the user's words: (a) **what is being maximized** — one scalar, or a margin over a baseline (prefer margins when part of the signal is banked by trivial baselines: a candidate that lifts the metric and the baseline equally has discovered nothing); (b) **what measures it** — an existing bench/test/load-test command, its runtime, and how noisy it is; (c) **what may change** — the code region or the pluggable design axes; (d) **what is sacred** — eval assets, data, anything a candidate must never touch; (e) **budgets** — rounds, full-eval count, wall-clock or cost tolerance; (f) **holdout** — is there data/scenarios that can be reserved as a `final` split the loop never optimizes against? Push for yes; it is the only structural defense against overfitting the selection signal.

## 2. Analyze the repo and choose the surface

Read the code around the optimization target and propose:

- **markers mode** (default) — one coherent region: wrap it in `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END` comment lines (comment syntax of the file's language). Everything outside, marker lines included, is frozen and enforced.
- **registry mode** — when the target decomposes into swappable, independently-scorable design axes (an objective, a policy, a schedule, a data layout…). Declare each axis in the config with a `baseline` impl whose docstring IS the contract, and seed `evolve/chunks/<axis>/` with baseline impls that reproduce current behavior exactly. Registry mode is what makes recombination (stacking per-axis winners) expressible — prefer it when the user's target has ≥2 natural axes.

Run `evolve init --mode <markers|registry>` to scaffold `evolve/` (idempotent), then fill in `evolve/evolve.json`: task, surface, eval commands, budgets, and **protected** — which must cover the config and archive (the default `["evolve/evolve.json", "evolve/archive/**"]`) plus every eval asset you author (the adapter script, load-test harnesses, holdout data). Do NOT protect the registry dir or `evolve/insights.md` — the round writes those. (The protect-paths hook stays disarmed until the first `evolve seed`, so you can freely write the config and adapter now; afterward, deliberate edits to a protected file need `EVOLVE_ALLOW_PROTECTED=1` in the session.)

## 3. Generate the eval adapter

Write the adapter in the project's own language/tooling, wrapping its real test/bench assets behind the engine's contract: emit `{results_dir}/metrics.json` with `combined_score` (required), and use the optional keys deliberately — `public` for metrics inventors should see, `private` for per-case breakdowns that would invite overfitting if briefed, `text_feedback` for one actionable sentence about *why* the score is what it is (it reaches descendants' briefs), `correct: false` for hard guardrail failures. Wire `{seed}` to real seeding and `{split}` to the inner/final data selection. Cheap correctness gates (compile, unit tests) go in `eval.smoke`/`eval.guardrails`, not the scored command. The adapter is a **protected** file.

## 4. Verify — refuse to finish until all of these pass

1. `evolve doctor` — the contract parses, markers/registry are well-formed, surface files are committed.
2. **Commit the seed state** (markers, adapter, config) — pristine clones score HEAD; uncommitted setup is invisible to scoring.
3. `evolve doctor --measure-noise` — k baseline runs; the measured floor is written to the config. If the floor is large relative to plausible wins, say so now and improve the eval (more seeds, longer runs, `full.seeds` ≥ 3) before proceeding.
4. `evolve seed` — gen-0 archived with a sane fitness.
5. **Sabotage mutant** — make a deliberately broken candidate (e.g. an edit inside the block that returns a constant / crashes) and score it with `evolve score --no-archive` (verify-only — do not pollute the archive or the dedup corpus): it must return `fitness: null` with the right guardrail reason. This proves the gates can observe the failure they exist for.
6. **Boundary probe** — make an edit *outside* the markers (or modify an existing registry impl) and confirm `evolve guard` rejects it (exit 4). Revert both probes.

Then report to the user: the contract summary, the measured noise floor, the gen-0 fitness, and the first `/evolve:round` invocation to try. Update `evolve/EVOLVE.md`'s contract section with anything project-specific the template doesn't cover.
