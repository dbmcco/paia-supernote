#!/usr/bin/env bash
# ABOUTME: workgraph worktree-setup hook. Called by workgraph after git worktree
# ABOUTME: add and .wg symlink creation ($1 = worktree path, $2 = project root).
# ABOUTME: .workgraph is gitignored, so fresh worktrees lack executors and drift
# ABOUTME: wrappers. Link the control plane so the pi executor and speedrift
# ABOUTME: drift checks resolve inside the worktree via the .workgraph path.
set -euo pipefail

WORKTREE_PATH="${1:-$PWD}"
PROJECT_ROOT="${2:?project root required}"

# Detect the control-plane directory (.workgraph or .wg).
if [[ -d "$PROJECT_ROOT/.workgraph" ]]; then
  CONTROL_PLANE="$PROJECT_ROOT/.workgraph"
elif [[ -d "$PROJECT_ROOT/.wg" ]]; then
  CONTROL_PLANE="$PROJECT_ROOT/.wg"
else
  echo "[worktree-setup] WARNING: no .workgraph or .wg in $PROJECT_ROOT — skipping" >&2
  exit 0
fi

# Create .workgraph symlink in the worktree (workgraph already creates .wg).
LINK="$WORKTREE_PATH/.workgraph"
if [[ -L "$LINK" ]]; then
  existing=$(readlink "$LINK")
  if [[ "$existing" == "$CONTROL_PLANE" ]]; then
    exit 0  # Already correct
  fi
  rm -f "$LINK"  # Stale or broken — replace
elif [[ -e "$LINK" ]]; then
  exit 0  # Real directory exists — don't overwrite
fi
ln -s "$CONTROL_PLANE" "$LINK"
