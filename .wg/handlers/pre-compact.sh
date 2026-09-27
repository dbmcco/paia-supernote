#!/usr/bin/env bash
# ABOUTME: Handler for pre-context-compaction events
# ABOUTME: Flushes pending learnings to Lessons MCP before context is lost

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

SESSION_ID="${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-unknown}}"
TASK_ID="$(current_task_id)"

# Events are recorded in real-time — no pending queue to flush.
# Run self-reflect to capture any remaining learnings before compaction.
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "pre_compact" \
    --content "Context compaction starting for session $SESSION_ID" \
    --session-id "$SESSION_ID" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

wg_log "$TASK_ID" "pre-compact: session=$SESSION_ID cli=$CLI_TOOL"
