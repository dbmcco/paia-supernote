"""Pydantic-backed write-safety guard for agent Supernote writes.

This module performs local ledger revision checks only. It does not download
from Supernote Cloud and does not call uploader/apply/S3 mutation endpoints.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from .cloud_change_ledger import CloudChangeLedger, StoredNotebookState
from .config import resolve_ledger_notebooks
from .contract_errors import AgentError, agent_error_json, redact_mapping

WriteConflictCode = Literal[
    "missing_notebook",
    "missing_base_revision",
    "unknown_notebook_revision",
    "stale_base_revision",
    "notebook_revision_mismatch",
    "disallowed_notebook",
    "invalid_write_request",
]


class AgentWriteRequest(BaseModel):
    """Validated agent write request surface shared by event/CLI/API callers."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "request_id",
            "request_event_id",
            "request_source_event_id",
            "run_id",
            "action_id",
        ),
    )
    agent: str = Field(min_length=1)
    notebook: str = Field(min_length=1)
    content_type: str | None = None
    base_notebook_revision: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "base_notebook_revision",
            "base_revision",
            "notebook_revision",
            "ledger_revision",
        ),
    )
    base_notebook: str | None = Field(
        default=None,
        validation_alias=AliasChoices("base_notebook", "revision_notebook"),
    )


class AgentWriteConflictError(AgentError):
    """Machine-readable failure for fail-closed write revision checks."""

    error_code: WriteConflictCode
    notebook: str
    requested_base_revision: str | None = None
    current_revision: str | None = None
    current_notebook_hash: str | None = None
    observed_at: str | None = None


AgentWriteConflict = AgentWriteConflictError


class AgentWriteAccepted(BaseModel):
    """Positive acknowledgement returned by the write guard before mutation."""

    request_id: str | None = None
    agent: str
    notebook: str
    base_notebook_revision: str
    current_revision: str
    observed_at: str
    mutation_applied: bool = False


class AgentWriteRevisionError(RuntimeError):
    """Exception carrying a structured write conflict response."""

    def __init__(self, conflict: AgentWriteConflictError) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict

    def __str__(self) -> str:
        return agent_error_json(self.conflict, indent=None)


def missing_notebook_conflict(
    event_data: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    resolved_agent: str,
) -> AgentWriteConflict:
    """Build repair guidance for a write request missing an explicit notebook."""
    allowed_notebooks = resolve_ledger_notebooks(config)
    example_notebook = allowed_notebooks[0] if allowed_notebooks else "Quick"
    default_notebook = str(
        (config.get("agent_mappings") or {}).get(resolved_agent, {}).get("notebook")
        or ""
    ).strip()
    valid_examples = [
        {
            "agent": resolved_agent,
            "notebook": _strip_note_suffix(example_notebook),
            "base_notebook_revision": "cloud-revision-from-last-read",
        }
    ]
    if default_notebook:
        valid_examples.append(
            {
                "agent": resolved_agent,
                "use_agent_default_notebook": True,
                "base_notebook_revision": "cloud-revision-from-last-read",
            }
        )
    return _conflict(
        "missing_notebook",
        notebook="",
        message=(
            "Agent write requests must include a non-empty notebook unless they "
            "explicitly opt in to the configured agent default notebook."
        ),
        field="notebook",
        received=_safe_received(event_data),
        expected={
            "notebook": "non-empty target notebook name",
            "or": {"use_agent_default_notebook": True},
            "allowed_notebooks": allowed_notebooks,
        },
        valid_examples=valid_examples,
        retryable=False,
        next_step=(
            "Resubmit the write with an explicit notebook from the read/changes "
            "contract, or set use_agent_default_notebook=true to intentionally "
            "use the agent mapping default."
        ),
    )



