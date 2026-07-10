# Reuse Audit — Slice 2: Snapshot + Read/OCR

Repo: `/Users/braydon/projects/experiments/paia-supernote`
Scope: `src/paia_supernote/note_snapshot.py`, `reader.py`, `page_state.py`
(+ cross-referenced `quick_filing.py`, `quick_filing_service.py`,
`organizer_runtime.py`, `organizer_images.py`, `uploader.py` for accuracy)
Mode: READ-ONLY. No files modified.

Verdict: The spec's "thin layer over existing primitives" claim is **largely
true for `read`** (reader primitives are a near-perfect fit) but **has a real gap
for `show`**: `build_snapshot_from_notebook` carries NO OCR text, so `show`'s
"OCR preview line" cannot come from the snapshot alone — it needs a join with
`page_state` cached OCR or a live reader call. Details below.

---

## 1. Confirmed reusable

### build_snapshot_from_notebook — note_snapshot.py:60-91
- **Signature:** `build_snapshot_from_notebook(notebook: Any, *, notebook_name: str, revision: str) -> NotebookSnapshot` (note_snapshot.py:60)
- **Input:** takes an ALREADY-PARSED supernotelib `notebook` object — NOT raw bytes. Caller must load bytes first. Reference pattern: `organizer_runtime.py:18-24` (`snapshot_loader`: bytes → `_revision` (sha256) → `load_notebook_from_bytes` → `build_snapshot_from_notebook`). REUSE that helper to avoid re-deriving the load/revision logic.
- **Return shape:** `NotebookSnapshot` (note_snapshot.py:53-58): `notebook_name: str`, `revision: str`, `page_order: list[str]` (ordered page_ids), `pages: dict[str, PageRecord]`, `metadata: NoteMetadataIndex`.

### PageRecord fields — note_snapshot.py:25-38
- `page_id: str` ✓
- `page_index: int` ✓
- `starred: bool` ✓ (carries star — pre-computed)
- `content_hash: str` ✓ (sha256 hex of page content chunks; see `_content_hash` note_snapshot.py:164)
- `headings: list[MetadataRecord]` ✓ — **but heading TEXT is not a plain string**: it lives in `MetadataRecord.content` (bytes|None, from `title.get_content()`, note_snapshot.py:104 / MetadataRecord at note_snapshot.py:8-15). For `show`'s heading display, decode `page.headings[0].content`. Minor formatting code needed (gap, §2).
- `page_metadata: dict`, `image_width/image_height: int|None`, `keywords`, `outgoing_links`, `incoming_links`.
- **GAP: NO OCR text / raw_text field.** The snapshot is purely structural. (See §2 gap-1.)

### _is_starred — note_snapshot.py:159-161
- `def _is_starred(metadata: dict[str, Any]) -> bool` — reads `metadata.get("FIVESTAR")`, truthy and not in `{"0","[]","None","none"}`. Same `FIVESTAR` key used by `StarDetector.starred_pages_from_metadata` (quick_filing.py:142-160) — **consistent star logic.** For the CLI, `PageRecord.starred` is already populated, so the CLI uses the field directly; `_is_starred` is the canonical implementation to keep in sync.

### SupernoteReader.read_pages — reader.py:609-655
- **Signature:** `async def read_pages(self, note_bytes: bytes, notebook_name: str, *, pages: list[int]) -> List[ReadResult]` (reader.py:609)
- **Input:** raw `.note` BYTES (already downloaded). Loads notebook internally via tempfile (reader.py:613-618); out-of-range pages are silently skipped (reader.py:624-625).
- **Returns:** `ReadResult` list (reader.py:42-49): `notebook: str`, `page_num: int`, `text: str` (**full OCR transcription**), `checkboxes: list[CheckboxItem]`, `content_type: str` ("task"|"snippet"|"general"), `timestamp: datetime`, `page_image: Any|None` (**a PIL Image** — confirmed by `_page_image_to_b64` calling `page_image.save(..., format="PNG")` at reader.py:187-190).
- This is exactly what `supernote read` needs: text + image. REUSE: wrap directly.

