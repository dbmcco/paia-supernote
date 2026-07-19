"""Offline integration coverage for the Cloud change ledger read/write contract.

These tests exercise the *integration boundary* between the mocked CloudPoller
detection loop and the ledger-aware IngestService ingest path, then prove the
cached read contract and the write-safety guard behave on the resulting SQLite
state — all without real Supernote Cloud contact, real OCR, or paia-events HTTP.

Boundaries exercised:

* ``CloudPoller._poll_once`` → ``IngestService._on_cloud_note_changed`` — the
  detection→ingest seam. A multi-poll fake uploader serves an initial snapshot
  and a changed snapshot across two real poll cycles.
* ``SupernoteReadContract`` — cached latest-state and changes reads derived only
  from the SQLite ledger/page-state cache.
* ``validate_agent_write_request`` — the base-revision write guard that must fail
  closed before any Cloud mutation.

Reused helpers:

* ``FakeReader`` (OCR spy) is imported from ``tests.test_ingest_change_ledger``
  rather than duplicated.
* The ingest config pattern mirrors the ledger unit tests.

No real Supernote Cloud credentials, no browser/Playwright, no model calls, and
no paia-events HTTP are used. Every store points at a temporary SQLite database.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from paia_supernote.agent_read_contracts import (
    LatestNotebookStateRequest,
    NotebookChangesRequest,
    ReadContractError,
    SupernoteReadContract,
)
from paia_supernote.agent_write_contracts import (
    AgentWriteRevisionError,
    validate_agent_write_request,
)
from paia_supernote.cloud_change_ledger import (
    CHANGE_ADDED,
    CHANGE_REMOVED,
    CHANGE_REORDER,
    CHANGE_UPDATED,
)
from paia_supernote.cloud_poller import CloudPoller
from paia_supernote.ingest_service import IngestService

# Reuse the OCR-spy reader helper from the ledger unit tests instead of
# duplicating it. It returns deterministic page IDs/hashes per note bytes and
# records every read_pages() call so call counts can be asserted directly.
from tests.test_ingest_change_ledger import FakeReader

_NOTEBOOK = "Quick"
_REV1_BYTES = b"quick-rev1"
_REV2_BYTES = b"quick-rev2"


# ---------------------------------------------------------------------------
# Shared fake boundaries
# ---------------------------------------------------------------------------


class MultiPollListingUploader:
    """Fake Supernote Cloud boundary that serves a sequence of poll snapshots.

    Serves a different Note-folder listing + download-bytes map per poll index
    so a two-poll CloudPoller sequence can be driven deterministically. Also
    records any mutation (``upload_notebook``) call so write tests can prove the
    upload boundary was never reached.
    """

    NOTE_FOLDER_ID = "note-folder"

    def __init__(self, polls: list[dict]) -> None:
        self._polls = polls
        self._index = 0
        self.downloaded: list[tuple[int, str]] = []
        self.upload_calls: list[tuple] = []
        self.ensure_authenticated = AsyncMock()

    def advance(self) -> None:
        """Advance to the next poll's listing/download map."""
        self._index += 1

    @property
    def index(self) -> int:
        return self._index

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        assert endpoint == "/api/file/list/query"
        poll = self._polls[self._index]
        return {"status": 200, "body": {"userFileVOList": list(poll["entries"])}}

    async def download_notebook(self, target_name: str) -> bytes:
        poll = self._polls[self._index]
        self.downloaded.append((self._index, target_name))
        return poll["downloads"][target_name]

    async def upload_notebook(self, *args: object, **kwargs: object) -> bool:
        self.upload_calls.append((args, kwargs))
        return True


def _entry(update_time: int) -> dict:
    return {
        "fileName": f"{_NOTEBOOK}.note",
        "updateTime": update_time,
        "isFolder": "N",
        "size": 10,
    }


def _polls() -> list[dict]:
    return [
        {
            "entries": [_entry(100)],
            "downloads": {f"{_NOTEBOOK}.note": _REV1_BYTES},
        },
        {
            "entries": [_entry(200)],
            "downloads": {f"{_NOTEBOOK}.note": _REV2_BYTES},
        },
    ]


def _reader() -> FakeReader:
    # Initial snapshot: A@0, B@1, C@2, D@3.
    # Changed snapshot: B@0, A@1, C@2(content-updated), E@3 →
    #   * A and B swap relative position (both same hash) → reorder
    #   * C content changes → updated
    #   * D disappears → removed
    #   * E appears → added
    # All four change types are produced in a single diff. Reorder requires at
    # least two same-hash common pages to move relative to each other.
    return FakeReader(
        {
            _REV1_BYTES: [
                ("A", "hash-a"),
                ("B", "hash-b"),
                ("C", "hash-c"),
                ("D", "hash-d"),
            ],
            _REV2_BYTES: [
                ("B", "hash-b"),
                ("A", "hash-a"),
                ("C", "hash-c-updated"),
                ("E", "hash-e"),
            ],
        }
    )


