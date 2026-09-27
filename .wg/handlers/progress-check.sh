#!/usr/bin/env bash
# ABOUTME: Handler for periodic progress monitoring during agent execution
# ABOUTME: Detects loop patterns by fingerprinting recent tool calls

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

LOOP_STATE_FILE="$WG_DIR/.loop-state"
TASK_ID="$(current_task_id)"
if [[ -z "$TASK_ID" ]]; then exit 0; fi

# Fingerprint: tool name + task ID + timestamp bucket (5-min windows)
TOOL_NAME="${WG_TOOL_NAME:-unknown}"
TIME_BUCKET=$(( $(date +%s) / 300 ))
FINGERPRINT="$TASK_ID:$TOOL_NAME:$TIME_BUCKET"

# Append fingerprint to loop state file
echo "$FINGERPRINT" >> "$LOOP_STATE_FILE" 2>/dev/null || true

# Count occurrences of this exact fingerprint
OCCURRENCES=$(grep -cF "${FINGERPRINT}" "$LOOP_STATE_FILE" 2>/dev/null || echo "0")

if [[ "$OCCURRENCES" -ge 3 ]]; then
  echo "WARNING: Possible loop detected — '$TOOL_NAME' repeated $OCCURRENCES times in task $TASK_ID"
  wg_log "$TASK_ID" "loop-warning: tool=$TOOL_NAME occurrences=$OCCURRENCES"
fi

# Update presence heartbeat so the ecosystem hub knows this session is still alive
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" presence heartbeat \
    --actor-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-session-$$}}" \
    --task "$TASK_ID" 2>/dev/null || true
fi
