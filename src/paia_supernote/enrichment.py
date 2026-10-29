"""Enrichment service — normalizes Supernote page text and extracts diagram data via Z.AI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from .model_config import (
    default_zai_base_url,
    default_zai_text_model,
    resolve_supernote_zai_api_key,
)

# The OCR reader path already allows 600s for the same gateway model family;
# enrichment used to hard-fail at 60s, which is shorter than glm-5.3 reasoning
# latency on real page content — the root cause of the 2026-08-10 empty-error
# storm (httpx.ReadTimeout('') stringifies to '').
ENRICH_TIMEOUT_SECONDS = 300.0
# A 429 means retry: bounded in-call retries honoring Retry-After, then raise.
MAX_429_RETRIES = 2
DEFAULT_RETRY_AFTER_SECONDS = 2.0


def _retry_after_seconds(response: httpx.Response) -> float:
    """Honor the Retry-After header when present; otherwise a small default."""
    value = response.headers.get("Retry-After")
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


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
            resp = None
            for attempt in range(MAX_429_RETRIES + 1):
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
                        # Mirror the reader's contract: non-openrouter chat
                        # surfaces get thinking disabled so reasoning latency
                        # stays bounded.
                        "thinking": {"type": "disabled"},
                    },
                    timeout=ENRICH_TIMEOUT_SECONDS,
                )
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    is_rate_limited = (
                        exc.response is not None and exc.response.status_code == 429
                    )
                    if is_rate_limited and attempt < MAX_429_RETRIES:
                        await asyncio.sleep(_retry_after_seconds(resp))
                        continue
                    raise
                break
            assert resp is not None
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
