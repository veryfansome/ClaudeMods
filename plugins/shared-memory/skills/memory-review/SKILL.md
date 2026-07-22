---
name: memory-review
description: Curate the shared store at ~/.claudemods/shared-memory — scan for staleness, the observed-verification queue, contradicted quarantine, dangling references, and lesson→framework consolidation; then verify / update / retire / consolidate with sign-off, writing beliefs via the file tools and mechanical retires via the apply script
---

# memory-review

You are running the curation pass of the shared-memory store — the hygiene safety net behind distill and reflect. The mechanical front (`review-scan`) and back (`review-apply`) are scripts; you decide *what* changes, they execute *how*. Before anything else, read the store's doctrine — `~/.claudemods/shared-memory/framework_memory_gate.md`, `reference_store_memory_format.md`, `framework_write_for_the_cold_reader.md`, `framework_a_guess_is_not_a_fact.md`, and `framework_reflect_memories_back.md` — and follow them throughout. This is a *proposing* pass: nothing is written without sign-off, and you stay in the loop.

The scan and apply scripts ship in this plugin's `bin/`, on the Bash PATH while the plugin is enabled — invoke them by bare name. Two channels, exactly as in distill: **beliefs** (a new framework, an edit, a tombstone) go through the **file tools** so every write is harness-visible and reflected back; **mechanical ops** (archive → de-index → delete → ledger) ride **`review-apply`** with a signed-off plan.

## 1. Scan (script)

First mark the review-run window, then create a working directory and scan into it — never read a review or plan file you did not write this run:

```
STORE=~/.claudemods/shared-memory
printf '{"event":"start","ts":%s}\n' "$(date +%s)" >> "$STORE/.runs.log"   # this run's bulk body reads are excluded from the recall signal
RUN=$(mktemp -d /tmp/review.XXXXXX)
review-scan > "$RUN/review.json"
```

The `.runs.log` `start` marker must be written **before** you read any memory bodies (§2's dangling-ref checks Read them, and every store-body Read is logged): it tells `review-scan`'s recall signal to ignore reads made *during a review*, so a curation pass doesn't flag every line it inspected. Write the matching `stop` at cleanup (§7).

`review-scan` enumerates the **live** memories from the host-store indexes (`MEMORY.md` + `MEMORY.local.md`, **plus the generated `MEMORY.<domain>.md` / `MEMORY.local.<domain>.md` projections**) — not a recursive walk; deduped by body path, so a memory in several projections is counted once — and returns, per its documented schema: each memory's `basis`/`type`/`last_verified`/`stale`/`doctrine`/`domains`/`supersedes`; `flags` (the `observed` queue, the `contradicted` quarantine, `supersedes_live` guard-2, advisory `similar` clusters); `reconcile` (two diffs — `orphan_bodies`/`dangling_index`/`desc_drift`/`contradicted_not_tombstoned`/`tombstoned_not_contradicted`, and half-done retires from `archive` stamps); and **`recall`** (M3: per-memory on-demand body-read counts, review-run windows excluded, with `enrich_candidate` set for a `feedback`/`framework` line that got read). Retire/consolidate still rest on staleness, `basis`, and judgment — the recall signal only ever proposes **enriching** a line, never retiring a memory.

## 2. Triage the mechanical flags

Read the scan whole; the flags are leads, not verdicts:

- **`stale`** (past the `last_verified` age threshold; doctrine is exempt) — re-run the memory's **Verified:** check and refresh the date, or, if it no longer holds, retire it.
- **`observed` queue** — memories that never faced a check. For each: give it the check it lacked (→ `verified`, document it, date it), or retire it. Judgment, not a script.
- **`contradicted` quarantine** — adjudicate: re-verify (flip back to `observed`/`verified`) or retire.
- **`supersedes_live`** (guard 2) — a consolidation framework whose `supersedes` still names a *live* memory: a consolidation wrote the framework but never finished retiring its sources. Complete the retire (§5).
- **`reconcile.dangling_index` / `orphan_bodies`** — a half-written belief: an index line with no body, or a body no index line loads. Finish it (write the missing side) or revert.
- **`reconcile.desc_drift`** — a flat `[]`-index line no longer carries the body `description` verbatim; realign one to the other (a belief edit). (Projection lines can't drift — they're generated.)
- **`reconcile.title_drift`** — a flat `[]`-index line's display title no longer matches the body `title` (M4: the line renders from the body); realign one to the other (a belief edit).
- **`reconcile.contradicted_not_tombstoned`** — **urgent**: a contradicted memory still steering because its index line wasn't tombstoned. Tombstone it now (§4).
- **`reconcile.archive`** — half-done retires (`pre-deindex`/`pre-delete`/`pre-ledger`): re-run the retire; it completes idempotently (the no-clobber archive makes re-archiving a no-op).
- **`recall` (M3 body-read insufficiency)** — a memory whose body was read on demand: for an `enrich_candidate` (a `feedback`/`framework` line that fired weakly enough that a session had to open the body), propose **enriching its index line** so it steers next time — a belief edit (§ file tools, sign-off), **never** a retirement. A `reference` entry is shown for context only (terse-index + rich-body is its design, so a body read is expected use, not a defect). Absence of reads is **not** a signal: a line that fires from the index alone is healthy.

## 3. Propose the batch (review gate — never skip)

Before writing anything, present the proposed actions to the collaborator: which memories verify/update/retire, and any consolidation. Sign-off is the gate; promotion and retirement are judgment the collaborator owns. Only after sign-off do you write.

## 4. Quarantine-on-discovery (a standing action, tombstone-first)

The moment *any* session finds a memory wrong — not only during this pass — quarantine it, **index line first**: replace the line with the tombstone

```
- [contradicted — pending review](<path>)
```

— the parseable link kept (so review still enumerates it), a fixed marker title, no trailing guidance — **then** flip the body's `basis: contradicted`. Tombstone-first because the *index line* is what loads and steers: the urgent protective act precedes the bookkeeping, and an interruption can't leave a still-steering line. Both writes are beliefs (file tools, harness-visible, reflected back). This tombstone-first order is for a **flat `[]`-index memory**.

A **domain-tagged** memory has no flat line — its line is a *generated* projection, and hand-editing a projection file is forbidden — so quarantine it **body-first** instead: flip the body's `basis: contradicted` (a belief edit via the file tools), **then** run `gen-projections`, which re-renders that memory's projection line as the tombstone from the body (§7). This is the reverse of the flat order and strictly weaker: between the body flip and `gen-projections` the **old projection line keeps loading and steering**, so run `gen-projections` immediately — a quarantine done *outside* a review pass must still run it. If interrupted before it runs, the next review's `reconcile.contradicted_not_tombstoned` (the contradicted body's projection line isn't a tombstone) and the doctor's `projection-integrity` both catch the un-regenerated projection.

## 5. Consolidation (lesson → framework)

Fold N related top-level lessons into one framework, retiring the N as superseded. Order and guards matter — the framework must exist and bind the retire, or a crash leaves the framework plus all N sources live (dilution):

1. **Draft the framework under `$RUN/` first.** Write it there (with its `supersedes: [names]` list naming exactly the N sources, and a **`title`** frontmatter field — the human display title for its index line, per the format doctrine; `title` and `description` both live in the body) and record `framework_sha256` = the hash of that exact draft in the plan.
2. **Sign-off**, then write the framework belief via the **file tools**, **byte-identical** to the draft (new file + its index line), so `review-apply`'s CAS matches. `review-apply` never writes the framework — beliefs are the file tools' job.
3. **`review-apply` the consolidation plan** (below): it CAS-checks the framework against `framework_sha256`, refuses to retire any source not named in the framework's `supersedes`, then archives → de-indexes → deletes → ledgers each source (each CAS'd against its scan-time hash; a stale one skips, exit 3).

