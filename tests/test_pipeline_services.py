"""Tests for the split ingest/enrich service runners."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.cloud_poller import CloudPoller
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
async def test_ingest_service_persists_completed_pages_before_later_page_failure(
    tmp_path: Path,
) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    mock_reader = AsyncMock()

    async def fake_process_file(note_bytes, notebook_name, on_result=None):
        assert notebook_name == "Quick"
        assert on_result is not None
        await on_result(
            SimpleNamespace(notebook="Quick", page_num=19, text="raw v1")
        )
        raise RuntimeError("page 20 failed")

    mock_reader.process_file.side_effect = fake_process_file
    service = IngestService(
        config=config,
        reader=mock_reader,
        uploader=AsyncMock(),
        cloud_poller=MagicMock(),
    )

    with pytest.raises(RuntimeError, match="page 20 failed"):
        await service._on_note_changed("Quick", b"note-bytes", 123456)

    row = service.page_state.get_page("Quick", 19)
    assert row is not None
    assert row.raw_text == "raw v1"
    assert row.source_revision.endswith(":19")


@pytest.mark.asyncio
async def test_enrich_service_discards_stale_revision_before_folio_write(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    store = PageStateStore(Path(config["state_db_path"]))
    store.init_schema()
    store.upsert_ocr_page("LFW", 19, "rev-1", "raw v1", "glm-4.5v")

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
    store.upsert_ocr_page("LFW", 19, "rev-1", "raw v1", "glm-4.5v")

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = RuntimeError("boom")

    service = EnrichService(
        config=config,
        page_state=store,
        enricher=mock_enricher,
    )

    wrote = await service.run_once()

    row = store.get_page("LFW", 19)
    assert wrote is False
    assert row.retry_count == 1
    assert row.last_error_stage == "enrich"
    assert row.last_error == "boom"


@pytest.mark.asyncio
async def test_enrich_service_skips_notebooks_outside_folio_sync_allowlist(
    tmp_path: Path,
) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["folio_sync_notebooks"] = ["LFW", "Synthera", "Navicyte"]
    store = PageStateStore(Path(config["state_db_path"]))
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-1", "raw inbox page", "glm-4.5v")

    mock_enricher = AsyncMock()
    mock_folio = AsyncMock()
    service = EnrichService(
        config=config,
        page_state=store,
        enricher=mock_enricher,
        folio_upserter=mock_folio,
    )

    wrote = await service.run_once()

    row = store.get_page("Quick", 19)
    assert wrote is False
    assert row.dirty_for_enrichment is False
    assert row.last_enriched_revision == "rev-1"
    mock_enricher.assert_not_awaited()
    mock_folio.assert_not_awaited()


def test_ingest_service_passes_configured_zai_api_key_to_reader(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["zai_api_key"] = "config-token"

    with patch("paia_supernote.ingest_service.SupernoteReader") as mock_reader_cls:
        service = IngestService(config=config)

    mock_reader_cls.assert_called_once()
    assert mock_reader_cls.call_args.kwargs["zai_api_key"] == "config-token"
    assert service.reader is mock_reader_cls.return_value


def test_ingest_service_cloud_poller_watches_only_folio_sync_notebooks(
    tmp_path: Path,
) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["folio_sync_notebooks"] = ["LFW", "Navicyte", "Synth"]

    service = IngestService(
        config=config,
        reader=AsyncMock(),
        uploader=AsyncMock(),
    )

    assert service.cloud_poller.watched_notebooks == {"LFW", "Navicyte", "Synth"}
    assert service.cloud_poller.process_existing_on_start is False


def test_enrich_service_passes_configured_zai_api_key_to_enricher(tmp_path: Path) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["zai_api_key"] = "config-token"

    with patch("paia_supernote.enrich_service.SupernoteEnricher") as mock_enricher_cls:
        service = EnrichService(config=config)

    mock_enricher_cls.assert_called_once()
    assert mock_enricher_cls.call_args.kwargs["zai_api_key"] == "config-token"
    assert service.enricher is mock_enricher_cls.return_value


@pytest.mark.asyncio
async def test_cloud_poller_retries_revision_after_download_error() -> None:
    uploader = AsyncMock()
    uploader._api_call.return_value = {
        "status": 200,
        "body": {
            "userFileVOList": [
                {
                    "fileName": "Quick.note",
                    "updateTime": 123,
                    "isFolder": "N",
                    "size": 42,
                }
            ]
        },
    }
    uploader.download_notebook = AsyncMock(
        side_effect=[RuntimeError("download failed"), b"latest-bytes"]
    )
    on_note_changed = AsyncMock()
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=on_note_changed,
        poll_interval=60,
    )

    await poller._poll_once()

    assert "Quick.note" not in poller._last_seen
    on_note_changed.assert_not_awaited()

    await poller._poll_once()

    assert poller._last_seen["Quick.note"] == 123
    on_note_changed.assert_awaited_once_with("Quick", b"latest-bytes", 123)


@pytest.mark.asyncio
async def test_cloud_poller_watches_navicyte_note() -> None:
    uploader = AsyncMock()
    uploader._api_call.return_value = {
        "status": 200,
        "body": {
            "userFileVOList": [
                {
                    "fileName": "Navicyte.note",
                    "updateTime": 123,
                    "isFolder": "N",
                    "size": 42,
                }
            ]
        },
    }
    uploader.download_notebook = AsyncMock(return_value=b"navicyte-bytes")
    on_note_changed = AsyncMock()
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=on_note_changed,
        poll_interval=60,
    )

    await poller._poll_once()

    assert poller._last_seen["Navicyte.note"] == 123
    uploader.download_notebook.assert_awaited_once_with("Navicyte.note")
    on_note_changed.assert_awaited_once_with("Navicyte", b"navicyte-bytes", 123)


@pytest.mark.asyncio
async def test_cloud_poller_uses_instance_watchlist() -> None:
    uploader = AsyncMock()
    uploader._api_call.return_value = {
        "status": 200,
        "body": {
            "userFileVOList": [
                {
                    "fileName": "Quick.note",
                    "updateTime": 123,
                    "isFolder": "N",
                    "size": 42,
                },
                {
                    "fileName": "Navicyte.note",
                    "updateTime": 124,
                    "isFolder": "N",
                    "size": 42,
                },
            ]
        },
    }
    uploader.download_notebook = AsyncMock(return_value=b"navicyte-bytes")
    on_note_changed = AsyncMock()
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=on_note_changed,
        poll_interval=60,
        watched_notebooks={"Navicyte"},
    )

    await poller._poll_once()

    assert "Quick.note" not in poller._last_seen
    assert poller._last_seen["Navicyte.note"] == 124
    uploader.download_notebook.assert_awaited_once_with("Navicyte.note")
    on_note_changed.assert_awaited_once_with("Navicyte", b"navicyte-bytes", 124)


@pytest.mark.asyncio
async def test_cloud_poller_can_baseline_existing_files_on_first_poll() -> None:
    uploader = AsyncMock()
    uploader._api_call.return_value = {
        "status": 200,
        "body": {
            "userFileVOList": [
                {
                    "fileName": "Navicyte.note",
                    "updateTime": 123,
                    "isFolder": "N",
                    "size": 42,
                }
            ]
        },
    }
    uploader.download_notebook = AsyncMock(return_value=b"navicyte-bytes")
    on_note_changed = AsyncMock()
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=on_note_changed,
        poll_interval=60,
        watched_notebooks={"Navicyte"},
        process_existing_on_start=False,
    )

    await poller._poll_once()

    assert poller._last_seen["Navicyte.note"] == 123
    uploader.download_notebook.assert_not_awaited()
    on_note_changed.assert_not_awaited()

    uploader._api_call.return_value["body"]["userFileVOList"][0]["updateTime"] = 124
    await poller._poll_once()

    uploader.download_notebook.assert_awaited_once_with("Navicyte.note")
    on_note_changed.assert_awaited_once_with("Navicyte", b"navicyte-bytes", 124)


@pytest.mark.asyncio
async def test_ingest_service_start_raises_when_cloud_poller_task_dies(
    tmp_path: Path,
) -> None:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")

    class CrashingPoller:
        def __init__(self) -> None:
            self.stop = AsyncMock()
            self._task: asyncio.Task[None] | None = None

        def start(self) -> None:
            async def crash() -> None:
                await asyncio.sleep(0)
                raise RuntimeError("poller died")

            self._task = asyncio.create_task(crash())

        async def wait(self) -> None:
            assert self._task is not None
            await self._task

    uploader = AsyncMock()
    poller = CrashingPoller()
    service = IngestService(
        config=config,
        reader=AsyncMock(),
        uploader=uploader,
        cloud_poller=poller,
    )

    with pytest.raises(RuntimeError, match="poller died"):
        await asyncio.wait_for(service.start(), timeout=0.2)
