#!/usr/bin/env bash
set -euo pipefail

wg add --id supernote-cloud-ledger.root 'Anchor approved Supernote ledger/read-write contract and Workgraph readiness' --description '```wg-contract
schema = 1
mode = "core"
objective = "Anchor approved Supernote ledger/read-write contract and Workgraph readiness"
touch = [".workgraph/planforge/20260719-124404/implementation-contract.md"]
acceptance = ["The approved design file from commit d111038 or current docs is identified by path.", "Existing modules for state storage, ingest, OCR, uploader, CLI, events, and API-like surfaces are mapped.", "The task output records unrelated dirty files and a policy not to commit them.", "The stale April speedriftd lease is released or refreshed, daemon_state is confirmed non-stalled before dispatch, or this plan is explicitly recorded as manual/observe Pi execution.", "Free worker capacity or manual-runtime fallback is recorded before materializing nodes."]
verify = ["git status --short --branch", "wg quickstart", "wg status", "driftdriver --dir /Users/braydon/projects/experiments/paia-supernote --json speedriftd status --refresh", "find docs -maxdepth 3 -type f | sort | grep -i '"'"'supernote\\|ledger\\|contract'"'"' || true"]
model_role = "contract architect"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "archdrift", "agencydrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.root --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.root --write-log --create-followups"
```

```agencydrift
profile = "agency:contract-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Read the approved design, current source modules, git state, and Workgraph/speedriftd state; resolve stale lease readiness before materializing implementation nodes.

Read First:
- AGENTS.md
- CLAUDE.md
- README.md
- pyproject.toml
- docs/
- src/paia_supernote/page_state.py
- src/paia_supernote/cloud_poller.py
- src/paia_supernote/ingest_service.py
- src/paia_supernote/uploader.py
- src/paia_supernote/cli.py
- src/paia_supernote/events.py

Implementation Steps:
- Run repo, Workgraph, and speedriftd orientation commands.
- If speedriftd is stalled with a stale lease, either refresh/release it for the scoped PlanForge session or record manual/observe Pi fallback before dispatch.
- Confirm worker capacity or document that nodes will be executed manually through Pi rather than autonomous speedriftd.
- Locate and summarize the approved cloud change-ledger design.
- Map source files and tests that already implement adjacent behavior.
- Record implementation order, non-goals, dirty-file guardrail, and API-surface availability.

Edge Cases:
- Design doc has moved or was renamed.
- Workgraph service is running but speedriftd status is stalled.
- Pre-existing dirty files overlap with intended touch set.
- No in-repo API surface exists beyond CLI and events.

Rollback:
This task should only create a plan artifact; remove the artifact if inaccurate.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Approved design cannot be found.
- Existing source lacks any identifiable write route or state store.
- Dirty files overlap with ledger implementation files.
- speedriftd cannot be recovered and manual fallback is not acceptable to the operator.

Acceptance:
- The approved design file from commit d111038 or current docs is identified by path.
- Existing modules for state storage, ingest, OCR, uploader, CLI, events, and API-like surfaces are mapped.
- The task output records unrelated dirty files and a policy not to commit them.
- The stale April speedriftd lease is released or refreshed, daemon_state is confirmed non-stalled before dispatch, or this plan is explicitly recorded as manual/observe Pi execution.
- Free worker capacity or manual-runtime fallback is recorded before materializing nodes.
' --model zai/glm-5.2
wg add --id supernote-cloud-ledger.storage 'Implement durable Cloud change ledger storage' --description '```wg-contract
schema = 1
mode = "core"
objective = "Implement durable Cloud change ledger storage"
touch = ["src/paia_supernote/cloud_change_ledger.py", "src/paia_supernote/page_state.py", "src/paia_supernote/config.py", "tests/test_cloud_change_ledger.py", "tests/test_page_state.py"]
acceptance = ["Ledger schema persists notebook snapshots, page snapshots, change events, and per-agent cursor state.", "Diff logic distinguishes added, updated, removed, and reordered pages.", "Reorder-only diffs preserve content hashes and do not mark pages as content-updated.", "Configuration exposes an allowlist without breaking existing folio_sync_notebooks behavior.", "Tests prove legacy folio_sync_notebooks config still works when allowlist is unset.", "Tests prove migration/opening of an existing state_db_path fixture is idempotent and preserves prior page_state rows."]
verify = ["uv run pytest -q tests/test_cloud_change_ledger.py tests/test_page_state.py", "uv run ruff check src/paia_supernote/cloud_change_ledger.py src/paia_supernote/page_state.py src/paia_supernote/config.py tests/test_cloud_change_ledger.py tests/test_page_state.py"]
model_role = "shared-contract implementer"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=strong"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "qadrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.storage --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.storage --write-log --create-followups"
```

```agencydrift
profile = "agency:data-contract-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Add SQLite-backed storage for allowlisted notebook snapshots, stable page IDs, ordered page hashes, change events, and per-agent cursor state.

Read First:
- src/paia_supernote/page_state.py
- src/paia_supernote/config.py
- tests/test_page_state.py
- README.md

Implementation Steps:
- Write failing tests for initial snapshot, unchanged snapshot, add, update, remove, reorder, cursor persistence, legacy config compatibility, and migration idempotency.
- Implement minimal domain models for snapshots, page revisions, change events, and cursors.
- Add SQLite DDL/migration logic compatible with temporary test databases and existing state_db_path.
- Wire allowlist config parsing with defaults preserving folio_sync_notebooks behavior.
- Run focused tests and ruff.

