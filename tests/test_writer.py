"""
Tests for paia_supernote.writer module.
Tests written following TDD approach.
"""

import io
from pathlib import Path

import pytest
import supernotelib

from paia_supernote.writer import SupernoteWriter


# Path to a real .note file for integration tests (Supernote Partner app sync)
_SUPERNOTE_DIR = Path.home() / "Library/Containers/com.ratta.supernote/Data/Library/Application Support/com.ratta.supernote/908410628964298752/Supernote/Note"
_NOTE_FIXTURE = _SUPERNOTE_DIR / "Personal.note"
_has_note_fixture = _NOTE_FIXTURE.exists()


class TestSupernoteWriter:
    """Test cases for SupernoteWriter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.writer = SupernoteWriter()
        self.test_content = "Hello from the agent!"

    def test_renders_page_for_sam_without_exception(self):
        """Should render a page for Sam agent without raising exception."""
        result = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        # Should return bytes representing RATTA_RLE encoded bitmap
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_renders_page_for_caroline_without_exception(self):
        """Should render a page for Caroline agent without raising exception."""
        result = self.writer.render_page(
            agent="Caroline",
            content=self.test_content,
        )

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_renders_page_for_ingrid_without_exception(self):
        """Should render a page for Ingrid agent without raising exception."""
        result = self.writer.render_page(
            agent="Ingrid",
            content=self.test_content,
        )

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_output_contains_more_bytes_than_empty_page(self):
        """Should produce more bytes when content is present vs empty."""
        result_with_content = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        result_empty = self.writer.render_page(
            agent="Sam",
            content="",
        )

        # Content should result in more bytes (different compression)
        assert len(result_with_content) != len(result_empty)

    def test_font_size_is_configurable_constant(self):
        """Should have configurable font size constants."""
        assert hasattr(SupernoteWriter, 'BODY_FONT_SIZE')
        assert hasattr(SupernoteWriter, 'DATE_FONT_SIZE')
        assert hasattr(SupernoteWriter, 'SIGNATURE_FONT_SIZE')

        assert isinstance(SupernoteWriter.BODY_FONT_SIZE, int)
        assert isinstance(SupernoteWriter.DATE_FONT_SIZE, int)
        assert isinstance(SupernoteWriter.SIGNATURE_FONT_SIZE, int)

        assert SupernoteWriter.BODY_FONT_SIZE > 0
        assert SupernoteWriter.DATE_FONT_SIZE > 0
        assert SupernoteWriter.SIGNATURE_FONT_SIZE > 0

    def test_date_appears_in_rendered_image(self):
        """Should include current date in the rendered page (date top-right region has ink)."""
        from paia_supernote import ratta_rle

        result = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        # Decode the RLE bytes back to a PIL image
        img = ratta_rle.decode(
            result,
            SupernoteWriter.DEVICE_WIDTH,
            SupernoteWriter.DEVICE_HEIGHT,
        )

        # The date is rendered top-right: x near DEVICE_WIDTH - MARGIN, y near MARGIN
        # Check the top-right quadrant for ink pixels (value < 128 = dark)
        margin = SupernoteWriter.MARGIN
        date_region = img.crop((
            SupernoteWriter.DEVICE_WIDTH // 2,  # right half
            0,
            SupernoteWriter.DEVICE_WIDTH,
            margin + 60,  # date area height
        ))
        pixels = list(date_region.get_flattened_data())
        ink_pixels = sum(1 for p in pixels if p < 128)
        assert ink_pixels > 0, "Expected ink pixels in date region (top-right)"


class TestBuildPage:
    """Tests for MAINLAYER page construction."""

    def setup_method(self):
        self.writer = SupernoteWriter()

    def test_build_page_returns_supernotelib_page(self):
        """build_page should return a supernotelib.Page with MAINLAYER set."""
        rle = self.writer.render_page("Sam", "test content")
        page = self.writer.build_page(rle)

        assert isinstance(page, supernotelib.Page)

    def test_build_page_mainlayer_has_content(self):
        """The MAINLAYER (layer 0) should contain the RLE bitmap."""
        rle = self.writer.render_page("Sam", "test content")
        page = self.writer.build_page(rle)

        mainlayer = page.get_layer(0)
        assert mainlayer.get_name() == "MAINLAYER"
        assert mainlayer.get_protocol() == "RATTA_RLE"
        assert mainlayer.get_content() == rle

    def test_build_page_has_valid_page_id(self):
        """Page should have a unique PAGEID starting with 'P'."""
        rle = self.writer.render_page("Sam", "test")
        page = self.writer.build_page(rle)

        page_id = page.get_pageid()
        assert page_id is not None
        assert page_id.startswith("P")
        assert len(page_id) > 20

    def test_build_page_orientation_vertical(self):
        """Page should be vertically oriented (A5X portrait)."""
        rle = self.writer.render_page("Sam", "test")
        page = self.writer.build_page(rle)

        assert page.get_orientation() == supernotelib.Page.ORIENTATION_VERTICAL

    def test_build_page_style_white(self):
        """Page should use the standard white background style."""
        rle = self.writer.render_page("Sam", "test")
        page = self.writer.build_page(rle)

        assert page.get_style() == "style_white"


class TestAppendToNotebook:
    """Integration tests for appending pages to real .note notebooks."""

    def setup_method(self):
        self.writer = SupernoteWriter()

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_append_returns_valid_note_bytes(self):
        """append_to_notebook should return bytes parseable by supernotelib."""
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        result = self.writer.append_to_notebook(notebook_bytes, "Sam", "Hello from Sam")

        assert isinstance(result, bytes)
        # Verify it's a valid notebook
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() > 0

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_append_increases_page_count(self):
        """Appending should add exactly one page to the notebook."""
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        original = supernotelib.load(io.BytesIO(notebook_bytes))
        original_pages = original.get_total_pages()

        result = self.writer.append_to_notebook(notebook_bytes, "Caroline", "Test")
        modified = supernotelib.load(io.BytesIO(result))

        assert modified.get_total_pages() == original_pages + 1

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_appended_page_has_mainlayer_content(self):
        """The appended page's MAINLAYER should contain rendered bitmap data."""
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        result = self.writer.append_to_notebook(notebook_bytes, "Ingrid", "Test content")

        nb = supernotelib.load(io.BytesIO(result))
        last_page = nb.get_page(nb.get_total_pages() - 1)
        mainlayer = last_page.get_layer(0)

        assert mainlayer.get_name() == "MAINLAYER"
        assert mainlayer.get_content() is not None
        assert len(mainlayer.get_content()) > 0


