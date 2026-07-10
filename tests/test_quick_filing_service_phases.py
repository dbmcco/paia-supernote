"""Tests for the composable phase methods (detect_and_record, apply_moves) and the
verify_before_upload safety hook extracted from QuickFilingService.run_once.

These exercise the new public surface that the safe CLI move pipeline drives, while
the existing test_quick_filing_service.py continues to guard that run_once behavior
is preserved after the refactor.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from paia_supernote.quick_filing import FilingCandidate
from paia_supernote.quick_filing_service import QuickFilingService


def _ready_candidate() -> FilingCandidate:
    return FilingCandidate(
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


def _install_ready_candidates(service: QuickFilingService) -> None:
    async def ready_candidates(_bytes: bytes) -> list[FilingCandidate]:
        return [_ready_candidate()]

    service._detect_candidates = ready_candidates


@pytest.mark.asyncio
async def test_detect_and_record_returns_pairs_and_persists_detected(
    tmp_path: Path,
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"source"
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=True,
    )
    _install_ready_candidates(service)

    pairs = await service.detect_and_record(b"source")

    assert len(pairs) == 1
    candidate, operation = pairs[0]
    assert candidate.target_notebook == "Test Note 2"
    assert operation.status == "detected"
    assert operation.source_pages == [0]
    # persisted to the ledger and retrievable by id
    assert service.ledger.get(operation.operation_id).status == "detected"


@pytest.mark.asyncio
async def test_apply_moves_executes_operational_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    uploader.upload_notebook.return_value = True
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
    )
    _install_ready_candidates(service)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, target, source_pages: b"updated " + target,
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda _source, pages: b"source without page",
    )

    pairs = await service.detect_and_record(b"source")
    completed = await service.apply_moves(pairs, b"source")

    assert completed == 1
    assert uploader.upload_notebook.await_count == 2  # target, then source
    op_id = pairs[0][1].operation_id
    assert service.ledger.get(op_id).status == "completed"


@pytest.mark.asyncio
async def test_verify_before_upload_called_for_target_then_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    uploader.upload_notebook.return_value = True
    verified: list[str] = []

    def verify(name: str, data: bytes) -> None:
        verified.append(name)

    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
        verify_before_upload=verify,
    )
    _install_ready_candidates(service)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, target, source_pages: b"updated " + target,
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda _source, pages: b"source without page",
    )

    pairs = await service.detect_and_record(b"source")
    await service.apply_moves(pairs, b"source")

    assert "Test Note 2.note" in verified
    assert "Test Note 1.note" in verified
    assert verified.index("Test Note 2.note") < verified.index("Test Note 1.note")


@pytest.mark.asyncio
async def test_verify_failure_on_target_aborts_before_any_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    uploader.upload_notebook.return_value = True

    def verify(name: str, data: bytes) -> None:
        raise ValueError("corrupted target")

    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
        verify_before_upload=verify,
    )
    _install_ready_candidates(service)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, target, source_pages: b"updated " + target,
    )

    pairs = await service.detect_and_record(b"source")
    with pytest.raises(ValueError, match="corrupted target"):
        await service.apply_moves(pairs, b"source")

    assert uploader.upload_notebook.await_count == 0
    op_id = pairs[0][1].operation_id
    assert service.ledger.get(op_id).status == "detected"


@pytest.mark.asyncio
async def test_verify_failure_on_source_keeps_target_written_source_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    uploader.upload_notebook.return_value = True

    def verify(name: str, data: bytes) -> None:
        if name == "Test Note 1.note":
            raise ValueError("corrupted source")

    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=False,
        verify_before_upload=verify,
    )
    _install_ready_candidates(service)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda _source, target, source_pages: b"updated " + target,
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda _source, pages: b"source without page",
    )

    pairs = await service.detect_and_record(b"source")
    with pytest.raises(ValueError, match="corrupted source"):
        await service.apply_moves(pairs, b"source")

    assert uploader.upload_notebook.await_count == 1  # target only, source aborted
    op_id = pairs[0][1].operation_id
    assert service.ledger.get(op_id).status == "target_written_source_pending"
