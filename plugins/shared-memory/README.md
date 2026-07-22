# shared-memory

A Claude Code plugin that distills generalizable lessons out of per-project auto-memory and into a common, git-shareable store — so a correction Claude learns in one project changes how it works in every project, instead of being relearned from zero each time.

The payload is *formation*, not facts: shaping how Claude works, not just recording what it knows. The corrections a collaborator gives — *you jumped to a conclusion from a few data points; you took an early off-ramp when the work got hard; you claimed "verified" without validating the assumption* — are the lessons effective people internalize over a career. Carried across projects, they compound. The plugin does three things: it **moves** lessons out of the project where they were learned, it **guards** the quality of every belief that gets stored, and it **reflects** on recent sessions to catch what in-the-moment judgment missed.

## The problem

Claude Code's auto memory is project-scoped: each project gets `~/.claude/projects/<project>/memory/` with a `MEMORY.md` index that loads at session start. For project facts — build commands, service topology, ticket conventions — that boundary is right. It's wrong for the memories that aren't about the project at all:

- **Behavioral lessons are taught where the mistake happened, not where they apply** — which is everywhere. Each one sits trapped in one project's memory dir while every other project repeats the mistake.
- **The re-teaching cost lands on the human** — re-giving a correction that was already "learned" is exactly the toil a memory system exists to remove, and nothing erodes trust faster.
- **Independently relearned copies drift** — each reflects whichever incident taught it there, and a refinement in one never reaches the others.
- **The delivery mechanism must live outside the project dirs** — new repos appear constantly and auto memory rewrites its own files, so any design that writes into every project dir is maintenance against a moving target.

## How it works

Three tiers; every move up is a deliberate act.

```
Tier 1: project memory      ~/.claude/projects/<proj>/memory/   (native, unchanged)
            │  /memory-distill promotes generalizable lessons
            ▼
Tier 2: shared store        ~/.claudemods/shared-memory/        (all projects, this machine)
            │  optional: push / PR to a git remote you choose
            ▼
Tier 3: shared store + remote   any git remote — team, org, public
```

A solo user stops at Tier 2 and gets the full value. A group agrees on a remote, each member's `~/.claudemods/shared-memory` is a clone, and group-scoped memories go through PR review like any other shared artifact. Tier 3 is not a separate mechanism — it's Tier 2 with a git remote.

The plugin ships **tooling plus the store's own doctrine** — skills, hooks, scripts, an installer, and the five memories the store needs to run itself (see [Doctrine](#doctrine)). Nothing else ships — no lessons about work, no preferences. **The store's content is yours.**

### Components

| Component | Mechanism | Job |
|---|---|---|
| Shared store | `~/.claudemods/shared-memory/` (user-owned; optionally a git clone) | Holds the doctrine, distilled memories, the index files, and the retirement ledger |
| Recall | `@import` of `MEMORY.md` + `MEMORY.local.md` in `~/.claude/CLAUDE.md` **+** a `SessionStart` hook injecting the current repo's domain projections | Loads the shared indexes into every session of every project (see [Recall](#recall)) |
| Capture | `PostToolUse` hook (`capture-candidate`) on Write/Edit, scoped to project memory dirs | Appends newly written project memories to a promotion-candidate queue |
| Distillation | `/memory-distill` skill + `distill-scan` / `distill-apply` | Scans project memory + queue, clusters duplicates, merges, promotes, updates indexes |
| Hygiene | `/memory-review` skill + `review-scan` / `review-apply` | Verifies stale or weakly-grounded memories, retires dead ones to `RETIRED.md`, consolidates lessons into frameworks |
| Reflection | `/memory-reflect` skill + `reflect-scan` (+ `SessionEnd` hook pre-recording session ends) | Ruminates over recent transcripts: audits recall misses, mines corrections that never became memories |
| Post-write eval | `PostToolUse` hook (`eval-memory`) on Write/Edit | Grades a belief as it lands and flags a missing `basis` or a near-duplicate — surface, don't block |
| Setup | `memory-init` (a script in `bin/`, not a skill — one-time and deterministic) | Solo install / uninstall / `--doctor` health pass / repo→domain mapping |

### One memory's journey

