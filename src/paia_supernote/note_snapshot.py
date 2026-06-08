from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MetadataRecord:
    kind: str
    page_id: str
    page_index: int
    metadata: dict[str, Any]
    content: bytes | None = None
    label: str | None = None


@dataclass(slots=True)
class LinkRecord:
    kind: str
    page_id: str
    page_index: int
    metadata: dict[str, Any]
    content: bytes | None = None
    target_page_id: str | None = None


@dataclass(slots=True)
class PageRecord:
    page_id: str
    page_index: int
    starred: bool
    page_metadata: dict[str, Any]
    content_hash: str
    image_width: int | None = None
    image_height: int | None = None
    headings: list[MetadataRecord] = field(default_factory=list)
    keywords: list[MetadataRecord] = field(default_factory=list)
    outgoing_links: list[LinkRecord] = field(default_factory=list)
    incoming_links: list[LinkRecord] = field(default_factory=list)


@dataclass(slots=True)
class NoteMetadataIndex:
    headings_by_page_id: dict[str, list[MetadataRecord]]
    keywords_by_page_id: dict[str, list[MetadataRecord]]
    links_by_page_id: dict[str, list[LinkRecord]]
    stars_by_page_id: dict[str, bool]


@dataclass(slots=True)
class NotebookSnapshot:
    notebook_name: str
    revision: str
    page_order: list[str]
    pages: dict[str, PageRecord]
    metadata: NoteMetadataIndex


def build_snapshot_from_notebook(
    notebook: Any,
    *,
    notebook_name: str,
    revision: str,
) -> NotebookSnapshot:
    pages: dict[str, PageRecord] = {}
    page_order: list[str] = []
    width = _call_or_none(notebook, "get_width")
    height = _call_or_none(notebook, "get_height")

    for page_index in range(notebook.get_total_pages()):
        page = notebook.get_page(page_index)
        page_id = _page_id(page, page_index)
        page_order.append(page_id)
        page_metadata = copy.deepcopy(getattr(page, "metadata", {}))
        pages[page_id] = PageRecord(
            page_id=page_id,
            page_index=page_index,
            starred=_is_starred(page_metadata),
            page_metadata=page_metadata,
            content_hash=_content_hash(page),
            image_width=width,
            image_height=height,
        )

    _attach_headings(notebook, page_order, pages)
    _attach_keywords(notebook, page_order, pages)
    _attach_links(notebook, page_order, pages)

    return NotebookSnapshot(
        notebook_name=notebook_name,
        revision=revision,
        page_order=page_order,
        pages=pages,
        metadata=NoteMetadataIndex(
            headings_by_page_id={page_id: page.headings for page_id, page in pages.items()},
            keywords_by_page_id={page_id: page.keywords for page_id, page in pages.items()},
            links_by_page_id={page_id: page.outgoing_links for page_id, page in pages.items()},
            stars_by_page_id={page_id: page.starred for page_id, page in pages.items()},
        ),
    )


def _attach_headings(
    notebook: Any,
    page_order: list[str],
    pages: dict[str, PageRecord],
) -> None:
    for title in _collection(notebook, "get_titles"):
        page_index = _metadata_page_index(title)
        page_id = _page_id_at(page_order, page_index)
        if page_id is None:
            continue
        record = MetadataRecord(
            kind="heading",
            page_id=page_id,
            page_index=page_index,
            metadata=copy.deepcopy(getattr(title, "metadata", {})),
            content=_call_or_none(title, "get_content"),
        )
        pages[page_id].headings.append(record)


def _attach_keywords(
    notebook: Any,
    page_order: list[str],
    pages: dict[str, PageRecord],
) -> None:
    for keyword in _collection(notebook, "get_keywords"):
        page_index = _metadata_page_index(keyword)
        page_id = _page_id_at(page_order, page_index)
        if page_id is None:
            continue
        label = _call_or_none(keyword, "get_keyword")
        record = MetadataRecord(
            kind="keyword",
            page_id=page_id,
            page_index=page_index,
            metadata=copy.deepcopy(getattr(keyword, "metadata", {})),
            content=_call_or_none(keyword, "get_content"),
            label=str(label) if label is not None else None,
        )
        pages[page_id].keywords.append(record)


def _attach_links(
    notebook: Any,
    page_order: list[str],
    pages: dict[str, PageRecord],
) -> None:
    for link in _collection(notebook, "get_links"):
        page_index = _metadata_page_index(link)
        page_id = _page_id_at(page_order, page_index)
        if page_id is None:
            continue
        target_page_id = _call_or_none(link, "get_pageid")
        record = LinkRecord(
            kind="link",
            page_id=page_id,
            page_index=page_index,
            metadata=copy.deepcopy(getattr(link, "metadata", {})),
            content=_call_or_none(link, "get_content"),
            target_page_id=str(target_page_id) if target_page_id is not None else None,
        )
        pages[page_id].outgoing_links.append(record)
        if record.target_page_id in pages:
            pages[record.target_page_id].incoming_links.append(record)


def _metadata_page_index(record: Any) -> int:
    page_number = _call_or_none(record, "get_page_number")
    return int(page_number) if page_number is not None else -1


def _page_id(page: Any, page_index: int) -> str:
    page_id = _call_or_none(page, "get_pageid")
    if page_id is None:
        page_id = getattr(page, "metadata", {}).get("PAGEID")
    if page_id is None:
        return f"page-{page_index}"
    return str(page_id)


def _page_id_at(page_order: list[str], page_index: int) -> str | None:
    if page_index < 0 or page_index >= len(page_order):
        return None
    return page_order[page_index]


def _is_starred(metadata: dict[str, Any]) -> bool:
    value = metadata.get("FIVESTAR")
    return bool(value and str(value).strip() not in {"0", "[]", "None", "none"})


def _content_hash(page: Any) -> str:
    digest = hashlib.sha256()
    for chunk in _page_content_chunks(page):
        digest.update(chunk)
    return digest.hexdigest()


def _page_content_chunks(page: Any) -> list[bytes]:
    if hasattr(page, "is_layer_supported") and page.is_layer_supported():
        chunks: list[bytes] = []
        for layer in _call_or_none(page, "get_layers") or []:
            content = _call_or_none(layer, "get_content")
            if content:
                chunks.append(bytes(content))
        return chunks
    content = _call_or_none(page, "get_content")
    return [bytes(content)] if content else []


def _collection(notebook: Any, name: str) -> list[Any]:
    value = _call_or_none(notebook, name)
    return list(value or [])


def _call_or_none(obj: Any, name: str) -> Any:
    method = getattr(obj, name, None)
    if method is None:
        return None
    return method()
