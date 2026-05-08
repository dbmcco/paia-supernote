"""Tests for Supernote enrichment orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
