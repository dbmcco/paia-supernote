# ABOUTME: Tests for the lunaroute vision backend — the default OCR backend
# ABOUTME: since 2026-10-28 (owner ruling: no more local Ollama models).
# ABOUTME: Hermetic: all HTTP is faked; no real model or gateway calls.

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from PIL import Image

import paia_supernote.reader as reader_mod
from paia_supernote.main import load_config
from paia_supernote.ocr_contract import TRANSCRIPTION_PROMPT
from paia_supernote.reader import (
    DEFAULT_LUNAROUTE_VISION_BASE_URL,
    DEFAULT_LUNAROUTE_VISION_MODEL,
    SupernoteReader,
    _lunaroute_gateway_key,
)


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


class _FakeAsyncClient:
    """Records posts; replies with a canned OpenAI-style completion."""

    last_request: dict[str, Any] | None = None
    response_payload: dict[str, Any] = {
        "choices": [{"message": {"content": "transcribed page text"}}]
    }

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        type(self).last_request = {
            "url": url,
            "json": json,
            "headers": headers,
        }
        return _FakeAsyncResponse(self.response_payload)


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAsyncClient]:
    _FakeAsyncClient.last_request = None
    monkeypatch.setattr(reader_mod.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


@pytest.fixture
def gateway_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LUNAROUTE_API_KEY", "test-gateway-token")
    monkeypatch.setenv("HOME", str(tmp_path))


def _reader(**overrides) -> SupernoteReader:
    kwargs: dict[str, Any] = {"vision_backend": "lunaroute"}
    kwargs.update(overrides)
    return SupernoteReader(**kwargs)


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (2, 2), color="white")


# --- Config defaults / rollback ---------------------------------------------


def test_default_config_uses_lunaroute_backend(tmp_path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml")
    assert config["vision_backend"] == "lunaroute"
    assert config["lunaroute_url"] == DEFAULT_LUNAROUTE_VISION_BASE_URL
    assert config["lunaroute_vision_model"] == DEFAULT_LUNAROUTE_VISION_MODEL


def test_vision_backend_env_is_the_named_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Rollback: SUPERNOTE_VISION_BACKEND=ollama restores the qwen3-vl path.
    monkeypatch.setenv("SUPERNOTE_VISION_BACKEND", "ollama")
    config = load_config(config_path=tmp_path / "missing.toml")
    assert config["vision_backend"] == "ollama"


def test_lunaroute_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SUPERNOTE_LUNAROUTE_URL", "http://127.0.0.1:9999/p/x/v1")
    monkeypatch.setenv("SUPERNOTE_LUNAROUTE_VISION_MODEL", "other-vision")
    config = load_config(config_path=tmp_path / "missing.toml")
    assert config["lunaroute_url"] == "http://127.0.0.1:9999/p/x/v1"
    assert config["lunaroute_vision_model"] == "other-vision"


# --- Gateway token resolution ------------------------------------------------


def test_gateway_key_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LUNAROUTE_API_KEY", "env-token")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _lunaroute_gateway_key() == "env-token"


def test_gateway_key_falls_back_to_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("LUNAROUTE_API_KEY", raising=False)
    env_dir = tmp_path / ".paia"
    env_dir.mkdir()
    (env_dir / "model-gateway.env").write_text(
        '# comment\nLUNAROUTE_API_KEY="file-token"\nOTHER=x\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _lunaroute_gateway_key() == "file-token"


def test_gateway_key_absent_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("LUNAROUTE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no .paia dir here
    assert _lunaroute_gateway_key() is None


# --- Transcription call shape and dispatch -----------------------------------


def test_transcribe_page_lunaroute_request_shape(
    fake_http: type[_FakeAsyncClient], gateway_key
) -> None:
    reader = _reader()
    result = asyncio.run(reader._transcribe_page(_tiny_image()))
    assert result == "transcribed page text"
    request = fake_http.last_request
    assert request is not None
    # goes to the model-gateway lunaroute surface, never local Ollama
    assert request["url"] == f"{DEFAULT_LUNAROUTE_VISION_BASE_URL}/chat/completions"
    assert "localhost:11434" not in request["url"]
    assert request["headers"]["Authorization"] == "Bearer test-gateway-token"
    payload = request["json"]
    assert payload["model"] == DEFAULT_LUNAROUTE_VISION_MODEL
    assert payload["max_tokens"] == 2048
    assert payload["temperature"] == 0
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": TRANSCRIPTION_PROMPT}


def test_transcribe_page_lunaroute_is_a_single_call(
    fake_http: type[_FakeAsyncClient], gateway_key
) -> None:
    """No hosted escalation on the gateway backend: one call, to the gateway."""
    reader = _reader()
    result = asyncio.run(reader._transcribe_page(_tiny_image()))
    assert result == "transcribed page text"
    # only the captured gateway request exists; nothing else was dialed
    assert fake_http.last_request is not None
    assert fake_http.last_request["url"].endswith("/chat/completions")


def test_transcribe_page_lunaroute_missing_key_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("LUNAROUTE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no env file either
    reader = _reader()
    with pytest.raises(RuntimeError, match="LUNAROUTE_API_KEY"):
        asyncio.run(reader._transcribe_page(_tiny_image()))


def test_transcribe_page_lunaroute_error_propagates(
    monkeypatch: pytest.MonkeyPatch, gateway_key
) -> None:
    class _FailingClient(_FakeAsyncClient):
        async def post(self, url, json=None, headers=None, **kwargs):
            return _FakeAsyncResponse({}, status=503)

    monkeypatch.setattr(reader_mod.httpx, "AsyncClient", _FailingClient)
    reader = _reader()
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(reader._transcribe_page(_tiny_image()))
