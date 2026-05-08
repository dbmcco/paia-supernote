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

    page_rles = [
        writer.render_page(spec.agent, chunk)
        for spec in pages
        for chunk in writer.paginate_content(spec.agent, spec.content)
    ]

    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        os.write(fd, base_bytes)
        os.close(fd)
        notebook = sn_parser.load_notebook(path)
        fresh_pages = [writer.build_page(rle) for rle in page_rles]
        notebook.pages = fresh_pages
        notebook.metadata.pages = [page.metadata for page in fresh_pages]
        rebuilt = sn_manip.reconstruct(notebook)
    finally:
        os.unlink(path)

    return rebuilt
