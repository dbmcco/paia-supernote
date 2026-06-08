from types import SimpleNamespace

import pytest

from paia_supernote import note_page_ops
from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages


class _FakePage:
    def __init__(self, page_id: str) -> None:
        self.metadata = {
            "PAGEID": page_id,
            "RECOGNTEXT": "123",
            "RECOGNFILE": "456",
            "RECOGNSTATUS": "1",
            "RECOGNFILESTATUS": "1",
        }


class _FakeNotebook:
    def __init__(self, page_ids: list[str]) -> None:
        self.pages = [_FakePage(page_id) for page_id in page_ids]
        self.metadata = SimpleNamespace(pages=[page.metadata for page in self.pages])
        self.titles = []
        self.keywords = []
        self.links = []

    def get_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def get_total_pages(self) -> int:
        return len(self.pages)

    def get_titles(self) -> list:
        return self.titles

    def get_keywords(self) -> list:
        return self.keywords

    def get_links(self) -> list:
        return self.links


class _FakeFooterRecord:
    def __init__(self, page_number: int, metadata: dict | None = None) -> None:
        self.page_number = page_number
        self.metadata = metadata or {}

    def get_page_number(self) -> int:
        return self.page_number

    def set_page_number(self, page_number: int) -> None:
        self.page_number = page_number


def test_copy_pages_to_end_increases_destination_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["source-0", "source-1"])
    target = _FakeNotebook(["target-0"])
    loaded = iter([source, target])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: next(loaded))
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    updated = copy_pages_to_end(b"source", b"target", source_pages=[1])

    assert updated.get_total_pages() == 2
    assert updated.get_page(1).metadata["PAGEID"] == "source-1"
    assert updated.get_page(1).metadata["RECOGNTEXT"] == "0"


def test_copy_pages_to_end_clears_native_star_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["source-0"])
    source.get_page(0).metadata["FIVESTAR"] = "native-star"
    target = _FakeNotebook(["target-0"])
    loaded = iter([source, target])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: next(loaded))
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    updated = copy_pages_to_end(b"source", b"target", source_pages=[0])

    assert updated.get_page(1).metadata["FIVESTAR"] == "0"


def test_remove_pages_decreases_source_count(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _FakeNotebook(["source-0", "source-1", "source-2"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    updated = remove_pages(b"source", pages=[1])

    assert updated.get_total_pages() == 2
    assert [page.metadata["PAGEID"] for page in updated.pages] == [
        "source-0",
        "source-2",
    ]


def test_remove_pages_leaves_blank_placeholder_when_all_pages_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["source-0"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    updated = remove_pages(b"source", pages=[0])

    assert updated.get_total_pages() == 1
    assert updated.get_page(0).metadata["PAGEID"] == "source-0"
    assert updated.get_page(0).metadata["RECOGNTEXT"] == "0"


def test_reorder_pages_reorders_by_page_id_and_syncs_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["page-a", "page-b", "page-c"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    updated = note_page_ops.reorder_pages(
        b"source", page_order=["page-c", "page-a", "page-b"]
    )

    assert [page.metadata["PAGEID"] for page in updated.pages] == [
        "page-c",
        "page-a",
        "page-b",
    ]
    assert updated.metadata.pages == [page.metadata for page in updated.pages]
    assert updated.get_page(0).metadata["RECOGNTEXT"] == "0"


def test_reorder_pages_remaps_heading_keyword_and_link_source_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["page-a", "page-b", "page-c"])
    title = _FakeFooterRecord(0)
    keyword = _FakeFooterRecord(1, {"KEYWORDPAGE": "2"})
    link = _FakeFooterRecord(2, {"PAGEID": "page-a"})
    source.titles = [title]
    source.keywords = [keyword]
    source.links = [link]
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(
        note_page_ops.sn_manip,
        "reconstruct",
        lambda notebook: notebook,
    )

    note_page_ops.reorder_pages(
        b"source", page_order=["page-c", "page-a", "page-b"]
    )

    assert title.get_page_number() == 1
    assert keyword.get_page_number() == 2
    assert keyword.metadata["KEYWORDPAGE"] == 3
    assert link.get_page_number() == 0
    assert link.metadata["PAGEID"] == "page-a"


def test_reorder_pages_rejects_missing_duplicate_or_unknown_page_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["page-a", "page-b", "page-c"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)

    with pytest.raises(ValueError, match="exactly once"):
        note_page_ops.reorder_pages(b"source", page_order=["page-a", "page-b"])

    with pytest.raises(ValueError, match="exactly once"):
        note_page_ops.reorder_pages(
            b"source", page_order=["page-a", "page-b", "page-b"]
        )

    with pytest.raises(ValueError, match="unknown"):
        note_page_ops.reorder_pages(
            b"source", page_order=["page-a", "page-b", "page-x"]
        )
