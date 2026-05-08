"""Tests for Supernote cognition route resolution."""

from __future__ import annotations

from paia_supernote.main import load_config
from paia_supernote.model_config import (
    resolve_supernote_zai_api_key,
    supernote_text_route,
)


def test_supernote_text_route_resolves_to_zai_coding_glm51() -> None:
    route = supernote_text_route()

    assert route.provider == "zai"
    assert route.surface == "zai_coding"
    assert route.model == "glm-5.1"
    assert route.base_url == "https://api.z.ai/api/coding/paas/v4"


def test_default_config_uses_supernote_zai_coding_route(tmp_path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml")

    assert config["zai_base_url"] == "https://api.z.ai/api/coding/paas/v4"
    assert config["zai_text_model"] == "glm-5.1"


def test_resolve_supernote_zai_api_key_uses_registry_credential_env_var() -> None:
    api_key = resolve_supernote_zai_api_key(
        env={
            "SUPERNOTE_ZAI_API_KEY": "supernote-token",
            "ZAI_API_KEY": "legacy-token",
        }
    )

    assert api_key == "supernote-token"


def test_resolve_supernote_zai_api_key_falls_back_to_legacy_zai_api_key() -> None:
    api_key = resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"})

    assert api_key == "legacy-token"


def test_zai_consumers_use_supernote_registry_env_var(monkeypatch) -> None:
    monkeypatch.setenv("SUPERNOTE_ZAI_API_KEY", "supernote-token")
    monkeypatch.setenv("ZAI_API_KEY", "legacy-token")

    from paia_supernote.enrichment import SupernoteEnricher
    from paia_supernote.reader import SupernoteReader
    from paia_supernote.task_curator import TaskCurator

    assert SupernoteReader().zai_api_key == "supernote-token"
    assert TaskCurator().zai_api_key == "supernote-token"
    assert SupernoteEnricher().zai_api_key == "supernote-token"