Edge Cases:
- Duplicate notebook names with different Cloud paths.
- Page metadata lacks a stable page ID and requires deterministic fallback.
- Notebook page count is zero.
- Cursor points to a pruned or unknown ledger revision.
- Existing state DB lacks new ledger tables.

Rollback:
Revert the ledger module and any page_state/config edits; no production database should be migrated during tests.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Existing state schema cannot be extended without destructive migration.
- Stable page identity cannot be represented from available note parsing evidence.

Acceptance:
- Ledger schema persists notebook snapshots, page snapshots, change events, and per-agent cursor state.
- Diff logic distinguishes added, updated, removed, and reordered pages.
- Reorder-only diffs preserve content hashes and do not mark pages as content-updated.
- Configuration exposes an allowlist without breaking existing folio_sync_notebooks behavior.
- Tests prove legacy folio_sync_notebooks config still works when allowlist is unset.
- Tests prove migration/opening of an existing state_db_path fixture is idempotent and preserves prior page_state rows.
' --model zai/glm-5.2 --after supernote-cloud-ledger.root
wg add --id supernote-cloud-ledger.ingest-diff 'Wire allowlisted Cloud ingest to ledger diffs and OCR skip logic' --description '```wg-contract
schema = 1
mode = "core"
objective = "Wire allowlisted Cloud ingest to ledger diffs and OCR skip logic"
touch = ["src/paia_supernote/cloud_poller.py", "src/paia_supernote/ingest_service.py", "src/paia_supernote/reader.py", "src/paia_supernote/cloud_change_ledger.py", "tests/test_ingest_change_ledger.py"]
acceptance = ["Non-allowlisted Cloud notebooks are skipped before download/OCR where existing API data allows.", "Allowlisted notebooks create ledger snapshots and change events.", "OCR spy tests prove only new or content-updated pages are transcribed.", "Reordered pages update ordering state without OCR."]
verify = ["uv run pytest -q tests/test_ingest_change_ledger.py tests/test_cloud_change_ledger.py", "uv run ruff check src/paia_supernote/cloud_poller.py src/paia_supernote/ingest_service.py src/paia_supernote/reader.py src/paia_supernote/cloud_change_ledger.py tests/test_ingest_change_ledger.py"]
model_role = "cross-system ingest implementer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=high, complexity=cross_system, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "qadrift", "archdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.ingest-diff --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.ingest-diff --write-log --create-followups"
```

```agencydrift
profile = "agency:cross-system-implementation-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Integrate the ledger with Cloud polling and ingest so only allowlisted notebooks are snapshotted and only new or content-changed pages are OCRed.

Read First:
- src/paia_supernote/cloud_poller.py
- src/paia_supernote/ingest_service.py
- src/paia_supernote/reader.py
- src/paia_supernote/page_state.py
- tests/test_ingest_service.py
- tests/test_cloud_poller.py

Implementation Steps:
- Add failing tests with mocked CloudPoller data and OCR spy readers.
- Extract page stable IDs and content hashes during ingest using existing reader/parser boundaries.
- Call ledger diffing before OCR and pass only changed pages to transcription.
- Persist OCR results against the new page revision identity while preserving existing enrichment state behavior.
- Run focused ingest and ledger tests.

Edge Cases:
- Cloud update_time changes but page hashes do not.
- Notebook binary changes only in metadata.
- Reader cannot extract one page image.
- Allowlist uses notebook display name while Cloud uses filename or path.

Rollback:
Revert ingest wiring and leave storage tests only if still useful; do not alter Cloud auth or upload code from this task.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- OCR skip logic would require changing reader public APIs beyond approved scope.
- Existing ingest pipeline cannot map OCR results to stable page identity.

Acceptance:
- Non-allowlisted Cloud notebooks are skipped before download/OCR where existing API data allows.
- Allowlisted notebooks create ledger snapshots and change events.
- OCR spy tests prove only new or content-updated pages are transcribed.
- Reordered pages update ordering state without OCR.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.storage
wg add --id supernote-cloud-ledger.read-contract 'Implement cached agent read contract and cursors' --description '```wg-contract
schema = 1
mode = "core"
objective = "Implement cached agent read contract and cursors"
touch = ["src/paia_supernote/agent_read_contracts.py", "src/paia_supernote/cloud_change_ledger.py", "src/paia_supernote/cli.py", "tests/test_agent_read_contracts.py"]
acceptance = ["Agents can read cached latest allowlisted notebook state without Cloud access.", "Agents can request changes since an explicit cursor.", "Per-agent cursors can be read, advanced, and isolated between agent IDs.", "Invalid, stale, or unknown cursors produce structured contract errors rather than tracebacks."]
verify = ["uv run pytest -q tests/test_agent_read_contracts.py tests/test_cloud_change_ledger.py", "uv run ruff check src/paia_supernote/agent_read_contracts.py src/paia_supernote/cloud_change_ledger.py src/paia_supernote/cli.py tests/test_agent_read_contracts.py"]
model_role = "agent contract implementer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=high, complexity=cross_system, context=medium, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "qadrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.read-contract --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.read-contract --write-log --create-followups"
```

```agencydrift
profile = "agency:agent-contract-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Add public read-contract functions for latest cached notebook state, changes since explicit cursor, and per-agent cursor advancement.

