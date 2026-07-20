import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.agent_write_contracts import (
    AgentWriteAccepted,
    AgentWriteRevisionError,
    validate_agent_write_request,
)
from paia_supernote.cloud_change_ledger import (
    CloudChangeLedger,
    NotebookSnapshot,
    PageRevision,
)
from paia_supernote.main import DEFAULT_CONFIG, SupernoteService


class SpyUploader:
    def __init__(self) -> None:
        self.page = object()
        self.start = AsyncMock()
        self.download_notebook = AsyncMock(return_value=b"notebook")
        self.upload_notebook = AsyncMock(return_value=True)


def _config(tmp_path: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    config["cloud_change_ledger_notebooks"] = ["Quick", "Walk"]
    return config


def _seed_revision(tmp_path: Path, notebook: str, revision: str) -> None:
    ledger = CloudChangeLedger(tmp_path / "state.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        NotebookSnapshot(
            notebook=notebook,
            cloud_revision=revision,
            cloud_update_time=123,
            pages=[
                PageRevision(
                    page_id=f"{notebook.lower()}-page-1",
                    page_index=0,
                    content_hash=f"{notebook.lower()}-hash-1",
                )
            ],
        )
    )


def _service(tmp_path: Path) -> SupernoteService:
    service = SupernoteService(config=_config(tmp_path))
    service.uploader = SpyUploader()
    service.events = AsyncMock()
    return service


def _published_error(service: SupernoteService) -> dict:
    error_text = service.events.publish_write_failed.await_args.kwargs["error"]
    return json.loads(error_text)


