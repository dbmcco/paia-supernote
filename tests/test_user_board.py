"""
Tests for user board functionality.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from src.paia_supernote.user_board import UserBoard


@pytest.fixture
def mock_session_file(tmp_path):
    """Create a temporary session file."""
    session_file = tmp_path / "session.json"
    return session_file


@pytest.fixture
def user_board(mock_session_file):
    """Create a UserBoard instance with mocked session file."""
    board = UserBoard()
    board.session_file = mock_session_file
    board.session_data = board._load_session()
    yield board


class TestUserBoard:
    """Test cases for UserBoard class."""

    def test_init(self, user_board):
        """Test UserBoard initialization."""
        assert user_board.events is not None
        assert isinstance(user_board.session_data, dict)
        assert "last_seen" in user_board.session_data
        assert "preferences" in user_board.session_data

    def test_load_session_new_file(self, user_board):
        """Test loading session when no file exists."""
        session_data = user_board._load_session()
        assert session_data["last_seen"] == 0
        assert session_data["preferences"] == {}

    def test_load_session_existing_file(self, mock_session_file):
        """Test loading session from existing file."""
        test_data = {"last_seen": 123, "preferences": {"theme": "dark"}}
        mock_session_file.write_text(json.dumps(test_data))

        board = UserBoard()
        board.session_file = mock_session_file
        board.session_data = board._load_session()

        assert board.session_data["last_seen"] == 123
        assert board.session_data["preferences"]["theme"] == "dark"

    def test_save_session(self, user_board):
        """Test saving session data."""
        user_board.session_data["last_seen"] = 456
        user_board.session_data["preferences"]["new_setting"] = "value"

        user_board._save_session()

        # Verify file was written
        assert user_board.session_file.exists()

        # Verify content
        with open(user_board.session_file) as f:
            saved_data = json.load(f)

        assert saved_data["last_seen"] == 456
        assert saved_data["preferences"]["new_setting"] == "value"

    @pytest.mark.asyncio
    async def test_start_stop(self, user_board):
        """Test starting and stopping user board."""
        # Mock the events client and main loop
        user_board.events.start = AsyncMock()
        user_board.events.stop = AsyncMock()
        user_board._save_session = MagicMock()

        # Mock _main_loop to exit immediately
        user_board._main_loop = AsyncMock()

        # Test start
        await user_board.start()
        user_board.events.start.assert_called_once()
        user_board._main_loop.assert_called_once()

        # Test stop
        await user_board.stop()
        user_board.events.stop.assert_called_once()
        user_board._save_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_status_service_running(self, user_board, capsys):
        """Test status display when services are running."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            mock_stat = MagicMock()
            mock_stat.st_mtime = 0.0
            mock_note1 = MagicMock(spec=Path)
            mock_note1.stat.return_value = mock_stat
            mock_note1.name = "notebook1.note"
            mock_note2 = MagicMock(spec=Path)
            mock_note2.stat.return_value = mock_stat
            mock_note2.name = "notebook2.note"

            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.glob", return_value=[mock_note1, mock_note2]):

                await user_board._show_status()

                captured = capsys.readouterr()
                assert "paia-events service: running" in captured.out
                assert "2 notebooks found" in captured.out

    @pytest.mark.asyncio
    async def test_show_recent_events(self, user_board, capsys):
        """Test displaying recent events."""
        mock_events = {
            "events": [
                {
                    "id": 1,
                    "timestamp": 1234567890,
                    "payload": {
                        "notebook": "test_notebook",
                        "page": 1,
                        "text": "This is a test transcription"
                    }
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_events
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            await user_board._show_recent_events()

            captured = capsys.readouterr()
            assert "test_notebook:1" in captured.out
            assert "This is a test transcription" in captured.out

    def test_show_help(self, user_board, capsys):
        """Test help display."""
        user_board._show_help()

        captured = capsys.readouterr()
        assert "PAIA Supernote User Board Help" in captured.out
        assert "STATUS: Check if services" in captured.out
        assert "EVENTS: View recent" in captured.out
        assert "WRITE: Send content" in captured.out