Read First:
- src/paia_supernote/cloud_change_ledger.py
- src/paia_supernote/cli.py
- src/paia_supernote/events.py
- README.md

Implementation Steps:
- Write tests for explicit cursor reads, per-agent cursor reads, cursor advancement, and cached latest reads.
- Implement a small public contract module with Pydantic request/response models.
- Wire minimal CLI access if an existing CLI read path exists; otherwise expose functions for surface adapters.
- Ensure contract methods do not instantiate CloudPoller or perform network access.
- Run focused tests.

Edge Cases:
- Agent ID contains spaces or path-like characters.
- Cursor references an unknown notebook.
- Notebook has no changes since cursor.
- Requested notebook is not allowlisted.

Rollback:
Remove the new contract module and CLI wiring; ledger storage remains independently testable.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- The approved design requires a different cursor shape than storage implemented.
- Read contract needs network fallback, which is out of scope.

Acceptance:
- Agents can read cached latest allowlisted notebook state without Cloud access.
- Agents can request changes since an explicit cursor.
- Per-agent cursors can be read, advanced, and isolated between agent IDs.
- Invalid, stale, or unknown cursors produce structured contract errors rather than tracebacks.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.storage
wg add --id supernote-cloud-ledger.write-safety 'Harden write route with base-revision concurrency checks' --description '```wg-contract
schema = 1
mode = "core"
objective = "Harden write route with base-revision concurrency checks"
touch = ["src/paia_supernote/uploader.py", "src/paia_supernote/notebook_writer.py", "src/paia_supernote/quick_filing_service.py", "src/paia_supernote/agent_write_contracts.py", "tests/test_write_concurrency.py"]
acceptance = ["Write requests require base revision information unless an existing internal safe path is explicitly exempted and documented.", "Stale, missing, or mismatched base revisions fail before upload/apply/S3 calls.", "Valid current base revisions preserve existing upload sequencing and dry-run behavior.", "Conflict errors include current revision, requested base revision, notebook identity, and next actions.", "The output documents the residual TOCTOU window between last ledger poll and write if no live pre-write fetch is added."]
verify = ["uv run pytest -q tests/test_write_concurrency.py tests/test_cloud_change_ledger.py", "uv run ruff check src/paia_supernote/uploader.py src/paia_supernote/notebook_writer.py src/paia_supernote/quick_filing_service.py src/paia_supernote/agent_write_contracts.py tests/test_write_concurrency.py"]
model_role = "write-safety implementer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=critical, complexity=cross_system, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "secdrift", "qadrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.write-safety --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.write-safety --write-log --create-followups"
```

```agencydrift
profile = "agency:write-safety-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Require base revision validation before any notebook write/upload path and fail closed with conflict details when the cached ledger revision is stale or mismatched.

Read First:
- src/paia_supernote/uploader.py
- src/paia_supernote/notebook_writer.py
- src/paia_supernote/quick_filing_service.py
- src/paia_supernote/note_page_ops.py
- tests/test_uploader.py
- tests/test_quick_filing_service.py

Implementation Steps:
- Write failing tests with uploader spies proving conflict checks happen before upload calls.
- Create minimal write request/response models if they do not already exist.
- Thread base revision validation through existing write entry points without changing page operation semantics.
- Preserve dry-run behavior and existing upload API signatures where possible with backward-compatible wrappers only when safe.
- Document poll-interval bounded revision freshness and do not add live Cloud mutation checks unless the approved design requires them.
- Run focused tests.

Edge Cases:
- Ledger has no snapshot for requested notebook.
- Cloud notebook changed after agent cached a revision.
- Dry-run write with stale base revision.
- Older internal code path calls uploader directly without a write contract.

Rollback:
Revert write contract and uploader guard changes; never run live Cloud upload during rollback verification.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Base revision cannot be checked without contacting Cloud during writes.
- Existing public uploader API would break for non-agent callers without a migration plan.

Acceptance:
- Write requests require base revision information unless an existing internal safe path is explicitly exempted and documented.
- Stale, missing, or mismatched base revisions fail before upload/apply/S3 calls.
- Valid current base revisions preserve existing upload sequencing and dry-run behavior.
- Conflict errors include current revision, requested base revision, notebook identity, and next actions.
- The output documents the residual TOCTOU window between last ledger poll and write if no live pre-write fetch is added.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.storage
wg add --id supernote-cloud-ledger.structured-errors 'Implement structured error guidance contract' --description '```wg-contract
schema = 1
mode = "core"
objective = "Implement structured error guidance contract"
touch = ["src/paia_supernote/contract_errors.py", "src/paia_supernote/agent_read_contracts.py", "src/paia_supernote/agent_write_contracts.py", "src/paia_supernote/cli.py", "src/paia_supernote/events.py", "tests/test_structured_errors.py", "tests/test_cli_structured_errors.py"]
acceptance = ["Error models include code, message, retryable, next_actions, and details.", "Write conflicts include revision comparison details.", "Allowlist and cursor errors are serializable as JSON for agents and readable in CLI output.", "No surface emits raw tracebacks for expected contract failures."]
verify = ["uv run pytest -q tests/test_structured_errors.py tests/test_cli_structured_errors.py tests/test_agent_read_contracts.py tests/test_write_concurrency.py", "uv run ruff check src/paia_supernote/contract_errors.py src/paia_supernote/agent_read_contracts.py src/paia_supernote/agent_write_contracts.py src/paia_supernote/cli.py src/paia_supernote/events.py tests/test_structured_errors.py tests/test_cli_structured_errors.py"]
model_role = "structured error contract implementer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=critical, complexity=cross_system, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "uxdrift", "secdrift", "qadrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.structured-errors --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.structured-errors --write-log --create-followups"
```

```agencydrift
profile = "agency:structured-output-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Define Pydantic/Instructor-style structured errors and use them for ledger, cursor, allowlist, auth, and write-conflict failures.