@pytest.mark.asyncio
async def test_missing_notebook_fails_before_uploader_or_apply_calls(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service.uploader.page = None
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-current",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service.uploader.start.assert_not_awaited()
    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
    service._replace_pages_with_uploader.assert_not_awaited()
    error = _published_error(service)
    assert error["error_code"] == "missing_notebook"
    assert error["field"] == "notebook"
    assert error["mutation_applied"] is False
    assert error["retryable"] is False
    assert error["received"]["agent"] == "Sam"
    assert error["expected"]["notebook"] == "non-empty target notebook name"
    assert error["expected"]["or"] == {"use_agent_default_notebook": True}
    assert {"agent": "Sam", "use_agent_default_notebook": True}.items() <= error[
        "valid_examples"
    ][1].items()
    assert "Resubmit" in error["next_step"]


@pytest.mark.asyncio
async def test_use_agent_default_notebook_opt_in_preserves_replace_pages_upload_path(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "use_agent_default_notebook": True,
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-current",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service._replace_pages_with_uploader.assert_awaited_once()
    assert (
        service._replace_pages_with_uploader.await_args.kwargs["target_name"]
        == "Quick.note"
    )
    service.events.publish_write_completed.assert_awaited_once()
    service.events.publish_write_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_base_revision_fails_before_uploader_or_apply_calls(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service.uploader.page = None
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service.uploader.start.assert_not_awaited()
    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
    service._replace_pages_with_uploader.assert_not_awaited()
    error = _published_error(service)
    assert error["error_code"] == "missing_base_revision"
    assert error["mutation_applied"] is False
    assert error["current_revision"] is None


@pytest.mark.asyncio
async def test_stale_base_revision_fails_before_upload_calls(tmp_path: Path) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-old",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
    service._replace_pages_with_uploader.assert_not_awaited()
    error = _published_error(service)
    assert error["error_code"] == "stale_base_revision"
    assert error["requested_base_revision"] == "rev-old"
    assert error["current_revision"] == "rev-current"
    assert error["notebook"] == "Quick"
    assert error["mutation_applied"] is False
    assert "retry" in error["next_step"].lower()


@pytest.mark.asyncio
async def test_mismatched_base_notebook_fails_before_upload_calls(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "base_notebook": "Walk",
            "base_notebook_revision": "rev-current",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service.uploader.download_notebook.assert_not_awaited()
    service.uploader.upload_notebook.assert_not_awaited()
    service._replace_pages_with_uploader.assert_not_awaited()
    error = _published_error(service)
    assert error["error_code"] == "notebook_revision_mismatch"
    assert error["requested_base_revision"] == "rev-current"
    assert error["mutation_applied"] is False


@pytest.mark.asyncio
async def test_current_base_revision_preserves_replace_pages_upload_path(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service._replace_pages_with_uploader = AsyncMock(return_value=True)

    await service._handle_write_request(
        {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "base_notebook_revision": "rev-current",
            "pages": [{"agent": "Sam", "content": "Page 1"}],
        }
    )

    service._replace_pages_with_uploader.assert_awaited_once()
    assert (
        service._replace_pages_with_uploader.await_args.kwargs["target_name"]
        == "Quick.note"
    )
    service.events.publish_write_completed.assert_awaited_once()
    service.events.publish_write_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_base_revision_preserves_append_upload_path(
    tmp_path: Path,
) -> None:
    _seed_revision(tmp_path, "Quick", "rev-current")
    service = _service(tmp_path)
    service.writer = MagicMock()
    service.writer.render_page.return_value = b"rle"
    # 1st download = base bytes; re-verify download = the appended bytes uploaded
    service.uploader.download_notebook = AsyncMock(
        side_effect=[b"notebook", b"updated-notebook"]
    )

    with patch(
        "paia_supernote.main.append_page_to_notebook",
        return_value=b"updated-notebook",
    ) as append_page:
        await service._handle_write_request(
            {
                "agent": "Sam",
                "notebook": "Quick",
                "content": "hello",
                "base_notebook_revision": "rev-current",
            }
        )

    assert service.uploader.download_notebook.await_count == 2
    service.writer.render_page.assert_called_once_with("Sam", "hello")
    append_page.assert_called_once_with(b"notebook", b"rle")
    service.uploader.upload_notebook.assert_awaited_once()
    service.events.publish_write_completed.assert_awaited_once()
    service.events.publish_write_failed.assert_not_awaited()


# --- write guard contract (before any uploader call) ------------------------


def test_disallowed_notebook_fails_at_write_guard_before_upload(
    tmp_path: Path,
) -> None:
    """The write guard rejects non-allowlisted notebooks before upload."""
    _seed_revision(tmp_path, "Quick", "rev-current")

    with pytest.raises(AgentWriteRevisionError) as exc:
        validate_agent_write_request(
            {
                "agent": "Sam",
                "notebook": "Other",
                "base_notebook_revision": "rev-current",
            },
            config={"cloud_change_ledger_notebooks": ["Quick"]},
            state_db_path=tmp_path / "state.db",
            resolved_agent="Sam",
            resolved_notebook="Other",
        )

    conflict = exc.value.conflict
    assert conflict.error_code == "disallowed_notebook"
    assert conflict.mutation_applied is False


def test_unknown_notebook_revision_fails_at_write_guard_before_upload(
    tmp_path: Path,
) -> None:
    """An allowlisted notebook with no cached snapshot fails closed at the guard."""
    with pytest.raises(AgentWriteRevisionError) as exc:
        validate_agent_write_request(
            {
                "agent": "Sam",
                "notebook": "Empty",
                "base_notebook_revision": "rev-current",
            },
            config={"cloud_change_ledger_notebooks": ["Empty"]},
            state_db_path=tmp_path / "state.db",
            resolved_agent="Sam",
            resolved_notebook="Empty",
        )

    conflict = exc.value.conflict
    assert conflict.error_code == "unknown_notebook_revision"
    assert conflict.retryable is True
    assert conflict.mutation_applied is False


def test_matching_base_revision_returns_accepted_at_write_guard(
    tmp_path: Path,
) -> None:
    """A current base revision passes the guard, proving it is the sole gate."""
    _seed_revision(tmp_path, "Quick", "rev-current")

    accepted = validate_agent_write_request(
        {
            "agent": "Sam",
            "notebook": "Quick",
            "base_notebook_revision": "rev-current",
        },
        config={"cloud_change_ledger_notebooks": ["Quick"]},
        state_db_path=tmp_path / "state.db",
        resolved_agent="Sam",
        resolved_notebook="Quick",
    )

    assert isinstance(accepted, AgentWriteAccepted)
    assert accepted.notebook == "Quick"
    assert accepted.base_notebook_revision == "rev-current"
    assert accepted.current_revision == "rev-current"
    assert accepted.mutation_applied is False