def _ingest_config(tmp_path: Path) -> dict:
    return {
        "events_url": "http://events.invalid",
        "state_db_path": str(tmp_path / "state.db"),
        "zai_vision_model": "glm-test-vision",
        "poll_interval": 999,
        "folio_sync_notebooks": [],
        "cloud_change_ledger_notebooks": [_NOTEBOOK],
    }


def _cloud_revision(update_time: int, note_bytes: bytes) -> str:
    return f"{update_time}:{hashlib.sha256(note_bytes).hexdigest()}"


async def _run_two_poll_ingest(tmp_path: Path) -> SimpleNamespace:
    """Drive a real CloudPoller→IngestService two-poll sequence and return state.

    Poll 1 ingests the initial snapshot; poll 2 ingests the changed snapshot.
    The returned namespace carries the service, OCR-spy reader, fake uploader,
    the poll-1 cursor (for isolating poll-2 changes), and the two revisions.
    """
    reader = _reader()
    uploader = MultiPollListingUploader(_polls())
    service = IngestService(
        config=_ingest_config(tmp_path),
        reader=reader,  # type: ignore[arg-type]
    )
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=service._on_cloud_note_changed,
        watched_notebooks=[_NOTEBOOK],
        process_existing_on_start=True,
    )

    # Poll 1: initial snapshot.
    await poller._poll_once()
    poll1_cursor = service.ledger.latest_change_id(_NOTEBOOK)
    assert poll1_cursor is not None

    # Poll 2: changed snapshot (added/updated/removed/reordered).
    uploader.advance()
    await poller._poll_once()

    return SimpleNamespace(
        service=service,
        reader=reader,
        uploader=uploader,
        poll1_cursor=poll1_cursor,
        rev1_revision=_cloud_revision(100, _REV1_BYTES),
        rev2_revision=_cloud_revision(200, _REV2_BYTES),
    )


# ---------------------------------------------------------------------------
# Two-poll diff contract — added / updated / removed / reordered
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_poll_records_added_updated_removed_reordered(
    tmp_path: Path,
) -> None:
    """A two-poll sequence records all four change types between snapshots."""
    result = await _run_two_poll_ingest(tmp_path)

    # Poll-2 changes, isolated from the initial-seed changes. A/B swap yields
    # two reorder events, so group by type rather than keying a single dict.
    poll2_changes = result.service.ledger.changes_since(
        _NOTEBOOK, result.poll1_cursor
    )
    assert {change.change_type for change in poll2_changes} == {
        CHANGE_ADDED,
        CHANGE_REORDER,
        CHANGE_REMOVED,
        CHANGE_UPDATED,
    }

    added = [c for c in poll2_changes if c.change_type == CHANGE_ADDED]
    removed = [c for c in poll2_changes if c.change_type == CHANGE_REMOVED]
    updated = [c for c in poll2_changes if c.change_type == CHANGE_UPDATED]
    reordered = [c for c in poll2_changes if c.change_type == CHANGE_REORDER]

    # Added: E at index 3.
    assert len(added) == 1
    assert added[0].page_id == "E"
    assert added[0].new_index == 3
    assert added[0].old_index is None
    # Removed: D was at index 3.
    assert len(removed) == 1
    assert removed[0].page_id == "D"
    assert removed[0].old_index == 3
    assert removed[0].new_index is None
    # Updated: C content changed (hash-c → hash-c-updated), stays at index 2.
    assert len(updated) == 1
    assert updated[0].page_id == "C"
    assert updated[0].old_hash == "hash-c"
    assert updated[0].new_hash == "hash-c-updated"
    assert updated[0].new_index == 2
    # Reorder: A and B swap positions, content unchanged.
    assert {c.page_id for c in reordered} == {"A", "B"}
    reorder_by_id = {c.page_id: c for c in reordered}
    assert reorder_by_id["A"].old_index == 0
    assert reorder_by_id["A"].new_index == 1
    assert reorder_by_id["B"].old_index == 1
    assert reorder_by_id["B"].new_index == 0
    for change in reordered:
        assert change.old_hash == change.new_hash

    # The current cached notebook is the changed (rev-2) revision.
    latest = result.service.ledger.latest_notebook_state(_NOTEBOOK)
    assert latest is not None
    assert latest.cloud_revision == result.rev2_revision


# ---------------------------------------------------------------------------
# OCR spy — only new / content-updated pages are OCRed
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ocr_spy_runs_only_for_new_or_content_updated_pages(
    tmp_path: Path,
) -> None:
    """OCR is invoked only for added or content-updated page indexes.

    Poll 1 OCRs all four initial pages; poll 2 OCRs only C (updated) and E
    (added). A and B (reorder-only) and D (removed) are never re-OCR'd. Ingest
    also never triggers a Cloud mutation (upload) — it is read-only w.r.t.
    writes.
    """
    result = await _run_two_poll_ingest(tmp_path)

    assert result.reader.ocr_calls == [
        (_REV1_BYTES, _NOTEBOOK, (0, 1, 2, 3)),
        (_REV2_BYTES, _NOTEBOOK, (2, 3)),
    ]
    # Exactly two OCR passes total (one per non-empty poll).
    assert len(result.reader.ocr_calls) == 2
    # Ingest must never mutate Cloud data — only download for parse/OCR.
    assert result.uploader.upload_calls == []
    assert [download[1] for download in result.uploader.downloaded] == [
        f"{_NOTEBOOK}.note",
        f"{_NOTEBOOK}.note",
    ]


