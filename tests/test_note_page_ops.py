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

    def get_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def get_total_pages(self) -> int:
        return len(self.pages)


def test_copy_pages_to_end_increases_destination_count(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _FakeNotebook(["source-0", "source-1"])
    target = _FakeNotebook(["target-0"])
    loaded = iter([source, target])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: next(loaded))
    monkeypatch.setattr(note_page_ops.sn_manip, "reconstruct", lambda notebook: notebook)

    updated = copy_pages_to_end(b"source", b"target", source_pages=[1])

    assert updated.get_total_pages() == 2
    assert updated.get_page(1).metadata["PAGEID"] == "source-1"
    assert updated.get_page(1).metadata["RECOGNTEXT"] == "0"


def test_remove_pages_decreases_source_count(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _FakeNotebook(["source-0", "source-1", "source-2"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(note_page_ops.sn_manip, "reconstruct", lambda notebook: notebook)

    updated = remove_pages(b"source", pages=[1])

    assert updated.get_total_pages() == 2
    assert [page.metadata["PAGEID"] for page in updated.pages] == ["source-0", "source-2"]


def test_remove_pages_leaves_blank_placeholder_when_all_pages_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeNotebook(["source-0"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(note_page_ops.sn_manip, "reconstruct", lambda notebook: notebook)

    updated = remove_pages(b"source", pages=[0])

    assert updated.get_total_pages() == 1
    assert updated.get_page(0).metadata["PAGEID"] == "source-0"
    assert updated.get_page(0).metadata["RECOGNTEXT"] == "0"
