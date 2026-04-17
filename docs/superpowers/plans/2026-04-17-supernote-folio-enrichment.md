# Supernote Folio Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Supernote ingestion durable under `launchd`, persist per-page enrichment state in SQLite, and upsert one Folio page object per Supernote page with renderable diagram data.

**Architecture:** Split the runtime into two services: an ingest daemon that polls Supernote Cloud, OCRs pages, and persists latest page revisions into SQLite, and an enrich daemon that turns dirty page rows into normalized markdown plus diagram payloads before upserting the matching Folio object. Reuse Folio's existing object upsert-by-path behavior and add a first-class UI renderer for stored diagram scenes so arbitrary page diagrams render without being forced through Mermaid.

**Tech Stack:** Python, SQLite, `httpx`, existing Z.AI-backed `SupernoteReader`, `pytest`, `launchd`, Svelte 5, Mermaid, Playwright

---

### Task 1: Add Red Tests For Durable Page State

**Files:**
- Create: `tests/test_page_state.py`
- Create: `src/paia_supernote/page_state.py`

- [ ] **Step 1: Write the failing page-state tests**

```python
from pathlib import Path

from paia_supernote.page_state import PageStateStore


def test_upsert_page_overwrites_same_notebook_page_and_marks_dirty(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()

    store.upsert_ocr_page(
        notebook="Quick",
        page=19,
        source_revision="rev-1",
        raw_text="first",
        ocr_model="glm-4.5v",
    )
    store.upsert_ocr_page(
        notebook="Quick",
        page=19,
        source_revision="rev-2",
        raw_text="second",
        ocr_model="glm-4.5v",
    )

    row = store.get_page("Quick", 19)
    assert row.source_revision == "rev-2"
    assert row.raw_text == "second"
    assert row.dirty_for_enrichment is True


def test_mark_enriched_only_updates_matching_revision(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-2", "second", "glm-4.5v")

    updated = store.mark_enriched(
        notebook="Quick",
        page=19,
        source_revision="rev-1",
        folio_object_id="abc",
    )

    row = store.get_page("Quick", 19)
    assert updated is False
    assert row.last_enriched_revision is None


def test_next_dirty_page_skips_future_retry_rows(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-2", "second", "glm-4.5v")
    store.mark_failed("Quick", 19, "enrich", "timeout", retry_delay_seconds=300)

    assert store.next_dirty_page() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_page_state.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paia_supernote.page_state'`

- [ ] **Step 3: Write the minimal SQLite page-state store**

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(slots=True)
class PageState:
    notebook: str
    page: int
    source_revision: str
    raw_text: str
    ocr_model: str
    dirty_for_enrichment: bool
    last_enriched_revision: str | None
    last_folio_object_id: str | None
    retry_count: int
    next_retry_at: str | None
    last_error: str | None
    last_error_stage: str | None


class PageStateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def init_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_state (
                    notebook TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    source_revision TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    ocr_model TEXT NOT NULL,
                    ocr_updated_at TEXT NOT NULL,
                    dirty_for_enrichment INTEGER NOT NULL DEFAULT 1,
                    last_enriched_revision TEXT,
                    last_folio_object_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    last_error_stage TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (notebook, page)
                )
                """
            )

    def upsert_ocr_page(
        self,
        notebook: str,
        page: int,
        source_revision: str,
        raw_text: str,
        ocr_model: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO page_state (
                    notebook, page, source_revision, raw_text, ocr_model,
                    ocr_updated_at, dirty_for_enrichment, retry_count,
                    next_retry_at, last_error, last_error_stage, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL, NULL, ?)
                ON CONFLICT(notebook, page) DO UPDATE SET
                    source_revision=excluded.source_revision,
                    raw_text=excluded.raw_text,
                    ocr_model=excluded.ocr_model,
                    ocr_updated_at=excluded.ocr_updated_at,
                    dirty_for_enrichment=1,
                    retry_count=0,
                    next_retry_at=NULL,
                    last_error=NULL,
                    last_error_stage=NULL,
                    updated_at=excluded.updated_at
                """,
                (notebook, page, source_revision, raw_text, ocr_model, now, now),
            )
