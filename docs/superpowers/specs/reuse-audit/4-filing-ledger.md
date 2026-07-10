# Reuse Audit — Slice 4: Filing / Star Detection + Ledger/Idempotency + Filing Service

Scope: `quick_filing.py`, `filing_ledger.py`, `quick_filing_service.py`, `task_curator.py`, `tasks_sync.py`
plus verified dependencies (`note_page_ops.py`, `note_snapshot.py`, `reader.py`, `uploader.py`).
Evidence date: 2026-07-10. Test state: `tests/test_quick_filing_service.py` +
`tests/test_filing_ledger.py` + `tests/test_quick_filing.py` → **22 passed**.

---

## HEADLINE FINDING (Duplication risk)

**REUSE the service's operational core; WRAP it — do NOT rebuild detection, the
ledger, or the copy/remove/upload mechanics. But the service as-is is NOT the full
spec "safe pipeline": it implements 5 of the 10 spec safety steps and is missing
backup, snapshot, post-mutate verification, and post-upload re-download/SHA-256
verification. Those four steps are mandatory CLAUDE.md ceremony and must be ADDED.**

Concretely: the CLI should drive `QuickFilingService` (its `_detect_candidates`,
`_record_candidate`/ledger, `_write_target_if_needed`, copy/remove/upload ordering)
for the move, but a thin safety orchestrator (CLI layer or an extended service)
must add: snapshot capture, timestamped backups, local page-id/content-hash
verification, re-download+SHA-256 verification, `mark_failed` on every failure
boundary, the `--to` override, plan mode, and human-readable reporting. Rebuilding
the detection→ledger→mutate→upload chain would duplicate tested, working code.

---

## KEY QUESTION — Does `quick_filing_service.py` already implement the full move pipeline?

**NO — partial.** It implements the *operational* move but not the *safety*
ceremony the spec (steps 1–10) and CLAUDE.md mandate.

Step-by-step against the spec's 10-step "safe pipeline":

| # | Spec step | Service status | Evidence |
|---|-----------|----------------|----------|
| 1 | Download fresh source (+ targets) | ✅ | `run_once` downloads source if `source_bytes is None` (`quick_filing_service.py:114-116`); target downloaded in `_write_target_if_needed` (`:206-208`). |
| 2 | Snapshot; capture page-ids + content-hashes | ❌ | No snapshot. `build_snapshot_from_notebook` is **not** imported/used anywhere in the service. Only `organizer_runtime.py:27` uses it. Revision is a per-page whole-file hash only (see Note). |
| 3 | Timestamped backup of every affected notebook | ❌ | **Zero backup logic in the service or its call sites.** No `~/.paia/supernote/backups/` writes anywhere in `quick_filing_service.py` or `main.py`. |
| 4 | Idempotency via `operation_id_for(...)` | ✅ | `_record_candidate`→`upsert_detected` (ON CONFLICT DO NOTHING) `:181-193`; status gating in `run_once` (`:124-135`) and `_write_target_if_needed` (`:200-204`). |
| 5 | Mutate: copy→targets, remove→source; zero recognition metadata | ✅ | `copy_pages_to_end` then batched `remove_pages`; `note_page_ops` clears recognition + filing-marker metadata. |
| 6 | Verify locally: moved page-ids in targets; kept-list matches; hashes preserved | ❌ | No local verification. The service mutates and uploads without re-checking page-ids or content hashes. |
| 7 | Upload targets before source | ✅ | All target writes happen in the per-candidate loop (`:136-141`) before the single batched `remove_pages`+source upload (`:143-166`). |
| 8 | Re-download every changed notebook; verify page counts + SHA-256 | ❌ | No re-download/verify after upload. The service uploads and trusts success. |
| 9 | Ledger: `mark_target_written`→`mark_source_removed`→`mark_completed`; `mark_failed` at boundary | ⚠️ PARTIAL | happy path + `mark_target_written_source_pending` exist and are tested. **`mark_failed` (`filing_ledger.py:170`) is NEVER called by the service** — target download/copy/upload failures propagate without a ledger failure record. |
| 10 | Print human-readable summary | ❌ (CLI-layer) | `run_once` returns a `dict` (`_result`, `:238`); no summary/next-command text. |

