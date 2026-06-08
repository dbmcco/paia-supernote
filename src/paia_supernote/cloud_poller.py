"""
ABOUTME: Supernote Cloud poller — detects changed .note files without the Partner app.
ABOUTME: Polls /api/file/list/query, downloads changed files, fires callback with bytes.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, Iterable, Optional

import structlog

log = structlog.get_logger(__name__)

# Type alias: async callback receives (notebook_name, note_bytes, update_time)
NoteChangedCallback = Callable[[str, bytes, "int | None"], Awaitable[None]]


class CloudPoller:
    """Polls Supernote Cloud for changed .note files.

    Replaces the FSEvents watcher — no Partner app or local sync needed.

    Detection strategy: compare updateTime per file name. When updateTime
    increases (or a file appears for the first time), download and fire callback.
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds

    # Only watch these notebooks (stem name without .note)
    WATCHED_NOTEBOOKS = {"Quick", "Walk", "LFW", "Navicyte", "Synth", "test", "tasks"}

    def __init__(
        self,
        uploader,
        on_note_changed: NoteChangedCallback,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        watched_notebooks: Iterable[str] | None = None,
        process_existing_on_start: bool = True,
    ) -> None:
        """
        Args:
            uploader: A started SupernoteUploader instance (browser session active).
            on_note_changed: Async callback fired per changed file.
            poll_interval: How often to poll in seconds (default 60).
            watched_notebooks: Optional notebook stems to watch.
            process_existing_on_start: Whether existing files should fire on first poll.
        """
        self._uploader = uploader
        self._callback = on_note_changed
        self._poll_interval = poll_interval
        self._process_existing_on_start = process_existing_on_start
        self._watched_notebooks = {
            str(name).strip()
            for name in (watched_notebooks or self.WATCHED_NOTEBOOKS)
            if str(name).strip()
        }
        self._watched_notebook_keys = {
            name.casefold() for name in self._watched_notebooks
        }

        # Tracks last-seen updateTime per file name
        self._last_seen: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stopping = False

    @property
    def watched_notebooks(self) -> set[str]:
        return set(self._watched_notebooks)

    @property
    def process_existing_on_start(self) -> bool:
        return self._process_existing_on_start

    def start(self) -> None:
        """Start the polling loop as a background asyncio task."""
        self._running = True
        self._stopping = False
        self._task = asyncio.create_task(self._loop())
        log.info("cloud_poller_started", poll_interval=self._poll_interval)

    async def wait(self) -> None:
        """Wait for the background poller task to exit."""
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            if self._stopping:
                return
            raise

    async def stop(self) -> None:
        """Stop polling and await the background task."""
        self._running = False
        self._stopping = True
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
                log.exception("cloud_poller_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Single poll: list Note folder, detect changes, and fire callbacks."""
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
            if notebook_name.casefold() not in self._watched_notebook_keys:
                continue

            last_update = self._last_seen.get(name, 0)
            if (
                last_update == 0
                and not self._process_existing_on_start
                and name not in self._last_seen
            ):
                self._last_seen[name] = update_time
                continue
            if update_time <= last_update:
                continue

            try:
                note_bytes = await self._uploader.download_notebook(name)
                log.info(
                    "cloud_file_changed",
                    name=name,
                    size=len(note_bytes),
                    update_time=update_time,
                )
                await self._callback(notebook_name, note_bytes, update_time)
                # Advance the revision marker only after download + callback succeed.
                self._last_seen[name] = update_time
            except Exception as exc:
                log.warning("cloud_download_error", name=name, error=str(exc))

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
        if result["status"] in (401, 403):
            log.warning(
                "cloud_session_expired",
                status=result["status"],
                hint="Run 'paia-supernote login' to re-authenticate",
            )
            return []
        if result["status"] != 200 or not isinstance(result["body"], dict):
            log.warning("cloud_list_failed", status=result["status"])
            return []
        return result["body"].get("userFileVOList", [])
