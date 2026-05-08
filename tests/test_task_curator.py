"""Tests for task curator module."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from paia_supernote.reader import CheckboxItem, ReadResult
from paia_supernote.task_curator import TaskCurator


@pytest.mark.asyncio
async def test_task_curator_reads_current_task_pages_from_note_bytes():
    """Task curator reads current Quick.note file to get task page content."""
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    read_result = ReadResult(
        notebook="Quick",
        page_num=18,
        text="□ Task 1\n○ Task 2\n☑ Completed task",
        checkboxes=[],
        content_type="task",
        timestamp=datetime.now(timezone.utc),
    )
    mock_reader.read_all_pages = AsyncMock(return_value=[read_result])

    result = await curator._read_current_task_page(b"note-bytes", "Quick")

    assert result == "□ Task 1\n○ Task 2\n☑ Completed task"
    mock_reader.read_all_pages.assert_awaited_once_with(
        b"note-bytes",
        "Quick",
        page_range=(18, 21),
    )


@pytest.mark.asyncio
async def test_task_curator_creates_paia_work_tasks_for_new_checkboxes():
    """Task curator creates paia-work tasks for new handwritten items."""
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    checkbox_items = [
        CheckboxItem(task_text="New focus task", tag="focus", page_num=0),
        CheckboxItem(task_text="New orbit task", tag="orbit", page_num=0),
    ]
    read_result = ReadResult(
        notebook="Quick",
        page_num=0,
        text="□ New focus task\n○ New orbit task",
        checkboxes=checkbox_items,
        content_type="task",
        timestamp=datetime.now(timezone.utc),
    )
    mock_reader.process_file = AsyncMock(return_value=[read_result])
    curator._linear.execute = AsyncMock(return_value={"status": "ok"})

    await curator._process_new_tasks("Quick.note")

    assert curator._linear.execute.await_count == 2
    curator._linear.execute.assert_any_await(
        "create_issue",
        title="New focus task",
        team_key="LFW",
        description="Created from Supernote Quick.note",
    )
    curator._linear.execute.assert_any_await(
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
