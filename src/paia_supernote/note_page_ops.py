from __future__ import annotations

import copy
import os
import tempfile
from typing import Any

import supernotelib.manipulator as sn_manip
import supernotelib.parser as sn_parser

_OFFSET_FIELDS = (
    "RECOGNTEXT",
    "RECOGNFILE",
    "TOTALPATH",
    "EXTERNALLINKINFO",
    "IDTABLE",
)
_FILING_MARKER_FIELDS = ("FIVESTAR",)


class UnsupportedLinkMetadataError(ValueError):
    """Raised when link footer metadata cannot be remapped during reorder."""


def copy_pages_to_end(
    source_bytes: bytes, target_bytes: bytes, *, source_pages: list[int]
) -> bytes:
    source = _load_from_bytes(source_bytes)
    target = _load_from_bytes(target_bytes)
    _clear_all_recognition_metadata(source)
    _clear_all_recognition_metadata(target)
    for page_index in source_pages:
        page = copy.deepcopy(source.get_page(page_index))
        clear_recognition_metadata(page)
        clear_filing_marker_metadata(page)
        target.pages.append(page)
    _sync_metadata_pages(target)
    return sn_manip.reconstruct(target)


def remove_pages(source_bytes: bytes, *, pages: list[int]) -> bytes:
    source = _load_from_bytes(source_bytes)
    remove_set = set(pages)
    _clear_all_recognition_metadata(source)
    original_pages = list(source.pages)
    source.pages = [
        page
        for page_index, page in enumerate(source.pages)
        if page_index not in remove_set
    ]
    if not source.pages:
        source.pages = [_blank_page_like(original_pages[0])]
    _sync_metadata_pages(source)
    return sn_manip.reconstruct(source)


def reorder_pages(source_bytes: bytes, *, page_order: list[str]) -> bytes:
    source = _load_from_bytes(source_bytes)
    existing_page_ids = [_page_id(page, index) for index, page in enumerate(source.pages)]
    _validate_page_order(existing_page_ids, page_order)

    old_index_by_page_id = {
        page_id: page_index for page_index, page_id in enumerate(existing_page_ids)
    }
    page_by_id = {
        page_id: page for page_id, page in zip(existing_page_ids, source.pages, strict=True)
    }
    new_index_by_old_index = {
        old_index_by_page_id[page_id]: new_index
        for new_index, page_id in enumerate(page_order)
    }

    _clear_all_recognition_metadata(source)
    source.pages = [page_by_id[page_id] for page_id in page_order]
    _remap_footer_page_numbers(source, new_index_by_old_index)
    _sync_metadata_pages(source)
    return sn_manip.reconstruct(source)


def _load_from_bytes(note_bytes: bytes):
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(note_bytes)
        return sn_parser.load_notebook(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _clear_all_recognition_metadata(notebook) -> None:
    for page_index in range(notebook.get_total_pages()):
        clear_recognition_metadata(notebook.get_page(page_index))


def _sync_metadata_pages(notebook) -> None:
    notebook.metadata.pages = [page.metadata for page in notebook.pages]


def _validate_page_order(existing_page_ids: list[str], page_order: list[str]) -> None:
    if len(set(existing_page_ids)) != len(existing_page_ids):
        raise ValueError("notebook contains duplicate PAGEID values")
    if len(set(page_order)) != len(page_order):
        raise ValueError("page_order must include each existing PAGEID exactly once")

    existing = set(existing_page_ids)
    requested = set(page_order)
    unknown = sorted(requested - existing)
    if unknown:
        raise ValueError(f"page_order contains unknown PAGEID values: {', '.join(unknown)}")
    if requested != existing:
        raise ValueError("page_order must include each existing PAGEID exactly once")


def _page_id(page: Any, page_index: int) -> str:
    method = getattr(page, "get_pageid", None)
    page_id = method() if method is not None else None
    if page_id is None:
        page_id = getattr(page, "metadata", {}).get("PAGEID")
    if page_id is None:
        page_id = f"page-{page_index}"
    return str(page_id)


def _remap_footer_page_numbers(
    notebook: Any, new_index_by_old_index: dict[int, int]
) -> None:
    for record in _footer_records(notebook, "get_titles"):
        _remap_footer_record(record, new_index_by_old_index)
    for record in _footer_records(notebook, "get_keywords"):
        new_index = _remap_footer_record(record, new_index_by_old_index)
        if new_index is not None and "KEYWORDPAGE" in getattr(record, "metadata", {}):
            record.metadata["KEYWORDPAGE"] = new_index + 1
    for record in _footer_records(notebook, "get_links"):
        if _remap_footer_record(record, new_index_by_old_index) is None:
            raise UnsupportedLinkMetadataError(
                "link metadata cannot be remapped to a source page"
            )


def _footer_records(notebook: Any, method_name: str) -> list[Any]:
    method = getattr(notebook, method_name, None)
    return list(method() or []) if method is not None else []


def _remap_footer_record(
    record: Any, new_index_by_old_index: dict[int, int]
) -> int | None:
    method = getattr(record, "get_page_number", None)
    if method is None:
        return None
    old_index = int(method())
    new_index = new_index_by_old_index.get(old_index)
    if new_index is None:
        return None
    setter = getattr(record, "set_page_number", None)
    if setter is not None:
        setter(new_index)
    else:
        record.page_number = new_index
    return new_index


def _blank_page_like(page):
    blank = copy.deepcopy(page)
    clear_recognition_metadata(blank)
    if hasattr(blank, "is_layer_supported") and blank.is_layer_supported():
        for layer in blank.get_layers():
            if layer.get_name() != "BGLAYER":
                layer.set_content(b"")
    elif hasattr(blank, "set_content"):
        blank.set_content(b"")
    return blank


def clear_recognition_metadata(page) -> None:
    for key in _OFFSET_FIELDS:
        if key in page.metadata:
            page.metadata[key] = "0"
    page.metadata["RECOGNSTATUS"] = "0"
    page.metadata["RECOGNFILESTATUS"] = "0"


def clear_filing_marker_metadata(page) -> None:
    for key in _FILING_MARKER_FIELDS:
        if key in page.metadata:
            page.metadata[key] = "0"
