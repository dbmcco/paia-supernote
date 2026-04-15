"""
ABOUTME: TasksSync — polls paia-work lanes and keeps tasks.note in sync.
ABOUTME: Rebuilds all 4 pages (focus/inbox/orbit/parking) on any lane change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

LANES = ["focus", "inbox", "orbit", "parking"]


class TasksSync:
    """Polls paia-work and rebuilds tasks.note when any lane changes.

    Runs as a background asyncio task. On each poll cycle:
    1. Fetch all four lanes from paia-work
    2. Hash each lane's task list
    3. If any hash changed, render 4 pages and upload tasks.note
    """

    DEFAULT_POLL_INTERVAL = 60  # seconds

    def __init__(
        self,
        uploader,
        writer,
        work_url: str,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """
        Args:
            uploader: Started SupernoteUploader instance.
            writer: SupernoteWriter instance with render_tasks_page.
            work_url: Base URL for paia-work API (e.g. http://localhost:3513).
            poll_interval: Seconds between polls.
        """
        self._uploader = uploader
        self._writer = writer
        self._work_url = work_url.rstrip("/")
        self._poll_interval = poll_interval
        self._lane_hashes: Dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        """Start the poll loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("tasks_sync_started", work_url=self._work_url)

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
        log.info("tasks_sync_stopped")

    async def _loop(self) -> None:
        """Poll loop: poll, sleep, repeat."""
        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:
                log.error("tasks_sync_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Fetch all tasks from paia-work, bucket by board tag, detect changes."""
        lanes_data: Dict[str, List[Dict[str, Any]]] = {lane: [] for lane in LANES}

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{self._work_url}/api/tasks",
                    timeout=10.0,
                )
                resp.raise_for_status()
                payload = resp.json()
                # paia-work returns a list or {"tasks": [...]}
                all_tasks: List[Dict[str, Any]] = (
                    payload if isinstance(payload, list) else payload.get("tasks", [])
                )
            except httpx.HTTPError as exc:
                log.warning("tasks_fetch_failed", error=str(exc))
                return  # Don't rebuild on partial data

        # Bucket by board tag, open tasks only (done/failed/cancelled are noise).
        CLOSED_STATUSES = {"done", "failed", "cancelled", "completed", "closed"}
        for task in all_tasks:
            if task.get("status") in CLOSED_STATUSES:
                continue
            tags: List[str] = task.get("tags") or []
            for lane in LANES:
                if f"board:{lane}" in tags:
                    lanes_data[lane].append(task)
                    break  # A task belongs to at most one board lane

        # Detect any lane change by MD5 hash
        changed = False
        for lane in LANES:
            tasks = lanes_data.get(lane, [])
            h = hashlib.md5(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
            if self._lane_hashes.get(lane) != h:
                self._lane_hashes[lane] = h
                changed = True

        if not changed:
            return

        log.info("tasks_lanes_changed", lanes=list(lanes_data.keys()))
        await self._rebuild_tasks_note(lanes_data)

    async def _rebuild_tasks_note(
        self, lanes_data: Dict[str, List[Dict[str, Any]]]
    ) -> None:
        """Render all 4 lane pages and upload tasks.note."""
        from .notebook_writer import append_page_to_notebook

        # Build each page's RATTA_RLE bytes
        page_rles: List[bytes] = []
        for lane in LANES:
            tasks = lanes_data.get(lane, [])
            rle = self._writer.render_tasks_page(lane, tasks)
            page_rles.append(rle)

        # Bootstrap: if tasks.note doesn't exist yet, use first page as base,
        # then append the rest. If it exists, download and replace all pages.
        try:
            existing = await self._uploader.download_notebook("tasks.note")
            log.info("tasks_note_exists", size=len(existing))
        except RuntimeError:
            existing = None

        if existing is None:
            # Build from scratch: first page is the base, append remaining 3
            notebook_bytes = await self._build_fresh_notebook(page_rles)
        else:
            notebook_bytes = await self._replace_all_pages(existing, page_rles)

        # Write to temp file and upload
        fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, notebook_bytes)
            os.close(fd)
            await self._uploader.upload_notebook(tmp_path, "tasks.note")
            log.info("tasks_note_uploaded", size=len(notebook_bytes))
        finally:
            os.unlink(tmp_path)

    async def _build_fresh_notebook(self, page_rles: List[bytes]) -> bytes:
        """Build a brand-new tasks.note from scratch using test.note as template."""
        from .notebook_writer import append_page_to_notebook

        # We need an existing .note to use as a structural base.
        # Use Quick.note (always present) as the template — take its first page
        # content, reconstruct as 1 page, then append our lane pages.
        try:
            base_bytes = await self._uploader.download_notebook("Quick.note")
        except RuntimeError:
            raise RuntimeError("Cannot create tasks.note: no base notebook available")

        import supernotelib.parser as sn_parser
        import supernotelib.manipulator as sn_manip
        import copy

        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, base_bytes)
            os.close(fd)
            nb = sn_parser.load_notebook(path)
            # Trim to 1 page
            nb.pages = [nb.pages[0]]
            if hasattr(nb.metadata, "pages"):
                nb.metadata.pages = [nb.metadata.pages[0]]
            one_page = sn_manip.reconstruct(nb)
        finally:
            os.unlink(path)

        # Replace page 0 with lane 0, then append lanes 1-3
        notebook_bytes = append_page_to_notebook(one_page, page_rles[0])
        # Now we have 2 pages (original + lane 0). Rebuild with only lane 0.
        fd2, path2 = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd2, notebook_bytes)
            os.close(fd2)
            nb2 = sn_parser.load_notebook(path2)
            # Keep only the last page (our lane page)
            nb2.pages = [nb2.pages[-1]]
            if hasattr(nb2.metadata, "pages"):
                nb2.metadata.pages = [nb2.metadata.pages[-1]]
            notebook_bytes = sn_manip.reconstruct(nb2)
        finally:
            os.unlink(path2)

        for rle in page_rles[1:]:
            notebook_bytes = append_page_to_notebook(notebook_bytes, rle)

        return notebook_bytes

    async def _replace_all_pages(
        self, existing: bytes, page_rles: List[bytes]
    ) -> bytes:
        """Replace all pages in an existing tasks.note with fresh lane pages."""
        import supernotelib.parser as sn_parser
        import supernotelib.manipulator as sn_manip
        from .notebook_writer import append_page_to_notebook, _OFFSET_FIELDS

        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, existing)
            os.close(fd)
            nb = sn_parser.load_notebook(path)

            # Replace content of existing pages where we have RLE
            n_existing = nb.get_total_pages()
            for i, rle in enumerate(page_rles):
                if i < n_existing:
                    page = nb.get_page(i)
                    # Zero recognition offsets
                    for key in _OFFSET_FIELDS:
                        if key in page.metadata:
                            page.metadata[key] = "0"
                    page.metadata["RECOGNSTATUS"] = "0"
                    page.metadata["RECOGNFILESTATUS"] = "0"
                    if page.is_layer_supported():
                        page.get_layer(0).set_content(rle)
                        layers = page.get_layers()
                        for j in range(1, len(layers)):
                            layer = layers[j]
                            name = layer.get_name()
                            if name and name != "BGLAYER":
                                layer.set_content(b"")

            # Trim or pad to exactly 4 pages
            if n_existing > len(page_rles):
                nb.pages = nb.pages[: len(page_rles)]
                if hasattr(nb.metadata, "pages"):
                    nb.metadata.pages = nb.metadata.pages[: len(page_rles)]

            notebook_bytes = sn_manip.reconstruct(nb)
        finally:
            os.unlink(path)

        # Append any extra pages beyond what existed
        for rle in page_rles[n_existing:]:
            notebook_bytes = append_page_to_notebook(notebook_bytes, rle)

        return notebook_bytes