Read First:
- src/paia_supernote/cli.py
- src/paia_supernote/events.py
- src/paia_supernote/user_board.py
- src/paia_supernote/agent_read_contracts.py
- src/paia_supernote/agent_write_contracts.py

Implementation Steps:
- Write tests for model serialization and expected failure classes.
- Implement compact Pydantic models and helpers to convert exceptions into response payloads.
- Replace expected contract exceptions in read and write paths with structured errors.
- Wire CLI formatting so human output and JSON output remain deterministic.
- Run focused error tests.

Edge Cases:
- Nested low-level exception should not leak secrets or auth cookies.
- Multiple next actions for a conflict must remain ordered and deterministic.
- CLI exits nonzero while still returning machine-readable JSON when requested.

Rollback:
Revert contract_errors and surface conversion changes; keep lower-level tests to ensure old behavior has not regressed before retrying.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Existing API framework requires a different response envelope.
- Structured errors need to cross a service boundary not represented in current source.

Acceptance:
- Error models include code, message, retryable, next_actions, and details.
- Write conflicts include revision comparison details.
- Allowlist and cursor errors are serializable as JSON for agents and readable in CLI output.
- No surface emits raw tracebacks for expected contract failures.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.read-contract --after supernote-cloud-ledger.write-safety
wg add --id supernote-cloud-ledger.surface-adapters 'Expose read/write contracts through existing CLI, event, and API-like surfaces' --description '```wg-contract
schema = 1
mode = "core"
objective = "Expose read/write contracts through existing CLI, event, and API-like surfaces"
touch = ["src/paia_supernote/cli.py", "src/paia_supernote/events.py", "src/paia_supernote/user_board.py", "src/paia_supernote/main.py", "tests/test_cli_structured_errors.py", "tests/integration/test_agent_contract_surfaces.py"]
acceptance = ["CLI exposes cached read/change/cursor behavior or updates existing commands to call the contract.", "Existing in-repo event and API-like surfaces that actually exist can return contract responses and structured errors where applicable.", "If no in-repo HTTP API exists, the task records that obligation as a follow-up instead of asserting unverifiable behavior.", "No new broad organizer workflow or live Cloud mutation path is introduced."]
verify = ["uv run pytest -q tests/test_cli_structured_errors.py", "uv run pytest -q -m integration tests/integration/test_agent_contract_surfaces.py", "uv run ruff check src/paia_supernote/cli.py src/paia_supernote/events.py src/paia_supernote/user_board.py src/paia_supernote/main.py tests/test_cli_structured_errors.py tests/integration/test_agent_contract_surfaces.py"]
model_role = "surface integration implementer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=high, complexity=cross_system, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "specdrift", "datadrift", "uxdrift", "qadrift", "archdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.surface-adapters --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.surface-adapters --write-log --create-followups"
```

```agencydrift
profile = "agency:integration-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Wire cached read, cursor, and base-revision guarded write contracts into existing in-repo user-facing and agent-facing entry points without adding product scope.

Read First:
- src/paia_supernote/cli.py
- src/paia_supernote/events.py
- src/paia_supernote/user_board.py
- src/paia_supernote/main.py
- README.md

Implementation Steps:
- Inspect current CLI/event/API-like entry points and choose minimal existing hooks.
- Add fixture-backed tests for each reachable surface.
- Wire commands or handlers to read/write contract modules.
- Normalize expected failures through structured error rendering.
- Record absent API surfaces as follow-up rather than adding a new server framework.
- Run surface unit and integration tests.

Edge Cases:
- An expected API surface is absent or implemented outside paia_supernote.
- Event service is unavailable in local tests.
- CLI must preserve existing command names and exit codes.

Rollback:
Revert surface wiring only; leave core contract modules intact for isolated verification.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Surface wiring requires adding a new server framework.
- Existing CLI/event semantics would be broken for current users.

