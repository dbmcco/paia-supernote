from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.agent_read_contracts import (
    NotebookChangesRequest,
    ReadContractError,
    SupernoteReadContract,
)
from paia_supernote.agent_write_contracts import (
    AgentWriteRevisionError,
    validate_agent_write_request,
)
from paia_supernote.cloud_change_ledger import (
    CloudChangeLedger,
    NotebookSnapshot,
    PageRevision,
)
from paia_supernote.contract_errors import (
    AgentError,
    agent_error_json,
    format_agent_error,
    make_agent_error,
    redact_mapping,
)
from paia_supernote.events import EventsClient


def _seed_revision(tmp_path: Path, notebook: str = "Quick") -> None:
    ledger = CloudChangeLedger(tmp_path / "state.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        NotebookSnapshot(
            notebook=notebook,
            cloud_revision="rev-current",
            cloud_update_time=123,
            pages=[
                PageRevision(
                    page_id="page-1",
                    page_index=0,
                    content_hash="hash-1",
                )
            ],
        )
    )


def test_agent_error_serializes_stable_guidance_and_redacts_secrets() -> None:
    error = make_agent_error(
        "invalid_cursor",
        "Cursor is outside the ledger range.",
        field="since",
        received={
            "since": 99,
            "Authorization": "Bearer secret-token",
            "nested": {"session_cookie": "cookie-value"},
        },
        expected={"since": "integer between 0 and 3"},
        valid_examples=[{"since": 3}],
        retryable=False,
        next_actions=[
            "Use the previous changes response next_cursor.",
            "Retry the read without changing Cloud notebooks.",
        ],
        mutation_applied=False,
        details={"ledger": "cached"},
    )

    payload = json.loads(agent_error_json(error))

    assert payload["error_code"] == "invalid_cursor"
    assert payload["next_step"] == "Use the previous changes response next_cursor."
    assert payload["next_actions"] == [
        "Use the previous changes response next_cursor.",
        "Retry the read without changing Cloud notebooks.",
    ]
    assert payload["received"]["Authorization"] == "<redacted>"
    assert payload["received"]["nested"]["session_cookie"] == "<redacted>"
    assert payload["mutation_applied"] is False


def test_read_allowlist_cursor_and_snapshot_errors_use_shared_envelope(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path)
    contract = SupernoteReadContract(
        {"cloud_change_ledger_notebooks": ["Quick", "Empty"]},
        tmp_path / "state.db",
    )

    with pytest.raises(ReadContractError) as disallowed:
        contract.get_changes(NotebookChangesRequest(notebook="Secret", since=0))
    with pytest.raises(ReadContractError) as invalid_cursor:
        contract.get_changes(NotebookChangesRequest(notebook="Quick", since=999))
    with pytest.raises(ReadContractError) as unknown_snapshot:
        contract.get_changes(NotebookChangesRequest(notebook="Empty", since=0))

    assert isinstance(disallowed.value.error, AgentError)
    assert disallowed.value.error.error_code == "disallowed_notebook"
    assert disallowed.value.error.next_actions == [disallowed.value.error.next_step]
    assert invalid_cursor.value.error.error_code == "invalid_cursor"
    assert unknown_snapshot.value.error.error_code == "unknown_notebook"
    assert unknown_snapshot.value.error.mutation_applied is False


def test_write_conflict_errors_include_revision_details_and_ordered_actions(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path)

    with pytest.raises(AgentWriteRevisionError) as stale:
        validate_agent_write_request(
            {
                "agent": "Sam",
                "notebook": "Quick",
                "base_notebook_revision": "rev-old",
                "content": "do not leak this content",
            },
            config={"cloud_change_ledger_notebooks": ["Quick"]},
            state_db_path=tmp_path / "state.db",
            resolved_agent="Sam",
            resolved_notebook="Quick",
        )

    payload = json.loads(agent_error_json(stale.value.conflict))

    assert payload["error_code"] == "stale_base_revision"
    assert payload["requested_base_revision"] == "rev-old"
    assert payload["current_revision"] == "rev-current"
    assert payload["details"]["revision_comparison"] == {
        "notebook": "Quick",
        "requested_base_revision": "rev-old",
        "current_revision": "rev-current",
        "matches_current_revision": False,
        "observed_at": payload["observed_at"],
    }
    assert payload["next_actions"] == [
        "Query changes/read for Quick at the current revision.",
        "Merge the agent write with any intervening notebook changes.",
        "Retry the write with the returned current notebook_revision.",
    ]
    assert payload["mutation_applied"] is False


