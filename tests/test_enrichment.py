"""Tests for Supernote enrichment orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from paia_supernote.enrichment import SupernoteEnricher


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_returns_markdown_and_scene(mock_client) -> None:
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": """
                    {
                      "markdown": "# Plan\\n- ship it",
                      "diagram": {
                        "kind": "scene",
                        "scene": {
                          "nodes": [
                            {
                              "id": "n1",
                              "label": "Start",
                              "shape": "box",
                              "x": 0.1,
                              "y": 0.2
                            }
                          ],
                          "edges": []
                        },
                        "summary": "Simple flow",
                        "confidence": 0.92,
                        "render_version": "1"
                      }
                    }
                    """
                }
            }
        ]
    }
    mock_http.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    enricher = SupernoteEnricher(zai_api_key="token")
    result = await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw bullets")

    assert result.markdown == "# Plan\n- ship it"
    assert result.diagram["kind"] == "scene"
    assert result.diagram["scene"]["nodes"][0]["label"] == "Start"
    assert result.summary == "Simple flow"
    assert result.confidence == 0.92


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_normalizes_string_diagram_to_mermaid(mock_client) -> None:
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": """
                    {
                      "markdown": "# Flow",
                      "diagram": "graph TD\\n  A[Start] --> B[Done]"
                    }
                    """
                }
            }
        ]
    }
    mock_http.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    enricher = SupernoteEnricher(zai_api_key="token")
    result = await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw flow")

    assert result.markdown == "# Flow"
    assert result.diagram["kind"] == "mermaid"
    assert "A[Start]" in result.diagram["mermaid"]
    assert result.diagram["render_version"] == "1"
    assert result.summary is None
    assert result.confidence is None


def _ok_response(content: str = '{"markdown": "# ok", "diagram": {"kind": "none", "render_version": "1"}}') -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock_response


def _rate_limited_response(retry_after: str | None = "2") -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_429():
        raise httpx.HTTPStatusError(
            "Client error '429 Too Many Requests'",
            request=httpx.Request("POST", "http://gateway.test/v1/chat/completions"),
            response=httpx.Response(429, request=httpx.Request("POST", "http://gateway.test/v1/chat/completions")),
        )

    mock_response.raise_for_status.side_effect = raise_429
    return mock_response


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_uses_long_timeout_for_reasoning_model_latency(mock_client) -> None:
    """Root cause of the 2026-08-10 empty-error storm: 60s timeout was shorter
    than gateway glm-5.3 reasoning latency on real page content (the OCR reader
    path already uses 600s for the same model family)."""
    mock_http = AsyncMock()
    mock_http.post.return_value = _ok_response()
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    enricher = SupernoteEnricher(zai_api_key="token")
    await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw")

    assert mock_http.post.call_args.kwargs["timeout"] >= 300.0


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_disables_thinking_like_the_reader_path(mock_client) -> None:
    """Mirror the reader's contract: non-openrouter chat surfaces get thinking
    disabled so reasoning latency stays bounded."""
    mock_http = AsyncMock()
    mock_http.post.return_value = _ok_response()
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    enricher = SupernoteEnricher(zai_api_key="token")
    await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw")

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload.get("thinking") == {"type": "disabled"}


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_retries_429_per_retry_after_then_succeeds(mock_client) -> None:
    """A 429 means retry: bounded in-call retry honoring Retry-After."""
    mock_http = AsyncMock()
    ok = _ok_response()
    mock_http.post.side_effect = [_rate_limited_response(), _rate_limited_response(), ok]
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    with patch("paia_supernote.enrichment.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        enricher = SupernoteEnricher(zai_api_key="token")
        result = await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw")

    assert result.markdown == "# ok"
    assert mock_http.post.await_count == 3
    # both retries honor the 2s Retry-After header
    assert [c.args[0] for c in mock_sleep.await_args_list] == [2.0, 2.0]


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_raises_after_bounded_429_retries(mock_client) -> None:
    mock_http = AsyncMock()
    mock_http.post.return_value = _rate_limited_response()
    mock_client.return_value.__aenter__.return_value = mock_http
    mock_client.return_value.__aexit__.return_value = False

    enricher = SupernoteEnricher(zai_api_key="token")
    with pytest.raises(httpx.HTTPStatusError):
        await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw")

    assert mock_http.post.await_count == 3  # bounded: 1 initial + 2 retries
