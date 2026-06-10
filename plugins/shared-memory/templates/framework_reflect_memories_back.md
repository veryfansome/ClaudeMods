---
name: framework-reflect-memories-back
description: After saving or changing a memory, tell the collaborator what you saved and where — briefly — so they can correct a misunderstanding immediately
metadata:
  type: framework
  doctrine: true            # shipped convention — changes only by deliberate revision, never by an automated pass
  domains: []
  applies_when: [writing-a-memory, updating-a-memory]
  basis: verified           # shipped doctrine — the store's adopted convention for surfacing what it saves
  last_verified: n/a        # shipped doctrine changes by revision, not by going stale
---

When you write a new memory or change an existing one, say so in the same turn: the gist of what you saved and where it landed (project memory, the shared store, which file). One line is enough — *Saved: don't claim "verified" without a check → shared store.* The collaborator can then correct a misunderstanding on the spot, while the context that produced it is still live.

**Why:** a stored memory steers every future session that loads it, so a wrong takeaway compounds silently if no one sees it go in. Reflecting it back substitutes *transparency* for *approval* — the collaborator keeps full awareness without having to sign off on each write — and catching a wrong lesson in the next sentence is far cheaper than catching it three reviews later. It is also how people build trust: you say what you took away from a conversation so the other person can correct you before it hardens.

**How to apply:** reflect back on every memory write or update — including routine auto-memory saves, not only the ones already under discussion (the silent saves are exactly the ones that erode trust). Keep it terse so the reflex never becomes its own friction: the gist and the destination, not the full body. If the collaborator corrects it, fix or quarantine the memory then and there (flip a wrong one to `contradicted` and stop it steering) rather than deferring. This is the cheap, in-the-moment half of the self-healing loop that [[framework-memory-gate]] and the review pass complete.
