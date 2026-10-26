"""Tests for Supernote cognition route resolution.

The Supernote model consumers are registry-driven: ``service_assignments.supernote``
in the shared ``cognition-presets.toml`` routes the service to its active
baseline. Since the 2026-10-25 gateway cutover (27bf26e) that baseline is the
``glm53_gateway`` preset on the lunaroute surface — the local paia model
gateway (OpenAI-compatible /chat/completions, same client shape as z.ai) —
with failback to the z.ai baseline after 2026-10-29 needing no code change.
These tests assert that cutover baseline and the credential-resolution rules
that stay provider-independent.
"""

from __future__ import annotations

from types import SimpleNamespace

from paia_supernote.main import load_config
from paia_supernote.model_config import (
    resolve_supernote_zai_api_key,
    supernote_text_route,
)


def test_supernote_text_route_resolves_to_lunaroute_glm53_gateway() -> None:
    # 2026-10-25 gateway cutover: supernote_text rides the local paia model
    # gateway's lunaroute surface instead of z.ai. If this fails after
    # 2026-10-29, the registry failed back to z.ai — update this baseline.
    route = supernote_text_route()

    assert route.provider == "lunaroute"
    assert route.model == "glm-5.3"
    assert route.base_url == "http://127.0.0.1:8792/p/lunaroute/v1"


def test_default_config_uses_lunaroute_gateway_baseline(tmp_path) -> None:
    # Config keys keep their legacy zai_* names; they carry the active model
    # route, whatever provider the registry assigns.
    config = load_config(config_path=tmp_path / "missing.toml")

    assert config["zai_base_url"] == "http://127.0.0.1:8792/p/lunaroute/v1"
    assert config["zai_text_model"] == "glm-5.3"


def test_resolve_supernote_zai_api_key_uses_registry_credential_env_var() -> None:
    # The registry routes the Supernote model consumers to the lunaroute
    # surface (glm53_gateway), so the registry-assigned LUNAROUTE_API_KEY
    # credential wins over both the legacy ZAI_API_KEY and any unrelated
    # openrouter secret.
    api_key = resolve_supernote_zai_api_key(
        env={
            "OPENROUTER_API_KEY": "openrouter-token",
            "LUNAROUTE_API_KEY": "lunaroute-token",
            "ZAI_API_KEY": "legacy-token",
        }
    )

    assert api_key == "lunaroute-token"


def test_resolve_supernote_zai_api_key_falls_back_to_legacy_zai_env(
    monkeypatch,
) -> None:
    # The live route is lunaroute (2026-10-25 gateway cutover), so the legacy
    # same-provider ZAI_API_KEY fallback is dormant. Force a z.ai route here to
    # keep the fallback guard covered: deployments that only set ZAI_API_KEY
    # keep working once the registry fails back to z.ai after 2026-10-29.
    fake_zai_route = SimpleNamespace(
        provider="zai",
        credential_env_var="SUPERNOTE_ZAI_API_KEY",
    )
    monkeypatch.setattr(
        "paia_supernote.model_config.supernote_text_route",
        lambda: fake_zai_route,
    )

    api_key = resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"})

    assert api_key == "legacy-token"


def test_resolve_supernote_zai_api_key_does_not_cross_provider_fallback(
    monkeypatch,
) -> None:
    # The legacy ZAI_API_KEY must NOT be used when the active route is a
    # different provider. We force an openrouter route here to exercise the
    # cross-provider guard explicitly.
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

    # Sanity: the live lunaroute route is also not z.ai, so the legacy key
    # does not apply there either.
    assert resolve_supernote_zai_api_key(env={"ZAI_API_KEY": "legacy-token"}) is None


def test_zai_consumers_use_supernote_registry_credential_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-token")
    monkeypatch.setenv("LUNAROUTE_API_KEY", "lunaroute-token")
    monkeypatch.setenv("ZAI_API_KEY", "legacy-token")

    from paia_supernote.enrichment import SupernoteEnricher
    from paia_supernote.reader import SupernoteReader
    from paia_supernote.task_curator import TaskCurator

    assert SupernoteReader().zai_api_key == "lunaroute-token"
    assert TaskCurator().zai_api_key == "lunaroute-token"
    assert SupernoteEnricher().zai_api_key == "lunaroute-token"
