"""Tests for paia_supernote.reader module."""

import json
import pytest
from unittest.mock import Mock, AsyncMock, patch
from PIL import Image

from paia_supernote.reader import SupernoteReader, ReadResult, CheckboxItem, SNAPSHOT_DIR


class TestSupernoteReader:
    """Test cases for SupernoteReader class."""

    @pytest.mark.asyncio
    @patch('paia_supernote.reader.supernotelib')
    @patch('paia_supernote.reader.ImageConverter')
    async def test_extracts_text_from_page_0(self, mock_converter_class, mock_supernotelib, tmp_path, monkeypatch):
        """Extracts text from page 0 of Quick.note without exception."""
        monkeypatch.setattr('paia_supernote.reader.SNAPSHOT_DIR', tmp_path / "snapshots")

        mock_notebook = Mock()
        mock_notebook.get_total_pages.return_value = 1
        mock_supernotelib.load_notebook.return_value = mock_notebook

        mock_converter = Mock()
        mock_converter_class.return_value = mock_converter
        test_image = Image.open("tests/fixtures/test_page.png")
        mock_converter.convert.return_value = test_image

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.content = [Mock()]
        mock_response.content[0].text = "Some handwritten notes here"
        mock_client.messages.create.return_value = mock_response

        reader = SupernoteReader(anthropic_client=mock_client)
        results = await reader.process_file("tests/fixtures/Quick.note")

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ReadResult)
        assert result.notebook == "Quick"
        assert result.page_num == 0
        assert result.text == "Some handwritten notes here"
        assert result.timestamp is not None
        mock_supernotelib.load_notebook.assert_called_once_with("tests/fixtures/Quick.note")
        mock_converter.convert.assert_called_once_with(0)

    def test_checkbox_diff_returns_empty_list_on_first_run(self, tmp_path, monkeypatch):
        """Checkbox diff returns empty list when no prior snapshot exists."""
        monkeypatch.setattr('paia_supernote.reader.SNAPSHOT_DIR', tmp_path / "snapshots")
        reader = SupernoteReader()

        changes = reader.detect_checkbox_changes("test_file.note", 0, "□ Some task text")

        assert changes == []

    def test_checkbox_diff_detects_newly_checked_item(self, tmp_path, monkeypatch):
        """Checkbox diff detects newly checked items when snapshot updated."""
        snapshot_dir = tmp_path / "snapshots"
        monkeypatch.setattr('paia_supernote.reader.SNAPSHOT_DIR', snapshot_dir)
        reader = SupernoteReader()

        # First call with unchecked item
        changes1 = reader.detect_checkbox_changes("test_file.note", 0, "□ Some task text")
        assert changes1 == []

        # Second call with checked item — should detect newly checked
        changes2 = reader.detect_checkbox_changes("test_file.note", 0, "☑ Some task text")
        assert len(changes2) == 1
        assert isinstance(changes2[0], CheckboxItem)
        assert changes2[0].task_text == "Some task text"
        assert changes2[0].tag == "focus"
        assert changes2[0].page_num == 0

        # Verify snapshot was persisted to disk
        snapshot_file = snapshot_dir / "test_file_page_0.json"
        assert snapshot_file.exists()
        snapshot_data = json.loads(snapshot_file.read_text())
        assert len(snapshot_data) == 1
        assert snapshot_data[0]["task_text"] == "Some task text"

    def test_checkbox_diff_orbit_tag(self, tmp_path, monkeypatch):
        """Circle markers (●) are tagged as orbit."""
        monkeypatch.setattr('paia_supernote.reader.SNAPSHOT_DIR', tmp_path / "snapshots")
        reader = SupernoteReader()

        # First call — no prior state
        reader.detect_checkbox_changes("test_file.note", 0, "○ Orbit task")

        # Second call — circle checked
        changes = reader.detect_checkbox_changes("test_file.note", 0, "● Orbit task")
        assert len(changes) == 1
        assert changes[0].tag == "orbit"
        assert changes[0].task_text == "Orbit task"

    @pytest.mark.asyncio
    async def test_classification_returns_task_for_checkbox_content(self):
        """Content with checkbox markers is classified as 'task'."""
        mock_client = AsyncMock()
        reader = SupernoteReader(anthropic_client=mock_client)

        assert await reader.classify_content("□ This is a task") == "task"
        assert await reader.classify_content("○ This is also a task") == "task"
        assert await reader.classify_content("☑ This is a completed task") == "task"
        assert await reader.classify_content("● This is another completed task") == "task"
        # No LLM call needed for task classification
        mock_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_classification_returns_snippet_via_llm(self):
        """Content classified as snippet when LLM detects strategy fragment."""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.content = [Mock()]
        mock_response.content[0].text = "snippet"
        mock_client.messages.create.return_value = mock_response

        reader = SupernoteReader(anthropic_client=mock_client)
        result = await reader.classify_content("Aberdeen positioning needs rethinking before the call")
        assert result == "snippet"
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_classification_returns_general_for_plain_content(self):
        """Plain content without markers classified as general."""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.content = [Mock()]
        mock_response.content[0].text = "general"
        mock_client.messages.create.return_value = mock_response

        reader = SupernoteReader(anthropic_client=mock_client)
        result = await reader.classify_content("This is just regular notes")
        assert result == "general"

    def test_only_processes_changed_pages(self):
        """Only changed pages are processed (same MD5 = skip)."""
        reader = SupernoteReader()
        test_image = Image.open("tests/fixtures/test_page.png")

        # First call: page is new
        assert reader.page_changed("test_file.note", 0, test_image) is True

        # Second call: same image, should skip
        assert reader.page_changed("test_file.note", 0, test_image) is False

        # Different page number: should process
        assert reader.page_changed("test_file.note", 1, test_image) is True

    @pytest.mark.asyncio
    @patch('paia_supernote.reader.supernotelib')
    @patch('paia_supernote.reader.ImageConverter')
    async def test_returns_read_result_dataclass(self, mock_converter_class, mock_supernotelib, tmp_path, monkeypatch):
        """process_file returns ReadResult with all required fields."""
        monkeypatch.setattr('paia_supernote.reader.SNAPSHOT_DIR', tmp_path / "snapshots")

        mock_notebook = Mock()
        mock_notebook.get_total_pages.return_value = 1
        mock_supernotelib.load_notebook.return_value = mock_notebook

        mock_converter = Mock()
        mock_converter_class.return_value = mock_converter
        test_image = Image.open("tests/fixtures/test_page.png")
        mock_converter.convert.return_value = test_image

        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.content = [Mock()]
        mock_response.content[0].text = "☑ Complete task one\n□ Start task two"
        mock_client.messages.create.return_value = mock_response

        reader = SupernoteReader(anthropic_client=mock_client)
        results = await reader.process_file("tests/fixtures/Quick.note")

        result = results[0]
        assert isinstance(result, ReadResult)
        assert result.notebook == "Quick"
        assert result.page_num == 0
        assert result.content_type == "task"
        assert len(result.checkboxes) == 1
        assert result.checkboxes[0].task_text == "Complete task one"
        assert result.checkboxes[0].tag == "focus"
        assert result.checkboxes[0].page_num == 0