**Conclusion:** The service gives the CLI a correct, idempotent, resume-safe
*engine* (detect → resolve → copy → upload-target-first → remove → upload-source →
ledger). It does NOT give the CLI the spec's full *safety envelope* (snapshot,
backup, verify-before/after-upload, mark-failed). Those are NEW code.

---

## 1. Confirmed reusable (exact signatures + file:line)

### `StarDetector.starred_pages_from_metadata` — quick_filing.py:142-158
```python
class StarDetector:                                    # quick_filing.py:142
    def starred_pages_from_metadata(self, metadata: dict[str, Any]) -> set[int]:  # :145
```
- Returns `set[int]` of **zero-based page indices** (uses `enumerate(pages)`).
- Conservative: reads `metadata["page_metadata"]` list; a page is starred when its
  `FIVESTAR` value is truthy and not in `{"0","[]","None","none"}`.
- **Confirms the spec's claim** ("yields the zero-based indices of starred pages
  from `FIVESTAR` metadata").
- Same predicate is duplicated in `note_snapshot.py:_is_starred` (`note_snapshot.py:_is_starred`)
  for the snapshot path — both read `FIVESTAR` identically. Keep one source of truth.

### `parse_filing_header` — quick_filing.py:55-71
```python
def parse_filing_header(text: str) -> FilingHeader:    # quick_filing.py:55
```
`FilingHeader` fields (quick_filing.py:9-16): `note_date: str|None`, `tags: list[str]`,
`bundle_index: int|None`, `bundle_total: int|None`, `title: str|None`, `raw_header: str`.
- Parses line 0 as header, line 1 as title; date via `\b(20\d{2}-\d{2}-\d{2})\b`;
  tags via `#([A-Za-z][A-Za-z0-9_-]*)` over the first 5 lines (lowercased);
  bundle `N/M` via `\b(\d{1,2})\s*/\s*(\d{1,2})\b`.

### `route_page_from_decision` — quick_filing.py:83-140
```python
def route_page_from_decision(                          # quick_filing.py:83
    *, notebook: str, page: int, source_revision: str,
    text: str, starred: bool, decision: FilingDestinationDecision,
) -> FilingCandidate:
```
- Emits `FilingCandidate` with `status` ∈ {"detected" (not starred), "ready"
  (decision.action=="move" and target_notebook set), "needs_review"}.
- `source_pages=[page]` always — **one candidate per page** (relevant to idempotency key).

### `notebook_name_to_tag` — quick_filing.py:47-51
```python
def notebook_name_to_tag(notebook_name: str) -> str:   # quick_filing.py:47
```
- Strips a trailing `.note`, lowercases, replaces non-alphanumeric runs with `-`,
  strips leading/trailing `-`. (`"Test Note 2"→"test-note-2"`, `"Navicyte.note"→"navicyte"`,
  `"LFW / HEC"→"lfw-hec"` — covered by `tests/test_quick_filing.py:135-138`.)
- **Caveat:** used ONLY to *build* `destination_map` (`main.py:_filing_destination_map:224`,
  tag→name) and the pilot's `--target`. It is **not** wired into per-page destination
  *resolution*; the actual page→target decision is delegated to the model
  (`reader.resolve_filing_destination`). The spec's "may match header tags via
  notebook_name_to_tag" deterministic path is NOT implemented.

### `FilingLedger` state machine — filing_ledger.py:34-231
SQLite-backed, keyed by a sha256 operation id.

- `FilingLedger(db_path: Path)` — `:35-36`.
- `init_schema()` — `:38-65` (idempotent CREATE TABLE).
- `operation_id_for(*, source_notebook, source_pages: list[int], source_revision: str, target_notebook: str|None) -> str` — **`@staticmethod`** `:67-81`.
  Hash of `"|".join([source_notebook, json.dumps(source_pages, compact), source_revision, target_notebook or ""])`.
  **Confirms the exact params the spec cites.**
