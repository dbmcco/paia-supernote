from types import SimpleNamespace

import pytest

from paia_supernote import note_page_ops


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
        self.links = []

    def get_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def get_total_pages(self) -> int:
        return len(self.pages)

    def get_links(self) -> list:
        return self.links


class _UnsupportedLinkRecord:
    metadata = {"PAGEID": "page-a"}


def test_note_reorder_module_reorders_notebook_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paia_supernote import note_reorder

    source = _FakeNotebook(["page-a", "page-b"])
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(note_page_ops.sn_manip, "reconstruct", lambda notebook: notebook)

    updated = note_reorder.reorder_pages(b"source", page_order=["page-b", "page-a"])

    assert [page.metadata["PAGEID"] for page in updated.pages] == ["page-b", "page-a"]
    assert updated.metadata.pages == [page.metadata for page in updated.pages]


def test_reorder_blocks_unsupported_link_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paia_supernote import note_reorder

    source = _FakeNotebook(["page-a", "page-b"])
    source.links = [_UnsupportedLinkRecord()]
    monkeypatch.setattr(note_page_ops, "_load_from_bytes", lambda _bytes: source)
    monkeypatch.setattr(note_page_ops.sn_manip, "reconstruct", lambda notebook: notebook)

    with pytest.raises(note_reorder.UnsupportedLinkMetadataError, match="link metadata"):
        note_reorder.reorder_pages(b"source", page_order=["page-b", "page-a"])
