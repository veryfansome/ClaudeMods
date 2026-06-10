#!/usr/bin/env bash
# Recall & inheritance probes for the shared-memory store (design doc: Test layer 2).
# These need a live `claude -p`, auth, and the real Claude Code binary, so they CANNOT
# run in ordinary CI — they are a headless/manual probe. The deterministic mechanics
# (install, uninstall, idempotency, foreign-content preservation, malformed input) are
# Layer 1 — real unit tests now, in plugins/shared-memory/tests/ (run: tests/run).
#
# Usage:
#   ./recall-probes.sh sandbox        # throwaway HOME; recall + inheritance + mapped-domain; skips loudly w/o auth
#   ./recall-probes.sh domain-recall  # just the M4 mapped-domain positive probe (sandbox)
#   ./recall-probes.sh install     # install into the REAL ~/.claude (confirms first)
#   ./recall-probes.sh uninstall   # reverse it in the REAL ~/.claude
#   ./recall-probes.sh capture     # P1: end-to-end capture-enqueue (needs the plugin ENABLED)
#
# Recall probes: sentinel recall in a fresh project, missing-MEMORY.local.md tolerance,
#         subagent import inheritance. Export ANTHROPIC_API_KEY (or run post-install in
#         the real environment) to exercise them; a fresh HOME has no credentials.
# P1 (capture): a real Write and a real Edit to a project-memory path must both enqueue on
#         .candidates.log. This needs a live `claude -p` WITH the shared-memory plugin
#         enabled (memory-init wires the store + imports, but the capture HOOK ships with
#         the plugin and activates only when the plugin is enabled) — so it runs against the
#         real ~/.claude, creates a throwaway project dir, and restores the append-only
#         queue afterward. Skips loudly if claude can't run.
# NOTE: the old sensitive-file-gate write-denial probes were removed — the store moved to
#       ~/.claudemods/ and belief writes are now allowed (inform-don't-gate), so there is no
#       write-approval to assert.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
INIT="$HERE/../bin/memory-init"
GEN="$HERE/../bin/gen-projections"
PLUGIN_DIR="$(cd "$HERE/.." && pwd)"   # this repo's plugin — loaded ad-hoc for the capture probe (--plugin-dir),
                                       # so P1 tests THIS code, not whatever build is globally installed/enabled
TS="$(date +%Y%m%d-%H%M%S)"
PASS=0; FAIL=0; SKIP=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
note() { echo "SKIP  $1"; SKIP=$((SKIP+1)); }

store()        { echo "$TARGET_HOME/.claudemods/shared-memory"; }
do_install()   { HOME="$TARGET_HOME" python3 "$INIT"; }
do_uninstall() { HOME="$TARGET_HOME" python3 "$INIT" --uninstall; }
cprobe()       { ( cd "$1" && HOME="$TARGET_HOME" claude -p "$2" 2>&1 ); }
# capture probe needs THIS repo's plugin active (its hooks) AND headless write permission.
# The target is under ~/.claude/projects/**/memory/ — a SENSITIVE dir the harness gates even
# in acceptEdits/--add-dir modes (verified), and that gate is exactly what a real interactive
# session approves. A headless probe can't approve, so it drops the gate for its one throwaway
# child that does exactly two scoped ops (a Write and an Edit) and is torn down after.
cprobe_plugin(){ ( cd "$1" && HOME="$TARGET_HOME" claude -p --plugin-dir "$PLUGIN_DIR" --dangerously-skip-permissions "$2" 2>&1 ); }

# --- doctor-recall: the non-mutating recall-health probe the doctor delegates to (M3) ---
# It asserts a LOW-SALIENCE substring of the seeded "Memory gate" index-line description —
# a string the model cannot produce unless the @import actually loaded (the guessable TITLE
# would be echoed either way). Emits ONE JSON line the doctor parses; writes .probe-stamp.json
# on a genuine pass. Timeout-guarded; hardened preflight (a whole-word token, not "OK").
ASSERT="check evidential basis before persisting"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

cprobe_to() {  # PROJ SECS PROMPT — claude -p with a timeout when one is available
  local proj="$1" secs="$2" prompt="$3"
  if [ -n "$TIMEOUT_BIN" ]; then
    ( cd "$proj" && HOME="$TARGET_HOME" "$TIMEOUT_BIN" "$secs" claude -p "$prompt" 2>&1 )
  else
    ( cd "$proj" && HOME="$TARGET_HOME" claude -p "$prompt" 2>&1 )
  fi
}

emit_recall() {  # STATUS DETAIL — a human line + the JSON line the doctor reads
  echo "recall-health: $1 — $2"
  printf '{"check":"recall-health","status":"%s","detail":"%s","findings":[],"caveats":[]}\n' "$1" "$2"
}

stamp_version() {  # record the current CLI version as one the recall probe passed on
  local ver; ver="$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  [ -n "$ver" ] || return 0
  HOME="$TARGET_HOME" python3 - "$(store)/.probe-stamp.json" "$ver" <<'PY'
import json, sys
path, ver = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    d = {}
vs = set(d.get("passed_versions") or []); vs.add(ver)
d["passed_versions"] = sorted(vs)
json.dump(d, open(path, "w"), indent=1)
PY
}

