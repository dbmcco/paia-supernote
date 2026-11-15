"""
ABOUTME: Supernote file reader and content processor module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Reads changed .note files and extracts structured content with vision.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import anthropic
import httpx
import supernotelib
from supernotelib.converter import ImageConverter

from . import blank_cap
from .model_config import (
    default_anthropic_model,
    default_zai_base_url,
    default_zai_text_model,
    default_zai_vision_model,
    resolve_supernote_zai_api_key,
)
from .note_snapshot import NotebookSnapshot, build_snapshot_from_notebook
from .ocr_contract import (
    TRANSCRIPTION_PROMPT,
    escalate_if_needed,
    needs_escalation,
)

SNAPSHOT_DIR = Path.home() / ".paia" / "supernote" / "snapshots"

logger = logging.getLogger(__name__)

# ollama 0.35.x ignores think:false / /no_think / chat_template_kwargs for
# qwen3-vl; the only verified way to keep the model out of its degenerate
# thinking loop (~377s/page, empty output) is an assistant prefill closing
# the think block (~8-23s/page, verified on all 24 eval-corpus pages
# 2026-10-06). Same pattern as scripts/ocr_eval.py and grok-aurora-cli.
OLLAMA_THINK_PREFILL = "<think>\n\n</think>\n\n"

# Hosted OCR escalation rides AtlasCloud (owner addendum 2026-10-06): the
# OpenAI API workspace has no credits and pi auth covers pi-side use only.
# Same gpt-5.6-luna model the routing split named; ~$0.004/page (verified
# live 2026-10-06).
DEFAULT_OCR_ESCALATION_BASE_URL = "https://api.atlascloud.ai/v1"
DEFAULT_OCR_ESCALATION_MODEL = "openai/gpt-5.6-luna"

# Lunaroute vision backend (owner ruling 2026-10-28: "we're not using local
# ollama models anymore"). The local paia model-gateway fronts the lunaroute
# surface (OpenAI-compatible /chat/completions). Phase-1 measurement
# (managers/apps/briefs/2026-10-28-vision-off-ollama-result.md): glm-5.3-vision
# OCR'd 3/3 eval-corpus pages clean, 0 [illegible] markers, 11-17.5s/page.
# The gateway token's documented home for agent processes is
# ~/.paia/model-gateway.env (values are never logged).
DEFAULT_LUNAROUTE_VISION_BASE_URL = "http://127.0.0.1:8792/p/lunaroute/v1"
DEFAULT_LUNAROUTE_VISION_MODEL = "glm-5.3-vision"


def _lunaroute_gateway_key() -> Optional[str]:
    """Gateway token: LUNAROUTE_API_KEY env first, then the model-gateway
    env file (~/.paia/model-gateway.env). Values are never logged."""
    if key := os.environ.get("LUNAROUTE_API_KEY"):
        return key
    try:
        env_path = Path.home() / ".paia" / "model-gateway.env"
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            cleaned = value.strip().strip('"').strip("'")
            if name.strip() == "LUNAROUTE_API_KEY" and cleaned:
                return cleaned
    except OSError:
        return None
    return None


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
    page_image: Any | None = None


ReadResultCallback = Callable[[ReadResult], Awaitable[None]]


def build_reader(config: dict) -> "SupernoteReader":
    """Construct a SupernoteReader from a flat config dict.

    Single source of truth for the vision/OCR reader wiring so the CLI, the
    daemon service, and the ingest service cannot diverge. ``config`` is whatever
    ``main.load_config`` returns (a dict with the seven reader keys).
    """
    return SupernoteReader(
        vision_backend=config["vision_backend"],
        ollama_model=config["ollama_model"],
        ollama_url=config["ollama_url"],
        zai_api_key=config["zai_api_key"],
        zai_base_url=config["zai_base_url"],
        zai_vision_model=config["zai_vision_model"],
        zai_text_model=config["zai_text_model"],
        lunaroute_url=config.get("lunaroute_url"),
        lunaroute_vision_model=config.get("lunaroute_vision_model"),
    )


class SupernoteReader:
    """Processes .note files to extract and classify content."""

    def __init__(
        self,
        anthropic_client: Optional[anthropic.AsyncAnthropic] = None,
        vision_backend: str = "anthropic",
        ollama_model: str = "qwen3-vl:8b",
        ollama_url: str = "http://localhost:11434",
        zai_api_key: Optional[str] = None,
        zai_base_url: str | None = None,
        zai_vision_model: str | None = None,
        zai_text_model: str | None = None,
        zai_retry_attempts: int = 4,
        zai_retry_base_delay: float = 60.0,
        lunaroute_url: str | None = None,
        lunaroute_vision_model: str | None = None,
        ocr_escalation_base_url: str | None = None,
        ocr_escalation_model: str | None = None,
    ):
        """Initialize the reader.

        Args:
            anthropic_client: Optional pre-built Anthropic client (for testing).
            vision_backend: "anthropic", "ollama", "zai", or "lunaroute".
            ollama_model: Model name to use when vision_backend="ollama".
            ollama_url: Base URL for the Ollama API.
            lunaroute_url: Base URL for the model-gateway lunaroute surface
                (vision_backend="lunaroute", the default since 2026-10-28).
            lunaroute_vision_model: Vision model name on the gateway surface.
        """
        self.page_checksums: Dict[str, str] = {}
        self._client = anthropic_client
        self.vision_backend = vision_backend
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url.rstrip("/")
        self.lunaroute_url = (
            lunaroute_url
            or os.environ.get("SUPERNOTE_LUNAROUTE_URL")
            or DEFAULT_LUNAROUTE_VISION_BASE_URL
        ).rstrip("/")
        self.lunaroute_vision_model = (
            lunaroute_vision_model
            or os.environ.get("SUPERNOTE_LUNAROUTE_VISION_MODEL")
            or DEFAULT_LUNAROUTE_VISION_MODEL
        )
        self.zai_api_key = zai_api_key or resolve_supernote_zai_api_key()
        self.zai_base_url = (zai_base_url or default_zai_base_url()).rstrip("/")
        self.zai_vision_model = zai_vision_model or default_zai_vision_model()
        self.zai_text_model = zai_text_model or default_zai_text_model()
        self.zai_retry_attempts = max(1, zai_retry_attempts)
        self.zai_retry_base_delay = max(0.0, zai_retry_base_delay)
        self.ocr_escalation_base_url = (
            ocr_escalation_base_url
            or os.environ.get("SUPERNOTE_OCR_ESCALATION_BASE_URL")
            or DEFAULT_OCR_ESCALATION_BASE_URL
        ).rstrip("/")
        self.ocr_escalation_model = (
            ocr_escalation_model
            or os.environ.get("SUPERNOTE_OCR_ESCALATION_MODEL")
            or DEFAULT_OCR_ESCALATION_MODEL
        )
        self.anthropic_model = default_anthropic_model()

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Get or create the anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    def build_snapshot(
        self,
        note_source: Union[str, Path, bytes],
        notebook_name: str,
        revision: str,
    ) -> NotebookSnapshot:
        """Parse a .note source into stable page IDs and content hashes."""
        if isinstance(note_source, bytes):
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
            try:
                os.write(tmp_fd, note_source)
                os.close(tmp_fd)
                notebook = supernotelib.load_notebook(tmp_path)
                return build_snapshot_from_notebook(
                    notebook,
                    notebook_name=notebook_name,
                    revision=revision,
                )
            finally:
                os.unlink(tmp_path)

        path = Path(note_source)
        notebook = supernotelib.load_notebook(str(path))
        return build_snapshot_from_notebook(
            notebook,
            notebook_name=notebook_name,
            revision=revision,
        )

    async def process_file(
        self,
        note_source: Union[str, Path, bytes],
        notebook_name: Optional[str] = None,
        on_result: Optional[ReadResultCallback] = None,
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
                return await self._process_path(
                    tmp_path,
                    notebook_name,
                    on_result=on_result,
                )
            finally:
                os.unlink(tmp_path)
        else:
            path = Path(note_source)
            name = notebook_name or path.stem
            return await self._process_path(str(path), name, on_result=on_result)

    async def _process_path(
        self,
        file_path: str,
        notebook_name: str,
        *,
        on_result: Optional[ReadResultCallback] = None,
    ) -> List[ReadResult]:
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
                page_image=page_image,
            )
            results.append(result)
            if on_result is not None:
                await on_result(result)

        return results

    async def _transcribe_page(self, page_image) -> Optional[str]:
        """Transcribe page content using the configured vision backend."""
        img_b64 = self._page_image_to_b64(page_image)

        if self.vision_backend == "zai":
            return await self._transcribe_page_zai(img_b64)
        if self.vision_backend == "lunaroute":
            return await self._transcribe_page_lunaroute(img_b64)
        if self.vision_backend == "ollama":
            return await self._transcribe_page_ollama_with_escalation(img_b64)
        return await self._transcribe_page_anthropic(img_b64)

    def _page_image_to_b64(self, page_image) -> str:
        img_buffer = BytesIO()
        page_image.save(img_buffer, format="PNG")
        return base64.b64encode(img_buffer.getvalue()).decode()

    async def _transcribe_page_anthropic(self, img_b64: str) -> Optional[str]:
        """Transcribe via Anthropic Claude vision API."""
        response = await self.client.messages.create(
            model=self.anthropic_model,
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
                        {"type": "text", "text": TRANSCRIPTION_PROMPT},
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
                    "content": TRANSCRIPTION_PROMPT,
                    "images": [img_b64],
                },
                # Think-block prefill - see OLLAMA_THINK_PREFILL above.
                {"role": "assistant", "content": OLLAMA_THINK_PREFILL},
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

    async def _transcribe_page_ollama_with_escalation(
        self, img_b64: str
    ) -> Optional[str]:
        """Local-first transcription with exactly-once hosted escalation.

        A weak local result (>= ILLEGIBLE_ESCALATION_THRESHOLD markers, empty
        output, or a local HTTP failure) escalates once to the hosted engine
        through the ocr_contract policy; the better result wins. Runs the
        sync hosted call via asyncio.to_thread so a ~35s hosted page never
        blocks the organizer's event loop.
        """
        try:
            local = await self._transcribe_page_ollama(img_b64) or ""
        except Exception:
            logger.warning(
                "local ollama OCR failed; escalating to hosted", exc_info=True
            )
            local = ""
        if not self._ocr_escalation_api_key():
            if needs_escalation(local):
                logger.warning(
                    "weak local OCR result but hosted escalation is "
                    "unconfigured (set ATLASCLOUD_API_KEY)"
                )
            return local or None
        result = await asyncio.to_thread(
            escalate_if_needed,
            local,
            lambda _local_text: self._transcribe_page_hosted_guarded(img_b64),
        )
        return result or None

    @staticmethod
    def _ocr_escalation_api_key() -> Optional[str]:
        return os.environ.get("ATLASCLOUD_API_KEY") or os.environ.get(
            "GROK_AURORA_ATLASCLOUD_API_KEY"
        )

    def _transcribe_page_hosted_guarded(self, img_b64: str) -> str:
        try:
            return self._transcribe_page_hosted(img_b64)
        except Exception:
            logger.warning(
                "hosted OCR escalation failed; keeping local result",
                exc_info=True,
            )
            return ""

    def _transcribe_page_hosted(self, img_b64: str) -> str:
        """One page through the hosted AtlasCloud vision model (sync)."""
        api_key = self._ocr_escalation_api_key()
        if not api_key:
            raise RuntimeError(
                "OCR escalation requires ATLASCLOUD_API_KEY (or "
                "GROK_AURORA_ATLASCLOUD_API_KEY)"
            )
        payload = {
            "model": self.ocr_escalation_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": TRANSCRIPTION_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            "temperature": 0,
            "max_tokens": 4096,
        }
        # Cloudflare fronts AtlasCloud and 1010-blocks default python UAs
        # on POST; the SDK-style UA is the verified shape (2026-10-06).
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "OpenAI/Python 1.54.4",
        }
        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                f"{self.ocr_escalation_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""

    async def _zai_chat_completion(
        self, model: str, messages: List[Dict[str, Any]], max_tokens: int
    ) -> str:
        """Call the configured OpenAI-compatible Supernote model endpoint."""
        if not self.zai_api_key:
            raise RuntimeError(
                "The configured Supernote model API key is required when using "
                "the zai backend"
            )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_tokens": max_tokens,
        }
        if "openrouter.ai" not in self.zai_base_url:
            payload["thinking"] = {"type": "disabled"}
        async with httpx.AsyncClient() as client:
            for attempt in range(self.zai_retry_attempts):
                response = await client.post(
                    f"{self.zai_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.zai_api_key}"},
                    json=payload,
                    timeout=600.0,
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    is_last_attempt = attempt >= self.zai_retry_attempts - 1
                    if exc.response.status_code != 429 or is_last_attempt:
                        raise
                    await asyncio.sleep(self._zai_retry_delay(exc.response, attempt))
                    continue
                data = response.json()
                return data["choices"][0]["message"]["content"]

        raise RuntimeError("Z.AI request did not return a response")

    def _zai_retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return self.zai_retry_base_delay * (attempt + 1)

    async def _transcribe_page_zai(self, img_b64: str) -> Optional[str]:
        """Transcribe via Z.AI multimodal chat."""
        return await self._zai_chat_completion(
            model=self.zai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": TRANSCRIPTION_PROMPT},
                    ],
                }
            ],
            max_tokens=2048,
        )

    async def _transcribe_page_lunaroute(self, img_b64: str) -> Optional[str]:
        """Transcribe via the local model-gateway vision model (lunaroute
        surface, OpenAI-compatible).

        The default OCR backend since 2026-10-28 (owner ruling: no more local
        Ollama models). Same exact-transcription contract prompt as every
        other backend; token resolves from LUNAROUTE_API_KEY or
        ~/.paia/model-gateway.env and is never logged. No hosted escalation:
        the gateway IS the hosted engine (the AtlasCloud escalation stays
        wired to the ollama backend's weak-result policy)."""
        api_key = _lunaroute_gateway_key()
        if not api_key:
            raise RuntimeError(
                "lunaroute OCR requires LUNAROUTE_API_KEY (env or "
                "~/.paia/model-gateway.env)"
            )
        payload = {
            "model": self.lunaroute_vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": TRANSCRIPTION_PROMPT},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 2048,
            "stream": False,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.lunaroute_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=300.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"] or None

    async def resolve_filing_destination(
        self,
        *,
        page_image: Any | None,
        transcription: str,
        source_notebook: str,
        destination_notebooks: list[str],
    ) -> dict[str, Any]:
        """Ask the configured model to choose the filing destination.

        Code validates the schema and destination set; the semantic decision belongs
        to the model.
        """
        destinations = [
            str(name) for name in destination_notebooks if str(name).strip()
        ]
        prompt = (
            "You are deciding whether a starred Supernote page should be moved to "
            "one of Braydon's target notes.\n\n"
            "The user marks pages by applying the native Supernote star and writing "
            "the target note near that marker. Use the page image and the OCR text as "
            "evidence. Choose move only when there is enough evidence of a target "
            "note intent; otherwise choose needs_review. Do not choose from topic "
            "alone when the destination marker is unclear.\n\n"
            f"Source notebook: {source_notebook}\n"
            f"Allowed target_notebook values: {json.dumps(destinations)}\n"
            f"OCR text:\n{transcription}\n\n"
            "Return exactly this JSON object and no other text:\n"
            '{"action":"move|needs_review",'
            '"target_notebook":"one allowed value or null",'
            '"evidence":"brief evidence","confidence":0.0}'
        )
        raw_response = await self._filing_destination_model_response(
            page_image=page_image,
            prompt=prompt,
        )
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError:
            return {
                "action": "needs_review",
                "target_notebook": None,
                "evidence": "Model did not return valid filing JSON.",
                "confidence": 0.0,
                "raw_response": raw_response,
            }

        if not isinstance(data, dict):
            return {
                "action": "needs_review",
                "target_notebook": None,
                "evidence": "Model returned a non-object filing decision.",
                "confidence": 0.0,
                "raw_response": raw_response,
            }

        action = str(data.get("action") or "needs_review")
        target = data.get("target_notebook")
        if action != "move" or target not in destinations:
            target = None
            action = "needs_review"
        return {
            "action": action,
            "target_notebook": target,
            "evidence": str(data.get("evidence") or "No decision evidence."),
            "confidence": _coerce_confidence(data.get("confidence")),
            "raw_response": raw_response,
        }

    async def _filing_destination_model_response(
        self, *, page_image: Any | None, prompt: str
    ) -> str:
        img_b64 = (
            self._page_image_to_b64(page_image)
            if page_image is not None
            else None
        )
        if self.vision_backend == "zai":
            content: Any
            if img_b64:
                content = [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ]
            else:
                content = prompt
            return await self._zai_chat_completion(
                model=self.zai_vision_model if img_b64 else self.zai_text_model,
                messages=[{"role": "user", "content": content}],
                max_tokens=512,
            )
        if self.vision_backend == "ollama":
            payload: dict[str, Any] = {
                "model": self.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
            if img_b64:
                payload["messages"][0]["images"] = [img_b64]
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=600.0,
                )
                response.raise_for_status()
                data = response.json()
                return data["message"]["content"]

        content_blocks: list[dict[str, Any]] = []
        if img_b64:
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                }
            )
        content_blocks.append({"type": "text", "text": prompt})
        response = await self.client.messages.create(
            model=self.anthropic_model,
            max_tokens=512,
            messages=[{"role": "user", "content": content_blocks}],
        )
        return response.content[0].text

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
            prompt = (
                "Classify this handwritten note. Reply with exactly one word:\n"
                "- 'snippet' if it contains a strategy fragment, client mention, "
                "open question, or incomplete strategic thought\n"
                "- 'general' otherwise\n\n"
                f"Note:\n{text}"
            )
            if self.vision_backend == "zai":
                classification = (
                    await self._zai_chat_completion(
                        model=self.zai_text_model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=16,
                    )
                ).strip().lower()
            else:
                response = await self.client.messages.create(
                    model=self.anthropic_model,
                    max_tokens=16,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
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

    async def read_all_pages(
        self,
        note_bytes: bytes,
        notebook_name: str,
        page_range: Optional[tuple[int, int]] = None,
    ) -> List[ReadResult]:
        """Read all pages from notebook bytes, without checking for changes.

        Args:
            note_bytes: Raw .note bytes downloaded from cloud.
            notebook_name: The name of the notebook.
            page_range: Optional tuple (start_page, end_page) for specific pages.
                        If None, all pages are read.
        """
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(tmp_fd, note_bytes)
            os.close(tmp_fd)

            notebook = supernotelib.load_notebook(tmp_path)
            converter = ImageConverter(notebook)

            results = []
            total = notebook.get_total_pages()
            start_page = page_range[0] if page_range else 0
            end_page = min(page_range[1] if page_range else total - 1, total - 1)

            for page_num in range(start_page, end_page + 1):
                page_image = converter.convert(page_num)
                transcription = await self._transcribe_page(page_image)
                if not transcription:
                    continue

                content_type = await self.classify_content(transcription)
                # Current checkbox state only; no snapshot comparison here.
                current_checked = []
                for match in re.finditer(r"[\u2611\u25a0]\s*(.+)", transcription):
                    current_checked.append(
                        {"task_text": match.group(1).strip(), "tag": "focus"}
                    )
                for match in re.finditer(r"[\u25cf]\s*(.+)", transcription):
                    current_checked.append(
                        {"task_text": match.group(1).strip(), "tag": "orbit"}
                    )

                checkbox_items = [
                    CheckboxItem(
                        task_text=item["task_text"], tag=item["tag"], page_num=page_num
                    )
                    for item in current_checked
                ]

                result = ReadResult(
                    notebook=notebook_name,
                    page_num=page_num,
                    text=transcription,
                    checkboxes=checkbox_items,
                    content_type=content_type,
                    timestamp=datetime.now(timezone.utc),
                    page_image=page_image,
                )
                results.append(result)

            return results
        finally:
            os.unlink(tmp_path)

    async def read_pages(
        self, note_bytes: bytes, notebook_name: str, *, pages: list[int]
    ) -> List[ReadResult]:
        """Read specific pages from notebook bytes, preserving page images."""
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(tmp_fd, note_bytes)
            os.close(tmp_fd)

            notebook = supernotelib.load_notebook(tmp_path)
            converter = ImageConverter(notebook)
            total = notebook.get_total_pages()
            results = []
            for page_num in pages:
                if page_num < 0 or page_num >= total:
                    continue
                result = await self._build_read_result(notebook_name, converter, page_num)
                if result is not None:
                    results.append(result)
            return results
        finally:
            os.unlink(tmp_path)

    async def _build_read_result(
        self, notebook_name: str, converter, page_num: int
    ):
        """OCR + classify a single page; returns a ReadResult or None for empty text.

        Shared by read_pages (fail-loud) and read_pages_resilient (isolated).
        Raises propagate to the caller so each policy can decide how to handle
        a transient vision failure on one page.
        """
        page_image = converter.convert(page_num)
        transcription = await self._transcribe_page(page_image)
        if not transcription:
            return None

        content_type = await self.classify_content(transcription)
        checkbox_items = []
        for match in re.finditer(r"[\u2611\u25a0]\s*(.+)", transcription):
            checkbox_items.append(
                CheckboxItem(
                    task_text=match.group(1).strip(),
                    tag="focus",
                    page_num=page_num,
                )
            )
        for match in re.finditer(r"[\u25cf]\s*(.+)", transcription):
            checkbox_items.append(
                CheckboxItem(
                    task_text=match.group(1).strip(),
                    tag="orbit",
                    page_num=page_num,
                )
            )
        return ReadResult(
            notebook=notebook_name,
            page_num=page_num,
            text=transcription,
            checkboxes=checkbox_items,
            content_type=content_type,
            timestamp=datetime.now(timezone.utc),
            page_image=page_image,
        )

    async def read_pages_resilient(
        self, note_bytes: bytes, notebook_name: str, *, pages: list[int]
    ) -> tuple[List["ReadResult"], dict[int, str]]:
        """Read pages, isolating per-page failures.

        Returns ``(successes, {page_num: error_message})``. A transient failure
        on one page does not discard the pages already read; failed pages are
        reported so the caller can record them for retry instead of losing the
        whole batch (which is what read_pages does on a single raise).

        Every requested page is accounted for, because a page that is asked for and
        answered with nothing is worse than a page that fails: the caller keeps its
        retry claim, the page stays pending, and the recorded error stays frozen at
        whatever failed earlier (Mgmt page 6 sat that way from 13 November, its alarm
        still carrying that night's 504). So a request outside the notebook's page
        count, and a page whose transcription comes back empty, are both named here.
        """
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".note")
        try:
            os.write(tmp_fd, note_bytes)
            os.close(tmp_fd)

            notebook = supernotelib.load_notebook(tmp_path)
            converter = ImageConverter(notebook)
            total = notebook.get_total_pages()
            results: list = []
            errors: dict[int, str] = {}
            for page_num in pages:
                if page_num < 0 or page_num >= total:
                    errors[page_num] = (
                        f"page {page_num} is outside this notebook's {total} pages, "
                        "so there is nothing to read for it"
                    )
                    continue
                try:
                    result = await self._build_read_result(
                        notebook_name, converter, page_num
                    )
                except Exception as exc:  # isolate per-page failure
                    errors[page_num] = str(exc) or exc.__class__.__name__
                    continue
                if result is None:
                    errors[page_num] = blank_cap.blank_page_error_message(page_num)
                    continue
                results.append(result)
            return results, errors
        finally:
            os.unlink(tmp_path)


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, confidence))
