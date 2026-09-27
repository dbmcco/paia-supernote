# Supernote Cloud Change Ledger Implementation Contract

## Approved design

`docs/superpowers/specs/2026-07-18-supernote-cloud-change-ledger-design.md`, committed as `d111038`.

PlanForge drift anchor:

`docs/superpowers/specs/2026-07-19-implement-the-approved-supernote-cloud-change-ledger-and-agent-r-spec.md`

## Existing surfaces

- `src/paia_supernote/cloud_poller.py`: Cloud listing, update-time detection, notebook download, auth recovery.
- `src/paia_supernote/ingest_service.py`: Cloud callback, reader invocation, OCR persistence, Folio/events integration.
- `src/paia_supernote/reader.py`: Notebook/page image conversion, vision transcription, content classification.
- `src/paia_supernote/page_state.py`: Existing SQLite OCR and enrichment state; must remain backward compatible.
- `src/paia_supernote/note_snapshot.py`: Stable page ID extraction, page order, hashes, stars, metadata.
- `src/paia_supernote/cli.py`: Agent-facing Cloud read/write CLI surface and JSON/prose formatting.
- `src/paia_supernote/events.py`: Existing write-request and write-result event surface.
- `src/paia_supernote/main.py`: Existing write route, safe upload/reverification, and service wiring.
- `src/paia_supernote/uploader.py`: Cloud auth, download, upload, and safe write pipeline.
- `tests/`: Existing focused unit tests and marked integration tests.

## Execution policy

Supernote Cloud is the source of truth. The first implementation uses an explicit notebook allowlist and cached OCR. It does not mutate real Cloud notebooks during tests. Write safety uses base-revision validation and preserves existing backup/upload/reverification behavior.

The Workgraph dispatcher is running but cannot dispatch because unrelated organizer work holds manual claims. This scoped graph is therefore being executed through the documented manual-Pi fallback. The Speedrift lease was refreshed for owner `supernote-change-ledger` with a two-hour TTL. No unrelated tasks or leases are being modified.

## Pre-existing dirty files

The working tree already contained:

- `uv.lock` modified before this effort.
- `_pull_ooda.py` untracked before this effort.
- `_pull_p29.py` untracked before this effort.

They are unrelated and must not be staged or changed by this implementation.

## Non-goals

- No local Partner-app watcher work in this first version.
- No new HTTP server framework.
- No automatic semantic prioritization or relevance scoring in deterministic code.
- No live Cloud mutation in tests.
- No broad organizer UI or filing workflow changes.

## Known drift notes

The pre-task drift check returned advisory findings for pre-existing scope/spec state and ecosystem lookup/update availability. The missing `archdrift` GitHub lookup is not a reason to alter implementation scope. Toolchain self-update is deferred; code work uses the installed Speedrift commands and records this decision here.
