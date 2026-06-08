"""Ingest service — OCRs pages and writes durable page state.

Uses the Supernote Partner app local sync folder (FSEvents) when available,
falling back to cloud polling when not. The local path requires no auth.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
import hashlib
from pathlib import Path
from typing import Any, Optional

import structlog

from .cloud_poller import CloudPoller
from .events import EventsClient
from .page_state import PageStateStore
from .reader import SupernoteReader
from .uploader import SupernoteUploader
from .watcher import SupernoteWatcher

log = structlog.get_logger(__name__)


class IngestService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        reader: SupernoteReader | None = None,
        uploader: SupernoteUploader | None = None,
        cloud_poller: CloudPoller | None = None,
    ) -> None:
        from .main import load_config
        self.config = config or load_config()
        self.reader = reader or SupernoteReader(
            vision_backend=self.config["vision_backend"],
            ollama_model=self.config["ollama_model"],
            ollama_url=self.config["ollama_url"],
            zai_api_key=self.config["zai_api_key"],
            zai_base_url=self.config["zai_base_url"],
            zai_vision_model=self.config["zai_vision_model"],
            zai_text_model=self.config["zai_text_model"],
        )
        self.events = EventsClient(base_url=self.config["events_url"])
        self.page_state = PageStateStore(Path(self.config["state_db_path"]))
        self.page_state.init_schema()
        self._watched_notebook_keys = self._build_watched_notebook_keys()

        # Cloud poller path (used only when Partner app sync unavailable)
        self._uploader: Optional[SupernoteUploader] = uploader
        self._cloud_poller: Optional[CloudPoller] = cloud_poller
        self._local_watcher: Optional[SupernoteWatcher] = None

        self._shutdown_event = asyncio.Event()

    def _build_watched_notebook_keys(self) -> set[str]:
        """Notebooks to watch: Walk + tasks always, plus folio_sync_notebooks."""
        keys = {"walk", "tasks"}
        keys.update(
            str(name).strip().casefold()
            for name in self.config.get("folio_sync_notebooks") or []
            if str(name).strip()
        )
        return keys

    def _partner_sync_path_available(self) -> bool:
        """True only when the sync folder exists AND has a recently-modified note file.

        Uses a single stat() on a known file rather than iterdir() to avoid
        InterruptedError from signals hitting os.listdir() during startup.
        """
        import time
        path = SupernoteWatcher.DEFAULT_SYNC_PATH
        if not path.exists():
            return False
        cutoff = time.time() - 7 * 24 * 3600  # 7 days
        for name in ("Quick.note", "Walk.note", "LFW.note", "tasks.note"):
            try:
                mtime = (path / name).stat().st_mtime
                if mtime > cutoff:
                    return True
            except OSError:
                continue
        return False

    async def _on_note_changed(
        self,
        notebook_name: str,
        note_bytes: bytes,
        update_time: int | None = None,
    ) -> None:
        note_hash = hashlib.sha256(note_bytes).hexdigest()
        persisted_pages: set[int] = set()

        async def persist_result(result) -> None:
            source_revision = f"{update_time or 0}:{note_hash}:{result.page_num}"
            self.page_state.upsert_ocr_page(
                notebook=result.notebook,
                page=result.page_num,
                source_revision=source_revision,
                raw_text=result.text,
                ocr_model=self.config["zai_vision_model"],
            )
            persisted_pages.add(result.page_num)
            log.info("ocr_succeeded", notebook=result.notebook, page=result.page_num)

        results = await self.reader.process_file(
            note_bytes,
            notebook_name,
            on_result=persist_result,
        )
        for result in results:
            if result.page_num in persisted_pages:
                continue
            await persist_result(result)

        if notebook_name.casefold() == "walk":
            for result in results:
                await self.events.publish_walk_feedback_detected(
                    notebook=result.notebook,
                    page=result.page_num,
                    text=result.text,
                )

    async def start(self) -> None:
        log.info("ingest_service_starting")
        await self.events.start()
        try:
            if self._partner_sync_path_available():
                log.info(
                    "ingest_using_local_watcher",
                    path=str(SupernoteWatcher.DEFAULT_SYNC_PATH),
                )
                await self._run_with_local_watcher()
            else:
                log.info("ingest_using_cloud_poller")
                await self._run_with_cloud_poller()
        finally:
            await self.events.stop()

    async def _run_with_local_watcher(self) -> None:
        loop = asyncio.get_running_loop()

        def on_file_changed(path: Path, notebook_name: str) -> None:
            if notebook_name.casefold() not in self._watched_notebook_keys:
                return
            try:
                note_bytes = path.read_bytes()
            except OSError as exc:
                log.warning("local_read_failed", path=str(path), error=str(exc))
                return
            asyncio.run_coroutine_threadsafe(
                self._on_note_changed(notebook_name, note_bytes),
                loop,
            )

        self._local_watcher = SupernoteWatcher(on_note_changed=on_file_changed)
        self._local_watcher.start()
        log.info("ingest_local_watcher_started")
        try:
            await self._shutdown_event.wait()
        finally:
            self._local_watcher.stop()
            self._local_watcher = None
            log.info("ingest_local_watcher_stopped")

    async def _run_with_cloud_poller(self) -> None:
        uploader = self._uploader or SupernoteUploader()
        cloud_poller = self._cloud_poller or CloudPoller(
            uploader=uploader,
            on_note_changed=self._on_note_changed,
            poll_interval=self.config["poll_interval"],
            watched_notebooks=self._watched_notebook_keys,
            process_existing_on_start=False,
        )
        self._uploader = uploader
        self._cloud_poller = cloud_poller
        await uploader.start()
        cloud_poller.start()
        try:
            await self._wait_until_shutdown_or_poller_exit()
        except Exception as exc:
            log.error("ingest_service_exiting_after_poller_failure", error=str(exc))
            raise
        finally:
            with suppress(Exception):
                await cloud_poller.stop()
            with suppress(Exception):
                await uploader.stop()

    async def stop(self) -> None:
        self._shutdown_event.set()

    async def _wait_until_shutdown_or_poller_exit(self) -> None:
        if self._cloud_poller is None:
            await self._shutdown_event.wait()
            return

        poller_wait = getattr(self._cloud_poller, "wait", None)
        if poller_wait is None or not inspect.iscoroutinefunction(poller_wait):
            await self._shutdown_event.wait()
            return

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        poller_task = asyncio.create_task(poller_wait())

        done, pending = await asyncio.wait(
            {shutdown_task, poller_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task

        if poller_task in done:
            poller_task.result()