def validate_agent_write_request(
    event_data: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    state_db_path: Path,
    resolved_agent: str,
    resolved_notebook: str,
) -> AgentWriteAccepted:
    """Validate an agent write's base revision against the cached ledger.

    Raises ``AgentWriteRevisionError`` before any caller should download, apply,
    upload, or S3 PUT notebook bytes.
    """
    payload = dict(event_data)
    payload["agent"] = resolved_agent
    payload["notebook"] = _strip_note_suffix(resolved_notebook)
    try:
        request = AgentWriteRequest.model_validate(payload)
    except ValidationError as exc:
        raise AgentWriteRevisionError(
            _conflict(
                "missing_base_revision"
                if _missing_base_revision(exc)
                else "invalid_write_request",
                notebook=_strip_note_suffix(resolved_notebook),
                message="Agent write requests must include a base notebook revision.",
                field="base_notebook_revision" if _missing_base_revision(exc) else None,
                received=_safe_received(event_data),
                expected={
                    "base_notebook_revision": (
                        "notebook_revision returned by supernote changes/read"
                    )
                },
                valid_examples=[
                    {
                        "agent": resolved_agent,
                        "notebook": _strip_note_suffix(resolved_notebook),
                        "base_notebook_revision": "cloud-revision-from-last-read",
                    }
                ],
                retryable=False,
                next_step=(
                    "Query the notebook through the ledger-backed read contract, "
                    "then retry with the returned notebook_revision."
                ),
            )
        ) from None

    notebook = _strip_note_suffix(request.notebook)
    if request.base_notebook is not None:
        base_notebook = _strip_note_suffix(request.base_notebook)
        if base_notebook.casefold() != notebook.casefold():
            raise AgentWriteRevisionError(
                _conflict(
                    "notebook_revision_mismatch",
                    notebook=notebook,
                    message=(
                        "The supplied base revision belongs to a different notebook."
                    ),
                    requested_base_revision=request.base_notebook_revision,
                    field="base_notebook",
                    received={
                        "notebook": notebook,
                        "base_notebook": base_notebook,
                        "base_notebook_revision": request.base_notebook_revision,
                    },
                    expected={"base_notebook": notebook},
                    retryable=False,
                    next_step=(
                        "Read the target notebook again and submit its matching "
                        "notebook/base revision."
                    ),
                )
            )

    canonical_notebook = _canonical_allowed_notebook(config, notebook)
    if canonical_notebook is None:
        raise AgentWriteRevisionError(
            _conflict(
                "disallowed_notebook",
                notebook=notebook,
                message=(
                    "Notebook is not in the configured Cloud change ledger allowlist."
                ),
                requested_base_revision=request.base_notebook_revision,
                field="notebook",
                received={"notebook": notebook},
                expected={"allowed_notebooks": resolve_ledger_notebooks(config)},
                valid_examples=[
                    {"notebook": name, "base_notebook_revision": "current-revision"}
                    for name in resolve_ledger_notebooks(config)[:3]
                ],
                retryable=False,
                next_step=(
                    "Choose an allowlisted notebook or update configuration before "
                    "writing."
                ),
            )
        )

    ledger = CloudChangeLedger(Path(state_db_path))
    ledger.init_schema()
    latest = ledger.latest_notebook_state(canonical_notebook)
    if latest is None:
        raise AgentWriteRevisionError(
            _conflict(
                "unknown_notebook_revision",
                notebook=canonical_notebook,
                message="No cached ledger snapshot exists for this notebook.",
                requested_base_revision=request.base_notebook_revision,
                field="notebook",
                received={"notebook": canonical_notebook},
                expected={"cached_snapshot": "created by successful Cloud ingest"},
                valid_examples=[
                    {"command": f"supernote changes {canonical_notebook} --json"}
                ],
                retryable=True,
                next_step=(
                    "Run or wait for Cloud ingest, then retry with the returned "
                    "notebook_revision."
                ),
            )
        )

    if request.base_notebook_revision != latest.cloud_revision:
        raise AgentWriteRevisionError(
            _stale_conflict(
                latest,
                requested_base_revision=request.base_notebook_revision,
            )
        )

    return AgentWriteAccepted(
        request_id=request.request_id,
        agent=request.agent,
        notebook=canonical_notebook,
        base_notebook_revision=request.base_notebook_revision,
        current_revision=latest.cloud_revision,
        observed_at=latest.observed_at,
    )


def notebook_bytes_hash(revision: str) -> str | None:
    """Extract the raw ``sha256(note_bytes)`` embedded in a Cloud revision.

    Revisions are ``"{update_time}:{sha256(note_bytes)}"`` (see ingest). Returns
    the 64-char hex hash, or ``None`` when the revision predates that shape (a
    legacy or synthetic revision) so callers can fail-open to the cache check
    rather than reject a write they cannot verify.
    """
    if ":" not in revision:
        return None
    candidate = revision.rsplit(":", 1)[1]
    if len(candidate) != 64:
        return None
    try:
        int(candidate, 16)  # validate hex
    except ValueError:
        return None
    return candidate


