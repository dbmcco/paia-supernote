from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import supernotelib.parser as sn_parser

from paia_supernote.filing_ledger import FilingLedger, FilingOperation
from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages
from paia_supernote.quick_filing import FilingCandidate, StarDetector, route_page
from paia_supernote.reader import SupernoteReader


class QuickFilingService:
    def __init__(
        self,
        *,
        uploader: Any,
        ledger_db_path: Path,
        source_notebook: str,
        destination_map: dict[str, str],
        dry_run: bool = True,
        reader: Any | None = None,
        star_detector: StarDetector | None = None,
    ) -> None:
        self.uploader = uploader
        self.ledger = FilingLedger(ledger_db_path)
        self.source_notebook = source_notebook
        self.destination_map = destination_map
        self.dry_run = dry_run
        self.reader = reader or SupernoteReader()
        self.star_detector = star_detector or StarDetector()

    def _validate_pilot_scope(self) -> None:
        allowed = {"Test Note 1", "Test Note 2"}
        names = {self.source_notebook, *self.destination_map.values()}
        if any(name not in allowed for name in names):
            raise ValueError("pilot only supports test notebooks")

    async def _detect_candidates(self, source_bytes: bytes) -> list[FilingCandidate]:
        starred_pages = self._starred_pages_from_note(source_bytes)
        if not starred_pages:
            return []
        read_results = await self.reader.read_all_pages(
            source_bytes,
            self.source_notebook,
            page_range=(min(starred_pages), max(starred_pages)),
        )
        candidates: list[FilingCandidate] = []
        for result in read_results:
            page_num = int(result.page_num)
            if page_num not in starred_pages:
                continue
            candidates.append(
                route_page(
                    notebook=self.source_notebook,
                    page=page_num,
                    source_revision=_source_revision(source_bytes, page_num),
                    text=str(result.text),
                    starred=True,
                    destination_map=self.destination_map,
                )
            )
        return candidates

    def _starred_pages_from_note(self, source_bytes: bytes) -> set[int]:
        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(source_bytes)
            notebook = sn_parser.load_notebook(path)
            metadata = {
                "page_metadata": [
                    notebook.get_page(index).metadata
                    for index in range(notebook.get_total_pages())
                ]
            }
        finally:
            if os.path.exists(path):
                os.unlink(path)
        return self.star_detector.starred_pages_from_metadata(metadata)

    async def run_once(self) -> dict[str, Any]:
        self._validate_pilot_scope()
        self.ledger.init_schema()
        source_bytes = await self.uploader.download_notebook(f"{self.source_notebook}.note")
        candidates = await self._detect_candidates(source_bytes)
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
        if operation.status not in {
            "target_written",
            "target_written_source_pending",
            "source_removed",
        }:
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

        if operation.status == "source_removed":
            self.ledger.mark_completed(operation.operation_id)
            return

        try:
            updated_source = remove_pages(source_bytes, pages=candidate.source_pages)
        except Exception as exc:
            self.ledger.mark_target_written_source_pending(
                operation.operation_id,
                target_revision_after=operation.target_revision_after or "uploaded",
                error=str(exc),
            )
            raise
        try:
            await _upload_bytes(
                self.uploader, updated_source, f"{candidate.source_notebook}.note"
            )
        except Exception as exc:
            self.ledger.mark_target_written_source_pending(
                operation.operation_id,
                target_revision_after=operation.target_revision_after or "uploaded",
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


def _source_revision(source_bytes: bytes, page_num: int) -> str:
    import hashlib

    digest = hashlib.sha256(source_bytes).hexdigest()
    return f"{digest}:{page_num}"
