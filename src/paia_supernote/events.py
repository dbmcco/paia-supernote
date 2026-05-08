"""
ABOUTME: PAIA events integration module
ABOUTME: Publishes/polls paia-events HTTP API (port 3511) — no MQTT, no direct DB.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

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
        self._write_handler: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = (
            None
        )
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
        """Register async handler for supernote.write.requested events."""
        self._write_handler = handler

    async def _register_subscriber(self) -> None:
        """Register paia-supernote as a subscriber for write.requested events."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/v1/subscribers",
                    json={
                        "name": _SUBSCRIBER_NAME,
                        "event_type_prefix": "supernote.write.requested",
                    },
                    timeout=5.0,
                )
                resp.raise_for_status()
                log.info("subscriber_registered", name=_SUBSCRIBER_NAME)
        except httpx.HTTPError as exc:
            # Non-fatal: if already registered or service is down, poll will still work
            log.warning("subscriber_registration_failed", error=str(exc))

    async def _poll_loop(self) -> None:
        """Poll for inbound write.requested events on a fixed interval."""
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
            payload: dict[str, Any] = dict(event.get("payload", {}))
            payload.setdefault("request_event_id", event_id)
            if event.get("source_event_id") is not None:
                payload.setdefault(
                    "request_source_event_id", event.get("source_event_id")
                )
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
        """Publish supernote.note.transcribed event."""
        ts = timestamp if timestamp is not None else time.time()
        await self._publish(
            event_type="supernote.note.transcribed",
            payload={"notebook": notebook, "page": page, "text": text, "timestamp": ts},
            dedupe_key=f"supernote.note_transcribed:{notebook}:{page}:{int(ts)}",
            occurred_at=datetime.fromtimestamp(ts, tz=timezone.utc),
        )

    async def publish_checkbox_completed(
        self,
        task_text: str,
        notebook: str,
        page: int,
        task_id: str = "",
        tag: str = "",
    ) -> None:
        """Publish supernote.checkbox.completed event."""
        _task_id = task_id or task_text[:32]
        await self._publish(
            event_type="supernote.checkbox.completed",
            payload={
                "task_id": _task_id,
                "notebook": notebook,
                "page": page,
                "task_text": task_text,
                "tag": tag,
            },
            dedupe_key=f"supernote.checkbox_completed:{notebook}:{page}:{_task_id}",
            occurred_at=datetime.now(timezone.utc),
        )

    async def publish_snippet_detected(
        self, notebook: str, page: int, text: str, agent: str
    ) -> None:
        """Publish supernote.snippet.detected event."""
        await self._publish(
            event_type="supernote.snippet.detected",
            payload={"notebook": notebook, "page": page, "text": text, "agent": agent},
            dedupe_key=f"supernote.snippet_detected:{notebook}:{page}:{text[:32]}",
            occurred_at=datetime.now(timezone.utc),
        )

    async def publish_walk_feedback_detected(
        self,
        *,
        notebook: str,
        page: int,
        text: str,
        source_revision: str | None = None,
    ) -> None:
        """Publish Walk-note handwriting as model-readable feedback evidence."""
        revision = source_revision or str(uuid.uuid4())
        await self._publish(
            event_type="supernote.walk_feedback.detected",
            payload={
                "schema_version": "supernote-walk-feedback-v1",
                "notebook": notebook,
                "page": page,
                "text": text,
                "source_revision": source_revision,
                "surface": "daily_walk",
                "decision_owner": "model",
            },
            dedupe_key=f"supernote.walk.feedback:{notebook}:{page}:{revision}",
            occurred_at=datetime.now(timezone.utc),
        )

    async def publish_write_completed(
        self,
        *,
        request_event_id: int | str | None = None,
        request_source_event_id: str | None = None,
        run_id: str | None = None,
        agent: str,
        notebook: str,
        content_type: str | None,
        page_count: int,
        artifact_refs: dict[str, Any] | None = None,
    ) -> None:
        """Publish supernote.write.completed with request correlation."""
        correlation_id = str(
            request_event_id or request_source_event_id or run_id or uuid.uuid4()
        )
        await self._publish(
            event_type="supernote.write.completed",
            payload={
                "request_event_id": request_event_id,
                "request_source_event_id": request_source_event_id,
                "run_id": run_id,
                "agent": agent,
                "notebook": notebook,
                "content_type": content_type,
                "page_count": page_count,
                "artifact_refs": artifact_refs or {},
            },
            dedupe_key=f"supernote.write.completed:{correlation_id}",
            occurred_at=datetime.now(timezone.utc),
        )

    async def publish_write_failed(
        self,
        *,
        request_event_id: int | str | None = None,
        request_source_event_id: str | None = None,
        run_id: str | None = None,
        agent: str | None,
        notebook: str | None,
        content_type: str | None,
        page_count: int = 0,
        error: str,
    ) -> None:
        """Publish supernote.write.failed with request correlation."""
        correlation_id = str(
            request_event_id or request_source_event_id or run_id or uuid.uuid4()
        )
        await self._publish(
            event_type="supernote.write.failed",
            payload={
                "request_event_id": request_event_id,
                "request_source_event_id": request_source_event_id,
                "run_id": run_id,
                "agent": agent,
                "notebook": notebook,
                "content_type": content_type,
                "page_count": page_count,
                "error": error,
            },
            dedupe_key=f"supernote.write.failed:{correlation_id}",
            occurred_at=datetime.now(timezone.utc),
        )

    async def _publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        occurred_at: datetime,
    ) -> None:
        """POST /v1/events — fire and forget with warning on failure."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/v1/events",
                    json={
                        "event_type": event_type,
                        "source_app": "paia-supernote",
                        "source_event_id": str(uuid.uuid4()),
                        "occurred_at": occurred_at.isoformat(),
                        "dedupe_key": dedupe_key,
                        "payload": payload,
                    },
                    timeout=5.0,
                )
                resp.raise_for_status()
            log.debug("event_published", event_type=event_type)
        except httpx.HTTPError as exc:
            log.warning("event_publish_failed", event_type=event_type, error=str(exc))


# Backwards-compatible alias used by main.py
EventsManager = EventsClient
