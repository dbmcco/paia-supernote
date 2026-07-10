# Reuse Audit — Slice 3: Page Mutation + Write/Render

**Repo:** `paia-supernote` · **Slice modules:** `note_page_ops.py`, `notebook_writer.py`, `writer.py`, `ratta_rle.py`
**Spec:** `docs/superpowers/specs/2026-07-10-supernote-agent-cli-design.md`
**Verdict:** The mutate/render primitives are **real, tested, and reusable as-is**. The spec's "thin layer over existing primitives" claim is **substantially true** — with one important correction: the spec frames the safe move/mutate pipeline as *new* orchestration, but an async copy→remove→upload→ledger pipeline **already exists** in `quick_filing_service.py`. The genuinely missing pieces are (a) timestamped backups, (b) post-mutation page-id/content-hash *verification* (the primitives preserve data but never assert it), and (c) re-download SHA verification.

---

## 1. Confirmed reusable

All signatures are **bytes-in / bytes-out**, pure, synchronous, no cloud coupling. Cite = `file:line`.

### `copy_pages_to_end` — `note_page_ops.py:25`
```python
def copy_pages_to_end(source_bytes: bytes, target_bytes: bytes, *, source_pages: list[int]) -> bytes
```
- Deep-copies each `source_pages` page from source, **clears recognition + filing-marker (FIVESTAR) metadata on each copied page** (calls `clear_recognition_metadata` + `clear_filing_marker_metadata` at `note_page_ops.py:31-32`), appends to target, rebuilds metadata page table, `reconstruct()`s.
- **Usable as-is.** Spec step 5 ("zero recognition metadata on moved pages (existing helper)") is already satisfied *inside* this function — no extra clearing needed by the CLI.
- Test: `tests/test_note_page_ops.py:56, 76` (count increase + star-marker cleared).

### `remove_pages` — `note_page_ops.py:41`
```python
def remove_pages(source_bytes: bytes, *, pages: list[int]) -> bytes
```
- Removes pages by zero-based index; if every page is removed it substitutes a blank placeholder page (so the notebook never goes to 0 pages). `_sync_metadata_pages` keeps header count correct.
- **Usable as-is.** Tests: `tests/test_note_page_ops.py:95, 113`.
- Note for CLI: index-based, **not** page-id-based. For `move --pages 3,4,5` the CLI maps supplied page-ids/indices to source indices (snapshot gives `page_order`).

### `reorder_pages` — `note_page_ops.py:57` (also re-exported via `note_reorder.py`)
```python
def reorder_pages(source_bytes: bytes, *, page_order: list[str]) -> bytes
```
- Takes **PAGEID strings**, validates each id appears exactly once (`_validate_page_order`, `:100`), remaps footer title/keyword/link page numbers, syncs metadata. Raises `UnsupportedLinkMetadataError` if a link footer record can't be remapped.
- **Usable as-is**, but spec explicitly defers reorder to fast-follow / out-of-scope v1. No new code needed if/when added.
- Thin wrapper `note_reorder.py` just re-exports `reorder_pages` + `UnsupportedLinkMetadataError` — no duplication.

### `append_page_to_notebook` — `notebook_writer.py:36`
```python
def append_page_to_notebook(note_source: str | Path | bytes, ratta_rle_bytes: bytes) -> bytes
```
- **Input = RAW RATTA_RLE bytes** (a MAINLAYER bitmap), NOT text/markdown/title+body. Accepts a path *or* raw notebook bytes (spills to temp file for `load_notebook`).
- Implementation (`_append_from_path`, `:71`): deep-copies the **last page as template**, assigns a fresh UUID-derived `PAGEID`, zeroes recognition offsets on **all** pre-existing pages (comment documents why: reconstruct() reflows offsets → dangling pointers crash the device), sets MAINLAYER layer 0 to the RLE bytes, clears other layers, appends page + metadata.
- **Usable as-is.** The spec's append pairing (`render_page` → `append_page_to_notebook`) is consistent: `render_page` emits RLE bytes, this consumes them. Tests: `tests/test_notebook_writer.py`.

### `SupernoteWriter.render_page` — `writer.py:101`
```python
def render_page(self, agent: str, content: str, content_type: str = "text") -> bytes
```
- **Input = plain text `content`** (word-wrapped onto the page). **Not markdown, not title+body.** It is agent-aware: auto-injects a top-right date stamp (`datetime.now()`), body in the agent's font (`AGENT_FONTS` Sam/Caroline/Ingrid), and a bottom-left `— {agent}` signature. Output = RLE bytes.
- `content_type` param exists but is currently unused for branching.
- **Usable as-is for the spec's `append` text/file/stdin flow**, but two caveats for a *generic* agent CLI:
  - It hardcodes a date stamp + agent signature on every page. Fine for agent-written pages; not a neutral "render arbitrary text" primitive. If the CLI must append impersonal/system text, either pass a neutral agent or add a no-decorations path.
  - `content_type="text"` is the only real mode; if markdown rendering is later wanted, that is NEW code.
