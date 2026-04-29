from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from paia_supernote.filing_ledger import FilingLedger, FilingOperation
from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages
from paia_supernote.quick_filing import FilingCandidate


class QuickFilingService:
    def __init__(
        self,
        *,
        uploader: Any,
        ledger_db_path: Path,
        source_notebook: str,
        destination_map: dict[str, str],
        dry_run: bool = True,
    ) -> None:
        self.uploader = uploader
        self.ledger = FilingLedger(ledger_db_path)
        self.source_notebook = source_notebook
        self.destination_map = destination_map
        self.dry_run = dry_run

    def _validate_pilot_scope(self) -> None:
        allowed = {"Test Note 1", "Test Note 2"}
        names = {self.source_notebook, *self.destination_map.values()}
        if any(name not in allowed for name in names):
            raise ValueError("pilot only supports test notebooks")

    def _detect_candidates(self, source_bytes: bytes) -> list[FilingCandidate]:
        return []

    async def run_once(self) -> dict[str, Any]:
        self._validate_pilot_scope()
        self.ledger.init_schema()
        source_bytes = await self.uploader.download_notebook(f"{self.source_notebook}.note")
        candidates = self._detect_candidates(source_bytes)
        operations = [self._record_candidate(candidate) for candidate in candidates]
        if self.dry_run:
            return _result(dry_run=True, candidates=candidates, operations=operations)

        completed = 0
        for candidate, operation in zip(candidates, operations, strict=True):
            if candidate.status != "ready" or candidate.target_notebook is None:
                continue
            await self._move_candidate(
                candidate=candidate,
                operation=operation,
                source_bytes=source_bytes,
            )
            completed += 1
        return {
            **_result(dry_run=False, candidates=candidates, operations=operations),
            "completed_count": completed,
        }

    def _record_candidate(self, candidate: FilingCandidate) -> FilingOperation:
        return self.ledger.upsert_detected(
            source_notebook=candidate.source_notebook,
            source_pages=candidate.source_pages,
            source_revision=candidate.source_revision,
            detected_header=candidate.detected_header,
            detected_tags=candidate.detected_tags,
            bundle_key=candidate.bundle_key,
            target_notebook=candidate.target_notebook,
            routing_reason=candidate.reason,
            confidence=candidate.confidence,
        )

    async def _move_candidate(
        self,
        *,
        candidate: FilingCandidate,
        operation: FilingOperation,
        source_bytes: bytes,
    ) -> None:
        if candidate.target_notebook is None:
            return
        target_name = f"{candidate.target_notebook}.note"
        target_bytes = await self.uploader.download_notebook(target_name)
        updated_target = copy_pages_to_end(
            source_bytes,
            target_bytes,
            source_pages=candidate.source_pages,
        )
        await _upload_bytes(self.uploader, updated_target, target_name)
        self.ledger.mark_target_written(
            operation.operation_id,
            target_revision_after="uploaded",
        )

        updated_source = remove_pages(source_bytes, pages=candidate.source_pages)
        try:
            await _upload_bytes(
                self.uploader, updated_source, f"{candidate.source_notebook}.note"
            )
        except Exception as exc:
            self.ledger.mark_target_written_source_pending(
                operation.operation_id,
                target_revision_after="uploaded",
                error=str(exc),
            )
            raise
        self.ledger.mark_source_removed(
            operation.operation_id,
            quick_revision_after="uploaded",
        )
        self.ledger.mark_completed(operation.operation_id)


async def _upload_bytes(uploader: Any, notebook_bytes: bytes, target_name: str) -> None:
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(notebook_bytes)
        ok = await uploader.upload_notebook(path, target_name)
        if not ok:
            raise RuntimeError(f"upload failed for {target_name}")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _result(
    *,
    dry_run: bool,
    candidates: list[FilingCandidate],
    operations: list[FilingOperation],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "operations": [
            {
                "operation_id": operation.operation_id,
                "status": operation.status,
                "source_pages": operation.source_pages,
                "target_notebook": operation.target_notebook,
            }
            for operation in operations
        ],
    }
