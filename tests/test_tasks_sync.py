"""Tests for tasks.note synchronization helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from paia_supernote.tasks_sync import TasksSync


def _make_mock_page() -> MagicMock:
    main_layer = MagicMock()
    main_layer.get_name.return_value = "MAINLAYER"
    scratch_layer = MagicMock()
    scratch_layer.get_name.return_value = "SCRATCH"

    page = MagicMock()
    page.metadata = {
        "RECOGNTEXT": "111",
        "RECOGNFILE": "222",
        "TOTALPATH": "333",
        "EXTERNALLINKINFO": "444",
        "IDTABLE": "555",
        "RECOGNSTATUS": "1",
        "RECOGNFILESTATUS": "1",
    }
    page.is_layer_supported.return_value = True
    page.get_layer.return_value = main_layer
    page.get_layers.return_value = [main_layer, scratch_layer]
    return page


@pytest.mark.asyncio
async def test_replace_all_pages_uses_shared_recognition_metadata_reset() -> None:
    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key="lin_api_test",
        linear_team_key="LFW",
    )
    page = _make_mock_page()
    notebook = MagicMock()
    notebook.get_total_pages.return_value = 1
    notebook.get_page.return_value = page
    notebook.pages = [page]
    notebook.metadata = MagicMock()
    notebook.metadata.pages = [page.metadata]

    with patch("supernotelib.parser.load_notebook", return_value=notebook), \
         patch("supernotelib.manipulator.reconstruct", return_value=b"reconstructed"):
        result = await sync._replace_all_pages(b"existing-note", [b"lane-rle"])

    assert result == b"reconstructed"
    for key in (
        "RECOGNTEXT",
        "RECOGNFILE",
        "TOTALPATH",
        "EXTERNALLINKINFO",
        "IDTABLE",
    ):
        assert page.metadata[key] == "0"
    assert page.metadata["RECOGNSTATUS"] == "0"
    assert page.metadata["RECOGNFILESTATUS"] == "0"
    page.get_layer(0).set_content.assert_called_once_with(b"lane-rle")
    page.get_layers.return_value[1].set_content.assert_called_once_with(b"")


@pytest.mark.asyncio
async def test_poll_once_fetches_linear_issues() -> None:
    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key="lin_api_test",
        linear_team_key="LFW",
    )
    sync._writer.render_tasks_page = MagicMock(return_value=b"rle-bytes")
    sync._uploader.upload_notebook = AsyncMock(return_value=True)

    mock_issues = [
        {"identifier": "LFW-1", "title": "Test issue", "state": {"name": "Todo"}},
    ]

    with patch.object(sync._linear, "execute", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"status": "ok", "issues": mock_issues}

        with patch.object(sync, "_rebuild_tasks_note", new_callable=AsyncMock) as mock_rebuild:
            await sync._poll_once()

        mock_execute.assert_called_once_with(
            "list_issues",
            team_key="LFW",
            limit=50,
        )


@pytest.mark.asyncio
async def test_poll_once_skips_on_linear_error() -> None:
    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key="lin_api_test",
        linear_team_key="LFW",
    )

    with patch.object(sync._linear, "execute", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"status": "error", "error": "API key invalid"}
        await sync._poll_once()


def _not_authorized_result() -> dict:
    """Shape returned by LinearTool.execute when the API key is missing."""
    return {
        "status": "error",
        "success": False,
        "tool": "linear",
        "error_code": "NOT_AUTHORIZED",
        "message": "LINEAR_API_KEY not configured.",
        "retryable": False,
        "next_step": "stop",
        "action": "list_issues",
    }


@pytest.mark.asyncio
async def test_fetch_failure_logs_real_cause_and_code() -> None:
    """A failed fetch must log the repair message/error_code, never a null error."""
    import structlog

    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key=None,
        linear_team_key="LFW",
    )
    with patch.object(
        sync._linear, "execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = _not_authorized_result()
        with structlog.testing.capture_logs() as logs:
            await sync._poll_once()

    failed = [e for e in logs if e["event"] == "tasks_fetch_failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "LINEAR_API_KEY not configured."
    assert failed[0]["error_code"] == "NOT_AUTHORIZED"
    assert failed[0]["retryable"] is False


@pytest.mark.asyncio
async def test_non_retryable_failure_backs_off_and_caps() -> None:
    """Non-retryable failures double the poll interval up to MAX_POLL_INTERVAL."""
    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key=None,
        linear_team_key="LFW",
        poll_interval=60,
    )
    assert sync._effective_interval == 60
    with patch.object(
        sync._linear, "execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = _not_authorized_result()
        await sync._poll_once()
        assert sync._effective_interval == 120
        await sync._poll_once()
        assert sync._effective_interval == 240
        for _ in range(20):
            await sync._poll_once()
        assert sync._effective_interval == sync.MAX_POLL_INTERVAL


@pytest.mark.asyncio
async def test_retryable_failure_keeps_base_interval() -> None:
    """Transient (retryable) errors keep the configured cadence."""
    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key="k",
        linear_team_key="LFW",
        poll_interval=60,
    )
    transient = {
        "status": "error",
        "error_code": "TRANSPORT_ERROR",
        "message": "connect timeout",
        "retryable": True,
    }
    with patch.object(
        sync._linear, "execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = transient
        await sync._poll_once()
        await sync._poll_once()
    assert sync._effective_interval == 60


@pytest.mark.asyncio
async def test_repeated_failures_log_at_most_once_per_hour() -> None:
    """125k identical failures must not produce 125k log lines."""
    import structlog

    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key=None,
        linear_team_key="LFW",
    )
    with patch.object(
        sync._linear, "execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = _not_authorized_result()
        with structlog.testing.capture_logs() as logs:
            for _ in range(100):
                await sync._poll_once()

    failed = [e for e in logs if e["event"] == "tasks_fetch_failed"]
    assert len(failed) == 1, f"expected 1 log line, got {len(failed)}"


@pytest.mark.asyncio
async def test_recovery_logs_once_and_resets_interval() -> None:
    import structlog

    sync = TasksSync(
        uploader=MagicMock(),
        writer=MagicMock(),
        linear_api_key=None,
        linear_team_key="LFW",
        poll_interval=60,
    )
    with patch.object(
        sync._linear, "execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = _not_authorized_result()
        with structlog.testing.capture_logs() as logs:
            await sync._poll_once()
        assert sync._effective_interval > 60

        mock_execute.return_value = {"status": "ok", "issues": []}
        with patch.object(sync, "_rebuild_tasks_note", new_callable=AsyncMock):
            with structlog.testing.capture_logs() as logs:
                await sync._poll_once()

    recovered = [e for e in logs if e["event"] == "tasks_fetch_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["suppressed_failures"] == 1
    assert sync._effective_interval == 60
    assert sync._consecutive_failures == 0
