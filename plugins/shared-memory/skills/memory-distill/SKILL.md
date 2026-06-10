---
name: memory-distill
description: Promote generalizable lessons from per-project auto-memory dirs into the shared store at ~/.claudemods/shared-memory — scan, cluster, judge, adversarially review, present the batch shape for early veto, then draft, propose for sign-off, and write beliefs via file tools with the mechanical apply script
---

# memory-distill

You are running the judgment core of the shared-memory distillation pass. The mechanical front and back are scripts; you decide *what*, they execute *how*. Before anything else, read the store's doctrine — `~/.claudemods/shared-memory/framework_memory_gate.md`, `reference_store_memory_format.md`, `framework_write_for_the_cold_reader.md`, `framework_a_guess_is_not_a_fact.md`, and `framework_reflect_memories_back.md` — and follow them throughout.

The scan and apply scripts ship in this plugin's `bin/`, on the Bash PATH while the plugin is enabled — invoke them by bare name.

## 1. Scan (script)

Mark the run window, then create a working directory and scan into it — never read a candidates or plan file you did not write this run (a leftover from an earlier run is stale by definition; delete it or ignore it):

```
STORE=~/.claudemods/shared-memory
printf '{"event":"start","ts":%s}\n' "$(date +%s)" >> "$STORE/.runs.log"   # window this run's store-body reads out of the recall signal
RUN=$(mktemp -d /tmp/distill.XXXXXX)
distill-scan > "$RUN/candidates.json"
```

The `.runs.log` `start` marks the run so the reads you'll make of existing store memory bodies (merging a fold, citing an antenna in §6) aren't miscounted as index-line insufficiency by `/memory-review`'s recall audit — the same window `/memory-review` writes for its own housekeeping reads. Write the matching `stop` at cleanup (§8). Bash calls don't share shell state — note the literal directory mktemp printed and use it wherever `$RUN` appears below.

Read the output — all of it. It opens with a `summary` (total candidates, counts per project): state those numbers; they are the ledger your batch shape must reconcile to. Candidates carry frontmatter (parsed fail-soft — investigate any `parse_flag` rather than dropping the file), a `sha256` for apply-time staleness checks, advisory `similar` hints, and `retired_name_match` tripwires. `retired_ledger` is attached whole.

