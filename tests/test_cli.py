"""Tests for the ``supernote`` CLI command layer + verbose formatting.

Handlers take injected deps (uploader, CliConfig) so the cloud + model boundaries
are mocked; formatting is asserted on its human-readable output.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import paia_supernote.cli as cli
from paia_supernote.cli import (
    DEFAULT_RENDER_DIR,
    CliConfig,
    _format_read,
    _normalize_notebook,
    _parse_pages,
    auth_recovery_message,
    cmd_append,
    cmd_auth_status,
    cmd_ls,
    cmd_move,
    cmd_plan,
    cmd_read,
    cmd_show,
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
    cloud = {"Quick.note": b"src", "Mgmt.note": b"tgt-cloud"}
    uploads: list[str] = []

    async def fake_download(name: str) -> bytes:
        return cloud[name]

    async def fake_upload(path: str, name: str) -> bool:
        cloud[name] = Path(path).read_bytes()  # cloud reflects the write
        uploads.append(name)
        return True

    uploader = AsyncMock()
    uploader.download_notebook.side_effect = fake_download
    uploader.upload_notebook.side_effect = fake_upload
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
    assert uploads == ["Mgmt.note", "Quick.note"]  # target, then source


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
async def test_cmd_show_does_not_crash_on_fresh_state_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd_show must self-initialize the page_state schema.

    On a fresh install (no ingest ever run) the state DB has no page_state
    table; cmd_show used to crash with sqlite3.OperationalError. It should
    initialize the schema and return an empty row set instead.
    """
    config = _config(tmp_path)  # state_db_path -> nonexistent DB
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"notebook-bytes"

    monkeypatch.setattr(cli, "_load_notebook", lambda raw: object())
    monkeypatch.setattr(
        cli,
        "build_snapshot_from_notebook",
        lambda notebook_obj, **kw: SimpleNamespace(page_order=[], pages={}),
    )

    rows = await cmd_show(config, uploader, "Quick")

    assert rows == []


