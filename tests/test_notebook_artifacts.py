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
_MANTA_NOTE_FIXTURE = _SUPERNOTE_DIR / "20260612_120652.note"
_has_note_fixture = _NOTE_FIXTURE.exists()
_has_manta_note_fixture = _MANTA_NOTE_FIXTURE.exists()

needs_fixture = pytest.mark.skipif(
    not _has_note_fixture, reason="No .note fixture file available"
)
needs_manta_fixture = pytest.mark.skipif(
    not _has_manta_note_fixture, reason="No Manta .note fixture file available"
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

    def test_long_page_content_spills_into_continuation_pages(self) -> None:
        content = "\n".join(
            f"Brief line {index}: {'meeting ' * 10}".strip()
            for index in range(80)
        )
        pages = [NotebookPageSpec(agent="Sam", content=content)]

        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)

        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() > 1

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

    def test_rebuilds_pages_with_fresh_page_ids(self) -> None:
        original = supernotelib.load(io.BytesIO(self.base_bytes))
        original_ids = {
            original.get_page(index).metadata.get("PAGEID")
            for index in range(original.get_total_pages())
        }

        pages = [NotebookPageSpec(agent="Sam", content="Fresh page")]
        result = replace_notebook_pages(self.base_bytes, writer=self.writer, pages=pages)

        rebuilt = supernotelib.load(io.BytesIO(result))
        rebuilt_ids = {
            rebuilt.get_page(index).metadata.get("PAGEID")
            for index in range(rebuilt.get_total_pages())
        }

        assert rebuilt_ids
        assert rebuilt_ids.isdisjoint(original_ids)


@needs_manta_fixture
class TestReplaceMantaNotebookPages:
    def test_replacement_uses_manta_canvas_and_page_style(self) -> None:
        from paia_supernote import ratta_rle

        writer = SupernoteWriter()
        result = replace_notebook_pages(
            _MANTA_NOTE_FIXTURE.read_bytes(),
            writer=writer,
            pages=[NotebookPageSpec(agent="Sam", content="Manta format check")],
        )

        nb = supernotelib.load(io.BytesIO(result))
        page = nb.get_page(0)
        mainlayer = page.get_layer(0)

        assert nb.get_width() == 1920
        assert nb.get_height() == 2560
        assert page.get_style() == "style_white_a5x2"
        decoded = ratta_rle.decode(mainlayer.get_content(), nb.get_width(), nb.get_height())
        assert decoded.size == (1920, 2560)
