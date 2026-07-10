from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable

import supernotelib.parser as sn_parser

from paia_supernote.filing_ledger import FilingLedger, FilingOperation
from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages
from paia_supernote.quick_filing import (
    FilingCandidate,
    FilingDestinationDecision,
    StarDetector,
    route_page_from_decision,
)
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
        allowed_source_notebooks: set[str] | None = None,
        reader: Any | None = None,
        star_detector: StarDetector | None = None,
        verify_before_upload: Callable[[str, bytes], Any] | None = None,
    ) -> None:
        self.uploader = uploader
        self.ledger = FilingLedger(ledger_db_path)
        self.source_notebook = source_notebook
        self.destination_map = destination_map
        self.dry_run = dry_run
        self.allowed_source_notebooks = allowed_source_notebooks
        self.reader = reader or SupernoteReader()
        self.star_detector = star_detector or StarDetector()
        self.verify_before_upload = verify_before_upload

    def _validate_scope(self) -> None:
        if self.allowed_source_notebooks is None:
            return
        if self.source_notebook not in self.allowed_source_notebooks:
            raise ValueError(
                f"source notebook is not configured for filing: {self.source_notebook}"
            )

    async def _detect_candidates(self, source_bytes: bytes) -> list[FilingCandidate]:
        starred_pages = self._starred_pages_from_note(source_bytes)
        if not starred_pages:
            return []
        read_results = await self.reader.read_pages(
            source_bytes,
            self.source_notebook,
            pages=sorted(starred_pages),
        )
        candidates: list[FilingCandidate] = []
        for result in read_results:
            page_num = int(result.page_num)
            if page_num not in starred_pages:
                continue
            decision = await self.reader.resolve_filing_destination(
                page_image=getattr(result, "page_image", None),
                transcription=str(result.text),
                source_notebook=self.source_notebook,
                destination_notebooks=list(
                    dict.fromkeys(self.destination_map.values())
                ),
            )
            candidates.append(
                route_page_from_decision(
                    notebook=self.source_notebook,
                    page=page_num,
                    source_revision=_source_revision(source_bytes, page_num),
                    text=str(result.text),
                    starred=True,
                    decision=FilingDestinationDecision(
                        action=str(decision.get("action") or "needs_review"),
                        target_notebook=decision.get("target_notebook"),
                        evidence=str(
                            decision.get("evidence") or "No decision evidence."
                        ),
                        confidence=float(decision.get("confidence") or 0.0),
                        raw_response=str(decision.get("raw_response") or decision),
                    ),
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

    async def detect_and_record(
        self, source_bytes: bytes
    ) -> list[tuple[FilingCandidate, FilingOperation]]:
        """Detect filing candidates and persist them to the ledger as 'detected'.

        Read + model path only; performs no cloud writes. Returns
        (candidate, operation) pairs the caller can hand to apply_moves.
        """
        self.ledger.init_schema()
        candidates = await self._detect_candidates(source_bytes)
        return [
            (candidate, self._record_candidate(candidate)) for candidate in candidates
        ]

    async def apply_moves(
        self,
        pairs: list[tuple[FilingCandidate, FilingOperation]],
        source_bytes: bytes,
    ) -> int:
        """Execute the operational move for ready pairs: write targets first, then
        remove the moved pages from the source in a single upload, advancing the
        ledger at each boundary. When ``verify_before_upload`` is set it is invoked
        with ``(notebook_name, bytes)`` immediately before every upload; raising
        from it aborts that upload and leaves the ledger in its honest partial
        state so the next run resumes rather than double-applying.

        Returns the number of completed operations.
        """
        completed = 0
        ready_pairs = [
            (candidate, operation)
            for candidate, operation in pairs
            if candidate.status == "ready" and candidate.target_notebook is not None
        ]
        source_cleanup_pairs: list[tuple[FilingCandidate, FilingOperation]] = []
        for candidate, operation in ready_pairs:
            if operation.status == "completed":
                completed += 1
                continue
            if operation.status == "source_removed":
                self.ledger.mark_completed(operation.operation_id)
                completed += 1
                continue
            await self._write_target_if_needed(
                candidate=candidate,
                operation=operation,
                source_bytes=source_bytes,
            )
            source_cleanup_pairs.append(
                (candidate, self.ledger.get(operation.operation_id))
            )

        if source_cleanup_pairs:
            moved_pages = sorted(
                {
                    page
                    for candidate, _operation in source_cleanup_pairs
                    for page in candidate.source_pages
                }
            )
            source_name = f"{self.source_notebook}.note"
            try:
                updated_source = remove_pages(source_bytes, pages=moved_pages)
                if self.verify_before_upload:
                    self.verify_before_upload(source_name, updated_source)
                await _upload_bytes(self.uploader, updated_source, source_name)
            except Exception as exc:
                for _candidate, operation in source_cleanup_pairs:
                    self.ledger.mark_target_written_source_pending(
                        operation.operation_id,
                        target_revision_after=(
                            operation.target_revision_after or "uploaded"
                        ),
                        error=str(exc),
                    )
                raise
            for _candidate, operation in source_cleanup_pairs:
                self.ledger.mark_source_removed(
                    operation.operation_id,
                    quick_revision_after="uploaded",
                )
                self.ledger.mark_completed(operation.operation_id)
                completed += 1
        return completed

    async def run_once(self, *, source_bytes: bytes | None = None) -> dict[str, Any]:
        self._validate_scope()
        self.ledger.init_schema()
        if source_bytes is None:
            source_bytes = await self.uploader.download_notebook(
                f"{self.source_notebook}.note"
            )
        pairs = await self.detect_and_record(source_bytes)
        candidates = [candidate for candidate, _operation in pairs]
        operations = [operation for _candidate, operation in pairs]
        if self.dry_run:
            return _result(dry_run=True, candidates=candidates, operations=operations)
        completed = await self.apply_moves(pairs, source_bytes)
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

    async def _write_target_if_needed(
        self,
        *,
        candidate: FilingCandidate,
        operation: FilingOperation,
        source_bytes: bytes,
    ) -> None:
        if candidate.target_notebook is None:
            return
        if operation.status in {
            "target_written",
            "target_written_source_pending",
            "source_removed",
            "completed",
        }:
            return

        target_name = f"{candidate.target_notebook}.note"
        target_bytes = await self.uploader.download_notebook(target_name)
        updated_target = copy_pages_to_end(
            source_bytes,
            target_bytes,
            source_pages=candidate.source_pages,
        )
        if self.verify_before_upload:
            self.verify_before_upload(target_name, updated_target)
        await _upload_bytes(self.uploader, updated_target, target_name)
        self.ledger.mark_target_written(
            operation.operation_id,
            target_revision_after="uploaded",
        )


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
