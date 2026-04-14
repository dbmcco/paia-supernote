"""
ABOUTME: Supernote file system watcher module
Purpose: Monitors local .note files for changes using FSEvents and triggers processing
"""

import hashlib
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Extensions to ignore — everything that isn't a real .note file
_IGNORED_EXTENSIONS = {".tmp", ".bak", ".swp", ".partial"}


class SupernoteWatcher:
    """Monitors Supernote sync folder for .note file changes."""

    DEFAULT_SYNC_PATH = Path(
        "~/Library/Containers/com.ratta.supernote/Data/Library/Application Support/"
        "com.ratta.supernote/908410628964298752/Supernote/Note/"
    ).expanduser()

    DEFAULT_DEBOUNCE_SECONDS = 5.0

    def __init__(
        self,
        on_note_changed: Optional[Callable[[Path, str], None]] = None,
        watch_path: Optional[Path] = None,
        debounce_seconds: Optional[float] = None,
    ) -> None:
        self._callback = on_note_changed
        self._watch_path = watch_path or self.DEFAULT_SYNC_PATH
        self._debounce_seconds = (
            debounce_seconds
            if debounce_seconds is not None
            else self.DEFAULT_DEBOUNCE_SECONDS
        )
        self._observer: Optional[Observer] = None
        self._checksums: Dict[str, str] = {}
        self._pending: Dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self._watch_path.exists():
            raise FileNotFoundError(
                f"Supernote sync path not found: {self._watch_path}"
            )
        handler = _NoteFileHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_path), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    @staticmethod
    def _checksum(path: str) -> str:
        try:
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except OSError:
            return ""

    def handle_event(self, file_path: str) -> None:
        """Process an FS event with filtering, debounce, and checksum check."""
        p = Path(file_path)

        # Only .note files
        if p.suffix != ".note":
            return

        # Skip ignored extensions that might appear before .note in name
        if any(ext in p.name for ext in _IGNORED_EXTENSIONS):
            return

        now = time.monotonic()
        with self._lock:
            self._pending[file_path] = now

        def _after_debounce() -> None:
            time.sleep(self._debounce_seconds)
            with self._lock:
                # Only proceed if no newer event superseded this one
                if self._pending.get(file_path) != now:
                    return
                self._pending.pop(file_path, None)

            new_checksum = self._checksum(file_path)
            if not new_checksum:
                return
            old_checksum = self._checksums.get(file_path, "")
            if new_checksum == old_checksum:
                return

            self._checksums[file_path] = new_checksum
            notebook_name = p.stem
            if self._callback:
                self._callback(p, notebook_name)

        threading.Thread(target=_after_debounce, daemon=True).start()


class _NoteFileHandler(FileSystemEventHandler):
    def __init__(self, watcher: SupernoteWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_modified(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._watcher.handle_event(event.src_path)