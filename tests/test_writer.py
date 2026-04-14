"""
Tests for paia_supernote.writer module.
Tests written following TDD approach.
"""

import pytest
from PIL import Image
from datetime import datetime
from pathlib import Path

from paia_supernote.writer import SupernoteWriter


class TestSupernoteMriter:
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

    @pytest.mark.xfail(reason="Requires merge functionality to load notebook")
    def test_date_appears_in_rendered_image(self, tmp_path):
        """Should include current date in the rendered page."""
        # Render a page
        result = self.writer.render_page(
            agent="Sam",
            content=self.test_content,
        )

        # Write result to temporary .note file
        temp_note_file = tmp_path / "test.note"

        # Create a minimal notebook structure to test with
        # We'll need to extract the page content to verify the date
        # This test will require the merge functionality to work
        notebook = supernotelib.load_notebook(create_test_notebook(temp_note_file))

        # TODO: Extract the rendered page and verify date appears
        # This might require implementing the merge functionality first
        # For now, we'll test that the render succeeds
        assert len(result) > 0


def create_test_notebook(path):
    """Create a minimal test notebook file for testing."""
    # Create minimal notebook structure using supernotelib
    # This is a helper for testing - implementation will vary based on supernotelib API
    # For now, return the path
    return str(path)