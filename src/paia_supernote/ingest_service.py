"""Ingest service — polls Supernote Cloud, OCRs pages, writes durable page state."""

from __future__ import annotations

import asyncio
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
        )
        self._shutdown_event = asyncio.Event()

    async def _on_note_changed(
        self,
        notebook_name: str,
        note_bytes: bytes,
        update_time: int | None = None,
    ) -> None:
        results = await self.reader.process_file(note_bytes, notebook_name)
        note_hash = hashlib.sha256(note_bytes).hexdigest()
        for result in results:
            source_revision = f"{update_time or 0}:{note_hash}:{result.page_num}"
            self.page_state.upsert_ocr_page(
                notebook=result.notebook,
                page=result.page_num,
                source_revision=source_revision,
                raw_text=result.text,
                ocr_model=self.config["zai_vision_model"],
            )
            log.info("ocr_succeeded", notebook=result.notebook, page=result.page_num)

    async def start(self) -> None:
        log.info("ingest_service_starting")
        await self.uploader.start()
        self.cloud_poller.start()
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        await self.cloud_poller.stop()
        await self.uploader.stop()
        self._shutdown_event.set()
