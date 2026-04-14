"""
ABOUTME: Task page curator module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Sam's intelligent task page curation for Quick.note
"""

from typing import Any, Dict, Optional
import anthropic
import httpx
from . import reader, writer, uploader, events


class TaskCurator:
    """Handles intelligent curation of task pages in Quick.note."""

    def __init__(
        self,
        reader: Optional[reader.SupernoteReader] = None,
        writer: Optional[writer.SupernoteWriter] = None,
        uploader: Optional[uploader.SupernoteUploader] = None,
        events_client: Optional[events.EventsClient] = None,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
    ):
        """Initialize the task curator with dependencies."""
        self.reader = reader
        self.writer = writer
        self.uploader = uploader
        self.events_client = events_client
        self.anthropic_client = anthropic_client

    async def handle_write_requested(self, payload: Dict[str, Any]) -> None:
        """Handle supernote.write_requested events with content_type='task_page_curate'."""
        if payload.get("content_type") != "task_page_curate":
            return

        # Read current task page state
        current_page_text = await self._read_current_task_page(payload["notebook"])

        # Send to LLM for reorganization
        reorganized_content = await self._reorganize_with_llm(current_page_text)

        # Render the reorganized page
        rendered_bytes = self.writer.render_page(
            agent=payload["agent"],
            content=reorganized_content
        )

        # Upload to Supernote Cloud
        await self.uploader.upload_notebook("temp_path", "target_path")

        # Emit note_transcribed event
        await self.events_client.publish_note_transcribed(
            notebook=payload["notebook"],
            page=1,  # TODO: determine actual page number
            text=reorganized_content
        )

    async def _read_current_task_page(self, notebook: str) -> str:
        """Read current task page content from the notebook file."""
        # TODO: Map notebook name to actual file path
        file_path = f"~/Supernote/{notebook}"
        results = await self.reader.process_file(file_path)

        # Find the task page (assuming it's the first or only page with task content)
        for result in results:
            if result.content_type == "task":
                return result.text

        # If no task page found, return empty content
        return ""

    async def _process_new_tasks(self, notebook: str) -> None:
        """Process new handwritten tasks and create paia-work entries."""
        file_path = f"~/Supernote/{notebook}"
        results = await self.reader.process_file(file_path)

        for result in results:
            for checkbox_item in result.checkboxes:
                # Create paia-work task for new checkbox items
                task_data = {
                    "text": checkbox_item.task_text,
                    "label": checkbox_item.tag,
                    "source": "supernote"
                }

                async with httpx.AsyncClient() as client:
                    await client.post(
                        "http://localhost:3512/v1/tasks",  # paia-work API
                        json=task_data
                    )

    async def _process_checkbox_completions(self, notebook: str) -> None:
        """Emit checkbox_completed events for checked items in the notebook."""
        file_path = f"~/Supernote/{notebook}"
        results = await self.reader.process_file(file_path)

        for result in results:
            for checkbox_item in result.checkboxes:
                await self.events_client.publish_checkbox_completed(
                    task_text=checkbox_item.task_text,
                    notebook=result.notebook,
                    page=result.page_num,
                )

    async def _reorganize_with_llm(self, current_text: str) -> str:
        """Send task page to LLM for intelligent reorganization."""
        response = await self.anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are curating Braydon's task page. Reorganize for clarity: "
                        "group by focus/orbit, move completed items to bottom, insert new "
                        "paia-work tasks where they fit naturally. You have agency to "
                        "reorganize. Output the full rewritten page content."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Current task page content:\n{current_text}"
                }
            ],
        )
        return response.content[0].text