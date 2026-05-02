"""Tests for task curator module."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone

from paia_supernote.task_curator import TaskCurator


@pytest.mark.asyncio
async def test_task_curator_reads_current_task_page_from_notebook_bytes():
    """Task curator reads notebook bytes via read_all_pages to get task page content.

    Regression: old implementation used process_file(~/Supernote/path) which (a) used
    a placeholder path that never existed and (b) skipped unchanged pages.  The fix
    must use read_all_pages so that pages 19-22 (0-based 18-21) are always readable.
    """
    # Arrange
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    from paia_supernote.reader import ReadResult
    read_results = [
        ReadResult(
            notebook="Quick",
            page_num=18,
            text="□ Task 1\n○ Task 2",
            checkboxes=[],
            content_type="task",
            timestamp=datetime.now(timezone.utc),
        ),
        ReadResult(
            notebook="Quick",
            page_num=19,
            text="☑ Completed task",
            checkboxes=[],
            content_type="task",
            timestamp=datetime.now(timezone.utc),
        ),
    ]
    mock_reader.read_all_pages = AsyncMock(return_value=read_results)

    # Act — new API: pass bytes + notebook name, not a file path string
    result = await curator._read_current_task_page(b"fake_note_bytes", "Quick")

    # Assert content from both pages is present
    assert "□ Task 1" in result
    assert "☑ Completed task" in result
    # Verify read_all_pages (not process_file) is called with the task-page range
    mock_reader.read_all_pages.assert_awaited_once_with(
        b"fake_note_bytes", "Quick", page_range=(18, 21)
    )


@pytest.mark.asyncio
async def test_task_curator_creates_linear_issues_for_new_checkboxes():
    """Task curator creates Linear issues when new handwritten □/○ items are detected."""
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader, linear_api_key="lin_api_test")

    from paia_supernote.reader import ReadResult, CheckboxItem
    checkbox_items = [
        CheckboxItem(task_text="New focus task", tag="focus", page_num=0),
        CheckboxItem(task_text="New orbit task", tag="orbit", page_num=0)
    ]
    read_result = ReadResult(
        notebook="Quick",
        page_num=0,
        text="□ New focus task\n○ New orbit task",
        checkboxes=checkbox_items,
        content_type="task",
        timestamp=datetime.now(timezone.utc)
    )
    mock_reader.process_file = AsyncMock(return_value=[read_result])

    with patch.object(curator._linear, "execute", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"status": "ok", "issue": {"identifier": "LFW-1"}}

        await curator._process_new_tasks("Quick.note")

        assert mock_execute.call_count == 2
        mock_execute.assert_any_call(
            "create_issue",
            title="New focus task",
            team_key="LFW",
            description="Created from Supernote Quick.note",
        )
        mock_execute.assert_any_call(
            "create_issue",
            title="New orbit task",
            team_key="LFW",
            description="Created from Supernote Quick.note",
        )


@pytest.mark.asyncio
async def test_task_curator_emits_checkbox_completed_events():
    """Task curator emits checkbox_completed events when boxes are newly checked."""
    # Arrange
    mock_reader = AsyncMock()
    mock_events_client = Mock()
    curator = TaskCurator(reader=mock_reader, events_client=mock_events_client)

    # Mock reader to return newly checked items
    from paia_supernote.reader import ReadResult, CheckboxItem
    checkbox_items = [
        CheckboxItem(task_text="Completed focus task", tag="focus", page_num=0),
    ]
    read_result = ReadResult(
        notebook="Quick",
        page_num=0,
        text="☑ Completed focus task",
        checkboxes=checkbox_items,
        content_type="task",
        timestamp=datetime.now(timezone.utc)
    )
    mock_reader.process_file = AsyncMock(return_value=[read_result])

    mock_events_client.publish_checkbox_completed = AsyncMock()

    # Act
    await curator._process_checkbox_completions("Quick.note")

    # Assert
    mock_events_client.publish_checkbox_completed.assert_called_once_with(
        task_text="Completed focus task",
        notebook="Quick",
        page=0
    )


@pytest.mark.asyncio
async def test_task_curator_aggregates_content_from_task_pages_18_to_21():
    """Curator joins text from all four task pages (0-based 18-21) in a single string.

    Regression: old implementation returned only the first task page; pages 20-21
    were silently dropped.
    """
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    from paia_supernote.reader import ReadResult
    mock_reader.read_all_pages = AsyncMock(return_value=[
        ReadResult(notebook="Quick", page_num=18, text="Page 19 tasks",
                   checkboxes=[], content_type="task",
                   timestamp=datetime.now(timezone.utc)),
        ReadResult(notebook="Quick", page_num=19, text="Page 20 tasks",
                   checkboxes=[], content_type="task",
                   timestamp=datetime.now(timezone.utc)),
        ReadResult(notebook="Quick", page_num=20, text="Page 21 tasks",
                   checkboxes=[], content_type="task",
                   timestamp=datetime.now(timezone.utc)),
        ReadResult(notebook="Quick", page_num=21, text="Page 22 tasks",
                   checkboxes=[], content_type="task",
                   timestamp=datetime.now(timezone.utc)),
    ])

    result = await curator._read_current_task_page(b"note_bytes", "Quick")

    # All four pages must be present in the aggregated output
    assert "Page 19 tasks" in result
    assert "Page 20 tasks" in result
    assert "Page 21 tasks" in result
    assert "Page 22 tasks" in result
    # Joined with newlines
    assert result.count("\n") == 3


@pytest.mark.asyncio
async def test_task_curator_keeps_designated_task_pages_even_when_not_classified_as_task():
    """Task-page curation should use the designated page span, not task-marker classification.

    Real Quick.note pages 19-22 can contain task-planning prose with weak or missing checkbox OCR.
    Those pages still need to be curated even if classify_content returns "general".
    """
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    from paia_supernote.reader import ReadResult
    mock_reader.read_all_pages = AsyncMock(return_value=[
        ReadResult(
            notebook="Quick",
            page_num=18,
            text="Fundraising mode notes",
            checkboxes=[],
            content_type="general",
            timestamp=datetime.now(timezone.utc),
        ),
        ReadResult(
            notebook="Quick",
            page_num=19,
            text="Prototype dashboard planning",
            checkboxes=[],
            content_type="general",
            timestamp=datetime.now(timezone.utc),
        ),
    ])

    result = await curator._read_current_task_page(b"note_bytes", "Quick")

    assert "Fundraising mode notes" in result
    assert "Prototype dashboard planning" in result


@pytest.mark.asyncio
async def test_task_curator_reorganize_with_llm_uses_zai_backend():
    """Task curation rewrite uses the approved Z.AI text model when configured."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Rewritten task page"}}]
    }
    mock_response.raise_for_status = Mock()

    with patch("httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        curator = TaskCurator(
            rewrite_backend="zai",
            zai_api_key="test-zai-key",
            zai_text_model="glm-5.1",
            linear_api_key="lin_api_test",
        )

        result = await curator._reorganize_with_llm("Current task page content")

        assert result == "Rewritten task page"
        _, kwargs = mock_async_client.return_value.__aenter__.return_value.post.await_args
        assert kwargs["headers"]["Authorization"] == "Bearer test-zai-key"
        assert kwargs["json"]["model"] == "glm-5.1"


