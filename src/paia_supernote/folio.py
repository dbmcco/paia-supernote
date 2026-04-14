"""
ABOUTME: Folio integration — sends transcribed Supernote content to folio for indexing.
ABOUTME: POSTs note objects to folio's /api/folio/objects endpoint with supernote metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_FOLIO_URL = "http://localhost:8000"


async def send_to_folio(
    notebook: str,
    page: int,
    text: str,
    timestamp: str | None = None,
    agent: str | None = None,
    *,
    folio_url: str = _DEFAULT_FOLIO_URL,
) -> dict[str, Any] | None:
    """Send a transcribed note page to folio for indexing.

    Args:
        notebook: Notebook name (e.g. 'Quick', 'LFW', 'Synth').
        page: Page number within the notebook.
        text: Transcribed content.
        timestamp: ISO8601 timestamp. Defaults to now if not provided.
        agent: Agent name that reviewed the page, or None.
        folio_url: Base URL for folio service.

    Returns:
        The created object dict from folio, or None on failure.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    payload = {
        "title": f"{notebook} — page {page}",
        "content": text,
        "path": f"supernote/{notebook}/page-{page}",
        "object_type": "supernote-transcription",
        "properties": {
            "notebook": notebook,
            "page": page,
            "timestamp": timestamp,
            "agent": agent,
            "source": "supernote",
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{folio_url}/api/folio/objects",
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            result = resp.json()
            log.info(
                "folio_indexed",
                notebook=notebook,
                page=page,
                agent=agent,
            )
            return result
    except httpx.HTTPError as exc:
        log.warning(
            "folio_index_failed",
            notebook=notebook,
            page=page,
            error=str(exc),
        )
        return None
