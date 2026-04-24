# ABOUTME: Notebook artifact publishing functionality for Daily Walk
# ABOUTME: Provides stable page replacement rather than append-only writes

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import supernotelib.manipulator as sn_manip
import supernotelib.parser as sn_parser


@dataclass(slots=True)
class NotebookPageSpec:
    agent: str
    content: str


def replace_notebook_pages(base_bytes: bytes, *, writer, pages: list[NotebookPageSpec]) -> bytes:
    """Replace all pages in a notebook with the specified page specs."""
    if not pages:
        raise ValueError("pages must not be empty")

    page_rles = [writer.render_page(spec.agent, spec.content) for spec in pages]

    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        os.write(fd, base_bytes)
        os.close(fd)
        notebook = sn_parser.load_notebook(path)
        existing = notebook.get_total_pages()

        # Replace existing pages with new content
        for index, rle in enumerate(page_rles[:existing]):
            page = notebook.get_page(index)
            page.get_layer(0).set_content(rle)

        # Remove extra pages if we have fewer new pages than existing
        if existing > len(page_rles):
            notebook.pages = notebook.pages[:len(page_rles)]
            notebook.metadata.pages = notebook.metadata.pages[:len(page_rles)]

        rebuilt = sn_manip.reconstruct(notebook)
    finally:
        os.unlink(path)

    # Add any additional pages beyond the existing page count
    for rle in page_rles[existing:]:
        rebuilt = writer.append_rle_page(rebuilt, rle)

    return rebuilt