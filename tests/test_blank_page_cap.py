"""A genuinely blank page must stop being retried, loudly, at a known count.
Item apps-supernote-ocr-silent-stop, residual call recorded 2026-11-15 by the
manager after the loud-no-op fix landed (main 144dd84):

    "a genuinely blank page backing off forever saying so is honest but
     unconverged - cap it at 5 attempts, then a terminal blank state with one
     final loud event (supernote.ocr_retry.blank) and no further vision calls;
     the stalled alarm's once-per-stall dedupe stays."

Three properties, and this file is written to fail until all three hold:
1. a blank page is attempted at most ``BLANK_ATTEMPT_CAP`` (5) times, then it
   enters a terminal blank state instead of scheduling another attempt;
2. the 6th attempt never happens - neither on the clock sweep nor on a re-ingest
   of the same bytes, so no vision budget is spent on a page already decided;
3. the terminal event fires once, and it names the page and the reason.

Guards against over-firing, because a cap that catches the wrong thing is worse
than no cap: a transient vision error is never capped, and new ink on a formerly
blank page starts a fresh ladder rather than being silently dropped.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from paia_supernote import blank_cap
from paia_supernote.reader import ReadResult
from tests.test_ingest_change_ledger import (
    FakeReader,
    ListingUploader,
    _service,
)

CAP = 5


class BlankReader(FakeReader):
    """FakeReader whose blank pages answer exactly like the real reader does.

    The message comes from ``blank_cap.blank_page_error_message`` - the same
    helper reader.py uses - so this fake cannot drift from the code under test.
    """

    def __init__(self, snapshots, blank_pages: set[int]) -> None:
        super().__init__(snapshots=snapshots)
        self.blank_pages = set(blank_pages)

    def _calls_for(self, page: int) -> int:
        return sum(1 for _b, _n, pages in self.ocr_calls if page in pages)

    async def read_pages_resilient(
        self, note_bytes: bytes, notebook_name: str, *, pages: list[int]
    ):
        self.ocr_calls.append((note_bytes, notebook_name, tuple(pages)))
        results: list[ReadResult] = []
        errors: dict[int, str] = {}
        for page_num in pages:
            if page_num in self.blank_pages:
                errors[page_num] = blank_cap.blank_page_error_message(page_num)
                continue
            results.append(
                ReadResult(
                    notebook=notebook_name,
                    page_num=page_num,
                    text=f"ocr-{note_bytes.decode()}-{page_num}",
                    checkboxes=[],
                    content_type="general",
                    timestamp=datetime.now(timezone.utc),
                    page_image=None,
                )
            )
        return results, errors


class FakeEvents:
    """Capture both the stall status and the new terminal blank event."""

    def __init__(self) -> None:
        self.stalls: list[dict] = []
        self.blanks: list[dict] = []

    async def publish_ocr_retry_status(self, *, stalled, entries):
        self.stalls.append({"stalled": stalled, "entries": list(entries)})

    async def publish_ocr_blank_terminal(self, *, entries):
        self.blanks.append({"entries": list(entries)})


def _force_due(svc, notebook: str, page: int) -> None:
    """Make the page's backoff elapsed without touching the notebook."""
    with sqlite3.connect(str(svc.page_state._db_path)) as conn:
        conn.execute(
            "UPDATE page_state SET next_retry_at = ? WHERE notebook = ? AND page = ?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                notebook,
                page,
            ),
        )


def _uploader(payload: bytes = b"rev1", name: str = "Quick") -> ListingUploader:
    return ListingUploader(
        entries=[{"fileName": f"{name}.note", "updateTime": 100}],
        downloads={f"{name}.note": payload},
    )


def _state(svc, notebook: str, page: int):
    return svc.page_state.get_page_or_none(notebook, page)


# --- 1. the cap itself, as pure arithmetic -----------------------------------


def test_the_cap_is_five_attempts_and_the_fifth_is_the_terminal_one() -> None:
    """Attempt numbering is per content identity, and 5 is where it stops."""
    assert blank_cap.BLANK_ATTEMPT_CAP == CAP
    numbers = [
        blank_cap.blank_attempt_number(prior_retry_count=n, same_content=True)
        for n in range(0, CAP)
    ]
    assert numbers == [1, 2, 3, 4, 5]
    assert [blank_cap.blank_attempt_is_terminal(n) for n in numbers] == [
        False,
        False,
        False,
        False,
        True,
    ]


def test_new_content_starts_the_ladder_again() -> None:
    """A revised page is new work, so its count does not inherit the old one."""
    assert (
        blank_cap.blank_attempt_number(prior_retry_count=CAP, same_content=False) == 1
    )


def test_the_blank_message_is_recognised_and_other_failures_are_not() -> None:
    """The cap must fire on a blank page and on nothing else."""
    blank = blank_cap.blank_page_error_message(6)
    assert "page 6" in blank
    assert blank_cap.is_blank_page_error(blank) is True
    assert blank_cap.is_blank_page_error("vision timeout (simulated)") is False
    assert blank_cap.is_blank_page_error(
        "page 9 is outside this notebook's 6 pages, so there is nothing to read for it"
    ) is False
    assert blank_cap.is_blank_page_error(None) is False
    assert blank_cap.is_blank_page_error("") is False


