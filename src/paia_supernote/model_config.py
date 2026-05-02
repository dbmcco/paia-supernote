"""Shared Paia cognition routes for Supernote model-backed work."""

from __future__ import annotations

from paia_agent_runtime import ExecutionProfile, ResolvedCognitionRoute, get_cognition_registry


def _supernote_route(profile: ExecutionProfile) -> ResolvedCognitionRoute:
    route = get_cognition_registry().route_for(profile, service_id="supernote")
    if route.provider != "zai":
        raise RuntimeError(
            f"Supernote Z.AI backend is configured with non-Z.AI route {route.surface!r}"
        )
    if route.base_url is None:
        raise RuntimeError(f"Supernote route {route.surface!r} has no base_url")
    return route


def supernote_vision_route() -> ResolvedCognitionRoute:
    return _supernote_route(ExecutionProfile.SUPERNOTE_VISION)


def supernote_text_route() -> ResolvedCognitionRoute:
    return _supernote_route(ExecutionProfile.SUPERNOTE_TEXT)


def default_zai_base_url() -> str:
    return supernote_text_route().base_url or ""


def default_zai_vision_model() -> str:
    return supernote_vision_route().model


def default_zai_text_model() -> str:
    return supernote_text_route().model


def default_anthropic_model() -> str:
    route = get_cognition_registry().route_for(ExecutionProfile.SUPERNOTE_ANTHROPIC, service_id="supernote")
    if route.provider != "anthropic":
        raise RuntimeError(f"Supernote Anthropic backend is configured with route {route.surface!r}")
    return route.model
