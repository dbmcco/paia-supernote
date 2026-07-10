"""Tests for the safe move pipeline: planning (read-only ledger annotation),
the explicit-page plan, and the verify-before-upload parse check.

Execution (backup / apply / re-verify) is covered in a second cycle below.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from paia_supernote import move_pipeline
from paia_supernote.filing_ledger import FilingLedger
from paia_supernote.move_pipeline import (
    MovePlan,
    execute_move_plan,
    plan_explicit_move,
    plan_starred_moves,
    verify_notebook_bytes,
)
from paia_supernote.quick_filing import FilingCandidate
from paia_supernote.quick_filing_service import QuickFilingService


def _candidate(
    page: int,
    target: str | None,
    *,
    revision: str = "rev-1",
    status: str = "ready",
) -> FilingCandidate:
    return FilingCandidate(
        status=status,
        source_notebook="Quick",
        source_pages=[page],
        source_revision=revision,
        detected_header="2026-04-29",
        detected_tags=[],
        target_notebook=target,
        bundle_key=None,
        title="Pilot",
        reason="model selected destination",
        confidence=0.9,
    )


def _service(tmp_path: Path, candidates: list[FilingCandidate]) -> QuickFilingService:
    service = QuickFilingService(
        uploader=AsyncMock(),
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Quick",
        destination_map={"mgmt": "Mgmt", "lfw": "LFW"},
        dry_run=True,
    )

    async def detect(_bytes: bytes) -> list[FilingCandidate]:
        return list(candidates)

    service._detect_candidates = detect  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_plan_marks_unfiled_page_as_would_move(tmp_path: Path) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    plan = await plan_starred_moves(service, b"source-bytes")

    assert isinstance(plan, MovePlan)
    assert plan.source_notebook == "Quick"
    assert len(plan.annotations) == 1
    move = plan.annotations[0]
    assert move.page == 0
    assert move.target_notebook == "Mgmt"
    assert move.ledger_status == "would_move"
    assert plan.affected_targets == ["Mgmt"]


@pytest.mark.asyncio
async def test_plan_marks_already_completed_page_as_already_moved(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    # Seed the ledger with a completed operation for the identical move.
    seeded = service.ledger.upsert_detected(
        source_notebook="Quick",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29",
        detected_tags=[],
        bundle_key=None,
        target_notebook="Mgmt",
        routing_reason="model selected destination",
        confidence=0.9,
    )
    service.ledger.mark_completed(seeded.operation_id)

    plan = await plan_starred_moves(service, b"source-bytes")

    assert plan.annotations[0].ledger_status == "already_moved"
    assert plan.annotations[0].operation_id == seeded.operation_id
    assert plan.affected_targets == []  # nothing left to move


@pytest.mark.asyncio
async def test_plan_to_override_forces_target_and_keys_idempotency_against_it(
    tmp_path: Path,
) -> None:
    # Model said LFW, but --to overrides everything to Mgmt.
    service = _service(tmp_path, [_candidate(0, "LFW")])
    plan = await plan_starred_moves(service, b"source-bytes", to_override="Mgmt")

    assert plan.annotations[0].target_notebook == "Mgmt"
    assert plan.annotations[0].ledger_status == "would_move"
    # Idempotency key must reflect the override target, not the model's guess.
    expected_id = FilingLedger.operation_id_for(
        source_notebook="Quick",
        source_pages=[0],
        source_revision="rev-1",
        target_notebook="Mgmt",
    )
    assert plan.annotations[0].operation_id == expected_id


@pytest.mark.asyncio
async def test_plan_needs_review_when_model_gave_no_target(tmp_path: Path) -> None:
    service = _service(tmp_path, [_candidate(3, None, status="needs_review")])
    plan = await plan_starred_moves(service, b"source-bytes")

    assert plan.annotations[0].target_notebook is None
    assert plan.annotations[0].ledger_status == "would_move"
    assert plan.affected_targets == []


@pytest.mark.asyncio
async def test_plan_explicit_move_builds_ready_candidate_without_model_call(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, [])
    plan = plan_explicit_move(
        service,
        source_bytes=b"source-bytes",
        pages=[2, 3],
        target="Mgmt",
    )

    assert len(plan.annotations) == 1
    move = plan.annotations[0]
    assert move.page == 2  # representative page
    assert move.target_notebook == "Mgmt"
    assert move.ledger_status == "would_move"
    assert plan.affected_targets == ["Mgmt"]
    # The carried candidate covers both pages and is ready to execute.
    assert plan.candidates[0].source_pages == [2, 3]
    assert plan.candidates[0].status == "ready"


def test_verify_returns_page_count_for_loadable_notebook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(get_total_pages=lambda: 5)
    monkeypatch.setattr(move_pipeline, "_load_notebook", lambda raw: fake)

    assert verify_notebook_bytes("Mgmt.note", b"ok") == 5


def test_verify_raises_for_unparseable_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_raw: bytes):
        raise ValueError("not a notebook")

    monkeypatch.setattr(move_pipeline, "_load_notebook", boom)

    with pytest.raises(ValueError, match="not a notebook"):
        verify_notebook_bytes("Mgmt.note", b"garbage")


def test_verify_raises_for_empty_notebook(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(get_total_pages=lambda: 0)
    monkeypatch.setattr(move_pipeline, "_load_notebook", lambda raw: fake)

    with pytest.raises(ValueError, match="no pages"):
        verify_notebook_bytes("Mgmt.note", b"empty")


@pytest.mark.asyncio
async def test_execute_dry_run_writes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    plan = await plan_starred_moves(service, b"src-bytes")

    result = await execute_move_plan(
        service,
        plan,
        source_bytes=b"src-bytes",
        backups_root=tmp_path / "bk",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.backup_dir is None
    assert result.completed_pages == []
    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_backs_up_source_and_targets_before_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    plan = await plan_starred_moves(service, b"src-bytes")

    async def fake_download(name: str) -> bytes:
        return {"Quick.note": b"src-cloud", "Mgmt.note": b"tgt-cloud"}[name]

    service.uploader.download_notebook = fake_download  # type: ignore[method-assign]
    service.uploader.upload_notebook = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda s, t, source_pages: b"tgt-mut",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages", lambda s, pages: b"src-mut"
    )
    monkeypatch.setattr(move_pipeline, "verify_notebook_bytes", lambda name, raw: 3)

    result = await execute_move_plan(
        service, plan, source_bytes=b"src-bytes", backups_root=tmp_path / "bk"
    )

    assert result.backup_dir is not None
    assert (result.backup_dir / "Quick.note").read_bytes() == b"src-bytes"
    assert (result.backup_dir / "Mgmt.note").read_bytes() == b"tgt-cloud"
    assert service.uploader.upload_notebook.await_count == 2  # target, then source
    assert {o.notebook for o in result.outcomes} == {"Quick", "Mgmt"}
    assert result.completed_pages == [0]


@pytest.mark.asyncio
async def test_execute_verify_failure_aborts_upload_but_backup_already_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    plan = await plan_starred_moves(service, b"src-bytes")

    async def fake_download(name: str) -> bytes:
        return {"Quick.note": b"src-cloud", "Mgmt.note": b"tgt-cloud"}[name]

    service.uploader.download_notebook = fake_download  # type: ignore[method-assign]
    service.uploader.upload_notebook = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda s, t, source_pages: b"tgt-MUTATED",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages",
        lambda s, pages: b"src-MUTATED",
    )

    def fake_verify(name: str, raw: bytes) -> int:
        if b"MUTATED" in raw:
            raise ValueError("parse failed for mutated bytes")
        return 3

    monkeypatch.setattr(move_pipeline, "verify_notebook_bytes", fake_verify)

    with pytest.raises(ValueError, match="parse failed"):
        await execute_move_plan(
            service, plan, source_bytes=b"src-bytes", backups_root=tmp_path / "bk"
        )

    quick_backups = list((tmp_path / "bk").glob("*/Quick.note"))
    mgmt_backups = list((tmp_path / "bk").glob("*/Mgmt.note"))
    assert quick_backups and mgmt_backups  # backup captured pre-state before apply
    assert quick_backups[0].read_bytes() == b"src-bytes"
    assert (
        service.uploader.upload_notebook.await_count == 0
    )  # verify aborted before upload


@pytest.mark.asyncio
async def test_execute_re_verifies_page_counts_after_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    plan = await plan_starred_moves(service, b"src-bytes")
    state = {"uploaded": 0}

    async def fake_download(name: str) -> bytes:
        if name == "Quick.note":
            return b"src-after" if state["uploaded"] else b"src-cloud"
        return b"tgt-after" if state["uploaded"] else b"tgt-cloud"

    async def fake_upload(path: str, name: str) -> bool:
        state["uploaded"] += 1
        return True

    service.uploader.download_notebook = fake_download  # type: ignore[method-assign]
    service.uploader.upload_notebook = fake_upload  # type: ignore[method-assign]
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda s, t, source_pages: b"tgt-mut",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages", lambda s, pages: b"src-mut"
    )

    def fake_verify(name: str, raw: bytes) -> int:
        if name == "Quick.note":
            return 2 if raw == b"src-after" else 3  # source loses a page
        return 3 if raw == b"tgt-after" else 2  # target gains a page

    monkeypatch.setattr(move_pipeline, "verify_notebook_bytes", fake_verify)

    result = await execute_move_plan(
        service, plan, source_bytes=b"src-bytes", backups_root=tmp_path / "bk"
    )

    by_nb = {o.notebook: o for o in result.outcomes}
    assert by_nb["Quick"].before_pages == 3
    assert by_nb["Quick"].after_pages == 2
    assert by_nb["Mgmt"].before_pages == 2
    assert by_nb["Mgmt"].after_pages == 3


@pytest.mark.asyncio
async def test_execute_skips_when_everything_already_moved(tmp_path: Path) -> None:
    service = _service(tmp_path, [_candidate(0, "Mgmt")])
    seeded = service.ledger.upsert_detected(
        source_notebook="Quick",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29",
        detected_tags=[],
        bundle_key=None,
        target_notebook="Mgmt",
        routing_reason="model selected destination",
        confidence=0.9,
    )
    service.ledger.mark_completed(seeded.operation_id)
    plan = await plan_starred_moves(service, b"src-bytes")

    result = await execute_move_plan(
        service, plan, source_bytes=b"src-bytes", backups_root=tmp_path / "bk"
    )

    assert result.backup_dir is None
    assert result.completed_pages == []
    assert result.skipped_pages == [0]
    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
