# evolve

A Claude Code plugin that runs a ShinkaEvolve-inspired evolutionary search over any project: LLM inventor agents are the mutation operators, a deterministic engine is the selection authority, and a user-supplied command is the sole fitness oracle. The design ports what the program-evolution literature shows is load-bearing — weighted parent sampling, novelty rejection before evaluation, inspiration-rich mutation context, a cheap-to-expensive eval cascade ([ShinkaEvolve, arXiv 2509.19349](https://arxiv.org/abs/2509.19349); [AlphaEvolve](https://arxiv.org/abs/2506.13131); [FunSearch](https://www.nature.com/articles/s41586-023-06924-6)) — and drops what a coding agent subsumes (patch wire formats, model plumbing).

## The division of labor

**The LLM proposes; it never scores itself and never decides which ancestors seed the next attempt.** Everything selection-critical is deterministic engine code (`bin/evolve`, python3 stdlib): the append-only archive, fitness+novelty weighted parent sampling (`σ(λ·(F−median)) · 1/(1+offspring)`), dedup, budget accounting, surface enforcement, and the holdout firewall. The skills orchestrate inventors and relay the engine's verdicts.

Fitness is trustworthy by construction, not convention:

- **Isolated-export scoring** — every candidate is evaluated in a `.git`-less export of HEAD (`git archive`) plus exactly the candidate (a diff, or genome files). Two things follow: anything an inventor plants outside the candidate — a shadowing module, a patched fixture — is simply absent; and, with no `.git` link, evolved code running during eval cannot discover the real repo to read the private holdout or rewrite the archive. This is input/discovery isolation, **not an OS sandbox** — eval still runs candidate code with your privileges (network, `$HOME`), so run evolution in a trusted environment and treat eval-produced `text_feedback`/`public` as candidate-influenced.
- **Measured noise floors** — `evolve doctor --measure-noise` runs the baseline k times and writes the spread to the config; promotion requires beating the champion by more than it.
- **The `final` split firewall** — records scored on the reserved holdout split never enter selection, enforced in the archive layer. Only a promoted champion is scored there, once, as validation.
- **Failures are signal** — a broken candidate records `fitness: null` with a reason; it never crashes the loop and never silently disappears.

## What a project supplies

Three things, created by `/evolve:init` and validated by `evolve doctor`:

1. **An evolvable surface.** `markers` mode: `EVOLVE-BLOCK-START`/`END` comment lines in declared files — only text between them may change. `registry` mode: a genome selecting one impl (+ params) per design axis from `evolve/chunks/<axis>/`, jepa-style; impls are append-only, and recombination (stacking per-axis winners) is a first-class move.
2. **Eval commands** (`proxy` cheap screen, `full` promotion budget, optional `final` holdout) emitting `combined_score` + optional `public` / `private` / `text_feedback` / `correct` — ShinkaEvolve's evaluator contract, verbatim.
3. **`evolve/evolve.json`** — the declarative contract: task, surface, protected paths, budgets, search knobs.

## Use

```
claude plugin install evolve@claudemods

/evolve:init      # interview → surface → adapter → verification (won't finish until the contract holds)
/evolve:round     # one generation; /loop 20m /evolve:round for unattended search
/evolve:status    # board, budget, staleness, next step
```

The engine CLI is also usable directly (`evolve board`, `evolve sample`, `evolve score …`) — see `evolve --help`. Exit codes 3/4/5 (budget / guard / duplicate) are the control-flow signals the round skill acts on.

## What is deliberately not here (v1)

Islands/migration and the UCB bandit over mutation models — the paper's own flagship results ran with migration at 0.0 and (for its agent-scaffold task) the bandit disabled; heterogeneous high-effort inventor agents supply the diversity instead. Records carry `inventor`/`operator` tags so a bandit can be layered on real data later without schema changes. Embedding-based dedup is approximated with a dependency-free normalized difflib ratio; projects wanting true embeddings can gate inside their own eval.
