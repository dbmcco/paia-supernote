"""Tests for SupernoteWatcher — FSEvents watcher with debounce and checksum."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paia_supernote.watcher import SupernoteWatcher


@pytest.fixture()
def watch_dir(tmp_path: Path) -> Path:
    """Create a temporary directory to watch."""
    d = tmp_path / "notes"
    d.mkdir()
    return d


@pytest.fixture()
def watcher_and_callback(watch_dir: Path):
    """Create a watcher with a short debounce and a mock callback."""
    callback = MagicMock()
    event = threading.Event()

    def recording_callback(path: Path, notebook_name: str) -> None:
        callback(path, notebook_name)
        event.set()

    w = SupernoteWatcher(
        on_note_changed=recording_callback,
        watch_path=watch_dir,
        debounce_seconds=0.2,
    )
    return w, callback, event


class TestNoteFileEvents:
    """Event fires when a .note file is modified."""

    def test_fires_on_note_modification(self, watch_dir, watcher_and_callback):
        watcher, callback, event = watcher_and_callback

        note = watch_dir / "Quick.note"
        note.write_bytes(b"initial content")

        watcher.handle_event(str(note))
        assert event.wait(timeout=2.0), "Callback was not invoked"

        callback.assert_called_once()
        call_path, call_name = callback.call_args[0]
        assert call_path == note
        assert call_name == "Quick"


class TestFilterIgnoresNonNote:
    """Events do NOT fire for .tmp, .bak, or other non-.note files."""

    @pytest.mark.parametrize("filename", ["data.tmp", "backup.bak", "scratch.txt"])
    def test_ignored_extensions(self, watch_dir, watcher_and_callback, filename):
        watcher, callback, event = watcher_and_callback

        f = watch_dir / filename
        f.write_bytes(b"junk")

        watcher.handle_event(str(f))
        time.sleep(0.5)

        callback.assert_not_called()


class TestDebounce:
    """Rapid writes produce only one event after the debounce window."""

    def test_rapid_writes_produce_single_event(self, watch_dir, watcher_and_callback):
        watcher, callback, event = watcher_and_callback

        note = watch_dir / "LFW.note"

        # Simulate rapid writes — each write updates the file content
        for i in range(5):
            note.write_bytes(f"version {i}".encode())
            watcher.handle_event(str(note))
            time.sleep(0.05)

        # Wait for debounce to settle
        assert event.wait(timeout=2.0), "Callback was not invoked"
        time.sleep(0.5)  # extra margin for any straggler threads

        callback.assert_called_once()


class TestChecksum:
    """No event if file bytes unchanged despite FS modification time change."""

    def test_no_event_on_unchanged_content(self, watch_dir, watcher_and_callback):
        watcher, callback, event = watcher_and_callback

        note = watch_dir / "Synth.note"
        note.write_bytes(b"same content")

        # First event — should fire (new file, no prior checksum)
        watcher.handle_event(str(note))
        assert event.wait(timeout=2.0), "First callback was not invoked"
        callback.assert_called_once()

        callback.reset_mock()
        event.clear()

        # "Touch" the file without changing content
        note.write_bytes(b"same content")
        watcher.handle_event(str(note))
        time.sleep(0.5)

        callback.assert_not_called()


class TestNotebookName:
    """notebook_name is extracted correctly from the file path stem."""

    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            ("Quick.note", "Quick"),
            ("LFW.note", "LFW"),
            ("Synth.note", "Synth"),
            ("My Notebook.note", "My Notebook"),
        ],
    )
    def test_notebook_name_extraction(
        self, watch_dir, watcher_and_callback, filename, expected_name
    ):
        watcher, callback, event = watcher_and_callback

        note = watch_dir / filename
        note.write_bytes(b"content")

        watcher.handle_event(str(note))
        assert event.wait(timeout=2.0), "Callback was not invoked"

        _, call_name = callback.call_args[0]
        assert call_name == expected_name
