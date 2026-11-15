"""How many blank-page OCR attempts are worth making, and when to stop.

ABOUTME: Pure selection for the blank-page retry cap (item
ABOUTME: apps-supernote-ocr-silent-stop). No I/O, no clock, no model.
The residual call this answers (manager, 2026-11-15, after the loud-no-op fix):
a genuinely blank page that backs off forever saying so is honest but
unconverged. Nothing screams - the backoff grows and the stall alarm is deduped -
but the page keeps claiming vision budget on every sweep and never resolves, so
"why is this page still pending?" has no terminus. Capping it gives the lane a
terminal state and one final event, without pretending the page was read: a
blank page is still not marked done, because an empty string in place of a page
that may hold ink the model failed to read would silently lose content.

The cap counts only the blank class. A vision timeout, a download failure or a
page index outside the notebook are different defects with different fixes, so
they keep their ordinary unbounded backoff (``retry_sweep``) - a cap that caught
them would hide a transient outage instead of converging anything.
"""
from __future__ import annotations

#: Attempts a page gets before a blank result is treated as its answer.
BLANK_ATTEMPT_CAP = 5

#: The failure stage recorded once the cap is reached. Distinct from "ocr" (still
#: retrying) and from "enrich" (a different lane), so a terminal page is legible
#: in page_state without adding a column.
BLANK_TERMINAL_STAGE = "ocr_blank_terminal"

#: The phrase reader.py uses when a transcription comes back empty. Kept here so
#: the producer and the classifier cannot drift apart.
BLANK_ERROR_MARKER = "produced no text"


def blank_page_error_message(page_num: int) -> str:
    """The named failure for a page whose transcription came back empty.

    The wording is the one already live since 144dd84 (Mgmt page 6 carries it in
    page_state and in the stall alarm), so rows recorded before this cap remain
    recognisable to :func:`is_blank_page_error`.
    """
    return (
        f"page {page_num} produced no text: the transcription "
        "came back empty, so the page is blank or the image did not "
        "reach the model"
    )


def is_blank_page_error(message: str | None) -> bool:
    """True when this failure is the empty-transcription class, and only that."""
    if not message:
        return False
    return BLANK_ERROR_MARKER in message.lower()


def blank_attempt_number(
    prior_retry_count: int | None, *, same_content: bool
) -> int:
    """Which attempt this blank result is, counting from 1.

    The count belongs to the content being read, not to the page forever: a page
    the user writes on again arrives with a new revision, and that is new work
    which starts at 1 rather than inheriting an exhausted ladder.
    """
    if not same_content:
        return 1
    return max(0, int(prior_retry_count or 0)) + 1


def blank_attempt_is_terminal(attempt_number: int) -> bool:
    """True when this attempt is the last one: the page goes terminal blank."""
    return attempt_number >= BLANK_ATTEMPT_CAP
