# Follow-up: agent-facing HTTP API surface for read/write contracts

Status: **recorded obligation** (not asserted by unverifiable tests)
Origin task: `supernote-cloud-ledger.surface-adapters`
Design spec: `docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md`

## Summary

The approved Cloud change ledger design calls for read/write contracts to be
exposed across "existing CLI, event, and API-like surfaces." This task wired the
surfaces that **actually exist in-repo**:

| Surface | Module | Wired? |
|---|---|---|
| CLI cached read/change/cursor | `src/paia_supernote/cli.py` (`supernote changes`) | ✅ |
| Event write-failure structured error | `src/paia_supernote/events.py` (`publish_write_failed`) | ✅ |
| Service write-safety guard | `src/paia_supernote/main.py` (`_handle_write_request`) | ✅ |
| Agent-facing HTTP API for read/write contracts | — | ❌ **does not exist** |

## No in-repo agent HTTP API exists

There is **no** in-repo HTTP server that exposes the cached read/write contracts
to agents:

- `src/paia_supernote/organizer_server.py` + `organizer_api.py` is a **human
  browser UI** (the Supernote Organizer). It renders page images and does not
  call `SupernoteReadContract`, `validate_agent_write_request`, or emit the
  shared structured error envelope.
- `src/paia_supernote/user_board.py` is a **human interactive TUI** (`input()`
  loop). Its write path publishes `supernote.write.requested` events over HTTP
  to `paia-events`; it is not itself an HTTP server and does not return contract
  responses.
- The only programmatic write surface is the **event-driven**
  `_handle_write_request` handler, which already runs the write-safety guard and
  publishes structured conflicts via `publish_write_failed`.

Per the surface-adapters acceptance contract, this obligation is recorded here
rather than asserting unverifiable behavior or inventing a new server framework.

## Decision (from the task contract)

> If no in-repo HTTP API exists, the task records that obligation as a follow-up
> instead of asserting unverifiable behavior.

Adding a new HTTP server framework is explicitly out of scope for this task and
a named escalation condition. It was **not** introduced.

## What a future task would need

Should an agent-facing HTTP API be desired later, it must:

1. Wrap `SupernoteReadContract` (latest state / changes / cursor) and
   `validate_agent_write_request` behind HTTP handlers.
2. Return the **same** shared structured error envelope
   (`contract_errors.AgentError` / `agent_error_json`) with stable codes,
   retryability, next actions, and conflict details.
3. Remain read-only / fail-closed: no live Cloud mutation, dry-run safety, and
   base-revision guard enforced before any upload.
4. Reuse the existing Pydantic request/response models in
   `agent_read_contracts.py` / `agent_write_contracts.py` rather than
   re-deriving them.

That work should be a **separate** Workgraph task with its own design, not a
silent addition to this one.
