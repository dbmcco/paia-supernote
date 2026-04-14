"""Tests for task curator module."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone

from paia_supernote.task_curator import TaskCurator


@pytest.mark.asyncio
async def test_task_curator_reads_current_task_page_from_note_file():
    """Task curator reads current Quick.note file to get task page content."""
    # Arrange
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    # Mock the reader to return ReadResult with task content
    from paia_supernote.reader import ReadResult, CheckboxItem
    read_result = ReadResult(
        notebook="Quick",
        page_num=0,
        text="□ Task 1\n○ Task 2\n☑ Completed task",
        checkboxes=[],
        content_type="task",
        timestamp=datetime.now(timezone.utc)
    )
    mock_reader.process_file = AsyncMock(return_value=[read_result])

    # Act
    result = await curator._read_current_task_page("Quick.note")

    # Assert
    assert result == "□ Task 1\n○ Task 2\n☑ Completed task"
    mock_reader.process_file.assert_called_once()


@pytest.mark.asyncio
async def test_task_curator_creates_paia_work_tasks_for_new_checkboxes():
    """Task curator creates paia-work tasks when new handwritten □/○ items are detected."""
    # Arrange
    mock_reader = AsyncMock()
    curator = TaskCurator(reader=mock_reader)

    # Mock reader to return new checkbox items
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

    # Mock paia-work API
    with patch('httpx.AsyncClient') as mock_client:
        mock_response = Mock()
        mock_response.status_code = 201
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

        # Act
        await curator._process_new_tasks("Quick.note")

        # Assert - should create 2 tasks via paia-work API
        assert mock_client.return_value.__aenter__.return_value.post.call_count == 2


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