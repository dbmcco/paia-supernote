"""Ingest service — polls Supernote Cloud, OCRs pages, writes durable page state."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
import hashlib
from pathlib import Path
from typing import Any

import structlog

from .cloud_poller import CloudPoller
from .page_state import PageStateStore
from .reader import SupernoteReader
from .uploader import SupernoteUploader

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
        self.uploader = uploader or SupernoteUploader()
        self.page_state = PageStateStore(Path(self.config["state_db_path"]))
        self.page_state.init_schema()
        self.cloud_poller = cloud_poller or CloudPoller(
            uploader=self.uploader,
            on_note_changed=self._on_note_changed,
            poll_interval=self.config["poll_interval"],
            watched_notebooks=self.config.get("folio_sync_notebooks"),
            process_existing_on_start=False,
        )
        self._shutdown_event = asyncio.Event()

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

    async def start(self) -> None:
        log.info("ingest_service_starting")
        await self.uploader.start()
        self.cloud_poller.start()
        try:
            await self._wait_until_shutdown_or_poller_exit()
        except Exception as exc:
            log.error("ingest_service_exiting_after_poller_failure", error=str(exc))
            with suppress(Exception):
                await self.cloud_poller.stop()
            with suppress(Exception):
                await self.uploader.stop()
            raise

    async def stop(self) -> None:
        await self.cloud_poller.stop()
        await self.uploader.stop()
        self._shutdown_event.set()

    async def _wait_until_shutdown_or_poller_exit(self) -> None:
        poller_wait = getattr(self.cloud_poller, "wait", None)
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
