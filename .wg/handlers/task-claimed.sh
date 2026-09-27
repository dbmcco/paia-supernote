#!/usr/bin/env bash
# ABOUTME: Handler invoked when an agent claims a workgraph task
# ABOUTME: Creates checkpoint, runs pre-task drift check, saves snapshot for outcome feedback

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"

# Create agentjj checkpoint for potential rollback
agentjj checkpoint "pre-task-$TASK_ID" 2>/dev/null || true

# Run pre-task drift check in JSON mode and save snapshot for outcome feedback loop.
# The snapshot is compared against the post-task check at task-completing time.
if [[ -n "$TASK_ID" ]]; then
  ACTOR_ID="${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-session-$$}}"
  PRE_CHECK_JSON=$("$WG_DIR/drifts" check --task "$TASK_ID" --write-log --json \
    --actor-id "$ACTOR_ID" --actor-class "${WG_ACTOR_CLASS:-interactive}" 2>/dev/null || echo "")
  if command -v driftdriver >/dev/null 2>&1 && [[ -n "$PRE_CHECK_JSON" ]]; then
    printf '%s' "$PRE_CHECK_JSON" | \
      driftdriver --dir "$PROJECT_DIR" save-check-snapshot --task-id "$TASK_ID" 2>/dev/null || true
  fi
fi

# Prime agent with relevant knowledge from distilled learnings
# This reads knowledge.jsonl (populated by distill_drift_knowledge) and
# logs the most relevant facts to the task so the agent can see them.
if command -v driftdriver >/dev/null 2>&1; then
  PRIMED=$(driftdriver --dir "$PROJECT_DIR" prime 2>/dev/null || echo "")
  if [[ -n "$PRIMED" ]]; then
    wg_log "$TASK_ID" "prior-knowledge: $PRIMED"
  fi
fi

# Enrich task contract with relevant prior learnings
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]]; then
  ENRICHED=$(driftdriver --dir "$PROJECT_DIR" wire enrich \
    --task-id "$TASK_ID" \
    --task-description "${WG_TASK_DESCRIPTION:-}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || echo "")
  if [[ -n "$ENRICHED" ]]; then
    wg_log "$TASK_ID" "contract-enriched: $ENRICHED"
  fi
fi

# Inject quality briefing so the agent knows its track record
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]]; then
  QUALITY=$(driftdriver --dir "$PROJECT_DIR" quality briefing \
    --actor-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-session-$$}}" 2>/dev/null || echo "")
  if [[ -n "$QUALITY" && "$QUALITY" != "{}" ]]; then
    echo "=== Quality Briefing ==="
    echo "$QUALITY"
    echo "========================"
    wg_log "$TASK_ID" "quality-briefing: $QUALITY"
  fi
fi

# Record task claim event immediately to lessons.db (real-time learning)
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "task_claimed" \
    --content "Task $TASK_ID claimed by $CLI_TOOL" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

# Emit task.claimed to events.jsonl so the factory brain tracks task lifecycle
EVENTS_FILE="$PROJECT_DIR/.workgraph/service/runtime/events.jsonl"
if [[ -d "$(dirname "$EVENTS_FILE")" ]]; then
  REPO_NAME="$(basename "$PROJECT_DIR")"
  TS="$(date +%s.%N 2>/dev/null || date +%s)"
  echo "{\"kind\":\"task.claimed\",\"repo\":\"$REPO_NAME\",\"ts\":$TS,\"payload\":{\"task\":\"$TASK_ID\",\"cli\":\"$CLI_TOOL\"}}" >> "$EVENTS_FILE" 2>/dev/null || true
fi

wg_log "$TASK_ID" "task-claimed: cli=$CLI_TOOL checkpoint=pre-task-$TASK_ID"