def assert_downloaded_matches_base(
    downloaded_bytes: bytes,
    accepted: AgentWriteAccepted,
) -> None:
    """Post-download compare-and-swap against the agent's base revision.

    The cache check in :func:`validate_agent_write_request` only sees the local
    ledger; it cannot detect a concurrent Cloud edit that landed after the cache
    was populated. This guard compares the *actual* downloaded Cloud bytes to the
    hash embedded in the agent's base revision and raises ``stale_base_revision``
    on divergence — before any caller appends/replaces/uploads.

    Fails open (no-op) when the base revision carries no embedded bytes-hash, so
    synthetic or legacy revisions keep working under the cache check alone.
    """
    expected = notebook_bytes_hash(accepted.base_notebook_revision)
    if expected is None:
        return
    actual = hashlib.sha256(downloaded_bytes).hexdigest()
    if actual == expected:
        return
    raise AgentWriteRevisionError(
        _conflict(
            "stale_base_revision",
            notebook=accepted.notebook,
            message=(
                "Downloaded Cloud bytes do not match the base revision — a "
                "concurrent edit landed between the agent read and this write."
            ),
            requested_base_revision=accepted.base_notebook_revision,
            current_revision=accepted.current_revision,
            field="base_notebook_revision",
            received={
                "base_notebook_revision": accepted.base_notebook_revision,
                "downloaded_sha256": actual,
            },
            expected={"base_notebook_revision": "a fresh revision from a re-read"},
            retryable=True,
            next_step=(
                "Re-read the notebook, merge any intervening changes, then retry."
            ),
            details={
                "post_download_cas": {
                    "base_notebook_revision": accepted.base_notebook_revision,
                    "expected_sha256": expected,
                    "downloaded_sha256": actual,
                    "match": False,
                }
            },
        )
    )


def _stale_conflict(
    latest: StoredNotebookState, *, requested_base_revision: str
) -> AgentWriteConflictError:
    return _conflict(
        "stale_base_revision",
        notebook=latest.notebook,
        message="The notebook has a newer cached revision than the agent read.",
        requested_base_revision=requested_base_revision,
        current_revision=latest.cloud_revision,
        current_notebook_hash=latest.notebook_hash,
        observed_at=latest.observed_at,
        field="base_notebook_revision",
        received={
            "base_notebook_revision": requested_base_revision,
            "notebook": latest.notebook,
        },
        expected={"base_notebook_revision": latest.cloud_revision},
        valid_examples=[
            {
                "notebook": latest.notebook,
                "base_notebook_revision": latest.cloud_revision,
            }
        ],
        retryable=True,
        next_step=(
            "Query changes/read for the current notebook_revision, merge if needed, "
            "then retry the write."
        ),
        next_actions=[
            f"Query changes/read for {latest.notebook} at the current revision.",
            "Merge the agent write with any intervening notebook changes.",
            "Retry the write with the returned current notebook_revision.",
        ],
        details={
            "revision_comparison": {
                "notebook": latest.notebook,
                "requested_base_revision": requested_base_revision,
                "current_revision": latest.cloud_revision,
                "matches_current_revision": False,
                "observed_at": latest.observed_at,
            }
        },
    )


def _conflict(
    error_code: WriteConflictCode,
    *,
    notebook: str,
    message: str,
    next_step: str,
    requested_base_revision: str | None = None,
    current_revision: str | None = None,
    current_notebook_hash: str | None = None,
    observed_at: str | None = None,
    field: str | None = None,
    received: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    valid_examples: list[dict[str, Any]] | None = None,
    retryable: bool = False,
    next_actions: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> AgentWriteConflictError:
    actions = list(next_actions or [])
    if not actions and next_step:
        actions = [next_step]
    merged_details = dict(details or {})
    if requested_base_revision is not None or current_revision is not None:
        merged_details.setdefault(
            "revision_comparison",
            {
                "notebook": notebook,
                "requested_base_revision": requested_base_revision,
                "current_revision": current_revision,
                "matches_current_revision": requested_base_revision == current_revision,
                "observed_at": observed_at,
            },
        )
    return AgentWriteConflictError(
        error_code=error_code,
        message=message,
        notebook=notebook,
        requested_base_revision=requested_base_revision,
        current_revision=current_revision,
        current_notebook_hash=current_notebook_hash,
        observed_at=observed_at,
        field=field,
        received=redact_mapping(received or {}),
        expected=redact_mapping(expected or {}),
        valid_examples=[redact_mapping(example) for example in valid_examples or []],
        retryable=retryable,
        next_step=next_step,
        next_actions=actions,
        mutation_applied=False,
        details=redact_mapping(merged_details),
    )


def _missing_base_revision(exc: ValidationError) -> bool:
    for error in exc.errors():
        if tuple(error.get("loc", ())) == ("base_notebook_revision",):
            return True
    return False


def _canonical_allowed_notebook(
    config: Mapping[str, Any], notebook: str
) -> str | None:
    requested = _strip_note_suffix(notebook).casefold()
    for allowed in resolve_ledger_notebooks(config):
        cleaned = _strip_note_suffix(allowed)
        if cleaned.casefold() == requested:
            return cleaned
    return None


def _strip_note_suffix(notebook: str) -> str:
    cleaned = str(notebook).strip()
    return cleaned[:-5] if cleaned.lower().endswith(".note") else cleaned


def _safe_received(event_data: Mapping[str, Any]) -> dict[str, Any]:
    received = dict(event_data)
    if "content" in received:
        received["content"] = "<redacted>"
    if "pages" in received:
        received["pages"] = f"<{len(received.get('pages') or [])} page(s)>"
    return redact_mapping(received)