@pytest.mark.asyncio
async def test_cmd_append_renders_backs_up_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader = AsyncMock()
    holder = {"bytes": b"note"}

    async def fake_download(name: str) -> bytes:
        return holder["bytes"]

    async def fake_upload(path: str, name: str) -> bool:
        holder["bytes"] = Path(path).read_bytes()  # cloud reflects the write
        return True

    uploader.download_notebook.side_effect = fake_download
    uploader.upload_notebook.side_effect = fake_upload

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
    uploader.list_note_files.return_value = [
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


def test_auth_recovery_message_without_env_creds(monkeypatch) -> None:
    monkeypatch.delenv("SN_PHONE", raising=False)
    monkeypatch.delenv("SN_PASSWORD", raising=False)
    msg = auth_recovery_message()

    assert "supernote auth login" in msg
    assert "No notes were changed" in msg


def test_auth_recovery_message_with_env_creds(monkeypatch) -> None:
    monkeypatch.setenv("SN_PHONE", "+15555550100")
    monkeypatch.setenv("SN_PASSWORD", "hunter2")
    msg = auth_recovery_message()

    assert "SN_PHONE/SN_PASSWORD" in msg
    assert "No notes were changed" in msg
    # With creds set it must NOT tell a human to run auth login manually.
    assert "run `supernote auth login` to log in manually" not in msg


@pytest.mark.asyncio
async def test_cmd_auth_status_reports_auto_login_when_creds_set(monkeypatch) -> None:
    uploader = AsyncMock()
    monkeypatch.setenv("SN_PHONE", "+15555550100")
    monkeypatch.setenv("SN_PASSWORD", "hunter2")

    result = await cmd_auth_status(uploader)

    assert result["authenticated"] is True
    assert result["auto_login"] is True


@pytest.mark.asyncio
async def test_cmd_auth_status_reports_no_auto_login_without_creds(
    monkeypatch,
) -> None:
    uploader = AsyncMock()
    monkeypatch.delenv("SN_PHONE", raising=False)
    monkeypatch.delenv("SN_PASSWORD", raising=False)

    result = await cmd_auth_status(uploader)

    assert result["auto_login"] is False


@pytest.mark.asyncio
async def test_cmd_move_surfaces_auth_error_as_actionable(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = UploadAuthError("403")

    with pytest.raises(UploadAuthError):
        await cmd_move(_config(tmp_path), uploader, "Quick", pages=[0], to="Mgmt")

    uploader.upload_notebook.assert_not_awaited()


def test_main_dispatches_ls_and_prints(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_uploader = AsyncMock()
    fake_uploader.list_note_files.return_value = [
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
    # The "supernote auth login" recovery hint only appears when no
    # SN_PHONE/SN_PASSWORD env creds are set; isolate from any real .env leakage
    # so this exercises the manual-login recovery branch deterministically.
    monkeypatch.delenv("SN_PHONE", raising=False)
    monkeypatch.delenv("SN_PASSWORD", raising=False)
    fake_uploader = AsyncMock()
    fake_uploader.list_note_files.side_effect = UploadAuthError("403")
    monkeypatch.setattr(
        "paia_supernote.cli.SupernoteUploader", lambda *a, **k: fake_uploader
    )
    monkeypatch.setattr(
        "paia_supernote.cli.load_cli_config", lambda path=None: _config(tmp_path)
    )

    rc = main(["ls"])

    assert rc == 2
    assert "supernote auth login" in capsys.readouterr().err


def test_parse_pages_handles_ranges_and_lists() -> None:
    assert _parse_pages("3,4,5") == [3, 4, 5]
    assert _parse_pages("3-5") == [3, 4, 5]
    assert _parse_pages(None) is None


def test_parse_pages_rejects_garbage_with_friendly_error() -> None:
    with pytest.raises(SystemExit, match="invalid --pages"):
        _parse_pages("abc")


def test_normalize_notebook_strips_dot_note_suffix() -> None:
    assert _normalize_notebook("Quick.note") == "Quick"
    assert _normalize_notebook("Quick") == "Quick"
    assert _normalize_notebook("quick.NOTE") == "quick"  # case-insensitive
    assert _normalize_notebook("Home planning.note") == "Home planning"
    # a name that merely contains 'note' is left untouched
    assert _normalize_notebook("Notes") == "Notes"


def test_default_render_dir_matches_help_text() -> None:
    assert DEFAULT_RENDER_DIR == Path("/tmp")


def test_format_read_includes_image_path_when_present() -> None:
    rows = [{"page": 5, "text": "hello", "image": "/tmp/Quick-page-5.png"}]
    out = _format_read(rows)
    assert "[page 5]" in out
    assert "hello" in out
    assert "(image: /tmp/Quick-page-5.png)" in out


def test_format_read_omits_image_line_when_absent() -> None:
    rows = [{"page": 5, "text": "hello"}]
    assert "(image:" not in _format_read(rows)


@pytest.mark.asyncio
async def test_cmd_read_render_writes_png_and_reports_path(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.return_value = b"note"
    config = _config(tmp_path)
    fake_image = MagicMock()
    config.reader.read_pages = AsyncMock(
        return_value=[
            SimpleNamespace(page_num=5, text="hello world", page_image=fake_image)
        ]
    )

    rows = await cmd_read(
        config, uploader, "Quick", pages=[5], render=True, render_dir=str(tmp_path)
    )

    expected = str(tmp_path / "Quick-page-5.png")
    assert rows[0]["image"] == expected
    fake_image.save.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_run_command_normalizes_notebook_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    uploader = AsyncMock()
    captured: dict = {}

    async def fake_show(cfg, up, notebook, *, pages=None):
        captured["notebook"] = notebook
        return []

    monkeypatch.setattr(cli, "cmd_show", fake_show)
    args = argparse.Namespace(
        command="show", notebook="Quick.note", pages=None, json=False, quiet=True
    )

    await cli._run_command(args, config, uploader)

    assert captured["notebook"] == "Quick"  # .note stripped -> no double-suffix
