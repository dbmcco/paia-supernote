"""Tests for the ``supernote`` CLI command layer + verbose formatting.

Handlers take injected deps (uploader, CliConfig) so the cloud + model boundaries
are mocked; formatting is asserted on its human-readable output.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from paia_supernote.cli import (
    CliConfig,
    auth_recovery_message,
    cmd_append,
    cmd_ls,
    cmd_move,
    cmd_plan,
    format_move_result,
    format_plan,
    main,
)
from paia_supernote.move_pipeline import (
    MovePlan,
    MoveResult,
    NotebookOutcome,
    PlannedMove,
)
from paia_supernote.uploader import UploadAuthError


def _config(tmp_path: Path) -> CliConfig:
    return CliConfig(
        ledger_db_path=tmp_path / "filing.db",
        state_db_path=tmp_path / "state.db",
        backups_root=tmp_path / "backups",
        destination_map={"mgmt": "Mgmt", "lfw": "LFW"},
        reader=MagicMock(),
    )


def _plan(*anns: PlannedMove) -> MovePlan:
    return MovePlan(
        source_notebook="Quick",
        source_revision="r",
        candidates=[],
        annotations=list(anns),
        affected_targets=[
            a.target_notebook
            for a in anns
            if a.target_notebook and a.ledger_status != "already_moved"
        ],
    )


@pytest.mark.asyncio
async def test_cmd_move_explicit_pages_runs_safe_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"src"
    uploader.upload_notebook.return_value = True
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.copy_pages_to_end",
        lambda s, t, source_pages: b"tgt+",
    )
    monkeypatch.setattr(
        "paia_supernote.quick_filing_service.remove_pages", lambda s, pages: b"src-"
    )
    monkeypatch.setattr(
        "paia_supernote.move_pipeline.verify_notebook_bytes", lambda name, raw: 3
    )

    result = await cmd_move(
        _config(tmp_path), uploader, "Quick", pages=[1, 2], to="Mgmt"
    )

    assert result.completed_pages == [1, 2]
    assert result.backup_dir is not None
    assert (result.backup_dir / "Quick.note").exists()
    assert (result.backup_dir / "Mgmt.note").exists()
    assert uploader.upload_notebook.await_count == 2  # target, then source


@pytest.mark.asyncio
async def test_cmd_move_dry_run_writes_nothing(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"src"

    result = await cmd_move(
        _config(tmp_path), uploader, "Quick", pages=[0], to="Mgmt", dry_run=True
    )

    assert result.dry_run is True
    uploader.upload_notebook.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_plan_explicit_is_read_only(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"src"

    plan = await cmd_plan(_config(tmp_path), uploader, "Quick", pages=[0], to="Mgmt")

    assert isinstance(plan, MovePlan)
    assert plan.annotations[0].target_notebook == "Mgmt"
    assert plan.annotations[0].ledger_status == "would_move"
    uploader.upload_notebook.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_append_renders_backs_up_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"note"
    uploader.upload_notebook.return_value = True

    fake_writer = MagicMock()
    fake_writer.return_value.render_page.return_value = b"rle"
    monkeypatch.setattr("paia_supernote.cli.SupernoteWriter", fake_writer)
    monkeypatch.setattr(
        "paia_supernote.cli.append_page_to_notebook", lambda nb, rle: b"updated"
    )
    monkeypatch.setattr("paia_supernote.cli.verify_notebook_bytes", lambda name, raw: 5)

    result = await cmd_append(
        _config(tmp_path), uploader, "tasks", "my daily task list", agent="Avery"
    )

    assert result["uploaded"] is True
    assert result["backup_dir"] is not None
    assert (result["backup_dir"] / "tasks.note").read_bytes() == b"note"
    uploader.upload_notebook.assert_awaited_once()
    fake_writer.return_value.render_page.assert_called_once_with(
        "Avery", "my daily task list"
    )


@pytest.mark.asyncio
async def test_cmd_ls_lists_only_note_files(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader._list_note_files.return_value = [
        {"fileName": "Quick.note", "isFolder": "N", "id": "1"},
        {"fileName": "Mgmt.note", "isFolder": "N", "id": "2"},
        {"fileName": "Archive", "isFolder": "Y", "id": "3"},
    ]

    notebooks = await cmd_ls(uploader)

    assert [n["name"] for n in notebooks] == ["Quick.note", "Mgmt.note"]


def test_format_move_result_includes_backup_counts_and_next_command(
    tmp_path: Path,
) -> None:
    plan = _plan(
        PlannedMove(0, "r", "Mgmt", 0.9, "model selected", "would_move", "op1")
    )
    result = MoveResult(
        plan=plan,
        backup_dir=tmp_path,
        outcomes=[NotebookOutcome("Quick", 3, 2), NotebookOutcome("Mgmt", 2, 3)],
        completed_pages=[0],
        skipped_pages=[],
        needs_review_pages=[],
        operation_ids=["op1"],
        dry_run=False,
    )

    text = format_move_result(result)

    assert "Backed up" in text
    assert "Quick" in text and "Mgmt" in text
    assert "Next" in text  # suggested next command
    assert "op1" in text  # operation id surfaced


def test_format_plan_distinguishes_would_move_and_already_moved() -> None:
    plan = _plan(
        PlannedMove(0, "r", "Mgmt", 0.9, "x", "would_move", "op1"),
        PlannedMove(1, "r", "LFW", 0.95, "x", "already_moved", "op2"),
        PlannedMove(2, "r", None, 0.2, "no signal", "would_move", "op3"),
    )

    text = format_plan(plan)

    assert "Mgmt" in text
    assert "would move" in text.lower()
    assert "already moved" in text.lower()
    assert "needs review" in text.lower() or "review" in text.lower()


def test_auth_recovery_message_names_the_login_command() -> None:
    msg = auth_recovery_message()

    assert "supernote auth login" in msg
    assert "No notes were changed" in msg


@pytest.mark.asyncio
async def test_cmd_move_surfaces_auth_error_as_actionable(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = UploadAuthError("403")

    with pytest.raises(UploadAuthError):
        await cmd_move(_config(tmp_path), uploader, "Quick", pages=[0], to="Mgmt")

    uploader.upload_notebook.assert_not_awaited()


def test_main_dispatches_ls_and_prints(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_uploader = AsyncMock()
    fake_uploader._list_note_files.return_value = [
        {"fileName": "Quick.note", "isFolder": "N", "id": "1"}
    ]
    fake_uploader.SESSION_FILE = Path("~/.paia/supernote/session.json")
    monkeypatch.setattr(
        "paia_supernote.cli.SupernoteUploader", lambda *a, **k: fake_uploader
    )
    monkeypatch.setattr(
        "paia_supernote.cli.load_cli_config", lambda path=None: _config(tmp_path)
    )

    rc = main(["ls"])

    assert rc == 0
    assert "Quick.note" in capsys.readouterr().out
    fake_uploader.start.assert_awaited_once()
    fake_uploader.stop.assert_awaited_once()


def test_main_returns_2_and_prints_recovery_on_auth_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fake_uploader = AsyncMock()
    fake_uploader._list_note_files.side_effect = UploadAuthError("403")
    monkeypatch.setattr(
        "paia_supernote.cli.SupernoteUploader", lambda *a, **k: fake_uploader
    )
    monkeypatch.setattr(
        "paia_supernote.cli.load_cli_config", lambda path=None: _config(tmp_path)
    )

    rc = main(["ls"])

    assert rc == 2
    assert "supernote auth login" in capsys.readouterr().err