- Pagination helper exists separately: `paginate_content(agent, content) -> list[str]` (`writer.py`) splits into page-sized chunks — reusable if `--file`/`--stdin` content overflows one page.
- Tests: `tests/test_writer.py` (render produces non-empty RLE, round-trips, layer placement).

### `build_page` / `append_to_notebook` / `append_rle_page` — `writer.py:149 / :231 / :442`
- `build_page(rle_content)` constructs a **fresh** `supernotelib.Page` from a hardcoded layer-info JSON (different strategy than `notebook_writer`'s deepcopy-last-page). `append_to_notebook(notebook_bytes, agent, content)` composes render→build→append. `append_rle_page(notebook_bytes, rle_content)` composes build→append.
- These are **alternative** append paths. The spec names `append_page_to_notebook` (notebook_writer), so prefer that; `SupernoteWriter.append_to_notebook` is a second, fully-composed append (render+append in one call) the CLI could also use.

### `ratta_rle.encode` / `decode` — `ratta_rle.py:79 / :114`
```python
def encode(image: Image.Image) -> bytes
def decode(data: bytes, width: int, height: int) -> Image.Image
```
- `encode`: PIL `L`-mode image → RATTA_RLE bytes. Quantizes 256 gray levels to 5 color codes via LUT; chunks runs ≤16384; deliberately avoids the context-dependent 0xFF marker. **Usable as-is**, already wired into `render_page`.
- `decode`: inverse, for round-trip tests. **Usable as-is.**
- Tests: `tests/test_ratta_rle.py`.

### Recognition-metadata-clearing helpers
- `clear_recognition_metadata(page)` — `note_page_ops.py:176` (zeros `RECOGNTEXT/RECOGNFILE/TOTALPATH/EXTERNALLINKINFO/IDTABLE` + `RECOGNSTATUS/RECOGNFILESTATUS`). Public, reusable.
- `clear_filing_marker_metadata(page)` — `note_page_ops.py:184` (zeros `FIVESTAR`). Public, reusable.
- **Both are already called inside `copy_pages_to_end`** for moved pages — the CLI does not need to call them again for moves.

---

## 2. Gaps needing NEW code

| Gap | What exists | What must be built |
|---|---|---|
| **G1. Composing copy+remove into a *verified* move** | `quick_filing_service.run_once` already does copy→remove→upload→ledger (see §3). `organizer_api.move_page_to_notebook` does single-page copy→remove→upload. **Neither verifies page-id preservation.** | New verifier that, after `copy_pages_to_end`, asserts the moved source page-IDs are now present in the target's tail, and after `remove_pages` asserts the remaining source page-IDs match the kept list. Building blocks exist: `note_snapshot.build_snapshot_from_notebook` (`note_snapshot.py:67`) yields `page_order` (list of PAGEIDs) + `PageRecord.content_hash` (SHA-256 over layer content, `note_snapshot.py:_content_hash`). **No such verifier exists today** (grep for `page.?id.*(preserv\|verif)` → 0 matches). |
| **G2. Timestamped backups** | `grep -i backup src/` → **0 matches**. No backup helper anywhere. | NEW: write affected notebooks to `~/.paia/supernote/backups/<ts>/` before any upload. Small, pure; trivial to add. |
| **G3. Re-download SHA-256 verification** | `organizer_api` computes `hashlib.sha256(...bytes).hexdigest()` of *staged* bytes (`organizer_api.py:150`) but never re-downloads to compare. | NEW: after upload, `download_notebook` again and compare SHA-256 of bytes vs staged bytes; assert page counts. |
| **G4. Conflict / auth retry around upload** | `uploader._ensure_authenticated` (`uploader.py:255`) exists; no explicit upload-retry-with-reauth loop in mutate path. `organizer_api.move_page_to_notebook` returns `partial_move_target_uploaded_source_failed` on source-upload failure but does **not** retry and does **not** mark a ledger. | NEW (CLI layer): wrap upload in try/except → on 403/csrf, emit the spec's recovery guidance and stop; optionally one reauth+retry. |
| **G5. Neutral text render (no agent/date decoration)** | `render_page` always stamps date + `— {agent}`. | Possibly NEW flag/path if the CLI must append impersonal/system text. Low effort. |

Note: the primitives themselves **do** preserve data correctly (deepcopy keeps `PAGEID`; `copy_pages_to_end` keeps it; tests confirm count behavior). The gap is the *assertion/verification step*, not a preservation bug.

---

## 3. Duplication risk

### REUSE: wrap `quick_filing_service.QuickFilingService` — the move pipeline is NOT new
The spec describes the "safe pipeline (every write command)" as new command-layer work, but **`quick_filing_service.py:run_once` (`:108`) already implements the core of it**:
- detect starred candidates (`_detect_candidates`, `:51`) → `StarDetector` + `reader.read_pages` + `reader.resolve_filing_destination`
- idempotency via `FilingLedger.upsert_detected` / `operation_id_for` (`_record_candidate`, `:181`)
- **resume**: skips `completed`; promotes `source_removed`→`completed`; `_write_target_if_needed` skips if already `target_written*` (`:194`) — exactly the spec's "no duplicate page in target" resume
- `copy_pages_to_end` into target (`:206`), `remove_pages` from source (`:227`)
- upload targets before source, then source
- ledger transitions `mark_target_written` → `mark_target_written_source_pending` (on source-upload failure) → `mark_source_removed` → `mark_completed` (`:240-249`)
- `dry_run=True` support (`:131`)

**This is the same pipeline the spec wants.** The CLI's `move --by-stars` should **wrap/extract** this service rather than re-implement. It is `dry_run`-first and async (see §4).

### A second, thinner move composition exists: `organizer_api.move_page_to_notebook` (`:153`)
- Single-page copy (`copy_pages_to_end`, `:181`) + remove (`remove_pages`, `:186`), stale-revision guard, uploads target-before-source, returns `partial_move_target_uploaded_source_failed` on source failure.
- **Does NOT use the ledger** (no idempotency/resume), no backup, no post-move verification.
- This overlaps with both the spec's `move <source> <target> --pages` and `quick_filing_service`. → **Duplication risk: three move codepaths** (spec-CLI-new, quick_filing_service, organizer_api). Recommendation: the new CLI command layer should consolidate to **one** shared async move helper (likely extracted from `quick_filing_service`) and have `organizer_api` call it too. Otherwise three divergent copy+remove loops.

### Duplicate `clear_recognition_metadata` definition
- Defined twice with identical logic: `note_page_ops.py:176` and `notebook_writer.py:27`. `notebook_writer.py` keeps its own private copy (`OFFSET_FIELDS` duplicated at `:15` too). Low severity, but the CLI should import the canonical `note_page_ops.clear_recognition_metadata` to avoid drift.

### Two distinct append implementations
- `notebook_writer.append_page_to_notebook` (deepcopy-last-page template, new UUID PAGEID) vs `writer.SupernoteWriter.build_page`/`append_rle_page` (fresh page from hardcoded layer JSON). Both work and are tested, but they produce pages via different strategies. For the CLI's `append`, pick **one** (spec names `append_page_to_notebook`) to avoid two append codepaths diverging.

---

## 4. Sync/async & lifecycle

- **Mutate + render primitives are all SYNCHRONOUS, pure bytes→bytes, no I/O:**
  - `note_page_ops.copy_pages_to_end / remove_pages / reorder_pages`
  - `notebook_writer.append_page_to_notebook`
  - `writer.SupernoteWriter.render_page / build_page / paginate_content`
  - `ratta_rle.encode / decode`
  These can be called directly in a sync CLI handler with no `await`.
- **Cloud + composition layers are ASYNC:**
  - `uploader.download_notebook / upload_notebook / _list_note_files / _ensure_authenticated` are `async def` (`uploader.py:155 / :109 / :210 / :255`).
  - `QuickFilingService.run_once` / `_detect_candidates` / `_write_target_if_needed` are `async def` (`quick_filing_service.py:108 / :51 / :194`).
  - `reader.read_pages / resolve_filing_destination` are awaited inside the service → also async.
- **CLI implication:** the write/move commands must run inside an event loop (`asyncio.run`) because they await cloud calls, but the in-memory mutate steps (copy/remove/append/render) stay plain sync calls *inside* that async pipeline. The read-side primitives (snapshot, `StarDetector.starred_pages_from_metadata`) are sync and fit either model.
- **Lifecycle note:** `QuickFilingService` owns a `FilingLedger` opened from a `ledger_db_path` in `__init__` (`:36`); no explicit close in the class. If the CLI wraps it, ensure the ledger handle is closed after the run to avoid lingering SQLite handles in a short-lived process.
- `copy_pages_to_end`/`append_page_to_notebook` spill to a `tempfile` and unlink it in `finally` — safe for reuse, no leaks to manage.

---

## Severity summary (for the implementation planner)
- **Note (no blocker):** Spec's "new safe pipeline" is largely **already implemented** in `quick_filing_service.run_once` — reuse/extract, don't rewrite. Real risk is **triplication** of the move loop (CLI-new / quick_filing_service / organizer_api).
- **Note (real gap, NEW code):** No backup helper (G2), no post-mutation page-id/hash *verifier* (G1), no re-download SHA verify (G3). All small; `note_snapshot.build_snapshot_from_notebook` is the verification building block.
- **Note:** Duplicate `clear_recognition_metadata` (note_page_ops vs notebook_writer) and two append strategies — pick canonical sources to prevent drift.
- **Note:** Sync primitives + async cloud = CLI command layer must be async (`asyncio.run`) but mutate calls stay sync inside it.

No blockers. All claimed primitives exist, are tested, and are reusable as-is; the work is orchestration + the four small gaps above.