# --- 2. the service-level arc: cap, terminal state, no 6th attempt -----------


@pytest.mark.asyncio
async def test_a_blank_page_reaches_the_cap_and_goes_terminal(tmp_path: Path) -> None:
    """Five blank attempts, then a terminal state - not an endless backoff."""
    reader = BlankReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, blank_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    for _ in range(CAP - 1):
        _force_due(svc, "Quick", 1)
        await svc.retry_sweep_once()

    assert reader._calls_for(1) == CAP
    state = _state(svc, "Quick", 1)
    assert state is not None
    assert state.retry_count == CAP
    assert state.last_error_stage == blank_cap.BLANK_TERMINAL_STAGE
    assert state.next_retry_at is None, "a terminal blank page schedules nothing"


@pytest.mark.asyncio
async def test_the_sixth_attempt_never_happens(tmp_path: Path) -> None:
    """Terminal means terminal: no sweep and no re-ingest spends vision on it."""
    reader = BlankReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, blank_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    for _ in range(CAP - 1):
        _force_due(svc, "Quick", 1)
        await svc.retry_sweep_once()
    assert reader._calls_for(1) == CAP

    calls_at_terminal = reader._calls_for(1)

    # Force the schedule due again first: only the TERMINAL state may stop the
    # attempt. Without that, a page still sitting inside its backoff would pass
    # this test for the wrong reason.
    _force_due(svc, "Quick", 1)
    assert await svc.retry_sweep_once() == []
    _force_due(svc, "Quick", 1)
    assert await svc.retry_sweep_once() == []
    # and a re-ingest of the same bytes, which is what the poller does on a tick
    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)

    assert reader._calls_for(1) == calls_at_terminal, "the 6th attempt must not happen"


@pytest.mark.asyncio
async def test_the_terminal_event_fires_once_and_names_the_page(tmp_path: Path) -> None:
    """One loud event per blank page, naming the page and the reason."""
    reader = BlankReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, blank_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader())
    events = FakeEvents()
    svc.events = events

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    for _ in range(CAP - 1):
        _force_due(svc, "Quick", 1)
        await svc.retry_sweep_once()

    assert len(events.blanks) == 1, (
        f"expected exactly one terminal event, got {events.blanks}"
    )
    entry = events.blanks[0]["entries"][0]
    assert entry["notebook"] == "Quick"
    assert entry["page"] == 1
    assert entry["attempts"] == CAP
    assert "no text" in entry["reason"].lower()

    # nothing further is announced, however many sweeps run afterwards
    await svc.retry_sweep_once()
    _force_due(svc, "Quick", 1)
    await svc.retry_sweep_once()
    assert len(events.blanks) == 1


# --- 3. the guards: the cap catches blank pages and nothing else -------------


@pytest.mark.asyncio
async def test_a_transient_error_is_never_capped(tmp_path: Path) -> None:
    """The cap is for blank pages. A vision timeout keeps its normal ladder."""
    reader = BlankReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, blank_pages=set()
    )

    async def transient(note_bytes, notebook_name, *, pages):
        reader.ocr_calls.append((note_bytes, notebook_name, tuple(pages)))
        return [], {page: "vision timeout (simulated)" for page in pages}

    reader.read_pages_resilient = transient  # type: ignore[assignment]
    svc = _service(tmp_path, reader, uploader=_uploader())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    for _ in range(CAP + 2):
        _force_due(svc, "Quick", 1)
        await svc.retry_sweep_once()

    state = _state(svc, "Quick", 1)
    assert state is not None
    assert state.retry_count > CAP
    assert state.last_error_stage != blank_cap.BLANK_TERMINAL_STAGE
    assert state.next_retry_at is not None, "a transient fault is still retried"


@pytest.mark.asyncio
async def test_new_ink_on_a_blank_page_starts_a_fresh_ladder(tmp_path: Path) -> None:
    """A rewritten page is new work; the old terminal state must not drop it."""
    reader = BlankReader(
        snapshots={
            b"rev1": [("P0", "h0"), ("P1", "h1")],
            b"rev2": [("P0", "h0"), ("P1", "h9")],
        },
        blank_pages={1},
    )
    svc = _service(tmp_path, reader, uploader=_uploader())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    for _ in range(CAP - 1):
        _force_due(svc, "Quick", 1)
        await svc.retry_sweep_once()
    assert _state(svc, "Quick", 1).last_error_stage == blank_cap.BLANK_TERMINAL_STAGE

    # the user writes on the page again: new content hash, new revision
    svc._uploader.downloads["Quick.note"] = b"rev2"
    await svc._on_cloud_note_changed("Quick", b"rev2", update_time=200)

    state = _state(svc, "Quick", 1)
    assert state is not None
    assert state.retry_count == 1, "new ink starts at attempt 1, not at the cap"
    assert state.last_error_stage == "ocr"
    assert state.next_retry_at is not None, "the new content gets its own retries"
