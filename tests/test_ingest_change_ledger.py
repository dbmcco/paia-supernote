from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from paia_supernote.cloud_change_ledger import (
    CHANGE_ADDED,
    CHANGE_REMOVED,
    CHANGE_REORDER,
    CHANGE_UPDATED,
    PageChangeRecord,
)
from paia_supernote.cloud_poller import CloudPoller
from paia_supernote.ingest_service import IngestService
from paia_supernote.reader import ReadResult


class ListingUploader:
    NOTE_FOLDER_ID = "note-folder"

    def __init__(self, entries: list[dict], downloads: dict[str, bytes]) -> None:
        self.entries = entries
        self.downloads = downloads
        self.downloaded: list[str] = []

    async def _api_call(self, endpoint: str, body: dict) -> dict:
        assert endpoint == "/api/file/list/query"
        return {"status": 200, "body": {"userFileVOList": self.entries}}

    async def download_notebook(self, target_name: str) -> bytes:
        self.downloaded.append(target_name)
        return self.downloads[target_name]


async def _record_change(changes: list[tuple[str, bytes, int | None]], *args) -> None:
    changes.append(args)


class FakeReader:
    def __init__(self, snapshots: dict[bytes, list[tuple[str, str]]]) -> None:
        self.snapshots = snapshots
        self.ocr_calls: list[tuple[bytes, str, tuple[int, ...]]] = []

    def build_snapshot(self, note_bytes: bytes, notebook_name: str, revision: str):
        pages: dict[str, SimpleNamespace] = {}
        page_order: list[str] = []
        for index, (page_id, content_hash) in enumerate(self.snapshots[note_bytes]):
            page_order.append(page_id)
            pages[page_id] = SimpleNamespace(
                page_id=page_id,
                page_index=index,
                content_hash=content_hash,
            )
        return SimpleNamespace(
            notebook_name=notebook_name,
            revision=revision,
            page_order=page_order,
            pages=pages,
        )

    async def read_pages(
        self, note_bytes: bytes, notebook_name: str, *, pages: list[int]
    ):
        self.ocr_calls.append((note_bytes, notebook_name, tuple(pages)))
        return [
            ReadResult(
                notebook=notebook_name,
                page_num=page_num,
                text=f"ocr-{note_bytes.decode()}-{page_num}",
                checkboxes=[],
                content_type="general",
                timestamp=datetime.now(timezone.utc),
                page_image=None,
            )
            for page_num in pages
        ]

    async def process_file(self, *args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("cloud ledger path should not OCR through process_file")


def _service(tmp_path: Path, reader: FakeReader, allowlist: list[str] | None = None):
    return IngestService(
        config={
            "events_url": "http://events.invalid",
            "state_db_path": str(tmp_path / "state.db"),
            "zai_vision_model": "glm-test-vision",
            "poll_interval": 999,
            "folio_sync_notebooks": [],
            "cloud_change_ledger_notebooks": (
                allowlist if allowlist is not None else ["Quick"]
            ),
        },
        reader=reader,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_cloud_poller_skips_non_allowlisted_notebooks_before_download() -> None:
    changes: list[tuple[str, bytes, int | None]] = []
    entries = [
        {
            "fileName": "Other.note",
            "updateTime": 10,
            "isFolder": "N",
            "size": 10,
        },
        {
            "fileName": "Folder/Quick.note",
            "updateTime": 11,
            "isFolder": "N",
            "size": 10,
        },
    ]
    uploader = ListingUploader(entries, {"Folder/Quick.note": b"quick-bytes"})
    poller = CloudPoller(
        uploader=uploader,
        on_note_changed=lambda *args: _record_change(changes, *args),
        watched_notebooks=["Quick"],
        process_existing_on_start=True,
    )

    await poller._poll_once()

    assert uploader.downloaded == ["Folder/Quick.note"]
    assert changes == [("Quick", b"quick-bytes", 11)]


@pytest.mark.asyncio
async def test_cloud_ingest_records_ledger_and_ocrs_only_new_or_updated_pages(
    tmp_path: Path,
) -> None:
    reader = FakeReader(
        {
            b"rev1": [("p0", "h0"), ("p1", "h1")],
            b"rev2": [("p0", "h0-updated"), ("p1", "h1"), ("p2", "h2")],
            b"rev2-copy": [("p0", "h0-updated"), ("p1", "h1"), ("p2", "h2")],
        }
    )
    service = _service(tmp_path, reader)

    await service._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    initial_cursor = service.ledger.latest_change_id("Quick")
    assert initial_cursor is not None
    await service._on_cloud_note_changed("Quick", b"rev2", update_time=200)
    await service._on_cloud_note_changed("Quick", b"rev2-copy", update_time=300)

    assert reader.ocr_calls == [
        (b"rev1", "Quick", (0, 1)),
        (b"rev2", "Quick", (0, 2)),
    ]
    rows = service.page_state.list_pages("Quick")
    assert {row.page: row.raw_text for row in rows} == {
        0: "ocr-rev2-0",
        1: "ocr-rev1-1",
        2: "ocr-rev2-2",
    }

    second_revision_changes = service.ledger.changes_since("Quick", initial_cursor)
    assert sorted(change.change_type for change in second_revision_changes) == [
        CHANGE_ADDED,
        CHANGE_UPDATED,
    ]
    assert {change.page_id for change in second_revision_changes} == {"p0", "p2"}


@pytest.mark.asyncio
async def test_cloud_ingest_records_reorder_without_ocr(tmp_path: Path) -> None:
    reader = FakeReader(
        {
            b"rev1": [("a", "hA"), ("b", "hB"), ("c", "hC")],
            b"rev2": [("b", "hB"), ("c", "hC"), ("a", "hA")],
        }
    )
    service = _service(tmp_path, reader)

    await service._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    cursor = service.ledger.latest_change_id("Quick")
    assert cursor is not None
    await service._on_cloud_note_changed("Quick", b"rev2", update_time=200)

    assert reader.ocr_calls == [(b"rev1", "Quick", (0, 1, 2))]
    changes = service.ledger.changes_since("Quick", cursor)
    assert [change.change_type for change in changes] == [
        CHANGE_REORDER,
        CHANGE_REORDER,
        CHANGE_REORDER,
    ]
    assert [(change.page_id, change.new_index) for change in changes] == [
        ("a", 2),
        ("b", 0),
        ("c", 1),
    ]

    with sqlite3.connect(tmp_path / "state.db") as conn:
        rows = conn.execute(
            """
            SELECT page_id, page_index, ocr_status
            FROM page_snapshot
            WHERE notebook = ? AND removed = 0
            ORDER BY page_id
            """,
            ("Quick",),
        ).fetchall()
    assert rows == [("a", 2, "ready"), ("b", 0, "ready"), ("c", 1, "ready")]


@pytest.mark.asyncio
async def test_cloud_ingest_skips_non_allowlisted_notebook_before_parse_or_ocr(
    tmp_path: Path,
) -> None:
    reader = FakeReader({b"rev": [("p0", "h0")]})
    service = _service(tmp_path, reader, allowlist=["Quick"])

    await service._on_cloud_note_changed("Other", b"rev", update_time=100)

    assert reader.ocr_calls == []
    assert service.ledger.latest_change_id("Other") is None


# --- OCR scheduler contract -------------------------------------------------


def test_ocr_page_indexes_excludes_reorder_and_removed_changes() -> None:
    """The OCR scheduler targets only added or content-updated pages.

    Reorder and removed changes must never trigger OCR, even when mixed into
    the same diff.  This is the unit contract behind "no OCR for reorder-only
    diffs" and is independent of the full ingest pipeline.
    """
    from paia_supernote.ingest_service import _ocr_page_indexes

    changes = [
        PageChangeRecord(
            change_id=1, notebook="Quick", revision="r", page_id="a",
            change_type=CHANGE_ADDED, old_hash=None, new_hash="hA",
            old_index=None, new_index=0, observed_at="t",
        ),
        PageChangeRecord(
            change_id=2, notebook="Quick", revision="r", page_id="b",
            change_type=CHANGE_UPDATED, old_hash="hB", new_hash="hB2",
            old_index=1, new_index=2, observed_at="t",
        ),
        PageChangeRecord(
            change_id=3, notebook="Quick", revision="r", page_id="c",
            change_type=CHANGE_REORDER, old_hash="hC", new_hash="hC",
            old_index=2, new_index=1, observed_at="t",
        ),
        PageChangeRecord(
            change_id=4, notebook="Quick", revision="r", page_id="d",
            change_type=CHANGE_REMOVED, old_hash="hD", new_hash=None,
            old_index=3, new_index=None, observed_at="t",
        ),
    ]
    assert _ocr_page_indexes(changes) == [0, 2]


@pytest.mark.asyncio
async def test_cloud_ingest_adds_and_reorder_ocrs_only_added_pages(
    tmp_path: Path,
) -> None:
    """A diff with both added and reordered pages OCRs only the added page.

    Proves that adding a page at the front (shifting common pages) does not
    cause spurious OCR on pages that merely moved position.
    """
    reader = FakeReader(
        {
            b"rev1": [("a", "hA"), ("b", "hB")],
            b"rev2": [("c", "hC"), ("b", "hB"), ("a", "hA")],
        }
    )
    service = _service(tmp_path, reader)

    await service._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    cursor = service.ledger.latest_change_id("Quick")
    assert cursor is not None
    await service._on_cloud_note_changed("Quick", b"rev2", update_time=200)

    # rev1 OCRs both pages; rev2 OCRs only the newly added page c (index 0).
    assert reader.ocr_calls == [(b"rev1", "Quick", (0, 1)), (b"rev2", "Quick", (0,))]
    changes = service.ledger.changes_since("Quick", cursor)
    assert sorted(c.change_type for c in changes) == [
        CHANGE_ADDED,
        CHANGE_REORDER,
        CHANGE_REORDER,
    ]
    assert {c.page_id for c in changes} == {"a", "b", "c"}
    # The reordered pages a and b were never re-OCR'd.
    added_change = next(c for c in changes if c.change_type == CHANGE_ADDED)
    assert added_change.page_id == "c"
