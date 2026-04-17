"""Tests for folio integration — send_to_folio() and upsert_supernote_page() post correct payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from paia_supernote.folio import send_to_folio, upsert_supernote_page


class TestSendToFolio:
    """send_to_folio() posts correct payload to folio's objects endpoint."""

    @pytest.mark.asyncio
    async def test_posts_correct_payload(self) -> None:
        ts = "2026-04-14T12:00:00+00:00"

        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": "abc-123", "title": "Quick — page 1"}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_to_folio(
                notebook="Quick",
                page=1,
                text="Meeting notes here",
                timestamp=ts,
                agent="Caroline",
            )

            mock_http.post.assert_called_once()
            call_args = mock_http.post.call_args
            url = call_args[0][0]
            body = call_args[1]["json"]

            assert url == "http://localhost:8000/api/folio/objects"
            assert body["title"] == "Quick — page 1"
            assert body["content"] == "Meeting notes here"
            assert body["object_type"] == "supernote-transcription"
            assert body["properties"]["notebook"] == "Quick"
            assert body["properties"]["page"] == 1
            assert body["properties"]["timestamp"] == ts
            assert body["properties"]["agent"] == "Caroline"
            assert body["properties"]["source"] == "supernote"
            assert result == {"id": "abc-123", "title": "Quick — page 1"}

    @pytest.mark.asyncio
    async def test_timestamp_is_iso8601(self) -> None:
        """When no timestamp provided, defaults to ISO8601 now."""
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await send_to_folio(notebook="Synth", page=5, text="ideas")

            body = mock_http.post.call_args[1]["json"]
            ts = body["properties"]["timestamp"]
            # Should parse as valid ISO8601
            parsed = datetime.fromisoformat(ts)
            assert parsed.tzinfo is not None  # timezone-aware

    @pytest.mark.asyncio
    async def test_agent_is_none_when_no_agent(self) -> None:
        """agent field is None when no agent reviewed the page."""
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await send_to_folio(notebook="Quick", page=1, text="notes")

            body = mock_http.post.call_args[1]["json"]
            assert body["properties"]["agent"] is None

    @pytest.mark.asyncio
    async def test_source_is_always_supernote(self) -> None:
        """source field is always 'supernote'."""
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await send_to_folio(
                notebook="LFW", page=3, text="strategy", agent="Ingrid"
            )

            body = mock_http.post.call_args[1]["json"]
            assert body["properties"]["source"] == "supernote"

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        """HTTP failure returns None instead of raising."""
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_to_folio(notebook="Quick", page=1, text="text")
            assert result is None

    @pytest.mark.asyncio
    async def test_custom_folio_url(self) -> None:
        """folio_url parameter overrides the default."""
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await send_to_folio(
                notebook="Quick",
                page=1,
                text="text",
                folio_url="http://folio.internal:9000",
            )

            url = mock_http.post.call_args[0][0]
            assert url == "http://folio.internal:9000/api/folio/objects"


class TestUpsertSupernotePage:
    """upsert_supernote_page() posts stable-path supernote-page object to folio."""

    @pytest.mark.asyncio
    async def test_upsert_supernote_page_posts_stable_path_payload(self) -> None:
        with patch("paia_supernote.folio.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": "obj-1", "path": "supernote/Quick/page-19"}
            mock_http.post.return_value = mock_resp
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await upsert_supernote_page(
                notebook="Quick",
                page=19,
                source_revision="rev-2",
                raw_text="raw",
                markdown="# Plan",
                diagram={"kind": "scene", "scene": {"nodes": [], "edges": []}, "render_version": "1"},
                folio_url="http://localhost:8000",
            )

            body = mock_http.post.call_args.kwargs["json"]
            assert body["path"] == "supernote/Quick/page-19"
            assert body["object_type"] == "supernote-page"
            assert body["content"] == "# Plan"
            assert body["properties"]["raw_text"] == "raw"
            assert body["properties"]["diagram"]["kind"] == "scene"
            assert body["properties"]["source"]["source_revision"] == "rev-2"
            assert result["id"] == "obj-1"
