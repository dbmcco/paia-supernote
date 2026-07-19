"""Offline integration coverage for the agent contract surfaces that exist in-repo.

These tests exercise the *reachable* surfaces that already wire the cached
read/change/cursor contract and the structured write-safety guard:

* CLI ``supernote changes`` — the only in-repo command that exposes cached
  ledger reads (``--latest``/changes/cursor) without contacting Supernote Cloud.
* ``SupernoteService._handle_write_request`` — the only in-repo agent write
  surface; it runs ``validate_agent_write_request`` before any upload.
* ``EventsClient.publish_write_failed`` — the in-repo event surface that carries
  the shared structured error envelope.

There is **no in-repo agent-facing HTTP API** for read/write contracts: the
organizer server (``organizer_server.py``) is a human browser UI, not a contract
API, and ``user_board.py`` is a human TUI that publishes ``write.requested``
events. That obligation is recorded in
``docs/superpowers/specs/follow-ups/agent-contract-http-api-surface.md`` rather
than asserted here.

No real Supernote Cloud contact, no real paia-events HTTP, no OCR.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import paia_supernote.cli as cli
from paia_supernote.cli import CliConfig
from paia_supernote.cloud_change_ledger import (
    CloudChangeLedger,
    NotebookSnapshot,
    PageRevision,
)
from paia_supernote.contract_errors import format_agent_error, make_agent_error
from paia_supernote.events import EventsClient
from paia_supernote.main import DEFAULT_CONFIG, SupernoteService

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOTEBOOK = "Quick"


def _allowlist_config(tmp_path: Path) -> dict:
    """Config pointing every store at a temp dir with a single allowlist entry."""
    return {"cloud_change_ledger_notebooks": [_NOTEBOOK]}


def _seed_snapshot(
    tmp_path: Path,
    notebook: str,
    revision: str,
    *,
    pages: list[PageRevision],
    update_time: int = 100,
) -> None:
    ledger = CloudChangeLedger(tmp_path / "state.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        NotebookSnapshot(
            notebook=notebook,
            cloud_revision=revision,
            cloud_update_time=update_time,
            pages=pages,
        )
    )


def _cli_config(tmp_path: Path) -> CliConfig:
    return CliConfig(
        ledger_db_path=tmp_path / "filing.db",
        state_db_path=tmp_path / "state.db",
        backups_root=tmp_path / "backups",
        destination_map={},
        reader=MagicMock(),
        raw_config=_allowlist_config(tmp_path),
    )


class _SpyUploader:
    """Stand-in uploader that records every Cloud-mutating call."""

    def __init__(self) -> None:
        self.page = object()
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.download_notebook = AsyncMock(return_value=b"notebook")
        self.upload_notebook = AsyncMock(return_value=True)


def _service(tmp_path: Path) -> SupernoteService:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["cloud_change_ledger_notebooks"] = [_NOTEBOOK]
    service = SupernoteService(config=config)
    service.uploader = _SpyUploader()
    service.events = AsyncMock()
    return service


# ---------------------------------------------------------------------------
# CLI surface — cached read / change / cursor behavior
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_changes_latest_returns_cached_state_without_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``supernote changes --latest`` reads the local SQLite cache only."""
    _seed_snapshot(
        tmp_path,
        _NOTEBOOK,
        "rev-1",
        pages=[PageRevision("p-1", 0, "hash-1")],
    )
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _cli_config(tmp_path))

    rc = cli.main(["--json", "changes", _NOTEBOOK, "--latest"])

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["notebook"] == _NOTEBOOK
    assert payload["notebook_revision"] == "rev-1"
    assert payload["page_count"] == 1
    assert payload["pages"][0]["page_id"] == "p-1"
    # No uploader was constructed for the cached read path (no Cloud contact).
    assert captured.err == ""


