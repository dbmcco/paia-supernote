#!/usr/bin/env bash
# ABOUTME: Pre-dispatch hook that enriches task prompts with Agency-composed agent identity.
# ABOUTME: Reads original prompt on stdin, outputs enriched prompt on stdout. Falls back silently.

set -euo pipefail

# ── Interface ──
# stdin:  original speedrift prompt
# stdout: enriched prompt (or original if Agency unavailable)
# env:    WG_TASK_ID (required), WG_SKIP_AGENCY (optional, set to "1" to bypass)
#
# Exit 0 always — this hook never blocks dispatch.

TASK_ID="${WG_TASK_ID:-}"
PROMPT="$(cat)"

# Pass through if no task ID or Agency is explicitly skipped
if [[ -z "$TASK_ID" || "${WG_SKIP_AGENCY:-}" == "1" ]]; then
  printf '%s' "$PROMPT"
  exit 0
fi

# Locate executors directory (sibling to hooks)
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WG_DIR="$(dirname "$HOOK_DIR")"
EXECUTORS_DIR="$WG_DIR/executors"

ASSIGN_SCRIPT="$EXECUTORS_DIR/agency-assign-workgraph"
WRAP_SCRIPT="$EXECUTORS_DIR/agency-speedrift-wrap.py"

# Both scripts must exist
if [[ ! -x "$ASSIGN_SCRIPT" || ! -f "$WRAP_SCRIPT" ]]; then
  printf '%s' "$PROMPT"
  exit 0
fi

# ── Events helper ──
EVENTS_FILE="${WG_DIR}/service/runtime/events.jsonl"

_emit() {
  local kind="$1" detail="$2"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
  mkdir -p "$(dirname "$EVENTS_FILE")" 2>/dev/null || true
  echo "{\"kind\":\"$kind\",\"ts\":\"$ts\",\"task\":\"$TASK_ID\",\"detail\":\"$detail\"}" \
    >> "$EVENTS_FILE" 2>/dev/null || true
}

# ── Call Agency ──
COMPOSED=""
COMPOSED=$("$ASSIGN_SCRIPT" "$TASK_ID" "" 2>/dev/null) || true

if [[ -z "$COMPOSED" ]]; then
  _emit "agency.enrichment.skipped" "reason=agency_unavailable"
  printf '%s' "$PROMPT"
  exit 0
fi

# ── Wrap: merge Agency identity with speedrift prompt ──
ORIG_TMP="$(mktemp)"
trap 'rm -f "$ORIG_TMP"' EXIT
printf '%s' "$PROMPT" > "$ORIG_TMP"

ENRICHED=""
ENRICHED=$(printf '%s' "$COMPOSED" | python3 "$WRAP_SCRIPT" "$ORIG_TMP" 2>/dev/null) || true

rm -f "$ORIG_TMP"
trap - EXIT

if [[ -n "$ENRICHED" ]]; then
  _emit "agency.enrichment.applied" "task=$TASK_ID"
  printf '%s' "$ENRICHED"
else
  _emit "agency.enrichment.skipped" "reason=wrap_failed"
  printf '%s' "$PROMPT"
fi
