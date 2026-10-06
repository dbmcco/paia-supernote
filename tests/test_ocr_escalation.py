"""Task 7: exact-transcription contract + exactly-once hosted OCR escalation.

Contract tests for ``escalate_if_needed`` / ``needs_escalation`` plus reader
wiring tests: local ollama (contract prompt + think-block prefill), hosted
AtlasCloud escalation (openai/gpt-5.6-luna), never-empty guarantees.
Spec: docs/superpowers/specs/2026-10-06-model-routing-split-design.md §4.4.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from PIL import Image

from paia_supernote.ocr_contract import (
    ILLEGIBLE_MARKER,
    TRANSCRIPTION_PROMPT,
    escalate_if_needed,
    needs_escalation,
)
from paia_supernote.reader import SupernoteReader

OLLAMA_THINK_PREFILL = "<think>\n\n</think>\n\n"


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (2, 2), color="white")


def _weak_local(n: int = 4) -> str:
    return " ".join([f"word{i} {ILLEGIBLE_MARKER}" for i in range(n)])


class _FakeAsyncResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSyncResponse(_FakeAsyncResponse):
    pass


def _ollama_payload(content: str) -> dict[str, Any]:
    return {"message": {"content": content}}


def _hosted_payload(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


class FakeHttp:
    """Patches httpx.AsyncClient/Client; routes by URL substring."""

    def __init__(self, monkeypatch, *, ollama_response=None, ollama_exc=None,
                 hosted_response=None, hosted_exc=None):
        self.calls: list[dict[str, Any]] = []
        self._ollama_response = ollama_response
        self._ollama_exc = ollama_exc
        self._hosted_response = hosted_response
        self._hosted_exc = hosted_exc
        outer = self

        class AsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, **kwargs):
                return outer._handle(url, json, sync=False)

        class SyncClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, **kwargs):
                return outer._handle(url, json, sync=True)

        monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
        monkeypatch.setattr(httpx, "Client", SyncClient)

    def _handle(self, url, body, *, sync):
        is_hosted = "atlascloud" in url or "/chat/completions" in url
        self.calls.append({"url": url, "json": body, "hosted": is_hosted})
        if is_hosted:
            if self._hosted_exc is not None:
                raise self._hosted_exc
            return _FakeSyncResponse(_hosted_payload(self._hosted_response or ""))
        if self._ollama_exc is not None:
            raise self._ollama_exc
        return _FakeAsyncResponse(_ollama_payload(self._ollama_response or ""))

    @property
    def hosted_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["hosted"]]

    @property
    def local_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if not c["hosted"]]


# --- Contract: escalate_if_needed -----------------------------------------


def test_escalate_four_markers_calls_hosted_once_returns_better():
    calls: list[str] = []

    def hosted(_local: str) -> str:
        calls.append(_local)
        return "clean hosted transcription"

    result = escalate_if_needed(_weak_local(4), hosted)
    assert result == "clean hosted transcription"
    assert calls == [_weak_local(4)]  # exactly one hosted call


def test_one_marker_never_escalates():
    def hosted(_local: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("hosted route must not be called")

    assert escalate_if_needed(f"one {ILLEGIBLE_MARKER} marker", hosted).startswith("one")


def test_empty_local_escalates_and_returns_hosted():
    def hosted(_local: str) -> str:
        return "hosted text"

    assert escalate_if_needed("   ", hosted) == "hosted text"


def test_hosted_worse_than_local_keeps_local():
    calls: list[str] = []

    def hosted(_local: str) -> str:
        calls.append(_local)
        return _weak_local(9)  # far worse

    local = _weak_local(4)
    assert escalate_if_needed(local, hosted) == local
    assert len(calls) == 1  # still exactly one attempt


def test_hosted_empty_result_falls_back_to_local():
    local = _weak_local(4)

    def hosted(_local: str) -> str:
        return ""

    assert escalate_if_needed(local, hosted) == local


def test_threshold_boundary():
    assert not needs_escalation(f"{ILLEGIBLE_MARKER} {ILLEGIBLE_MARKER}")
    assert needs_escalation(f"{ILLEGIBLE_MARKER} " * 3)
    assert needs_escalation("")
    assert not needs_escalation("plain text")


# --- Reader wiring: local ollama leg --------------------------------------


@pytest.mark.asyncio
async def test_ollama_payload_uses_contract_prompt_and_prefill(monkeypatch):
    fake = FakeHttp(monkeypatch, ollama_response="local text")
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == "local text"
    [call] = fake.local_calls
    assert call["url"].endswith("/api/chat")
    messages = call["json"]["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == TRANSCRIPTION_PROMPT
    assert messages[0]["images"], "page image must ride the local payload"
    assert messages[1] == {"role": "assistant", "content": OLLAMA_THINK_PREFILL}


# --- Reader wiring: hosted escalation leg ---------------------------------


@pytest.mark.asyncio
async def test_weak_local_escalates_once_to_hosted(monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "test-key")
    fake = FakeHttp(monkeypatch, ollama_response=_weak_local(4),
                    hosted_response="clean hosted")
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == "clean hosted"
    assert len(fake.hosted_calls) == 1
    [hosted_call] = fake.hosted_calls
    body = hosted_call["json"]
    assert body["model"] == reader.ocr_escalation_model
    assert hosted_call["url"].startswith(reader.ocr_escalation_base_url)
    content = body["messages"][0]["content"]
    assert content[0]["text"] == TRANSCRIPTION_PROMPT
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_local_http_failure_escalates_to_hosted(monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "test-key")
    fake = FakeHttp(monkeypatch, ollama_exc=httpx.ConnectError("boom"),
                    hosted_response="hosted fallback")
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == "hosted fallback"
    assert len(fake.hosted_calls) == 1


@pytest.mark.asyncio
async def test_strong_local_never_calls_hosted(monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "test-key")
    fake = FakeHttp(monkeypatch, ollama_response="fine local text",
                    hosted_exc=AssertionError("hosted must not be called"))
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == "fine local text"
    assert fake.hosted_calls == []


@pytest.mark.asyncio
async def test_hosted_failure_degrades_to_local(monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "test-key")
    weak = _weak_local(4)
    fake = FakeHttp(monkeypatch, ollama_response=weak,
                    hosted_exc=httpx.ConnectError("atlas down"))
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == weak  # hosted failed; local weak text is still the answer
    assert len(fake.hosted_calls) == 1


@pytest.mark.asyncio
async def test_missing_hosted_key_returns_local_without_escalation(monkeypatch):
    monkeypatch.delenv("ATLASCLOUD_API_KEY", raising=False)
    monkeypatch.delenv("GROK_AURORA_ATLASCLOUD_API_KEY", raising=False)
    weak = _weak_local(4)
    fake = FakeHttp(monkeypatch, ollama_response=weak)
    reader = SupernoteReader(vision_backend="ollama", ollama_model="qwen3-vl:8b")

    result = await reader._transcribe_page(_tiny_image())

    assert result == weak
    assert fake.hosted_calls == []