Acceptance:
- CLI exposes cached read/change/cursor behavior or updates existing commands to call the contract.
- Existing in-repo event and API-like surfaces that actually exist can return contract responses and structured errors where applicable.
- If no in-repo HTTP API exists, the task records that obligation as a follow-up instead of asserting unverifiable behavior.
- No new broad organizer workflow or live Cloud mutation path is introduced.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.structured-errors
wg add --id supernote-cloud-ledger.unit-tests 'Complete focused unit test suite for ledger, ingest, and contracts' --description '```wg-contract
schema = 1
mode = "core"
objective = "Complete focused unit test suite for ledger, ingest, and contracts"
touch = ["tests/test_cloud_change_ledger.py", "tests/test_ingest_change_ledger.py", "tests/test_agent_read_contracts.py", "tests/test_write_concurrency.py", "tests/test_structured_errors.py", "tests/test_cli_structured_errors.py", "tests/test_page_state.py"]
acceptance = ["All required unit tests pass deterministically with temporary state.", "Tests assert no OCR calls for reorder-only diffs.", "Tests assert stale write conflicts happen before uploader calls.", "Tests assert structured errors are stable JSON-compatible payloads.", "Tests assert legacy config and state migration compatibility."]
verify = ["uv run pytest -q tests/test_cloud_change_ledger.py tests/test_ingest_change_ledger.py tests/test_agent_read_contracts.py tests/test_write_concurrency.py tests/test_structured_errors.py tests/test_cli_structured_errors.py tests/test_page_state.py"]
model_role = "contract test implementer"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=strong"
model_escalation_route = "pi:premium"
handoff_detail = "medium"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "qadrift", "specdrift", "datadrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.unit-tests --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.unit-tests --write-log --create-followups"
```

```agencydrift
profile = "agency:qa-contract-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Backfill and tighten unit tests for ledger diffing, ingest OCR skip, cursor reads, cached reads, write conflicts, migration compatibility, and structured error serialization.

Read First:
- tests/
- src/paia_supernote/cloud_change_ledger.py
- src/paia_supernote/ingest_service.py
- src/paia_supernote/agent_read_contracts.py
- src/paia_supernote/agent_write_contracts.py
- src/paia_supernote/contract_errors.py

Implementation Steps:
- Review tests added by implementation nodes for missing edge cases.
- Add focused tests only where behavior lacks coverage.
- Use spies/mocks for OCR and upload boundaries.
- Run the unit contract suite.

Edge Cases:
- Tests accidentally require live credentials.
- Temporary SQLite files leak across tests.
- Assertions are too coupled to implementation internals rather than contract behavior.

Rollback:
Revert only brittle or redundant test additions; do not weaken tests that encode approved safety contracts.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Tests require major source rewrites to become deterministic.
- A public contract behavior remains ambiguous after reading the design.
- Tests uncover a possible unsafe live-upload path.

Acceptance:
- All required unit tests pass deterministically with temporary state.
- Tests assert no OCR calls for reorder-only diffs.
- Tests assert stale write conflicts happen before uploader calls.
- Tests assert structured errors are stable JSON-compatible payloads.
- Tests assert legacy config and state migration compatibility.
' --model zai/glm-5.2 --after supernote-cloud-ledger.ingest-diff --after supernote-cloud-ledger.surface-adapters
wg add --id supernote-cloud-ledger.integration-tests 'Add offline integration tests for Cloud ledger read/write contract' --description '```wg-contract
schema = 1
mode = "core"
objective = "Add offline integration tests for Cloud ledger read/write contract"
touch = ["tests/integration/test_cloud_change_contract.py", "tests/integration/test_agent_contract_surfaces.py", "tests/fixtures/supernote_ledger/", "tests/conftest.py"]
acceptance = ["Integration tests use mocks/fixtures and temporary databases only.", "A two-poll scenario records added/updated/removed/reordered events correctly.", "OCR spy call counts prove only new/changed pages are OCRed.", "A stale write request returns structured conflict guidance and never calls uploader upload."]
verify = ["uv run pytest -q -m integration tests/integration/test_cloud_change_contract.py tests/integration/test_agent_contract_surfaces.py"]
model_role = "integration test implementer"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "qadrift", "datadrift", "secdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.integration-tests --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.integration-tests --write-log --create-followups"
```

```agencydrift
profile = "agency:integration-qa-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Create fixture-backed integration tests for allowlist ingest, ledger diff, OCR skip, cached read, surface error, and write conflict flow without real Cloud mutation.

Read First:
- tests/fixtures/README.md
- tests/conftest.py
- tests/integration/
- src/paia_supernote/cloud_poller.py
- src/paia_supernote/ingest_service.py
- src/paia_supernote/uploader.py

Implementation Steps:
- Create fixture factories for notebook/page metadata and page bytes rather than proprietary real .note files.
- Build a mocked CloudPoller sequence for initial snapshot and changed snapshot.
- Add OCR and uploader spies with explicit call-count assertions.
- Exercise cached read and surface structured error responses.
- Run the scoped integration command.

Edge Cases:
- Existing integration marker excludes tests from default pytest.
- Fixture page identity does not match reader parsing assumptions.
- Mocked upload path accidentally bypasses concurrency validation.

Rollback:
Remove integration fixtures and tests if invalid; do not alter production code from this task except tiny testability seams approved by prior nodes.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Offline fixtures cannot represent required page identity semantics.
- Integration requires live Supernote Cloud credentials.

