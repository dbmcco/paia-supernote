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
async def test_service_refuses_non_test_source(tmp_path: Path) -> None:
    service = QuickFilingService(
        uploader=AsyncMock(),
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Quick",
        destination_map={"lfw": "LFW"},
        dry_run=True,
    )

    with pytest.raises(ValueError, match="pilot only supports test notebooks"):
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
async def test_service_detects_starred_pages_from_reader_results(tmp_path: Path) -> None:
    uploader = AsyncMock()
    reader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    reader.read_all_pages.return_value = [
        type(
            "ReadResult",
            (),
            {
                "page_num": 0,
                "text": "2026-04-29 #test #meeting\nPilot page",
            },
        )()
    ]
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=True,
        reader=reader,
    )
    service._starred_pages_from_note = lambda _bytes: {0}

    result = await service.run_once()

    assert result["candidate_count"] == 1
    assert result["operations"][0]["target_notebook"] == "Test Note 2"
