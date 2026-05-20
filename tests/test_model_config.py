"""Tests for Supernote cognition route resolution."""

from __future__ import annotations

from paia_supernote.main import load_config
from paia_supernote.model_config import (
    resolve_supernote_zai_api_key,
    supernote_text_route,
)


def test_supernote_text_route_resolves_to_temporary_openrouter_grok43() -> None:
    route = supernote_text_route()

    assert route.provider == "openrouter"
    assert route.surface == "openrouter"
    assert route.model == "x-ai/grok-4.3"
    assert route.base_url == "https://openrouter.ai/api/v1"


def test_default_config_uses_supernote_temporary_openrouter_route(tmp_path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml")

    assert config["zai_base_url"] == "https://openrouter.ai/api/v1"
    assert config["zai_text_model"] == "x-ai/grok-4.3"


def test_resolve_supernote_zai_api_key_uses_temporary_openrouter_env_var() -> None:
    api_key = resolve_supernote_zai_api_key(
        env={
            "OPENROUTER_API_KEY": "openrouter-token",
            "SUPERNOTE_ZAI_API_KEY": "supernote-token",
            "ZAI_API_KEY": "legacy-token",
        }
    )

    assert api_key == "openrouter-token"


def test_resolve_supernote_zai_api_key_does_not_cross_provider_fallback() -> None:
    api_key = resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"})

    assert api_key is None


def test_zai_consumers_use_supernote_openrouter_registry_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("SUPERNOTE_ZAI_API_KEY", "supernote-token")
    monkeypatch.setenv("ZAI_API_KEY", "legacy-token")

    from paia_supernote.enrichment import SupernoteEnricher
    from paia_supernote.reader import SupernoteReader
    from paia_supernote.task_curator import TaskCurator

    assert SupernoteReader().zai_api_key == "openrouter-token"
    assert TaskCurator().zai_api_key == "openrouter-token"
    assert SupernoteEnricher().zai_api_key == "openrouter-token"
