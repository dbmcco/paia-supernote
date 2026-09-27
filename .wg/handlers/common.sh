#!/usr/bin/env bash
# ABOUTME: Shared utilities for driftdriver handler scripts
# ABOUTME: MCP invocation helpers, wg wrappers, CLI detection

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WG_DIR="$(dirname "$HANDLER_DIR")"
PROJECT_DIR="$(dirname "$WG_DIR")"

# Parse --cli flag
CLI_TOOL="unknown"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cli) CLI_TOOL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Record event immediately to lessons.db via driftdriver record-event
# For record/decision operations: writes directly to DB (real-time learning)
# For query operations (search_knowledge, get_project_context): no-op
lessons_mcp() {
  local tool_name="$1"
  shift
  local args="$1"
  case "$tool_name" in
    record_event|record_decision)
      local event_type content
      event_type=$(echo "$args" | jq -r '.event // .event_type // "observation"' 2>/dev/null || echo "observation")
      content=$(echo "$args" | jq -c '.' 2>/dev/null || echo "$args")
      if command -v driftdriver >/dev/null 2>&1; then
        driftdriver --dir "$PROJECT_DIR" record-event \
          --event-type "$event_type" \
          --content "$content" \
          --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
          --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
      fi
      ;;
    # Query operations are handled by their callers directly; no batch queue needed
    search_knowledge|get_project_context|flush_learnings)
      ;;
  esac
}

# Log to workgraph
wg_log() {
  local task_id="${1:-}"
  local message="$2"
  if [[ -n "$task_id" ]]; then
    wg log "$task_id" "$message" 2>/dev/null || true
  fi
}

# Get current task ID from environment or workgraph
current_task_id() {
  echo "${WG_TASK_ID:-$(wg status --json 2>/dev/null | jq -r '.in_progress[0].id // empty' 2>/dev/null || echo "")}"
}
