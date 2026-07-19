"""Tests for Supernote cognition route resolution.

The Supernote ZAI consumers are registry-driven: ``service_assignments.supernote``
in the shared ``cognition-presets.toml`` routes the service to its active z.ai
baseline (``zai_glm51_coding`` -> ``glm-5.1``). The earlier ``openrouter_grok43``
routing was a temporary failover (due 2026-05-18) that was reverted in
``paia-agent-runtime`` commit c85551e; these tests assert the restored baseline.
"""

from __future__ import annotations

from types import SimpleNamespace

from paia_supernote.main import load_config
from paia_supernote.model_config import (
    resolve_supernote_zai_api_key,
    supernote_text_route,
)


def test_supernote_text_route_resolves_to_zai_glm_baseline() -> None:
    route = supernote_text_route()

    assert route.provider == "zai"
    assert route.model == "glm-5.1"
    assert route.base_url == "https://api.z.ai/api/coding/paas/v4"


def test_default_config_uses_supernote_zai_baseline(tmp_path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml")

    assert config["zai_base_url"] == "https://api.z.ai/api/coding/paas/v4"
    assert config["zai_text_model"] == "glm-5.1"


def test_resolve_supernote_zai_api_key_uses_registry_credential_env_var() -> None:
    # The registry routes the Supernote ZAI consumers to the z.ai route, so the
    # service-scoped SUPERNOTE_ZAI_API_KEY credential wins over both the legacy
    # ZAI_API_KEY and any unrelated openrouter secret.
    api_key = resolve_supernote_zai_api_key(
        env={
            "OPENROUTER_API_KEY": "openrouter-token",
            "SUPERNOTE_ZAI_API_KEY": "supernote-token",
            "ZAI_API_KEY": "legacy-token",
        }
    )

    assert api_key == "supernote-token"


def test_resolve_supernote_zai_api_key_falls_back_to_legacy_zai_env() -> None:
    # When the active route is z.ai, the legacy ZAI_API_KEY remains a valid
    # same-provider fallback so deployments that only set ZAI_API_KEY keep working.
    api_key = resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"})

    assert api_key == "legacy-token"


def test_resolve_supernote_zai_api_key_does_not_cross_provider_fallback(
    monkeypatch,
) -> None:
    # The legacy ZAI_API_KEY must NOT be used when the active route is a different
    # provider (e.g. openrouter). We force an openrouter route here to exercise the
    # cross-provider guard, which the live z.ai baseline would otherwise short-circuit.
    fake_openrouter_route = SimpleNamespace(
        provider="openrouter",
        credential_env_var="OPENROUTER_API_KEY",
    )
    monkeypatch.setattr(
        "paia_supernote.model_config.supernote_text_route",
        lambda: fake_openrouter_route,
    )

    api_key = resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"})

    assert api_key is None


def test_zai_consumers_use_supernote_registry_credential_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("SUPERNOTE_ZAI_API_KEY", "supernote-token")
    monkeypatch.setenv("ZAI_API_KEY", "legacy-token")

    from paia_supernote.enrichment import SupernoteEnricher
    from paia_supernote.reader import SupernoteReader
    from paia_supernote.task_curator import TaskCurator

    assert SupernoteReader().zai_api_key == "supernote-token"
    assert TaskCurator().zai_api_key == "supernote-token"
    assert SupernoteEnricher().zai_api_key == "supernote-token"
