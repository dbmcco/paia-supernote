"""
ABOUTME: Supernote file reader and content processor module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Processes changed .note files, extracts content via vision, and classifies content types
"""

import base64
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

import httpx
import supernotelib
from supernotelib.converter import ImageConverter
import anthropic


SNAPSHOT_DIR = Path.home() / ".paia" / "supernote" / "snapshots"


@dataclass
class CheckboxItem:
    task_text: str
    tag: str  # "focus" or "orbit"
    page_num: int


@dataclass
class ReadResult:
    notebook: str
    page_num: int
    text: str
    checkboxes: List[CheckboxItem]
    content_type: str  # "task", "snippet", or "general"
    timestamp: datetime


class SupernoteReader:
    """Processes .note files to extract and classify content."""

    def __init__(
        self,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
        vision_backend: str = "anthropic",
        ollama_model: str = "gemma4:31b",
        ollama_url: str = "http://localhost:11434",
    ):
        """Initialize the reader.

        Args:
            anthropic_client: Optional pre-built Anthropic client (for testing).
            vision_backend: "anthropic" or "ollama".
            ollama_model: Model name to use when vision_backend="ollama".
            ollama_url: Base URL for the Ollama API.
        """
        self.page_checksums: Dict[str, str] = {}
        self._client = anthropic_client
        self.vision_backend = vision_backend
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url.rstrip("/")

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Get or create the anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def process_file(
        self, note_source: Union[str, Path, bytes], notebook_name: Optional[str] = None
    ) -> List[ReadResult]:
        """Process a .note file and extract content.

        Args:
            note_source: Path to a local .note file (str or Path), or raw .note bytes
                         downloaded from cloud.
            notebook_name: Override for the notebook name. Required when note_source
                           is bytes. When note_source is a path, defaults to the stem.
        """
        if isinstance(note_source, bytes):
            if notebook_name is None:
                raise ValueError("notebook_name is required when note_source is bytes")
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
            try:
                os.write(tmp_fd, note_source)
                os.close(tmp_fd)
                return await self._process_path(tmp_path, notebook_name)
            finally:
                os.unlink(tmp_path)
        else:
            path = Path(note_source)
            name = notebook_name or path.stem
            return await self._process_path(str(path), name)

    async def _process_path(self, file_path: str, notebook_name: str) -> List[ReadResult]:
        """Internal: load notebook from path and process all pages."""
        notebook = supernotelib.load_notebook(file_path)
        converter = ImageConverter(notebook)

        results = []
        for page_num in range(notebook.get_total_pages()):
            page_image = converter.convert(page_num)

            # Use notebook_name as the stable key (file_path may be a temp path)
            if not self.page_changed(notebook_name, page_num, page_image):
                continue

            transcription = await self._transcribe_page(page_image)
            if not transcription:
                continue

            content_type = await self.classify_content(transcription)
            newly_checked = self.detect_checkbox_changes(
                notebook_name, page_num, transcription
            )

            result = ReadResult(
                notebook=notebook_name,
                page_num=page_num,
                text=transcription,
                checkboxes=newly_checked,
                content_type=content_type,
                timestamp=datetime.now(timezone.utc),
            )
            results.append(result)

        return results

    async def _transcribe_page(self, page_image) -> Optional[str]:
        """Transcribe page content using the configured vision backend."""
        img_buffer = BytesIO()
        page_image.save(img_buffer, format="PNG")
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode()

        if self.vision_backend == "ollama":
            return await self._transcribe_page_ollama(img_b64)
        return await self._transcribe_page_anthropic(img_b64)

    _TRANSCRIBE_PROMPT = (
        "Transcribe this handwritten note page exactly. "
        "Preserve checkbox markers (□ for unchecked, ☑ for checked) "
        "and circle markers (○ for unchecked, ● for checked). "
        "Return only the transcribed text, no commentary."
    )

    async def _transcribe_page_anthropic(self, img_b64: str) -> Optional[str]:
        """Transcribe via Anthropic Claude vision API."""
        response = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": self._TRANSCRIBE_PROMPT},
                    ],
                }
            ],
        )
        return response.content[0].text

    async def _transcribe_page_ollama(self, img_b64: str) -> Optional[str]:
        """Transcribe via local Ollama vision model."""
        payload = {
            "model": self.ollama_model,
            "messages": [
                {
                    "role": "user",
                    "content": self._TRANSCRIBE_PROMPT,
                    "images": [img_b64],
                }
            ],
            "stream": False,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=600.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]

    def detect_checkbox_changes(
        self, notebook_name: str, page_num: int, text: str
    ) -> List[CheckboxItem]:
        """Detect checkbox changes between current and prior snapshot."""
        snapshot_path = SNAPSHOT_DIR / f"{notebook_name}_page_{page_num}.json"

        # Extract currently checked items with their tags
        current_checked: List[Dict[str, str]] = []
        for match in re.finditer(r"[☑■]\s*(.+)", text):
            current_checked.append(
                {"task_text": match.group(1).strip(), "tag": "focus"}
            )
        for match in re.finditer(r"[●]\s*(.+)", text):
            current_checked.append(
                {"task_text": match.group(1).strip(), "tag": "orbit"}
            )

        # Load previous snapshot
        previous_checked_texts: set = set()
        if snapshot_path.exists():
            prior = json.loads(snapshot_path.read_text())
            previous_checked_texts = {item["task_text"] for item in prior}

        # Find newly checked items
        newly_checked = [
            CheckboxItem(
                task_text=item["task_text"], tag=item["tag"], page_num=page_num
            )
            for item in current_checked
            if item["task_text"] not in previous_checked_texts
        ]

        # Persist current snapshot
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(current_checked, indent=2))

        return newly_checked

    async def classify_content(self, text: str) -> str:
        """Classify content type: task, snippet, or general."""
        checkbox_markers = ["□", "○", "☑", "●", "■"]
        if any(marker in text for marker in checkbox_markers):
            return "task"

        # Use LLM to detect strategy snippets
        try:
            response = await self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Classify this handwritten note. Reply with exactly one word:\n"
                            "- 'snippet' if it contains a strategy fragment, client mention, "
                            "open question, or incomplete strategic thought\n"
                            "- 'general' otherwise\n\n"
                            f"Note:\n{text}"
                        ),
                    }
                ],
            )
            classification = response.content[0].text.strip().lower()
            if classification == "snippet":
                return "snippet"
        except Exception:
            pass

        return "general"

    def page_changed(self, notebook_name: str, page_num: int, page_image) -> bool:
        """Check if a page has changed by comparing MD5 checksums."""
        img_buffer = BytesIO()
        page_image.save(img_buffer, format="PNG")
        current_checksum = hashlib.md5(img_buffer.getvalue()).hexdigest()

        key = f"{notebook_name}:{page_num}"
        previous_checksum = self.page_checksums.get(key)

        self.page_checksums[key] = current_checksum
        return previous_checksum is None or current_checksum != previous_checksum
