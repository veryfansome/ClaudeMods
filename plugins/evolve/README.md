# evolve

A Claude Code plugin that runs a ShinkaEvolve-inspired evolutionary search over any project: LLM inventor agents are the mutation operators, a deterministic engine is the selection authority, and a user-supplied command is the sole fitness oracle. The design ports what the program-evolution literature shows is load-bearing — weighted parent sampling, novelty rejection before evaluation, inspiration-rich mutation context, a cheap-to-expensive eval cascade ([ShinkaEvolve, arXiv 2509.19349](https://arxiv.org/abs/2509.19349); [AlphaEvolve](https://arxiv.org/abs/2506.13131); [FunSearch](https://www.nature.com/articles/s41586-023-06924-6)) — and drops what a coding agent subsumes (patch wire formats, model plumbing).

## The division of labor

**The LLM proposes; it never scores itself and never decides which ancestors seed the next attempt.** Everything selection-critical is deterministic engine code (`bin/evolve`, python3 stdlib): the append-only archive, fitness+novelty weighted parent sampling (`σ(λ·(F−median)) · 1/(1+offspring)`), dedup, budget accounting, surface enforcement, and the holdout firewall. The skills orchestrate inventors and relay the engine's verdicts.

Fitness is trustworthy by construction, not convention:

- **Isolated-export scoring** — every candidate is evaluated in a `.git`-less export of HEAD (`git archive`) plus exactly the candidate (a diff, or genome files). Two things follow: anything an inventor plants outside the candidate — a shadowing module, a patched fixture — is simply absent; and, with no `.git` link, evolved code running during eval cannot discover the real repo to read the private holdout or rewrite the archive. This is input/discovery isolation, **not an OS sandbox** — eval still runs candidate code with your privileges (network, `$HOME`), so run evolution in a trusted environment and treat eval-produced `text_feedback`/`public` as candidate-influenced.
- **Measured noise floors + pre-registered gates** — `evolve doctor --measure-noise` runs the baseline k times and writes the spread to the config; that floor is the proxy screen threshold and the sacred non-regression rail. A win isn't decided by judgment after the fact: `evolve preregister` commits a round's gates *before* scoring — **G1** (fitness must not regress below `champion − noise_floor`, auto-derived and machine-checked by `evolve gate`) and **G2** (the round objective) — and promotion requires **G1 ∧ G2**, re-scored at full budget (the proxy screens, it never promotes).
- **The `final` split firewall** — records scored on the reserved holdout split never enter selection, enforced in the archive layer. Only a promoted champion is scored there, once, as validation.
- **Failures are signal** — a broken candidate records `fitness: null` with a reason; it never crashes the loop and never silently disappears.

## What a project supplies

Three things, created by `/evolve:init` and validated by `evolve doctor`:

1. **An evolvable surface.** `markers` mode: `EVOLVE-BLOCK-START`/`END` comment lines in declared files — only text between them may change. `registry` mode: a genome selecting one impl (+ params) per design axis from `evolve/chunks/<axis>/`, jepa-style; impls are append-only, and recombination (stacking per-axis winners) is a first-class move.
2. **Eval commands** (`proxy` cheap screen, `full` promotion budget, optional `final` holdout) emitting `combined_score` + optional `public` / `private` / `text_feedback` / `correct` — ShinkaEvolve's evaluator contract, verbatim.
3. **`evolve/evolve.json`** — the declarative contract: task, surface, protected paths, budgets, search knobs.

## Install

Requires Claude Code, `python3`, and `git` (scoring exports HEAD with `git archive`). From a clone of this repo:

```
# 1. Register this repo as a local marketplace (run from the repo root)
claude plugin marketplace add .

# 2. Install the plugin (user scope by default)
claude plugin install evolve@claudemods

# 3. Activate it in the current session — run the slash command (or restart Claude Code):
/reload-plugins
```

Enabling the plugin is what puts the engine CLI (`bin/evolve`) on the **Bash-tool** PATH — so `evolve …` runs *inside* a Claude Code session (after `/reload-plugins`), not from your terminal — and arms the `PreToolUse` hook that guards a project's protected paths during a run. A local (directory-source) marketplace does not auto-register from this repo's project `enabledPlugins`; step 1 is required. If you've added the marketplace before and just pulled new commits, run `claude plugin marketplace update claudemods` before installing so the new manifest is picked up.

Nothing global is created — unlike a store-backed plugin, `evolve` holds no state of its own. All state lives per-project under `evolve/`, created by the setup below.

## Use in a project

From inside the target project (a git repo), in a Claude Code session:

```
/evolve:init      # interview → choose surface → generate the eval adapter → verify
                  #   (won't finish until the contract holds: markers/registry parse,
                  #    the seed scores, a sabotage mutant fails, an out-of-block edit is rejected)
/evolve:round     # one generation; /loop 20m /evolve:round for unattended search
/evolve:status    # board, budget, staleness, next step
```

`/evolve:init` scaffolds `evolve/` (config, archive, manual) and commits the seed state; from then on the project owns its contract. The engine CLI is also usable directly — `evolve board`, `evolve sample`, `evolve score …`, `evolve rescore …`, `evolve preregister …` / `evolve gate …` (commit a round's promotion gates before scoring, then check a candidate against them — the sacred fitness-non-regression gate G1 is auto-derived and machine-checked), `evolve doctor --measure-noise`, `evolve doctor --measure-env-offset` (measure cross-environment fitness comparability); see `evolve --help`. Exit codes are the round loop's control signals: **3** budget exhausted, **4** guard violation, **5** novelty (duplicate) rejection, **6** infrastructure failure (export/setup — not archived, retry).

**Uninstall:** `claude plugin uninstall evolve@claudemods` removes the plugin and disarms the hook. A project's `evolve/` directory is yours — it stays until you delete it; unwired, it is inert data.

## Developing the plugin

Enabled plugins run a **frozen install-time cache snapshot**, not your working copy — so edits to `bin/` or the skills don't take effect until you reinstall. To exercise current code without reinstalling, launch with `claude --plugin-dir plugins/evolve` from the repo root. Run the offline test suite with `plugins/evolve/tests/run` (no model calls, no network).

## What is deliberately not here (v1)

Islands/migration and the UCB bandit over mutation models — the paper's own flagship results ran with migration at 0.0 and (for its agent-scaffold task) the bandit disabled; heterogeneous high-effort inventor agents supply the diversity instead. Records carry `inventor`/`operator` tags so a bandit can be layered on real data later without schema changes. Embedding-based dedup is approximated with a dependency-free normalized difflib ratio; projects wanting true embeddings can gate inside their own eval.
