"""Tests for the SupernoteEnricher that normalizes page text and diagram data."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from paia_supernote.enrichment import SupernoteEnricher


@pytest.mark.asyncio
@patch("paia_supernote.enrichment.httpx.AsyncClient")
async def test_enricher_returns_markdown_and_scene(mock_client) -> None:
    from unittest.mock import MagicMock

    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": (
                    '{"markdown": "# Plan\\n- ship it",'
                    ' "diagram": {"kind": "scene",'
                    ' "scene": {"nodes": [{"id": "n1", "label": "Start", "shape": "box",'
                    ' "x": 0.1, "y": 0.2}], "edges": []},'
                    ' "summary": "Simple flow", "confidence": 0.92, "render_version": "1"}}'
                )
            }
        }]
    }
    mock_http.post.return_value = mock_resp
    mock_client.return_value.__aenter__.return_value = mock_http

    enricher = SupernoteEnricher(zai_api_key="token")
    result = await enricher.enrich_page(notebook="Quick", page=19, raw_text="raw bullets")

    assert result.markdown == "# Plan\n- ship it"
    assert result.diagram["kind"] == "scene"
    assert result.diagram["scene"]["nodes"][0]["label"] == "Start"