- `upsert_detected(*, source_notebook, source_pages, source_revision, detected_header, detected_tags, bundle_key, target_notebook, routing_reason, confidence, target_insert_position="end", target_revision_before=None) -> FilingOperation` — `:83-135`.
  `INSERT … ON CONFLICT(operation_id) DO NOTHING` with status `'detected'`; returns `self.get(id)`. **This is the idempotency primitive:** re-detection of the same key returns the existing row with its current status.
- `mark_target_written(operation_id, *, target_revision_after: str)` — `:137-145` → status `target_written`, clears error.
- `mark_target_written_source_pending(operation_id, *, target_revision_after: str, error: str)` — `:147-155` → status `target_written_source_pending`, sets error.
- `mark_source_removed(operation_id, *, quick_revision_after: str)` — `:157-165` → status `source_removed`, clears error.
- `mark_completed(operation_id)` — `:167-168` → status `completed`, `completed_at=now`, clears error.
- `mark_failed(operation_id, *, error: str)` — `:170-171` → status `failed`, sets error. **Defined but UNUSED by the service** (only called in tests).
- `get(operation_id) -> FilingOperation` — `:173-211`; raises `KeyError` if absent.

**Status state transitions (documented):**
```
detected ──(target write ok)──► target_written ──(source removed)──► source_removed ──► completed
   │                                 │
   └─(source upload fails after target written)─► target_written_source_pending
                                                   (resume: re-run skips target re-append,
                                                    only does remove+upload source)
   any failure ─► failed   (mark_failed EXISTS but is NOT invoked by quick_filing_service)
```
`FilingOperation` dataclass fields (filing_ledger.py:12-32): `operation_id, created_at,
updated_at, status, source_notebook, source_pages, source_revision, detected_header,
detected_tags, bundle_key, target_notebook, target_insert_position,
target_revision_before, target_revision_after, quick_revision_after, routing_reason,
confidence, error, completed_at`.

**Verified resume behaviour (tests pass):** `target_written_source_pending` → re-run
does NOT re-download/upload the target (asserted in
`tests/test_quick_filing_service.py` `test_service_retry_after_target_written_does_not_upload_target_again`,
and `test_service_marks_source_cleanup_pending_after_target_write`).

---

## 2. Gaps needing NEW code (what the CLI needs that the service does NOT do)

1. **Snapshot + page-id/content-hash capture (spec step 2).** `build_snapshot_from_notebook`
   (note_snapshot.py:61) exists and is tested but is NOT used by the service. The CLI/plan
   command needs it to produce page-id + star + heading + content-hash per page
   (the `supernote show` / `plan` output) and as the basis for verify.
2. **Timestamped backups to `~/.paia/supernote/backups/<ts>/` (spec step 3, CLAUDE.md).**
   No backup logic exists anywhere in the service path. **Mandatory** — must be added.
3. **Local post-mutate verification (spec step 6):** moved page-ids present in targets;
   remaining source page-ids match the kept list; content hashes preserved. Not implemented.
4. **Post-upload re-download + SHA-256/page-count verification (spec step 8).** Not implemented.
5. **`mark_failed` on ALL failure boundaries (spec step 9).** Currently only the
   source-upload failure records partial state (`target_written_source_pending`).
   Target download / `copy_pages_to_end` / target upload failures raise without any
   ledger `mark_failed`. The CLI orchestrator must catch and `mark_failed` at the exact
   boundary, or the service must be extended to do so.
6. **`--to <notebook>` override (spec).** The service's destination is fixed by
   `destination_map` + model; there is no single-target override parameter. NEW.
7. **`plan` / move-map mode (read-only, zero writes).** `dry_run=True` short-circuits
   before writes (`run_once:119-120`) but returns only `detected` operations — it does
   NOT produce the spec's "page → target, confidence, already-moved-to-X-on-<date> vs
   would-move" move map, and does not exercise resume semantics. NEW presentation layer
   (it can reuse `_detect_candidates` + `upsert_detected` + `ledger.get`).
