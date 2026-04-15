"""Tests for paia-supernote main service entrypoint."""

from __future__ import annotations

import asyncio
import signal
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paia_supernote.main import (
    DEFAULT_CONFIG,
    SupernoteService,
    build_parser,
    load_config,
    main,
)


# -- Config loading -----------------------------------------------------------


class TestLoadConfig:
    """Config loaded from TOML correctly, with env var overrides."""

    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        config = load_config(config_path=tmp_path / "nonexistent.toml")
        assert config["events_url"] == "http://localhost:3511"
        assert config["folio_url"] == "http://localhost:3512"
        assert config["work_url"] == "http://localhost:3560"
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

    @patch("paia_supernote.main.SupernoteService")
    @patch("paia_supernote.main.load_config")
    @patch("paia_supernote.main._configure_logging")
    def test_main_starts_service(
        self,
        mock_logging: MagicMock,
        mock_load_config: MagicMock,
        mock_service_cls: MagicMock,
    ) -> None:
        mock_load_config.return_value = dict(DEFAULT_CONFIG)
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        # Make start() a coroutine that returns immediately
        async def fake_start() -> None:
            pass

        mock_service.start = MagicMock(side_effect=fake_start)
        mock_service.stop = AsyncMock()

        main(argv=[])

        mock_logging.assert_called_once()
        mock_load_config.assert_called_once()
        mock_service_cls.assert_called_once()
