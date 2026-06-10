---
name: memory-reflect
description: Ruminate over recent session transcripts and reconcile against the shared store at ~/.claudemods/shared-memory — audit recall (lessons that should have fired but didn't) and mine friction moments (corrections that never became memories) into distill-queue pointers, both proposing with sign-off
---

# memory-reflect

You are running the rumination pass — reading recent session transcripts and reconciling them against the store. Two jobs, **both proposing** (nothing lands without sign-off, you stay in the loop): a **recall audit** (did the memories that should have steered a session actually fire?) and **mining** (friction moments where a correction happened but never became a memory). Before anything else, read the store's doctrine — `~/.claudemods/shared-memory/framework_memory_gate.md`, `reference_store_memory_format.md`, `framework_write_for_the_cold_reader.md`, `framework_a_guess_is_not_a_fact.md`, and `framework_reflect_memories_back.md` — and follow it throughout.

The scan script ships in this plugin's `bin/`, on the Bash PATH while the plugin is enabled — invoke it by bare name.

## 1. Scan (script)

Run the miner and read its report. It writes its side effects — the queued pointers and the watermark — into the store itself, so you need no scratch dir, only its summary:

```
reflect-scan
```

`reflect-scan` owns the `.reflect/` state — an append-only `ends.jsonl` (the `SessionEnd` hook records each ending transcript + reason) and a `watermark.json` it keeps (per-transcript 0-based record offset + a `final` flag). It discovers sessions by scanning the transcript dir *and* the recorded ends — so a session the hook missed is still mined — walks each transcript **only past its watermark**, appends `reflect-pointer:` lines to the distill queue for the salient moments it finds, and reports `{scanned, swept, pointers_emitted, by_marker}` — each `scanned` entry also carrying the session's recovered `cwd` and its repo's mapped `domains`, the inputs for §2's domain-miss audit. It **never excerpts** — a pointer is `reflect-pointer:<session-path>:<record-offset>:<marker>`, a lead to a moment, never transcript text. A `swept` entry is a recorded transcript already aged out by `cleanupPeriodDays` — expected, not an error.

The markers are leads, not verdicts: `concession` (the assistant conceded — the high-precision mirror of a correction), `interruption`, `permission-decline`, `repeated-tool-failure`. Each marks a *moment*; whether a *lesson* lives there is your judgment in §3.

## 2. Recall audit (judgment)

Over a sample of the scanned sessions, read the work and ask: **did a store memory that should have steered this session actually fire?** A miss is a lesson that exists but wasn't applied — usually a **weak trigger** (the index description doesn't fire in the moment it's needed) or a scope mismatch, not a missing memory. Surface each miss with its evidence (the session moment + the memory that should have caught it). The fix is a belief edit — sharpen the index description or re-scope — proposed in §4, written via the file tools, reflected back. Don't manufacture misses: if recall looks healthy across the sample, that clean finding **is** the right result — say so.

**Domain-miss (M4).** Domain-scoped recall introduces a miss the old flat index never had: a memory that didn't load *at all* because the session's repo isn't mapped to its domain — a silent false-negative, not a weak trigger. Each `scanned` entry carries the session's `cwd` and its repo's mapped `domains` (what *did* load beyond the always-on general+personal tier). For a sampled session, a domain-scoped store memory whose `domains` don't intersect that set did **not** load there; read the work and check whether any such memory was actually relevant. If one was, that's a domain-miss, and the fix is a *judgment* about which side is mis-scoped: either the repo should carry the domain — `memory-init --map-repo-add <domain>`, a machine-local **config** change, not a belief (propose it for the collaborator to run) — or the memory is over-narrow and should shed or broaden a domain (a **belief edit** via the file tools). **Honest limit:** this only catches a miss that left friction in the transcript; a fully-silent domain miss — the relevant lesson simply never surfaced and nobody noticed — escapes transcript audit, bounded only by conservative tagging (a `[]` memory always loads) and distill's create-time domain guardrail.

## 3. Mining (judgment)

Each pointer marks a friction moment where a correction *may* have happened. For each fresh pointer, **read the transcript region at its offset and widen** — read the surrounding turns, because the marker points at the moment and the lesson (if any) is in the exchange around it. Then judge with the memory gate and the promote-on-relevance bar: is there a **durable, generalizable lesson** here that never became a memory? Most markers are noise — a concession mid-exploration, a transient tool failure, a one-off interruption teach nothing; name them noise and move on. Keep only the moments that carry a real, reusable lesson.

## 4. Propose, then hand off (review gate — never skip)

Present both halves for sign-off:

- **Recall-audit fixes** — memories to strengthen/re-scope/retire, each with evidence. On sign-off these are **belief edits via the file tools** (harness-visible, reflected back); a retirement goes through `/memory-review`. A **domain-miss** fix may instead be a repo-map change (`memory-init --map-repo-add <domain>`) — machine-local config the collaborator runs, not a belief; if the fix is a memory re-tag (change its `domains`), that's a belief edit, and re-render projections after with `gen-projections`.
- **Mined leads** — reflect-scan queued a `reflect-pointer:` for every salient moment it found; from your §3 read, call out which look like real lessons (session + marker → the lesson) and which are noise, so the collaborator knows what's worth distilling.

Then send the collaborator to **`/memory-distill`** — the judgment core that adjudicates *every* queued pointer: it reads each pointed-to region, applies the full promote/leave taxonomy and adversarial review, promotes the real lessons, and records **each** consumed pointer's queue-line offset in `queue_remove` — a dismissed lead retires from the queue too, so nothing lingers. Reflect **discovers**; distill **promotes**: every belief-write stays on one reviewed path, never duplicated here.

## Hard rules

- **Never excerpt transcript content into the store** — pointers are leads and the store holds only *distilled* lessons, never quoted dialogue. Emotional/verbatim transcript text belongs to the archive, deliberately, not to a memory.
- The gate applies to you: a mined "lesson" with no grounding beyond one heated moment is not a memory — leave it.
- Never write or modify a `doctrine: true` file.
- A clean recall audit and a queue of well-chosen leads is a complete, successful pass — mining nothing durable from a noisy week is the honest outcome, not a failure to hit a quota.