1. In project A you correct Claude — *"you claimed verified without testing it."* Auto memory saves the correction in A's memory dir; the capture hook queues its path.
2. `/memory-distill` clusters it with a similar correction already sitting in project B, proposes one generalized lesson, and you sign off: the merged memory lands in the store, the originals are archived, both project copies are removed.
3. The next session in *any* project — including project C, which never learned it — loads the index line at launch; the next time a "verified" claim is forming, the lesson taught in A and B changes behavior in C.
4. Later, `/memory-review` folds it with related lessons into a framework, and `/memory-reflect` checks recent transcripts to confirm it actually fires — and to catch the times it didn't.

## Install

Requires Claude Code and `python3`. From a clone of this repo:

```
# 1. Register this repo as a local marketplace (run from the repo root)
claude plugin marketplace add .

# 2. Install the plugin (user scope by default)
claude plugin install shared-memory@claudemods

# 3. Activate it in the current session — run the slash command (or restart Claude Code):
/reload-plugins

# 4. Seed the store + wire recall, from a Claude Code Bash step:
memory-init
```

Two independent pieces, and you need **both**:

- **Enabling the plugin** (steps 1–3) is what activates the `capture` / `eval` / `SessionStart` domain-recall / `SessionEnd` reflection hooks and puts the plugin's `bin/` on the Bash tool's PATH. `bin/` is added to the **Bash-tool** PATH, not your interactive shell — so `memory-init` runs *inside* a Claude Code session (after `/reload-plugins`), not from your terminal. A local marketplace does not auto-register from this repo's project `enabledPlugins`; step 1 is required.
- **`memory-init`** (step 4) creates the store at `~/.claudemods/shared-memory/`, seeds the five doctrine memories from verbatim templates, adds the two `@import` lines to `~/.claude/CLAUDE.md` (inside `<!-- shared-memory:begin/end -->` sentinels), and adds the store's read/edit permission rules to `~/.claude/settings.json`. It's deterministic and idempotent: config files are backed up before any edit, settings are parse-validated and written atomically, and re-running converges. It's a plain script, so it also runs directly with no plugin needed: `python3 plugins/shared-memory/bin/memory-init`.

Running `memory-init` *without* enabling the plugin gives you flat `@import` recall + permission rules, but **no** capture, domain-scoped recall, post-write eval, or reflection — those live in the plugin's hooks. New sessions then load the shared index automatically, including the subagents they spawn.

Run `memory-init --doctor` any time for a read-only health pass (wiring drift, plugin build freshness, recall health, projection integrity, coverage, version drift, ignore confinement); it emits JSON and exits 2 if any check fails, but never self-heals — the fix for drift is `memory-init` again.

## Using it

The three skills are the interface; each proposes and waits for your sign-off before anything is written.

