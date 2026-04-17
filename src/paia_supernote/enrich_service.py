"""Enrich service — consumes dirty page rows, enriches with LLM, upserts to Folio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import structlog

from .enrichment import SupernoteEnricher
from .folio import upsert_supernote_page
from .page_state import PageStateStore

log = structlog.get_logger(__name__)


class EnrichService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        page_state: PageStateStore | None = None,
        enricher: SupernoteEnricher | None = None,
        folio_client: Callable | None = None,
        folio_upserter: Callable | None = None,
    ) -> None:
        from .main import load_config
        self.config = config or load_config()
        self.page_state = page_state or PageStateStore(Path(self.config["state_db_path"]))
        self.page_state.init_schema()
        self.enricher = enricher or SupernoteEnricher(
            zai_base_url=self.config["zai_base_url"],
            zai_text_model=self.config["zai_text_model"],
        )
        self.folio_client = folio_upserter or folio_client or upsert_supernote_page
        self._shutdown_event = asyncio.Event()

    async def run_once(self) -> bool:
        row = self.page_state.next_dirty_page()
        if row is None:
            return False
        log.info("enrich_started", notebook=row.notebook, page=row.page)
        try:
            enriched = await self.enricher.enrich_page(
                notebook=row.notebook,
                page=row.page,
                raw_text=row.raw_text,
            )
        except Exception as exc:
            self.page_state.mark_failed(
                notebook=row.notebook,
                page=row.page,
                stage="enrich",
                error=str(exc),
                retry_delay_seconds=60,
            )
            log.error(
                "enrich_failed",
                notebook=row.notebook,
                page=row.page,
                error=str(exc),
            )
            return False
        current = self.page_state.get_page(row.notebook, row.page)
        if current.source_revision != row.source_revision:
            log.info(
                "enrich_stale_revision_discarded",
                notebook=row.notebook,
                page=row.page,
            )
            return False
        result = await self.folio_client(
            notebook=row.notebook,
            page=row.page,
            source_revision=row.source_revision,
            raw_text=row.raw_text,
            markdown=enriched.markdown,
            diagram=enriched.diagram,
            folio_url=self.config["folio_url"],
        )
        self.page_state.mark_enriched(
            notebook=row.notebook,
            page=row.page,
            source_revision=row.source_revision,
            folio_object_id=result["id"],
        )
        log.info("enrich_succeeded", notebook=row.notebook, page=row.page)
        return True

    async def start(self) -> None:
        log.info("enrich_service_starting")
        while not self._shutdown_event.is_set():
            try:
                wrote = await self.run_once()
            except Exception as exc:
                log.error("enrich_error", error=str(exc))
                wrote = False
            if not wrote:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._shutdown_event.wait()), timeout=10.0
                    )
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._shutdown_event.set()
