"""
ABOUTME: Supernote Cloud poller — detects changed .note files without the Partner app.
ABOUTME: Polls /api/file/list/query, downloads changed files, fires callback with bytes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Dict, Optional

log = logging.getLogger(__name__)

# Type alias: async callback receives (notebook_name, note_bytes)
NoteChangedCallback = Callable[[str, bytes], Awaitable[None]]


class CloudPoller:
    """Polls Supernote Cloud for changed .note files.

    Replaces the FSEvents watcher — no Partner app or local sync needed.

    Detection strategy: compare updateTime per file name. When updateTime
    increases (or a file appears for the first time), download and fire callback.
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds

    # Only watch these notebooks (stem name without .note)
    WATCHED_NOTEBOOKS = {"Quick", "LFW", "Synth", "test", "tasks"}

    def __init__(
        self,
        uploader,
        on_note_changed: NoteChangedCallback,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """
        Args:
            uploader: A started SupernoteUploader instance (browser session active).
            on_note_changed: Async callback(notebook_name, note_bytes) fired per changed file.
            poll_interval: How often to poll in seconds (default 60).
        """
        self._uploader = uploader
        self._callback = on_note_changed
        self._poll_interval = poll_interval

        # Tracks last-seen updateTime per file name
        self._last_seen: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        """Start the polling loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("cloud_poller_started", poll_interval=self._poll_interval)

    async def stop(self) -> None:
        """Stop polling and await the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("cloud_poller_stopped")

    async def _loop(self) -> None:
        """Polling loop: poll, sleep, repeat."""
        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:
                log.error("cloud_poller_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Single poll: list Note folder, detect changes, download and fire callbacks."""
        file_list = await self._list_notes()

        for entry in file_list:
            name: str = entry.get("fileName", "")
            update_time: int = entry.get("updateTime", 0)

            if not name.endswith(".note"):
                continue
            if entry.get("isFolder") == "Y":
                continue
            if name.startswith("."):
                continue
            if entry.get("size", 0) == 0:
                continue

            notebook_name = name[:-5]  # strip .note suffix
            if notebook_name not in self.WATCHED_NOTEBOOKS:
                continue

            last_update = self._last_seen.get(name, 0)
            if update_time <= last_update:
                continue

            # File is new or updated — download and fire
            self._last_seen[name] = update_time

            try:
                note_bytes = await self._uploader.download_notebook(name)
                log.info(
                    "cloud_file_changed",
                    name=name,
                    size=len(note_bytes),
                    update_time=update_time,
                )
                await self._callback(notebook_name, note_bytes)
            except Exception as exc:
                log.error("cloud_download_error", name=name, error=str(exc))

    async def _list_notes(self) -> list:
        """Fetch the Note folder listing from Supernote Cloud."""
        result = await self._uploader._api_call("/api/file/list/query", {
            "directoryId": self._uploader.NOTE_FOLDER_ID,
            "pageNo": 1,
            "pageSize": 200,
            "order": "time",
            "sequence": "desc",
            "filterType": 0,
        })
        if result["status"] != 200 or not isinstance(result["body"], dict):
            log.warning("cloud_list_failed", status=result["status"])
            return []
        return result["body"].get("userFileVOList", [])
