"""
Tests for paia_supernote.writer module.
Tests written following TDD approach.
"""

import pytest
from PIL import Image
from datetime import datetime
from pathlib import Path

from paia_supernote.writer import SupernoteWriter


class TestSupernoteWriter:
    """Test cases for SupernoteWriter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.writer = SupernoteWriter()
        self.test_content = "Hello from the agent!"

    def test_renders_page_for_sam_without_exception(self):
        """Should render a page for Sam agent without raising exception."""
        # This test will fail initially because render_page raises NotImplementedError
        result = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        # Should return bytes representing a .note page
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
        # Render with content
        result_with_content = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        # Render with empty content
        result_empty = self.writer.render_page(
            agent="Sam",
            content="",
        )

        # Content should result in more bytes (different compression)
        assert len(result_with_content) != len(result_empty)

    def test_font_size_is_configurable_constant(self):
        """Should have configurable font size constants."""
        # Font sizes should be accessible as class constants
        assert hasattr(SupernoteWriter, 'BODY_FONT_SIZE')
        assert hasattr(SupernoteWriter, 'DATE_FONT_SIZE')
        assert hasattr(SupernoteWriter, 'SIGNATURE_FONT_SIZE')

        # Should be integers suitable for PIL font sizes
        assert isinstance(SupernoteWriter.BODY_FONT_SIZE, int)
        assert isinstance(SupernoteWriter.DATE_FONT_SIZE, int)
        assert isinstance(SupernoteWriter.SIGNATURE_FONT_SIZE, int)

        # Should be reasonable values
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
        pixels = list(date_region.getdata())
        ink_pixels = sum(1 for p in pixels if p < 128)
        assert ink_pixels > 0, "Expected ink pixels in date region (top-right)"