Acceptance:
- Integration tests use mocks/fixtures and temporary databases only.
- A two-poll scenario records added/updated/removed/reordered events correctly.
- OCR spy call counts prove only new/changed pages are OCRed.
- A stale write request returns structured conflict guidance and never calls uploader upload.
' --model zai/glm-5.2 --after supernote-cloud-ledger.ingest-diff --after supernote-cloud-ledger.unit-tests
wg add --id supernote-cloud-ledger.cleanup-docs 'Update docs and remove overbuild/dependency drift' --description '```wg-contract
schema = 1
mode = "core"
objective = "Update docs and remove overbuild/dependency drift"
touch = ["README.md", "docs/supernote-cloud-change-ledger-contracts.md", "src/paia_supernote/cloud_change_ledger.py", "src/paia_supernote/agent_read_contracts.py", "src/paia_supernote/agent_write_contracts.py", "src/paia_supernote/contract_errors.py", "tests/test_cloud_change_ledger.py", "tests/test_ingest_change_ledger.py", "tests/test_agent_read_contracts.py", "tests/test_write_concurrency.py", "tests/test_structured_errors.py", "tests/test_cli_structured_errors.py", "tests/integration/test_cloud_change_contract.py", "tests/integration/test_agent_contract_surfaces.py"]
acceptance = ["README or design docs describe allowlist config, cached reads, cursors, base-revision writes, structured errors, and the poll-interval bounded TOCTOU window.", "No unused temporary scaffolding, dead abstractions, or debug scripts are left in scoped source/tests.", "uv.lock is unchanged unless a task output explains an unavoidable dependency reason."]
verify = ["uv run ruff check src/ tests/", "git diff --stat", "git diff -- uv.lock"]
model_role = "cleanup and docs implementer"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "medium"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "yagnidrift", "specdrift", "depsdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.cleanup-docs --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.cleanup-docs --write-log --create-followups"
```

```agencydrift
profile = "agency:yagni-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Document config and agent contracts, remove unused scaffolding introduced during implementation, and ensure uv.lock is unchanged unless justified.

Read First:
- README.md
- docs/
- pyproject.toml
- uv.lock
- src/paia_supernote/cloud_change_ledger.py
- src/paia_supernote/agent_read_contracts.py
- src/paia_supernote/agent_write_contracts.py
- src/paia_supernote/contract_errors.py
- tests/

Implementation Steps:
- Update only user-relevant docs for the implemented contract.
- Search for unused helpers or TODO scaffolding introduced by prior tasks.
- Remove dead code with tests still passing.
- Inspect uv.lock diff and revert unrelated lockfile churn unless a dependency change is explicitly justified.

Edge Cases:
- Docs file from approved design should be amended, not duplicated.
- A helper appears unused but is part of a public API.
- uv.lock was dirty before the session.

Rollback:
Revert docs and cleanup edits separately from functional source changes if they obscure behavior.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Cleanup would require changing behavior not covered by tests.
- Lockfile changes appear necessary despite no dependency plan.

Acceptance:
- README or design docs describe allowlist config, cached reads, cursors, base-revision writes, structured errors, and the poll-interval bounded TOCTOU window.
- No unused temporary scaffolding, dead abstractions, or debug scripts are left in scoped source/tests.
- uv.lock is unchanged unless a task output explains an unavoidable dependency reason.
' --model zai/glm-5.2 --after supernote-cloud-ledger.integration-tests
wg add --id supernote-cloud-ledger.final-gates 'Run final drift, dependency, and quality gates' --description '```wg-contract
schema = 1
mode = "core"
objective = "Run final drift, dependency, and quality gates"
touch = [".workgraph/planforge/20260719-124404/final-gates.md"]
acceptance = ["All final verification commands pass or failures are captured for the fix-loop node.", "Final gate artifact records commands, exit signals, unrelated dirty files, dependency drift, and unresolved risks.", "No command mutates real Supernote Cloud notebooks.", "Any secdrift/write-safety finding blocks handoff until fixed."]
verify = ["uv run ruff check src/ tests/", "uv run pytest -q", "uv run pytest -q -m integration tests/integration/test_cloud_change_contract.py tests/integration/test_agent_contract_surfaces.py", "git diff --check", "git diff -- uv.lock"]
model_role = "drift gate runner"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=strong"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "qadrift", "specdrift", "datadrift", "yagnidrift", "secdrift", "depsdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.final-gates --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.final-gates --write-log --create-followups"
```

```agencydrift
profile = "agency:qa-drift-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Run lint, unit tests, integration tests, spec/sec/deps/YAGNI drift, and safe-upload preservation checks before review.

Read First:
- .workgraph/planforge/20260719-124404/implementation-contract.md
- pyproject.toml
- README.md
- tests/
- src/paia_supernote/

Implementation Steps:
- Run final quality commands exactly as listed.
- Record outputs and first failures in the final gate artifact.
- Check git diff for unrelated files, lockfile churn, and generated artifacts.
- Create repair input for the fix-loop node if anything fails.

Edge Cases:
- Integration tests are skipped by default pytest marker and need explicit command.
- Ruff reports pre-existing issues outside touched files.
- Unrelated dirty files remain in the working tree.

Rollback:
This node should only write gate artifacts; delete inaccurate artifacts and rerun commands.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Same failure class recurs after two repairs.
- A final gate implies possible live-data mutation risk.
- A secdrift finding suggests stale writes can reach upload.

