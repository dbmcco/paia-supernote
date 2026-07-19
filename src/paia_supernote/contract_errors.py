"""Shared agent-facing structured error contract.

The JSON form is the canonical machine surface. CLI prose and event payload
messages are derived from the same Pydantic model so expected failures do not
leak tracebacks, cookies, tokens, or large semantic payloads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SECRET_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "token",
    "api_key",
)
_REDACTED = "<redacted>"


class AgentError(BaseModel):
    """Stable error envelope used by agent-facing read/write surfaces."""

    model_config = ConfigDict(extra="allow")

    error_code: str
    message: str
    field: str | None = None
    received: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    valid_examples: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False
    next_step: str = ""
    next_actions: list[str] = Field(default_factory=list)
    mutation_applied: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_next_guidance(self) -> AgentError:
        if not self.next_actions and self.next_step:
            self.next_actions = [self.next_step]
        if not self.next_step and self.next_actions:
            self.next_step = self.next_actions[0]
        return self


class AgentContractError(Exception):
    """Exception carrying a structured ``AgentError`` response."""

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.message)
        self.error = error

    def __str__(self) -> str:
        return agent_error_json(self.error, indent=None)


def make_agent_error(
    error_code: str,
    message: str,
    *,
    field: str | None = None,
    received: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    valid_examples: list[dict[str, Any]] | None = None,
    retryable: bool = False,
    next_step: str = "",
    next_actions: list[str] | None = None,
    mutation_applied: bool = False,
    details: Mapping[str, Any] | None = None,
    **extra: Any,
) -> AgentError:
    """Create a redacted structured error with deterministic guidance fields."""
    actions = list(next_actions or [])
    if not actions and next_step:
        actions = [next_step]
    if not next_step and actions:
        next_step = actions[0]
    return AgentError(
        error_code=error_code,
        message=message,
        field=field,
        received=redact_mapping(received or {}),
        expected=redact_mapping(expected or {}),
        valid_examples=[redact_mapping(example) for example in valid_examples or []],
        retryable=retryable,
        next_step=next_step,
        next_actions=actions,
        mutation_applied=mutation_applied,
        details=redact_mapping(details or {}),
        **extra,
    )


def cloud_auth_error(message: str) -> AgentError:
    """Structured recovery guidance for stale Supernote Cloud auth/session errors."""
    return make_agent_error(
        "cloud_auth_required",
        message,
        field="auth_session",
        received={"session": "expired_or_missing"},
        expected={"auth_session": "valid Supernote Cloud session"},
        valid_examples=[{"command": "supernote auth login"}],
        retryable=True,
        next_step="Refresh Supernote Cloud auth, then retry the read/write command.",
        mutation_applied=False,
    )


def agent_error_json(error: AgentError, *, indent: int | None = 2) -> str:
    """Serialize an agent error as deterministic JSON."""
    return json.dumps(error.model_dump(mode="json"), indent=indent)


def format_agent_error(error: AgentError) -> str:
    """Render a structured error as deterministic, human-readable guidance."""
    lines = [f"{error.error_code}: {error.message}"]
    if error.field:
        lines.append(f"Field: {error.field}")
    if error.expected:
        lines.append(f"Expected: {_compact_json(error.expected)}")
    if error.received:
        lines.append(f"Received: {_compact_json(error.received)}")
    if error.details:
        lines.append(f"Details: {_compact_json(error.details)}")
    if error.next_actions:
        lines.append("Next actions:")
        lines.extend(
            f"  {index}. {action}"
            for index, action in enumerate(error.next_actions, 1)
        )
    elif error.next_step:
        lines.append(f"Next: {error.next_step}")
    lines.append(f"Retryable: {'yes' if error.retryable else 'no'}")
    lines.append(f"Mutation applied: {'yes' if error.mutation_applied else 'no'}")
    return "\n".join(lines)


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe mapping with known secret-bearing fields redacted."""
    return {str(key): _redact_value(str(key), item) for key, item in value.items()}


def redact_value(value: Any) -> Any:
    """Return a JSON-safe value with nested secret-bearing fields redacted."""
    return _redact_value("", value)


def _redact_value(key: str, value: Any) -> Any:
    if _is_secret_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_value(str(child_key), item)
            for child_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value("", item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _compact_json(value: Any) -> str:
    return json.dumps(redact_value(value), separators=(",", ":"))