8. **Per-page idempotent skip reporting** ("already moved to X on <date> (op <id>)").
   The data exists (`FilingOperation.status=='completed'` + `completed_at` + `target_notebook`),
   but no formatting/human-readable layer exists. NEW (CLI) — primitive data is reusable.
9. **Deterministic handwritten-name → notebook routing.** The spec/CLAUDE.md wants the
   handwritten name beside the star to resolve directly (with header-tag fallback via
   `notebook_name_to_tag`). The service routes purely through the LLM
   (`resolve_filing_destination`). A deterministic parser/matcher is NEW if the product
   wants it; otherwise the model path is reusable as-is.
10. **Human-readable summary + next-command guidance + 403/auth guidance (CLI product layer).** NEW.

### `task_curator.py` and `tasks_sync.py` — NOT part of the filing pipeline
- `task_curator.py`: LLM-driven **rewrite of task pages** (tasks.note p0–3 / Quick.note
  p18–21) and Linear sync. Has its OWN upload path (`uploader.upload_notebook`), bypasses
  the filing ledger entirely. Relevant only to a future `supernote append`/tasks verb, **not** to `move`/filing.
- `tasks_sync.py`: background asyncio loop polling Linear → rebuilds `tasks.note`
  (`_loop`/`_poll_once`). Also self-contained; no ledger, no star filing.
- **Neither should be reused for the filing/move CLI.** They confirm `tasks.note` is owned
  by a separate sync loop; a filing move targeting `tasks.note` would need to coordinate
  with this writer (potential write-contention risk — see Residual Risks).

---

## 3. Duplication risk — explicit reuse vs build-new verdict

- **REUSE:** `StarDetector`, `parse_filing_header`, `route_page_from_decision`,
  `notebook_name_to_tag`, the entire `FilingLedger` (operation_id + state machine +
  `upsert_detected` ON-CONFLICT idempotency), `note_page_ops.copy_pages_to_end` /
  `remove_pages`, `uploader.download_notebook` / `upload_notebook` / `_list_note_files`,
  `reader.read_pages` / `resolve_filing_destination`, and `notebook_writer.append_page_to_notebook`.
- **REUSE (engine):** `QuickFilingService.run_once` for the operational move
  (detect → resolve → idempotent upsert → copy → upload-target-first → batched remove →
  upload-source → ledger advance + resume). It is the right primitive to wrap.
- **BUILD NEW (safety envelope + UX):** snapshot capture, timestamped backups,
  local page-id/content-hash verify, post-upload re-download/SHA-256 verify,
  `mark_failed` at every failure boundary, `--to` override, `plan` move-map, per-page
  skip reporting, deterministic handwritten-name routing (optional), and CLI
  summary/guidance/auth-surface.

> The service's copy/remove/upload logic is tested and correct; duplicating it in the
> CLI would be a clear regression risk. **Recommendation: extend the service (or build a
> thin `FilingOrchestrator` that composes `_detect_candidates` + `_record_candidate` +
> `_write_target_if_needed` + the note_page_ops/upload primitives) to add the four missing
> safety steps, rather than re-implementing the move.**

---

## 4. Sync/async & lifecycle

- **The service is async.** `run_once` (`quick_filing_service.py:108`) is `async def` and
  `await`s `uploader.download_notebook`/`upload_notebook`, `reader.read_pages`, and
  `reader.resolve_filing_destination`. `_detect_candidates`, `_write_target_if_needed`,
  and module-level `_upload_bytes` (`:225`) are all async.
- **`FilingLedger` is SYNC** (blocking `sqlite3`); `upsert_detected`/`mark_*`/`get` are
  plain calls. It is constructed inside the service and the operations are quick/locked
  per-connection.
