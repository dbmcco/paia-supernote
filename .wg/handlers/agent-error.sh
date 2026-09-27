#!/usr/bin/env bash
# ABOUTME: Handler invoked when the agent encounters an error
# ABOUTME: Records error to Lessons MCP, checks for agentjj rollback point, outputs recovery suggestion

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"
ERROR_MSG="${WG_ERROR_MESSAGE:-unknown error}"

# Record error event immediately to lessons.db
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "agent_error" \
    --content "Error on task $TASK_ID: $ERROR_MSG" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

# Check if agentjj checkpoint exists for this task
CHECKPOINT="pre-task-$TASK_ID"
HAS_CHECKPOINT=$(agentjj list-checkpoints 2>/dev/null | grep -cF "$CHECKPOINT" || echo "0")

if [[ "$HAS_CHECKPOINT" -gt 0 ]]; then
  echo "RECOVERY: agentjj rollback to checkpoint '$CHECKPOINT' available"
  echo "  Run: agentjj restore $CHECKPOINT"
else
  echo "RECOVERY: no checkpoint found — review git status and retry from last known good state"
fi

# Emit task.failed to events.jsonl so the factory brain tracks task lifecycle
EVENTS_FILE="$PROJECT_DIR/.workgraph/service/runtime/events.jsonl"
if [[ -d "$(dirname "$EVENTS_FILE")" ]]; then
  REPO_NAME="$(basename "$PROJECT_DIR")"
  TS="$(date +%s.%N 2>/dev/null || date +%s)"
  echo "{\"kind\":\"task.failed\",\"repo\":\"$REPO_NAME\",\"ts\":$TS,\"payload\":{\"task\":\"$TASK_ID\",\"error\":\"$ERROR_MSG\"}}" >> "$EVENTS_FILE" 2>/dev/null || true
fi

wg_log "$TASK_ID" "agent-error: error=$ERROR_MSG checkpoint_available=$HAS_CHECKPOINT"
