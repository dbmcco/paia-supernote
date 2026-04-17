"""Tests for paia-supernote main service entrypoint."""

from __future__ import annotations

import asyncio
import signal
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.main import (
    DEFAULT_CONFIG,
    SupernoteService,
    build_parser,
    load_config,
    main,
    render_status,
)
from paia_supernote.ingest_service import IngestService
from paia_supernote.enrich_service import EnrichService
from paia_supernote.page_state import PageStateStore


# -- Config loading -----------------------------------------------------------


class TestLoadConfig:
    """Config loaded from TOML correctly, with env var overrides."""

    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        config = load_config(config_path=tmp_path / "nonexistent.toml")
        assert config["events_url"] == "http://localhost:3511"
        assert config["folio_url"] == "http://localhost:3512"
        assert config["work_url"] == "http://localhost:3560"
        assert config["state_db_path"].endswith("supernote-state.db")
        assert config["vision_backend"] == "zai"
        assert config["rewrite_backend"] == "zai"
        assert config["zai_vision_model"] == "glm-4.5v"
        assert config["zai_text_model"] == "glm-5.1"
        assert "agent_mappings" in config

    def test_loads_toml_file(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(textwrap.dedent("""\
            [supernote]
            poll_interval = 30

            [services]
            events_url = "http://events:9000"
            folio_url = "http://folio:9001"
        """))

        config = load_config(config_path=cfg_file)
        assert config["poll_interval"] == 30
        assert config["events_url"] == "http://events:9000"
        assert config["folio_url"] == "http://folio:9001"
        # Unset keys keep defaults
        assert config["work_url"] == "http://localhost:3560"

    def test_env_vars_override_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(textwrap.dedent("""\
            [services]
            events_url = "http://toml-events:1111"
        """))
        monkeypatch.setenv("PAIA_EVENTS_URL", "http://env-events:2222")

        config = load_config(config_path=cfg_file)
        assert config["events_url"] == "http://env-events:2222"

    def test_env_var_poll_interval(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPERNOTE_POLL_INTERVAL", "120")
        config = load_config(config_path=tmp_path / "nope.toml")
        assert config["poll_interval"] == 120

    def test_agent_mappings_from_toml(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(textwrap.dedent("""\
            [agents.Sam]
            font = "Comic Sans"
            notebook = "Fun"
        """))

        config = load_config(config_path=cfg_file)
        assert config["agent_mappings"]["Sam"]["font"] == "Comic Sans"

    def test_state_db_path_can_be_loaded_from_file_and_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(textwrap.dedent("""\
            [supernote]
            state_db_path = "/tmp/from-file.db"
        """))

        config = load_config(config_path=cfg_file)
        assert config["state_db_path"] == "/tmp/from-file.db"

        monkeypatch.setenv("SUPERNOTE_STATE_DB_PATH", "/tmp/from-env.db")
        config = load_config(config_path=cfg_file)
        assert config["state_db_path"] == "/tmp/from-env.db"

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
    """main() starts without exception (mock all I/O); cloud poller and events loop both started."""

    @pytest.mark.asyncio
    async def test_start_invokes_all_components(self, tmp_path: Path) -> None:
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

        # Schedule stop after start runs
        async def stop_after_start() -> None:
            await asyncio.sleep(0.05)
            await service.stop()

        await asyncio.gather(service.start(), stop_after_start())

        service.events.start.assert_awaited_once()
        service.events.register_write_handler.assert_called_once()
        service.uploader.start.assert_awaited_once()
        service.cloud_poller.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_invokes_all_components(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service.events = MagicMock()
        service.events.stop = AsyncMock()
        service.uploader = MagicMock()
        service.uploader.stop = AsyncMock()
        service.cloud_poller = MagicMock()
        service.cloud_poller.stop = AsyncMock()
        service.tasks_sync = MagicMock()
        service.tasks_sync.stop = AsyncMock()

        await service.stop()

        service.tasks_sync.stop.assert_awaited_once()
        service.cloud_poller.stop.assert_awaited_once()
        service.uploader.stop.assert_awaited_once()
        service.events.stop.assert_awaited_once()


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
    async def test_ingest_service_persists_latest_page_revision(self, tmp_path: Path) -> None:
        from paia_supernote.ingest_service import IngestService

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
        from paia_supernote.enrich_service import EnrichService

        config = dict(DEFAULT_CONFIG)
        config["state_db_path"] = str(tmp_path / "state.db")

        store = PageStateStore(tmp_path / "state.db")
        store.init_schema()
        store.upsert_ocr_page("Quick", 19, "rev-1", "raw v1", "glm-4.5v")

        async def mutate_revision_then_return(*, notebook: str, page: int, raw_text: str):
            store.upsert_ocr_page(notebook, page, "rev-2", "raw v2", "glm-4.5v")
            return SimpleNamespace(
                markdown="# Updated",
                diagram={"kind": "scene", "scene": {"nodes": [], "edges": []}, "render_version": "1"},
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
        """content_type=task_page_curate is routed to TaskCurator, not the generic write path.

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
        mock_task_curator.handle_write_requested.assert_awaited_once_with({
            "agent": "Sam",
            "notebook": "Quick",
            "content": "some new content",
            "content_type": "task_page_curate",
            "notebook_bytes": b"mock_notebook_bytes",
        })
        mock_uploader.download_notebook.assert_awaited_once_with("Quick.note")