Acceptance:
- All final verification commands pass or failures are captured for the fix-loop node.
- Final gate artifact records commands, exit signals, unrelated dirty files, dependency drift, and unresolved risks.
- No command mutates real Supernote Cloud notebooks.
- Any secdrift/write-safety finding blocks handoff until fixed.
' --model zai/glm-5.2 --after supernote-cloud-ledger.cleanup-docs
wg add --id supernote-cloud-ledger.adversarial-review 'Bounded adversarial review of ledger and write safety' --description '```wg-contract
schema = 1
mode = "core"
objective = "Bounded adversarial review of ledger and write safety"
touch = [".workgraph/planforge/20260719-124404/adversarial-review.md", ".workgraph/planforge/20260719-124404/roborev.md"]
acceptance = ["Review explicitly checks stale write race, invalid cursor, reorder-only OCR, allowlist bypass, structured error guidance, dependency drift, and absence of live mutation tests.", "Review is bounded to two rounds and does not request broad rewrites outside approved scope.", "Blocking findings are converted into fix-loop tasks or explicitly recorded as accepted residual risks.", "The cached-ledger TOCTOU window is accepted, documented, or escalated if it violates the approved design."]
verify = ["test -s .workgraph/planforge/20260719-124404/adversarial-review.md", "test -s .workgraph/planforge/20260719-124404/roborev.md"]
model_role = "bounded adversarial reviewer"
model_route = "pi:premium"
model = "openai-codex/gpt-5.5"
model_tier = "premium"
model_fit = "risk=critical, complexity=cross_system, context=large, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["reviewdrift", "secdrift", "datadrift", "yagnidrift", "agencydrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.adversarial-review --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.adversarial-review --write-log --create-followups"
```

```agencydrift
profile = "agency:adversarial-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Perform a bounded adversarial review focused on stale writes, cursor confusion, unsafe Cloud mutation, structured error recoverability, overbuild, dependency drift, and declared TOCTOU residual risk.

Read First:
- .workgraph/planforge/20260719-124404/final-gates.md
- src/paia_supernote/cloud_change_ledger.py
- src/paia_supernote/agent_read_contracts.py
- src/paia_supernote/agent_write_contracts.py
- src/paia_supernote/contract_errors.py
- tests/

Implementation Steps:
- Read final gate artifact and diffs.
- Attack stale base revision, cursor replay, allowlist bypass, reorder-only OCR, dependency drift, and expected auth/conflict errors.
- Run or cite focused commands where needed.
- Write roborev and adversarial review artifacts with blocking, nonblocking, and no-op findings.

Edge Cases:
- Reviewer discovers issue requiring live Cloud validation.
- Review proposes broad UI or organizer automation outside scope.
- Review duplicates an already-tested scenario without new evidence.

Rollback:
Review artifacts can be superseded by a new round; do not modify code directly in this node.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- A blocking safety finding cannot be reproduced with fixtures.
- Review identifies architectural contradiction with the approved design.

Acceptance:
- Review explicitly checks stale write race, invalid cursor, reorder-only OCR, allowlist bypass, structured error guidance, dependency drift, and absence of live mutation tests.
- Review is bounded to two rounds and does not request broad rewrites outside approved scope.
- Blocking findings are converted into fix-loop tasks or explicitly recorded as accepted residual risks.
- The cached-ledger TOCTOU window is accepted, documented, or escalated if it violates the approved design.
' --model openai-codex/gpt-5.5 --after supernote-cloud-ledger.final-gates
wg add --id supernote-cloud-ledger.fix-loop 'Repair blocking test, drift, or review findings' --description '```wg-contract
schema = 1
mode = "core"
objective = "Repair blocking test, drift, or review findings"
touch = ["src/paia_supernote/cloud_change_ledger.py", "src/paia_supernote/agent_read_contracts.py", "src/paia_supernote/agent_write_contracts.py", "src/paia_supernote/contract_errors.py", "src/paia_supernote/uploader.py", "src/paia_supernote/notebook_writer.py", "src/paia_supernote/quick_filing_service.py", "src/paia_supernote/ingest_service.py", "src/paia_supernote/cloud_poller.py", "src/paia_supernote/reader.py", "src/paia_supernote/cli.py", "src/paia_supernote/events.py", "src/paia_supernote/config.py", "src/paia_supernote/page_state.py", "README.md", "docs/supernote-cloud-change-ledger-contracts.md", "tests/test_cloud_change_ledger.py", "tests/test_ingest_change_ledger.py", "tests/test_agent_read_contracts.py", "tests/test_write_concurrency.py", "tests/test_structured_errors.py", "tests/test_cli_structured_errors.py", "tests/test_page_state.py", "tests/integration/test_cloud_change_contract.py", "tests/integration/test_agent_contract_surfaces.py", "tests/conftest.py", ".workgraph/planforge/20260719-124404/fix-loop.md"]
acceptance = ["All blocking findings are fixed or escalated with evidence.", "Exact failing commands from the gate or review pass after repair.", "No repair expands scope beyond named contract modules, tests, docs, or artifacts.", "Any touch outside the named touch set requires explicit authorization recorded in fix-loop.md."]
verify = ["uv run ruff check src/ tests/", "uv run pytest -q", "uv run pytest -q -m integration tests/integration/test_cloud_change_contract.py tests/integration/test_agent_contract_surfaces.py"]
model_role = "bounded repair implementer"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=high, complexity=shared_module, context=medium, verification=moderate"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["fixdrift", "coredrift", "qadrift", "reviewdrift", "secdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.fix-loop --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.fix-loop --write-log --create-followups"
```

```agencydrift
profile = "agency:repair-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Address blocking findings from tests, drift gates, or adversarial review with bounded repairs, then rerun exact failing commands.