- **Current invocation:**
  1. **Daemon (primary):** `paia-supernote` (`main.py`) — `_run_note_filing_if_configured`
     (≈ `main.py:490-520`) calls `service.run_once(source_bytes=note_bytes)` as a per-change
     handler right after a notebook is downloaded/processed. Gated by config keys:
     `filing_enabled`, `filing_dry_run`, `filing_source_notebooks`, `filing_ledger_db_path`,
     `filing_destination_notebooks` (map built by `_filing_destination_map:224`).
  2. **Standalone script:** `scripts/quick_filing_pilot.py` — constructs
     `SupernoteUploader()` (Playwright browser), `await uploader.start()`, builds a
     `SupernoteReader` (vision backend from config), runs `service.run_once()`, then
     `await uploader.stop()` in a `finally`. This is the CLI's blueprint.
- **Uploader requires a Playwright browser lifecycle:** `await uploader.start()` /
  `await uploader.stop()` (`uploader.py`). It is NOT a synchronous call. The CLI must own
  this lifecycle inside `asyncio.run(...)`.
- **How a CLI would invoke it:** mirror the pilot — `asyncio.run` an async entry point
  that does `SupernoteUploader().start()` → construct `QuickFilingService` (inject
  `reader=`/`star_detector=` for testability — already supported, `:30-31`) →
  `await service.run_once(...)` → `await uploader.stop()`. `supernote auth status` /
  `auth login` map to `uploader._ensure_authenticated` / `_interactive_reauth`.
  `--dry-run` maps to the existing `dry_run=True` ctor flag (read-only, verified by
  `test_service_dry_run_does_not_upload`).
- **Dependency-injection already present:** `reader` and `star_detector` are optional ctor
  params (`quick_filing_service.py:30-31`), and `source_bytes` is injectable into
  `run_once` — ideal for fixture-based CLI tests without cloud/Playwright.

---

## Notes & residual risks

- **`source_revision` is a whole-file content hash, not a cloud revision number.**
  `_source_revision` (`quick_filing_service.py:260`) = `f"{sha256(entire_notebook_bytes)}:{page_num}"`.
  Supernote Cloud exposes no revision number, so content-hash is the de-facto revision.
  Consequence (matches spec intent): any byte change anywhere in the notebook changes
  every page's revision → new operation ids → previously-filed pages can be re-filed.
  This is coarse (file-granular, not per-page-content); acceptable but flag for the
  planner. The ledger will never falsely "skip" because of a real edit, but an unrelated
  edit invalidates all prior filing records for that notebook.
- **Single-page candidates only.** `route_page_from_decision` always sets
  `source_pages=[page]`; bundles are detected (`bundle_key`) but never grouped into one
  multi-page operation. If the product wants "move this 3-page bundle as one op", that is
  NEW grouping logic; today each page is its own operation.
- **`remove_pages` keeps at least one blank page** (`note_page_ops.remove_pages` falls back
  to `_blank_page_like` when all pages removed). Fine for `Quick.note`, but the CLI must
  not assume an empty notebook is possible.
- **Write contention with `tasks_sync` / `task_curator`:** both write `tasks.note`
  independently (no ledger, no lock coordination with the filing service beyond the
  uploader's per-name `asyncio.Lock` in-process). A filing move whose target is
  `tasks.note` could race the sync loop across processes. The CLI should avoid filing INTO
  `tasks.note` or serialize with the same lock set.
- **`mark_failed` defined-but-unused** is the clearest correctness gap: a partial target
  failure currently leaves the operation in `detected` (or stuck) rather than `failed`, so
  a resume run will retry the target append — acceptable only because `_write_target_if_needed`
  is itself idempotent-against-`target_written`, but a truly failed target copy leaves no
  honest error record. The spec's "honest partial state" requires wiring `mark_failed`.
- **403 trap is real and handled only in the uploader,** not surfaced by the service. The
  uploader raises `UploadAuthError` on 401/403 and self-recovers via
  `_interactive_reauth`; `download_notebook`/`_list_note_files` raise `UploadAuthError` on
  401/403. The CLI must translate these to the actionable "run `supernote auth login`"
  message (spec). Today the service would just propagate the exception.

---

## Acceptance report

Read-only audit; no repo files changed (correctness verified by running the existing
test suite only).
