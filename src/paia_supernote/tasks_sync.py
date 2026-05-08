"""
ABOUTME: TasksSync — polls Linear (LFW) and keeps tasks.note in sync.
ABOUTME: Rebuilds the task page when open issues change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

import structlog

from paia_agent_runtime.tools.linear import LinearTool

log = structlog.get_logger(__name__)


class TasksSync:
    """Polls Linear and rebuilds tasks.note when open issues change.

    Runs as a background asyncio task. On each poll cycle:
    1. Fetch open issues from Linear via LinearTool
    2. Hash the issue list
    3. If the hash changed, render the task page and upload tasks.note
    """

    DEFAULT_POLL_INTERVAL = 60

    def __init__(
        self,
        uploader,
        writer,
        linear_api_key: str,
        linear_team_key: str = "LFW",
        linear_team_id: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._uploader = uploader
        self._writer = writer
        self._linear = LinearTool(api_key=linear_api_key, team_id=linear_team_id)
        self._linear_team_key = linear_team_key
        self._poll_interval = poll_interval
        self._issue_hash: str = ""
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("tasks_sync_started", team_key=self._linear_team_key)

    async def stop(self) -> None:
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
        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:
                log.error("tasks_sync_error", error=str(exc))
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        result = await self._linear.execute(
            "list_issues",
            team_key=self._linear_team_key,
            limit=50,
        )

        if result.get("status") != "ok":
            log.warning("tasks_fetch_failed", error=result.get("error"))
            return

        issues: List[Dict[str, Any]] = result.get("issues", [])

        h = hashlib.md5(json.dumps(issues, sort_keys=True).encode()).hexdigest()
        if self._issue_hash == h:
            return
        self._issue_hash = h

        log.info("tasks_changed", count=len(issues))
        await self._rebuild_tasks_note(issues)

    async def _rebuild_tasks_note(
        self, issues: List[Dict[str, Any]]
    ) -> None:
        from .notebook_writer import append_page_to_notebook

        rle = self._writer.render_tasks_page("tasks", issues)

        try:
            existing = await self._uploader.download_notebook("tasks.note")
            log.info("tasks_note_exists", size=len(existing))
        except RuntimeError:
            existing = None

        if existing is None:
            notebook_bytes = await self._build_fresh_notebook([rle])
        else:
            notebook_bytes = await self._replace_all_pages(existing, [rle])

        fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, notebook_bytes)
            os.close(fd)
            await self._uploader.upload_notebook(tmp_path, "tasks.note")
            log.info("tasks_note_uploaded", size=len(notebook_bytes))
        finally:
            os.unlink(tmp_path)

    async def _build_fresh_notebook(self, page_rles: List[bytes]) -> bytes:
        from .notebook_writer import append_page_to_notebook

        try:
            base_bytes = await self._uploader.download_notebook("Quick.note")
        except RuntimeError:
            raise RuntimeError("Cannot create tasks.note: no base notebook available")

        import supernotelib.parser as sn_parser
        import supernotelib.manipulator as sn_manip

        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, base_bytes)
            os.close(fd)
            nb = sn_parser.load_notebook(path)
            nb.pages = [nb.pages[0]]
            if hasattr(nb.metadata, "pages"):
                nb.metadata.pages = [nb.metadata.pages[0]]
            one_page = sn_manip.reconstruct(nb)
        finally:
            os.unlink(path)

        notebook_bytes = append_page_to_notebook(one_page, page_rles[0])
        fd2, path2 = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd2, notebook_bytes)
            os.close(fd2)
            nb2 = sn_parser.load_notebook(path2)
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
        import supernotelib.parser as sn_parser
        import supernotelib.manipulator as sn_manip
        from .notebook_writer import append_page_to_notebook, clear_recognition_metadata

        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, existing)
            os.close(fd)
            nb = sn_parser.load_notebook(path)

            n_existing = nb.get_total_pages()
            for i, rle in enumerate(page_rles):
                if i < n_existing:
                    page = nb.get_page(i)
                    clear_recognition_metadata(page)
                    if page.is_layer_supported():
                        page.get_layer(0).set_content(rle)
                        layers = page.get_layers()
                        for j in range(1, len(layers)):
                            layer = layers[j]
                            name = layer.get_name()
                            if name and name != "BGLAYER":
                                layer.set_content(b"")

            if n_existing > len(page_rles):
                nb.pages = nb.pages[: len(page_rles)]
                if hasattr(nb.metadata, "pages"):
                    nb.metadata.pages = nb.metadata.pages[: len(page_rles)]

            notebook_bytes = sn_manip.reconstruct(nb)
        finally:
            os.unlink(path)

        for rle in page_rles[n_existing:]:
            notebook_bytes = append_page_to_notebook(notebook_bytes, rle)

        return notebook_bytes