class TestAppendRlePage:
    """Tests for append_rle_page — append raw RLE bytes to a notebook."""

    def setup_method(self):
        self.writer = SupernoteWriter()

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_append_rle_page_returns_valid_bytes(self):
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        rle = self.writer.render_page("Sam", "RLE append test")
        result = self.writer.append_rle_page(notebook_bytes, rle)
        assert isinstance(result, bytes)
        nb = supernotelib.load(io.BytesIO(result))
        assert nb.get_total_pages() > 0

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_append_rle_page_increases_page_count(self):
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        original = supernotelib.load(io.BytesIO(notebook_bytes))
        original_pages = original.get_total_pages()

        rle = self.writer.render_page("Caroline", "Extra page")
        result = self.writer.append_rle_page(notebook_bytes, rle)
        modified = supernotelib.load(io.BytesIO(result))
        assert modified.get_total_pages() == original_pages + 1

    @pytest.mark.skipif(not _has_note_fixture, reason="No .note fixture file available")
    def test_append_rle_page_last_page_has_content(self):
        notebook_bytes = _NOTE_FIXTURE.read_bytes()
        rle = self.writer.render_page("Ingrid", "Layer check")
        result = self.writer.append_rle_page(notebook_bytes, rle)

        nb = supernotelib.load(io.BytesIO(result))
        last_page = nb.get_page(nb.get_total_pages() - 1)
        mainlayer = last_page.get_layer(0)
        assert mainlayer.get_content() == rle