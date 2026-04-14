"""
ABOUTME: Tests for notebook_writer module — append_page_to_notebook accepts path or bytes.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paia_supernote.notebook_writer import append_page_to_notebook


FAKE_RLE = b"\xff" * 100  # placeholder RATTA_RLE bytes


def _make_mock_notebook(tmp_path: Path) -> str:
    """Create a temp file that looks like a .note path for mocking purposes."""
    note_file = tmp_path / "fake.note"
    note_file.write_bytes(b"fake content")
    return str(note_file)


def _mock_load_notebook(path: str):
    """Return a fake Notebook object with one page."""
    mock_layer = MagicMock()
    mock_layer.get_name.return_value = "MAINLAYER"

    mock_page = MagicMock()
    mock_page.metadata = {"PAGEID": "OLDID", "RECOGNSTATUS": "0"}
    mock_page.is_layer_supported.return_value = True
    mock_page.get_layer.return_value = mock_layer
    mock_page.get_layers.return_value = [mock_layer]
    mock_page.RECOGNSTATUS_NONE = 0

    mock_notebook = MagicMock()
    mock_notebook.get_total_pages.return_value = 1
    mock_notebook.get_page.return_value = mock_page
    mock_notebook.pages = [mock_page]
    return mock_notebook


class TestAppendPageToNotebook:
    """Tests for append_page_to_notebook."""

    def test_raises_file_not_found_for_missing_path(self, tmp_path):
        """FileNotFoundError raised when path does not exist."""
        with pytest.raises(FileNotFoundError):
            append_page_to_notebook(str(tmp_path / "missing.note"), FAKE_RLE)

    def test_raises_file_not_found_for_missing_path_object(self, tmp_path):
        """FileNotFoundError raised when Path object does not exist."""
        with pytest.raises(FileNotFoundError):
            append_page_to_notebook(tmp_path / "missing.note", FAKE_RLE)

    def test_accepts_file_path_as_string(self, tmp_path):
        """Accepts a string file path and calls load_notebook with it."""
        note_path = _make_mock_notebook(tmp_path)
        mock_nb = _mock_load_notebook(note_path)

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   return_value=mock_nb) as mock_load, \
             patch("paia_supernote.notebook_writer.sn_manip.reconstruct",
                   return_value=b"reconstructed"):
            result = append_page_to_notebook(note_path, FAKE_RLE)

        mock_load.assert_called_once_with(note_path)
        assert result == b"reconstructed"

    def test_accepts_file_path_as_path_object(self, tmp_path):
        """Accepts a Path object and converts to string for load_notebook."""
        note_path = Path(_make_mock_notebook(tmp_path))
        mock_nb = _mock_load_notebook(str(note_path))

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   return_value=mock_nb), \
             patch("paia_supernote.notebook_writer.sn_manip.reconstruct",
                   return_value=b"reconstructed"):
            result = append_page_to_notebook(note_path, FAKE_RLE)

        assert result == b"reconstructed"

    def test_accepts_bytes_and_writes_temp_file(self, tmp_path):
        """When bytes passed, writes to temp file, loads, cleans up."""
        note_bytes = b"fake note bytes"
        mock_nb = _mock_load_notebook("/tmp/fake.note")

        captured_paths = []

        def capturing_load(path):
            captured_paths.append(path)
            assert os.path.exists(path), "Temp file must exist during load"
            return mock_nb

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   side_effect=capturing_load), \
             patch("paia_supernote.notebook_writer.sn_manip.reconstruct",
                   return_value=b"reconstructed"):
            result = append_page_to_notebook(note_bytes, FAKE_RLE)

        assert result == b"reconstructed"
        assert len(captured_paths) == 1
        # Temp file must be cleaned up after the call
        assert not os.path.exists(captured_paths[0])

    def test_bytes_temp_file_cleaned_up_on_error(self):
        """Temp file is removed even when load_notebook raises."""
        note_bytes = b"fake note bytes"
        captured_paths = []

        def failing_load(path):
            captured_paths.append(path)
            raise RuntimeError("load failed")

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   side_effect=failing_load):
            with pytest.raises(RuntimeError, match="load failed"):
                append_page_to_notebook(note_bytes, FAKE_RLE)

        assert len(captured_paths) == 1
        assert not os.path.exists(captured_paths[0])

    def test_new_page_gets_unique_id(self, tmp_path):
        """Appended page should have a different PAGEID from the template."""
        note_path = _make_mock_notebook(tmp_path)
        mock_nb = _mock_load_notebook(note_path)
        original_id = mock_nb.get_page(0).metadata["PAGEID"]

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   return_value=mock_nb), \
             patch("paia_supernote.notebook_writer.sn_manip.reconstruct",
                   return_value=b"ok"):
            append_page_to_notebook(note_path, FAKE_RLE)

        appended_page = mock_nb.pages[-1]
        assert appended_page.metadata["PAGEID"] != original_id

    def test_mainlayer_set_to_rle_bytes(self, tmp_path):
        """Layer 0 set_content called with the provided RATTA_RLE bytes."""
        note_path = _make_mock_notebook(tmp_path)
        mock_nb = _mock_load_notebook(note_path)

        with patch("paia_supernote.notebook_writer.sn_parser.load_notebook",
                   return_value=mock_nb), \
             patch("paia_supernote.notebook_writer.sn_manip.reconstruct",
                   return_value=b"ok"):
            append_page_to_notebook(note_path, FAKE_RLE)

        appended_page = mock_nb.pages[-1]
        appended_page.get_layer(0).set_content.assert_called_with(FAKE_RLE)
