# Supernote Organizer Write Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add same-notebook drag reorder with preview/apply endpoints that rewrite and upload a `.note` only when the browser snapshot revision is still current.

**Architecture:** Keep write behavior server-side and conservative. The browser maintains a proposed page order, asks the API to preview/validate it, then applies it with the snapshot revision it loaded. The API re-downloads the cloud note before preview/apply, blocks stale revisions and unsafe metadata, writes reordered bytes through `note_reorder.reorder_pages`, uploads with `SupernoteUploader.upload_notebook`, and returns a fresh snapshot.

**Tech Stack:** Python stdlib HTTP server, existing `OrganizerApi`, existing `note_reorder`, existing `SupernoteUploader`, vanilla JS drag/drop, pytest, Speedrift drift checks.

---

## File Structure

- `src/paia_supernote/organizer_api.py`: add `preview_reorder()` and `apply_reorder()`; serialize consistent JSON results.
- `src/paia_supernote/organizer_server.py`: add `POST` routing and JSON body parsing for preview/apply.
- `src/paia_supernote/organizer_ui.py`: add drag/drop page tiles, unsaved state, preview/apply calls, and status output.
- `tests/test_organizer_api.py`: validate stale revision, unsafe order, unsafe link metadata, upload path, and fresh snapshot return.
- `tests/test_organizer_server.py`: validate POST routing and HTTP status/body behavior.
- `tests/test_organizer_ui.py`: validate drag/apply controls and client-side contract strings.

## Task 1: Reorder Preview And Apply API

**Files:**
- Modify: `src/paia_supernote/organizer_api.py`
- Test: `tests/test_organizer_api.py`

- [ ] **Step 1: Write failing API tests**

Add tests with fake uploader, fake snapshot loader, and monkeypatched `note_reorder.reorder_pages`:

```python
@pytest.mark.asyncio
async def test_preview_reorder_rejects_stale_revision(tmp_path) -> None:
    api = _make_api(tmp_path, revision="current")
    result = await api.preview_reorder("LFW", expected_revision="old", page_order=["page-a"])
    assert result["ok"] is False
    assert result["reason"] == "stale_revision"

@pytest.mark.asyncio
async def test_apply_reorder_uploads_reordered_bytes_and_returns_snapshot(tmp_path, monkeypatch) -> None:
    uploader = _FakeUploader()
    api = _make_api(tmp_path, uploader=uploader, revision="rev-1")
    monkeypatch.setattr("paia_supernote.organizer_api.note_reorder.reorder_pages", lambda b, page_order: b"reordered")
    result = await api.apply_reorder("LFW", expected_revision="rev-1", page_order=["page-a"])
    assert result["ok"] is True
    assert uploader.uploaded[0][1] == "LFW.note"
    assert Path(uploader.uploaded[0][0]).read_bytes() == b"reordered"
    assert result["snapshot"]["notebook_name"] == "LFW"
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/test_organizer_api.py -q`

Expected: failures for missing `preview_reorder` / `apply_reorder`.

- [ ] **Step 3: Implement minimal API**

Add result helpers and methods:

```python
async def preview_reorder(self, notebook_name: str, *, expected_revision: str, page_order: list[str]) -> dict[str, Any]:
    note_bytes = await self.uploader.download_notebook(f"{notebook_name}.note")
    snapshot = self.snapshot_loader(notebook_name, note_bytes)
    if snapshot.revision != expected_revision:
        return {"ok": False, "reason": "stale_revision", "current_revision": snapshot.revision}
    try:
        note_reorder.reorder_pages(note_bytes, page_order=page_order)
    except note_reorder.UnsupportedLinkMetadataError as exc:
        return {"ok": False, "reason": "unsupported_link_metadata", "error": str(exc)}
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_page_order", "error": str(exc)}
    return {"ok": True, "revision": snapshot.revision, "page_order": list(page_order)}
```

`apply_reorder()` repeats validation, writes reordered bytes to a temp `.note`, calls `upload_notebook(path, f"{notebook_name}.note")`, and returns a fresh `get_snapshot()` payload.

- [ ] **Step 4: Verify green**

Run: `uv run pytest tests/test_organizer_api.py -q`