```

- [ ] **Step 4: Add the queue helpers the tests need**

```python
    def get_page(self, notebook: str, page: int) -> PageState:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT notebook, page, source_revision, raw_text, ocr_model,
                       dirty_for_enrichment, last_enriched_revision,
                       last_folio_object_id, retry_count, next_retry_at,
                       last_error, last_error_stage
                FROM page_state
                WHERE notebook = ? AND page = ?
                """,
                (notebook, page),
            ).fetchone()
        assert row is not None
        return PageState(*row)

    def next_dirty_page(self) -> PageState | None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT notebook, page, source_revision, raw_text, ocr_model,
                       dirty_for_enrichment, last_enriched_revision,
                       last_folio_object_id, retry_count, next_retry_at,
                       last_error, last_error_stage
                FROM page_state
                WHERE dirty_for_enrichment = 1
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY updated_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        return None if row is None else PageState(*row)

    def mark_enriched(
        self,
        notebook: str,
        page: int,
        source_revision: str,
        folio_object_id: str,
    ) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE page_state
                SET dirty_for_enrichment = 0,
                    last_enriched_revision = ?,
                    last_folio_object_id = ?,
                    retry_count = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_error_stage = NULL,
                    updated_at = ?
                WHERE notebook = ? AND page = ? AND source_revision = ?
                """,
                (
                    source_revision,
                    folio_object_id,
                    datetime.now(timezone.utc).isoformat(),
                    notebook,
                    page,
                    source_revision,
                ),
            )
        return cur.rowcount == 1

    def mark_failed(
        self,
        notebook: str,
        page: int,
        stage: str,
        error: str,
        retry_delay_seconds: int,
    ) -> None:
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE page_state
                SET retry_count = retry_count + 1,
                    next_retry_at = ?,
                    last_error = ?,
                    last_error_stage = ?,
                    updated_at = ?
                WHERE notebook = ? AND page = ?
                """,
                (
                    retry_at,
                    error,
                    stage,
                    datetime.now(timezone.utc).isoformat(),
                    notebook,
                    page,
                ),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_page_state.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/paia_supernote/page_state.py tests/test_page_state.py
git commit -m "feat: add durable supernote page state store"
```

### Task 2: Add Red Tests For Enrichment Output And Folio Upsert

**Files:**
- Create: `src/paia_supernote/enrichment.py`
- Modify: `src/paia_supernote/folio.py`
- Create: `tests/test_enrichment.py`
- Modify: `tests/test_folio.py`

- [ ] **Step 1: Write the failing enrichment contract tests**

```python
from unittest.mock import AsyncMock, patch

import pytest

from paia_supernote.enrichment import SupernoteEnricher


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_returns_markdown_and_scene(mock_client) -> None:
    mock_http = AsyncMock()
    mock_resp = AsyncMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": """
                {
                  "markdown": "# Plan\\n- ship it",
                  "diagram": {
                    "kind": "scene",
                    "scene": {"nodes": [{"id": "n1", "label": "Start", "shape": "box", "x": 0.1, "y": 0.2}], "edges": []},
                    "summary": "Simple flow",
                    "confidence": 0.92,
                    "render_version": "1"
                  }
                }
                """
            }
        }]
    }
    mock_http.post.return_value = mock_resp
    mock_client.return_value.__aenter__.return_value = mock_http

    enricher = SupernoteEnricher(zai_api_key="token")
    result = await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw bullets")

    assert result.markdown == "# Plan\\n- ship it"
    assert result.diagram["kind"] == "scene"
    assert result.diagram["scene"]["nodes"][0]["label"] == "Start"
```

- [ ] **Step 2: Write the failing Folio upsert test**

```python
@pytest.mark.asyncio
async def test_upsert_supernote_page_posts_stable_path_payload() -> None:
    with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"id": "obj-1", "path": "supernote/Quick/page-19"}
        mock_http.post.return_value = mock_resp
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await upsert_supernote_page(
            notebook="Quick",
            page=19,
            source_revision="rev-2",
            raw_text="raw",
            markdown="# Plan",
            diagram={"kind": "scene", "scene": {"nodes": [], "edges": []}, "render_version": "1"},
            folio_url="http://localhost:8000",
        )

        body = mock_http.post.call_args.kwargs["json"]
        assert body["path"] == "supernote/Quick/page-19"
        assert body["object_type"] == "supernote-page"
        assert body["content"] == "# Plan"
        assert body["properties"]["raw_text"] == "raw"
        assert body["properties"]["diagram"]["kind"] == "scene"
        assert body["properties"]["source"]["source_revision"] == "rev-2"
        assert result["id"] == "obj-1"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_enrichment.py tests/test_folio.py -q`
Expected: FAIL because `SupernoteEnricher` and `upsert_supernote_page` do not exist yet.

- [ ] **Step 4: Implement the enrichment model and Z.AI call**

```python
from dataclasses import dataclass
import json
import os

import httpx


@dataclass(slots=True)
class EnrichedPage:
    markdown: str
    diagram: dict[str, object]
    summary: str | None
    confidence: float | None


class SupernoteEnricher:
    def __init__(
        self,
        *,
        zai_api_key: str | None = None,
        zai_base_url: str = "https://api.z.ai/api/coding/paas/v4",
        zai_text_model: str = "glm-5.1",
    ) -> None:
        self.zai_api_key = zai_api_key or os.environ["ZAI_API_KEY"]
        self.zai_base_url = zai_base_url.rstrip("/")
        self.zai_text_model = zai_text_model

    async def enrich_page(self, *, notebook: str, page: int, raw_text: str) -> EnrichedPage:
        prompt = (
            "Normalize this Supernote page into readable markdown and a renderable diagram. "
            "Return strict JSON with keys markdown and diagram."
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.zai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.zai_api_key}"},
                json={
                    "model": self.zai_text_model,
                    "messages": [{"role": "user", "content": f"{prompt}\\n\\nNotebook: {notebook}\\nPage: {page}\\n\\n{raw_text}"}],
                    "response_format": {"type": "json_object"},
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            payload = json.loads(resp.json()["choices"][0]["message"]["content"])
        diagram = payload.get("diagram") or {"kind": "none", "render_version": "1"}
        return EnrichedPage(
            markdown=payload.get("markdown", raw_text),
            diagram=diagram,
            summary=diagram.get("summary"),
            confidence=diagram.get("confidence"),
        )
```

- [ ] **Step 5: Implement the Folio page upsert helper**

```python
async def upsert_supernote_page(
    *,
    notebook: str,
    page: int,
    source_revision: str,
    raw_text: str,
    markdown: str,
    diagram: dict[str, object],
    folio_url: str = _DEFAULT_FOLIO_URL,
) -> dict[str, Any] | None:
    payload = {
        "title": f"{notebook} — page {page}",
        "path": f"supernote/{notebook}/page-{page}",
        "content": markdown,
        "object_type": "supernote-page",
        "properties": {
            "raw_text": raw_text,
            "diagram": diagram,
            "source": {
                "notebook": notebook,
                "page": page,
                "source_revision": source_revision,
            },
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{folio_url}/api/folio/objects", json=payload, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_enrichment.py tests/test_folio.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/paia_supernote/enrichment.py src/paia_supernote/folio.py tests/test_enrichment.py tests/test_folio.py
git commit -m "feat: add supernote enrichment and folio upsert"
```

### Task 3: Refactor `paia-supernote` Into Ingest And Enrich Runners

**Files:**
- Create: `src/paia_supernote/ingest_service.py`
- Create: `src/paia_supernote/enrich_service.py`
- Modify: `src/paia_supernote/cloud_poller.py`
- Modify: `src/paia_supernote/main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write the failing CLI and lifecycle tests**

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

def test_parser_supports_ingest_enrich_and_status() -> None:
    parser = build_parser()
    assert parser.parse_args(["ingest"]).mode == "ingest"
    assert parser.parse_args(["enrich"]).mode == "enrich"
    assert parser.parse_args(["status"]).mode == "status"


@pytest.mark.asyncio
async def test_ingest_service_persists_latest_page_revision(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    mock_reader = AsyncMock()
    mock_reader.process_file.return_value = [
        SimpleNamespace(notebook="Quick", page_num=19, text="raw v1"),
    ]
    mock_uploader = AsyncMock()
    mock_poller = MagicMock()
    service = IngestService(config=config, reader=mock_reader, uploader=mock_uploader, cloud_poller=mock_poller)

    await service._on_note_changed("Quick", b"note-bytes", 123456)

    row = service.page_state.get_page("Quick", 19)
    assert row.raw_text == "raw v1"
    assert row.source_revision.endswith(":19")


@pytest.mark.asyncio
async def test_enrich_service_discards_stale_revision_before_folio_write(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-1", "raw v1", "glm-4.5v")

    async def mutate_revision_then_return(*, notebook: str, page: int, raw_text: str):
        store.upsert_ocr_page(notebook, page, "rev-2", "raw v2", "glm-4.5v")
        return SimpleNamespace(
            markdown="# Updated",
            diagram={"kind": "scene", "scene": {"nodes": [], "edges": []}, "render_version": "1"},
        )

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = mutate_revision_then_return
    mock_folio = AsyncMock()

    service = EnrichService(config=config, page_state=store, enricher=mock_enricher, folio_client=mock_folio)
    wrote = await service.run_once()

    assert wrote is False
    mock_folio.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -q`
Expected: FAIL because the CLI is still single-process and the new services do not exist.

- [ ] **Step 3: Add the ingest runner**

```python
class IngestService:
    def __init__(self, *, config: dict[str, Any], reader: SupernoteReader | None = None, uploader: SupernoteUploader | None = None, cloud_poller: CloudPoller | None = None) -> None:
        self.config = config
        self.reader = reader or SupernoteReader(
            vision_backend=config["vision_backend"],
            ollama_model=config["ollama_model"],
            ollama_url=config["ollama_url"],
            zai_base_url=config["zai_base_url"],
            zai_vision_model=config["zai_vision_model"],
            zai_text_model=config["zai_text_model"],
        )
        self.uploader = uploader or SupernoteUploader()
        self.page_state = PageStateStore(Path(config["state_db_path"]))
        self.cloud_poller = cloud_poller or CloudPoller(
            uploader=self.uploader,
            on_note_changed=self._on_note_changed,
            poll_interval=config["poll_interval"],
        )

    async def _on_note_changed(self, notebook_name: str, note_bytes: bytes, update_time: int | None = None) -> None:
        results = await self.reader.process_file(note_bytes, notebook_name)
        note_hash = hashlib.sha256(note_bytes).hexdigest()
        for result in results:
            source_revision = f"{update_time or 0}:{note_hash}:{result.page_num}"
            self.page_state.upsert_ocr_page(
                notebook=result.notebook,
                page=result.page_num,
                source_revision=source_revision,
                raw_text=result.text,
                ocr_model=self.config["zai_vision_model"],
            )
```

- [ ] **Step 4: Update the poller callback signature to include source metadata**

```python
NoteChangedCallback = Callable[[str, bytes, int | None], Awaitable[None]]

await self._callback(notebook_name, note_bytes, update_time)
```

- [ ] **Step 5: Add the enrich runner and status command**

```python
class EnrichService:
    async def run_once(self) -> bool:
        row = self.page_state.next_dirty_page()
        if row is None:
            return False
        enriched = await self.enricher.enrich_page(notebook=row.notebook, page=row.page, raw_text=row.raw_text)
        current = self.page_state.get_page(row.notebook, row.page)
        if current.source_revision != row.source_revision:
            return False
        result = await upsert_supernote_page(
            notebook=row.notebook,
            page=row.page,
            source_revision=row.source_revision,
            raw_text=row.raw_text,
            markdown=enriched.markdown,
            diagram=enriched.diagram,
            folio_url=self.config["folio_url"],
        )
        self.page_state.mark_enriched(
            notebook=row.notebook,
            page=row.page,
            source_revision=row.source_revision,
            folio_object_id=result["id"],
        )
        return True
```

- [ ] **Step 6: Rework `main.py` to dispatch by mode**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paia-supernote")
    parser.add_argument("--config", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="mode", required=False)
    subparsers.add_parser("ingest")
    subparsers.add_parser("enrich")
    subparsers.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    mode = args.mode or "ingest"
    if mode == "ingest":
        runner = IngestService(config)
    elif mode == "enrich":
        runner = EnrichService(config)
    else:
        print(render_status(Path(config["state_db_path"])))
        return
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py tests/test_page_state.py tests/test_enrichment.py tests/test_folio.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/paia_supernote/cloud_poller.py src/paia_supernote/ingest_service.py src/paia_supernote/enrich_service.py src/paia_supernote/main.py tests/test_main.py
git commit -m "feat: split supernote into ingest and enrich runners"
```

### Task 4: Add Launchd Assets For Both Long-Lived Services

**Files:**
- Create: `scripts/service.sh`
- Create: `scripts/paia-supernote-ingest.plist`
- Create: `scripts/paia-supernote-enrich.plist`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write the failing smoke test for config defaults**

```python
def test_default_config_includes_state_db_path(tmp_path: Path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml")
    assert config["state_db_path"].endswith("supernote-state.db")
```

- [ ] **Step 2: Add launchd-friendly config defaults**

```python
DEFAULT_CONFIG = {
    "poll_interval": 60,
    "vision_backend": "zai",
    "rewrite_backend": "zai",
    "ollama_model": "qwen2.5vl:7b",
    "ollama_url": "http://localhost:11434",
    "zai_base_url": "https://api.z.ai/api/coding/paas/v4",
    "zai_vision_model": "glm-4.5v",
    "zai_text_model": "glm-5.1",
    "events_url": "http://localhost:3511",
    "folio_url": "http://localhost:3512",
    "work_url": "http://localhost:3560",
    "state_db_path": str(Path("~/.paia/supernote/supernote-state.db").expanduser()),
}
```

- [ ] **Step 3: Add the service wrapper**

```bash
#!/usr/bin/env bash
set -euo pipefail

INGEST_LABEL="com.paia.supernote.ingest"
ENRICH_LABEL="com.paia.supernote.enrich"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/paia-supernote"

case "${1:-help}" in
  install)
    mkdir -p "$PLIST_DIR" "$LOG_DIR"
    cp "$(dirname "$0")/paia-supernote-ingest.plist" "$PLIST_DIR/$INGEST_LABEL.plist"
    cp "$(dirname "$0")/paia-supernote-enrich.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    launchctl load "$PLIST_DIR/$INGEST_LABEL.plist"
    launchctl load "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  uninstall)
    launchctl unload "$PLIST_DIR/$INGEST_LABEL.plist" 2>/dev/null || true
    launchctl unload "$PLIST_DIR/$ENRICH_LABEL.plist" 2>/dev/null || true
    rm -f "$PLIST_DIR/$INGEST_LABEL.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  start)
    launchctl start "$INGEST_LABEL"
    launchctl start "$ENRICH_LABEL"
    ;;
  stop)
    launchctl stop "$INGEST_LABEL"
    launchctl stop "$ENRICH_LABEL"
    ;;
  status)
    launchctl list "$INGEST_LABEL" || true
    launchctl list "$ENRICH_LABEL" || true
    ;;
  logs)
    tail -f "$LOG_DIR/ingest.stdout.log" "$LOG_DIR/ingest.stderr.log" "$LOG_DIR/enrich.stdout.log" "$LOG_DIR/enrich.stderr.log"
    ;;
esac
```

- [ ] **Step 4: Add the two plist files**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.paia.supernote.ingest</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/braydon/.local/bin/uv</string>
  <string>run</string>
  <string>paia-supernote</string>
  <string>ingest</string>
</array>
<key>WorkingDirectory</key>
<string>/Users/braydon/projects/experiments/paia-supernote</string>
<key>EnvironmentVariables</key>
<dict>
  <key>PATH</key>
  <string>/Users/braydon/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
</dict>
<key>KeepAlive</key>
<true/>
<key>RunAtLoad</key>
<true/>
<key>StandardOutPath</key>
<string>/Users/braydon/Library/Logs/paia-supernote/ingest.stdout.log</string>
<key>StandardErrorPath</key>
<string>/Users/braydon/Library/Logs/paia-supernote/ingest.stderr.log</string>
</dict>
</plist>
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.paia.supernote.enrich</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/braydon/.local/bin/uv</string>
    <string>run</string>
    <string>paia-supernote</string>
    <string>enrich</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/braydon/projects/experiments/paia-supernote</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Users/braydon/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/braydon/Library/Logs/paia-supernote/enrich.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/braydon/Library/Logs/paia-supernote/enrich.stderr.log</string>
</dict>
</plist>
```

- [ ] **Step 5: Validate the plists**

Run:

```bash
plutil -lint scripts/paia-supernote-ingest.plist
plutil -lint scripts/paia-supernote-enrich.plist
```

Expected: `OK` for both files

- [ ] **Step 6: Commit**

```bash
git add src/paia_supernote/main.py scripts/service.sh scripts/paia-supernote-ingest.plist scripts/paia-supernote-enrich.plist tests/test_main.py
git commit -m "feat: add launchd supervision for supernote services"
```

### Task 5: Render Stored Diagram Scenes In Folio

**Files:**
- Create: `../folio/ui/src/lib/components/notes/DiagramScene.svelte`
- Create: `../folio/ui/src/lib/components/notes/DiagramMermaid.svelte`
- Modify: `../folio/ui/src/lib/components/notes/NoteEditor.svelte`
- Modify: `../folio/ui/src/lib/types/index.ts`
- Create: `../folio/ui/tests/supernote-diagram.spec.ts`

- [ ] **Step 1: Write the failing Playwright test**

```typescript
import { test, expect } from "@playwright/test";
import { openNoteByTitle } from "./helpers";

const API = "http://localhost:3520/api/folio";

test("renders a stored supernote scene diagram", async ({ page, request }) => {
  const res = await request.post(`${API}/objects`, {
    data: {
      title: "Quick — page 19",
      path: "supernote/Quick/page-19",
      object_type: "supernote-page",
      content: "# Plan\n- ship it",
      properties: {
        diagram: {
          kind: "scene",
          render_version: "1",
          scene: {
            nodes: [{ id: "n1", label: "Start", shape: "box", x: 0.2, y: 0.3, width: 0.2, height: 0.12 }],
            edges: [],
          },
        },
      },
    },
  });

  const noteId = (await res.json()).id;
  await page.goto("/");
  await openNoteByTitle(page, "Quick — page 19");
  await expect(page.locator("[data-testid='diagram-scene']")).toBeVisible();
  await expect(page.locator("text=Start")).toBeVisible();
  await request.delete(`${API}/notes/${noteId}`);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ../folio/ui && npm exec playwright test tests/supernote-diagram.spec.ts`
Expected: FAIL because no note-page renderer exists for `properties.diagram.scene`.

- [ ] **Step 3: Implement the diagram scene renderer**

```svelte
<script lang="ts">
  interface DiagramNode {
    id: string;
    label: string;
    shape: string;
    x: number;
    y: number;
    width?: number;
    height?: number;
  }

  interface DiagramEdge {
    from: string;
    to: string;
    label?: string;
  }

  let { diagram }: { diagram: Record<string, unknown> } = $props();
  const scene = $derived((diagram.scene as { nodes?: DiagramNode[]; edges?: DiagramEdge[] }) ?? { nodes: [], edges: [] });
</script>

<svg data-testid="diagram-scene" viewBox="0 0 1000 700">
  {#each scene.nodes ?? [] as node}
    <rect
      x={node.x * 1000}
      y={node.y * 700}
      width={(node.width ?? 0.18) * 1000}
      height={(node.height ?? 0.1) * 700}
      rx="16"
      class="fill-white stroke-zinc-300"
    />
    <text x={(node.x + 0.02) * 1000} y={(node.y + 0.06) * 700}>{node.label}</text>
  {/each}
</svg>
```

- [ ] **Step 4: Implement the Mermaid renderer**

```svelte
<script lang="ts">
  import { onMount } from "svelte";
  import mermaid from "mermaid";

  let { source }: { source: string } = $props();
  let svg = $state("");

  onMount(async () => {
    mermaid.initialize({ startOnLoad: false, theme: "default" });
    const rendered = await mermaid.render(`supernote-mermaid-${crypto.randomUUID()}`, source);
    svg = rendered.svg;
  });
</script>

<div data-testid="diagram-mermaid">{@html svg}</div>
```

- [ ] **Step 5: Integrate the renderers into the note view**

```svelte
  const diagram = $derived((note.properties?.diagram as Record<string, unknown> | undefined) ?? null);

  import DiagramScene from "./DiagramScene.svelte";
  import DiagramMermaid from "./DiagramMermaid.svelte";
  import MilkdownEditor from "./MilkdownEditor.svelte";
{#if note.object_type === "supernote-page" && diagram}
  {#if diagram.kind === "scene" && diagram.scene}
    <DiagramScene {diagram} />
  {:else if diagram.kind === "mermaid" && diagram.mermaid}
    <DiagramMermaid source={String(diagram.mermaid)} />
  {/if}
{/if}
<MilkdownEditor
  markdown={editorContent}
  onChange={handleContentChange}
  onUploadImage={handleUploadImage}
  onLinkClick={handleLinkClick}
/>
```

- [ ] **Step 6: Widen the note properties typing just enough for the renderer**

```typescript
export interface Note {
  id: string;
  tenant_id: string;
  title: string;
  content: string;
  path: string | null;
  metadata: Record<string, unknown> | null;
  object_type: string | null;
  properties: Record<string, unknown> | null;
  pinned_at: string | null;
  created_at: string;
  updated_at: string;
}
```

Keep the API type permissive and decode `diagram` locally in the component instead of building a large frontend schema prematurely.

- [ ] **Step 7: Run frontend verification**

Run:

```bash
cd ../folio/ui
npm run check
npm exec playwright test tests/supernote-diagram.spec.ts
```

Expected: both commands PASS

- [ ] **Step 8: Commit**

```bash
git -C ../folio add ui/src/lib/components/notes/DiagramScene.svelte ui/src/lib/components/notes/DiagramMermaid.svelte ui/src/lib/components/notes/NoteEditor.svelte ui/tests/supernote-diagram.spec.ts
git -C ../folio commit -m "feat: render stored supernote diagrams in folio"
```

### Task 6: Final Cross-Repo Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run the `paia-supernote` backend verification suite**

Run:

```bash
cd /Users/braydon/projects/experiments/paia-supernote
uv run pytest tests/test_main.py tests/test_page_state.py tests/test_enrichment.py tests/test_folio.py -q
```

Expected: PASS

- [ ] **Step 2: Run the Folio backend/UI verification that covers the new diagram path**

Run:

```bash
cd /Users/braydon/projects/experiments/folio
uv run pytest tests/test_api.py -q
cd ui
npm run check
npm exec playwright test tests/supernote-diagram.spec.ts
```

Expected: PASS

- [ ] **Step 3: Smoke-test the launchd assets and status command**

Run:

```bash
cd /Users/braydon/projects/experiments/paia-supernote
plutil -lint scripts/paia-supernote-ingest.plist
plutil -lint scripts/paia-supernote-enrich.plist
uv run paia-supernote status
```

Expected: plist validation succeeds and `status` prints queue counts rather than crashing.

- [ ] **Step 4: Run the repo-local speedrift check before handoff**

Run:

```bash
cd /Users/braydon/projects/experiments/paia-supernote
TASK_ID="$(wg ready --json | jq -r '.[0].id')"
test -n "$TASK_ID"
./.workgraph/drifts check --task "$TASK_ID" --write-log --create-followups || test $? -eq 3
```

Expected: exit `0` or `3`; any findings become explicit follow-up tasks, not hidden scope creep.

- [ ] **Step 5: Confirm only intended files changed in each repo**

Run:

```bash
git -C /Users/braydon/projects/experiments/paia-supernote status --short
git -C /Users/braydon/projects/experiments/folio status --short
```

Expected: only the planned Supernote and Folio files remain modified.
