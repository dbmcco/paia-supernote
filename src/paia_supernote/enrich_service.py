"""Enrich service — consumes dirty page rows, enriches with LLM, upserts to Folio."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog

from .enrichment import SupernoteEnricher
from .folio import upsert_supernote_page
from .page_state import PageStateStore

log = structlog.get_logger(__name__)

# Retry policy: exponential backoff from 60s, capped at once per day, so a
# persistently failing page stops hammering the gateway every minute (the
# 2026-08-10 -> 2026-10-28 incident: ~4,500 enrich_failed for 5 pages).
ENRICH_BASE_RETRY_DELAY_SECONDS = 60
ENRICH_DAILY_RETRY_CAP_SECONDS = 86400


def categorize_enrich_exception(exc: BaseException) -> str:
    """Map an enrich-path exception to a fixed category (never free text)."""
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response is not None and exc.response.status_code == 429:
            return "rate_limited"
        return "http_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return "network"
    if isinstance(exc, (ValueError, KeyError, IndexError)):
        return "response_shape"
    return "unknown"


def format_enrich_error(exc: BaseException) -> str:
    """Never-empty typed error token: ``<ExceptionType>:<category>``.

    ``httpx.ReadTimeout()`` stringifies to the empty string — the exact
    incident shape — so the exception message is never trusted, only its
    type and category are recorded.
    """
    return f"{type(exc).__name__}:{categorize_enrich_exception(exc)}"


def enrich_retry_delay_seconds(retry_count: int) -> int:
    """Exponential backoff (60s * 2^retry_count), capped at once per day."""
    delay = ENRICH_BASE_RETRY_DELAY_SECONDS * (2 ** max(0, int(retry_count)))
    return min(delay, ENRICH_DAILY_RETRY_CAP_SECONDS)


def enrich_backoff_cap_retry_count() -> int:
    """Smallest ``retry_count`` whose delay reaches the daily cap."""
    count = 0
    while enrich_retry_delay_seconds(count) < ENRICH_DAILY_RETRY_CAP_SECONDS:
        count += 1
    return count


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
            zai_api_key=self.config["zai_api_key"],
            zai_base_url=self.config["zai_base_url"],
            zai_text_model=self.config["zai_text_model"],
        )
        self.folio_client = folio_upserter or folio_client or upsert_supernote_page
        self._shutdown_event = asyncio.Event()

    def _should_sync_to_folio(self, notebook: str) -> bool:
        configured = self.config.get("folio_sync_notebooks")
        if not configured:
            return True
        allowed = {str(item).strip().casefold() for item in configured if str(item).strip()}
        return str(notebook or "").strip().casefold() in allowed

    async def run_once(self) -> bool:
        row = self.page_state.next_dirty_page()
        if row is None:
            return False
        if not self._should_sync_to_folio(row.notebook):
            self.page_state.mark_enrichment_skipped(
                notebook=row.notebook,
                page=row.page,
                source_revision=row.source_revision,
            )
            log.info("enrich_skipped_notebook", notebook=row.notebook, page=row.page)
            return False
        log.info("enrich_started", notebook=row.notebook, page=row.page)
        try:
            enriched = await self.enricher.enrich_page(
                notebook=row.notebook,
                page=row.page,
                raw_text=row.raw_text,
            )
        except Exception as exc:
            error = format_enrich_error(exc)
            category = categorize_enrich_exception(exc)
            retry_delay = enrich_retry_delay_seconds(row.retry_count)
            self.page_state.mark_failed(
                notebook=row.notebook,
                page=row.page,
                stage="enrich",
                error=error,
                retry_delay_seconds=retry_delay,
            )
            log.error(
                "enrich_failed",
                notebook=row.notebook,
                page=row.page,
                error=error,
                category=category,
                retry_delay_seconds=retry_delay,
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
