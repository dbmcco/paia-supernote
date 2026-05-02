"""
ABOUTME: Task page curator module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Sam's intelligent task page curation for Quick.note
"""

from __future__ import annotations
import os
import tempfile
from typing import Any, Dict, Optional

import anthropic
import httpx

from paia_agent_runtime.tools.linear import LinearTool

from . import events, reader, uploader, writer
from .model_config import default_anthropic_model, default_zai_base_url, default_zai_text_model
from .notebook_writer import append_page_to_notebook


class TaskCurator:
    """Handles intelligent curation of task pages in Quick.note."""

    def __init__(
        self,
        reader: Optional[reader.SupernoteReader] = None,
        writer: Optional[writer.SupernoteWriter] = None,
        uploader: Optional[uploader.SupernoteUploader] = None,
        events_client: Optional[events.EventsClient] = None,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
        rewrite_backend: str = "anthropic",
        zai_api_key: Optional[str] = None,
        zai_base_url: str | None = None,
        zai_text_model: str | None = None,
        linear_api_key: Optional[str] = None,
        linear_team_key: str = "LFW",
        linear_team_id: Optional[str] = None,
    ):
        """Initialize the task curator with dependencies."""
        self.reader = reader
        self.writer = writer
        self.uploader = uploader
        self.events_client = events_client
        self.anthropic_client = anthropic_client
        self.rewrite_backend = rewrite_backend
        self.zai_api_key = zai_api_key or os.environ.get("ZAI_API_KEY")
        self.zai_base_url = (zai_base_url or default_zai_base_url()).rstrip("/")
        self.zai_text_model = zai_text_model or default_zai_text_model()
        self.anthropic_model = default_anthropic_model()
        self._linear = LinearTool(
            api_key=linear_api_key or "",
            team_id=linear_team_id,
        )
        self._linear_team_key = linear_team_key

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client."""
        if self.anthropic_client is None:
            self.anthropic_client = anthropic.AsyncAnthropic()
        return self.anthropic_client

    async def handle_write_requested(self, payload: Dict[str, Any]) -> None:
        """Handle supernote.write_requested events with content_type='task_page_curate'."""
        if payload.get("content_type") != "task_page_curate":
            return

        notebook_name = payload["notebook"]
        notebook_bytes = payload["notebook_bytes"]
        agent = payload["agent"]

        # Read current task page state
        current_page_text = await self._read_current_task_page(notebook_bytes, notebook_name)

        # Fetch open Linear tasks for the team
        linear_tasks = await self._fetch_linear_tasks()

        # Send to LLM for reorganization
        reorganized_content = await self._reorganize_with_llm(current_page_text, linear_tasks)

        # Render the reorganized page
        rendered_bytes = self.writer.render_page(
            agent=agent,
            content=reorganized_content
        )

        # Replace the task pages in the notebook
        updated_bytes = await self._replace_task_pages(notebook_bytes, rendered_bytes, notebook_name)

        # Upload to Supernote Cloud
        with tempfile.NamedTemporaryFile(
            suffix=".note", delete=False, dir="/tmp"
        ) as tmp:
            tmp.write(updated_bytes)
            tmp_path = tmp.name

        try:
            success = await self.uploader.upload_notebook(tmp_path, f"{notebook_name}.note")
        finally:
            os.unlink(tmp_path)

        # Emit note_transcribed event
        await self.events_client.publish_note_transcribed(
            notebook=notebook_name,
            page=21,
            text=reorganized_content
        )

    async def _read_current_task_page(self, notebook_bytes: bytes, notebook_name: str) -> str:
        """Read current task page content from the notebook bytes.

        tasks.note: pages 0-3 (up to 4 lane pages, read what's available)
        Quick.note: pages 18-21 (task pages)
        """
        if notebook_name == "tasks":
            page_range = (0, 3)
        else:
            page_range = (18, 21)

        try:
            results = await self.reader.read_all_pages(
                notebook_bytes, notebook_name, page_range=page_range
            )
        except Exception:
            # If page_range is out of bounds (e.g. tasks.note has fewer pages),
            # fall back to reading all available pages
            results = await self.reader.read_all_pages(
                notebook_bytes, notebook_name, page_range=None
            )

        aggregated_content = []
        for result in results:
            if result.text.strip():
                aggregated_content.append(result.text.strip())

        return "\n".join(aggregated_content)

    async def _process_new_tasks(self, notebook: str) -> None:
        """Process new handwritten tasks and create Linear issues."""
        file_path = f"~/Supernote/{notebook}"
        results = await self.reader.process_file(file_path)

        for result in results:
            for checkbox_item in result.checkboxes:
                await self._linear.execute(
                    "create_issue",
                    title=checkbox_item.task_text,
                    team_key=self._linear_team_key,
                    description=f"Created from Supernote {notebook}",
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

    async def _fetch_linear_tasks(self) -> list[dict[str, Any]]:
        """Fetch open Linear issues for the team."""
        result = await self._linear.execute(
            "list_issues",
            team_key=self._linear_team_key,
            limit=50,
        )
        if result.get("status") != "ok":
            return []
        return result.get("issues", [])

    async def _replace_task_pages(
        self, notebook_bytes: bytes, rendered_page: bytes, notebook_name: str
    ) -> bytes:
        """Replace task pages in the notebook with the new curated page.

        tasks.note: replace page 0, clear pages 1-3
        Quick.note: replace page 18
        """
        import supernotelib.parser as sn_parser
        import supernotelib.manipulator as sn_manip
        from .notebook_writer import clear_recognition_metadata

        fd, path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(fd, notebook_bytes)
            os.close(fd)
            nb = sn_parser.load_notebook(path)

            n_existing = nb.get_total_pages()

            if notebook_name == "tasks":
                # tasks.note: replace page 0, trim to 1 page
                if n_existing > 0:
                    page = nb.get_page(0)
                    clear_recognition_metadata(page)
                    if page.is_layer_supported():
                        page.get_layer(0).set_content(rendered_page)
                        layers = page.get_layers()
                        for j in range(1, len(layers)):
                            layer = layers[j]
                            name = layer.get_name()
                            if name and name != "BGLAYER":
                                layer.set_content(b"")
                # Trim to exactly 1 page
                if n_existing > 1:
                    nb.pages = nb.pages[:1]
                    if hasattr(nb.metadata, "pages"):
                        nb.metadata.pages = nb.metadata.pages[:1]
            else:
                # Quick.note: replace page 18
                TASK_PAGE_START = 18
                if TASK_PAGE_START < n_existing:
                    page = nb.get_page(TASK_PAGE_START)
                    clear_recognition_metadata(page)
                    if page.is_layer_supported():
                        page.get_layer(0).set_content(rendered_page)
                        layers = page.get_layers()
                        for j in range(1, len(layers)):
                            layer = layers[j]
                            name = layer.get_name()
                            if name and name != "BGLAYER":
                                layer.set_content(b"")

            notebook_bytes = sn_manip.reconstruct(nb)
        finally:
            os.unlink(path)

        return notebook_bytes

    async def _reorganize_with_llm(self, current_text: str, linear_tasks: list[dict[str, Any]] | None = None) -> str:
        """Send task page to LLM for intelligent reorganization."""
        if self.rewrite_backend == "zai":
            return await self._reorganize_with_zai(current_text, linear_tasks)

        tasks_context = ""
        if linear_tasks:
            tasks_text = "\n".join(
                f"- [{task.get('identifier', '?')}] {task.get('title', '')} ({task.get('state', {}).get('name', 'open')})"
                for task in linear_tasks
            )
            tasks_context = f"\n\nOpen Linear tasks from LFW team:\n{tasks_text}"

        response = await self.client.messages.create(
            model=self.anthropic_model,
            max_tokens=2048,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are curating Braydon's task page. Reorganize for clarity: "
                        "group by focus/orbit, move completed items to bottom, then append "
                        "open Linear tasks that are NOT already on the page (match by identifier). "
                        "You have agency to reorganize. Output the full rewritten page content."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Current task page content:{tasks_context}\n\n{current_text}"
                }
            ],
        )
        return response.content[0].text

    async def _reorganize_with_zai(self, current_text: str, linear_tasks: list[dict[str, Any]] | None = None) -> str:
        """Rewrite task page content using the Z.AI coding endpoint."""
        if not self.zai_api_key:
            raise RuntimeError("ZAI_API_KEY is required when using the zai rewrite backend")

        tasks_context = ""
        if linear_tasks:
            tasks_text = "\n".join(
                f"- [{task.get('identifier', '?')}] {task.get('title', '')} ({task.get('state', {}).get('name', 'open')})"
                for task in linear_tasks
            )
            tasks_context = f"\n\nOpen Linear tasks from LFW team:\n{tasks_text}"

        payload = {
            "model": self.zai_text_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are curating Braydon's task page. Reorganize for clarity: "
                        "group by focus/orbit, move completed items to bottom, then append "
                        "open Linear tasks that are NOT already on the page (match by identifier). "
                        "You have agency to reorganize. Output the full rewritten page content."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Current task page content:{tasks_context}\n\n{current_text}",
                },
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "stream": False,
            "max_tokens": 2048,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.zai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.zai_api_key}"},
                json=payload,
                timeout=600.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