def test_missing_write_revision_redacts_secret_received_fields(tmp_path: Path) -> None:
    _seed_revision(tmp_path)

    with pytest.raises(AgentWriteRevisionError) as missing:
        validate_agent_write_request(
            {
                "agent": "Sam",
                "notebook": "Quick",
                "content": "secret content",
                "Authorization": "Bearer secret-token",
                "pages": [{"content": "page secret"}],
            },
            config={"cloud_change_ledger_notebooks": ["Quick"]},
            state_db_path=tmp_path / "state.db",
            resolved_agent="Sam",
            resolved_notebook="Quick",
        )

    payload = missing.value.conflict.model_dump(mode="json")

    assert payload["error_code"] == "missing_base_revision"
    assert payload["received"]["content"] == "<redacted>"
    assert payload["received"]["pages"] == "<1 page(s)>"
    assert payload["received"]["Authorization"] == "<redacted>"


@pytest.mark.asyncio
async def test_write_failed_event_payload_contains_structured_error_and_prose() -> None:
    client = EventsClient(base_url="http://localhost:3511")
    error = make_agent_error(
        "stale_base_revision",
        "The notebook has a newer cached revision than the agent read.",
        field="base_notebook_revision",
        received={"base_notebook_revision": "rev-old"},
        expected={"base_notebook_revision": "rev-current"},
        retryable=True,
        next_actions=[
            "Read the current notebook revision.",
            "Retry with the current base revision.",
        ],
        details={
            "revision_comparison": {
                "requested_base_revision": "rev-old",
                "current_revision": "rev-current",
            }
        },
    )

    with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.post.return_value = MagicMock(raise_for_status=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await client.publish_write_failed(
            request_event_id=43,
            agent="Sam",
            notebook="Quick",
            content_type="replace_pages",
            error=agent_error_json(error, indent=None),
            structured_error=error,
            error_message=format_agent_error(error),
        )

    body = mock_http.post.call_args.kwargs["json"]
    payload = body["payload"]
    assert payload["structured_error"]["error_code"] == "stale_base_revision"
    assert payload["structured_error"]["next_actions"] == [
        "Read the current notebook revision.",
        "Retry with the current base revision.",
    ]
    assert payload["error_message"].startswith("stale_base_revision:")
    assert "Traceback" not in payload["error_message"]
    assert payload["structured_error"]["mutation_applied"] is False


# --- structured prose and redaction contract --------------------------------


def test_format_agent_error_renders_all_sections_in_deterministic_prose() -> None:
    """format_agent_error produces stable, human-readable prose with every
    guidance section (field, expected, received, details, next actions).
    """
    error = make_agent_error(
        "stale_base_revision",
        "The notebook has a newer cached revision than the agent read.",
        field="base_notebook_revision",
        received={"base_notebook_revision": "rev-old"},
        expected={"base_notebook_revision": "rev-current"},
        valid_examples=[{"base_notebook_revision": "rev-current"}],
        retryable=True,
        next_actions=[
            "Read the current notebook revision.",
            "Retry with the current base revision.",
        ],
        details={
            "revision_comparison": {
                "requested_base_revision": "rev-old",
                "current_revision": "rev-current",
            }
        },
    )

    prose = format_agent_error(error)

    assert prose.startswith(
        "stale_base_revision: The notebook has a newer cached revision "
        "than the agent read."
    )
    assert "Field: base_notebook_revision" in prose
    assert "Expected:" in prose and "rev-current" in prose
    assert "Received:" in prose and "rev-old" in prose
    assert "Details:" in prose
    assert "Next actions:" in prose
    assert "1. Read the current notebook revision." in prose
    assert "2. Retry with the current base revision." in prose
    assert "Retryable: yes" in prose
    assert "Mutation applied: no" in prose
    assert "Traceback" not in prose


def test_agent_error_json_compact_single_line_is_stable() -> None:
    """indent=None yields a single-line JSON string for event payloads."""
    error = make_agent_error(
        "invalid_cursor",
        "Cursor out of range.",
        next_step="Use next_cursor.",
    )

    compact = agent_error_json(error, indent=None)

    assert "\n" not in compact
    assert json.loads(compact)["error_code"] == "invalid_cursor"


def test_redact_mapping_redacts_secrets_and_coerces_non_serializable() -> None:
    """redact_mapping must redact known secret fields at any depth and coerce
    non-JSON-serializable values to str instead of raising.
    """
    redacted = redact_mapping(
        {
            "Authorization": "Bearer token",
            "safe": "ok",
            "nested": {"api_key": "secret", "data": 42},
            "obj": object(),
        }
    )

    assert redacted["Authorization"] == "<redacted>"
    assert redacted["nested"]["api_key"] == "<redacted>"
    assert redacted["nested"]["data"] == 42
    assert redacted["safe"] == "ok"
    assert isinstance(redacted["obj"], str)