@pytest.mark.integration
def test_cli_changes_returns_cached_change_batches_and_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``supernote changes`` returns cached change batches + a forward cursor."""
    ledger = CloudChangeLedger(tmp_path / "state.db")
    ledger.init_schema()
    # First snapshot seeds the notebook; second produces an update change.
    ledger.apply_snapshot(
        NotebookSnapshot(
            _NOTEBOOK,
            "rev-1",
            100,
            [PageRevision("p-1", 0, "hash-1")],
        )
    )
    ledger.apply_snapshot(
        NotebookSnapshot(
            _NOTEBOOK,
            "rev-2",
            200,
            [PageRevision("p-1", 0, "hash-1b"), PageRevision("p-2", 1, "hash-2")],
        )
    )
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _cli_config(tmp_path))

    rc = cli.main(["--json", "changes", _NOTEBOOK])

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["notebook"] == _NOTEBOOK
    assert payload["notebook_revision"] == "rev-2"
    change_types = {change["change_type"] for change in payload["changes"]}
    assert {"updated", "added"} <= change_types
    assert payload["next_cursor"] >= payload["cursor"]


@pytest.mark.integration
def test_cli_disallowed_notebook_emits_shared_structured_error_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A disallowed notebook yields the shared structured envelope + exit 2."""
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _cli_config(tmp_path))

    rc = cli.main(["--json", "changes", "Secret", "--since", "0"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert rc == 2
    assert payload["error_code"] == "disallowed_notebook"
    assert payload["next_actions"] == [payload["next_step"]]
    assert payload["mutation_applied"] is False
    assert "Traceback" not in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Service write surface — fail-closed on conflict, no unsafe upload
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_stale_revision_blocks_upload_and_publishes_structured_conflict(
    tmp_path: Path,
) -> None:
    """A stale base revision fails closed: no Cloud mutation, structured conflict."""
    _seed_snapshot(
        tmp_path,
        _NOTEBOOK,
        "rev-current",
        pages=[PageRevision("p-1", 0, "hash-1")],
    )
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": _NOTEBOOK,
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-old",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    # No Cloud mutation occurred — write guard failed before download/upload.
    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
    service._replace_pages_with_uploader.assert_not_awaited()

    service.events.publish_write_failed.assert_awaited_once()
    call = service.events.publish_write_failed.await_args
    error_payload = json.loads(call.kwargs["error"])
    assert error_payload["error_code"] == "stale_base_revision"
    assert error_payload["requested_base_revision"] == "rev-old"
    assert error_payload["current_revision"] == "rev-current"
    assert error_payload["mutation_applied"] is False
    assert error_payload["retryable"] is True
    # The structured conflict model travels alongside the prose message.
    assert call.kwargs["structured_error"].error_code == "stale_base_revision"
    assert call.kwargs["error_message"].startswith("stale_base_revision:")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_current_revision_preserves_safe_upload_path(
    tmp_path: Path,
) -> None:
    """A current base revision preserves the existing safe upload behavior."""
    _seed_snapshot(
        tmp_path,
        _NOTEBOOK,
        "rev-current",
        pages=[PageRevision("p-1", 0, "hash-1")],
    )
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": _NOTEBOOK,
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-current",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service._replace_pages_with_uploader.assert_awaited_once()
    service.events.publish_write_completed.assert_awaited_once()
    service.events.publish_write_failed.assert_not_awaited()


# ---------------------------------------------------------------------------
# Event surface — structured error envelope is carried verbatim
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_event_write_failed_payload_carries_shared_structured_envelope() -> None:
    """``publish_write_failed`` serializes the shared structured error envelope."""
    error = make_agent_error(
        "stale_base_revision",
        "The notebook has a newer cached revision than the agent read.",
        field="base_notebook_revision",
        received={"base_notebook_revision": "rev-old"},
        expected={"base_notebook_revision": "rev-current"},
        retryable=True,
        next_actions=[
            "Query changes/read for the current notebook_revision.",
            "Retry the write with the returned current notebook_revision.",
        ],
    )
    client = EventsClient(base_url="http://localhost:3511")

    with patch("paia_supernote.events.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.post.return_value = MagicMock(raise_for_status=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await client.publish_write_failed(
            request_event_id=7,
            agent="Sam",
            notebook=_NOTEBOOK,
            content_type="replace_pages",
            error="stale_base_revision",
            structured_error=error,
            error_message=format_agent_error(error),
        )

    body = mock_http.post.call_args.kwargs["json"]
    payload = body["payload"]
    # The structured_error sub-object uses the same envelope as the CLI/contract.
    assert payload["structured_error"]["error_code"] == "stale_base_revision"
    assert payload["structured_error"]["next_actions"] == error.next_actions
    assert payload["structured_error"]["mutation_applied"] is False
    assert payload["error_message"].startswith("stale_base_revision:")
    assert "Traceback" not in payload["error_message"]
