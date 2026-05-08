from __future__ import annotations

import copy
import os
import tempfile

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
