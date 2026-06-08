# Supernote Cross-Note Page Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user drag one page tile from the current organizer grid onto another notebook in the sidebar, appending it to the target note while keeping the UI in the source note.

**Architecture:** Reuse existing `note_page_ops.copy_pages_to_end()` and `remove_pages()` for notebook byte mutation. Add an `OrganizerApi.move_page_to_notebook()` method that validates the source revision, uploads the target note first, then uploads the source note. Expose that through a new HTTP POST route and wire sidebar drop targets in the existing single-file organizer UI.

**Tech Stack:** Python 3.12, `http.server`, `supernotelib`, existing `SupernoteUploader`, embedded vanilla JavaScript, pytest, Speedrift Workgraph.

---

### Task 1: Backend Move API

**Files:**
- Modify: `src/paia_supernote/organizer_api.py`
- Modify: `tests/test_organizer_api.py`

- [ ] **Step 1: Write failing API tests**

Add tests in `tests/test_organizer_api.py`:

```python
@pytest.mark.asyncio
async def test_move_page_to_notebook_uploads_target_then_source(tmp_path, monkeypatch):
    organizer_api = _api_module()
    uploader = _FakeUploader()
    uploader.download_payloads = {
        "LFW.note": b"source-bytes",
        "Quick.note": b"target-bytes",
    }
    snapshots = {
        ("LFW", b"source-bytes"): _snapshot(),
        ("Quick", b"target-bytes"): NotebookSnapshot(
            notebook_name="Quick",
            revision="target-rev",
            page_order=["target-page"],
            pages={
                "target-page": PageRecord(
                    page_id="target-page",
                    page_index=0,
                    starred=False,
                    page_metadata={"PAGEID": "target-page"},
                    content_hash="target",
                    image_width=1404,
                    image_height=1872,
                )
            },
            metadata=NoteMetadataIndex(
                headings_by_page_id={},
                keywords_by_page_id={},
                links_by_page_id={},
                stars_by_page_id={},
            ),
        ),
    }
    api = organizer_api.OrganizerApi(
        uploader=uploader,
        snapshot_loader=lambda name, note_bytes: snapshots[(name, note_bytes)],
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )
    monkeypatch.setattr(
        organizer_api.note_page_ops,
        "copy_pages_to_end",
        lambda source, target, *, source_pages: b"target-with-page",
    )
    monkeypatch.setattr(
        organizer_api.note_page_ops,
        "remove_pages",
        lambda source, *, pages: b"source-without-page",
    )

    result = await api.move_page_to_notebook(
        "LFW",
        "page-b",
        source_revision="rev-1",
        target_notebook="Quick",
    )

    assert result["ok"] is True
    assert uploader.downloaded == ["LFW.note", "Quick.note"]
    assert [target for _path, target in uploader.uploaded] == ["Quick.note", "LFW.note"]
    assert uploader.uploaded_bytes == [b"target-with-page", b"source-without-page"]
```

Also add tests for stale source revision, same-notebook rejection, missing page rejection, and partial failure when target upload succeeds but source upload fails.

- [ ] **Step 2: Verify API tests fail**

Run:

```bash
uv run pytest tests/test_organizer_api.py -q
```

Expected: fail because `OrganizerApi.move_page_to_notebook` does not exist.

- [ ] **Step 3: Implement API method**

Add `from paia_supernote import note_page_ops` to `organizer_api.py`.

Add `move_page_to_notebook()` to `OrganizerApi`:

```python
async def move_page_to_notebook(
    self,
    source_notebook: str,
    page_id: str,
    *,
    source_revision: str,
    target_notebook: str,
) -> dict[str, Any]:
    if source_notebook == target_notebook:
        return {"ok": False, "reason": "same_notebook"}
    source_bytes = await self.uploader.download_notebook(f"{source_notebook}.note")
    source_snapshot = self.snapshot_loader(source_notebook, source_bytes)
    if source_snapshot.revision != source_revision:
        return {
            "ok": False,
            "reason": "stale_revision",
            "current_revision": source_snapshot.revision,
        }
    if page_id not in source_snapshot.page_order:
        return {"ok": False, "reason": "unknown_page_id", "page_id": page_id}
    source_page_index = source_snapshot.page_order.index(page_id)
    target_bytes = await self.uploader.download_notebook(f"{target_notebook}.note")
    target_reordered = note_page_ops.copy_pages_to_end(
        source_bytes,
        target_bytes,
        source_pages=[source_page_index],
    )
    source_reordered = note_page_ops.remove_pages(source_bytes, pages=[source_page_index])
    await self._upload_note_bytes(target_reordered, f"{target_notebook}.note")
    try:
        await self._upload_note_bytes(source_reordered, f"{source_notebook}.note")
    except Exception as exc:
        return {
            "ok": False,
            "reason": "partial_move_target_uploaded_source_failed",
            "source_notebook": source_notebook,
            "target_notebook": target_notebook,
            "page_id": page_id,
            "error": str(exc),
        }
    return {
        "ok": True,
        "source_notebook": source_notebook,
        "target_notebook": target_notebook,
        "page_id": page_id,
        "source_revision": hashlib.sha256(source_reordered).hexdigest(),
        "target_revision": hashlib.sha256(target_reordered).hexdigest(),
    }
```

Extract existing temp upload logic into:

```python
async def _upload_note_bytes(self, note_bytes: bytes, target_name: str) -> None:
    tmp_path = _write_temp_note(note_bytes)
    try:
        await self.uploader.upload_notebook(tmp_path, target_name)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
```

