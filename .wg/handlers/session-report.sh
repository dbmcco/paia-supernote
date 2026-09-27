#!/usr/bin/env bash
# ABOUTME: Flush pending events and generate session report at session end
# ABOUTME: Calls driftdriver report to close the learning loop

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

SESSION_ID="${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-unknown}}"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

driftdriver report --session-id "$SESSION_ID" --project "$PROJECT_NAME" --push 2>/dev/null || true

wg_log "$(current_task_id)" "session-report: session=$SESSION_ID project=$PROJECT_NAME"
