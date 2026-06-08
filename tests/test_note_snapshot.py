from __future__ import annotations

from types import SimpleNamespace

import pytest


def _snapshot_module():
    try:
        from paia_supernote import note_snapshot
    except ModuleNotFoundError as exc:
        pytest.fail(f"expected note_snapshot module to exist: {exc}")
    return note_snapshot


class _FakePage:
    def __init__(
        self,
        page_id: str,
        *,
        starred: bool = False,
        content: bytes | None = None,
    ) -> None:
        self.metadata = {"PAGEID": page_id}
        if starred:
            self.metadata["FIVESTAR"] = "native-star"
        self.content = content

    def get_content(self) -> bytes | None:
        return self.content

    def is_layer_supported(self) -> bool:
        return False


class _FakeTitle:
    def __init__(self, page_number: int, label: str) -> None:
        self.metadata = {"TITLE": label, "TITLERECTORI": "10,20,30,40"}
        self.content = label.encode()
        self._page_number = page_number

    def get_page_number(self) -> int:
        return self._page_number

    def get_content(self) -> bytes:
        return self.content


class _FakeKeyword:
    def __init__(self, page_number: int, keyword: str) -> None:
        self.metadata = {
            "KEYWORD": keyword,
            "KEYWORDPAGE": str(page_number + 1),
            "KEYWORDRECT": "1,2,3,4",
        }
        self.content = keyword.encode()
        self._page_number = page_number

    def get_page_number(self) -> int:
        return self._page_number

    def get_content(self) -> bytes:
        return self.content

    def get_keyword(self) -> str:
        return str(self.metadata["KEYWORD"])


class _FakeLink:
    def __init__(self, page_number: int, target_page_id: str) -> None:
        self.metadata = {
            "LINKTYPE": "0",
            "LINKINOUT": "0",
            "LINKRECT": "5,6,7,8",
            "PAGEID": target_page_id,
        }
        self.content = b"link"
        self._page_number = page_number

    def get_page_number(self) -> int:
        return self._page_number

    def get_content(self) -> bytes:
        return self.content

    def get_pageid(self) -> str:
        return str(self.metadata["PAGEID"])


class _FakeNotebook:
    def __init__(self) -> None:
        self.pages = [
            _FakePage("page-a", content=b"alpha"),
            _FakePage("page-b", starred=True, content=b"bravo"),
        ]
        self.metadata = SimpleNamespace(pages=[page.metadata for page in self.pages])
        self.titles = [_FakeTitle(1, "Project heading")]
        self.keywords = [_FakeKeyword(0, "strategy")]
        self.links = [_FakeLink(1, "page-a")]

    def get_total_pages(self) -> int:
        return len(self.pages)

    def get_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def get_titles(self) -> list[_FakeTitle]:
        return self.titles

    def get_keywords(self) -> list[_FakeKeyword]:
        return self.keywords

    def get_links(self) -> list[_FakeLink]:
        return self.links

    def get_width(self) -> int:
        return 1404

    def get_height(self) -> int:
        return 1872


def test_snapshot_keys_pages_by_stable_page_id() -> None:
    note_snapshot = _snapshot_module()
    notebook = _FakeNotebook()

    snapshot = note_snapshot.build_snapshot_from_notebook(
        notebook,
        notebook_name="LFW",
        revision="rev-1",
    )

    assert snapshot.notebook_name == "LFW"
    assert snapshot.revision == "rev-1"
    assert snapshot.page_order == ["page-a", "page-b"]
    assert list(snapshot.pages) == ["page-a", "page-b"]
    assert snapshot.pages["page-a"].page_index == 0
    assert snapshot.pages["page-b"].page_index == 1
    assert snapshot.pages["page-b"].starred is True
    assert snapshot.pages["page-a"].image_width == 1404
    assert snapshot.pages["page-a"].image_height == 1872
    assert snapshot.pages["page-a"].content_hash != snapshot.pages["page-b"].content_hash


def test_snapshot_groups_native_metadata_by_page_id() -> None:
    note_snapshot = _snapshot_module()
    snapshot = note_snapshot.build_snapshot_from_notebook(
        _FakeNotebook(),
        notebook_name="LFW",
        revision="rev-1",
    )

    assert snapshot.pages["page-b"].headings[0].kind == "heading"
    assert snapshot.pages["page-b"].headings[0].metadata["TITLE"] == "Project heading"
    assert snapshot.pages["page-a"].keywords[0].kind == "keyword"
    assert snapshot.pages["page-a"].keywords[0].label == "strategy"
    assert snapshot.pages["page-b"].outgoing_links[0].target_page_id == "page-a"
    assert snapshot.pages["page-a"].incoming_links[0].page_id == "page-b"


def test_snapshot_does_not_mutate_page_metadata() -> None:
    note_snapshot = _snapshot_module()
    notebook = _FakeNotebook()
    original_metadata = [dict(page.metadata) for page in notebook.pages]

    snapshot = note_snapshot.build_snapshot_from_notebook(
        notebook,
        notebook_name="LFW",
        revision="rev-1",
    )
    snapshot.pages["page-a"].page_metadata["PAGEID"] = "changed"

    assert [page.metadata for page in notebook.pages] == original_metadata