# ---------------------------------------------------------------------------
# Cached read — latest state and changes from SQLite only, no Cloud contact
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cached_read_returns_latest_state_and_changes_without_cloud(
    tmp_path: Path,
) -> None:
    """The read contract returns cached state/changes with no Cloud wiring."""
    result = await _run_two_poll_ingest(tmp_path)
    # Read contract is constructed with only the SQLite db path + config: no
    # uploader, no poller, no Cloud client — proving the read is cache-backed.
    contract = SupernoteReadContract(
        {"cloud_change_ledger_notebooks": [_NOTEBOOK]},
        tmp_path / "state.db",
    )

    latest = contract.get_latest_notebook_state(
        LatestNotebookStateRequest(notebook=_NOTEBOOK)
    )
    assert latest.notebook == _NOTEBOOK
    assert latest.notebook_revision == result.rev2_revision
    assert latest.page_count == 4
    # Current pages ordered by cached index; OCR ready for all ingested pages.
    assert [page.page_id for page in latest.pages] == ["B", "A", "C", "E"]
    assert {
        page.page_id: page.content_hash for page in latest.pages
    } == {
        "B": "hash-b",
        "A": "hash-a",
        "C": "hash-c-updated",
        "E": "hash-e",
    }
    assert {page.ocr_status for page in latest.pages} == {"ready"}

    # Cached changes since the poll-1 cursor replay exactly the poll-2 diff.
    changes = contract.get_changes(
        NotebookChangesRequest(notebook=_NOTEBOOK, since=result.poll1_cursor)
    )
    assert {change.change_type for change in changes.changes} == {
        CHANGE_ADDED,
        CHANGE_REORDER,
        CHANGE_REMOVED,
        CHANGE_UPDATED,
    }
    assert changes.notebook_revision == result.rev2_revision
    assert changes.next_cursor >= result.poll1_cursor


# ---------------------------------------------------------------------------
# Structured surface error — disallowed notebook read fails with guidance
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_disallowed_notebook_read_returns_structured_error(
    tmp_path: Path,
) -> None:
    """A non-allowlisted notebook read surfaces a structured error envelope."""
    await _run_two_poll_ingest(tmp_path)
    contract = SupernoteReadContract(
        {"cloud_change_ledger_notebooks": [_NOTEBOOK]},
        tmp_path / "state.db",
    )

    with pytest.raises(ReadContractError) as exc_info:
        contract.get_changes(NotebookChangesRequest(notebook="Secret", since=0))

    error = exc_info.value.error
    assert error.error_code == "disallowed_notebook"
    assert error.mutation_applied is False
    assert error.retryable is False
    # Machine-readable guidance fields are populated.
    assert error.field == "notebook"
    assert error.expected["allowed_notebooks"] == [_NOTEBOOK]
    assert error.next_actions  # non-empty recovery steps


# ---------------------------------------------------------------------------
# Write conflict — stale base revision fails closed before any upload
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_write_conflict_returns_structured_guidance_and_never_uploads(
    tmp_path: Path,
) -> None:
    """A stale base revision returns structured conflict guidance and never
    reaches the uploader mutation boundary."""
    result = await _run_two_poll_ingest(tmp_path)
    config = {"cloud_change_ledger_notebooks": [_NOTEBOOK]}
    state_db = tmp_path / "state.db"
    uploader = MultiPollListingUploader(_polls())

    async def attempt_write_route() -> None:
        # The write route validates the base revision BEFORE any Cloud mutation.
        validate_agent_write_request(
            {
                "agent": "Sam",
                "notebook": _NOTEBOOK,
                "content_type": "replace_pages",
                "base_notebook_revision": result.rev1_revision,
                "pages": [{"agent": "Sam", "content": "Page 1"}],
            },
            config=config,
            state_db_path=state_db,
            resolved_agent="Sam",
            resolved_notebook=_NOTEBOOK,
        )
        # Only reached if the guard accepted the revision — the real route then
        # downloads and uploads. This must never execute for a stale base.
        await uploader.download_notebook(f"{_NOTEBOOK}.note")
        await uploader.upload_notebook("staged.note", f"{_NOTEBOOK}.note")

    with pytest.raises(AgentWriteRevisionError) as exc_info:
        await attempt_write_route()

    conflict = exc_info.value.conflict
    assert conflict.error_code == "stale_base_revision"
    assert conflict.notebook == _NOTEBOOK
    assert conflict.requested_base_revision == result.rev1_revision
    assert conflict.current_revision == result.rev2_revision
    assert conflict.mutation_applied is False
    assert conflict.retryable is True
    assert conflict.next_actions  # structured recovery guidance

    # The guard failed before the upload boundary — no Cloud mutation occurred.
    assert uploader.downloaded == []
    assert uploader.upload_calls == []
