#!/usr/bin/env bash
# ABOUTME: Handler for agent session start events
# ABOUTME: Ensures driftdriver wrappers, starts wg service, prints project context

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

# Ensure driftdriver install wrappers exist (idempotent)
driftdriver --dir "$PROJECT_DIR" install 2>/dev/null || true

# Provide repo-local shims needed by Workgraph-generated agent wrappers.
export PATH="$PROJECT_DIR/.workgraph/bin:$PATH"

# Refresh runtime control/status before deciding whether this session may start services.
SPEEDRIFT_STATUS="$(driftdriver --dir "$PROJECT_DIR" --json speedriftd status --refresh 2>/dev/null || echo '{}')"
CONTROL_MODE="$(printf '%s\n' "$SPEEDRIFT_STATUS" | jq -r '.control.mode // "observe"' 2>/dev/null || echo "observe")"

# Interactive sessions only auto-start services when repo control mode explicitly allows it.
if [[ "$CONTROL_MODE" == "supervise" || "$CONTROL_MODE" == "autonomous" ]]; then
  wg service start 2>/dev/null || true
fi

# Prime agent with project knowledge from lessons.db (real-time path)
if command -v driftdriver >/dev/null 2>&1; then
  CONTEXT=$(driftdriver --dir "$PROJECT_DIR" prime 2>/dev/null || echo "")
  if [[ -n "$CONTEXT" ]]; then
    echo "=== Project Knowledge Summary ==="
    echo "$CONTEXT"
    echo "================================="
  fi

  # Record session start event immediately
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "session_start" \
    --content "Session started for $(basename "$PROJECT_DIR") via $CLI_TOOL" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true

  # Register presence so the ecosystem hub sees this session as active
  driftdriver --dir "$PROJECT_DIR" presence register \
    --actor-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-session-$$}}" \
    --actor-class interactive \
    --name "$CLI_TOOL" 2>/dev/null || true

  # Surface any pending human decisions
  DECISIONS=$(driftdriver --dir "$PROJECT_DIR" decisions pending 2>/dev/null || echo "")
  if [[ -n "$DECISIONS" && "$DECISIONS" != "No pending decisions." ]]; then
    echo "$DECISIONS"
  fi

  # Emit session.started to events.jsonl so the factory brain knows to back off
  EVENTS_FILE="$PROJECT_DIR/.workgraph/service/runtime/events.jsonl"
  mkdir -p "$(dirname "$EVENTS_FILE")" 2>/dev/null || true
  REPO_NAME="$(basename "$PROJECT_DIR")"
  TS="$(date +%s.%N 2>/dev/null || date +%s)"
  echo "{\"kind\":\"session.started\",\"repo\":\"$REPO_NAME\",\"ts\":$TS,\"payload\":{\"cli\":\"$CLI_TOOL\",\"actor_id\":\"${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-session-$$}}\"}}" >> "$EVENTS_FILE" 2>/dev/null || true
  echo "events.jsonl: session.started emitted for $REPO_NAME"
fi