## 6. Apply the mechanical ops (script)

Build the plan at `$RUN/plan.json` per the schema in `review-apply --help` — `retire` (each: `name`, store-relative `path`, `sha256`, `remove_index_lines` [`{index, lines}` per containing index], `reason`, `archive_dest`) and/or `consolidate` (each: `framework`, `framework_sha256`, `supersedes`, `sources`). Validate before applying:

```
review-apply --validate "$RUN/plan.json"
review-apply "$RUN/plan.json"
```

Exit 0 = clean; exit 3 = partial (some items skipped and reported — re-queue or rebuild the plan); exit 2 = the store lock is held. The guard (in the shared module) resolves-then-verifies every path under the store, confines the archive dest under `local/archive/`, and refuses to touch a `doctrine: true` file or its index line — so curation can never delete or mint doctrine.

## 7. Clean up

If this pass edited a **domain-tagged** memory's body (a `title`/`description` realign, a `basis: contradicted` flip) or retired one, re-render the projections so the change reaches its `MEMORY.<domain>.md` (a contradicted domain body renders as its tombstone; a retired one's line drops when its body is gone):

```
gen-projections
```

**Check its exit code** — a non-zero exit means nothing was re-rendered: usually a refusal (a body left without a `title`, an unparseable body, or an invalid domain tag; named on stderr), occasionally a transient store-lock-held (retry). Inspect stderr, resolve the named body, and re-run rather than closing the pass with stale projections (a quarantine flip whose projection never regenerated keeps the old line steering — §4).

Then close the review-run window and delete the scratch dir:

```
printf '{"event":"stop","ts":%s}\n' "$(date +%s)" >> "$STORE/.runs.log"
rm -rf "$RUN"
```

Always write the `stop` (even if the pass found issues) so the window closes; an unclosed `start` is only bounded by a fixed cap, which would over-exclude later reads. The store's own `.gitignore` keeps `local/` and the machine-local state (`.recall.log`, `.runs.log`, …) out of any remote.

---

**Deferred (M3):** the doctor re-run (recall health, `autoMemoryDirectory`, wiring drift, `git check-ignore`, version) folds into this pass when `--doctor` ships.