- **`/memory-distill`** — promote generalizable lessons from project memory into the store. Scans and clusters candidates, judges each (promote / antenna / fold / leave / flag), runs an adversarial self-review, then presents the batch *shape* for an early veto before any wording exists, drafts the memories, proposes the full batch, and — on sign-off — writes the beliefs through the file tools and runs the mechanical ops (archive, delete/slim, queue-consume) through `distill-apply`.
- **`/memory-review`** — hygiene. Flags memories past a staleness threshold, the `observed` review queue, and `contradicted` quarantines; checks dangling references; proposes verify / update / retire / consolidate actions. Retiring archives the memory, removes its index line, deletes the file, and appends one line to `RETIRED.md`.
- **`/memory-reflect`** — rumination. Reads recent transcripts and runs two audits: a **recall audit** (where a lesson should have fired but didn't) and **mining** (corrections and discoveries that never became memories). Findings join the same queue `/memory-distill` consumes.

## Recall

**Two always-loaded index lines**, added by `memory-init` to `~/.claude/CLAUDE.md`:

```markdown
@~/.claudemods/shared-memory/MEMORY.md
@~/.claudemods/shared-memory/MEMORY.local.md
```

`MEMORY.md` is the *general* index — one line per memory, same convention as auto memory's index; bodies are read on demand with normal file tools. `MEMORY.local.md` is the *personal* overlay: same format, gitignored, indexing memories that never enter group review. An index line is the unit of recall — a link and the memory's `description`, written trigger-rich (a framework's line is prefixed with its `applies_when` triggers, since the trigger is what has to fire mid-task):

```markdown
- [Memory gate](framework_memory_gate.md) — fires on storing-a-memory / stating-a-conclusion: information saved to memory should have hard factual grounding — check evidential basis before persisting
```

**Recall is hybrid.** As the store grows, an always-loaded flat index dilutes — a `gcp` lesson, a `code-review` reference, and an unrelated note all load in every session. So a memory can carry a `domains` set, and its line then lives in a *domain projection* (`MEMORY.<domain>.md`) that loads only where it applies:

- The **always-load tier** (`MEMORY.md` + `MEMORY.local.md`, the `[]`-tagged memories) keeps riding the `@import`, because that is the only channel a session's **subagents inherit**.
- The **domain tier** is delivered by a `SessionStart` hook (`load-recall`): it reads the current repo's domains from a machine-local map (`.domains.json`) and injects the matching `MEMORY.<domain>.md` / `MEMORY.local.<domain>.md` projections as `additionalContext` — **parent session only**, not inherited by subagents (a platform constraint that forces the split).

Domain projections are *generated* (`gen-projections`) from each memory's `domains` and never hand-edited; the flat index keeps only `[]`-tagged lines. Map a repo to its domains with `memory-init --map-repo <domain>...` (or `--map-repo-add`); `--list-domains` enumerates the domains that have a projection. The map is machine-local, so on a fresh clone or a second machine it's empty until you map each repo — the loader emits a one-line onboarding hint and `--doctor` warns (`unmapped-with-projections`) so this is never silent.

Conservative tagging is the safety lever: **the general tier always loads, so a genuinely cross-cutting lesson is never filtered out** — add a domain only to *narrow* where a lesson loads, and default to `[]` (loads everywhere) when unsure. `/memory-reflect`'s recall audit catches "should have fired but didn't," domain-mismatch misses included.

## Memory format

Close to what native auto memory writes, extended with epistemic metadata. The full field spec ships *in the store* as `reference_store_memory_format.md` — sessions writing memories need it in recall, not in this README. A real store entry:

```markdown
---
name: feedback-discuss-before-acting
title: Discuss before acting
description: When a mistake would be costly (time, money, rework) — survey the live system, present the exact scope, and get sign-off before acting; being reliable beats being helpful
metadata:
  type: feedback            # feedback (lesson) | reference (pointer/fact/convention) | framework (trigger + procedure, adds applies_when)
  domains: []               # empty = loads everywhere; else lowercase tags: gcp, terraform, code-review, project_<name>
  basis: observed           # verified (documented check ran) | observed (grounded lead — hedged, on the review queue) | contradicted (quarantined)
  last_verified: 2026-06-10 # verified: date the check last ran; observed: date the grounding was last revisited; n/a for doctrine
---

For changes where a mistake would be costly — multi-file edits, security remediation, infrastructure — survey first, present a concrete plan, and wait for sign-off before editing.

**Why:** Acting without confirming scope causes thrashing — edit, correction, revert — and thrashing breaches trust. Being reliable beats being helpful.

**How to apply:** Weigh the cost of being wrong. If a mistake would be expensive, discuss and confirm first. If it's trivial to undo, acting first is probably fine.
```

Key fields:

- **`type`** — `feedback` (a lesson about how to work; preferences are feedback too) | `reference` (a pointer, fact, or convention) | `framework` (a trigger plus a procedure, carrying `applies_when`). Native auto memory writes other values (`user`, `project`); distill maps them on the way in.
- **`basis`** — the evidence grade. `verified` (a check that could have disproven the claim ran, documented in a **Verified:** section) | `observed` (grounded but never faced a killing check — hedged, carries its open question, sits on the review queue) | `contradicted` (conflicting evidence appeared — quarantined by tombstoning its index line until a review adjudicates). A claim with no grounding at all is not a memory. The body wears its basis honestly: an `observed` entry hedges; a `verified` one documents the check.
- **`domains`** — the *loading* axis (a set), orthogonal to the *location* axis below. A memory tagged `[gcp, terraform]` loads whenever the repo matches either.
- **Scope is location.** `local/` + `MEMORY.local.md` is *personal* (truth depends on the person; never leaves the machine). The top level is *general* (about the work or the world; provisional until reviewed at a sharing boundary). There is no `scope` field — moving the file *is* how scope changes, content untouched.

### Store layout

```
~/.claudemods/shared-memory/
├── MEMORY.md                 # general index: one line per memory; always loaded via @import
├── MEMORY.local.md           # personal overlay index (gitignored); always loaded too
├── MEMORY.<domain>.md        # a generated domain projection: loaded only in matching repos
├── MEMORY.local.<domain>.md  # personal domain projection (gitignored)
├── RETIRED.md                # committed retirement ledger (name, date, reason, superseded-by)
├── framework_memory_gate.md              # shipped doctrine (seeded by memory-init)
├── framework_a_guess_is_not_a_fact.md    #   "
├── framework_write_for_the_cold_reader.md#   "
├── framework_reflect_memories_back.md    #   "
├── reference_store_memory_format.md      # shipped doctrine — the field spec, kept in recall
├── feedback_discuss_before_acting.md     # a promoted lesson (general by location)
├── local/                    # machine-local, gitignored as a unit
│   ├── feedback_<pref>.md     # personal memories, indexed by MEMORY.local.md
│   └── archive/              # verbatim originals of promoted memories (provenance + recovery)
├── .candidates.log           # promotion queue fed by the capture hook + /memory-reflect (gitignored)
├── .candidates.consumed      # consumed-occurrence ledger for the queue (gitignored)
├── .recall.log               # body-read telemetry (gitignored)
├── .runs.log                 # review run-windows (gitignored)
├── .probe-stamp.json         # CLI version(s) the recall probe passed on (gitignored)
├── .reflect/                 # rumination state: watermark, learned salience markers (gitignored)
├── .domains.json             # repo→domain map, machine-local (gitignored)
└── .gitignore                # committed — keeps the personal overlay out of PRs from day one
```

The tooling keys off frontmatter (`name`, `type`, `basis`, `domains`); filenames are cosmetic (`type_` prefixes are house style, not contract). Provenance travels in the memory's **Why:**, not in source paths — archive paths embed project names, which a group remote would turn into incidental disclosure, so the shared file carries none.

## The memory gate

At the moment of saving, the **memory gate** — itself shipped doctrine, the store's load-bearing first entry — models what a careful person does with a curious fact: gets suspicious, asks one more question, and commits only what the evidence supports. Stop before saving if the fact is surprising, rests on a guess, hangs on a single observation, has an unknown mechanism, contradicts an existing memory, or is already covered by a shared one. When triggered, either ask the clarifying question, run the check, or shrink the claim to what the evidence supports and store *that* — graded `observed`, hedged, with the open question written in. If the grounded core isn't worth keeping, save nothing.

The gate doesn't *block* (nothing does — see the principles). Enforcement is soft and native: its trigger rides the always-loaded index, the distill skill honors it, and when a belief lands, the `eval-memory` hook checks it against the store and surfaces a missing `basis` or a near-duplicate on two channels (a `systemMessage` the collaborator sees directly, and `additionalContext` Claude reads the same turn) — non-blocking, so a flagged conflict is corrected in conversation or quarantined, not refused.

## Doctrine

Five memories about the store itself ship with the plugin and are seeded into every store, each declaring `doctrine: true`:

- **Memory gate** — whether to save (above).
- **A guess is not a fact** — how to speak about certainty (the `basis` machinery operationalized).
- **Write for the cold reader** — how a memory is worded (the reader is a stranger with zero context).
- **Reflect memories back** — surface every write to the collaborator so a wrong takeaway is corrected at once (transparency in place of write-time approval).
- **Store memory format** — the field spec, kept in recall where sessions writing memories need it.

The `doctrine: true` flag is a *restriction, not a privilege*: automated passes may never write a file that carries it or mint a new one (`distill-apply` refuses both), so doctrine changes only by deliberate revision — store file and shipped template together.

## Design principles

1. **Native first.** Only Claude Code's documented mechanisms (CLAUDE.md imports, auto memory, hooks, skills) — no MCP server, no database, no daemon.
2. **Lessons are the payload.** Optimize for behavioral feedback; let facts travel only on evidence of cross-project relevance.
3. **Files are the database.** Plain markdown with frontmatter — human-readable, diffable, shareable via git.
4. **Claude curates, mechanisms deliver.** Distillation is judgment work (a skill); delivery is zero-judgment plumbing (imports + hooks).
5. **Don't touch the fan-out.** No design that writes into N project memory dirs. One shared store, one injection point.
6. **Curated, not accumulated.** A small index of verified lessons beats a large archive of observations — provenance, supersedence, and staleness review as judgment inside skills, no embeddings, no decay curves. A belief changes only with the collaborator in the loop.
7. **Inform, don't gate.** A stored belief steers every future session, so the system stays honest by *surfacing and healing*, not by blocking writes. Every memory is graded with its `basis`, reflected back the moment it's written, and re-examined by review and reflect. The bar rises only with blast radius: a solo store self-heals; a memory crossing into a shared remote still goes through review.

## Trust: a memory is a loaded instruction

The store feeds always-loaded context, making it the highest-leverage prompt-injection surface a session has: whoever can land text in it steers every future session. Treat the store like code, because it runs like code.

- The group review bar is "safe to **load**", not just "safe to share": read a candidate memory as instructions Claude will follow, and ask what behavior it licenses before asking whether it's true.
- **No auto-pull.** An automatic `SessionStart` pull turns a compromised remote into a compromise of every member's next session; a human stays between the remote and the context window.
- **The store carries no executables.** Tooling lives in the plugin (whose install *is* the trust ceremony); a `bin/` arriving via a synced store is a supply-chain payload, and `memory-init` treats it as one.
- **`.gitignore` is policy that syncs from the remote** and is what keeps `local/` personal. The behind-remote warning and the doctor both run `git check-ignore` over every machine-local path, and `memory-init` refuses a clone that *tracks* any of them.

## Uninstall & recovery

**Uninstall** with `memory-init --uninstall` — it removes only the wiring it added (the `@import` block and the settings rules) and leaves the store: your memories are yours. The plugin's own hooks stay live until you disable the plugin, so a full removal is two steps: disable/uninstall the plugin (stops the hooks), then optionally `rm -rf ~/.claudemods/shared-memory`. `--uninstall` warns if the plugin still appears enabled.

**Break-glass recovery** (no working Claude Code required — everything the install touches is a few marked lines in human-editable text):

1. **`~/.claude/CLAUDE.md`** — delete everything from `<!-- shared-memory:begin -->` through `<!-- shared-memory:end -->` inclusive (delete the file if nothing else remains).
2. **`~/.claude/settings.json`** — copy the newest `settings.json.smbak.*` backup over it, or hand-delete the rules whose patterns contain `shared-memory` plus the `Read(~/.claude/projects/**)` rule. Invalid settings are rejected loudly at launch, so the backup is the safer path.
3. **Plugin** — delete the `"shared-memory@claudemods"` line from `enabledPlugins`; one removal disarms every skill, hook, and executable the plugin carries.
4. **Optional** — `rm -rf ~/.claudemods/shared-memory` to remove the memories too. Unwired, the store is inert data.

## Requirements & platform notes

- **Claude Code ≥ 2.1.85** (the hook `if` field, used to path-scope the capture hook, was added then; older versions fire on every Write/Edit and the script's path re-check downgrades that to wasted spawns).
- **python3** (stdlib only, any modern 3.x) for the scripts. Claude Code bundles no python; a missing interpreter fails loudly (`env: python3`) rather than silently, and `--doctor` probes for it.
- The store lives at `~/.claudemods/`, **outside `~/.claude/`**, deliberately: writes under `~/.claude/` hit a built-in sensitive-file gate, so relocating the store makes its own permission rules the write policy (no per-write prompt) — friction the "inform, don't gate" principle removes. The human stays informed via the harness rendering every file-tool write plus the reflect-memories-back doctrine.
- Recall wiring works **without an approval dialog**: the external-import approval belongs to *project-level* CLAUDE.md files, and a user-level file importing a user-level path (`~/.claudemods/` is under home) doesn't count as external — verified by a recall probe.

## Roadmap

Shipped and deferred features — group remote tooling, federated domain packs, and open design questions — are tracked in [ROADMAP.md](ROADMAP.md).

## Tests

Layer-1 deterministic tests (python3 stdlib `unittest`, no model calls, CI-ready) live in `tests/` — run `tests/run`. The paths that need a live `claude -p` (recall, subagent inheritance, capture-enqueue, mapped-domain recall) are Layer-2 probes in `probes/recall-probes.sh`, which skip loudly without auth; the doctor's `recall-health` check is the deployed self-test.
