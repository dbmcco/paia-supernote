"""Tests for cached Supernote agent read contracts and CLI query surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import paia_supernote.cli as cli
from paia_supernote.agent_read_contracts import (
    AdvanceAgentCursorRequest,
    AgentCursorRequest,
    LatestNotebookStateRequest,
    NotebookChangesRequest,
    ReadContractError,
    SupernoteReadContract,
)
from paia_supernote.cli import CliConfig
from paia_supernote.cloud_change_ledger import (
    CHANGE_ADDED,
    CHANGE_UPDATED,
    CloudChangeLedger,
    NotebookSnapshot,
    PageChangeRecord,
    PageRevision,
)
from paia_supernote.page_state import PageStateStore


def _snapshot(
    notebook: str,
    revision: str,
    pages: list[tuple[str, str]],
) -> NotebookSnapshot:
    return NotebookSnapshot(
        notebook=notebook,
        cloud_revision=revision,
        cloud_update_time=None,
        pages=[
            PageRevision(page_id=page_id, page_index=index, content_hash=hash_)
            for index, (page_id, hash_) in enumerate(pages)
        ],
    )


def _seed_contract(
    tmp_path: Path,
) -> tuple[
    SupernoteReadContract,
    Path,
    int,
    list[PageChangeRecord],
]:
    db_path = tmp_path / "state.db"
    page_state = PageStateStore(db_path)
    page_state.init_schema()
    ledger = CloudChangeLedger(db_path)
    ledger.init_schema()

    first_changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")])
    )
    page_state.upsert_ocr_page("Quick", 0, "rev-1:p0:h0", "old page zero", "m")
    page_state.upsert_ocr_page("Quick", 1, "rev-1:p1:h1", "page one text", "m")
    ledger.mark_page_ocr_status("Quick", "p0", "ready")
    ledger.mark_page_ocr_status("Quick", "p1", "ready")
    cursor = ledger.latest_change_id("Quick")
    assert cursor is not None

    second_changes = ledger.apply_snapshot(
        _snapshot(
            "Quick",
            "rev-2",
            [("p0", "h0-updated"), ("p1", "h1"), ("p2", "h2")],
        )
    )
    page_state.upsert_ocr_page(
        "Quick",
        0,
        "rev-2:p0:h0-updated",
        "updated page zero text",
        "m",
    )
    page_state.upsert_ocr_page("Quick", 2, "rev-2:p2:h2", "new page two text", "m")
    ledger.mark_page_ocr_status("Quick", "p0", "ready")
    ledger.mark_page_ocr_status("Quick", "p2", "ready")

    contract = SupernoteReadContract(
        {"cloud_change_ledger_notebooks": ["Quick"]},
        db_path,
    )
    assert first_changes
    return contract, db_path, cursor, second_changes


def test_cached_latest_notebook_state_returns_pages_and_ocr_without_cloud(
    tmp_path: Path,
) -> None:
    contract, _, _, _ = _seed_contract(tmp_path)

    response = contract.get_latest_notebook_state(
        LatestNotebookStateRequest(notebook="Quick.note", include_text=True)
    )

    assert response.notebook == "Quick"
    assert response.notebook_revision == "rev-2"
    assert response.page_count == 3
    assert [page.page_id for page in response.pages] == ["p0", "p1", "p2"]
    assert response.pages[0].ocr_status == "ready"
    assert response.pages[0].text == "updated page zero text"
    assert response.pages[2].text_preview == "new page two text"


def test_explicit_change_cursor_returns_ordered_cached_changes(
    tmp_path: Path,
) -> None:
    contract, _, cursor, second_changes = _seed_contract(tmp_path)

    response = contract.get_changes(
        NotebookChangesRequest(notebook="Quick", since=cursor)
    )

    assert [change.change_id for change in response.changes] == [
        change.change_id for change in second_changes
    ]
    assert {change.change_type for change in response.changes} == {
        CHANGE_ADDED,
        CHANGE_UPDATED,
    }
    assert response.next_cursor == max(change.change_id for change in second_changes)
    assert response.changes[0].text_preview is not None


def test_timestamp_cursor_and_empty_diffs_are_replayable(tmp_path: Path) -> None:
    contract, _, cursor, second_changes = _seed_contract(tmp_path)
    ledger = contract._ledger  # read-only test access to stored timestamps
    first_observed_at = ledger.changes_since("Quick", 0)[0].observed_at

    by_timestamp = contract.get_changes(
        NotebookChangesRequest(notebook="Quick", since=first_observed_at)
    )
    after_latest = contract.get_changes(
        NotebookChangesRequest(notebook="Quick", since=by_timestamp.next_cursor)
    )

    assert [change.change_id for change in by_timestamp.changes] == [
        change.change_id for change in second_changes
    ]
    assert after_latest.changes == []
    assert after_latest.cursor == max(cursor, by_timestamp.next_cursor)


def test_per_agent_cursors_can_read_advance_and_remain_isolated(
    tmp_path: Path,
) -> None:
    contract, _, _, second_changes = _seed_contract(tmp_path)
    agent = "Sam Agent/../one"

    initial = contract.read_agent_cursor(
        AgentCursorRequest(agent=agent, notebook="Quick")
    )
    changes = contract.get_changes(
        NotebookChangesRequest(notebook="Quick", agent=agent)
    )
    advanced = contract.advance_agent_cursor(
        AdvanceAgentCursorRequest(
            agent=agent,
            notebook="Quick",
            change_id=changes.next_cursor,
        )
    )
    other_agent = contract.read_agent_cursor(
        AgentCursorRequest(agent="Sam Agent/../two", notebook="Quick")
    )

    assert initial.cursor == 0
    assert len(changes.changes) == 4
    assert advanced.advanced is True
    assert advanced.cursor == max(change.change_id for change in second_changes)
    assert other_agent.cursor == 0


def test_invalid_unknown_and_disallowed_requests_return_structured_errors(
    tmp_path: Path,
) -> None:
    contract, db_path, _, _ = _seed_contract(tmp_path)

    with pytest.raises(ReadContractError) as invalid_cursor:
        contract.get_changes(NotebookChangesRequest(notebook="Quick", since=999))
    with pytest.raises(ReadContractError) as bad_timestamp:
        contract.get_changes(
            NotebookChangesRequest(notebook="Quick", since="not-a-cursor")
        )
    with pytest.raises(ReadContractError) as disallowed:
        contract.get_changes(NotebookChangesRequest(notebook="Secret", since=0))
    unknown_contract = SupernoteReadContract(
        {"cloud_change_ledger_notebooks": ["Empty"]},
        db_path,
    )
    with pytest.raises(ReadContractError) as unknown:
        unknown_contract.get_changes(NotebookChangesRequest(notebook="Empty", since=0))

    assert invalid_cursor.value.error.error_code == "invalid_cursor"
    assert bad_timestamp.value.error.error_code == "invalid_cursor"
    assert disallowed.value.error.error_code == "disallowed_notebook"
    assert unknown.value.error.error_code == "unknown_notebook"
    assert invalid_cursor.value.error.mutation_applied is False
    assert invalid_cursor.value.error.next_step


def test_advance_agent_cursor_beyond_latest_raises_structured_error(
    tmp_path: Path,
) -> None:
    """Advancing a per-agent cursor beyond the latest cached change fails with
    a structured invalid_cursor error rather than silently persisting a gap.
    """
    contract, _, _, _ = _seed_contract(tmp_path)
    latest = contract._ledger.latest_change_id("Quick")
    assert latest is not None

    with pytest.raises(ReadContractError) as exc:
        contract.advance_agent_cursor(
            AdvanceAgentCursorRequest(
                agent="Sam", notebook="Quick", change_id=latest + 1
            )
        )

    assert exc.value.error.error_code == "invalid_cursor"
    assert exc.value.error.mutation_applied is False


def test_changes_cli_json_reads_cache_without_starting_uploader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, db_path, cursor, _ = _seed_contract(tmp_path)
    config = CliConfig(
        ledger_db_path=tmp_path / "filing.db",
        state_db_path=db_path,
        backups_root=tmp_path / "backups",
        destination_map={},
        reader=object(),
        raw_config={"cloud_change_ledger_notebooks": ["Quick"]},
    )

    def fail_uploader(*args, **kwargs):
        raise AssertionError("changes command must not instantiate Cloud uploader")

    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: config)
    monkeypatch.setattr(cli, "SupernoteUploader", fail_uploader)

    rc = cli.main(["--json", "changes", "Quick", "--since", str(cursor)])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["notebook"] == "Quick"
    assert payload["cursor"] == cursor
    assert len(payload["changes"]) == 2


def test_changes_cli_structured_error_is_json_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, db_path, _, _ = _seed_contract(tmp_path)
    config = CliConfig(
        ledger_db_path=tmp_path / "filing.db",
        state_db_path=db_path,
        backups_root=tmp_path / "backups",
        destination_map={},
        reader=object(),
        raw_config={"cloud_change_ledger_notebooks": ["Quick"]},
    )
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: config)

    rc = cli.main(["--json", "changes", "Secret", "--since", "0"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert rc == 2
    assert payload["error_code"] == "disallowed_notebook"
    assert payload["mutation_applied"] is False