Read First:
- .workgraph/planforge/20260719-124404/final-gates.md
- .workgraph/planforge/20260719-124404/adversarial-review.md
- .workgraph/planforge/20260719-124404/roborev.md
- git diff --stat output captured by prior nodes

Implementation Steps:
- Classify findings as blocking, advisory, duplicate, or out of scope.
- Patch only the smallest named source/test/doc set needed for blocking findings.
- For write-safety or secdrift findings, escalate to premium after one failed repair attempt.
- Rerun the exact command that failed before running broad gates.
- Record repair attempts and remaining risks.

Edge Cases:
- Finding requires live Cloud validation.
- Fix conflicts with approved design.
- Same failure recurs after two attempts.
- A needed file is outside the bounded touch set.

Rollback:
Use git diff to revert unsuccessful repair patches; do not revert unrelated successful contract implementation.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- One repair attempt fails for a write-safety/security finding.
- Two repair attempts fail for any non-security issue class.
- Repair requires new dependency, migration, or live Cloud access.
- Fix would weaken write-route safety or allowlist enforcement.

Acceptance:
- All blocking findings are fixed or escalated with evidence.
- Exact failing commands from the gate or review pass after repair.
- No repair expands scope beyond named contract modules, tests, docs, or artifacts.
- Any touch outside the named touch set requires explicit authorization recorded in fix-loop.md.
' --model zai/glm-5.2 --after supernote-cloud-ledger.adversarial-review
wg add --id supernote-cloud-ledger.handoff 'Final status, commit, push, and handoff' --description '```wg-contract
schema = 1
mode = "core"
objective = "Final status, commit, push, and handoff"
touch = [".workgraph/planforge/20260719-124404/handoff.md", "git index", "Workgraph task statuses"]
acceptance = ["Only scoped implementation, tests, docs, and plan artifacts are committed.", "Pre-existing unrelated dirty files are preserved or explicitly excluded from the commit.", "Quality gates pass immediately before commit.", "git push succeeds and final status reports branch up to date with origin or records any remote policy blocker.", "Handoff artifact lists commands run, changed files, follow-up issues, residual risks, and the cached-ledger TOCTOU note.", "Workgraph status is updated only if dispatch was confirmed; otherwise manual-runtime fallback is recorded."]
verify = ["git status --short --branch", "uv run ruff check src/ tests/", "uv run pytest -q", "uv run pytest -q -m integration tests/integration/test_cloud_change_contract.py tests/integration/test_agent_contract_surfaces.py", "git pull --rebase", "git push", "git status --short --branch"]
model_role = "release handoff operator"
model_route = "pi:standard"
model = "zai/glm-5.2"
model_tier = "standard"
model_fit = "risk=medium, complexity=shared_module, context=medium, verification=strong"
model_escalation_route = "pi:premium"
handoff_detail = "high"
small_model_ready = "no"
```

```specdrift
schema = 1
spec = ["/Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md"]
check = "does this task remain consistent with the end-state contract?"
```

```speedrift
modules = ["coredrift", "reviewdrift", "qadrift", "secdrift"]
pre_task = "./.workgraph/drifts check --task supernote-cloud-ledger.handoff --write-log --create-followups"
post_task = "./.workgraph/drifts check --task supernote-cloud-ledger.handoff --write-log --create-followups"
```

```agencydrift
profile = "agency:release-review-preferred-fallback-pi"
preferred_runtime = "pi"
fallback_runtime = "manual"
```

Verify final state, isolate unrelated dirty files, commit scoped changes, pull --rebase, push, update Workgraph status when dispatch was confirmed, and hand off residual risks.

Read First:
- .workgraph/planforge/20260719-124404/final-gates.md
- .workgraph/planforge/20260719-124404/adversarial-review.md
- .workgraph/planforge/20260719-124404/fix-loop.md
- AGENTS.md
- git status --short --branch

Implementation Steps:
- Run final verification commands.
- Review git diff and stage only scoped files.
- Create follow-up Workgraph tasks or issues for accepted residual work.
- Commit with a concise supernote-scoped message.
- Run git pull --rebase, resolve conflicts without touching unrelated files, then git push.
- Record final status, Workgraph update status, and handoff notes.

Edge Cases:
- Repository is already ahead of origin before this plan.
- Unrelated dirty files remain in worktree.
- Remote rebase conflicts with concurrent changes.
- Push is rejected by remote policy.
- Workgraph was executed manually because speedriftd remained stalled.

Rollback:
Before commit, unstage with git restore --staged; after a failed rebase, use git rebase --abort and replan if conflicts affect unrelated dirty files.

Roborev: required

Small Model Ready: no

Escalation Conditions:
- Final tests fail after a passing fix loop.
- git pull --rebase conflicts with unrelated dirty files.
- Push fails for credentials or remote policy.

Acceptance:
- Only scoped implementation, tests, docs, and plan artifacts are committed.
- Pre-existing unrelated dirty files are preserved or explicitly excluded from the commit.
- Quality gates pass immediately before commit.
- git push succeeds and final status reports branch up to date with origin or records any remote policy blocker.
- Handoff artifact lists commands run, changed files, follow-up issues, residual risks, and the cached-ledger TOCTOU note.
- Workgraph status is updated only if dispatch was confirmed; otherwise manual-runtime fallback is recorded.
' --model zai/glm-5.2 --after supernote-cloud-ledger.fix-loop
