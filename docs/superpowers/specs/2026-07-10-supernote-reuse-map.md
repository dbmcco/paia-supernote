# Supernote CLI — Reuse Map (planning input)

**Status:** Feeds planforge/speedrift planning
**Date:** 2026-07-10
**Spec:** [`2026-07-10-supernote-agent-cli-design.md`](./2026-07-10-supernote-agent-cli-design.md)
**Detailed audits:** [`reuse-audit/1..5`](./reuse-audit/) (cloud-auth, snapshot-read, mutate-write, filing-ledger, cli-service-surface)

## Headline — the spec's "thin layer" claim, refined

The spec said the CLI is a thin layer over existing primitives. The audit
sharpens this: for the **move** path the right reuse target is not the raw
primitives but **`quick_filing_service.QuickFilingService`** (`run_once`,
`quick_filing_service.py:108`), which already implements the *operational* move:

- detect starred candidates (`_detect_candidates` `:51`) → `StarDetector` + `reader.read_pages` + `reader.resolve_filing_destination`
- idempotency + resume via `FilingLedger` (`_record_candidate` `:181`; skips `completed`; promotes `source_removed`→`completed`; `_write_target_if_needed` skips if already `target_written*` `:194` — this is exactly the spec's "no duplicate page in target")
- `copy_pages_to_end` into targets (`:206`), `remove_pages` from source (`:227`); recognition + filing-marker metadata cleared
- upload **targets before source**; ledger transitions `mark_target_written` → `mark_target_written_source_pending` (on source-upload failure) → `mark_source_removed` → `mark_completed` (`:240-249`)
- `dry_run=True` support (`:131`)

**What the service does NOT do (the four safety steps the spec + CLAUDE.md mandate):**

1. Snapshot / page-id + content-hash capture (`build_snapshot_from_notebook` is **not** used by the service).
2. Timestamped backup of every affected notebook to `~/.paia/supernote/backups/` (zero backup logic anywhere in the service or `main.py`).
3. Post-mutate local verification (moved page-ids present in targets; kept-list matches source).
4. Post-upload re-download + SHA-256 verification vs staged bytes.

**Therefore:** the CLI **wraps** `QuickFilingService` and adds a thin safety
orchestrator for those four steps, plus plan mode, the `--to` override, and
human-readable reporting. Rebuilding detection / ledger / mutate / upload would
duplicate tested, working code.

## Per-verb reuse table

| Verb | Reuse | New code needed |
|---|---|---|
| `ls` | `SupernoteUploader._list_note_files` (`uploader.py:210`) | thin wrapper + formatting |
| `show <nb>` | `build_snapshot_from_notebook` (`note_snapshot.py:61`); `PageStateStore.list_pages` for cached OCR (`page_state.py`); `FilingLedger.get` for filing status | **OCR join (Gap-1, medium):** snapshot carries no text; join cached `PageState.raw_text` (fast/offline) keyed by `(notebook,page)`, validate freshness via `source_revision` vs snapshot `revision`; fall back to live `reader.read_pages` only on request. Spec undersold this. |
| `read <nb>` | `reader.read_pages` / `read_all_pages` (`reader.py:604`/`:537`, async) | `--render` PNG is ~2 lines: `result.page_image.save(...)`; or reuse `PageImageCache.get_or_render` (`organizer_images.py`). No new engine. |
| `append <nb>` | **Port `scripts/append_mgmt_list.py`** — it is a working prototype of exactly this verb: `SupernoteWriter.render_page` → `append_page_to_notebook` (`notebook_writer.py:36`, takes **raw RLE bytes**) → upload. | Add `--text/--file/--stdin`; throw away the hardcoded content. Input to `append_page_to_notebook` is RATTA_RLE, **not** text — pair with `render_page`. |
| `move <src> <tgt> --pages` | wrap `QuickFilingService` operational core | the 4 safety steps + explicit-pages entry point (service is star-driven today) |
| `move <src> --by-stars` | `QuickFilingService.run_once` + `StarDetector.starred_pages_from_metadata` (`quick_filing.py:142`) + `reader.resolve_filing_destination` | the 4 safety steps; `--to <nb>` override; plan mode |
| `remove <nb> --pages` | `remove_pages` (`note_page_ops.py:41`) | safety pipeline (backup/verify) wrapper |
| `plan <src> [--by-stars]` | **Port `scripts/quick_note_audit.py`** (`QuickNoteAuditService`, `quick_note_audit.py:201`) — argparse + `load_config` + read-only move-map report ≈ exactly `plan`'s output | make it print "page → target, confidence, already-moved vs would-move" from snapshot+detection+ledger; zero writes |
| `auth status` / `auth login` | reuse `main._run_login()` (`main.py:958-968`) almost verbatim; `SupernoteUploader._ensure_authenticated` (`uploader.py:255`) is the 403-recovery point | surface as subcommands; `auth login` runs `headless=False` |

## Confirmed-reusable primitives (exact)

- `SupernoteUploader.download_notebook(target) -> bytes` (`:155`), `upload_notebook(path, target) -> bool` (`:109`), `_list_note_files() -> list[dict]` (`:210`) — all async.
- `build_snapshot_from_notebook(notebook, *, notebook_name, revision) -> NotebookSnapshot` (`note_snapshot.py:61`); `PageRecord` carries page_id, starred, heading, content-hash — **no OCR text**.
- `reader.read_pages` / `read_all_pages` / `resolve_filing_destination(...) -> FilingDestinationDecision` (async).
- `copy_pages_to_end`, `remove_pages`, `reorder_pages` (`note_page_ops.py:25`/`:41`/`:57`, sync); `append_page_to_notebook(note_source, ratta_rle_bytes) -> bytes` (`notebook_writer.py:36`, sync); recognition/filing-marker clearing helpers (sync).
- `StarDetector.starred_pages_from_metadata(metadata) -> set[int]` (`quick_filing.py:142`); `parse_filing_header -> FilingHeader`; `notebook_name_to_tag` (`:47`) — note: tag matching is **not** wired into per-page resolution today; destination is model-decided via `resolve_filing_destination`.
- `FilingLedger`: `operation_id_for(*, source_notebook, source_pages, source_revision, target_notebook)` (`:67`, staticmethod, sha256) — **confirms spec params exactly**. State machine: `upsert_detected`→`detected` → `mark_target_written`→`target_written` / `mark_target_written_source_pending`→`target_written_source_pending` → `mark_source_removed`→`source_removed` → `mark_completed`→`completed` / `mark_failed`. `get(op_id)`.

## Gaps that need genuine new code

1. **OCR join for `show`** (medium) — see table. New wiring, not a missing primitive.
2. **The four safety steps** around the service's move: snapshot, backup, post-mutate verify, re-download SHA-256 verify.
3. **CLI command surface** — no existing argparse structure to extend; build a new `cli.py` module + one `[project.scripts]` line (`supernote = "paia_supernote.cli:main"`). Keep separate from the `paia-supernote` daemon entry.
4. **`--to` override, plan mode, explicit-pages move, human-readable reporting.**

## Scripts — duplication verdicts

| Script | Verdict |
|---|---|
| `quick_note_audit.py` | **REUSE → informs `plan`.** Already argparse + `load_config` + read-only audit. Extract its service. |
| `append_mgmt_list.py` | **REUSE pattern, throwaway content.** Working prototype of `append`. Port the 4-step flow. |
| `inspect_note_stars.py` | **THROWAWAY (debug).** Subsumed by `StarDetector` + `build_snapshot`. |
| `quick_filing_pilot.py` | **REUSE → informs `move` wiring.** argparse driver for `QuickFilingService.run_once`. |
| `probe_cloud_api.py`, `create_test_note.py`, `e2e_write_test.py` | fixtures/probes — keep for tests, not verbs. |

## Cross-cutting constraints (plan must honor)

- **Entry pattern:** one-shot `asyncio.run(coro)` per verb (as `main.py:1018/1022`, `user_board.py:631`), NOT the manual-loop service pattern. Each verb: construct `SupernoteUploader`, `await start()` → work → `await stop()` in `try/finally`. Sync primitives are called inline inside the coroutine.
- **Auth session persists** to `~/.paia/supernote/session.json` (Playwright `storage_state`, restored on start, saved on stop/reauth). A valid session needs **no login** per call — not a showstopper. Login only when it genuinely expires (surfaced as `UploadAuthError`/403).
- **Per-call cost:** `start()` launches headless Chromium + a `networkidle` round-trip on every invocation (multi-hundred-ms). Acceptable for a CLI; note for `ls`/`show` latency.
- **Cross-process lock:** `_cloud_api_lock()` fcntl at `~/.paia/supernote/cloud-api.lock` (`uploader.py:341`). CLI serializes with the running daemon — good (no races), but a CLI call blocks behind the daemon if it holds the lock. Plan should surface this in error guidance.
- **403 trap:** on stale auth, emit actionable "run `supernote auth login`, then retry. No notes changed." instead of a raw 403.

## What this means for the plan

- The build is **small and low-risk**: one new `cli.py` + a safety-orchestration layer around `QuickFilingService` + porting two scripts' flows + the OCR join.
- **No** new cloud, binary-format, detection, or ledger capability.
- TDD over the thin new layer: mock cloud + OCR + service; assert pipeline order, the 4 safety steps, idempotent skip, partial-failure resume, no-write-on-auth-failure, `plan` writes nothing.
