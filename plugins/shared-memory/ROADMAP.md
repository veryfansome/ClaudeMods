# shared-memory — roadmap

What's shipped, what was deliberately held off, and the design questions still open. See [README.md](README.md) for what the plugin does today.

## Shipped (current: v0.3.3)

The full single-machine system is built and dogfooded:

- **Store + recall** — the store at `~/.claudemods/shared-memory/`, `@import` wiring, permission rules, subagent-inherited recall.
- **`/memory-distill`** — scan, cluster, judge, adversarial self-review, batch-shape veto, propose, sign-off, write beliefs via file tools + mechanical ops via `distill-apply`.
- **Plugin packaging** — skills + hooks + scripts + doctrine templates in the `shared-memory` plugin, enabled at user scope.
- **`/memory-review`** — staleness / `observed` queue / `contradicted` quarantine, dangling-reference checks, retire to `RETIRED.md`, lesson→framework consolidation.
- **`/memory-reflect`** — transcript rumination: recall audit + mining, fed by a `SessionEnd` watermark.
- **Capture + post-write eval hooks** — `capture-candidate` queues project-memory writes; `eval-memory` flags a missing `basis` or a near-duplicate, non-blocking.
- **`memory-init --doctor`** — read-only health pass (wiring drift, plugin build freshness, recall health, projection integrity, coverage, version drift, ignore confinement).
- **Domain-scoped recall** — `gen-projections` renders `MEMORY.<domain>.md` / `MEMORY.local.<domain>.md` from each memory's `domains`; a `SessionStart` `load-recall` hook injects the current repo's matching projections; a machine-local `.domains.json` repo→domain map (`--map-repo` / `--map-repo-add` / `--list-domains`); fresh-clone onboarding hint + `unmapped-with-projections` doctor warning; a Layer-2 `domain-recall` probe.
- **Correctness hardening (R1–R9)** — a nine-round remediation of the distill/review/reflect/apply core: archive-before-delete data-loss guards, single-winner stale-lock recovery, crash-atomic writes across the apply channel (tmp+rename / no-clobber `os.link`), store-wide UTF-8 fail-soft, installer round-trip safety, and 300+ Layer-1 tests. See the git history of `projects/wip/shared_memories/` for the full record.

## Deferred features

Held off deliberately — they matter only for sharing beyond one machine, and the single-user system delivers full value without them.

- **Group remote stores (`memory-init --remote <url>`)** — the flag currently stubs out ("planned, not yet implemented"). Planned: clone a group store and wire it; the solo→remote **upgrade that re-roots history** (current contents go up as a fresh reviewed initial commit; unreviewed local history stays local); the clone-safety refusal (reject a clone that *tracks* any machine-local path); PR conventions for general memories ("safe to load", not just "safe to share"); a behind-remote warning `SessionStart` hook. Sharing a store via a plain git remote + manual PR review already works today; this is the convenience + safety tooling around it.
- **Federated domain packs (`memory-init --mount <domain> <url>`)** — a domain pack is a shared-memory store scoped to one domain, published by whoever owns that knowledge, that others mount and subscribe to rather than each re-distilling a drifting private copy (this plugin's core failure lifted from per-repo to per-person). Planned: mount a pack as a gitignored subtree with its own remote; the loader unions a pack's index with your own projection for a domain; **read-only by default** (distill writes only into your store), **writable** (`--writable`) for a pack you maintain (promote in with sign-off + the same gate/eval, PR upstream); a fork-and-mount-your-fork consumption model. Pack trust machinery (a per-pack load-bar review) stays out of scope while the system is effectively single-user — mount only what you trust.
- **Atomic per-run batch commit** for git-backed group stores — one commit per distill/review batch, so a shared store's history is a clean sequence of reviewed changes. Independent of the write channel (beliefs stay on the file tools).

## Open questions

- **Index recall efficacy & budget** — recall rests on one-line index entries carrying enough signal to fire mid-task, and on the list staying short enough that entries don't dilute each other. Domain-scoped indexes are the designed remedy for dilution; whether index lines fire reliably is measured passively (already-promoted topics reappearing in the queue) and actively (the reflect recall audit).
- **Gate strictness** — too lax and unvalidated beliefs persist; too strict and saving becomes a research project so the system stops remembering. The dial probably scales with blast radius (a hedged `observed` into a project dir vs. an `observed` lesson promoting to the store); whether the current setting is right is unproven.
- **Multi-machine** — a git clone syncs the general memories, but the personal overlay, auto memory, transcripts, and the `.domains.json` map are all machine-local — so on a second machine reflection only sees that machine's experience, and committed domain projections load nowhere until each repo is re-mapped there. Which gap is worth closing first (a private remote/branch for `local/`?) is open.
- **Local override of a shared memory** — invalidating a wrong group memory is done in the clone and PR'd; there's no local layer that overrides a committed memory. Only a read-only subscriber (can't PR, wants to suppress/correct one entry while still pulling updates) needs one. For a mounted read-only pack the answer is structural (fork it, mount your fork, cherry-pick upstream) rather than a new override layer.

## Known residuals (accepted, bounded)

Documented limitations from the hardening rounds, acceptable for a single-user advisory-locked store:

- **≥3-way stale-lock double-holder** — the reported two-acquirer race is fully fixed (single-winner recovery); a ≥3-way contention on one dead-owner lock has a small residual double-hold window. Implausible on a serialized single-user store; a crashed run's stale lock is still recovered on the next acquire. (Closable later with a mkdir-based lock.)
- **Append-only state files grow unbounded** — `.candidates.log` / `.candidates.consumed` / `.recall.log` / `.runs.log` / `.reflect/` have no GC. A few MB and sub-second parse over multi-year single-user use. Any future compaction must be co-designed with the queue's stable-offset invariant (a naive rewrite would misalign every consumed offset), not a naive truncate.
