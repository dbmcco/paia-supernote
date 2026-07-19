# Supernote Cloud Change Ledger and Agent Read/Write Contracts

## Status

Approved design. Implementation follows the existing `paia-supernote` ingest and write paths.

## Goal

Allow PAIA agents to query an allowlisted set of Supernote Cloud notebooks, see what changed since an explicit cursor or timestamp, or since that agent's last successful check, and read cached OCR for new or updated pages. Preserve the existing write route while making reads and writes use explicit, model-readable contracts with actionable repair guidance.

A typical result should say that two pages were added and a third page was updated, identify those pages, report OCR readiness, and provide the current notebook revision needed for a safe write.

## Scope and source of truth

Supernote Cloud is the canonical source for this feature. The local Partner-app watcher is not part of the first implementation. Only notebooks explicitly listed in configuration are processed. The service remains read-only with respect to Cloud for the change-ledger feature; existing write operations remain available through the established write route.

## Architecture

The existing `CloudPoller` remains responsible for detecting changed Cloud notebooks and downloading their bytes. The ingest path will then:

1. Parse the downloaded notebook.
2. Build a snapshot using stable Supernote page IDs, current page positions, and content hashes.
3. Compare the snapshot with the last successful snapshot for that notebook.
4. Persist the notebook revision, page snapshots, and change records atomically.
5. OCR only pages that are new or whose content hash changed.
6. Attach OCR results to the exact page revision.
7. Keep the last good snapshot if download, parsing, or persistence fails.

Queries read the durable ledger and cached OCR. They do not download and re-OCR the notebook again unless the caller explicitly requests a fresh read.

The existing `supernote read` command will use cached OCR when it matches the current page revision. A fresh-read option will remain available for explicit reprocessing.

## Durable state

The current SQLite state database will gain durable notebook and change history rather than relying only on the existing page-indexed OCR rows.

The design requires these logical records:

- `notebook_snapshot`: notebook name, Cloud revision/hash, observed time, Cloud update time, page count, and snapshot status.
- `page_snapshot`: notebook, stable page ID, page index, content hash, first-seen and last-seen revisions, current/removed state, and OCR status.
- `page_change`: monotonically ordered change ID, notebook, revision, page ID, change type, old/new hashes, old/new page indexes, and observed time.
- `agent_cursor`: agent identity, notebook, last acknowledged change ID, and update time.

Page IDs are the primary identity. Page indexes are positional evidence only and must not be used as the identity of a page. A page edit therefore remains an update to the same page when its Supernote page ID is stable.

Repeated polls of identical notebook bytes must be idempotent and must not create duplicate change records.

## Query surface

The agent-facing query surface will support both explicit and convenience forms:

```text
supernote changes <notebook> --since <change-id-or-timestamp> --json
supernote changes <notebook> --agent <agent> --json
supernote read <notebook> --pages <indexes> --json
```

An explicit cursor or timestamp is replayable and auditable. The agent form starts after that agent's last acknowledged cursor. Query results include the next cursor, notebook revision, page ID, page index, change type, OCR status, and a short text preview. Full OCR is available through `read`; page images remain available through the existing render option.

The service must not advance an agent cursor after a failed query. Cursor advancement must be explicit or occur only after a successful, complete response has been produced.

The JSON response is the canonical machine surface. Human-readable CLI output is rendered from the same validated response model.

## Write route and concurrency

The existing `supernote.write.requested` route remains the mutation path. It will use a shared Pydantic request model containing a request ID, agent, notebook, content type, content/page specifications, and the notebook revision the agent read before requesting the write.

A write that carries a stale base revision must fail closed. The service will not overwrite a newer human or agent change. It will return the current revision and a query/read retry path. Successful writes continue through the existing backup, upload, and re-verification pipeline.

Missing semantic inputs must not be silently guessed. Explicit configured policy may provide an allowed default only when the request opts into that policy; otherwise the service returns a validation error.

The change ledger will record the resulting Cloud revision when the next poll observes the write. Write completion and failure events will retain request correlation IDs and artifact references.

## Agent-facing error contract

All CLI, API, and event-facing failures use one structured contract:

```python
class AgentError(BaseModel):
    error_code: str
    message: str
    field: str | None
    received: dict[str, Any]
    expected: dict[str, Any]
    valid_examples: list[dict[str, Any]]
    retryable: bool
    next_step: str
    mutation_applied: bool
```

The contract must cover invalid arguments, disallowed or missing notebooks, missing pages, invalid cursors, expired authentication, Cloud outages, pending or failed OCR, stale revisions, upload conflicts, and unsupported write content types.

Every error must tell the agent what failed, whether anything changed, whether retrying is safe, and exactly what to do next. The JSON form contains the complete object; prose output is derived from it. Opaque exceptions and bare error strings are not acceptable at an agent-facing boundary.

For model-generated structured output, Pydantic validation and bounded Instructor-style repair feedback will identify the failing field and expected shape. The service may ask a model to repair its own invalid structured output, but it must not silently rewrite an agent's semantic tool arguments.

## Model ownership

Deterministic code owns Cloud polling, page identity, hashing, snapshot comparison, schema validation, revision checks, persistence, upload safety, and audit history. The model owns transcription interpretation, enrichment, relevance, prioritization, and follow-up decisions. No keyword rules or hidden thresholds will decide whether a changed page matters.

## Testing and acceptance

Tests must cover:

- No-op polling for identical notebook bytes.
- Two added pages and one updated page producing exactly three changes.
- Stable page identity across edits.
- Page removal and page reordering.
- Duplicate-poll idempotency.
- OCR only for new or changed pages.
- OCR pending and OCR failure visibility.
- Explicit cursor and timestamp queries.
- Per-agent cursor behavior and failed-query cursor preservation.
- Structured JSON and human-readable output parity.
- Invalid requests returning complete repair guidance.
- Stale-revision writes failing without mutation.
- Successful writes preserving backup and post-upload verification.
- Cloud authentication and download failures preserving the last good snapshot.

Acceptance requires an end-to-end fixture in which a notebook changes from one revision to the next, the query reports two additions and one update, cached OCR is returned, and a write based on an obsolete revision is rejected with a safe retry instruction.
