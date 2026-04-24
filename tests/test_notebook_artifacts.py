# ABOUTME: Tests for notebook_artifacts module — stable page replacement publishing.
# ABOUTME: Validates replace_notebook_pages and NotebookPageSpec against real .note files.

from __future__ import annotations

import io
from pathlib import Path

import pytest
import supernotelib

from paia_supernote.notebook_artifacts import NotebookPageSpec, replace_notebook_pages
from paia_supernote.writer import SupernoteWriter

_SUPERNOTE_DIR = (
    Path.home()
    / "Library/Containers/com.ratta.supernote/Data/Library/Application Support"
    / "com.ratta.supernote/908410628964298752/Supernote/Note"
)
_NOTE_FIXTURE = _SUPERNOTE_DIR / "Personal.note"
_has_note_fixture = _NOTE_FIXTURE.exists()

needs_fixture = pytest.mark.skipif(
    not _has_note_fixture, reason="No .note fixture file available"
)


class TestNotebookPageSpec:
    def test_dataclass_fields(self) -> None:
        spec = NotebookPageSpec(agent="Sam", content="Hello")
        assert spec.agent == "Sam"
        assert spec.content == "Hello"

    def test_slots(self) -> None:
        spec = NotebookPageSpec(agent="Sam", content="Hello")
        with pytest.raises(AttributeError):
            spec.extra = "nope"  # type: ignore[attr-defined]


@needs_fixture
class TestReplaceNotebookPages:
    def setup_method(self) -> None:
        self.writer = SupernoteWriter()
        self.base_bytes = _NOTE_FIXTURE.read_bytes()

    def test_returns_valid_notebook_bytes(self) -> None:
        pages = [NotebookPageSpec(agent="Sam", content="Page one")]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() > 0

    def test_replaces_to_exact_page_count(self) -> None:
        pages = [
            NotebookPageSpec(agent="Sam", content="A"),
            NotebookPageSpec(agent="Caroline", content="B"),
            NotebookPageSpec(agent="Ingrid", content="C"),
        ]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() == 3

    def test_single_page_replacement(self) -> None:
        pages = [NotebookPageSpec(agent="Sam", content="Only page")]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() == 1

    def test_mainlayer_has_content_after_replace(self) -> None:
        pages = [NotebookPageSpec(agent="Caroline", content="Check layer")]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)
        nb = supernotelib.load(io.BytesIO(result))
        page = nb.get_page(0)
        mainlayer = page.get_layer(0)
        assert mainlayer.get_content() is not None
        assert len(mainlayer.get_content()) > 0

    def test_raises_on_empty_pages(self) -> None:
        with pytest.raises(ValueError, match="pages must not be empty"):
            replace_notebook_pages(self.base_bytes, writer=self.writer, pages=[])

    def test_more_pages_than_existing_appends_extras(self) -> None:
        original = supernotelib.load(io.BytesIO(self.base_bytes))
        original_count = original.get_total_pages()
        extra = original_count + 2
        pages = [
            NotebookPageSpec(agent="Sam", content=f"Page {i}") for i in range(extra)
        ]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() == extra