### SupernoteReader.read_all_pages — reader.py:544-603
- **Signature:** `async def read_all_pages(self, note_bytes: bytes, notebook_name: str, page_range: Optional[tuple[int, int]] = None) -> List[ReadResult]` (reader.py:544)
- `page_range` is `(start_page, end_page)` **inclusive** (reader.py:559-560). Same `ReadResult` shape.
- **Behavior note:** both readers `continue` (skip) any page whose transcription is empty (reader.py:567-568, 627-628) — a page that OCRs to nothing is silently dropped from results. `read` will not emit it; caller cannot distinguish "blank page" from "not in range."

### resolve_filing_destination — reader.py:309-362
- **Signature:** `async def resolve_filing_destination(self, *, page_image: Any|None, transcription: str, source_notebook: str, destination_notebooks: list[str]) -> dict[str, Any]` (reader.py:309)
- **Returns a PLAIN `dict[str, Any]`, NOT a dataclass.** Keys: `action` ("move"|"needs_review"), `target_notebook` (str|None), `evidence` (str), `confidence` (float), `raw_response` (str). Schema/code validation is done in-code (reader.py:370-379); invalid model JSON → `action="needs_review"`.
- **The typed `FilingDestinationDecision` lives in `quick_filing.py:34-40`** (fields: `action: str`, `target_notebook: str|None`, `evidence: str`, `confidence: float`, `raw_response: str`). It is a **1:1 mirror** of the reader's dict. The existing `quick_filing_service.py:74-81` constructs it from the dict. REUSE: import `FilingDestinationDecision` from `quick_filing` and adapt the dict (or better, REUSE `route_page_from_decision` quick_filing.py:90-140 which already consumes a `FilingDestinationDecision` and emits a `FilingCandidate` with status `detected|ready|needs_review`).
- Note `destination_notebooks` should be the **values** of the destination map (filenames), as `quick_filing_service.py:64` does `list(dict.fromkeys(self.destination_map.values()))`.

---

## 2. Gaps needing NEW code

### Gap-1 (medium, biggest one for `show`): no OCR text on the snapshot.
The spec's `show` table (design spec lines ~96) lists "OCR preview line" and is "backed by `build_snapshot_from_notebook` + ledger." But `PageRecord` has no text/raw_text (note_snapshot.py:25-38). The snapshot cannot produce an OCR preview. The CLI must JOIN the snapshot with OCR from one of:
  - **Cached (SYNC, offline, fast):** `PageStateStore.list_pages(notebook)` → `PageState.raw_text` (page_state.py:19) / `.source_revision` (page_state.py:18). Join on `PageRecord.page_index == PageState.page` (page_state PRIMARY KEY is `(notebook, page)` at page_state.py:51). **Validate freshness:** compare `PageState.source_revision` to the snapshot's `revision`; a missing/stale row = no cached OCR for that page.
  - **Live (ASYNC, costs a vision-model call):** `reader.read_pages(...)` → `ReadResult.text`.
  This join + freshness decision is genuine NEW orchestration code (no primitive does it). `page_state` cache is the right default for `show` (fast, offline); fall back to live OCR only if requested. **Severity: medium — it's new wiring, not a missing primitive, but the spec undersells it by attributing `show` solely to snapshot+ledger.**

### Gap-2 (low): render a page to PNG for `read --render`.
No dedicated "render to PNG file" primitive, but you do NOT need one. `read_pages` already returns `page_image` (PIL Image). NEW code is ~2 lines: `result.page_image.save(f"/tmp/{name}-page-{result.page_num:03d}.png", format="PNG")`. If scaled/cached renders are wanted, a full engine already exists: `PageImageCache.get_or_render` (organizer_images.py, keyed by `notebook_name|revision|page_id|scale` → sha256 → cache path, returns `CachedPageImage`) + `OrganizerRuntime.page_renderer` (organizer_runtime.py:35-44, `ImageConverter(notebook).convert(page_index)`). No new rendering engine.

