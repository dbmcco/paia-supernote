"""Enrichment service — normalizes Supernote page text and extracts diagram data via Z.AI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx


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
        zai_base_url: str = "https://api.z.ai/api/coding/paas/v4",
        zai_text_model: str = "glm-5.1",
    ) -> None:
        self.zai_api_key = zai_api_key or os.environ["ZAI_API_KEY"]
        self.zai_base_url = zai_base_url.rstrip("/")
        self.zai_text_model = zai_text_model

    async def enrich_page(self, *, notebook: str, page: int, raw_text: str) -> EnrichedPage:
        prompt = (
            "Normalize this Supernote page into readable markdown and a renderable diagram. "
            "Return strict JSON with keys markdown and diagram."
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
        diagram = payload.get("diagram") or {"kind": "none", "render_version": "1"}
        return EnrichedPage(
            markdown=payload.get("markdown", raw_text),
            diagram=diagram,
            summary=diagram.get("summary"),
            confidence=diagram.get("confidence"),
        )