A candidate may also carry **`recall_miss`** (`{matched, score}`): it re-appeared on the queue after the memory it names was *already promoted*, so that memory's index line didn't fire and the lesson got re-learned. Treat it as a **leave + enrich** signal, not a promote — the store already has this lesson; surface "enrich `<matched>`'s index line so it fires next time" in the batch (a recommendation for `/memory-review`, which owns index-line belief edits), and do not re-promote the duplicate. (It's an *inference* from re-capture, never grounds to retire anything.)

Alongside `candidates` the scan carries a `queue` array — the classified candidate queue. `file` entries are the project memories already folded into `candidates`; the rest are **`passthrough`** entries (`reflect-pointer:` leads that `/memory-reflect` mined from session transcripts) and **`stale`** entries (dead paths). Each carries a **queue-line `offset`** — its integer position in `.candidates.log`, which is what you put in `queue_remove` to consume it (a `file` entry carries every offset that collapsed into it).

## 2. Cluster (judgment)

Judge over the flat list. Treat `similar` scores as hints, never partitions — two drifted copies of one lesson may score low; read names, descriptions, and bodies. Group candidates that teach the same lesson.

## 3. Judge each cluster (judgment)

Distill's only output is **promotion** — `promote` (new memory), `antenna` (new recall surface citing an existing memory), or `fold` (extend an existing one). Every candidate that isn't promoted is **left** (it stays a project memory, untouched — only a *promoted* source is ever removed, and only after it's archived) or, when the call is genuinely unclear, **flagged** for the user. Promote, leave, or flag — nothing else.

Per the what-travels taxonomy:
- **Behavioral lesson** → promote, even from a single copy.
- **Collaborator preference** → promote into `local/` (personal by location). The test is whether its truth depends on the person, not how principled the wording sounds: if a different collaborator could legitimately want the opposite (comment style, doc length, pacing), it is a preference — `local/` — even when phrased as a general rule.
- **Cross-project fact** → promote only on evidence of relevance beyond one project, as `reference`, tagged `project_<name>` in `domains` when bound to specific projects.
- **Project fact** → leave alone — including lessons that only make sense from one repo's vantage point: when "the repo", "the infra", or "the team" silently means *this project's*, it is a project fact wearing general words.
- **Already promoted but diverged** → merge the refinement into the existing store memory (extend it; don't fork) — unless the existing memory declares `doctrine: true`: doctrine is out of distill's scope, so put a doctrine-revision recommendation in the proposal — addressed to the maintainers at https://github.com/veryfansome/claudemods — instead of an edit in the plan.
- **Variant of an existing store memory** (doctrine included) → apply the trigger-surface test. If the candidate would fire in the same moments as the existing entry, fold it in — or recommend, for doctrine; two index lines competing for one moment is dilution. If it *extends* the trigger surface — domain vocabulary or situations the existing line won't fire on — keep it separate as an additional antenna for the same principle: its body carries only the novel content plus a citation of the existing memory by title, never a restatement of what that memory already says.
- **Suspected inaccuracy** → a candidate that looks *wrong*, not just narrow or duplicated: it contradicts another candidate, contradicts a store memory, or states as fact what its own grounding can't support. Don't promote over the doubt. Flag it in the batch shape with the evidence — which memory it contradicts, or the basis its source can't carry — for the collaborator to resolve (quarantine, downgrade, retire, or fix-and-promote). You flag; you don't silently correct.
- Check every cluster against `retired_ledger` by meaning, not just name — a candidate matching a retired entry is not re-promoted unless new evidence answers the recorded retirement reason; say so explicitly in the proposal if you re-promote.
- **Queued pointers (`passthrough`).** A `reflect-pointer:<session-path>:<record-offset>:<marker>` is a mined *lead*, not a candidate file — its evidence is a transcript region, not a memory body. Mind the **two distinct offsets**: the queue entry's own `offset` field is its `.candidates.log` line index (what you put in `queue_remove`); the `<record-offset>` *inside the pointer string* is where to read in the transcript. Parse the string (strip the `reflect-pointer:` prefix, then `rsplit(":", 2)` → path, record-offset, marker), read the transcript from that record and **widen** to the surrounding turns, and judge by the same bar: did the marker's moment reveal a durable, generalizable lesson? Promote it — its source is the transcript, so there is **no archive and no `source_ops`**, only the belief plus the consumption — or dismiss it. **Either way, put the queue entry's `offset` in `queue_remove`**: a promoted *and* a dismissed lead both retire from the queue, so a looked-at pointer is never re-surfaced. A `stale` queue entry is dead — `queue_remove` its offset with no other action.

**The promotion bar is demonstrated cross-project relevance, not generality.** The shared index loads into every session of every project, so each line costs signal everywhere — a claim that is *true everywhere* but *seen in only one project* has not earned its slot. Behavioral lessons and collaborator preferences are the deliberate exceptions above: they generalize by nature (or belong to the person), so a single copy travels. But a fact, reference, or tool/platform detail that merely *sounds* general stays a project memory until a second project shows it recurs — that recurrence is the evidence, and a later pass promotes it then. "It's generally true" is not that evidence, and reaching for it is the signature of the promote-vs-leave flip-flop: when a single-project fact tempts you with "but this applies everywhere," leave it. Leaving is reversible on the next sighting; a diluted always-loaded index is not.

## 4. Adversarial review (independent critic)

Before presenting, spawn a *fresh* subagent to argue against your dispositions, reviewing the primary evidence rather than your summary of it. Give it the doctrine and this taxonomy (the promote-on-relevance bar, the preference test, the trigger-surface test, the suspected-inaccuracy bar), `$RUN/candidates.json` (each candidate's full body — the evidence you judged), and your proposed disposition for every candidate. Have it press on:
- each promote: does it earn an always-loaded slot, or is it a generally-true single-project fact that should leave? is top-level vs `local/` right by the preference test?
- each consume / covered-by-doctrine: actually covered, or does the source carry something novel that would be lost?
- consolidation: are any two promotes the same lesson from two angles? any missed duplicate, contradiction, or `retired_ledger` match?
- fidelity: does each disposition match what the candidate's body *actually says*? — catch mischaracterizations.

Reconcile its dissents: apply the ones that are clearly right (noting in the shape what changed and why), and carry the contested ones and judgment calls into step 5 as open flags. Never let it silently change a disposition; never drop a dissent without surfacing it.

## 5. Present the batch shape (review gate — never skip)

Before drafting any wording, show the collaborator the shape and wait for direction. Use this fixed layout every run, so the review reads the same each time (numbers and names are illustrative):

```
## Batch shape — 76 candidates

Accounting: 4 consumed → 3 promotions (1 promote · 1 antenna · 1 fold) · 71 left · 1 flagged   (4 + 71 + 1 = 76)

### New store memories — promote / antenna
| # | action | description | location · domains | cites | consumes |
|---|--------|-------------|--------------------|-------|----------|
| 1 | promote | New shared-memory description | top-level · [] | — | feedback_a.md, feedback_b.md |
| 2 | antenna | New recall-surface description | top-level · [gcp] | framework_a.md | note_d.md |

### Extended existing memories — fold
| # | into | what's added | consumes |
|---|------|--------------|----------|
| 3 | framework_a.md | Refinement description | reference_c.md |

### Left in projects — 71

### Flags & notes
- #4 flag — <candidate>: contradicts `reference_z.md` on <point>, or a borderline promote-vs-leave — you decide
- #1 re-promote — answers RETIRED.md "<reason>" with <new evidence> — confirm
- doctrine — <candidate> suggests refining `framework_x`, which distill can't touch — recommendation for the maintainers
```

The Accounting line counts every scanned candidate exactly once — consumed (merged into a promotion), left, or flagged — and must equal the scan total; if it doesn't, you haven't read the whole file, so re-read before presenting. Each description is self-contained: it reads on its own and names its subject (never "the same principle" / "the above"), and for a promote or antenna it previews the memory's eventual index line. `location · domains` is the v2 projection the memory routes to (`top-level · [..]` or `local/ · [..]`); `cites` is filled only for an antenna — the existing memory it extends. Leaves are a count only, not enumerated — so the `#` is just a handle for pointing at a shown cluster (veto #2). **Flags & notes** carries everything that needs the user's eyes beyond a clean promote or leave: suspected inaccuracies and borderline calls (the user decides), re-promotions answering a `RETIRED.md` reason (confirm), and doctrine-revision recommendations (out of distill's scope — addressed to the maintainers). Keep every cell terse — judging the shape must not require wading through prose. Feedback lands cheapest here; no wording exists yet. Do not draft until the shape is approved.

## 6. Merge and draft (judgment)

Compose under `$RUN/drafts/`, never in the store — the store stays untouched until step 8, and permission prompts on draft files are scratch noise, not belief writes. One memory per cluster. Strip incident specifics (the incident survives in the archive); follow write-for-the-cold-reader; tag `domains` — a set; add a tag only to *narrow* where the lesson loads, default to empty (loads everywhere) when unsure (the dual of the promotion bar: promotion decides whether it travels, `domains` where it loads, both defaulting toward inclusion); grade `basis` honestly per the format reference — `verified` requires a **Verified:** section documenting the check (for preferences, the collaborator's explicit statements); anything less is `observed`, hedged, carrying its open question. Names in plain words, as a command or a fact; filename = `<type>_<name with underscores>.md`; `name` = the hyphenated slug mirroring the filename; **`title`** = the short human display title for the index line (a few plain words, e.g. `Cosmo alert suppression` — distinct from the slug `name`); `description` = the index line's sentence, verbatim (frameworks get their `applies_when` trigger prefix in the index). `title` and `description` both live in the body frontmatter — the index line renders from them (M4), so author both there.

## 7. Propose (review gate — never skip)

Build the plan file at `$RUN/plan.json` per the schema in `distill-apply --help`: `beliefs` (every new/changed store memory + its index line), `archives` (one per source copy, `promoted_to` = the merged memory's name), `source_ops` (delete or slim each source, plus one `remove_index_lines` per source project removing its consumed MEMORY.md lines), `queue_remove` (consumed queue-line **offsets** — integers, not paths). Validate before proposing:

```
distill-apply --validate "$RUN/plan.json"
```

Fix validation errors *before* showing the user anything — sign-off must never be followed by a quiet fix. (`--validate` checks the plan's structure, not the live contents of target files — a mis-copied or stale index line passes it and surfaces only at apply, as an exit-3 skip.) Then present the manifest in this fixed layout — a human rendering of the plan, split by the two write channels (numbers and names are illustrative):

```
## Plan — 3 beliefs · 4 archives · 4 source files · 4 queue removals

### Beliefs — written via file tools, each diff-prompted

1. feedback_x.md — new · observed · top-level · []
   index line: - [Short title](feedback_x.md) — <description>
   <full body, exactly as it will be written>

2. reference_y.md — new (antenna, cites framework_a) · observed · top-level · [gcp]
   index line: - [Short title](reference_y.md) — <description>
   <full body — novel content plus a citation of framework_a by title>

3. framework_a.md — edit (fold) · verified · top-level · []
   index line: - [Short title](framework_a.md) — <description, if it changed>
   <what the fold adds — the changed section, not the whole memory>

### Mechanical — one distill-apply run on the signed-off plan
Archives → local/archive/ (verbatim + promoted_to):
- proj_a/…/feedback_a.md → feedback_x
- proj_a/…/feedback_b.md → feedback_x
- proj_d/…/note_d.md → reference_y
- proj_c/…/reference_c.md → framework_a

Source ops (project dirs — each only after its archive lands):
| source | op | project index line |
|---|---|---|
| proj_a/…/feedback_a.md | delete | remove from proj_a MEMORY.md |
| proj_a/…/feedback_b.md | delete | remove from proj_a MEMORY.md |
| proj_d/…/note_d.md | delete | remove from proj_d MEMORY.md |
| proj_c/…/reference_c.md | slim → keep "<project-specific remainder>" | unchanged |

(proj_a's two index-line removals are one `remove_index_lines` op carrying both lines; proj_c keeps its line — reference_c was slimmed, not deleted.)

Queue: remove 4 entries from .candidates.log
```

The header counts are an at-a-glance summary — beliefs (memories written), archives (source copies stamped), source files (deleted or slimmed), queue removals — and should reconcile against the scan: every consumed source is archived, then either deleted and de-indexed or slimmed in place (its line kept). Index-line removals are batched one `remove_index_lines` op per project, shown per source above, so they aren't a separate count. **Each belief is shown in full** — its tag line (new/edit · basis · destination), its verbatim index line, and, for a new memory, the body exactly as it will be written; for a fold, only the changed section (the whole existing memory isn't reprinted). The bodies are reviewed here, at sign-off — not first-seen one at a time at the step-8 write prompts. **Mechanical** ops are exactly what `distill-apply` will run, in order: archive each source, then the project-dir deletes/slims (each gated on its archive landing — the only writes into project dirs, so every one is named), then queue compaction. A `leave` produces no operation and an unresolved `flag` produces none either, so neither appears. Wait for explicit sign-off. Apply any requested changes and re-validate before showing again.

## 8. Write (two channels)

After sign-off only:
1. **Beliefs via file tools** — Write each new memory file (with `title` + `domains` in its frontmatter). For a `[]`-tagged (general/personal) memory, also Edit the flat `MEMORY.md`/`MEMORY.local.md` to add its index line, as before. For a **domain-tagged** memory, do **not** hand-write an index line — its line is a generated projection; write only the body. Each prompts with its diff — the platform's confirmation of content already reviewed at step 7.
2. **Render the domain projections** — after the belief writes, run `gen-projections` so any domain-tagged memory's line is (re)rendered into its `MEMORY.<domain>.md` projection(s) and slimmed from the flat index. (For a `[]`-only batch it's a harmless no-op.)
```
gen-projections
```
**Check its exit code before continuing.** A non-zero exit means **nothing was rendered** — usually a *refusal* (a body with unparseable frontmatter, a missing `title`, or an invalid domain tag; the offending file is named on stderr), occasionally a transient store-lock-held (another batch in flight — retry). Inspect stderr; on a refusal, fix the named body (add the `title` / correct the tag) and re-run. Either way do **not** proceed to step 4 while gen-projections is failing, or the just-written domain memory is left with **no index line anywhere** (no flat line by §8.1, no projection) while step 4 archives and deletes its sources.
3. **Update the repo→domain map** — if you promoted a memory tagged with domain `d` from a repo whose `.domains.json` entry lacks `d` (`memory-init --list-domains` shows the universe; check the current repo), **propose** adding it so the lesson reloads in the repo that produced it, and on the collaborator's confirmation run `memory-init --map-repo-add <d>`. Use `--map-repo-add` (unions `d` into the repo's existing domains), **never** bare `--map-repo` here — `--map-repo` *replaces* the whole set, silently dropping any domain already mapped to the repo. Proposing, not auto-writing — the repo's domain set is the collaborator's judgment.
4. **Mechanical ops via the script**:
```
distill-apply "$RUN/plan.json"
```
Read its report. Exit 3 means partial: an op was skipped. Most skips are the tolerated race — a source changed since scan, became unreadable, or had its archive skipped — and re-enter via the queue next pass, so re-scan. The lone non-race skip reports the plan named index lines absent from the file: a malformed plan the queue won't recover, so rebuild it from a fresh scan. Never re-run apply blind.
5. Close the run window and delete the run directory: `printf '{"event":"stop","ts":%s}\n' "$(date +%s)" >> "$STORE/.runs.log"; rm -rf "$RUN"` — always write the `stop` (even on a partial run) so the window closes, and the run dir's plan carries pre-publication belief text.

## Hard rules

- The gate applies to you: nothing unverified dressed as fact; shrink claims to what the evidence supports.
- Never write or modify memories declaring `doctrine: true` — doctrine ships with this plugin and changes only by deliberate revision at its source (store copy and shipped template together), never in a distill batch; the validator enforces this. Recommendations go to the maintainers at https://github.com/veryfansome/claudemods.
- Never modify the scan, apply, or validation scripts mid-run — if a plan fails validation, the plan is wrong or the plugin needs a reviewed change at https://github.com/veryfansome/claudemods; an agent does not relax its own validator.
- If the store is missing its doctrine files, stop and tell the user — don't improvise the conventions.
- Never write into a mounted domain pack (a `<domain>/` subtree under the store with its own remote) unless it is mounted *writable* — by default packs are read-only subscriptions, so a domain lesson you learn goes into your own store tagged with the domain; promote into a writable pack (one the user maintains) only with sign-off, leaving contribution upstream to the pack's own PR flow.