### Gap-3 (low): heading/keyword formatting for display.
`headings`/`keywords` are `list[MetadataRecord]` with text in `.content` (bytes|None). New trivial formatting to extract the first heading's text. No primitive missing.

### Gap-4 (low/note): double-parse of the same bytes.
`read_pages` parses bytes→tempfile→supernotelib internally every call (reader.py:613-618). If `show`/`plan` also call `build_snapshot_from_notebook` on the same bytes, that's two parses. `OrganizerRuntime` caches the parsed notebook by `(notebook_name, revision)` (organizer_runtime.py:11, 21, 24). REUSE that caching pattern to parse once.

---

## 3. Duplication risk

- **REUSE: wrap `reader.read_pages` / `read_all_pages` for `supernote read`.** Do not rewrite OCR. Both already produce text + page_image.
- **REUSE: wrap `build_snapshot_from_notebook` + `PageStateStore` for `supernote show`.** Do not rewrite snapshot.
- **HIGH duplication alert — `quick_filing_service.py` is already a working prototype of the spec's `move --by-stars` safe pipeline** (download → `StarDetector` → `reader.read_pages` → `reader.resolve_filing_destination` → `route_page_from_decision` → ledger upsert → `copy_pages_to_end` → `remove_pages` → upload target-before-source → `mark_target_written`/`mark_source_removed`/`mark_completed`, with dry_run + `target_written_source_pending` resume at quick_filing_service.py:120-128). The spec's `plan` and `move --by-stars` are largely wraps around this. **The CLI should delegate to / refactor `QuickFilingService`, not reimplement the pipeline.** (Slightly outside this slice, but it consumes all three of my slice's primitives, so flagging for the planner.)
- `organizer_ui.py` renders an HTML "page grid" from a serialized snapshot dict (no OCR text either) — that's the web organizer's "show", a different surface. No direct code reuse for a text/JSON CLI, but it confirms the canonical page-iteration pattern (`for page_id in page_order: page=pages[page_id]; page.starred; len(page.headings)`).
- `resolve_filing_destination` returns a dict that mirrors `FilingDestinationDecision` exactly — REUSE the dataclass instead of re-defining a parallel type.

---

## 4. Sync/async & lifecycle

**SYNC primitives (callable directly from a sync CLI, no event loop):**
- `build_snapshot_from_notebook`, all of `PageStateStore` (SQLite), `StarDetector`, `parse_filing_header`, `route_page_from_decision`, `notebook_name_to_tag` (quick_filing.py:47), `organizer_runtime.snapshot_loader` + `page_renderer`, `PageImageCache`, `note_page_ops.copy_pages_to_end` / `remove_pages`, `load_notebook_from_bytes` (organizer_runtime.py:75).

**ASYNC primitives (need `asyncio.run(...)` wrapper in the CLI):**
- `SupernoteUploader.download_notebook` / `upload_notebook` / `_list_note_files` (uploader.py:155/109/210).
- `SupernoteReader.read_pages` / `read_all_pages` / `process_file` / `resolve_filing_destination` / `_transcribe_page` / `classify_content`.

**Implications for the CLI commands:**
- `supernote show` can be **fully synchronous** if the OCR preview uses `page_state` cache (snapshot + SQLite join, both sync). Only needs the loop if it falls back to live OCR.
- `supernote ls` needs the loop (`_list_note_files` is async).
- `supernote read` needs the loop (live OCR) and **is never offline** — OCR calls a vision model: Anthropic key (default), or Z.AI key via `resolve_supernote_zai_api_key`, or local Ollama URL. `SupernoteReader.__init__` (reader.py:55-88) wires these; the CLI must pass/inherit the right `vision_backend`.
- `supernote plan` / `supernote move --by-stars` need the loop (download + OCR + upload).

**Uploader lifecycle (must handle in CLI):**
- `SupernoteUploader.download_notebook` raises `RuntimeError("Browser not started. Call start() first.")` if `self.page is None` (uploader.py:160-161) — it drives a Playwright browser session. The CLI must `start()` then `stop()` (or use the existing wrapper).
- **The "403 trap" is already encoded:** `_list_note_files` raises `UploadAuthError` on 401/403 (uploader.py:216-217), and `download_notebook`/`_find_file_ids` catch it and call `_restart_browser_session()` (uploader.py:163-167) before retrying. The spec's `supernote auth` / actionable 403 message can surface `UploadAuthError` directly rather than inventing new detection.

---

## Acceptance

READ-ONLY audit complete. No repo files changed; only this findings file written to /tmp.
```
acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Concrete file:line findings for all primitives in scope: build_snapshot_from_notebook (note_snapshot.py:60), PageRecord fields incl page_id/starred/content_hash/headings (note_snapshot.py:25-38), _is_starred (note_snapshot.py:159), read_pages (reader.py:609), read_all_pages (reader.py:544), resolve_filing_destination returning a dict not a class (reader.py:309), FilingDestinationDecision in quick_filing.py:34, cached OCR keyed by (notebook,page) in page_state.py:51. Plus 4 gaps and a high-value duplication-risk flag (quick_filing_service.py prototype)."
    }
  ],
  "changedFiles": [],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "grep for primitives across src/ (read-only)",
      "result": "passed",
      "summary": "Located download_notebook/upload_notebook/_list_note_files, FilingDestinationDecision, StarDetector, parse_filing_header, notebook_name_to_tag, ImageConverter usages, PageStateStore wiring, pyproject scripts."
    }
  ],
  "validationOutput": [
    "Confirmed PageRecord has NO raw_text field -> show OCR-preview gap is real",
    "Confirmed resolve_filing_destination returns dict[str,Any] not FilingDestinationDecision (class mirrors it in quick_filing.py:34)",
    "Confirmed cached OCR keyed by (notebook,page) PRIMARY KEY page_state.py:51; freshness via source_revision column",
    "Confirmed page_image is PIL Image (reader.py:187-190 save PNG)",
    "Confirmed quick_filing_service.py already implements the move --by-stars safe pipeline (reuse, don't rewrite)"
  ],
  "residualRisks": [
    "show's OCR preview line is NOT derivable from snapshot+ledger alone (spec undersells this); planner must add a snapshot<->page_state join with revision-freshness check, or accept a live-OCR cost",
    "read_pages/read_all_pages silently skip pages with empty transcription (reader.py:567-568,627-628) -> a blank/unreadable page disappears from results",
    "read OCR requires network + model credentials (anthropic/zai/ollama); never offline. CLI must configure vision_backend and manage SupernoteReader lifecycle",
    "SupernoteUploader needs Playwright start()/stop() session; 403 already maps to UploadAuthError + auto _restart_browser_session (uploader.py:163-167,216-217)",
    "Same bytes parsed twice (snapshot + reader) unless the OrganizerRuntime (notebook,revision) caching pattern is reused"
  ],
  "noStagedFiles": true,
  "diffSummary": "No repo changes. Read-only reuse audit; findings written to /tmp/supernote-reuse-audit/2-snapshot-read.md.",
  "reviewFindings": [
    "no blockers",
    "medium gap: show OCR preview needs snapshot<->page_state join (no text on PageRecord), note_snapshot.py:25-38",
    "reuse alert: QuickFilingService (quick_filing_service.py) already implements the spec's move --by-stars pipeline; CLI should delegate/refactor, not reimplement"
  ],
  "manualNotes": "Spec claim 'thin layer over existing primitives' holds for `read` (reader is a near-perfect fit) but not cleanly for `show` (snapshot lacks OCR text). FilingDestinationDecision is a real class but lives in quick_filing.py; reader.resolve_filing_destination returns a dict that mirrors it 1:1. Cached OCR exists and is keyed by (notebook, page) with a source_revision column for freshness."
}
```
