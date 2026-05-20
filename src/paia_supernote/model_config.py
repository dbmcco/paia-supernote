"""Shared Paia cognition routes for Supernote model-backed work."""

from __future__ import annotations

import os
from collections.abc import Mapping

from paia_agent_runtime import (
    ExecutionProfile,
    ResolvedCognitionRoute,
    get_cognition_registry,
)

LEGACY_ZAI_API_KEY_ENV = "ZAI_API_KEY"
SUPPORTED_SUPERNOTE_PROVIDERS = frozenset({"zai", "openrouter"})


def _supernote_route(profile: ExecutionProfile) -> ResolvedCognitionRoute:
    route = get_cognition_registry().route_for(profile, service_id="supernote")
    if route.provider not in SUPPORTED_SUPERNOTE_PROVIDERS:
        raise RuntimeError(
            "Supernote model backend is configured with unsupported route "
            f"{route.surface!r}"
        )
    if route.base_url is None:
        raise RuntimeError(f"Supernote route {route.surface!r} has no base_url")
    return route


def supernote_vision_route() -> ResolvedCognitionRoute:
    return _supernote_route(ExecutionProfile.SUPERNOTE_VISION)


def supernote_text_route() -> ResolvedCognitionRoute:
    return _supernote_route(ExecutionProfile.SUPERNOTE_TEXT)


def supernote_zai_credential_env_var() -> str:
    route = supernote_text_route()
    if not route.credential_env_var:
        raise RuntimeError("Supernote model credential has no env var configured")
    return route.credential_env_var


def resolve_supernote_zai_api_key(
    *, env: Mapping[str, str] | None = None, legacy_fallback: bool = True
) -> str | None:
    """Resolve Supernote's active model secret from central credential metadata.

    Resolution order:
    1. The registry-assigned Supernote credential env var.
    2. Legacy ZAI_API_KEY, only when the active route is still the Z.AI route.
    """
    environ = env or os.environ
    route = supernote_text_route()
    credential_env_var = route.credential_env_var
    if not credential_env_var:
        raise RuntimeError("Supernote model credential has no env var configured")
    if api_key := environ.get(credential_env_var):
        return api_key
    if (
        route.provider == "zai"
        and legacy_fallback
        and (api_key := environ.get(LEGACY_ZAI_API_KEY_ENV))
    ):
        return api_key
    return None


def default_zai_base_url() -> str:
    return supernote_text_route().base_url or ""


def default_zai_vision_model() -> str:
    return supernote_vision_route().model


def default_zai_text_model() -> str:
    return supernote_text_route().model


def default_anthropic_model() -> str:
    route = get_cognition_registry().route_for(
        ExecutionProfile.SUPERNOTE_ANTHROPIC, service_id="supernote"
    )
    if route.provider != "anthropic":
        raise RuntimeError(
            f"Supernote Anthropic backend is configured with route {route.surface!r}"
        )
    return route.model
