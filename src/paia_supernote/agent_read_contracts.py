"""Pydantic-backed cached read contract for Supernote Cloud ledger state.

This module is intentionally read-contract only: it reads the local SQLite
ledger/page-state cache and never instantiates CloudPoller or SupernoteUploader.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .cloud_change_ledger import CloudChangeLedger, PageChangeRecord
from .config import resolve_ledger_notebooks
from .contract_errors import AgentContractError, make_agent_error
from .page_state import PageState, PageStateStore

CursorKind = Literal["change_id", "timestamp", "agent"]


class ReadContractError(AgentContractError):
    """Exception carrying a structured ``AgentError`` response."""


class LatestNotebookStateRequest(BaseModel):
    notebook: str = Field(min_length=1)
    include_text: bool = False

    @field_validator("notebook")
    @classmethod
    def _strip_notebook(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("notebook must not be blank")
        return _strip_note_suffix(cleaned)


class NotebookChangesRequest(BaseModel):
    notebook: str = Field(min_length=1)
    since: int | str | datetime | None = None
    agent: str | None = None

    @field_validator("notebook")
    @classmethod
    def _strip_notebook(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("notebook must not be blank")
        return _strip_note_suffix(cleaned)

    @field_validator("agent")
    @classmethod
    def _strip_agent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("agent must not be blank")
        return cleaned


class AgentCursorRequest(BaseModel):
    agent: str = Field(min_length=1)
    notebook: str = Field(min_length=1)

    @field_validator("agent", "notebook")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("notebook")
    @classmethod
    def _strip_notebook_suffix(cls, value: str) -> str:
        return _strip_note_suffix(value)


class AdvanceAgentCursorRequest(AgentCursorRequest):
    change_id: int = Field(ge=0)


class CachedPageState(BaseModel):
    page_id: str
    page_index: int
    content_hash: str
    ocr_status: str
    text_preview: str | None = None
    text: str | None = None
    ocr_model: str | None = None


class LatestNotebookStateResponse(BaseModel):
    notebook: str
    notebook_revision: str
    notebook_hash: str
    cloud_update_time: int | None
    observed_at: str
    page_count: int
    next_cursor: int
    pages: list[CachedPageState]


class CachedPageChange(BaseModel):
    change_id: int
    notebook: str
    revision: str
    page_id: str
    change_type: str
    old_hash: str | None
    new_hash: str | None
    old_index: int | None
    new_index: int | None
    observed_at: str
    ocr_status: str | None = None
    text_preview: str | None = None


class NotebookChangesResponse(BaseModel):
    notebook: str
    cursor_kind: CursorKind
    cursor: int | str
    next_cursor: int
    notebook_revision: str
    changes: list[CachedPageChange]


class AgentCursorResponse(BaseModel):
    agent: str
    notebook: str
    cursor: int


class AdvanceAgentCursorResponse(BaseModel):
    agent: str
    notebook: str
    previous_cursor: int
    cursor: int
    advanced: bool


class SupernoteReadContract:
    """Cached notebook/change read contract backed only by local SQLite state."""

    def __init__(self, config: Mapping[str, Any], state_db_path: Path) -> None:
        self._config = dict(config)
        self._ledger = CloudChangeLedger(Path(state_db_path))
        self._page_state = PageStateStore(Path(state_db_path))
        self._page_state.init_schema()
        self._ledger.init_schema()

    def get_latest_notebook_state(
        self, request: LatestNotebookStateRequest
    ) -> LatestNotebookStateResponse:
        notebook = self._canonical_notebook(request.notebook)
        latest = self._require_latest_state(notebook)
        current_pages = self._ledger.current_pages(notebook)
        cached_pages = self._page_state.list_pages(notebook)
        latest_cursor = self._ledger.latest_change_id(notebook) or 0
        return LatestNotebookStateResponse(
            notebook=notebook,
            notebook_revision=latest.cloud_revision,
            notebook_hash=latest.notebook_hash,
            cloud_update_time=latest.cloud_update_time,
            observed_at=latest.observed_at,
            page_count=latest.page_count,
            next_cursor=latest_cursor,
            pages=[
                CachedPageState(
                    page_id=page.page_id,
                    page_index=page.page_index,
                    content_hash=page.content_hash,
                    ocr_status=page.ocr_status,
                    text_preview=_preview(state.raw_text) if state else None,
                    text=state.raw_text if state and request.include_text else None,
                    ocr_model=state.ocr_model if state else None,
                )
                for page in current_pages
                for state in [_match_page_state(page, cached_pages)]
            ],
        )

    def get_changes(
        self, request: NotebookChangesRequest
    ) -> NotebookChangesResponse:
        notebook = self._canonical_notebook(request.notebook)
        latest = self._require_latest_state(notebook)
        cursor_kind, cursor, changes = self._changes_for_request(request, notebook)
        cached_pages = self._page_state.list_pages(notebook)
        latest_cursor = self._ledger.latest_change_id(notebook) or 0
        return NotebookChangesResponse(
            notebook=notebook,
            cursor_kind=cursor_kind,
            cursor=cursor,
            next_cursor=latest_cursor,
            notebook_revision=latest.cloud_revision,
            changes=[
                _change_response(change, cached_pages)
                for change in changes
            ],
        )

    def read_agent_cursor(self, request: AgentCursorRequest) -> AgentCursorResponse:
        notebook = self._canonical_notebook(request.notebook)
        self._require_latest_state(notebook)
        cursor = self._ledger.get_agent_cursor(request.agent, notebook) or 0
        return AgentCursorResponse(
            agent=request.agent,
            notebook=notebook,
            cursor=cursor,
        )

    def advance_agent_cursor(
        self, request: AdvanceAgentCursorRequest
    ) -> AdvanceAgentCursorResponse:
        notebook = self._canonical_notebook(request.notebook)
        self._require_latest_state(notebook)
        latest_cursor = self._ledger.latest_change_id(notebook) or 0
        if request.change_id > latest_cursor:
            raise _error(
                "invalid_cursor",
                "Cannot advance beyond the latest cached change for this notebook.",
                field="change_id",
                received={"change_id": request.change_id, "latest": latest_cursor},
                expected={"change_id": f"integer between 0 and {latest_cursor}"},
                next_step=(
                    "Query changes first and advance to the returned next_cursor."
                ),
            )
        previous = self._ledger.get_agent_cursor(request.agent, notebook) or 0
        advanced = self._ledger.advance_agent_cursor(
            request.agent,
            notebook,
            request.change_id,
        )
        return AdvanceAgentCursorResponse(
            agent=request.agent,
            notebook=notebook,
            previous_cursor=previous,
            cursor=max(previous, request.change_id),
            advanced=advanced,
        )

    def _changes_for_request(
        self, request: NotebookChangesRequest, notebook: str
    ) -> tuple[CursorKind, int | str, list[PageChangeRecord]]:
        if request.agent is not None:
            cursor = self._ledger.get_agent_cursor(request.agent, notebook) or 0
            return "agent", cursor, self._ledger.changes_since(notebook, cursor)
        if request.since is None:
            return "change_id", 0, self._ledger.changes_since(notebook, 0)
        if isinstance(request.since, int):
            cursor = self._validate_change_cursor(notebook, request.since)
            return "change_id", cursor, self._ledger.changes_since(notebook, cursor)
        if isinstance(request.since, datetime):
            cursor = request.since.isoformat()
            return "timestamp", cursor, self._ledger.changes_after_observed_at(
                notebook,
                cursor,
            )
        cursor_text = request.since.strip()
        if cursor_text.isdigit():
            cursor = self._validate_change_cursor(notebook, int(cursor_text))
            return "change_id", cursor, self._ledger.changes_since(notebook, cursor)
        timestamp = _parse_timestamp(cursor_text)
        return "timestamp", timestamp, self._ledger.changes_after_observed_at(
            notebook,
            timestamp,
        )

    def _validate_change_cursor(self, notebook: str, cursor: int) -> int:
        latest_cursor = self._ledger.latest_change_id(notebook) or 0
        if cursor < 0 or cursor > latest_cursor:
            raise _error(
                "invalid_cursor",
                "The requested change cursor is outside the cached ledger range.",
                field="since",
                received={"cursor": cursor, "latest": latest_cursor},
                expected={"since": f"integer between 0 and {latest_cursor}"},
                valid_examples=[{"since": 0}, {"since": latest_cursor}],
                next_step=(
                    "Use the next_cursor returned by the previous changes response."
                ),
            )
        return cursor

    def _canonical_notebook(self, notebook: str) -> str:
        requested = _strip_note_suffix(notebook).casefold()
        for allowed in resolve_ledger_notebooks(self._config):
            cleaned = _strip_note_suffix(allowed)
            if cleaned.casefold() == requested:
                return cleaned
        raise _error(
            "disallowed_notebook",
            "Notebook is not in the configured Cloud change ledger allowlist.",
            field="notebook",
            received={"notebook": notebook},
            expected={"allowed_notebooks": resolve_ledger_notebooks(self._config)},
            valid_examples=[
                {"notebook": name}
                for name in resolve_ledger_notebooks(self._config)[:3]
            ],
            next_step="Choose an allowlisted notebook or update configuration first.",
        )

    def _require_latest_state(self, notebook: str):
        latest = self._ledger.latest_notebook_state(notebook)
        if latest is None:
            raise _error(
                "unknown_notebook",
                "No cached ledger snapshot exists for this allowlisted notebook.",
                field="notebook",
                received={"notebook": notebook},
                expected={"cached_snapshot": "created by successful Cloud ingest"},
                valid_examples=[{"command": f"supernote changes {notebook} --json"}],
                next_step="Run or wait for Cloud ingest to cache the notebook first.",
            )
        return latest


def _change_response(
    change: PageChangeRecord, cached_pages: list[PageState]
) -> CachedPageChange:
    state = _match_change_state(change, cached_pages)
    ocr_status = "not_applicable" if change.new_index is None else None
    if state is not None:
        ocr_status = "ready"
    return CachedPageChange(
        change_id=change.change_id,
        notebook=change.notebook,
        revision=change.revision,
        page_id=change.page_id,
        change_type=change.change_type,
        old_hash=change.old_hash,
        new_hash=change.new_hash,
        old_index=change.old_index,
        new_index=change.new_index,
        observed_at=change.observed_at,
        ocr_status=ocr_status,
        text_preview=_preview(state.raw_text) if state else None,
    )


def _match_page_state(page: Any, cached_pages: list[PageState]) -> PageState | None:
    suffix = f":{page.page_id}:{page.content_hash}"
    for state in cached_pages:
        if state.source_revision.endswith(suffix):
            return state
    for state in cached_pages:
        if state.page == page.page_index:
            return state
    return None


def _match_change_state(
    change: PageChangeRecord, cached_pages: list[PageState]
) -> PageState | None:
    if change.new_hash is None or change.new_index is None:
        return None
    suffix = f":{change.page_id}:{change.new_hash}"
    for state in cached_pages:
        if state.source_revision.endswith(suffix):
            return state
    for state in cached_pages:
        if state.page == change.new_index:
            return state
    return None


def _parse_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _error(
            "invalid_cursor",
            "The requested since cursor is neither an integer nor an ISO timestamp.",
            field="since",
            received={"since": value},
            expected={"since": "integer change ID or ISO-8601 timestamp"},
            valid_examples=[{"since": 0}, {"since": "2026-07-19T12:00:00+00:00"}],
            next_step="Pass a prior next_cursor or an ISO timestamp from observed_at.",
        ) from None
    return parsed.isoformat()


def _preview(text: str, limit: int = 120) -> str:
    first_line = " ".join(text.split())
    return first_line[:limit]


def _strip_note_suffix(notebook: str) -> str:
    cleaned = notebook.strip()
    return cleaned[:-5] if cleaned.lower().endswith(".note") else cleaned


def _error(
    error_code: str,
    message: str,
    *,
    next_step: str,
    field: str | None = None,
    received: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    valid_examples: list[dict[str, Any]] | None = None,
    retryable: bool = False,
) -> ReadContractError:
    return ReadContractError(
        make_agent_error(
            error_code,
            message,
            field=field,
            received=received,
            expected=expected,
            valid_examples=valid_examples,
            retryable=retryable,
            next_step=next_step,
            mutation_applied=False,
        )
    )
