"""Which scheduled OCR retries are due, and which of those have stalled.

ABOUTME: Pure selection for the clock-driven retry sweep (item
ABOUTME: apps-supernote-ocr-silent-stop). No I/O: the caller supplies the rows and
ABOUTME: a predicate for "this page is still ledger-pending", so the decision is
ABOUTME: testable without a database, a poller or a model.

The defect this exists for: the retry sweep lived only in the change handler, so a
page that failed OCR while its notebook stayed quiet kept a ``next_retry_at`` that
nothing ever reached. The sweep now runs on a clock, and anything still due after a
grace window is reported as a stall instead of passing unmentioned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Protocol

from . import blank_cap

# A retry that has been due this long without completing is a stall, not a delay.
DEFAULT_STALL_AFTER_SECONDS = 900.0


class RetryRow(Protocol):
    """The page_state fields this selection needs."""

    notebook: str
    page: int
    next_retry_at: str | None
    last_error: str | None
    last_error_stage: str | None


@dataclass(frozen=True)
class DueRetry:
    """One page whose OCR retry has come due."""

    notebook: str
    page: int
    next_retry_at: str
    overdue_seconds: float
    last_error: str | None
    last_error_stage: str | None

    def as_event_entry(self) -> dict:
        return {
            "notebook": self.notebook,
            "page": self.page,
            "next_retry_at": self.next_retry_at,
            "overdue_seconds": round(self.overdue_seconds, 1),
            "last_error": self.last_error,
            "last_error_stage": self.last_error_stage,
        }


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def due_retries(
    rows: Iterable[RetryRow],
    *,
    now: datetime,
    pending: Callable[[str, int], bool],
) -> list[DueRetry]:
    """Rows whose backoff has elapsed AND whose page is still ledger-pending.

    A row whose ``next_retry_at`` cannot be parsed is not selected: an unreadable
    schedule is a different defect (and the change path still sweeps it), so this
    selection never guesses. A row already in the blank-terminal stage is skipped
    regardless of any schedule it carries (item apps-supernote-ocr-silent-stop).
    """
    due: list[DueRetry] = []
    for row in rows:
        if row.next_retry_at is None:
            continue
        if getattr(row, "last_error_stage", None) == blank_cap.BLANK_TERMINAL_STAGE:
            # A blank page that reached its attempt cap is decided, not delayed.
            # It carries no schedule of its own; this check is the belt for a row
            # that somehow still has one, so a terminal page can never cost a
            # download or a vision call again.
            continue
        when = _parse(row.next_retry_at)
        if when is None or when > now:
            continue
        if not pending(row.notebook, row.page):
            continue
        due.append(
            DueRetry(
                notebook=row.notebook,
                page=row.page,
                next_retry_at=row.next_retry_at,
                overdue_seconds=(now - when).total_seconds(),
                last_error=getattr(row, "last_error", None),
                last_error_stage=getattr(row, "last_error_stage", None),
            )
        )
    return sorted(due, key=lambda d: (d.next_retry_at, d.notebook, d.page))


def stalled_retries(
    due: Iterable[DueRetry],
    *,
    stall_after_seconds: float = DEFAULT_STALL_AFTER_SECONDS,
) -> list[DueRetry]:
    """The subset of due retries that have been due longer than the grace window."""
    return [d for d in due if d.overdue_seconds >= stall_after_seconds]
