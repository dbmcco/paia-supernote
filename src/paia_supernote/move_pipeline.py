"""Safe move pipeline for the ``supernote`` CLI.

This module wraps :class:`~paia_supernote.quick_filing_service.QuickFilingService`
with the safety the service deliberately does not perform: a read-only *plan*
(what would move, what already moved), a timestamped *backup* of every affected
notebook before any cloud write, a *verify-before-upload* parse check, and a
*re-download + verify* pass after the move.

The service owns detection, idempotency, mutate and upload; this module owns the
safety net around it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import supernotelib.parser as sn_parser

from paia_supernote.filing_ledger import FilingLedger
from paia_supernote.quick_filing import FilingCandidate
from paia_supernote.quick_filing_service import QuickFilingService


@dataclass(slots=True)
class PlannedMove:
    page: int
    source_revision: str
    target_notebook: str | None
    confidence: float
    reason: str
    ledger_status: str
    operation_id: str


@dataclass(slots=True)
class MovePlan:
    source_notebook: str
    source_revision: str
    candidates: list[FilingCandidate]
    annotations: list[PlannedMove]
    affected_targets: list[str]

    @property
    def ready_to_move(self) -> list[FilingCandidate]:
        """Candidates the executor would act on: ready, targeted, not yet moved."""
        return [
            candidate
            for candidate, ann in zip(self.candidates, self.annotations, strict=True)
            if candidate.status == "ready"
            and candidate.target_notebook is not None
            and ann.ledger_status != "already_moved"
        ]


def _load_notebook(notebook_bytes: bytes):
    """Parse raw .note bytes into a supernotelib notebook via a temp file."""
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(notebook_bytes)
        return sn_parser.load_notebook(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def verify_notebook_bytes(name: str, notebook_bytes: bytes) -> int:
    """Parse a notebook and return its page count.

    Used as the ``verify_before_upload`` hook: raising here aborts the upload
    before it can damage the cloud copy. Returns the page count on success.
    """
    notebook = _load_notebook(notebook_bytes)
    total = notebook.get_total_pages()
    if total <= 0:
        raise ValueError(f"{name}: parsed notebook has no pages")
    return total


def _ledger_status(ledger: FilingLedger, operation_id: str) -> str:
    try:
        status = ledger.get(operation_id).status
    except KeyError:
        return "would_move"
    return "already_moved" if status == "completed" else status


def _annotate(
    service: QuickFilingService, candidate: FilingCandidate, target: str | None
) -> PlannedMove:
    operation_id = FilingLedger.operation_id_for(
        source_notebook=candidate.source_notebook,
        source_pages=candidate.source_pages,
        source_revision=candidate.source_revision,
        target_notebook=target,
    )
    return PlannedMove(
        page=candidate.source_pages[0] if candidate.source_pages else -1,
        source_revision=candidate.source_revision,
        target_notebook=target,
        confidence=candidate.confidence,
        reason=candidate.reason,
        ledger_status=_ledger_status(service.ledger, operation_id),
        operation_id=operation_id,
    )


async def plan_starred_moves(
    service: QuickFilingService,
    source_bytes: bytes,
    *,
    to_override: str | None = None,
) -> MovePlan:
    """Read-only plan of star-driven moves.

    Runs the service's detection (model-based, no cloud writes) and annotates
    each candidate with its ledger status: ``would_move`` (nothing filed yet),
    ``already_moved`` (completed), or the in-flight partial state to resume.
    ``to_override`` forces every starred page to a single target and keys
    idempotency against that target.
    """
    service.ledger.init_schema()
    candidates = await service._detect_candidates(source_bytes)
    annotations: list[PlannedMove] = []
    affected: set[str] = set()
    for candidate in candidates:
        target = to_override or candidate.target_notebook
        ann = _annotate(service, candidate, target)
        annotations.append(ann)
        if (
            candidate.status == "ready"
            and target
            and ann.ledger_status != "already_moved"
        ):
            affected.add(target)
    return MovePlan(
        source_notebook=service.source_notebook,
        source_revision=candidates[0].source_revision if candidates else "",
        candidates=candidates,
        annotations=annotations,
        affected_targets=sorted(affected),
    )


def plan_explicit_move(
    service: QuickFilingService,
    *,
    source_bytes: bytes,
    pages: list[int],
    target: str,
) -> MovePlan:
    """Read-only plan for an explicit ``move <src> <target> --pages`` request.

    No model call: the user named the target. Idempotency is keyed on a
    notebook-level content revision (SHA-256 of the source bytes).
    """
    service.ledger.init_schema()
    revision = hashlib.sha256(source_bytes).hexdigest()
    candidate = FilingCandidate(
        status="ready",
        source_notebook=service.source_notebook,
        source_pages=list(pages),
        source_revision=revision,
        detected_header="",
        detected_tags=[],
        target_notebook=target,
        bundle_key=None,
        title=None,
        reason="explicit move requested by user",
        confidence=1.0,
    )
    ann = _annotate(service, candidate, target)
    return MovePlan(
        source_notebook=service.source_notebook,
        source_revision=revision,
        candidates=[candidate],
        annotations=[ann],
        affected_targets=[target] if ann.ledger_status != "already_moved" else [],
    )


@dataclass(slots=True)
class NotebookOutcome:
    notebook: str
    before_pages: int
    after_pages: int


@dataclass(slots=True)
class MoveResult:
    plan: MovePlan
    backup_dir: Path | None
    outcomes: list[NotebookOutcome]
    completed_pages: list[int]
    skipped_pages: list[int]
    needs_review_pages: list[int]
    operation_ids: list[str]
    dry_run: bool


async def execute_move_plan(
    service: QuickFilingService,
    plan: MovePlan,
    *,
    source_bytes: bytes,
    backups_root: Path,
    dry_run: bool = False,
    now: datetime | None = None,
) -> MoveResult:
    """Execute a move plan with the full safe pipeline.

    Order of operations: back up every affected notebook and record pre-move page
    counts → wire a verify-before-upload parse check → persist ready candidates as
    'detected' → ``apply_moves`` (targets first, then source removal, ledger at
    each boundary) → re-download every affected notebook and confirm it parses
    with sane page counts. On any failure the exception propagates with backups
    already on disk and the ledger in its honest partial state.
    """
    skipped = [
        ann.page for ann in plan.annotations if ann.ledger_status == "already_moved"
    ]
    review = [ann.page for ann in plan.annotations if ann.target_notebook is None]
    operation_ids = [ann.operation_id for ann in plan.annotations]

    if dry_run or not plan.ready_to_move:
        return MoveResult(
            plan=plan,
            backup_dir=None,
            outcomes=[],
            completed_pages=[],
            skipped_pages=skipped,
            needs_review_pages=review,
            operation_ids=operation_ids,
            dry_run=dry_run,
        )

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(backups_root) / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    affected = sorted({plan.source_notebook, *plan.affected_targets})
    before_pages: dict[str, int] = {}

    # 1. Back up every affected notebook + record its pre-move page count. The
    #    source uses the freshly-downloaded bytes the caller passed in, so the
    #    backup and the mutation input are identical; targets are fetched.
    for notebook in affected:
        name = f"{notebook}.note"
        data = (
            source_bytes
            if notebook == plan.source_notebook
            else (await service.uploader.download_notebook(name))
        )
        (backup_dir / name).write_bytes(data)
        before_pages[notebook] = verify_notebook_bytes(name, data)

    # 2. Wire verify-before-upload so apply_moves parses each notebook right
    #    before its upload and aborts on a bad mutation.
    service.verify_before_upload = lambda name, raw: verify_notebook_bytes(name, raw)

    # 3. Persist ready candidates as 'detected' to obtain operation records.
    pairs: list = []
    for candidate in plan.ready_to_move:
        operation = service.ledger.upsert_detected(
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
        pairs.append((candidate, operation))

    # 4. Apply: write targets, remove source pages, advance the ledger.
    await service.apply_moves(pairs, source_bytes)

    # 5. Re-download every affected notebook and confirm it parses + counts.
    outcomes = []
    for notebook in affected:
        name = f"{notebook}.note"
        data = await service.uploader.download_notebook(name)
        after = verify_notebook_bytes(name, data)
        outcomes.append(NotebookOutcome(notebook, before_pages[notebook], after))

    completed_pages = sorted(
        {page for candidate in plan.ready_to_move for page in candidate.source_pages}
    )
    return MoveResult(
        plan=plan,
        backup_dir=backup_dir,
        outcomes=outcomes,
        completed_pages=completed_pages,
        skipped_pages=skipped,
        needs_review_pages=review,
        operation_ids=operation_ids,
        dry_run=False,
    )