Use `_upload_note_bytes()` from `apply_reorder()`.

- [ ] **Step 4: Verify API tests pass**

Run:

```bash
uv run pytest tests/test_organizer_api.py -q
```

Expected: all organizer API tests pass.

### Task 2: HTTP Move Route

**Files:**
- Modify: `src/paia_supernote/organizer_server.py`
- Modify: `tests/test_organizer_server.py`

- [ ] **Step 1: Write failing server route tests**

Add a fake API method to `_FakeOrganizerApi`:

```python
async def move_page_to_notebook(self, source_notebook, page_id, *, source_revision, target_notebook):
    self.move_requests.append((source_notebook, page_id, source_revision, target_notebook))
    return self.move_result
```

Add a route test:

```python
def test_move_page_post_route_returns_api_result(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.move_result = {"ok": True, "source_notebook": "LFW", "target_notebook": "Quick"}

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/pages/page-a/move",
            {"source_revision": "rev-1", "target_notebook": "Quick"},
        )

    assert status == 200
    assert body == {"ok": True, "source_notebook": "LFW", "target_notebook": "Quick"}
    assert api.move_requests == [("LFW", "page-a", "rev-1", "Quick")]
```

Add status mapping tests for `same_notebook` / `unknown_page_id` as 422, `stale_revision` as 409, and partial failure as 500.

- [ ] **Step 2: Verify server tests fail**

Run:

```bash
uv run pytest tests/test_organizer_server.py -q
```

Expected: fail because the route does not exist.

- [ ] **Step 3: Implement the route**

In `_route_post()`, add a branch for:

```text
/api/notebooks/{source}/pages/{page_id}/move
```

Read `source_revision` and `target_notebook` from the JSON body, call `organizer_api.move_page_to_notebook()`, and send the result with a helper that maps domain reasons to HTTP status codes.

- [ ] **Step 4: Verify server tests pass**

Run:

```bash
uv run pytest tests/test_organizer_server.py -q
```

Expected: all server tests pass.

### Task 3: Sidebar Drop UI

**Files:**
- Modify: `src/paia_supernote/organizer_ui.py`
- Modify: `tests/test_organizer_ui.py`

- [ ] **Step 1: Write failing UI render tests**

Assert notebook links expose target metadata:

```python
assert 'data-notebook-name="Quick"' in html
assert 'data-drop-target="notebook"' in html
```

Assert the JavaScript includes cross-note move functions and status text:

```python
assert "movePageToNotebook" in html
assert "/move" in html
assert "Moving page to" in html
assert "Moved page to" in html
assert "Apply or undo the current reorder before moving pages to another note." in html
```

- [ ] **Step 2: Verify UI tests fail**

Run:

```bash
uv run pytest tests/test_organizer_ui.py -q
```

Expected: fail because notebook drop-target metadata and JS do not exist.

- [ ] **Step 3: Implement UI wiring**

Change `_render_notebooks()` to render:

```html
<a class="notebook..." href="/organizer?notebook=Quick" data-drop-target="notebook" data-notebook-name="Quick">Quick</a>
```

In JavaScript:

- Track `movingPage = false`.
- On drag start, preserve the dragged tile/page ID.
- Add dragover/drop handlers to `.notebook[data-drop-target="notebook"]`.
- Reject current notebook targets.
- If `isDirty()`, show `Apply or undo the current reorder before moving pages to another note.`
- POST to `/api/notebooks/${source}/pages/${pageId}/move` with `{ source_revision, target_notebook }`.
- On success, remove the tile, refresh `originalOrder`, call `renumberTiles()`, and show `Moved page to <target>.`
- On failure, keep the grid unchanged and show returned error/reason.

- [ ] **Step 4: Verify UI tests pass**

Run:

```bash
uv run pytest tests/test_organizer_ui.py -q
```

Expected: all UI tests pass.

### Task 4: Integration Verification And Tailnet Restart

**Files:**
- No new source files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_organizer_api.py tests/test_organizer_server.py tests/test_organizer_ui.py tests/test_note_page_ops.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run full tests**

Run:

```bash
uv run pytest -q
```

Expected: full suite passes.

- [ ] **Step 3: Run Speedrift completion check**

Run:

```bash
./.workgraph/drifts check --task supernote-cross-note-page-move --write-log --create-followups
```

Expected: task score green; repo-wide advisory findings may remain.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add src/paia_supernote/organizer_api.py src/paia_supernote/organizer_server.py src/paia_supernote/organizer_ui.py tests/test_organizer_api.py tests/test_organizer_server.py tests/test_organizer_ui.py docs/superpowers/plans/2026-06-08-supernote-cross-note-page-move-implementation.md
git commit -m "Add cross-note page moves"
```

- [ ] **Step 5: Restart tailnet organizer**

Stop the existing process on `100.77.214.44:18765`, then run:

```bash
uv run paia-supernote organizer --host 100.77.214.44 --port 18765
```

Smoke check:

```bash
curl -sS --max-time 60 -o /tmp/paia-supernote-organizer-cross-move.html -w '%{http_code} %{content_type} %{size_download}\n' http://100.77.214.44:18765/organizer
rg -n 'data-drop-target="notebook"|movePageToNotebook|/move' /tmp/paia-supernote-organizer-cross-move.html
```

Expected: HTTP 200 and cross-note move UI code present.
