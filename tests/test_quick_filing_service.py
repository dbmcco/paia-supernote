from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from paia_supernote.quick_filing import FilingCandidate
from paia_supernote.quick_filing_service import QuickFilingService


@pytest.mark.asyncio
async def test_service_dry_run_does_not_upload(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=True,
    )

    async def no_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return []

    service._detect_candidates = no_candidates

    result = await service.run_once()

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    uploader.download_notebook.assert_awaited_once_with("Test Note 1.note")
    uploader.upload_notebook.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_refuses_source_outside_configured_scope(tmp_path: Path) -> None:
    service = QuickFilingService(
        uploader=AsyncMock(),
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Quick",
        destination_map={"lfw": "LFW"},
        dry_run=True,
        allowed_source_notebooks={"Test Note 1"},
    )

    with pytest.raises(ValueError, match="source notebook is not configured"):
        await service.run_once()


@pytest.mark.asyncio
async def test_service_records_ready_candidates_in_dry_run(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=True,
    )

    async def ready_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return [
            FilingCandidate(
                status="ready",
                source_notebook="Test Note 1",
                source_pages=[0],
                source_revision="rev-1",
                detected_header="2026-04-29 #test",
                detected_tags=["test"],
                target_notebook="Test Note 2",
                bundle_key=None,
                title="Pilot",
                reason="matched #test",
                confidence=1.0,
            )
        ]

    service._detect_candidates = ready_candidates

    result = await service.run_once()

    assert result["candidate_count"] == 1
    assert result["operations"][0]["status"] == "detected"


@pytest.mark.asyncio
async def test_service_marks_source_cleanup_pending_after_target_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    uploader.upload_notebook.side_effect = [True, RuntimeError("source upload failed")]
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
    )

    async def ready_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return [
            FilingCandidate(
                status="ready",
                source_notebook="Test Note 1",
                source_pages=[0],
                source_revision="rev-1",
                detected_header="2026-04-29 #test",
                detected_tags=["test"],
                target_notebook="Test Note 2",
                bundle_key=None,
                title="Pilot",
                reason="matched #test",
                confidence=1.0,
            )
        ]

    service._detect_candidates = ready_candidates
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, _target, source_pages: b"updated target",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda _source, pages: b"updated source",
    )

    with pytest.raises(RuntimeError, match="source upload failed"):
        await service.run_once()

    operation = service.ledger.upsert_detected(
        source_notebook="Test Note 1",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29 #test",
        detected_tags=["test"],
        bundle_key=None,
        target_notebook="Test Note 2",
        routing_reason="matched #test",
        confidence=1.0,
    )
    assert operation.status == "target_written_source_pending"


@pytest.mark.asyncio
async def test_service_retry_after_target_written_does_not_upload_target_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    uploader.upload_notebook.return_value = True
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
    )
    operation = service.ledger.upsert_detected(
        source_notebook="Test Note 1",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29 #test",
        detected_tags=["test"],
        bundle_key=None,
        target_notebook="Test Note 2",
        routing_reason="matched #test",
        confidence=1.0,
    )
    service.ledger.mark_target_written(
        operation.operation_id,
        target_revision_after="uploaded",
    )

    async def ready_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return [
            FilingCandidate(
                status="ready",
                source_notebook="Test Note 1",
                source_pages=[0],
                source_revision="rev-1",
                detected_header="2026-04-29 #test",
                detected_tags=["test"],
                target_notebook="Test Note 2",
                bundle_key=None,
                title="Pilot",
                reason="matched #test",
                confidence=1.0,
            )
        ]

    service._detect_candidates = ready_candidates
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, _target, source_pages: b"updated target",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda _source, pages: b"updated source",
    )

    await service.run_once()

    uploader.download_notebook.assert_awaited_once_with("Test Note 1.note")
    uploader.upload_notebook.assert_awaited_once()
    assert service.ledger.get(operation.operation_id).status == "completed"


@pytest.mark.asyncio
async def test_service_removes_multiple_moved_pages_in_one_source_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"synth target", b"lfw target"]
    uploader.upload_notebook.return_value = True
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Mgmt",
        destination_map={"synth": "Synth", "lfw": "LFW"},
        dry_run=False,
    )

    async def ready_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return [
            FilingCandidate(
                status="ready",
                source_notebook="Mgmt",
                source_pages=[0],
                source_revision="rev-1",
                detected_header="Synth Orbit",
                detected_tags=[],
                target_notebook="Synth",
                bundle_key=None,
                title=None,
                reason="matched destination Synth",
                confidence=1.0,
            ),
            FilingCandidate(
                status="ready",
                source_notebook="Mgmt",
                source_pages=[1],
                source_revision="rev-1",
                detected_header="LFW Orbit",
                detected_tags=[],
                target_notebook="LFW",
                bundle_key=None,
                title=None,
                reason="matched destination LFW",
                confidence=1.0,
            ),
        ]

    removed_pages: list[list[int]] = []
    service._detect_candidates = ready_candidates
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, target, source_pages: b"updated " + target,
    )

    def fake_remove_pages(_source: bytes, *, pages: list[int]) -> bytes:
        removed_pages.append(pages)
        return b"source without moved pages"

    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        fake_remove_pages,
    )

    result = await service.run_once(source_bytes=b"source")

    assert result["completed_count"] == 2
    assert removed_pages == [[0, 1]]
    assert uploader.upload_notebook.await_count == 3


@pytest.mark.asyncio
async def test_service_detects_starred_pages_from_reader_results(
    tmp_path: Path,
) -> None:
    uploader = AsyncMock()
    reader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    reader.read_pages.return_value = [
        type(
            "ReadResult",
            (),
            {
                "page_num": 0,
                "text": "2026-04-29 #meeting\nTest Note 2\nPilot page",
            },
        )()
    ]
    reader.resolve_filing_destination.return_value = {
        "action": "move",
        "target_notebook": "Test Note 2",
        "evidence": "The target note name is written beside the star.",
        "confidence": 1.0,
    }
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test-note-2": "Test Note 2"},
        dry_run=True,
        reader=reader,
    )
    service._starred_pages_from_note = lambda _bytes: {0}

    result = await service.run_once()

    assert result["candidate_count"] == 1
    assert result["operations"][0]["target_notebook"] == "Test Note 2"
    reader.read_pages.assert_awaited_once_with(
        b"source",
        "Test Note 1",
        pages=[0],
    )
    reader.resolve_filing_destination.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_lets_model_choose_destination_from_page_context(
    tmp_path: Path,
) -> None:
    uploader = AsyncMock()
    reader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    reader.read_pages.return_value = [
        type(
            "ReadResult",
            (),
            {
                "page_num": 3,
                "text": "OCR did not clearly capture the destination.",
                "page_image": object(),
            },
        )()
    ]
    reader.resolve_filing_destination.return_value = {
        "action": "move",
        "target_notebook": "Mgmt",
        "evidence": "Mgmt is written beside the native star marker.",
        "confidence": 0.86,
    }
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Quick",
        destination_map={"mgmt": "Mgmt", "lfw": "LFW"},
        dry_run=True,
        reader=reader,
    )
    service._starred_pages_from_note = lambda _bytes: {3}

    result = await service.run_once()

    assert result["operations"][0]["target_notebook"] == "Mgmt"
    reader.resolve_filing_destination.assert_awaited_once_with(
        page_image=reader.read_pages.return_value[0].page_image,
        transcription="OCR did not clearly capture the destination.",
        source_notebook="Quick",
        destination_notebooks=["Mgmt", "LFW"],
    )
