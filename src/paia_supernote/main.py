"""
ABOUTME: paia-supernote main service entry point
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Main daemon that orchestrates file watching, content processing, and agent integration
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict

import structlog

from .events import EventsClient
from .cloud_poller import CloudPoller
from .enrich_service import EnrichService
from .ingest_service import IngestService
from .reader import SupernoteReader
from .writer import SupernoteWriter
from .uploader import SupernoteUploader
from .notebook_writer import append_page_to_notebook
from .tasks_sync import TasksSync
from .task_curator import TaskCurator
from .model_config import default_zai_base_url, default_zai_text_model, default_zai_vision_model

log = structlog.get_logger(__name__)

DEFAULT_CONFIG_PATH = Path("~/.paia/supernote/config.toml").expanduser()

DEFAULT_CONFIG: Dict[str, Any] = {
    "poll_interval": 60,
    "vision_backend": "zai",
    "rewrite_backend": "zai",
    "ollama_model": "qwen2.5vl:7b",
    "ollama_url": "http://localhost:11434",
    "zai_api_key": None,
    "zai_base_url": default_zai_base_url(),
    "zai_vision_model": default_zai_vision_model(),
    "zai_text_model": default_zai_text_model(),
    "events_url": "http://localhost:3511",
    "folio_url": "http://localhost:3512",
    "work_url": "http://localhost:3560",
    "state_db_path": str(Path("~/.paia/supernote/supernote-state.db").expanduser()),
    "agent_mappings": {
        "Sam": {"font": "Bradley Hand", "notebook": "Quick"},
        "Caroline": {"font": "Noteworthy", "notebook": "LFW"},
        "Ingrid": {"font": "Chalkduster", "notebook": "Synth"},
    },
}


def load_config(config_path: Path | None = None) -> Dict[str, Any]:
    """Load config from TOML file, falling back to env vars and defaults.

    Precedence: env vars > TOML file > defaults.
    """
    config = dict(DEFAULT_CONFIG)

    path = config_path or DEFAULT_CONFIG_PATH
    if path.exists():
        with open(path, "rb") as f:
            file_config = tomllib.load(f)
        # Flatten nested TOML sections into our config dict
        if "supernote" in file_config:
            sn = file_config["supernote"]
            if "poll_interval" in sn:
                config["poll_interval"] = int(sn["poll_interval"])
            for key in (
                "vision_backend",
                "rewrite_backend",
                "ollama_model",
                "ollama_url",
                "zai_api_key",
                "zai_base_url",
                "zai_vision_model",
                "zai_text_model",
                "state_db_path",
            ):
                if key in sn:
                    config[key] = sn[key]
        if "services" in file_config:
            svc = file_config["services"]
            for key in ("events_url", "folio_url", "work_url"):
                if key in svc:
                    config[key] = svc[key]
        if "agents" in file_config:
            config["agent_mappings"] = file_config["agents"]

    # Env var overrides
    if env_poll := os.environ.get("SUPERNOTE_POLL_INTERVAL"):
        config["poll_interval"] = int(env_poll)
    if env_vision_backend := os.environ.get("SUPERNOTE_VISION_BACKEND"):
        config["vision_backend"] = env_vision_backend
    if env_rewrite_backend := os.environ.get("SUPERNOTE_REWRITE_BACKEND"):
        config["rewrite_backend"] = env_rewrite_backend
    if env_zai_api_key := os.environ.get("ZAI_API_KEY"):
        config["zai_api_key"] = env_zai_api_key
    if env_state_db_path := os.environ.get("SUPERNOTE_STATE_DB_PATH"):
        config["state_db_path"] = env_state_db_path
    if env_events := os.environ.get("PAIA_EVENTS_URL"):
        config["events_url"] = env_events
    if env_folio := os.environ.get("PAIA_FOLIO_URL"):
        config["folio_url"] = env_folio
    if env_work := os.environ.get("PAIA_WORK_URL"):
        config["work_url"] = env_work
    if env_state_db := os.environ.get("SUPERNOTE_STATE_DB_PATH"):
        config["state_db_path"] = env_state_db

    return config


class SupernoteService:
    """Main paia-supernote service that coordinates all components."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.events = EventsClient(base_url=self.config["events_url"])
        self.reader = SupernoteReader(
            vision_backend=self.config["vision_backend"],
            ollama_model=self.config["ollama_model"],
            ollama_url=self.config["ollama_url"],
            zai_api_key=self.config["zai_api_key"],
            zai_base_url=self.config["zai_base_url"],
            zai_vision_model=self.config["zai_vision_model"],
            zai_text_model=self.config["zai_text_model"],
        )
        self.writer = SupernoteWriter()
        self.uploader = SupernoteUploader()
        self.cloud_poller = CloudPoller(
            uploader=self.uploader,
            on_note_changed=self._on_note_changed,
            poll_interval=self.config["poll_interval"],
        )
        self.tasks_sync = TasksSync(
            uploader=self.uploader,
            writer=self.writer,
            work_url=self.config["work_url"],
            poll_interval=self.config["poll_interval"],
        )
        self.task_curator = TaskCurator(
            reader=self.reader,
            writer=self.writer,
            uploader=self.uploader,
            events_client=self.events,
            rewrite_backend=self.config["rewrite_backend"],
            zai_base_url=self.config["zai_base_url"],
            zai_text_model=self.config["zai_text_model"],
        )
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the Supernote service."""
        log.info("service_starting", config_keys=list(self.config.keys()))

        # Start events connection
        await self.events.start()
        self.events.register_write_handler(self._handle_write_request)

        # Start uploader browser session (also used by cloud poller for API calls)
        await self.uploader.start()

        # Start cloud poller (no Partner app needed)
        self.cloud_poller.start()

        # Start tasks sync (paia-work → tasks.note)
        self.tasks_sync.start()

        log.info("service_started")

        # Wait for shutdown signal
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the Supernote service, flushing pending ops."""
        log.info("service_stopping")

        await self.tasks_sync.stop()
        await self.cloud_poller.stop()
        await self.uploader.stop()
        await self.events.stop()

        self._shutdown_event.set()
        log.info("service_stopped")

    async def _on_note_changed(
        self, notebook_name: str, note_bytes: bytes, update_time: int | None = None
    ) -> None:
        """Handle change events from the cloud poller."""
        log.info("note_changed", notebook=notebook_name, size=len(note_bytes))
        await self._process_file_change(notebook_name, note_bytes)

    async def _process_file_change(self, notebook_name: str, note_bytes: bytes) -> None:
        """Process a changed .note file through the read pipeline."""
        try:
            read_results = await self.reader.process_file(note_bytes, notebook_name)

            for result in read_results:
                # All transcriptions go to folio
                await self.events.publish_note_transcribed(
                    result.notebook, result.page_num, result.text
                )

                if result.content_type == "task":
                    await self._handle_task_content(result)
                elif result.content_type == "snippet":
                    await self._handle_strategy_snippet(result)

                for checkbox in result.checkboxes:
                    await self.events.publish_checkbox_completed(
                        task_id="",
                        notebook=result.notebook,
                        page=result.page_num,
                        task_text=checkbox.task_text,
                        tag=checkbox.tag,
                    )
                    # tasks.note checkboxes → mark done in paia-work
                    await self._handle_task_checkbox(
                        result.notebook, result.page_num,
                        checkbox.task_text, checkbox.tag,
                    )

        except Exception as exc:
            log.error("file_processing_error", notebook=notebook_name, error=str(exc))

    async def _handle_task_content(self, result) -> None:
        """Handle task content (box/circle markers)."""
        log.info("task_content_detected", notebook=result.notebook,
                 preview=result.text[:100])

    async def _handle_strategy_snippet(self, result) -> None:
        """Handle strategy snippet detection — route to appropriate agent."""
        agent = "Caroline" if result.notebook == "LFW" else "Ingrid"
        await self.events.publish_snippet_detected(
            notebook=result.notebook,
            page=result.page_num,
            text=result.text,
            agent=agent,
        )

    async def _handle_task_checkbox(self, notebook: str, page: int, task_text: str, tag: str) -> None:
        """Mark a task done in paia-work when checked off on tasks.note.

        Parses the task ID from text like '☑ Task title  [abc123]'.
        """
        import re
        import httpx

        if notebook != "tasks":
            return

        match = re.search(r'\[([^\]]+)\]', task_text)
        if not match:
            log.warning("tasks_checkbox_no_id", text=task_text[:80])
            return

        task_id = match.group(1)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.config['work_url']}/api/tasks/{task_id}/done",
                    timeout=5.0,
                )
                resp.raise_for_status()
            log.info("task_marked_done", task_id=task_id)
        except httpx.HTTPError as exc:
            log.warning("task_mark_done_failed", task_id=task_id, error=str(exc))

    async def _handle_write_request(self, event_data: Dict[str, Any]) -> None:
        """Handle write request events from agents.

        Pipeline: download from cloud → render_page → append → upload/replace.
        Does not require Partner app sync — reads directly from Supernote Cloud.
        """
        import tempfile
        import os

        try:
            agent = event_data["agent"]
            notebook = event_data.get("notebook") or self.config["agent_mappings"].get(agent, {}).get("notebook", "")
            content = event_data.get("content", "")
            content_type = event_data.get("content_type")

            # Validate agent is known
            if agent not in self.config["agent_mappings"]:
                log.warning("write_request_unknown_agent", agent=agent)
                return

            # tasks.note is owned by TasksSync — agents cannot write to it
            if notebook == "tasks":
                log.warning("write_request_rejected_tasks_notebook", agent=agent)
                return

            # Delegate task_page_curate to TaskCurator
            if content_type == "task_page_curate":
                log.info("delegating_task_page_curation", agent=agent, notebook=notebook)
                event_data["notebook_bytes"] = await self.uploader.download_notebook(f"{notebook}.note")
                await self.task_curator.handle_write_requested(event_data)
                return

            target_name = f"{notebook}.note"

            log.info("write_request_received", agent=agent, notebook=notebook)

            # Step 1: Download the current notebook from cloud
            notebook_bytes = await self.uploader.download_notebook(target_name)
            log.info("notebook_downloaded", target_name=target_name,
                     size=len(notebook_bytes))

            # Step 2: Render page content to RATTA_RLE bytes
            ratta_rle_bytes = self.writer.render_page(agent, content)

            # Step 3: Append page to the downloaded notebook bytes
            updated_bytes = append_page_to_notebook(notebook_bytes, ratta_rle_bytes)

            # Step 4: Write updated notebook to a temp file and upload
            with tempfile.NamedTemporaryFile(
                suffix=".note", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(updated_bytes)
                tmp_path = tmp.name

            try:
                success = await self.uploader.upload_notebook(tmp_path, target_name)
            finally:
                os.unlink(tmp_path)

            if success:
                log.info("write_complete", agent=agent, notebook=notebook)
            else:
                log.warning("write_upload_failed", agent=agent, notebook=notebook)

        except Exception as exc:
            log.error("write_request_error", error=str(exc))


def _configure_logging() -> None:
    """Configure structlog for JSON output to stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


def render_status(db_path: Path) -> str:
    from .page_state import PageStateStore
    if not db_path.exists():
        return "No state database found."
    store = PageStateStore(db_path)
    store.init_schema()
    dirty = store.dirty_count()
    ocr_errors = store.error_count("ocr")
    enrich_errors = store.error_count("enrich")
    return (
        f"dirty_pages={dirty}  ocr_errors={ocr_errors}  enrich_errors={enrich_errors}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="paia-supernote",
        description="PAIA service for bidirectional Supernote device integration",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to config TOML file (default: {DEFAULT_CONFIG_PATH})",
    )
    subparsers = parser.add_subparsers(dest="mode", required=False)
    subparsers.add_parser("ingest", help="Poll Supernote Cloud and OCR pages")
    subparsers.add_parser("enrich", help="Enrich dirty pages and upsert to Folio")
    subparsers.add_parser("status", help="Show pipeline queue counts")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Main entry point for paia-supernote service."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_logging()

    config = load_config(args.config)
    mode = args.mode or "ingest"

    if mode == "status":
        print(render_status(Path(config["state_db_path"])))
        return

    if mode == "enrich":
        service: Any = EnrichService(config)
    else:
        service = IngestService(config)

    loop = asyncio.new_event_loop()

    def _signal_handler() -> None:
        log.info("shutdown_signal_received")
        loop.create_task(service.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        log.info("service_interrupted")
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
