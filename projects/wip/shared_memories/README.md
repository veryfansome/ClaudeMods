# Shared Memories

> **Status: design.** Nothing is implemented yet — the Roadmap is the plan. Mechanisms described in the present tense are the target state; references to "this machine" are the author's dogfooding environment, not requirements.

A lightweight, Claude Code-native system for distilling generalizable memories out of per-project auto-memory directories into a common, git-shareable store — so a lesson learned in one project never has to be relearned in another.

The payload is formation, not facts: shaping how Claude works, not just recording what it knows. The corrections collaborators give Claude — *you jumped to a conclusion from a few early data points; you took an early off-ramp when the work got hard; you claimed "verified" without validating the assumption* — are the lessons effective humans internalize over a career. Carried across projects, they compound: each Claude instance becomes more effective in every project at once, and unique — shaped by the projects it has worked on and the humans who have corrected it. The system therefore does three things: it moves lessons out of the projects where they were learned, it guards the quality of every belief that gets stored, and it reflects on recent experience to catch what in-the-moment judgment missed.

## The problem

Claude Code's auto memory is project-scoped: each project gets its own directory under `~/.claude/projects/<project>/memory/`, with a `MEMORY.md` index whose first 200 lines / 25KB load at session start. For project facts — build commands, service topology, ticket conventions — that's the right boundary. The problem is the memories that aren't about the project at all:

- Behavioral lessons are taught where the mistake happened, not where they apply — which is everywhere. Each one sits trapped in one project's memory dir while every other project repeats the mistake from zero.
- The re-teaching cost lands on the human: delivering the same correction again in the next project is exactly the toil a memory system exists to remove, and nothing erodes trust faster than re-giving feedback that was already "learned".
- Independently re-learned copies drift: each project's version reflects whichever incident taught it there, and a refinement captured in one copy never reaches the others.
- Scale constrains the fix: project memory dirs appear with every new repo, and auto memory rewrites its own files — so any design that must write into every project dir is maintenance against a moving target. The delivery mechanism has to live outside the project dirs entirely.

## Architecture

This project ships **tooling only** — skills, hooks, conventions, an installer — and contains no memories, with one deliberate exception: the memory-gate framework that `/memory-init` seeds into every new store (a store without it is missing its first entry). The store is yours: a plain directory (optionally a git repo, optionally with a remote) that the tooling reads and writes. Nothing here is specific to any person, team, or organization.

Three tiers, with explicit promotion between them:

```
Tier 1: project memory           ~/.claude/projects/<proj>/memory/    (native, unchanged)
            │  /memory-distill promotes generalizable lessons
            ▼
Tier 2: shared store             ~/.claude/shared-memory/             (all projects, this machine)
            │  optional: push / PR to a remote you choose
            ▼
Tier 3: shared store + remote    any git remote — team, org, public   (whoever clones it)
```

Tier 3 is not a separate mechanism — it's Tier 2 with a git remote. A solo user stops at Tier 2 and still gets the full value. A group agrees on a remote, each member's `~/.claude/shared-memory` is a clone, and group-scoped memories go through PR review like any other shared artifact. The tooling doesn't know or care which mode you're in.

### Components

| Component | Mechanism | Job |
|---|---|---|
| Shared store | `~/.claude/shared-memory/` (user-owned; optionally a git clone) | Holds distilled memories + the two index files |
| Recall (v1) | `@`-imports of `MEMORY.md` + `MEMORY.local.md` in `~/.claude/CLAUDE.md` | Loads the shared indexes into every session of every project |
| Recall (v2) | `SessionStart` hook with `additionalContext` | Scope-filtered injection (only inject `gcp` memories in GCP projects, etc.) |
| Gate | `memory-gate` framework (+ optional `PreToolUse` hook) | Pre-write validation of any belief about to persist |
| Capture | `PostToolUse` hook on Write/Edit, path-scoped to project memory dirs | Appends newly written project memories to a promotion-candidate queue |
| Distillation | `/memory-distill` skill | Scans project memory dirs + queue, clusters duplicates, merges, promotes, updates indexes |
| Hygiene | `/memory-review` skill | Verifies stale or weakly-based memories, retires dead ones, consolidates lessons into frameworks |
| Reflection | `/memory-reflect` skill (+ optional `SessionEnd` enqueue hook) | Ruminates over recent transcripts: audits recall misses, mines corrections that never became memories |
| Setup | `/memory-init` skill | One-time install: create or clone the store, seed the gate, wire the imports + permission rules, run the doctor pass |

