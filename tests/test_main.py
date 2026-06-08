"""Tests for paia-supernote main service entrypoint."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.enrich_service import EnrichService
from paia_supernote.ingest_service import IngestService
from paia_supernote.main import (
    DEFAULT_CONFIG,
    NotebookConflictError,
    SupernoteService,
    _filing_destination_map,
    _is_conflict_name,
    _watched_notebooks,
    build_parser,
    load_config,
    main,
    render_status,
)
from paia_supernote.model_config import supernote_zai_credential_env_var
from paia_supernote.page_state import PageStateStore
from paia_supernote.uploader import SupernoteUploadConflictError

# -- Config loading -----------------------------------------------------------


class TestLoadConfig:
    """Config loaded from TOML correctly, with env var overrides."""

    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        with patch(
            "paia_supernote.main.resolve_supernote_zai_api_key", return_value=None
        ):
            config = load_config(config_path=tmp_path / "nonexistent.toml")
        assert config["events_url"] == "http://localhost:3511"
        assert config["folio_url"] == "http://localhost:3512"
        assert config["work_url"] == "http://localhost:3560"
        assert config["state_db_path"].endswith("supernote-state.db")
        assert config["vision_backend"] == "zai"
        assert config["rewrite_backend"] == "zai"
        assert config["zai_api_key"] is None
        assert config["zai_vision_model"] == "x-ai/grok-4.3"
        assert config["zai_text_model"] == "x-ai/grok-4.3"
        assert "agent_mappings" in config

    def test_loads_toml_file(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [supernote]
            poll_interval = 30

            [services]
            events_url = "http://events:9000"
            folio_url = "http://folio:9001"
        """)
        )

        config = load_config(config_path=cfg_file)
        assert config["poll_interval"] == 30
        assert config["events_url"] == "http://events:9000"
        assert config["folio_url"] == "http://folio:9001"
        # Unset keys keep defaults
        assert config["work_url"] == "http://localhost:3560"

    def test_env_vars_override_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [services]
            events_url = "http://toml-events:1111"
        """)
        )
        monkeypatch.setenv("PAIA_EVENTS_URL", "http://env-events:2222")

        config = load_config(config_path=cfg_file)
        assert config["events_url"] == "http://env-events:2222"

    def test_env_var_poll_interval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SUPERNOTE_POLL_INTERVAL", "120")
        config = load_config(config_path=tmp_path / "nope.toml")
        assert config["poll_interval"] == 120

    def test_agent_mappings_from_toml(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [agents.Sam]
            font = "Comic Sans"
            notebook = "Fun"
        """)
        )

        config = load_config(config_path=cfg_file)
        assert config["agent_mappings"]["Sam"]["font"] == "Comic Sans"

    def test_filing_config_loads_explicit_sources_and_destinations(
        self, tmp_path: Path
    ) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [filing]
            enabled = true
            dry_run = false
            source_notebooks = ["LFW", "MGMT"]
            destination_notebooks = ["Navicyte", "Synth"]

            [filing.destination_map]
            "LFW HEC" = "LFW"
        """)
        )

        config = load_config(config_path=cfg_file)

        assert config["filing_enabled"] is True
        assert config["filing_dry_run"] is False
        assert _watched_notebooks(config) >= {
            "Walk",
            "tasks",
            "LFW",
            "MGMT",
            "Navicyte",
            "Synth",
        }
        assert _filing_destination_map(config)["lfw-hec"] == "LFW"
        assert _filing_destination_map(config)["navicyte"] == "Navicyte"

    def test_state_db_path_can_be_loaded_from_file_and_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [supernote]
            state_db_path = "/tmp/from-file.db"
        """)
        )

        config = load_config(config_path=cfg_file)
        assert config["state_db_path"] == "/tmp/from-file.db"

        monkeypatch.setenv("SUPERNOTE_STATE_DB_PATH", "/tmp/from-env.db")
        config = load_config(config_path=cfg_file)
        assert config["state_db_path"] == "/tmp/from-env.db"

    def test_service_cloud_poller_can_be_disabled_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPERNOTE_SERVICE_CLOUD_POLLER_ENABLED", "false")

        config = load_config(config_path=tmp_path / "nonexistent.toml")

        assert config["service_cloud_poller_enabled"] is False

    def test_service_uploader_can_be_lazy_started_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPERNOTE_SERVICE_UPLOADER_START_MODE", "lazy")

        config = load_config(config_path=tmp_path / "nonexistent.toml")

        assert config["service_uploader_start_mode"] == "lazy"

    def test_zai_api_key_can_be_loaded_from_file_and_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SUPERNOTE_ZAI_API_KEY", raising=False)
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        monkeypatch.delenv(supernote_zai_credential_env_var(), raising=False)
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
            [supernote]
            zai_api_key = "file-token"
        """)
        )

        config = load_config(config_path=cfg_file)
        assert config["zai_api_key"] == "file-token"

        monkeypatch.setenv(supernote_zai_credential_env_var(), "supernote-env-token")
        config = load_config(config_path=cfg_file)
        assert config["zai_api_key"] == "supernote-env-token"

        monkeypatch.delenv(supernote_zai_credential_env_var())
        monkeypatch.setenv("ZAI_API_KEY", "legacy-env-token")
        config = load_config(config_path=cfg_file)
        assert config["zai_api_key"] == "file-token"

    def test_default_config_includes_state_db_path(self, tmp_path: Path) -> None:
        config = load_config(config_path=tmp_path / "missing.toml")
        assert config["state_db_path"].endswith("supernote-state.db")


# -- CLI parser ---------------------------------------------------------------


class TestBuildParser:
    def test_help_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "paia-supernote" in captured.out

    def test_config_arg(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--config", "/tmp/test.toml"])
        assert args.config == Path("/tmp/test.toml")

    def test_subcommands(self) -> None:
        parser = build_parser()
        assert parser.parse_args(["ingest"]).mode == "ingest"
        assert parser.parse_args(["enrich"]).mode == "enrich"
        assert parser.parse_args(["status"]).mode == "status"


# -- Service lifecycle --------------------------------------------------------


def _make_service(tmp_path: Path) -> SupernoteService:
    """Create a service with test config."""
    config = dict(DEFAULT_CONFIG)
    return SupernoteService(config=config)


class TestServiceStart:
    """main() starts without exception with all I/O mocked."""

    @pytest.mark.asyncio
    async def test_start_invokes_all_components(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        calls: list[str] = []
        service.events = MagicMock()
        service.events.start = AsyncMock(side_effect=lambda: calls.append("events"))
        service.events.stop = AsyncMock()
        service.events.register_write_handler = MagicMock(
            side_effect=lambda _handler: calls.append("handler")
        )
        service.uploader = MagicMock()
        service.uploader.start = AsyncMock(side_effect=lambda: calls.append("uploader"))
        service.uploader.stop = AsyncMock()
        service.cloud_poller = MagicMock()
        service.cloud_poller.start = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.start = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        async def wait_forever() -> None:
            await asyncio.Future()

        service.cloud_poller.wait = AsyncMock(side_effect=wait_forever)

        # Schedule stop after start runs
        async def stop_after_start() -> None:
            await asyncio.sleep(0.05)
            await service.stop()

        await asyncio.gather(service.start(), stop_after_start())

        service.events.start.assert_awaited_once()
        service.events.register_write_handler.assert_called_once()
        service.uploader.start.assert_awaited_once()
        service.cloud_poller.start.assert_called_once()
        assert calls[:3] == ["uploader", "handler", "events"]

    @pytest.mark.asyncio
    async def test_start_skips_cloud_poller_when_disabled(
        self, tmp_path: Path
    ) -> None:
        config = dict(DEFAULT_CONFIG)
        config["service_cloud_poller_enabled"] = False
        config["state_db_path"] = str(tmp_path / "state.db")
        service = SupernoteService(config=config)
        service.events = MagicMock()
        service.events.start = AsyncMock()
        service.events.stop = AsyncMock()
        service.events.register_write_handler = MagicMock()
        service.uploader = MagicMock()
        service.uploader.start = AsyncMock()
        service.uploader.stop = AsyncMock()
        service.cloud_poller = MagicMock()
        service.cloud_poller.start = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.start = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        async def stop_after_start() -> None:
            await asyncio.sleep(0.05)
            await service.stop()

        await asyncio.gather(service.start(), stop_after_start())

        service.cloud_poller.start.assert_not_called()
        service.cloud_poller.stop.assert_not_awaited()
        service.events.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_skips_uploader_when_lazy_and_poller_disabled(
        self, tmp_path: Path
    ) -> None:
        config = dict(DEFAULT_CONFIG)
        config["service_cloud_poller_enabled"] = False
        config["service_uploader_start_mode"] = "lazy"
        config["state_db_path"] = str(tmp_path / "state.db")
        service = SupernoteService(config=config)
        service.events = MagicMock()
        service.events.start = AsyncMock()
        service.events.stop = AsyncMock()
        service.events.register_write_handler = MagicMock()
        service.uploader = MagicMock()
        service.uploader.start = AsyncMock()
        service.uploader.stop = AsyncMock()
        service.cloud_poller = MagicMock()
        service.cloud_poller.start = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.start = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        async def stop_after_start() -> None:
            await asyncio.sleep(0.05)
            await service.stop()

        await asyncio.gather(service.start(), stop_after_start())

        service.uploader.start.assert_not_awaited()
        service.uploader.stop.assert_not_awaited()
        service.events.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_request_lazy_starts_service_uploader(self) -> None:
        config = dict(DEFAULT_CONFIG)
        config["service_uploader_start_mode"] = "lazy"
        service = SupernoteService(config=config)
        service.uploader = MagicMock()
        service.uploader.page = None
        service.uploader.start = AsyncMock()
        service.events = AsyncMock()
        service._replace_pages_with_uploader = AsyncMock(return_value=True)

        await service._handle_write_request(
            {
                "agent": "Sam",
                "notebook": "Walk",
                "content_type": "replace_pages",
                "pages": [{"agent": "Sam", "content": "Page 1"}],
            }
        )

        service.uploader.start.assert_awaited_once()
        service._replace_pages_with_uploader.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_invokes_all_components(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service.events = MagicMock()
        service.events.stop = AsyncMock()
        service.uploader = MagicMock()
        service.uploader.stop = AsyncMock()
        service._uploader_started = True
        service.cloud_poller = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        await service.stop()

        service.tasks_sync.stop.assert_awaited_once()
        service.cloud_poller.stop.assert_awaited_once()
        service.uploader.stop.assert_awaited_once()
        service.events.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_raises_when_cloud_poller_task_dies(
        self, tmp_path: Path
    ) -> None:
        service = _make_service(tmp_path)
        service.events = MagicMock()
        service.events.start = AsyncMock()
        service.events.stop = AsyncMock()
        service.events.register_write_handler = MagicMock()
        service.uploader = MagicMock()
        service.uploader.start = AsyncMock()
        service.uploader.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.start = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        class CrashingPoller:
            def __init__(self) -> None:
                self.stop = AsyncMock()
                self._task: asyncio.Task[None] | None = None

            def start(self) -> None:
                async def crash() -> None:
                    await asyncio.sleep(0)
                    raise RuntimeError("poller died")

                self._task = asyncio.create_task(crash())

            async def wait(self) -> None:
                assert self._task is not None
                await self._task

        service.cloud_poller = CrashingPoller()

        with pytest.raises(RuntimeError, match="poller died"):
            await asyncio.wait_for(service.start(), timeout=0.2)


class TestNoteChangeProcessing:
    @pytest.mark.asyncio
    async def test_walk_note_changes_publish_feedback_evidence(
        self, tmp_path: Path
    ) -> None:
        service = _make_service(tmp_path)
        service.reader = AsyncMock()
        service.reader.process_file.return_value = [
            SimpleNamespace(
                notebook="Walk",
                page_num=0,
                text="Prior Gene context exists.",
                content_type="general",
                checkboxes=[],
            )
        ]
        service.events = MagicMock()
        service.events.publish_note_transcribed = AsyncMock()
        service.events.publish_walk_feedback_detected = AsyncMock()

        await service._process_file_change("Walk", b"walk-bytes")

        service.events.publish_walk_feedback_detected.assert_awaited_once_with(
            notebook="Walk",
            page=0,
            text="Prior Gene context exists.",
        )

    @pytest.mark.asyncio
    async def test_configured_filing_source_runs_filing_service(
        self,
        tmp_path: Path,
    ) -> None:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "filing_enabled": True,
                "filing_dry_run": False,
                "filing_ledger_db_path": str(tmp_path / "filing.db"),
                "filing_source_notebooks": ["LFW"],
                "filing_destination_notebooks": ["MGMT"],
            }
        )
        service = SupernoteService(config=config)
        service.reader = AsyncMock()
        service.reader.process_file.return_value = []

        with patch("paia_supernote.main.QuickFilingService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.run_once.return_value = {
                "status": "ok",
                "candidate_count": 1,
                "dry_run": False,
            }
            mock_cls.return_value = mock_service

            await service._process_file_change("LFW", b"lfw-bytes")

        mock_cls.assert_called_once()
        mock_service.run_once.assert_awaited_once_with(source_bytes=b"lfw-bytes")

    @pytest.mark.asyncio
    async def test_configured_filing_source_matches_cloud_name_case_insensitively(
        self,
        tmp_path: Path,
    ) -> None:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "filing_enabled": True,
                "filing_dry_run": False,
                "filing_ledger_db_path": str(tmp_path / "filing.db"),
                "filing_source_notebooks": ["MGMT"],
                "filing_destination_notebooks": ["Synth"],
            }
        )
        service = SupernoteService(config=config)
        service.reader = AsyncMock()
        service.reader.process_file.return_value = []

        with patch("paia_supernote.main.QuickFilingService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.run_once.return_value = {
                "status": "ok",
                "candidate_count": 1,
                "dry_run": False,
            }
            mock_cls.return_value = mock_service

            await service._process_file_change("Mgmt", b"mgmt-bytes")

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["source_notebook"] == "Mgmt"
        mock_service.run_once.assert_awaited_once_with(source_bytes=b"mgmt-bytes")

    @pytest.mark.asyncio
    async def test_configured_filing_still_runs_when_reader_processing_fails(
        self,
        tmp_path: Path,
    ) -> None:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "filing_enabled": True,
                "filing_dry_run": False,
                "filing_ledger_db_path": str(tmp_path / "filing.db"),
                "filing_source_notebooks": ["Quick"],
                "filing_destination_notebooks": ["Mgmt"],
            }
        )
        service = SupernoteService(config=config)
        service.reader = AsyncMock()
        service.reader.process_file.side_effect = RuntimeError("vision unavailable")

        with patch("paia_supernote.main.QuickFilingService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.run_once.return_value = {
                "status": "ok",
                "candidate_count": 1,
                "dry_run": False,
            }
            mock_cls.return_value = mock_service

            await service._process_file_change("Quick", b"quick-bytes")

        mock_cls.assert_called_once()
        mock_service.run_once.assert_awaited_once_with(source_bytes=b"quick-bytes")


# -- SIGTERM clean shutdown ---------------------------------------------------


class TestSignalHandling:
    """SIGTERM triggers clean shutdown."""

    @pytest.mark.asyncio
    async def test_sigterm_triggers_shutdown(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service.events = MagicMock()
        service.events.start = AsyncMock()
        service.events.stop = AsyncMock()
        service.events.register_write_handler = MagicMock()
        service.uploader = MagicMock()
        service.uploader.start = AsyncMock()
        service.uploader.stop = AsyncMock()
        service.cloud_poller = MagicMock()
        service.cloud_poller.start = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.start = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        async def send_sigterm() -> None:
            await asyncio.sleep(0.05)
            # Directly call stop to simulate signal handling
            await service.stop()

        await asyncio.gather(service.start(), send_sigterm())

        # If we got here, shutdown was clean
        service.cloud_poller.stop.assert_awaited_once()
        service.events.stop.assert_awaited_once()
        service.uploader.stop.assert_awaited_once()


# -- main() entrypoint -------------------------------------------------------


class TestMainEntrypoint:
    """main() function wires everything together."""

    def test_parser_supports_ingest_enrich_and_status(self) -> None:
        parser = build_parser()
        assert parser.parse_args(["ingest"]).mode == "ingest"
        assert parser.parse_args(["enrich"]).mode == "enrich"
        assert parser.parse_args(["status"]).mode == "status"

    @patch("paia_supernote.main.load_config")
    @patch("paia_supernote.main._configure_logging")
    def test_main_ingest_mode_starts_ingest_service(
        self,
        mock_logging: MagicMock,
        mock_load_config: MagicMock,
    ) -> None:
        mock_load_config.return_value = dict(DEFAULT_CONFIG)

        async def fake_start() -> None:
            pass

        with patch("paia_supernote.main.IngestService") as mock_cls:
            mock_service = MagicMock()
            mock_service.start = MagicMock(side_effect=fake_start)
            mock_service.stop = AsyncMock()
            mock_cls.return_value = mock_service

            main(argv=["ingest"])

        mock_logging.assert_called_once()
        mock_load_config.assert_called_once()
        mock_cls.assert_called_once()


class TestStatusRendering:
    def test_render_status_reports_dirty_and_error_counts(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.db"
        store = PageStateStore(db_path)
        store.init_schema()
        store.upsert_ocr_page("Quick", 19, "rev-1", "raw", "glm-4.5v")
        store.mark_failed(
            notebook="Quick",
            page=19,
            stage="enrich",
            error="timeout",
            retry_delay_seconds=30,
        )

        rendered = render_status(db_path)

        assert "dirty_pages=1" in rendered
        assert "ocr_errors=0" in rendered
        assert "enrich_errors=1" in rendered


# -- IngestService / EnrichService --------------------------------------------


class TestIngestService:
    """IngestService persists page revisions to SQLite on each note change."""

    @pytest.mark.asyncio
    async def test_ingest_service_persists_latest_page_revision(
        self, tmp_path: Path
    ) -> None:

        config = dict(DEFAULT_CONFIG)
        config["state_db_path"] = str(tmp_path / "state.db")

        mock_reader = AsyncMock()
        mock_reader.process_file.return_value = [
            SimpleNamespace(notebook="Quick", page_num=19, text="raw v1"),
        ]
        mock_uploader = AsyncMock()
        mock_poller = MagicMock()

        service = IngestService(
            config=config,
            reader=mock_reader,
            uploader=mock_uploader,
            cloud_poller=mock_poller,
        )

        await service._on_note_changed("Quick", b"note-bytes", 123456)

        row = service.page_state.get_page("Quick", 19)
        assert row.raw_text == "raw v1"
        assert row.source_revision.endswith(":19")


class TestEnrichService:
    """EnrichService drops stale in-flight revisions before writing to Folio."""

    @pytest.mark.asyncio
    async def test_enrich_service_discards_stale_revision_before_folio_write(
        self, tmp_path: Path
    ) -> None:

        config = dict(DEFAULT_CONFIG)
        config["state_db_path"] = str(tmp_path / "state.db")

        store = PageStateStore(tmp_path / "state.db")
        store.init_schema()
        store.upsert_ocr_page("Quick", 19, "rev-1", "raw v1", "glm-4.5v")

        async def mutate_revision_then_return(
            *, notebook: str, page: int, raw_text: str
        ):
            store.upsert_ocr_page(notebook, page, "rev-2", "raw v2", "glm-4.5v")
            return SimpleNamespace(
                markdown="# Updated",
                diagram={
                    "kind": "scene",
                    "scene": {"nodes": [], "edges": []},
                    "render_version": "1",
                },
            )

        mock_enricher = AsyncMock()
        mock_enricher.enrich_page.side_effect = mutate_revision_then_return
        mock_folio = AsyncMock()

        service = EnrichService(
            config=config,
            page_state=store,
            enricher=mock_enricher,
            folio_client=mock_folio,
        )
        wrote = await service.run_once()

        assert wrote is False
        mock_folio.assert_not_awaited()


# -- main() entrypoint (existing tests below) --------------------------------


class TestMainEntrypoint2:
    @pytest.mark.asyncio
    async def test_write_request_delegates_task_page_curate_to_task_curator(
        self,
        tmp_path: Path,
    ) -> None:
        """content_type=task_page_curate routes to TaskCurator.

        Regression: @patch on instance attributes (uploader, task_curator) raises
        AttributeError because they are set in __init__, not defined at class level.
        The fix is to replace them directly on the service instance after construction.
        """
        # Arrange — create service then replace instance attrs with mocks
        service = SupernoteService(config=DEFAULT_CONFIG)
        mock_uploader = AsyncMock()
        mock_task_curator = AsyncMock()
        mock_uploader.download_notebook = AsyncMock(return_value=b"mock_notebook_bytes")
        service.uploader = mock_uploader
        service.task_curator = mock_task_curator

        event_data = {
            "agent": "Sam",
            "notebook": "Quick",
            "content": "some new content",
            "content_type": "task_page_curate",
        }

        # Act
        await service._handle_write_request(event_data)

        # Assert — TaskCurator received the event with notebook bytes injected
        mock_task_curator.handle_write_requested.assert_awaited_once_with(
            {
                "agent": "Sam",
                "notebook": "Quick",
                "content": "some new content",
                "content_type": "task_page_curate",
                "notebook_bytes": b"mock_notebook_bytes",
            }
        )
        mock_uploader.download_notebook.assert_awaited_once_with("Quick.note")

    @pytest.mark.asyncio
    async def test_write_request_replace_pages_calls_notebook_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        """content_type=replace_pages uses stable page replacement."""
        service = SupernoteService(config=DEFAULT_CONFIG)
        mock_uploader = AsyncMock()
        mock_uploader.download_notebook = AsyncMock(return_value=b"fake_notebook")
        mock_uploader.upload_notebook = AsyncMock(return_value=True)
        service.uploader = mock_uploader

        event_data = {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "pages": [
                {"agent": "Sam", "content": "Page 1"},
                {"agent": "Caroline", "content": "Page 2"},
            ],
        }

        with patch(
            "paia_supernote.main.replace_notebook_pages",
            return_value=b"rebuilt_notebook",
        ) as mock_replace:
            await service._handle_write_request(event_data)

        mock_uploader.download_notebook.assert_awaited_once_with("Quick.note")
        mock_replace.assert_called_once()
        mock_uploader.upload_notebook.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_request_replace_pages_empty_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        """replace_pages with empty pages list should not attempt replacement."""
        service = SupernoteService(config=DEFAULT_CONFIG)
        mock_uploader = AsyncMock()
        service.uploader = mock_uploader

        event_data = {
            "agent": "Sam",
            "notebook": "Quick",
            "content_type": "replace_pages",
            "pages": [],
        }

        await service._handle_write_request(event_data)
        mock_uploader.download_notebook.assert_not_awaited()

    def test_conflict_name_matches_target_notebook_only(self) -> None:
        assert _is_conflict_name("Walk_CONFLICT_20260505115959184.note", "Walk")
        assert not _is_conflict_name("Meetings_CONFLICT_20260505115959184.note", "Walk")
        assert not _is_conflict_name("Walk.note", "Walk")

    @pytest.mark.asyncio
    async def test_replace_pages_conflict_is_failed_without_retry(self) -> None:
        service = SupernoteService(config=DEFAULT_CONFIG)
        service.uploader = AsyncMock()
        service.events = AsyncMock()
        service._replace_pages_with_uploader = AsyncMock(
            side_effect=NotebookConflictError("Walk.note has unresolved cloud conflict")
        )

        await service._handle_write_request(
            {
                "agent": "Sam",
                "notebook": "Walk",
                "content_type": "replace_pages",
                "pages": [{"agent": "Sam", "content": "Page 1"}],
            }
        )

        service._replace_pages_with_uploader.assert_awaited_once()
        service.events.publish_write_failed.assert_awaited_once()
        assert (
            "unresolved cloud conflict"
            in service.events.publish_write_failed.await_args.kwargs["error"]
        )

    @pytest.mark.asyncio
    async def test_replace_pages_upload_conflict_is_failed_without_fresh_retry(
        self,
    ) -> None:
        service = SupernoteService(config=DEFAULT_CONFIG)
        service.uploader = AsyncMock()
        service.events = AsyncMock()
        service._replace_pages_with_uploader = AsyncMock(
            side_effect=SupernoteUploadConflictError(
                "Walk.note has blocking cloud copies: Walk(1).note"
            )
        )

        with patch("paia_supernote.main.SupernoteUploader") as fresh_uploader:
            await service._handle_write_request(
                {
                    "agent": "Sam",
                    "notebook": "Walk",
                    "content_type": "replace_pages",
                    "pages": [{"agent": "Sam", "content": "Page 1"}],
                }
            )

        fresh_uploader.assert_not_called()
        service.events.publish_write_failed.assert_awaited_once()
        assert (
            "blocking cloud copies"
            in service.events.publish_write_failed.await_args.kwargs["error"]
        )
