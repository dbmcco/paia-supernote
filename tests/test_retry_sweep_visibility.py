"""A scheduled OCR retry must run on a clock, and must announce a stall.

Why this file exists (item apps-supernote-ocr-silent-stop, 2026-11-15):

The retry sweep for a page that failed OCR lived inside ``_on_cloud_note_changed``,
and the poller only calls that handler for a file whose ``updateTime`` advanced.
So a page that failed OCR on a quiet notebook had a ``next_retry_at`` that nothing
would ever reach: two Mgmt pages failed at 2026-11-13 21:37:59Z and 21:42:09Z with
retries due at 21:42:59Z and 21:47:09Z, the notebook never changed again, the
ingest process had been up since 6 November, and nothing said a word about it.

So the sweep needs two properties this module pins down with tests:

1. it runs because time passed, not because a file changed; and
2. a retry that is due and still not done announces itself as a stall, exactly
   once per stall, and announces the recovery when it clears.

Silent lanes announce their failures; they are never silent about their own stalls.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_ingest_change_ledger import (
    FlakyReader,
    ListingUploader,
    _service,
)


class FakeEvents:
    """Capture publish_ocr_retry_status calls instead of talking to the bus."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish_ocr_retry_status(self, *, stalled, entries):
        self.calls.append({"stalled": stalled, "entries": list(entries)})


def _due_retry(svc, notebook: str, page: int) -> None:
    """Force the page's backoff to have elapsed, without an edit to the notebook."""
    db = str(svc.page_state._db_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE page_state SET next_retry_at = ? WHERE notebook = ? AND page = ?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                notebook,
                page,
            ),
        )


def _uploader_for(name: str = "Quick", payload: bytes = b"rev1") -> ListingUploader:
    return ListingUploader(
        entries=[{"fileName": f"{name}.note", "updateTime": 100}],
        downloads={f"{name}.note": payload},
    )


@pytest.mark.asyncio
async def test_a_due_retry_runs_on_the_clock_with_no_change_to_the_notebook(
    tmp_path: Path,
) -> None:
    """The bug in one test: nothing edits the notebook, and the retry still runs."""
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader_for())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    assert {p.page_index: p.ocr_status for p in svc.ledger.current_pages("Quick")}[
        1
    ] == "pending"

    _due_retry(svc, "Quick", 1)
    reader.fail_pages = set()  # the transient fault has cleared

    executed = await svc.retry_sweep_once()

    assert {p.page_index: p.ocr_status for p in svc.ledger.current_pages("Quick")}[
        1
    ] == "ready"
    assert [(d.notebook, d.page) for d in executed] == [("Quick", 1)]


@pytest.mark.asyncio
async def test_the_clock_sweep_leaves_a_page_whose_backoff_has_not_elapsed_alone(
    tmp_path: Path,
) -> None:
    """It is a retry sweep, not a re-OCR: a page still inside its backoff waits."""
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader_for())
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    reader.fail_pages = set()
    calls_before = len(reader.ocr_calls)

    executed = await svc.retry_sweep_once()

    assert executed == []
    assert len(reader.ocr_calls) == calls_before


@pytest.mark.asyncio
async def test_a_retry_that_cannot_run_announces_its_stall_once(
    tmp_path: Path,
) -> None:
    """The lane must not be silent about a retry that will not run.

    A retry whose bytes cannot be fetched stays due and undone. The first sweep past
    the grace publishes one stall that names the page and how late it is; the next
    sweep over the same standing stall publishes nothing more.
    """
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    uploader = _uploader_for()
    svc = _service(tmp_path, reader, uploader=uploader)
    events = FakeEvents()
    svc.events = events
    svc._retry_stall_grace_seconds = 0  # any overdue retry counts as stalled

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    _due_retry(svc, "Quick", 1)

    async def boom(target_name: str) -> bytes:
        raise RuntimeError("cloud session expired")

    uploader.download_notebook = boom  # type: ignore[assignment]

    await svc.retry_sweep_once()
    stalls = [c for c in events.calls if c["stalled"]]
    assert len(stalls) == 1
    entry = stalls[0]["entries"][0]
    assert entry["notebook"] == "Quick" and entry["page"] == 1
    assert entry["overdue_seconds"] >= 0
    assert entry["last_error_stage"] == "ocr"

    await svc.retry_sweep_once()
    assert len([c for c in events.calls if c["stalled"]]) == 1, (
        "a standing stall is announced once"
    )


@pytest.mark.asyncio
async def test_a_stalled_retry_that_clears_announces_the_recovery(
    tmp_path: Path,
) -> None:
    """Silence after a stall must itself be explained, so recovery is published."""
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    uploader = _uploader_for()
    svc = _service(tmp_path, reader, uploader=uploader)
    events = FakeEvents()
    svc.events = events
    svc._retry_stall_grace_seconds = 0

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    _due_retry(svc, "Quick", 1)

    async def boom(target_name: str) -> bytes:
        raise RuntimeError("cloud session expired")

    real_download = uploader.download_notebook
    uploader.download_notebook = boom  # type: ignore[assignment]
    await svc.retry_sweep_once()
    assert [c for c in events.calls if c["stalled"]]

    uploader.download_notebook = real_download  # type: ignore[assignment]
    reader.fail_pages = set()
    await svc.retry_sweep_once()

    assert events.calls[-1]["stalled"] is False
    assert events.calls[-1]["entries"] == []
    assert {p.page_index: p.ocr_status for p in svc.ledger.current_pages("Quick")}[
        1
    ] == "ready"


@pytest.mark.asyncio
async def test_a_retry_whose_download_fails_still_announces_rather_than_hiding(
    tmp_path: Path,
) -> None:
    """A sweep that cannot even fetch the bytes must say why, not log and move on."""
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    uploader = _uploader_for()
    svc = _service(tmp_path, reader, uploader=uploader)
    events = FakeEvents()
    svc.events = events
    svc._retry_stall_grace_seconds = 0

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    _due_retry(svc, "Quick", 1)

    async def boom(target_name: str) -> bytes:
        raise RuntimeError("cloud session expired")

    uploader.download_notebook = boom  # type: ignore[assignment]

    await svc.retry_sweep_once()

    stalls = [c for c in events.calls if c["stalled"]]
    assert len(stalls) == 1
    assert stalls[0]["entries"][0]["page"] == 1
    assert "cloud session expired" in stalls[0]["entries"][0]["sweep_error"]


@pytest.mark.asyncio
async def test_a_due_retry_inside_the_grace_window_says_nothing_at_all(
    tmp_path: Path,
) -> None:
    """No noise: a retry that is merely late, and then runs, is not an alarm."""
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    svc = _service(tmp_path, reader, uploader=_uploader_for())
    events = FakeEvents()
    svc.events = events
    svc._retry_stall_grace_seconds = 3600  # a one-second overrun is not a stall

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    _due_retry(svc, "Quick", 1)
    reader.fail_pages = set()

    await svc.retry_sweep_once()

    assert events.calls == []
    assert {p.page_index: p.ocr_status for p in svc.ledger.current_pages("Quick")}[
        1
    ] == "ready"
