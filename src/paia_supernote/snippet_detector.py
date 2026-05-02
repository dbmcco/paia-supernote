"""
ABOUTME: Snippet detector for LFW.note and Synth.note strategy fragments
ABOUTME: Classifies transcribed text and routes snippets to Caroline or Ingrid via paia-events
"""

from __future__ import annotations

from typing import Optional

import anthropic
import structlog

from .events import EventsClient
from .model_config import default_anthropic_model

log = structlog.get_logger(__name__)

# Notebook → agent routing
_NOTEBOOK_AGENT_MAP: dict[str, str] = {
    "LFW": "caroline",
    "Synth": "ingrid",
}

# Heuristic task markers — content with these is always "task", skip LLM
_TASK_MARKERS = ["□", "○", "☑", "●", "■"]


class SnippetDetector:
    """Evaluates transcribed notebook content for strategic snippets.

    Triggered after watcher fires on LFW.note or Synth.note change
    and reader transcribes the page. This module classifies the text
    and emits supernote.snippet_detected for the routed agent.
    """

    def __init__(
        self,
        events_client: EventsClient,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
    ) -> None:
        self._events = events_client
        self._client = anthropic_client
        self._model = default_anthropic_model()

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def evaluate(
        self, notebook: str, page: int, text: str
    ) -> Optional[str]:
        """Classify transcribed text and emit snippet event if warranted.

        Returns the classification ("snippet", "task", "general") or None
        if the notebook has no routing target.
        """
        agent = _NOTEBOOK_AGENT_MAP.get(notebook)
        if agent is None:
            log.debug("snippet_detector_skip", notebook=notebook, reason="no routing target")
            return None

        # Fast-path: task markers → "task", no LLM needed
        if any(marker in text for marker in _TASK_MARKERS):
            log.info("snippet_detector_task", notebook=notebook, page=page)
            return "task"

        # LLM classification
        classification = await self._classify(text)
        log.info(
            "snippet_detector_result",
            notebook=notebook,
            page=page,
            classification=classification,
            agent=agent,
        )

        if classification == "snippet":
            await self._events.publish_snippet_detected(
                notebook=notebook, page=page, text=text, agent=agent
            )

        return classification

    async def _classify(self, text: str) -> str:
        """Use Claude to classify text as snippet or general."""
        try:
            response = await self.client.messages.create(
                model=self._model,
                max_tokens=16,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Classify this handwritten note. Reply with exactly one word:\n"
                            "- 'snippet' if it contains an incomplete strategic thought, "
                            "a named client or project with an open question, "
                            "meeting prep that lacks context, or something relevant "
                            "to an active workstream\n"
                            "- 'general' if it is a fully formed note, general journaling, "
                            "or does not warrant follow-up\n\n"
                            f"Note:\n{text}"
                        ),
                    }
                ],
            )
            result = response.content[0].text.strip().lower()
            if result in ("snippet", "general"):
                return result
        except Exception as exc:
            log.warning("snippet_classify_error", error=str(exc))

        return "general"
