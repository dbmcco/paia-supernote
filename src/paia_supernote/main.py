"""
ABOUTME: paia-supernote main service entry point
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Main daemon for file watching, content processing, and agent integration
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import os
import signal
import sys
from contextlib import suppress
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict

import structlog
import tomllib

from .agent_write_contracts import (
    AgentWriteRevisionError,
    assert_downloaded_matches_base,
    missing_notebook_conflict,
    validate_agent_write_request,
)
from .cloud_poller import CloudPoller
from .contract_errors import agent_error_json, format_agent_error
from .enrich_service import EnrichService
from .events import EventsClient
from .ingest_service import IngestService
from .model_config import (
    default_zai_base_url,
    default_zai_text_model,
    default_zai_vision_model,
    resolve_supernote_zai_api_key,
)
from .notebook_artifacts import NotebookPageSpec, replace_notebook_pages
from .notebook_writer import append_page_to_notebook
from .organizer_runtime import create_organizer_api
from .organizer_server import make_organizer_handler
from .quick_filing import notebook_name_to_tag
from .quick_filing_service import QuickFilingService
from .reader import build_reader
from .task_curator import TaskCurator
from .tasks_sync import TasksSync
from .uploader import (
    SupernoteUploadConflictError,
    SupernoteUploader,
    UploadSyncInProgressError,
)
from .writer import SupernoteWriter

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
    "linear_api_key": None,
    "linear_team_key": "LFW",
    "linear_team_id": None,
    "state_db_path": str(Path("~/.paia/supernote/supernote-state.db").expanduser()),
    "folio_sync_notebooks": [],
    "filing_enabled": False,
    "filing_dry_run": True,
    "filing_ledger_db_path": str(
        Path("~/.paia/supernote/filing-ledger.db").expanduser()
    ),
    "filing_source_notebooks": ["Test Note 1"],
    "filing_destination_notebooks": [
        "Test Note 2",
        "LFW",
        "MGMT",
        "Navicyte",
        "Synth",
        "Synthera",
    ],
    "filing_destination_map": {},
    "service_cloud_poller_enabled": True,
    "service_uploader_start_mode": "eager",
    "agent_mappings": {
        "Sam": {"font": "Bradley Hand", "notebook": "Quick"},
        "Caroline": {"font": "Noteworthy", "notebook": "LFW"},
        "Ingrid": {"font": "Chalkduster", "notebook": "Synth"},
    },
}


class NotebookConflictError(RuntimeError):
    """Raised when Supernote Cloud already has a conflict copy for a notebook."""


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
                "folio_sync_notebooks",
                "service_cloud_poller_enabled",
                "service_uploader_start_mode",
            ):
                if key in sn:
                    config[key] = sn[key]
        if "services" in file_config:
            svc = file_config["services"]
            for key in ("events_url", "folio_url", "work_url"):
                if key in svc:
                    config[key] = svc[key]
        if "linear" in file_config:
            lin = file_config["linear"]
            for key in ("linear_api_key", "linear_team_key", "linear_team_id"):
                if key in lin:
                    config[key] = lin[key]
        if "filing" in file_config:
            filing = file_config["filing"]
            for key in (
                "enabled",
                "dry_run",
                "ledger_db_path",
                "source_notebooks",
                "destination_notebooks",
                "destination_map",
            ):
                if key not in filing:
                    continue
                config_key = f"filing_{key}"
                config[config_key] = filing[key]
        if "agents" in file_config:
            config["agent_mappings"] = file_config["agents"]

    # Env var overrides
    if env_poll := os.environ.get("SUPERNOTE_POLL_INTERVAL"):
        config["poll_interval"] = int(env_poll)
    if env_vision_backend := os.environ.get("SUPERNOTE_VISION_BACKEND"):
        config["vision_backend"] = env_vision_backend
    if env_rewrite_backend := os.environ.get("SUPERNOTE_REWRITE_BACKEND"):
        config["rewrite_backend"] = env_rewrite_backend
    if env_zai_api_key := resolve_supernote_zai_api_key():
        config["zai_api_key"] = env_zai_api_key
    if env_state_db_path := os.environ.get("SUPERNOTE_STATE_DB_PATH"):
        config["state_db_path"] = env_state_db_path
    if env_events := os.environ.get("PAIA_EVENTS_URL"):
        config["events_url"] = env_events
    if env_folio := os.environ.get("PAIA_FOLIO_URL"):
        config["folio_url"] = env_folio
    if env_work := os.environ.get("PAIA_WORK_URL"):
        config["work_url"] = env_work
    if env_linear_key := os.environ.get("LINEAR_API_KEY"):
        config["linear_api_key"] = env_linear_key
    if env_linear_team_key := os.environ.get("LINEAR_TEAM_KEY"):
        config["linear_team_key"] = env_linear_team_key
    if env_linear_team_id := os.environ.get("LINEAR_TEAM_ID"):
        config["linear_team_id"] = env_linear_team_id
    if env_state_db := os.environ.get("SUPERNOTE_STATE_DB_PATH"):
        config["state_db_path"] = env_state_db
    if env_folio_sync := os.environ.get("SUPERNOTE_FOLIO_SYNC_NOTEBOOKS"):
        config["folio_sync_notebooks"] = [
            notebook.strip()
            for notebook in env_folio_sync.split(",")
            if notebook.strip()
        ]
    if env_filing_enabled := os.environ.get("SUPERNOTE_FILING_ENABLED"):
        config["filing_enabled"] = env_filing_enabled.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if env_filing_dry_run := os.environ.get("SUPERNOTE_FILING_DRY_RUN"):
        config["filing_dry_run"] = env_filing_dry_run.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if env_filing_sources := os.environ.get("SUPERNOTE_FILING_SOURCE_NOTEBOOKS"):
        config["filing_source_notebooks"] = [
            notebook.strip()
            for notebook in env_filing_sources.split(",")
            if notebook.strip()
        ]
    if env_service_cloud_poller := os.environ.get(
        "SUPERNOTE_SERVICE_CLOUD_POLLER_ENABLED"
    ):
        config["service_cloud_poller_enabled"] = (
            env_service_cloud_poller.strip().lower() in {"1", "true", "yes", "on"}
        )
    if env_uploader_start_mode := os.environ.get(
        "SUPERNOTE_SERVICE_UPLOADER_START_MODE"
    ):
        config["service_uploader_start_mode"] = env_uploader_start_mode.strip().lower()

    return config


def _filing_destination_map(config: Dict[str, Any]) -> dict[str, str]:
    destinations = {
        notebook_name_to_tag(str(name)): str(name)
        for name in list(config.get("filing_destination_notebooks") or [])
        if str(name).strip()
    }
    for key, value in dict(config.get("filing_destination_map") or {}).items():
        destinations[notebook_name_to_tag(str(key))] = str(value)
    return destinations


def _watched_notebooks(config: Dict[str, Any]) -> set[str]:
    notebooks = {"Walk", "tasks"}
    notebooks.update(
        str(name).strip()
        for name in config.get("folio_sync_notebooks") or []
        if str(name).strip()
    )
    if config.get("filing_enabled"):
        notebooks.update(
            str(name).strip()
            for name in config.get("filing_source_notebooks") or []
            if str(name).strip()
        )
    return notebooks


class SupernoteService:
    """Main paia-supernote service that coordinates all components."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.events = EventsClient(base_url=self.config["events_url"])
        self.reader = build_reader(self.config)
        self.writer = SupernoteWriter()
        self.uploader = SupernoteUploader()
        self.cloud_poller = CloudPoller(
            uploader=self.uploader,
            on_note_changed=self._on_note_changed,
            poll_interval=self.config["poll_interval"],
            watched_notebooks=_watched_notebooks(self.config),
            process_existing_on_start=False,
            on_poll_health=self._on_poll_health,
        )
        self.tasks_sync = TasksSync(
            uploader=self.uploader,
            writer=self.writer,
            linear_api_key=self.config["linear_api_key"],
            linear_team_key=self.config["linear_team_key"],
            linear_team_id=self.config.get("linear_team_id"),
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
            linear_api_key=self.config["linear_api_key"],
            linear_team_key=self.config["linear_team_key"],
            linear_team_id=self.config.get("linear_team_id"),
        )
        self._shutdown_event = asyncio.Event()
        self._uploader_started = False

    def _should_start_uploader_on_service_start(self) -> bool:
        if self.config.get("service_cloud_poller_enabled", True):
            return True
        return self.config.get("service_uploader_start_mode", "eager") != "lazy"

    async def _ensure_service_uploader_started(self) -> None:
        if getattr(self.uploader, "page", None) is not None:
            self._uploader_started = True
            return
        await self.uploader.start()
        self._uploader_started = True

    def _resolve_agent_name(self, raw_agent: str) -> str | None:
        candidate = str(raw_agent or "").strip()
        if not candidate:
            return None
        if candidate in self.config["agent_mappings"]:
            return candidate

        lowered = candidate.lower()
        alias_map = {
            "sam": "Sam",
            "samantha": "Sam",
            "caroline": "Caroline",
            "ingrid": "Ingrid",
        }
        if lowered in alias_map and alias_map[lowered] in self.config["agent_mappings"]:
            return alias_map[lowered]

        for configured in self.config["agent_mappings"]:
            if configured.lower() == lowered:
                return configured
        return None

    async def start(self) -> None:
        """Start the Supernote service."""
        log.info("service_starting", config_keys=list(self.config.keys()))

        try:
            if self._should_start_uploader_on_service_start():
                await self.uploader.start()
                self._uploader_started = True
            else:
                log.info("service_uploader_lazy_start_enabled")

            # Register inbound writes only after the uploader is ready.
            self.events.register_write_handler(self._handle_write_request)
            await self.events.start()

            if self.config.get("service_cloud_poller_enabled", True):
                self.cloud_poller.start()
            else:
                log.info("service_cloud_poller_disabled")

            # Start tasks sync (Linear → tasks.note)
            self.tasks_sync.start()

            log.info("service_started")

            await self._wait_until_shutdown_or_poller_exit()
        except Exception as exc:
            log.error("service_exiting_after_poller_failure", error=str(exc))
            with suppress(Exception):
                await self.tasks_sync.stop()
            with suppress(Exception):
                if self.config.get("service_cloud_poller_enabled", True):
                    await self.cloud_poller.stop()
            with suppress(Exception):
                if self._uploader_started:
                    await self.uploader.stop()
            with suppress(Exception):
                await self.events.stop()
            raise

    async def stop(self) -> None:
        """Stop the Supernote service, flushing pending ops."""
        log.info("service_stopping")

        await self.tasks_sync.stop()
        if self.config.get("service_cloud_poller_enabled", True):
            await self.cloud_poller.stop()
        if self._uploader_started:
            await self.uploader.stop()
        await self.events.stop()

        self._shutdown_event.set()
        log.info("service_stopped")

    async def _wait_until_shutdown_or_poller_exit(self) -> None:
        if not self.config.get("service_cloud_poller_enabled", True):
            await self._shutdown_event.wait()
            return

        poller_wait = getattr(self.cloud_poller, "wait", None)
        if poller_wait is None or not inspect.iscoroutinefunction(poller_wait):
            await self._shutdown_event.wait()
            return

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        poller_task = asyncio.create_task(poller_wait())

        done, pending = await asyncio.wait(
            {shutdown_task, poller_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task

        if poller_task in done:
            poller_task.result()

    async def _on_note_changed(
        self, notebook_name: str, note_bytes: bytes, update_time: int | None = None
    ) -> None:
        """Handle change events from the cloud poller."""
        log.info("note_changed", notebook=notebook_name, size=len(note_bytes))
        await self._process_file_change(notebook_name, note_bytes)

    async def _on_poll_health(
        self, healthy: bool, detail: dict[str, object]
    ) -> None:
        """Surface cloud read-poll health so Walk feedback ingest failures are visible.

        A 401/403 leaves the poller fetching an empty list silently; this emits a
        monitoring event (failed/recovered) on each transition so the outage is
        observable rather than buried in logs.
        """
        await self.events.publish_feedback_ingest_status(
            healthy=healthy,
            reason=detail.get("reason"),  # type: ignore[arg-type]
            status=detail.get("status"),  # type: ignore[arg-type]
            notebooks=detail.get("notebooks"),  # type: ignore[arg-type]
        )

    async def _process_file_change(self, notebook_name: str, note_bytes: bytes) -> None:
        """Process a changed .note file through the read pipeline."""
        try:
            read_results = await self.reader.process_file(note_bytes, notebook_name)

            for result in read_results:
                await self.events.publish_note_transcribed(
                    result.notebook, result.page_num, result.text
                )
                if str(result.notebook).casefold() == "walk":
                    await self.events.publish_walk_feedback_detected(
                        notebook=result.notebook,
                        page=result.page_num,
                        text=result.text,
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
                    # tasks.note checkboxes → mark done in Linear
                    await self._handle_task_checkbox(
                        result.notebook,
                        result.page_num,
                        checkbox.task_text,
                        checkbox.tag,
                    )

        except Exception as exc:
            log.error("file_processing_error", notebook=notebook_name, error=str(exc))

        try:
            await self._run_note_filing_if_configured(notebook_name, note_bytes)
        except Exception as exc:
            log.error("note_filing_error", notebook=notebook_name, error=str(exc))

    async def _run_note_filing_if_configured(
        self, notebook_name: str, note_bytes: bytes
    ) -> None:
        if not self.config.get("filing_enabled"):
            return
        source_notebooks = {
            str(name).strip()
            for name in self.config.get("filing_source_notebooks") or []
            if str(name).strip()
        }
        source_notebook_keys = {name.casefold() for name in source_notebooks}
        if notebook_name.casefold() not in source_notebook_keys:
            return

        service = QuickFilingService(
            uploader=self.uploader,
            ledger_db_path=Path(self.config["filing_ledger_db_path"]),
            source_notebook=notebook_name,
            destination_map=_filing_destination_map(self.config),
            dry_run=bool(self.config.get("filing_dry_run", True)),
            allowed_source_notebooks={notebook_name},
            reader=self.reader,
        )
        result = await service.run_once(source_bytes=note_bytes)
        log.info(
            "note_filing_checked",
            notebook=notebook_name,
            candidate_count=result.get("candidate_count"),
            dry_run=result.get("dry_run"),
        )

    async def _handle_task_content(self, result) -> None:
        """Handle task content (box/circle markers)."""
        log.info(
            "task_content_detected", notebook=result.notebook, preview=result.text[:100]
        )

    async def _handle_strategy_snippet(self, result) -> None:
        """Handle strategy snippet detection — route to appropriate agent."""
        agent = "Caroline" if result.notebook == "LFW" else "Ingrid"
        await self.events.publish_snippet_detected(
            notebook=result.notebook,
            page=result.page_num,
            text=result.text,
            agent=agent,
        )

    async def _handle_task_checkbox(
        self, notebook: str, page: int, task_text: str, tag: str
    ) -> None:
        """Mark a Linear issue done when checked off on tasks.note.

        Parses the issue identifier from text like '☑ Task title  [LFW-42]'.
        """
        import re

        from paia_agent_runtime.tools.linear import LinearTool

        if notebook != "tasks":
            return

        match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", task_text)
        if not match:
            log.warning("tasks_checkbox_no_id", text=task_text[:80])
            return

        identifier = match.group(1)
        linear = LinearTool(
            api_key=self.config["linear_api_key"],
            team_id=self.config.get("linear_team_id"),
        )
        result = await linear.execute("complete_issue", id=identifier)
        if result.get("status") == "ok":
            log.info("task_marked_done", identifier=identifier)
        else:
            log.warning(
                "task_mark_done_failed",
                identifier=identifier,
                error=result.get("error"),
            )

    async def _publish_write_completed(
        self,
        event_data: Dict[str, Any],
        *,
        agent: str,
        notebook: str,
        content_type: str | None,
        page_count: int,
        artifact_refs: dict[str, Any] | None = None,
    ) -> None:
        await self.events.publish_write_completed(
            request_event_id=event_data.get("request_event_id"),
            request_source_event_id=event_data.get("request_source_event_id"),
            run_id=event_data.get("run_id") or event_data.get("action_id"),
            agent=agent,
            notebook=notebook,
            content_type=content_type,
            page_count=page_count,
            artifact_refs=artifact_refs,
        )

    async def _publish_write_failed(
        self,
        event_data: Dict[str, Any],
        *,
        agent: str | None,
        notebook: str | None,
        content_type: str | None,
        page_count: int = 0,
        error: str,
        structured_error: Any | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.events.publish_write_failed(
            request_event_id=event_data.get("request_event_id"),
            request_source_event_id=event_data.get("request_source_event_id"),
            run_id=event_data.get("run_id") or event_data.get("action_id"),
            agent=agent,
            notebook=notebook,
            content_type=content_type,
            page_count=page_count,
            error=error,
            structured_error=structured_error,
            error_message=error_message,
        )

    async def _handle_write_request(self, event_data: Dict[str, Any]) -> None:
        """Handle write request events from agents.

        Pipeline: download from cloud → render_page → append → upload/replace.
        Does not require Partner app sync — reads directly from Supernote Cloud.
        """
        import os
        import tempfile

        raw_agent: str | None = None
        agent: str | None = None
        notebook: str | None = None
        content_type: str | None = None

        try:
            raw_agent = str(event_data["agent"])
            agent = self._resolve_agent_name(raw_agent)
            content = event_data.get("content", "")
            content_type = event_data.get("content_type")

            # Validate agent is known
            if agent is None or agent not in self.config["agent_mappings"]:
                log.warning("write_request_unknown_agent", agent=raw_agent)
                await self._publish_write_failed(
                    event_data,
                    agent=raw_agent,
                    notebook=event_data.get("notebook"),
                    content_type=content_type,
                    error="unknown_agent",
                )
                return

            requested_notebook = event_data.get("notebook")
            if str(requested_notebook or "").strip():
                notebook = str(requested_notebook).strip()
            elif event_data.get("use_agent_default_notebook") is True:
                notebook = str(
                    self.config["agent_mappings"].get(agent, {}).get("notebook") or ""
                ).strip()
            else:
                raise AgentWriteRevisionError(
                    missing_notebook_conflict(
                        event_data,
                        config=self.config,
                        resolved_agent=agent,
                    )
                )

            if not notebook:
                raise AgentWriteRevisionError(
                    missing_notebook_conflict(
                        event_data,
                        config=self.config,
                        resolved_agent=agent,
                    )
                )

            accepted = validate_agent_write_request(
                event_data,
                config=self.config,
                state_db_path=Path(self.config["state_db_path"]),
                resolved_agent=agent,
                resolved_notebook=notebook,
            )

            # Delegate task_page_curate to TaskCurator (can target tasks.note)
            if content_type == "task_page_curate":
                log.info(
                    "delegating_task_page_curation", agent=agent, notebook=notebook
                )
                event_data["notebook_bytes"] = await self.uploader.download_notebook(
                    f"{notebook}.note"
                )
                await self.task_curator.handle_write_requested(event_data)
                return

            # tasks.note is owned by TasksSync — agents cannot write to it
            if notebook == "tasks":
                log.warning("write_request_rejected_tasks_notebook", agent=agent)
                await self._publish_write_failed(
                    event_data,
                    agent=agent,
                    notebook=notebook,
                    content_type=content_type,
                    error="tasks_notebook_rejected",
                )
                return

            await self._ensure_service_uploader_started()

            # Handle replace_pages content type
            if content_type == "replace_pages":
                page_specs = [
                    NotebookPageSpec(
                        agent=str(page.get("agent") or agent),
                        content=str(page.get("content") or ""),
                    )
                    for page in event_data.get("pages", [])
                ]
                if not page_specs:
                    log.warning("write_request_replace_pages_empty", notebook=notebook)
                    await self._publish_write_failed(
                        event_data,
                        agent=agent,
                        notebook=notebook,
                        content_type=content_type,
                        error="empty_pages",
                    )
                    return

                target_name = f"{notebook}.note"
                try:
                    success = await self._replace_pages_with_uploader(
                        uploader=self.uploader,
                        target_name=target_name,
                        page_specs=page_specs,
                    )
                except (
                    NotebookConflictError,
                    SupernoteUploadConflictError,
                    UploadSyncInProgressError,
                ) as exc:
                    log.warning(
                        "replace_pages_conflict_blocked",
                        agent=agent,
                        notebook=notebook,
                        error=str(exc),
                    )
                    await self._publish_write_failed(
                        event_data,
                        agent=agent,
                        notebook=notebook,
                        content_type=content_type,
                        page_count=len(page_specs),
                        error=str(exc),
                    )
                    return
                except RuntimeError as exc:
                    log.warning(
                        "replace_pages_retrying_with_fresh_uploader",
                        agent=agent,
                        notebook=notebook,
                        error=str(exc),
                    )
                    fresh_uploader = SupernoteUploader()
                    await fresh_uploader.start()
                    try:
                        success = await self._replace_pages_with_uploader(
                            uploader=fresh_uploader,
                            target_name=target_name,
                            page_specs=page_specs,
                        )
                    finally:
                        await fresh_uploader.stop()

                if success:
                    log.info(
                        "replace_pages_complete",
                        agent=agent,
                        notebook=notebook,
                        page_count=len(page_specs),
                    )
                    await self._publish_write_completed(
                        event_data,
                        agent=agent,
                        notebook=notebook,
                        content_type=content_type,
                        page_count=len(page_specs),
                        artifact_refs={"notebook": target_name},
                    )
                else:
                    log.warning(
                        "replace_pages_upload_failed", agent=agent, notebook=notebook
                    )
                    await self._publish_write_failed(
                        event_data,
                        agent=agent,
                        notebook=notebook,
                        content_type=content_type,
                        page_count=len(page_specs),
                        error="upload_failed",
                    )
                return

            target_name = f"{notebook}.note"

            log.info("write_request_received", agent=agent, notebook=notebook)

            # Step 1: Download the current notebook from cloud
            notebook_bytes = await self.uploader.download_notebook(target_name)
            log.info(
                "notebook_downloaded", target_name=target_name, size=len(notebook_bytes)
            )

            # Step 1.5: Post-download compare-and-swap. The cache-only base check
            # can't see a concurrent Cloud edit; reject now if the live bytes
            # diverge from the base revision the agent read, before we mutate.
            assert_downloaded_matches_base(notebook_bytes, accepted)

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
                # Step 5: re-verify the Cloud holds exactly the bytes we uploaded.
                # Catches a concurrent overwrite that landed during the upload
                # window — a successful upload alone cannot detect it.
                try:
                    await self._reverify_upload(
                        self.uploader, target_name, updated_bytes
                    )
                except RuntimeError as exc:
                    log.warning(
                        "write_reverify_failed",
                        agent=agent,
                        notebook=notebook,
                        error=str(exc),
                    )
                    await self._publish_write_failed(
                        event_data,
                        agent=agent,
                        notebook=notebook,
                        content_type=content_type,
                        page_count=1,
                        error=str(exc),
                    )
                    return
                log.info("write_complete", agent=agent, notebook=notebook)
                await self._publish_write_completed(
                    event_data,
                    agent=agent,
                    notebook=notebook,
                    content_type=content_type,
                    page_count=1,
                    artifact_refs={"notebook": target_name},
                )
            else:
                log.warning("write_upload_failed", agent=agent, notebook=notebook)
                await self._publish_write_failed(
                    event_data,
                    agent=agent,
                    notebook=notebook,
                    content_type=content_type,
                    page_count=1,
                    error="upload_failed",
                )

        except AgentWriteRevisionError as exc:
            log.warning(
                "write_revision_conflict",
                conflict=exc.conflict.model_dump(),
            )
            await self._publish_write_failed(
                event_data,
                agent=agent or raw_agent,
                notebook=notebook,
                content_type=content_type,
                error=agent_error_json(exc.conflict, indent=None),
                structured_error=exc.conflict,
                error_message=format_agent_error(exc.conflict),
            )
        except Exception as exc:
            log.error("write_request_error", error=str(exc))
            await self._publish_write_failed(
                event_data,
                agent=agent or raw_agent,
                notebook=notebook,
                content_type=content_type,
                error=str(exc),
            )

    async def _reverify_upload(
        self,
        uploader: SupernoteUploader,
        target_name: str,
        expected_bytes: bytes,
    ) -> None:
        """Re-download and confirm the Cloud holds exactly the bytes we uploaded.

        Mirrors the manual-move path's re-verify (``cli._reverify_sha256``):
        catches a concurrent overwrite that landed during the upload window,
        which a successful upload response alone cannot detect.
        """
        redownloaded = await uploader.download_notebook(target_name)
        if (
            hashlib.sha256(redownloaded).digest()
            != hashlib.sha256(expected_bytes).digest()
        ):
            raise RuntimeError(
                f"{target_name}: post-upload re-verify failed (sha256 mismatch)"
            )

    async def _replace_pages_with_uploader(
        self,
        *,
        uploader: SupernoteUploader,
        target_name: str,
        page_specs: list[NotebookPageSpec],
    ) -> bool:
        import os
        import tempfile

        await self._raise_if_cloud_conflict_exists(uploader, target_name)
        try:
            notebook_bytes = await uploader.download_notebook(target_name)
        except RuntimeError as exc:
            if "not found in Note folder" not in str(exc):
                raise
            notebook_bytes = await uploader.download_notebook("Quick.note")

        updated_bytes = replace_notebook_pages(
            notebook_bytes,
            writer=self.writer,
            pages=page_specs,
        )

        with tempfile.NamedTemporaryFile(
            suffix=".note", delete=False, dir="/tmp"
        ) as tmp:
            tmp.write(updated_bytes)
            tmp_path = tmp.name

        try:
            return await uploader.upload_notebook(tmp_path, target_name)
        finally:
            os.unlink(tmp_path)

    async def _raise_if_cloud_conflict_exists(
        self,
        uploader: SupernoteUploader,
        target_name: str,
    ) -> None:
        if not isinstance(uploader, SupernoteUploader):
            return
        stem = target_name.removesuffix(".note")
        result = await uploader._api_call(
            "/api/file/list/query",
            {
                "directoryId": uploader.NOTE_FOLDER_ID,
                "pageNo": 1,
                "pageSize": 200,
                "order": "time",
                "sequence": "desc",
                "filterType": 0,
            },
        )
        if result.get("status") != 200:
            return
        conflicts = [
            str(entry.get("fileName") or "")
            for entry in (result.get("body") or {}).get("userFileVOList", [])
            if _is_conflict_name(str(entry.get("fileName") or ""), stem)
        ]
        if conflicts:
            joined_conflicts = ", ".join(conflicts)
            raise NotebookConflictError(
                f"{target_name} has unresolved cloud conflict(s): {joined_conflicts}"
            )


def _is_conflict_name(file_name: str, target_stem: str) -> bool:
    lower_name = file_name.casefold()
    lower_stem = target_stem.casefold()
    return (
        lower_name.endswith(".note")
        and lower_name.startswith(f"{lower_stem}_")
        and "conflict" in lower_name
    )


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
    subparsers.add_parser("service", help="Run the event-backed Supernote service")
    ingest_parser = subparsers.add_parser(
        "ingest", help="Poll Supernote Cloud and OCR pages"
    )
    ingest_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit (no continuous loop).",
    )
    ingest_parser.add_argument(
        "--backfill",
        action="store_true",
        help="With --once: process ALL existing pages in each watched notebook, "
        "not just changes since the last poll (cost-guard override).",
    )
    ingest_parser.add_argument(
        "--notebook",
        action="append",
        default=None,
        metavar="NAME",
        help="Scope the poll to this notebook stem (repeatable). "
        "Defaults to all allowlisted notebooks. Pair with --once.",
    )
    subparsers.add_parser("enrich", help="Enrich dirty pages and upsert to Folio")
    subparsers.add_parser("status", help="Show pipeline queue counts")
    organizer_parser = subparsers.add_parser(
        "organizer",
        help="Run the local Supernote Organizer web UI",
    )
    organizer_parser.add_argument("--host", default="127.0.0.1")
    organizer_parser.add_argument("--port", type=int, default=8765)
    organizer_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory for rendered organizer page images",
    )
    subparsers.add_parser(
        "login", help="Re-authenticate with Supernote Cloud (opens browser)"
    )
    return parser


async def _run_login() -> None:
    """Open a visible browser to refresh the Supernote Cloud session."""
    from .uploader import SupernoteUploader

    print(
        "Opening Supernote Cloud in browser — log in, "
        "then close the tab or press Ctrl+C here."
    )
    uploader = SupernoteUploader(headless=False)
    await uploader.start()
    try:
        await uploader.ensure_authenticated()
        print(f"Session saved to {uploader.SESSION_FILE}")
    finally:
        await uploader.stop()


async def _run_organizer(
    *,
    host: str,
    port: int,
    cache_dir: Path | None,
    uploader_factory: Callable[[], Any] = SupernoteUploader,
    server_factory: Callable[..., Any] = ThreadingHTTPServer,
) -> None:
    uploader = uploader_factory()
    await uploader.start()
    loop = asyncio.get_running_loop()

    def run_on_launcher_loop(awaitable: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(awaitable, loop).result()

    server = None
    try:
        api = create_organizer_api(uploader=uploader, cache_dir=cache_dir)
        handler = make_organizer_handler(api, async_runner=run_on_launcher_loop)
        server = server_factory((host, port), handler)
        actual_host, actual_port = server.server_address
        print(f"Supernote Organizer: http://{actual_host}:{actual_port}/organizer")
        await asyncio.to_thread(server.serve_forever)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        await uploader.stop()


def main(argv: list[str] | None = None) -> None:
    """Main entry point for paia-supernote service."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_logging()

    config = load_config(args.config)
    mode = args.mode or "service"

    if mode == "status":
        print(render_status(Path(config["state_db_path"])))
        return

    if mode == "login":
        asyncio.run(_run_login())
        return

    if mode == "organizer":
        asyncio.run(
            _run_organizer(
                host=args.host,
                port=args.port,
                cache_dir=args.cache_dir,
            )
        )
        return

    if mode == "ingest" and args.once:
        # On-demand single poll cycle (``ingest --once [--backfill] [--notebook X]``).
        service = IngestService(config)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                service.ingest_once(
                    process_existing_on_start=bool(args.backfill),
                    notebooks=args.notebook,
                )
            )
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        return

    if mode == "ingest" and not args.once and (args.backfill or args.notebook):
        # --backfill / --notebook are --once modifiers; without --once the
        # continuous-loop daemon ignores them. Warn so this is not silent.
        log.warning(
            "ingest_flags_require_once_ignored",
            backfill=args.backfill,
            notebook=args.notebook,
        )

    if mode == "service":
        service: Any = SupernoteService(config)
    elif mode == "enrich":
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
