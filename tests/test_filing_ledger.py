from pathlib import Path

from paia_supernote.filing_ledger import FilingLedger


def test_ledger_creates_idempotent_operation(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()

    first = ledger.upsert_detected(
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
    second = ledger.upsert_detected(
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

    assert second.operation_id == first.operation_id
    assert second.status == "detected"


def test_ledger_records_partial_success(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()
    op = ledger.upsert_detected(
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

    ledger.mark_target_written(op.operation_id, target_revision_after="target-rev-2")
    updated = ledger.get(op.operation_id)

    assert updated.status == "target_written"
    assert updated.target_revision_after == "target-rev-2"


def test_ledger_records_completion_after_source_removed(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()
    op = ledger.upsert_detected(
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

    ledger.mark_target_written(op.operation_id, target_revision_after="target-rev-2")
    ledger.mark_source_removed(op.operation_id, quick_revision_after="quick-rev-2")
    ledger.mark_completed(op.operation_id)
    updated = ledger.get(op.operation_id)

    assert updated.status == "completed"
    assert updated.quick_revision_after == "quick-rev-2"
    assert updated.completed_at is not None


def test_ledger_marks_failed_with_error(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()
    op = ledger.upsert_detected(
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

    ledger.mark_failed(op.operation_id, error="upload failed")
    updated = ledger.get(op.operation_id)

    assert updated.status == "failed"
    assert updated.error == "upload failed"
