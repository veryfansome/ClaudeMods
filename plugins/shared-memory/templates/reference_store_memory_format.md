---
name: reference-store-memory-format
description: When writing or editing any memory in this store — the fields every memory declares and what they mean
metadata:
  type: reference
  doctrine: true            # shipped convention — changes only by deliberate revision, never by an automated pass
  domains: []
  basis: verified          # shipped doctrine — this file is the convention it describes
  last_verified: n/a       # shipped doctrine changes by revision, not by going stale
---

Every memory in this store is one markdown file: YAML frontmatter carrying the fields below, then a body that can be acted on with no other context (see "Write for the cold reader").

- `name` — the lesson, as a command or a fact, in plain words, hyphenated; mirrors the filename.
- `title` — the short human display title for the index line (`- [title](link) — description`); a few plain words, capitalized as you'd read them. Distinct from `name` (the hyphenated slug): e.g. `name: reference-cosmo-suppression-process`, `title: Cosmo alert suppression`. The body carries it so the index line renders from the body alone.
- `description` — the index line's sentence, verbatim: one trigger-rich sentence that makes the memory fire mid-task. The index holds exactly one line per memory: `title`, link, `description` — all three from the body.
- `type` — `feedback` (a lesson about how to work — a collaborator's preferences are feedback too) | `reference` (a pointer, fact, or convention, like this file) | `framework` (a trigger plus a procedure; frameworks also carry `applies_when`, the situations they fire in, and — on a **consolidation** framework — `supersedes: [names]`, the memories it folds in and replaces). A memory of any type may be an **antenna** — it *cites* an existing memory rather than restating it — and then carries `cites: [names]`, the memories it points to, so a review or the post-write eval can tell a deliberate antenna from an accidental duplicate.
- `doctrine` — `true` on the store's shipped conventions only, this file among them. A doctrine memory changes by deliberate revision — the store file and its shipped template together — never by an automated pass; its standing is adoption, noted in the `basis` comment instead of a **Verified:** section, and its `last_verified` is `n/a`.
- `domains` — where the memory applies: empty means everywhere (most behavioral lessons); otherwise the tools, arenas, or projects it is bound to, lowercase, as you'd say them (`gcp`, `terraform`, `code-review`, or `project_<name>` to bind a memory to specific projects). Reuse a tag the store already uses before minting a new one. Tagged at write time so later delivery can filter without revisiting every memory.
- `basis` — what the evidence is:
  - `verified`: a check that could have proven the claim wrong ran, and the body documents it (see **Verified:** below). For facts about systems, a command or probe; for a collaborator's preferences, their explicit statement is the check.
  - `observed`: grounded in something real — an incident, an output, a one-off correction — but the claim as written never faced a check that could kill it. Hedge the wording; these wait on the review pass (any session walking the store with its collaborator to verify, update, or retire entries) to be confirmed or removed. A claim with no grounding at all is not a memory.
  - `contradicted`: conflicting evidence has appeared. Set this and **tombstone** the index line — strip its guidance (the trigger-rich description; neutralize the imperative title), leaving a contradicted-pending-review marker so no session follows the memory — then let the next review pass adjudicate: re-verify or retire. (A prefix alone leaves the guidance loaded into every session.) The tombstoned line keeps its parseable link but drops all guidance — `- [contradicted — pending review](<path>)`: a fixed marker title, no trailing description — so the review scan still enumerates the memory while nothing steers off it.
- `last_verified` — for `verified` entries, the date the documented check last ran (refreshed when a review pass re-runs it); for `observed` entries, the date the grounding was recorded or last revisited. Its only job is to show what is old. `n/a` for shipped doctrine: conventions change by revision, not by going stale.

Where a memory lives is its scope, and moving it is how scope changes. Personal entries — preferences, identity, environment, anything whose truth depends on the person — live under `local/`, are indexed in `MEMORY.local.md`, and never leave this machine. Everything at the top level is general — about the work or the world, true regardless of who is at the keyboard, provisional until someone else reviews it at a sharing boundary. Promoting a personal memory the rest of a team adopts is a move plus an index-line move, never a content edit.

Body shape: the lesson first; then **Why:** (the recurring pattern, told so it can be pictured without the original incident); then **How to apply:** (what to actually do); and, for verified memories, **Verified:** (the check and its result — the command and what it returned, or when and how the collaborator said it). Shipped doctrine carries no **Why:** or **Verified:** sections: its standing is adoption, noted in the `basis` comment.
