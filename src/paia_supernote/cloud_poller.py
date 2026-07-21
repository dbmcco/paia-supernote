"""
ABOUTME: Supernote Cloud poller — detects changed .note files without the Partner app.
ABOUTME: Polls /api/file/list/query, downloads changed files, fires callback with bytes.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Awaitable, Callable, Dict, Iterable, Optional

import structlog

log = structlog.get_logger(__name__)

# Type alias: async callback receives (notebook_name, note_bytes, update_time)
NoteChangedCallback = Callable[[str, bytes, "int | None"], Awaitable[None]]

# Type alias: async callback fired only on poll-health transitions.
# Receives (healthy, detail) where detail carries reason/status for monitoring.
PollHealthCallback = Callable[[bool, Dict[str, object]], Awaitable[None]]


class BackfillPageCapExceeded(RuntimeError):
    """Raised by an on_changed callback to abort a back-fill poll.

    Signals that the configured page-count cap would be exceeded; propagates out
    of ``poll_once`` (the per-file handler re-raises it instead of swallowing)
    so the on-demand ingest can report a clean abort instead of OCR-ing past
    the cap. Lives here (not in ingest_service) to avoid a circular import —
    ingest_service already imports from this module.
    """

    def __init__(self, notebook: str, requested: int, remaining: int) -> None:
        self.notebook = notebook
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"{notebook}: {requested} pages exceed the back-fill cap "
            f"({remaining} remaining); aborting before OCR"
        )


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
        on_poll_health: PollHealthCallback | None = None,
    ) -> None:
        """
        Args:
            uploader: A started SupernoteUploader instance (browser session active).
            on_note_changed: Async callback fired per changed file.
            poll_interval: How often to poll in seconds (default 60).
            watched_notebooks: Optional notebook stems to watch.
            process_existing_on_start: Whether existing files should fire on first poll.
            on_poll_health: Async callback fired only when poll health changes
                (healthy<->degraded), so a persistent auth failure surfaces once
                rather than flooding the bus. Receives (healthy, detail).
        """
        self._uploader = uploader
        self._callback = on_note_changed
        self._poll_interval = poll_interval
        self._process_existing_on_start = process_existing_on_start
        self._on_poll_health = on_poll_health
        # Start healthy; the first auth failure transitions to degraded and fires.
        self._poll_healthy = True
        watched_source = (
            self.WATCHED_NOTEBOOKS if watched_notebooks is None else watched_notebooks
        )
        self._watched_notebooks = {
            str(name).strip() for name in watched_source if str(name).strip()
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

    async def poll_once(self) -> None:
        """Run a single poll cycle and return (no loop).

        For on-demand / back-fill use (``ingest --once``). Shares the exact
        list -> detect -> download -> callback path with the continuous loop
        so behavior is identical, just without the sleep/repeat.
        """
        await self._poll_once()

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

            notebook_name = _notebook_name_from_file_name(name)
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
            except BackfillPageCapExceeded:
                raise
            except Exception as exc:
                log.warning("cloud_download_error", name=name, error=str(exc))

    async def _list_notes(self) -> list:
        """Fetch the Note folder listing from Supernote Cloud.

        On a 401/403 we attempt a silent re-auth once (which auto-logs in when
        SN_PHONE/SN_PASSWORD are set), then retry the listing. This lets the
        daemon recover from session expiry without a human running `login`.
        """
        result = await self._fetch_note_listing()
        if result["status"] in (401, 403):
            try:
                await self._uploader.ensure_authenticated()
                result = await self._fetch_note_listing()
            except Exception as exc:
                log.warning("cloud_auto_reauth_failed", error=str(exc))

        if result["status"] in (401, 403):
            log.warning(
                "cloud_session_expired",
                status=result["status"],
                hint="Set SN_PHONE/SN_PASSWORD to enable auto-relogin, "
                "or run 'supernote auth login'.",
            )
            await self._set_poll_health(
                False, reason="cloud_session_expired", status=result["status"]
            )
            return []
        if result["status"] != 200 or not isinstance(result["body"], dict):
            log.warning("cloud_list_failed", status=result["status"])
            await self._set_poll_health(
                False, reason="cloud_list_failed", status=result["status"]
            )
            return []
        await self._set_poll_health(True, reason=None, status=result["status"])
        return result["body"].get("userFileVOList", [])

    async def _fetch_note_listing(self) -> dict:
        """Walk the Note folder tree; aggregate all entries (root + subfolders).

        Returns a result shaped like the raw API response so the caller's
        health/auth handling (which inspects ``result["status"]``) is unchanged.
        The root listing's status is authoritative for auth/health; a subfolder
        listing that fails is skipped rather than failing the whole poll.
        """
        root = await self._uploader._api_call(
            "/api/file/list/query",
            {
                "directoryId": self._uploader.NOTE_FOLDER_ID,
                "pageNo": 1,
                "pageSize": 200,
                "order": "time",
                "sequence": "desc",
                "filterType": 0,
            },
        )
        if root["status"] != 200 or not isinstance(root["body"], dict):
            return root  # caller handles auth/health on the root status

        all_entries = list(root["body"].get("userFileVOList", []))
        queue = [str(e.get("id")) for e in all_entries if e.get("isFolder") == "Y"]
        seen = set(queue)
        while queue:
            directory_id = queue.pop(0)
            sub = await self._uploader._api_call(
                "/api/file/list/query",
                {
                    "directoryId": directory_id,
                    "pageNo": 1,
                    "pageSize": 200,
                    "order": "time",
                    "sequence": "desc",
                    "filterType": 0,
                },
            )
            if sub["status"] != 200 or not isinstance(sub["body"], dict):
                continue  # skip an unreadable subfolder, don't fail the poll
            for entry in sub["body"].get("userFileVOList", []):
                all_entries.append(entry)
                if entry.get("isFolder") == "Y":
                    child = str(entry.get("id") or "")
                    if child and child not in seen:
                        seen.add(child)
                        queue.append(child)

        return {"status": 200, "body": {"userFileVOList": all_entries}}

    async def _set_poll_health(
        self, healthy: bool, *, reason: str | None, status: int | None
    ) -> None:
        """Fire the health callback only when poll health changes state.

        Throttles monitoring to transitions so a persistent 403 surfaces once
        (degraded) and once again on recovery — never per-poll.
        """
        if healthy == self._poll_healthy:
            return
        self._poll_healthy = healthy
        log.info("cloud_poll_health_changed", healthy=healthy, reason=reason)
        if self._on_poll_health is None:
            return
        detail: Dict[str, object] = {
            "reason": reason,
            "status": status,
            "notebooks": sorted(self._watched_notebooks),
        }
        try:
            await self._on_poll_health(healthy, detail)
        except Exception as exc:  # monitoring must never break the poll loop
            log.warning("cloud_poll_health_callback_error", error=str(exc))


def _notebook_name_from_file_name(file_name: str) -> str:
    """Return display notebook stem from a Cloud file name or path."""
    leaf = PurePosixPath(str(file_name)).name
    return leaf[:-5] if leaf.endswith(".note") else leaf