### One memory's journey

1. In project A you correct Claude — *"you claimed verified without testing it."* Auto memory saves the correction in A's memory dir; the capture hook appends its path to the queue.
2. `/memory-distill` clusters it with a similar correction already sitting in project B, proposes one generalized lesson, and you sign off: the merged memory lands in the store, the originals are archived, both project copies are deleted.
3. The next session in *any* project — including project C, which never learned it — loads the index line at launch; the next time a "verified" claim is forming, the lesson taught in A and B changes behavior in C.
4. Later, `/memory-review` folds it with two related lessons into a framework, and `/memory-reflect` checks recent transcripts to confirm it actually fires — and to catch the times it didn't.

## Vocabulary

The recurring terms:

- **Genre** — what kind of memory something is (behavioral lesson, thinking framework, preference, fact). A judgment call made at distill time; `type` only hints at it.
- **`type`** — the frontmatter field auto memory already writes on this machine (`user | feedback | project | reference`), plus this system's `framework`.
- **`scope`** — who may adopt a memory once it's in the shared store: `personal` (one person's own — stays out of group review, and in v1 stays on one machine, since the `local/` overlay never syncs) or `group` (committed and PR-reviewed when the store has a remote).
- **`basis`** — how a memory knows what it claims: `observed` (seen it happen) → `tested` (a discriminating test ran) → `understood` (mechanism known *and* tested — the rungs are cumulative, so a plausible mechanism story that never ran its test is still just `observed`). The rung bounds how broadly the memory may speak.
- **Promotion** — `/memory-distill` moving a memory from a project dir into the shared store, generalizing it on the way.

## What travels, what stays

Two separate decisions per memory: whether it travels into the shared store at all, and — once there — what `scope` it carries:

| Genre | Typical `type` | Example | Travels into the shared store? |
|---|---|---|---|
| Behavioral lessons | `feedback` | "Don't claim 'verified' when the evidence is only 'no counterexample yet'" | Yes — immediately, even from one copy; they're about Claude, not the project. Worded at their rung: narrow + open question until the `basis` climbs |
| Thinking frameworks | `framework` | "Conclusion about to be stored → check its evidential basis first" | Yes — by default; archetype-indexed and domain-free (next section) |
| Collaborator preferences | `feedback`, `user` | "Terse code comments; single-line paragraphs in markdown" | Yes — but `scope: personal`; they belong to the human, never imposed on a group (and in v1 stay machine-local — see Multi-machine) |
| Cross-project facts | `reference` | Internal mirror URLs, org-wide ticket conventions, shared infra | Yes — once seen in, or clearly relevant to, more than one project |
| Project facts | `project`, `reference` | Build commands, service names, ticket parents | No — the native system already handles them well |

## Lessons become frameworks

A correction is the raw form of learning; the mature form is a **thinking framework**: a trigger plus a procedure, indexed by problem archetype rather than by domain. *Intermittent failure → suspect time, caching, and concurrency before logic. Conclusion resting on absence of evidence → design the discriminating test before claiming it.* Archetypes are domain-free, which is why frameworks transfer to out-of-domain work — they are the part of experience that behaves like general intelligence.

Frameworks are a memory genre (`type: framework`), stored, scoped, and promoted like everything else, but they sit at the top of a consolidation ladder that the skills climb over time:

```
incident     one correction, in the project where it happened
   ▼           promotion: generalize, strip incident specifics
lesson       "don't claim 'verified' without a discriminating test"
   ▼           consolidation: N related lessons → one trigger + procedure
framework    epistemic checkpoint that fires whenever a conclusion is about to be stated or stored
```

Frameworks evolve rather than multiply: when a new lesson fits an existing framework, `/memory-review` folds it in as a new trigger or refinement instead of minting another entry — judgment deepens, the index stays short.

Frameworks apply at two moments. At **task start**: the always-loaded index lists each framework by its trigger, so a session begins with its accumulated judgment in view. And at **memory-write time** — where the system turns its frameworks on itself: whether a belief is ready to persist is the first decision they govern (see the memory gate, below).

## Design principles