doctor_recall() {  # non-interactive; runs against $HOME, non-mutating on the store
  local PROJ; PROJ="$(mktemp -d)"
  local live; live="$(cprobe_to "$PROJ" 30 'Reply with exactly the token READY and nothing else.')"
  if ! printf '%s' "$live" | grep -qw READY; then
    emit_recall skip "claude -p unavailable/unauthed here — recall-health not run"
    rm -rf "$PROJ"; return 0
  fi
  local out; out="$(cprobe_to "$PROJ" 60 "From your loaded memory index, quote verbatim the one-line description of the memory titled 'Memory gate'. If it is not in your context, reply exactly NO.")"
  rm -rf "$PROJ"
  if printf '%s' "$out" | grep -qF "$ASSERT"; then
    emit_recall ok "store index loads in a fresh session (gate description recalled verbatim)"
    stamp_version
    return 0
  fi
  emit_recall fail "store index did NOT load — the gate description was not recalled"
  return 1
}

probes() {
  do_install >/dev/null
  local SENTINEL="SM_SENTINEL_$TS"
  echo "- [probe sentinel](framework_memory_gate.md) — $SENTINEL" >> "$(store)/MEMORY.md"
  local PROJ; PROJ="$(mktemp -d)"
  if ! cprobe "$PROJ" "Reply with exactly OK" | grep -q "OK"; then
    note "recall probes: claude -p cannot run under this HOME (no auth) — run post-install in the real environment, or export ANTHROPIC_API_KEY"
    rm -rf "$PROJ"; return 0
  fi
  cprobe "$PROJ" "If the string $SENTINEL appears anywhere in your context, reply with it verbatim; otherwise reply NO" | grep -q "$SENTINEL" \
    && ok "sentinel loads in a fresh project" || bad "sentinel recall"
  rm -f "$(store)/MEMORY.local.md"
  cprobe "$PROJ" "If the string $SENTINEL appears anywhere in your context, reply with it verbatim; otherwise reply NO" | grep -q "$SENTINEL" \
    && ok "missing MEMORY.local.md does not break the surviving import" || bad "missing-import behavior (sentinel lost)"
  printf '# Personal memory index\n' > "$(store)/MEMORY.local.md"
  # (removed: the old sensitive-file-gate Write/Edit-denial probes — under inform-don't-gate the
  #  store moved out of ~/.claude/ and belief writes are allowed; there is nothing to deny here.)
  cprobe "$PROJ" "Spawn a subagent (Explore or general agent) and ask it whether the string $SENTINEL appears in its context; reply with the subagent's answer" | grep -q "$SENTINEL" \
    && ok "subagent inherits the import" || note "subagent inheritance: inconclusive (agent answer did not echo sentinel)"
  rm -rf "$PROJ"
}

capture_probe() {
  # P1 (Layer 2): a real Write AND a real Edit to a project-memory path must each enqueue
  # on .candidates.log via the async capture hook. Restores the append-only queue after.
  local QUEUE; QUEUE="$(store)/.candidates.log"
  local PROJ_ENC="-sm-capture-probe-$TS"
  local MEMDIR="$TARGET_HOME/.claude/projects/$PROJ_ENC/memory"
  local WPATH="$MEMDIR/feedback_capture_$TS.md"
  local EPATH="$MEMDIR/reference_capture_$TS.md"
  mkdir -p "$MEMDIR"
  printf -- '---\nname: reference-capture\nmetadata:\n  type: reference\n  basis: observed\n---\nseed\n' > "$EPATH"
  # snapshot the append-only queue so the probe leaves it exactly as it found it
  local SNAP HAD_Q=0; SNAP="$(mktemp)"
  [ -f "$QUEUE" ] && { HAD_Q=1; cp "$QUEUE" "$SNAP"; }
  if ! cprobe_plugin "$MEMDIR" "Reply with exactly OK" | grep -q "OK"; then
    note "capture probe: claude -p cannot run here (no auth) — export ANTHROPIC_API_KEY or run in an authed environment"
    rm -rf "$TARGET_HOME/.claude/projects/$PROJ_ENC"; rm -f "$SNAP"; return 0
  fi
  cprobe_plugin "$MEMDIR" "Use the Write tool to create the file $WPATH with exactly this content and nothing else:
---
name: feedback-capture
metadata:
  type: feedback
  basis: observed
---
body" >/dev/null
  cprobe_plugin "$MEMDIR" "Use the Edit tool to replace the word seed with edited in the file $EPATH" >/dev/null
  sleep 2  # capture is async (O_APPEND) — give the background hook time to land
  grep -qsF "$WPATH" "$QUEUE" && ok "Write-created memory enqueued" || bad "Write-created memory did not enqueue"
  grep -qsF "$EPATH" "$QUEUE" && ok "Edit-created memory enqueued"  || bad "Edit-created memory did not enqueue"
  if [ "$HAD_Q" = 1 ]; then cp "$SNAP" "$QUEUE"; else rm -f "$QUEUE"; fi   # restore to exactly the prior state
  rm -f "$SNAP"
  rm -rf "$TARGET_HOME/.claude/projects/$PROJ_ENC"
}

