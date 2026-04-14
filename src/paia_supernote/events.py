"""
ABOUTME: PAIA events integration module
ABOUTME: Publishes/polls paia-events HTTP API (port 3511) — no MQTT, no direct DB.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Awaitable, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "http://localhost:3511"
_SUBSCRIBER_NAME = "paia-supernote"
_POLL_INTERVAL = 5.0  # seconds between inbound event polls


class EventsClient:
    """HTTP client for paia-events — publish outbound, poll inbound."""

    def __init__(self, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url
        self._write_handler: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._since_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register subscriber and start inbound poll loop."""
        await self._register_subscriber()
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("events_client_started", base_url=self._base_url)

    async def stop(self) -> None:
        """Cancel poll loop."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        log.info("events_client_stopped")

    # ------------------------------------------------------------------
    # Inbound subscription
    # ------------------------------------------------------------------

    def register_write_handler(
        self, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register async handler for supernote.write_requested events."""
        self._write_handler = handler

    async def _register_subscriber(self) -> None:
        """Register paia-supernote as a subscriber for write_requested events."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/v1/subscribers",
                    json={
                        "name": _SUBSCRIBER_NAME,
                        "event_type_prefix": "supernote.write_requested",
                    },
                    timeout=5.0,
                )
                resp.raise_for_status()
                log.info("subscriber_registered", name=_SUBSCRIBER_NAME)
        except httpx.HTTPError as exc:
            # Non-fatal: if already registered or service is down, poll will still work
            log.warning("subscriber_registration_failed", error=str(exc))

    async def _poll_loop(self) -> None:
        """Poll for inbound write_requested events on a fixed interval."""
        while True:
            try:
                await self._fetch_and_dispatch()
            except Exception as exc:
                log.warning("poll_error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)

    async def _fetch_and_dispatch(self) -> None:
        """Fetch pending events and dispatch to registered handler."""
        if self._write_handler is None:
            return

        params: dict[str, Any] = {"limit": 50}
        if self._since_id is not None:
            params["since_id"] = self._since_id

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/v1/subscribers/{_SUBSCRIBER_NAME}/events",
                params=params,
                timeout=5.0,
            )
            resp.raise_for_status()

        data = resp.json()
        events: list[dict[str, Any]] = data.get("events", [])

        for event in events:
            event_id: int = event["id"]
            payload: dict[str, Any] = event.get("payload", {})
            try:
                await self._write_handler(payload)
            except Exception as exc:
                log.error("write_handler_error", event_id=event_id, error=str(exc))
            # Advance cursor even on handler error so we don't replay forever
            if self._since_id is None or event_id > self._since_id:
                self._since_id = event_id

    # ------------------------------------------------------------------
    # Outbound publishing
    # ------------------------------------------------------------------

    async def publish_note_transcribed(
        self, notebook: str, page: int, text: str, timestamp: float | None = None
    ) -> None:
        """Publish supernote.note_transcribed event."""
        ts = timestamp if timestamp is not None else time.time()
        await self._publish(
            event_type="supernote.note_transcribed",
            payload={"notebook": notebook, "page": page, "text": text, "timestamp": ts},
            dedupe_key=f"supernote.note_transcribed:{notebook}:{page}:{int(ts)}",
        )

    async def publish_checkbox_completed(
        self,
        task_text: str,
        notebook: str,
        page: int,
        task_id: str = "",
        tag: str = "",
    ) -> None:
        """Publish supernote.checkbox_completed event."""
        _task_id = task_id or task_text[:32]
        await self._publish(
            event_type="supernote.checkbox_completed",
            payload={
                "task_id": _task_id,
                "notebook": notebook,
                "page": page,
                "task_text": task_text,
                "tag": tag,
            },
            dedupe_key=f"supernote.checkbox_completed:{notebook}:{page}:{_task_id}",
        )

    async def publish_snippet_detected(
        self, notebook: str, page: int, text: str, agent: str
    ) -> None:
        """Publish supernote.snippet_detected event."""
        await self._publish(
            event_type="supernote.snippet_detected",
            payload={"notebook": notebook, "page": page, "text": text, "agent": agent},
            dedupe_key=f"supernote.snippet_detected:{notebook}:{page}:{text[:32]}",
        )

    async def _publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> None:
        """POST /v1/events — fire and forget with warning on failure."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._base_url}/v1/events",
                    json={
                        "event_type": event_type,
                        "source_app": "paia-supernote",
                        "source_event_id": str(uuid.uuid4()),
                        "dedupe_key": dedupe_key,
                        "payload": payload,
                    },
                    timeout=5.0,
                )
            log.debug("event_published", event_type=event_type)
        except httpx.HTTPError as exc:
            log.warning("event_publish_failed", event_type=event_type, error=str(exc))


# Backwards-compatible alias used by main.py
EventsManager = EventsClient