1. **Native first.** Use Claude Code's documented mechanisms (CLAUDE.md imports, auto memory, hooks, skills) — no MCP server, no database, no daemon.
2. **Lessons are the payload.** Project facts mostly belong where they are; what travels best is feedback about how Claude itself works. Optimize the pipeline for behavioral lessons, and let facts travel only on evidence of cross-project relevance.
3. **Files are the database.** Plain markdown with frontmatter, readable and editable by humans, diffable and shareable via git.
4. **Claude curates, mechanisms deliver.** Distillation (dedup, merge, generalize) is judgment work — it belongs in a skill Claude executes. Delivery (getting memories into context) should be zero-judgment plumbing.
5. **Don't touch the fan-out.** Avoid designs that require writing into N project memory dirs. One shared store, one injection point.
6. **Curated, not accumulated.** A small index of verified lessons beats a large archive of observations. Lifecycle means provenance, supersedence, and staleness review, applied as judgment inside skills — no embeddings, no decay curves, and nothing changes a memory without sign-off. (Zero-judgment plumbing like the capture log or a freshness check may run unattended; deciding what's true never does.)
7. **Gate at write time.** A stored belief compounds across sessions and, through promotion, across projects — so the bar for persisting a conclusion is higher than the bar for acting on one. A surprising claim gets its follow-up question before it gets saved, and every memory declares its evidential `basis`.

## Recall path

**v1 is two import lines.** User-level `~/.claude/CLAUDE.md` applies to all projects, and `@path` imports are fully loaded at launch:

```markdown
# ~/.claude/CLAUDE.md
@~/.claude/shared-memory/MEMORY.md
@~/.claude/shared-memory/MEMORY.local.md
```

The shared `MEMORY.md` is an index — one line per memory, same convention as auto memory's index — and memory bodies are read on demand with normal file tools. `MEMORY.local.md` is the personal overlay: same format, gitignored, indexing the `scope: personal` memories that never enter group review (empty in a solo store until needed). An index line is the unit of recall — one line, a link, and a trigger-rich description:

```markdown
- [Verified means tested](feedback_verified_means_tested.md) — before writing "verified/always/never", name the alternative hypothesis and run the test that separates it
- [Memory gate](framework_memory_gate.md) — fires when storing a memory or stating a conclusion: fishy information earns a follow-up question, not a save
```

Add permission rules in user-level `~/.claude/settings.json` — reads free, writes always a prompt:

```json
{
  "permissions": {
    "allow": ["Read(~/.claude/shared-memory/**)"],
    "ask": ["Edit(~/.claude/shared-memory/**)"]
  }
}
```

The `ask` rule (spelled `Edit(...)`, which in permission syntax covers Write and every other file-editing tool) is the mechanism behind "changes only deliberately" below: any session can read the store freely, but nothing writes to it without a prompt — including the distill/review skills, whose writes the user has already signed off on anyway.

Every project — including ones created tomorrow — inherits this automatically, and so do the subagents a session spawns. Headless probes load it with no per-project approval. The unprobed interactive case is documented as a one-time approval dialog — and a *decline* permanently disables the import for that project, silently killing recall there — so M0's first interactive sessions test both paths (evidence and honest rungs in Platform assumptions). A decline can also happen later — a teammate's first session after `/memory-init`, one reflexive keypress — so the doctor pass in `/memory-init` and `/memory-review` checks for it: `~/.claude.json` keeps per-project external-import approval state (emergent and undocumented — `basis: observed`), which turns a silently recall-dead project into a detectable, repairable one. And because the store sits outside all project dirs — with the `ask` rule making every write a prompt — shared memories change only deliberately: via the distill/review skills, under git review when the store has a remote, never as a side effect of work inside one project.

**v2 (optional): scope-filtered injection.** If the always-on index grows costly, replace the import with a user-level `SessionStart` hook that filters memories by their `domains` against the current project and emits the relevant slice as `hookSpecificOutput.additionalContext`. Same store, smarter delivery. Imports load in full — the budget here is token cost, not any hard cap — and subagents inherit the import, so a session that fans out pays the index once per agent. The v2 trigger is therefore tokens × agents, not raw line count; `/memory-review` tracks it.

## Memory file format

Same format auto memory already uses on this machine, extended with promotion and epistemic metadata:

```markdown
---
name: feedback-verified-means-tested
description: Don't claim "verified" from consistent-but-untested observations — run the test that could falsify the claim first
metadata:
  type: feedback            # user | feedback | project | reference | framework
  scope: group              # personal (lives under local/) | group (committed/PR-reviewed if the store has a remote)
  domains: []               # v2 scoped-injection filter; empty = universal, which most behavioral lessons are
  basis: tested             # observed (seen it happen) | tested (discriminating test ran) | understood (mechanism known and tested — rungs are cumulative)
  promoted_from:            # provenance: verbatim originals, archived machine-locally at promotion — the **Why:** below is the summary that travels
    - local/archive/<project-a>/feedback_verify_before_concluding.md
    - local/archive/<project-b>/feedback_check_state_before_diagnosing.md
  last_verified: 2026-06-09
---

"Verified" means a discriminating test was run, not that early observations were consistent. Absence of evidence in N places is not evidence of design.

**Why:** Recurring correction across projects: conclusions stated as verified from a few agreeing data points that couldn't distinguish the claim from the nearest alternative (e.g., "memories are never written here" vs. "no session ever tried to write one").

**How to apply:** Before writing "verified / confirmed / always / never", name the alternative hypothesis and the test that separates it from the claim. If that test hasn't run, run it or downgrade the claim to "observed so far".
```

Auto memory writes fields beyond these (`node_type`, `originSessionId`); the archived originals keep them verbatim, and `originSessionId` is worth singling out — it names the session that taught a lesson, the link `/memory-reflect` follows from a memory back to its transcript.

Frameworks carry one extra field, `applies_when` — the archetype triggers they fire on (see the memory gate for the genre's shape).

Store layout:

```
~/.claude/shared-memory/
├── MEMORY.md                 # group-adoptable index: one line per memory; always loaded via the import
├── MEMORY.local.md           # personal overlay index, same format; always loaded too (gitignored)
├── framework_memory_gate.md
├── feedback_verified_means_tested.md
├── feedback_finish_hard_paths.md
├── local/                    # machine-local, gitignored as a unit
│   ├── user_prose_style.md   # scope: personal memories live here, indexed by MEMORY.local.md
│   └── archive/              # verbatim originals of promoted memories: provenance + recovery for the promoter (a solo store may deliberately un-ignore it; a group store must not)
├── .candidates.log           # promotion queue fed by the capture hook and /memory-reflect mining (gitignored)
├── .reflect/                 # rumination state: last-reflected watermark, learned salience markers (gitignored)
└── .gitignore                # committed, seeded by /memory-init — on a fresh clone it's what keeps the local overlay out of PRs from day one
```

Flat at the top level, `type_` filename prefixes, frontmatter `name` mirroring the filename with hyphens. That's a house style, not an inherited contract: auto memory's own naming isn't even uniform on one machine (some project dirs here use `type_` prefixes, one uses prefix-free hyphenated names) — so the tooling keys off frontmatter for everything that matters and treats filenames as cosmetic. Subdirectories by domain only if the flat dir gets unwieldy.

## Write path

### The memory gate (pre-write validation)

`/memory-review` is a post-write safety net; the stronger guard sits at write time, before a belief persists. The gate models what a careful human does with a curious bit of information: gets suspicious, asks one more question. It is itself a framework — the first entry any store should contain, and this doc's example of the genre:

```markdown
---
name: framework-memory-gate
description: Fishy information earns a follow-up question, not a save — check evidential basis before persisting
metadata:
  type: framework
  scope: group              # the gate must survive cloning — a store without it is missing its first entry
  applies_when: [storing-a-memory, stating-a-conclusion]
  basis: observed           # its own honest rung: reasoned but not yet watched working — /memory-reflect's audits are what upgrade it
---

Stop before saving if any trigger fires: the fact is surprising; a single observation carries the whole claim; the mechanism is unknown (you can't say *why* it's true); it contradicts an existing memory; a shared memory already covers it (don't fork a local copy — queue a proposed extension for the next distill pass); a name or label implies a purpose nobody confirmed.

**How to apply:** When triggered, either (a) ask the collaborator the clarifying question now, (b) run the probe that would settle it, or (c) store anyway — at the honest rung (`observed` or `tested`), stating the actual evidence, with the open question written into the body so the next session inherits the curiosity instead of a false certainty.
```

Option (c) is what keeps the gate from deterring memory-writing: when curiosity can't complete now, the observation is still captured — at its honest rung, carrying its own follow-up question. One rule rides on top of the `basis` ladder: only `understood` claims may be written as rules broader than their incident; the lower rungs speak narrowly and carry their open questions. The gate itself practices option (c): it ships at `basis: observed` — a reasoned procedure nobody has yet watched catch a bad save — which keeps it on `/memory-review`'s suspicion queue until reflect's audits show it firing. A framework that graded itself `understood` before its first test would fail its own checklist. (The breadth rule governs claims; for a framework — a prescription, not a claim — `basis` rates the evidence that the procedure works, while how widely it applies lives in `applies_when`.)

v1 enforcement is soft and native: the gate's trigger line rides the always-loaded index (its body is one read away), and the distill skill honors it. If soft proves insufficient, a `PreToolUse` hook hardens it — with an honest limit. A hook cannot tell a native auto-memory write from any other `Write` or `Edit` call, and native memories carry no `basis` field, so a hard lint on project dirs would block auto memory itself; the mechanical lint ("every memory declares `basis`") is therefore enforceable only on the shared store, where every file is ours. Project dirs keep soft discipline — the gate rides the index, and the reflect audit catches what slipped. The strong form, a prompt-type hook challenging fishy saves at the moment of temptation, buys rigor at the price of an LLM call on every matching write.

### `/memory-distill` (the core skill)

1. **Scan** `~/.claude/projects/*/memory/*.md` (skipping `MEMORY.md`) plus `.candidates.log`. Worktree session dirs (`*--claude-worktrees-*`) can be skipped entirely — they contain transcripts, not memories; worktree sessions write to the main repo's memory dir.
2. **Cluster** near-duplicates and generalizable candidates — name/description similarity is enough at this scale; no embeddings.
3. **Judge** each cluster per the what-travels taxonomy: behavioral lesson (promote even from a single copy — it generalizes by nature), cross-project fact (promote on evidence of relevance beyond one project), project fact (leave alone), or already-promoted-but-diverged (merge refinements upstream — e.g., one copy records a failure mode the others lack).
4. **Merge** clusters into one shared memory, keeping the union of useful detail and recording `promoted_from` provenance. Generalize while merging: strip incident-specific details — the incident survives in provenance — and word the lesson as broadly as its `basis` earns: universal phrasing is for `understood`; below that, the entry states its scope and carries its open question. The gate applies with extra force here: promotion widens a belief's blast radius, so anything below `basis: tested` that promotes anyway must be flagged with its rung.
5. **Propose** the batch to the user before writing — promotion is judgment; the user signs off.
6. **Write** to the shared store and update the shared `MEMORY.md`, archiving the verbatim source copies under `local/archive/` — machine-local, never committed. Project memory dirs aren't git repos, so the archive is where originals survive and where the promoter can recover an over-generalized promotion from; only the promoter ever had them, and keeping them out of the remote keeps transcript-grade incident detail out of group history (the **Why:** line is the provenance that travels). Then in each source project, **delete the promoted memory and its index line, or slim it** to the project-specific remainder when a copy mixes a general lesson with genuinely local detail.

No pointer stubs: the shared index is already loaded in every session, so a pointer in a project store would be bookkeeping, not memory. What actually prevents re-learning is the gate's duplicate check at write time, with the capture queue and the next distill pass as the backstop for anything that slips through. Step 6 stays the deliberate exception to the "don't touch the fan-out" principle — the one write into project dirs — and it's a one-shot, strictly shrinking operation. One race it tolerates rather than fights: a session already live in a source project still holds the old memory in context and can rewrite what step 6 just deleted; the capture queue picks the rewrite up and the next pass re-merges it.

### Capture hook (optional but cheap)

A user-level `PostToolUse` hook on `Write|Edit`, scoped to project memory paths, appends the written file's path to `.candidates.log` — one line per event, `O_APPEND`, so concurrent sessions share the queue safely. No LLM at hook time, async, near-zero cost — it just means `/memory-distill` starts from a queue instead of a full rescan. The path scope is anchored to `~/.claude/projects/**/memory/**` so ordinary repo files in directories that happen to be named `memory/` — and the shared store itself — never enqueue; the hooks' `if` filter takes permission-rule syntax for exactly this (per the hooks docs), and the script re-checks the path regardless. The rule is spelled `Edit(...)` deliberately: Edit-family permission rules cover every built-in file-editing tool, Write included, whereas a `Write(...)` rule matches only Write calls — and Edit is the majority of auto memory's writes on this machine, so the obvious-looking spelling would silently drop most captures. M2's acceptance test probes exactly this.

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{ "type": "command", "command": "~/.claude/hooks/capture-candidate.sh", "async": true, "if": "Edit(~/.claude/projects/**/memory/**)" }]
    }]
  }
}
```

The script lives under `~/.claude/hooks/`, not inside the store — see Trust for why a syncable store must never carry executables.

### `/memory-review` (hygiene)

Walks the shared store and flags: memories past a `last_verified` age threshold, the `basis: observed` entries (a standing suspicion queue — verify and upgrade them, or retire them), pairs with contradictory guidance, and memories whose referenced artifacts (skills, repos, tools) no longer exist. Proposes verify/update/retire actions, and re-runs `/memory-init`'s doctor checks — import approvals, `autoMemoryDirectory` overrides — which can break at any time after install. As related lessons accumulate, it also proposes consolidating them up the ladder into frameworks, keeping the index short and each directive strong. No scores, no cron, no decay math — just a periodic curation pass you (or a scheduled job) run occasionally.

### `/memory-reflect` (rumination)

Humans consolidate experience during downtime — replaying recent events, dwelling longest on the emotionally charged ones. This is the deliberate version: read recent session transcripts (already on disk under `~/.claude/projects/` — including the worktree dirs distill skips — the raw record of dialogue and tool calls) and reconcile them against the store. Auto memory captures only what in-the-moment Claude thought to save, which filters every lesson through the very judgment being corrected; transcripts hold the corrections that evaporated at session end — experience one rung below the ladder's `incident`.

Two audits per reviewed session, both proposing rather than writing (gate and sign-off apply, as everywhere):

1. **Recall audit** — with the shared index in hand, find moments where a lesson or framework should have fired but visibly didn't. Hindsight without task pressure turns "should something have fired?" into checklist matching. Each miss carries a diagnosis — index line too terse, lesson never promoted, `applies_when` too narrow, saturation — and each diagnosis maps to a remedy. This is the active measurement for the index-recall-efficacy open question.
2. **Mining** — corrections and discoveries that never became memories: user pushback, repeated instructions, error-and-recovery sequences, genuine surprises. Candidates join the same queue `/memory-distill` consumes.

Cost is managed by reading selectively. A watermark under `.reflect/` marks the last-reflected session (an optional `SessionEnd` hook can record finished sessions there), and a salience grep pre-filters for the moments worth reading in full. The closest transcript parallel to emotional encoding in human memory is the dialogue's friction and concession markers: user corrections ("no, that's wrong", "I told you"), Claude's own concessions ("you're right", "I overclaimed"), interruptions, permission denials, repeated tool failures. The marker set starts standard and is itself learnable — reflection notices which markers actually preceded promoted lessons for this collaborator and proposes additions.

Deliberately v1-simple: recent sessions, standard markers, top findings only — learn from running it before building more. Self-review by the same model shares the model's blind spots; that's accepted as best-effort — humans don't ruminate consistently or perfectly either, and the commonest finding (an explicit correction) needs no subtle judgment to spot.

## Sharing the store (Tier 3, optional)

Any group — a team, an org, a public community — can share a store by agreeing on a git remote. The tooling stays generic; the remote is configuration.

- Each member's `~/.claude/shared-memory` is a clone of the agreed remote. The store repo belongs to the group, not to this project.
- Upgrading an existing solo store to a group remote is a review event, not a `git push`: the accumulated history was never PR-reviewed, so `/memory-init` re-roots it — current contents go up as a fresh, reviewed initial commit; the old history stays local.
- `scope: group` memories are committed and PR-reviewed. The review asks three questions: is it true for everyone; is it safe to share (no credentials, no personal info, no single-user assumptions like hardcoded usernames unless parameterized); and is it safe to *load* (see Trust, below)? A second filter: general lesson, or one collaborator's preference? Preferences stay `scope: personal`. Raw incident detail stays out of the diff: verbatim originals remain in the promoter's `local/archive/`, and the only incident context the PR carries is the memory's one-line **Why:** — a leak there is catchable and revertable; one in a committed original is in group history for good.
- `scope: personal` memories stay personal mechanically, not by promise — the gitignored `local/` + `MEMORY.local.md` arrangement from Recall path. One line per memory keeps the committed `MEMORY.md`'s merge conflicts line-local and rebases trivial.
- A `/memory-init` setup (one-time, per user) creates the store — seeded with the memory-gate framework and the committed `.gitignore`, or cloned from a remote if given one — adds the two `@import` lines to `~/.claude/CLAUDE.md`, places the capture-hook script under `~/.claude/hooks/` (never inside the store — see Trust), and adds the permission rules: the store's read + ask-to-write pair, plus `~/.claude/projects/**` reads and memory-dir edits for `/memory-distill` and `/memory-reflect`, which otherwise hit a wall of prompts on their first run. When cloning, it seeds nothing the clone already provides — which never includes the gitignored files, so `MEMORY.local.md` and `local/` are created unconditionally (a clone lacks them, and what a dangling `@import` does is undocumented) — and refuses any executables the clone tries to provide. It ends with a doctor pass: per-project import-approval state (is recall actually loading everywhere?) and `autoMemoryDirectory` overrides (do the capture and scan scopes still cover every memory dir?). Solo and group installs are the same flow, differing only in whether a remote URL is passed.
- Freshness: a `SessionStart` hook warns when the clone is behind its remote (current only as of the last `git fetch` — run one async in the hook, or accept the staleness); pulling stays a deliberate act (see Trust). A pull only reaches the *next* session anyway — imports resolve at launch — and stale-by-a-few-days is acceptable; this is curated knowledge, not state.

The natural distribution form for the tooling itself is a Claude Code **plugin** — plugins bundle skills + hooks + scripts in one namespaced, marketplace-distributable package — so "anyone can use it" reduces to: install the plugin, run `/memory-init`.

### Trust: a memory is a loaded instruction

The gate and `basis` interrogate whether Claude's own beliefs are true; this section is about whether incoming beliefs are safe. The store feeds always-loaded context — its index lines load into every session, and memory bodies are one pre-approved read away — making it the highest-leverage prompt-injection surface a session has: whoever can land text in it steers every future session of every member. Treat the store like code, because it runs like code:

- The group review bar is "safe to load", not just "safe to share": read a candidate memory as instructions Claude will follow and ask what behavior it licenses — weakening the gate, exfiltrating context, "always run this command" — before asking whether it's true.
- No auto-pull. An automatic `SessionStart` pull turns a compromised remote into a compromise of every member's next session; the default keeps a human between the remote and the context window.
- The store carries no executables. Tooling lives in the plugin (pre-plugin: `~/.claude/hooks/`); gitignore is no defense on a fresh clone, so a `bin/` arriving via remote is a supply-chain payload, and `/memory-init` treats it as one. The contrast is the point: plugins have a documented executables channel (`bin/`, added to the Bash `PATH`), and that's fine because plugin *install* is the trust ceremony — the store syncs with none, which is exactly why it must never grow one.
- A group remote gets the protections of any repo whose contents execute: protected branch, required review, no direct pushes.
- Public stores are out of scope until provenance and pinned, reviewed versions exist — adopting strangers' standing instructions is a supply-chain decision, not a `git clone`.

## Platform assumptions

The design leans on platform behavior observed on Claude Code 2.1.170 — most of it emergent convention rather than documented contract. Applying this doc's own vocabulary to its foundations:

- **The file conventions are emergent, not contractual — and not even uniform** (`basis: observed`). The frontmatter `type` field and index-line format are how current auto memory happens to write — not a published schema — and this machine's own project dirs already disagree on filename style (`type_` prefixes in some, prefix-free hyphenated names in another) while carrying undocumented fields (`node_type`, `originSessionId`). So the tooling trusts frontmatter, ignores filenames, and `/memory-review` sanity-checks that the conventions still hold.
- **Memory writes are hook-visible** (`basis: tested` — live transcripts show auto-memory files written via ordinary `Write` and `Edit` tool calls, with Edit the majority). This is what makes the capture hook possible — and why its filter must match the Edit family, not just Write. It's implementation detail, not contract: a future dedicated memory tool would bypass the matcher, and the queue then degrades gracefully to `/memory-distill`'s full rescan.
- **Transcripts persist and are readable** (`basis: tested` — read directly throughout this design's research): each session's `.jsonl` lands under `~/.claude/projects/<project>/`, which is what `/memory-reflect` mines. Location and format are emergent, not contract. They omit the system prompt, but CLAUDE.md and hook-injected content arrive as user-message records — so reflection sees the dialogue, the tool calls, and most injected context.
- **User-level imports load without per-project approval — verified headless only.** The docs document an approval dialog for "external imports in a project" and warn that *declining disables those imports permanently for that project* — which would silently kill recall there. What's unestablished is whether a user-level file importing a user-level path counts as external. Headless probes (`basis: tested` for headless: a brand-new and a known project both loaded the sentinel, approval ledger untouched) can't show dialogs, so they can't discriminate; M0's first interactive sessions are the test — approve in one fresh project, decline in a throwaway second.
- **Import-approval state is inspectable** (`basis: observed` — per-project flags like `hasClaudeMdExternalIncludesApproved` sit in `~/.claude.json` on 2.1.170). Entirely undocumented, so the doctor check that reads it to detect recall-dead projects must fail soft if the key moves.
- **Subagents inherit the import** (`basis: tested` — a fresh headless session and the subagent it spawned both saw the sentinel). Resolution happens at parent launch: a session started before the import existed spawns subagents without it — so installing the store reaches new sessions, not already-running ones.
- **Worktrees share the main repo's memory dir** — documented contract (the memory docs state all worktrees in a repo share one auto-memory directory), independently confirmed by probe. This is why `/memory-distill` skips `*--claude-worktrees-*` dirs while `/memory-reflect` reads them.

One adjacent, documented knob this design deliberately doesn't use: `autoMemoryDirectory` relocates a project's auto memory wholesale, so pointing every project at one directory would merge project facts that should stay separate. The goal is selective promotion — the shared store sits beside auto memory, not in place of it. The knob still bites if someone else sets it: a relocated memory dir falls outside both the capture hook's path scope and `/memory-distill`'s scan glob, silently blinding capture and promotion for that project — which is why the doctor pass reads the setting and widens both, or warns.

## Roadmap

- [ ] **M0 — store + recall (v1):** create `~/.claude/shared-memory/` as a standalone user-owned store, seed it with the memory-gate framework plus the clearest behavioral lessons already sitting in this machine's project memories (dogfood data, not part of the project), add the `@import` lines + permission rules (store read + ask-to-write; `~/.claude/projects/**` reads and memory-dir edits for the later skills). The first interactive sessions double as the discriminating test for the import-approval question — approve in one fresh project, decline in a throwaway second — plus one more cheap probe: launch once with `MEMORY.local.md` deleted, since missing-import behavior is undocumented. *Value on day one with zero code.*
- [ ] **M1 — `/memory-distill` skill:** scan, cluster, propose, promote, archive + delete-or-slim the sources — gate-aware throughout (every promotion carries an honest `basis`). Run it against this machine's existing project memory dirs as the acceptance test.
- [ ] **M2 — capture hook + `/memory-review` + `/memory-reflect`:** candidate queue (acceptance: the `Edit(...)` if-filter catches Edit *and* Write calls); staleness pass + `basis: observed` suspicion queue; lesson→framework consolidation; transcript rumination (v1: watermark, standard salience markers, recall audit + mining); optionally the gate-hardening hook on memory writes.
- [ ] **M3 — `/memory-init` + packaging:** one-command setup (create or clone the store, seed the gate + `.gitignore`, wire the imports and permission rules, run the doctor pass), solo→remote upgrade that re-roots history, PR conventions for `scope: group`, behind-remote warning hook; package the skills + hooks as a Claude Code plugin.
- [ ] **M4 (if needed) — scoped injection (v2):** `SessionStart` hook with domain filtering, replacing the flat import when the index's token cost warrants it.

## Open questions

- **Index recall efficacy and budget:** v1 recall rests on one-line index entries carrying enough signal to fire mid-task, and on the list staying short enough that entries don't dilute each other — too little per line and too many lines fail identically: the lesson exists but doesn't fire. The index is also the only *in-the-moment* defense against re-learning a promoted lesson; the capture queue and the next distill pass only correct after the fact. Measured passively by already-promoted topics reappearing in the capture queue, actively by `/memory-reflect`'s recall audit. Remedies, escalating: richer index lines; consolidation into frameworks; v2 ranked injection when line count (dilution) or token cost × fan-out (budget) says so (`/memory-review` tracks both).
- **Gate strictness:** too lax and unvalidated beliefs persist; too strict and saving a memory becomes a research project, so the system stops remembering. The dial probably scales with blast radius — a project memory can afford `basis: observed`, while an `observed` lesson promoting to `scope: group` ships narrowly worded, flagged, and on the suspicion queue rather than not at all — but whether that's strict enough is unproven.
- **Multi-machine:** the git clone syncs `scope: group` memories for free, but nothing else travels — the personal overlay is gitignored (so the scope defined as one person's own is, in v1, also machine-bound until `local/` gets its own private remote or branch), and auto memory and transcripts stay local too, so reflection only ruminates over this machine's experience. Which of those gaps is worth closing first?
