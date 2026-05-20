"""Enrichment service — normalizes Supernote page text and extracts diagram data via Z.AI."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .model_config import (
    default_zai_base_url,
    default_zai_text_model,
    resolve_supernote_zai_api_key,
)


@dataclass(slots=True)
class EnrichedPage:
    markdown: str
    diagram: dict[str, object]
    summary: str | None
    confidence: float | None


class SupernoteEnricher:
    def __init__(
        self,
        *,
        zai_api_key: str | None = None,
        zai_base_url: str | None = None,
        zai_text_model: str | None = None,
    ) -> None:
        self.zai_api_key = zai_api_key or resolve_supernote_zai_api_key()
        if not self.zai_api_key:
            raise RuntimeError(
                "The configured Supernote model API key is required when using the zai enrichment backend"
            )
        self.zai_base_url = (zai_base_url or default_zai_base_url()).rstrip("/")
        self.zai_text_model = zai_text_model or default_zai_text_model()

    async def enrich_page(self, *, notebook: str, page: int, raw_text: str) -> EnrichedPage:
        prompt = (
            "Normalize this Supernote page into readable markdown and a renderable diagram. "
            "Return strict JSON with keys markdown and diagram. "
            "diagram must be either "
            "{\"kind\":\"scene\",\"render_version\":\"1\",\"scene\":{\"nodes\":[],\"edges\":[]},\"summary\":null,\"confidence\":null}, "
            "{\"kind\":\"mermaid\",\"render_version\":\"1\",\"mermaid\":\"graph TD\\nA-->B\",\"summary\":null,\"confidence\":null}, "
            "or {\"kind\":\"none\",\"render_version\":\"1\",\"summary\":null,\"confidence\":null}."
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.zai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.zai_api_key}"},
                json={
                    "model": self.zai_text_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"{prompt}\n\nNotebook: {notebook}\nPage: {page}\n\n{raw_text}",
                        }
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            payload = json.loads(resp.json()["choices"][0]["message"]["content"])
        raw_diagram = payload.get("diagram")
        if isinstance(raw_diagram, dict):
            diagram = raw_diagram
        elif isinstance(raw_diagram, str) and raw_diagram.strip():
            diagram = {
                "kind": "mermaid",
                "render_version": "1",
                "mermaid": raw_diagram,
            }
        else:
            diagram = {"kind": "none", "render_version": "1"}
        return EnrichedPage(
            markdown=payload.get("markdown", raw_text),
            diagram=diagram,
            summary=diagram.get("summary"),
            confidence=diagram.get("confidence"),
        )
