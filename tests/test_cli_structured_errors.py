from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import paia_supernote.cli as cli
from paia_supernote.cli import CliConfig


def _config(tmp_path: Path) -> CliConfig:
    return CliConfig(
        ledger_db_path=tmp_path / "filing.db",
        state_db_path=tmp_path / "state.db",
        backups_root=tmp_path / "backups",
        destination_map={},
        reader=MagicMock(),
        raw_config={"cloud_change_ledger_notebooks": ["Quick"]},
    )


def test_changes_cli_json_error_is_deterministic_shared_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _config(tmp_path))

    rc = cli.main(["--json", "changes", "Quick", "--since", "not-a-cursor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert rc == 2
    assert captured.out == ""
    assert captured.err == json.dumps(payload, indent=2) + "\n"
    assert payload["error_code"] == "unknown_notebook"
    assert payload["next_actions"] == [payload["next_step"]]
    assert payload["mutation_applied"] is False
    assert "Traceback" not in captured.err


def test_changes_cli_prose_error_is_readable_and_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _config(tmp_path))

    rc = cli.main(["changes", "Secret", "--since", "0"])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "disallowed_notebook: Notebook is not" in captured.err
    assert "Field: notebook" in captured.err
    assert "Next actions:" in captured.err
    assert "Mutation applied: no" in captured.err
    assert "Traceback" not in captured.err


def test_auth_cli_json_error_uses_shared_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class FailingUploader:
        SESSION_FILE = tmp_path / "session.json"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def ensure_authenticated(self) -> None:
            raise cli.UploadAuthError("403 stale session")

    monkeypatch.setattr(cli, "load_cli_config", lambda path=None: _config(tmp_path))
    monkeypatch.setattr(
        cli,
        "SupernoteUploader",
        lambda *args, **kwargs: FailingUploader(),
    )
    monkeypatch.delenv("SN_PHONE", raising=False)
    monkeypatch.delenv("SN_PASSWORD", raising=False)

    rc = cli.main(["--json", "auth", "status"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert rc == 2
    assert payload["error_code"] == "cloud_auth_required"
    assert payload["retryable"] is True
    assert payload["mutation_applied"] is False
    assert payload["received"]["session"] == "<redacted>"
