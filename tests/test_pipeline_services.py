"""Tests for the split ingest/enrich service runners."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from paia_supernote.enrich_service import EnrichService
from paia_supernote.ingest_service import IngestService
from paia_supernote.main import DEFAULT_CONFIG
from paia_supernote.page_state import PageStateStore


@pytest.mark.asyncio
async def test_ingest_service_persists_latest_page_revision(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    mock_reader = AsyncMock()
    mock_reader.process_file.return_value = [
        SimpleNamespace(notebook="Quick", page_num=19, text="raw v1"),
    ]
    mock_uploader = AsyncMock()
    mock_poller = AsyncMock()

    service = IngestService(
        config=config,
        reader=mock_reader,
        uploader=mock_uploader,
        cloud_poller=mock_poller,
    )

    await service._on_note_changed("Quick", b"note-bytes", 123456)

    row = service.page_state.get_page("Quick", 19)
    assert row.raw_text == "raw v1"
    assert row.source_revision.endswith(":19")


@pytest.mark.asyncio
async def test_enrich_service_discards_stale_revision_before_folio_write(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    store = PageStateStore(Path(config["state_db_path"]))
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-1", "raw v1", "glm-4.5v")

    async def mutate_revision_then_return(*, notebook: str, page: int, raw_text: str):
        store.upsert_ocr_page(notebook, page, "rev-2", "raw v2", "glm-4.5v")
        return SimpleNamespace(
            markdown="# Updated",
            diagram={"kind": "scene", "scene": {"nodes": [], "edges": []}, "render_version": "1"},
            summary=None,
            confidence=None,
        )

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = mutate_revision_then_return
    mock_folio = AsyncMock()

    service = EnrichService(
        config=config,
        page_state=store,
        enricher=mock_enricher,
        folio_upserter=mock_folio,
    )
    wrote = await service.run_once()

    assert wrote is False
    mock_folio.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_service_marks_retry_state_on_failure(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    store = PageStateStore(Path(config["state_db_path"]))
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-1", "raw v1", "glm-4.5v")

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = RuntimeError("boom")

    service = EnrichService(
        config=config,
        page_state=store,
        enricher=mock_enricher,
    )

    wrote = await service.run_once()

    row = store.get_page("Quick", 19)
    assert wrote is False
    assert row.retry_count == 1
    assert row.last_error_stage == "enrich"
    assert row.last_error == "boom"
