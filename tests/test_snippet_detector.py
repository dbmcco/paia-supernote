"""Tests for snippet detector module."""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from paia_supernote.snippet_detector import SnippetDetector


# --- Notebook-to-agent routing ---


@pytest.mark.asyncio
async def test_lfw_note_routes_to_caroline():
    """LFW.note changes route to Caroline."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = Mock(
        content=[Mock(text="snippet")]
    )

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    await detector.evaluate(
        notebook="LFW", page=3, text="Aberdeen positioning — need to rethink the angle before Thursday call"
    )

    mock_events.publish_snippet_detected.assert_called_once_with(
        notebook="LFW",
        page=3,
        text="Aberdeen positioning — need to rethink the angle before Thursday call",
        agent="caroline",
    )


@pytest.mark.asyncio
async def test_synth_note_routes_to_ingrid():
    """Synth.note changes route to Ingrid."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = Mock(
        content=[Mock(text="snippet")]
    )

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    await detector.evaluate(
        notebook="Synth", page=1, text="What if we combined the modular pipeline with the existing ingest?"
    )

    mock_events.publish_snippet_detected.assert_called_once_with(
        notebook="Synth",
        page=1,
        text="What if we combined the modular pipeline with the existing ingest?",
        agent="ingrid",
    )


# --- Classification: task content does NOT trigger snippet ---


@pytest.mark.asyncio
async def test_task_content_classified_as_task_not_snippet():
    """Content with □ markers is classified as 'task', not 'snippet'. No event emitted."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    result = await detector.evaluate(
        notebook="LFW", page=0, text="□ Follow up with client\n□ Prepare deck for Friday"
    )

    assert result == "task"
    mock_events.publish_snippet_detected.assert_not_called()
    # Task detection is heuristic — no LLM call needed
    mock_anthropic.messages.create.assert_not_called()


# --- Classification: strategy fragment triggers snippet ---


@pytest.mark.asyncio
async def test_strategy_fragment_classified_as_snippet():
    """Strategy fragment is classified as 'snippet' by Claude."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = Mock(
        content=[Mock(text="snippet")]
    )

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    result = await detector.evaluate(
        notebook="LFW",
        page=2,
        text="Aberdeen deal — unclear whether they want managed or self-serve. Need to figure out before proposal.",
    )

    assert result == "snippet"
    mock_events.publish_snippet_detected.assert_called_once()


# --- Classification: general note does NOT trigger snippet ---


@pytest.mark.asyncio
async def test_general_note_no_event_emitted():
    """General note classified as 'general' — no snippet event emitted."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = Mock(
        content=[Mock(text="general")]
    )

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    result = await detector.evaluate(
        notebook="LFW", page=5, text="Beautiful day today. Took the dog for a walk."
    )

    assert result == "general"
    mock_events.publish_snippet_detected.assert_not_called()


# --- Edge: unknown notebook does not crash ---


@pytest.mark.asyncio
async def test_unknown_notebook_returns_none_no_event():
    """Unknown notebook (not LFW or Synth) returns None — no routing target."""
    mock_events = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = Mock(
        content=[Mock(text="snippet")]
    )

    detector = SnippetDetector(
        events_client=mock_events, anthropic_client=mock_anthropic
    )

    result = await detector.evaluate(
        notebook="Quick", page=0, text="Some strategic thought here"
    )

    assert result is None
    mock_events.publish_snippet_detected.assert_not_called()
