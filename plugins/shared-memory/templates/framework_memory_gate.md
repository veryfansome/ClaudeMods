---
name: framework-memory-gate
description: Information saved to memory should have hard factual grounding — check evidential basis before persisting
metadata:
  type: framework
  doctrine: true            # shipped convention — changes only by deliberate revision, never by an automated pass
  domains: []
  applies_when: [storing-a-memory, stating-a-conclusion]
  basis: verified           # shipped doctrine — the store's adopted convention for saving memories
  last_verified: n/a        # shipped doctrine changes by revision, not by going stale
---

Stop before saving if any trigger fires: the fact is surprising; it rests on an educated guess; a single observation carries the whole claim; the mechanism is unknown (you can't say *why* it's true); it contradicts an existing memory; a shared memory already covers it (don't fork a local copy — propose extending the existing memory instead); the conclusion comes from what something is named, not from what it was seen to do.

**How to apply:** When triggered, either (a) ask the collaborator the clarifying question now, (b) run the check that would settle it, or (c) shrink the claim to what the evidence actually supports and store that — the thing that happened, not the conclusion it hinted at — graded `observed`, hedged, with the open question written in. If the grounded core isn't worth keeping, don't save anything.