- [ ] **Step 5: Speedrift drift and commit**

Run: `./.workgraph/drifts check --task supernote-organizer-apply-api --write-log --create-followups`

Commit: `git commit -m "Add organizer reorder apply API"`

## Task 2: HTTP Preview And Apply Routes

**Files:**
- Modify: `src/paia_supernote/organizer_server.py`
- Test: `tests/test_organizer_server.py`

- [ ] **Step 1: Write failing server tests**

Add tests for:

```python
POST /api/notebooks/LFW/reorder/preview
POST /api/notebooks/LFW/reorder/apply
```

Payload:

```json
{"expected_revision":"rev-1","page_order":["page-b","page-a"]}
```

Expected:
- `200` and `{"ok": true}` for success.
- `409` for `reason == "stale_revision"`.
- `422` for invalid page order or unsupported link metadata.

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/test_organizer_server.py -q`

- [ ] **Step 3: Implement minimal server POST support**

Add `do_POST()`, `_read_json_body()`, `_route_post()`, and `_send_reorder_result()`.

- [ ] **Step 4: Verify green**

Run: `uv run pytest tests/test_organizer_server.py -q`

- [ ] **Step 5: Speedrift drift and commit**

Run: `./.workgraph/drifts check --task supernote-organizer-apply-server --write-log --create-followups`

Commit: `git commit -m "Add organizer reorder HTTP routes"`

## Task 3: Drag/Preview/Apply UI

**Files:**
- Modify: `src/paia_supernote/organizer_ui.py`
- Test: `tests/test_organizer_ui.py`

- [ ] **Step 1: Write failing UI tests**

Assert rendered HTML contains:
- draggable tiles: `draggable="true"`
- apply button id: `id="apply-order"`
- undo button id: `id="undo-order"`
- status region: `id="organizer-status"`
- JS calls `/reorder/preview` and `/reorder/apply`
- JS includes `expected_revision` and current ordered `page_id`s.

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/test_organizer_ui.py -q`

- [ ] **Step 3: Implement minimal UI**

Add:
- tile dragstart/dragover/drop handlers
- `currentOrder()` from `.page-tile`
- `markDirty()` to enable Undo/Apply
- `previewOrder()` before apply
- `applyOrder()` with confirm-free direct apply only after preview ok
- status text area for errors and success

- [ ] **Step 4: Verify green**

Run: `uv run pytest tests/test_organizer_ui.py -q`

- [ ] **Step 5: Speedrift drift and commit**

Run: `./.workgraph/drifts check --task supernote-organizer-apply-ui --write-log --create-followups`

Commit: `git commit -m "Add organizer drag apply UI"`

## Task 4: Final Verification And Tailnet Restart

**Files:**
- No new production files expected.
- Use existing tests.

- [ ] **Step 1: Run focused suite**

Run:

```bash
uv run pytest tests/test_organizer_api.py tests/test_organizer_server.py tests/test_organizer_ui.py tests/test_note_reorder.py tests/test_note_page_ops.py -q
```

- [ ] **Step 2: Run full suite**

Run: `uv run pytest -q`

- [ ] **Step 3: Run post-task Speedrift drift**

Run: `./.workgraph/drifts check --task supernote-organizer-apply-final --write-log --create-followups`

- [ ] **Step 4: Restart tailnet server**

Run:

```bash
uv run paia-supernote organizer --host 100.77.214.44 --port 18765
```

Verify:

```bash
curl -sS --max-time 30 -o /tmp/paia-supernote-notebooks.json -w '%{http_code}\n' http://100.77.214.44:18765/api/notebooks
curl -sS --max-time 60 -o /tmp/paia-supernote-organizer.html -w '%{http_code}\n' http://100.77.214.44:18765/organizer
```

- [ ] **Step 5: Commit final verification notes if files changed**

If no files changed, do not create an empty commit.

## Self-Review

- Spec coverage: same-notebook reorder, preview, stale revision protection, upload apply, and UI drag/apply are covered.
- Explicitly out of scope: cross-notebook move, custom markers, and automatic conflict merge.
- Type consistency: `expected_revision`, `page_order`, `ok`, `reason`, `snapshot`, and `current_revision` are used consistently across API/server/UI.
