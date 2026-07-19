"""
ABOUTME: Notebook page-append helper — wraps RATTA_RLE bytes into a .note file.
ABOUTME: Appends a new page with agent content, then reconstructs the binary.
"""

from __future__ import annotations

import copy
import os
import tempfile
import uuid
from pathlib import Path
from typing import Union

import supernotelib.manipulator as sn_manip
import supernotelib.parser as sn_parser

OFFSET_FIELDS = (
    "RECOGNTEXT",
    "RECOGNFILE",
    "TOTALPATH",
    "EXTERNALLINKINFO",
    "IDTABLE",
)


def clear_recognition_metadata(page) -> None:
    """Zero stale recognition offsets and status fields on a notebook page."""
    for key in OFFSET_FIELDS:
        if key in page.metadata:
            page.metadata[key] = "0"
    page.metadata["RECOGNSTATUS"] = "0"
    page.metadata["RECOGNFILESTATUS"] = "0"


def append_page_to_notebook(
    note_source: Union[str, Path, bytes], ratta_rle_bytes: bytes
) -> bytes:
    """Parse an existing .note file and append a new page containing ratta_rle_bytes.

    Uses sn_parser.load_notebook() which loads layer bitmap content, required for
    reconstruct() to work correctly.

    Args:
        note_source: Either an absolute path to the existing .note file (str or Path),
                     or the raw bytes of the .note file (downloaded from cloud).
        ratta_rle_bytes: RATTA_RLE-encoded bitmap for the new page's MAINLAYER.

    Returns:
        Raw bytes of the modified .note with the new page appended.

    Raises:
        FileNotFoundError: If note_source is a path that does not exist.
    """
    if isinstance(note_source, bytes):
        # Write bytes to a temp file so load_notebook can read from a path.
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(tmp_fd, note_source)
            os.close(tmp_fd)
            return _append_from_path(tmp_path, ratta_rle_bytes)
        finally:
            os.unlink(tmp_path)
    else:
        path = Path(note_source)
        if not path.exists():
            raise FileNotFoundError(f"Note file not found: {note_source}")
        return _append_from_path(str(path), ratta_rle_bytes)


def _append_from_path(note_path: str, ratta_rle_bytes: bytes) -> bytes:
    """Internal: load notebook from path, append page, reconstruct."""
    # load_notebook returns a Notebook with layer content loaded into memory
    notebook = sn_parser.load_notebook(note_path)

    # reconstruct() recalculates layer bitmap offsets but leaves RECOGNTEXT,
    # RECOGNFILE, TOTALPATH etc. as-is. After reconstruction the file layout
    # changes, so ALL existing pages' recognition offsets become dangling
    # pointers — causing the device to close the file immediately on open.
    # Zero them on every page before appending.
    for i in range(notebook.get_total_pages()):
        page = notebook.get_page(i)
        clear_recognition_metadata(page)

    # Template: deepcopy the last page to inherit all metadata fields and structure
    last_idx = notebook.get_total_pages() - 1
    template_page = copy.deepcopy(notebook.get_page(last_idx))

    # Give the new page a fresh unique ID
    new_page_id = str(uuid.uuid4()).replace("-", "").upper()[:32]
    template_page.metadata["PAGEID"] = new_page_id

    # Clear recognition data — it belongs to the original page.
    # set_recogn_*/set_totalpath don't zero the metadata dict entries, so we
    # also zero them directly. Stale byte-offsets in RECOGNTEXT/RECOGNFILE
    # cause the device to close the file immediately on open.
    template_page.set_recogn_file(None)
    template_page.set_recogn_text(None)
    template_page.set_totalpath(None)
    clear_recognition_metadata(template_page)

    # Replace MAINLAYER (layer 0) with our RATTA_RLE bytes
    if template_page.is_layer_supported():
        template_page.get_layer(0).set_content(ratta_rle_bytes)
        # Clear non-background layers so we don't carry over handwriting
        layers = template_page.get_layers()
        for i in range(1, len(layers)):
            layer = layers[i]
            name = layer.get_name()
            if name and name != "BGLAYER":
                layer.set_content(b"")
    else:
        # Legacy format: content stored directly on the page
        template_page.set_content(ratta_rle_bytes)

    # Append the new page — also update notebook.metadata.pages so the header
    # page count stays in sync. Mismatch causes the device to close the file.
    notebook.pages.append(template_page)
    if hasattr(notebook, "metadata") and hasattr(notebook.metadata, "pages"):
        notebook.metadata.pages.append(template_page.metadata)
    return sn_manip.reconstruct(notebook)