domain_recall_probe() {
  # M4 recall-health, mapped-domain POSITIVE probe (Layer 2, sandbox). Proves the SessionStart
  # loader (load-recall) injects a MAPPED repo's domain projection end-to-end — real `claude -p`
  # + the plugin's own hook (loaded ad-hoc via --plugin-dir, since a throwaway HOME has no enabled
  # plugin). Self-contained: a synthetic `[probe]`-domain memory with a sentinel the projection is
  # the only carrier of — so it needs none of the real store's (private) content, and nothing to
  # hardcode into this public repo. The out-of-domain case is a DEMOTED note: absence can't PROVE
  # non-load, so a missing sentinel there is informational, only its unexpected PRESENCE fails.
  do_install >/dev/null
  local SENT="SM_DOMAIN_$TS"
  cat > "$(store)/reference_domainprobe.md" <<EOF
---
name: reference-domainprobe
title: Domain probe
description: $SENT
metadata:
  type: reference
  domains: [probe]
  basis: observed
---
body
EOF
  HOME="$TARGET_HOME" python3 "$GEN" >/dev/null 2>&1   # render MEMORY.probe.md, slim the flat line
  local REPO; REPO="$(mktemp -d)"; ( cd "$REPO" && git init -q )
  ( cd "$REPO" && HOME="$TARGET_HOME" python3 "$INIT" --map-repo-add probe >/dev/null )
  if ! cprobe_plugin "$REPO" "Reply with exactly OK" | grep -q "OK"; then
    note "domain-recall: claude -p cannot run here (no auth) — export ANTHROPIC_API_KEY or run in an authed environment"
    rm -rf "$REPO"; return 0
  fi
  local q="If a memory description containing the exact string $SENT is in your context, reply with that string verbatim; otherwise reply NO"
  cprobe_plugin "$REPO" "$q" | grep -q "$SENT" \
    && ok "mapped-domain projection loads via the SessionStart hook" \
    || bad "mapped-domain projection did NOT load in a mapped repo (loader/hook broken)"
  local REPO2; REPO2="$(mktemp -d)"; ( cd "$REPO2" && git init -q )   # unmapped → must NOT carry it
  if cprobe_plugin "$REPO2" "$q" | grep -q "$SENT"; then
    bad "out-of-domain repo loaded the domain projection (should be scoped out)"
  else
    note "out-of-domain: sentinel absent as expected (informational — absence can't prove non-load)"
  fi
  rm -rf "$REPO" "$REPO2"
}

case "${1:-sandbox}" in
  sandbox)
    TARGET_HOME="$(mktemp -d)"; export TARGET_HOME
    echo "sandbox HOME: $TARGET_HOME"
    probes
    domain_recall_probe
    cd /; rm -rf "$TARGET_HOME"
    ;;
  domain-recall)
    TARGET_HOME="$(mktemp -d)"; export TARGET_HOME
    echo "sandbox HOME: $TARGET_HOME"
    domain_recall_probe
    cd /; rm -rf "$TARGET_HOME"
    echo "---- pass:$PASS fail:$FAIL skip:$SKIP"; [ "$FAIL" -eq 0 ]; exit $?
    ;;
  capture)
    TARGET_HOME="$HOME"; export TARGET_HOME
    echo "P1 capture-enqueue probe — needs the shared-memory plugin ENABLED in this claude."
    read -r -p "Run against the REAL $HOME? (creates a throwaway project dir; restores the queue after) [y/N] " a
    [ "$a" = "y" ] && capture_probe || echo "aborted"
    ;;
  install)
    TARGET_HOME="$HOME"
    read -r -p "Install shared-memory wiring into the REAL $HOME/.claude? [y/N] " a
    [ "$a" = "y" ] && do_install || echo "aborted"
    ;;
  uninstall)
    TARGET_HOME="$HOME"
    read -r -p "Remove shared-memory wiring from the REAL $HOME/.claude? (store is left in place) [y/N] " a
    [ "$a" = "y" ] && do_uninstall || echo "aborted"
    ;;
  doctor-recall)
    # non-interactive: the doctor (memory-init --doctor) delegates here. Runs against the
    # REAL $HOME, non-mutating on the store (only writes .probe-stamp.json on a pass).
    # exit directly so the PASS/FAIL/SKIP tally below (which doctor_recall doesn't touch)
    # doesn't clobber its exit code or trail non-JSON after the JSON line.
    TARGET_HOME="$HOME"; export TARGET_HOME
    doctor_recall; exit $?
    ;;
  *) echo "usage: $0 [sandbox|domain-recall|capture|install|uninstall|doctor-recall]"; exit 2 ;;
esac
echo "---- pass:$PASS fail:$FAIL skip:$SKIP"
[ "$FAIL" -eq 0 ]