@pytest.mark.asyncio
async def test_task_curator_fetches_linear_tasks_for_reorganization():
    """Curation should fetch open Linear tasks and pass them to the LLM."""
    curator = TaskCurator(linear_api_key="lin_api_test")

    mock_issues = [
        {"identifier": "LFW-1", "title": "Fix auth timeout", "state": {"name": "In Progress"}},
        {"identifier": "LFW-2", "title": "Deploy dashboard", "state": {"name": "Todo"}},
    ]

    with patch.object(curator._linear, "execute", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"status": "ok", "issues": mock_issues}

        result = await curator._fetch_linear_tasks()

        assert len(result) == 2
        assert result[0]["identifier"] == "LFW-1"


@pytest.mark.asyncio
async def test_task_curator_reorganize_includes_linear_tasks_in_prompt():
    """Curation prompt should include open Linear tasks."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Rewritten with tasks"}}]
    }
    mock_response.raise_for_status = Mock()

    with patch("httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        curator = TaskCurator(
            rewrite_backend="zai",
            zai_api_key="test-zai-key",
            linear_api_key="lin_api_test",
        )

        linear_tasks = [
            {"identifier": "LFW-1", "title": "Fix auth timeout", "state": {"name": "In Progress"}},
        ]

        result = await curator._reorganize_with_llm("Current task page content", linear_tasks)

        assert result == "Rewritten with tasks"
        _, kwargs = mock_async_client.return_value.__aenter__.return_value.post.await_args
        prompt = kwargs["json"]["messages"][1]["content"]
        assert "LFW-1" in prompt
        assert "Fix auth timeout" in prompt
