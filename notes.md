# Notes — evolve plugin: open work from the source campaign

Source: 8+ rounds (~80 scored candidates, four regime changes) of a registry-mode evolutionary search over a production recommendation-model component — referred to below as **the source campaign**. These are findings about the FRAMEWORK, not that model; everything was measured, not inferred. Candidate ids and tool references are historical evidence from that campaign — none exist elsewhere, so each item states the general need it demonstrates.

Cleaned 2026-08-09: implemented findings were removed from this file; their history lives in the plugin's commit log (retraction rework → champion removal v0.3.0 → auto-λ v0.4.0 → adoption/gates removal v0.5.0 → cross-partition validity v0.5.1 → information diet + inventor jail v0.6.0). Original section numbers are preserved for cross-reference with those commits.

## 2 + 7 + 8 (read side). Parent-adherence guard — engine-side, from persisted slot state

The engine samples diverse parents; nothing verifies inventors USED them. Measured on the campaign's r8 round — genome chunk-distance from each candidate to its engine-assigned parent vs to the then-champion's stack ("champion" names the concept the engine had at the time):

| candidate | assigned parent | d(parent) | d(champion-adjacent) |
|---|---|---|---|
| r8_0 | r6_9 | 1 | 4 |
| r8_1 | r2_5-2 | 7 | **1** |
| r8_2 | r1_6 | 7 | **1** |
| r8_3 | r6_8 | 4 | **1** |
| r8_4 | r4_2 | 6 | **1** |
| r8_5 | r5_c1 | 5 | **1** |

Five of six inventors discarded their assigned parent and mutated the top lineage instead (r6 showed 9 of 10 on one chassis). **Effective population = one lineage across an 80-candidate archive** — the archive's diversity was nominal, and the offspring penalty + novelty weighting had nothing to act on.

The naive gate is **vacuous**: "for every axis NOT in the candidate's declared `axes_changed`, the impl must equal the parent's; every declared axis must differ" is satisfied *by construction* whenever `declared == diff(candidate, parent)` — a candidate that mutated a different genome passes by declaring its diff honestly. Verified in the campaign: assigning all six r8 genomes to one parent with `declared` set to each candidate's computed diff yields 6/6 PASS while the tool's own diagnostics show five of them one chunk from the top lineage.

What works (a gate needs at least one component the proposer cannot influence, using only data the engine already holds):

- **FAIL when any other archived genome is STRICTLY nearer than the assigned parent.** Strictly, not `<=`: sibling genomes that differ from the parent only on the axis being mutated tie legitimately. On the r8 regression this gives 1/6 PASS — the correct answer — and holds under the bypass declaration.
- **Partner exemption** (measured in the campaign's r9 round): in dense combination families the strictly-nearer rule false-flags sanctioned cross-pairs — a child of two siblings lands one axis from a cousin (5/20 slots there). A closer-other hit on the slot's own assigned crossover partner counts as PASS; a hit on a third genome is recorded for owner adjudication rather than auto-rejected.
- **Mandatory axis confinement** from the engine's assigned axis, with an explicit, logged escape for protocols that permit multi-axis mutation.

Engine integration: `evolve sample` already persists the slot table (`evolve/rounds/<seed>-<hash>-slots.json`) precisely so this can be engine-enforced — the missing half is `guard`/`score` READING it, so adherence is checked from engine-side state instead of an orchestrator-side tool. Owner rule: a deviation is not fixable — reject the proposal and respawn the slot. Generalizable lesson: in an LLM-driven search, every gate input the proposer supplies is an attack surface; compute verdicts from the artifact plus engine-side state.

## 6 (residuals). Smaller items

- **`doctor --measure-env-offset --ref` dual-env footgun**: a ref id scored in BOTH environments resolves to its raw-max record and the measurement can overwrite a hand-derived offset — worth documenting (and possibly guarding) engine-side; currently recorded only in the campaign's project manual.
- **Frozen-readout caveat**: cheap proxy readouts went 0-for-3 predicting full-budget wave outcomes in the campaign (two large discounts, one sign inversion from a positive readout). If the framework ever suggests cheap screens, a proxy that ranks candidates must itself be validated against full-budget outcomes before it gates spending.

## 12. A regime change is a boundary, and everything that needs one is free at it

A dataset/featurizer change already forces (a) a fresh selection partition, (b) a re-pinned baseline, and (c) a full-pool re-measurement. Any *other* pending change that would break score comparability — most importantly fixing an archived impl that computes the wrong thing — costs **nothing extra** if it lands at the same boundary. Batching is not a convenience; it is the difference between one wave and several, and between one incomparability statement and a tangle of them. The campaign executed four such boundaries in ~30 hours, every step orchestrator convention.

Corollaries the framework could support directly:

- **The engine could own the boundary**: an `evolve cutover --to <env>` verb that re-pins the baseline, marks the previous partition closed, emits the re-measurement roster (honoring retracted ids and retired impls), and refuses to proceed while any impl fails a project-supplied structural check. Today every step is convention, which is how one of them (pass a real parent on every bulk ingest) was skipped twice and manufactured phantom budget stops.
- **`append-only` should be scoped to behavior**, not bytes. Docstrings and machine-readable axis `contract` strings must stay current — under the information diet they are close to the only facts an inventor gets, and both of the campaign's inventor-facing texts described a pre-cutover profile width for three rounds after it changed, which is precisely how a hardcoded-arithmetic habit survived. Freezing them protects nothing and misinforms every future candidate.
- **Generation-preserving re-ingest**: a re-measurement ingest of a known id reuses the id's existing generation — which also propagates *damaged* stamps into the new partition (the campaign's archive carried inflated generations from an early parentless bulk ingest, and its wave tool had to pass explicit `--generation` = lineage depth per record). A boundary wants a first-class lineage-depth re-ingest mode.
- **Partition integrity is asserted nowhere**: env tags are free text, and nothing ties an ingested record's env to the dataset version or the machine that produced it — a wave from one machine can be filed under another's tag, which is exactly the cross-regime mixing partitioning exists to prevent. The cutover verb is the natural home for an env↔dataset assertion.
