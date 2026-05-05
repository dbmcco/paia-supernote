"""Tests for paia-events HTTP client integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from paia_supernote.events import EventsClient, EventsManager


@pytest.fixture
def client() -> EventsClient:
    return EventsClient(base_url="http://localhost:3511")


class TestEventPublishing:
    """Outbound publish methods POST correct payloads."""

    @pytest.mark.asyncio
    async def test_publish_note_transcribed(self, client: EventsClient) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_note_transcribed(
                "Quick", 1, "Meeting notes here", timestamp=1713100000.0
            )

            mock_http.post.assert_called_once()
            call_kwargs = mock_http.post.call_args
            body = call_kwargs[1]["json"]
            assert body["event_type"] == "supernote.note.transcribed"
            assert body["source_app"] == "paia-supernote"
            assert "occurred_at" in body
            assert body["payload"]["notebook"] == "Quick"
            assert body["payload"]["page"] == 1
            assert body["payload"]["text"] == "Meeting notes here"
            assert body["payload"]["timestamp"] == 1713100000.0

    @pytest.mark.asyncio
    async def test_publish_checkbox_completed(self, client: EventsClient) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_checkbox_completed(
                task_id="task-123",
                notebook="Quick",
                page=2,
                task_text="Buy groceries",
                tag="focus",
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["event_type"] == "supernote.checkbox.completed"
            assert "occurred_at" in body
            assert body["payload"]["task_id"] == "task-123"
            assert body["payload"]["task_text"] == "Buy groceries"
            assert body["payload"]["notebook"] == "Quick"
            assert body["payload"]["page"] == 2
            assert body["payload"]["tag"] == "focus"

    @pytest.mark.asyncio
    async def test_publish_snippet_detected(self, client: EventsClient) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_snippet_detected(
                "LFW", 3, "Strategy pivot", "Caroline"
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["event_type"] == "supernote.snippet.detected"
            assert "occurred_at" in body
            assert body["payload"]["agent"] == "Caroline"
            assert body["payload"]["text"] == "Strategy pivot"

    @pytest.mark.asyncio
    async def test_publish_walk_feedback_detected_is_model_decision_evidence(
        self, client: EventsClient
    ) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_walk_feedback_detected(
                notebook="Walk",
                page=1,
                text="Gene history exists from last week.",
                source_revision="rev-1",
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["event_type"] == "supernote.walk.feedback.detected"
            assert body["payload"]["schema_version"] == "supernote-walk-feedback-v1"
            assert body["payload"]["decision_owner"] == "model"
            assert body["payload"]["text"] == "Gene history exists from last week."

    @pytest.mark.asyncio
    async def test_publish_write_completed_includes_request_correlation(
        self, client: EventsClient
    ) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_write_completed(
                request_event_id=42,
                request_source_event_id="source-42",
                run_id="run-42",
                agent="Sam",
                notebook="Walk",
                content_type="replace_pages",
                page_count=2,
                artifact_refs={"notebook": "Walk.note"},
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["event_type"] == "supernote.write.completed"
            assert body["dedupe_key"] == "supernote.write.completed:42"
            assert body["payload"]["request_event_id"] == 42
            assert body["payload"]["request_source_event_id"] == "source-42"
            assert body["payload"]["run_id"] == "run-42"
            assert body["payload"]["agent"] == "Sam"
            assert body["payload"]["notebook"] == "Walk"
            assert body["payload"]["page_count"] == 2
            assert body["payload"]["artifact_refs"] == {"notebook": "Walk.note"}

    @pytest.mark.asyncio
    async def test_publish_write_failed_includes_error_and_request_correlation(
        self, client: EventsClient
    ) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.publish_write_failed(
                request_event_id=43,
                request_source_event_id="source-43",
                run_id="run-43",
                agent="Sam",
                notebook="Walk",
                content_type="replace_pages",
                page_count=2,
                error="upload_failed",
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["event_type"] == "supernote.write.failed"
            assert body["dedupe_key"] == "supernote.write.failed:43"
            assert body["payload"]["request_event_id"] == 43
            assert body["payload"]["request_source_event_id"] == "source-43"
            assert body["payload"]["run_id"] == "run-43"
            assert body["payload"]["error"] == "upload_failed"

    @pytest.mark.asyncio
    async def test_publish_survives_http_error(self, client: EventsClient) -> None:
        """Publish failure is logged but does not raise."""
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await client.publish_note_transcribed("Quick", 1, "text")


class TestSubscriberRegistration:
    """Subscriber registration on start."""

    @pytest.mark.asyncio
    async def test_start_registers_subscriber(self, client: EventsClient) -> None:
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.return_value = MagicMock(raise_for_status=MagicMock())
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Stop poll loop immediately
            with patch.object(client, "_poll_loop", new_callable=AsyncMock):
                await client.start()

            mock_http.post.assert_called_once()
            body = mock_http.post.call_args[1]["json"]
            assert body["name"] == "paia-supernote"
            assert body["event_type_prefix"] == "supernote.write.requested"

    @pytest.mark.asyncio
    async def test_start_survives_registration_failure(
        self, client: EventsClient
    ) -> None:
        """Failed subscriber registration does not prevent startup."""
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch.object(client, "_poll_loop", new_callable=AsyncMock):
                await client.start()  # Should not raise


class TestInboundPollLoop:
    """Poll loop fetches events and dispatches to handler."""

    @pytest.mark.asyncio
    async def test_dispatches_write_requested_to_handler(
        self, client: EventsClient
    ) -> None:
        received: list[dict] = []

        async def handler(payload: dict) -> None:
            received.append(payload)

        client.register_write_handler(handler)

        fake_events = [
            {
                "id": 1,
                "payload": {
                    "agent": "Sam",
                    "notebook": "Quick",
                    "content": "hi",
                    "content_type": "text",
                },
            },
            {
                "id": 2,
                "payload": {
                    "agent": "Caroline",
                    "notebook": "LFW",
                    "content": "strategy",
                    "content_type": "text",
                },
            },
        ]

        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"events": fake_events, "ok": True}
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._fetch_and_dispatch()

        assert len(received) == 2
        assert received[0]["agent"] == "Sam"
        assert received[0]["request_event_id"] == 1
        assert received[1]["agent"] == "Caroline"

    @pytest.mark.asyncio
    async def test_cursor_advances_after_dispatch(self, client: EventsClient) -> None:
        """since_id advances to the highest event id seen."""
        client.register_write_handler(AsyncMock())

        fake_events = [{"id": 5, "payload": {}}, {"id": 7, "payload": {}}]

        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"events": fake_events, "ok": True}
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._fetch_and_dispatch()

        assert client._since_id == 7

    @pytest.mark.asyncio
    async def test_handler_error_does_not_stop_cursor_advance(
        self, client: EventsClient
    ) -> None:
        """Cursor still advances even if handler raises, to avoid infinite replay."""

        async def bad_handler(payload: dict) -> None:
            raise RuntimeError("boom")

        client.register_write_handler(bad_handler)

        fake_events = [{"id": 10, "payload": {}}]

        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"events": fake_events, "ok": True}
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._fetch_and_dispatch()  # Should not raise

        assert client._since_id == 10

    @pytest.mark.asyncio
    async def test_no_dispatch_without_handler(self, client: EventsClient) -> None:
        """No handler registered → fetch is skipped entirely."""
        with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._fetch_and_dispatch()

            mock_http.get.assert_not_called()


class TestBackwardsCompat:
    """EventsManager alias resolves to EventsClient."""

    def test_events_manager_is_alias(self) -> None:
        assert EventsManager is EventsClient
