"""ABOUTME: Regression tests for the 2026-10-26 pipeline wedge.
ABOUTME: Bounds every unbounded wait that let one stalled cloud request
leak a thread per poll until the process hit its thread ceiling.

Wedge anatomy (organizer 49,885 x "can't start new thread"):
  1. browser-side fetch in _api_call's page.evaluate had NO timeout -> a
     stalled cloud.supernote.com request held the cross-process cloud-api.lock
     forever (ingest/organizer share it);
  2. every other request blocked forever acquiring that lock;
  3. ThreadingHTTPServer spawns one thread per request and each hung in
     run_coroutine_threadsafe(...).result() with no deadline -> thread pile-up
     -> ceiling -> RuntimeError storm; BrokenPipe noise from poller timeouts.
"""

from __future__ import annotations

import asyncio
import fcntl
import inspect
from http.server import ThreadingHTTPServer
from unittest.mock import AsyncMock, Mock

import pytest

from paia_supernote import main as paia_main
from paia_supernote.uploader import SupernoteUploader


class TestApiCallFetchDeadline:
    """The browser-side fetch must carry an abort deadline."""

    @pytest.mark.asyncio
    async def test_fetch_uses_abort_signal_timeout(self, tmp_path, monkeypatch):
        uploader = SupernoteUploader()
        monkeypatch.setattr(
            SupernoteUploader, "CLOUD_API_LOCK_FILE", tmp_path / "cloud-api.lock"
        )
        captured: dict = {}

        async def fake_evaluate(script, *_args, **_kwargs):
            captured["script"] = script
            return {"status": 200, "body": {}}

        page = Mock()
        page.evaluate = fake_evaluate
        uploader.page = page

        await uploader._api_call("/api/file/list/query", {})

        script = captured["script"]
        assert "AbortSignal.timeout" in script, (
            "page.evaluate fetch must abort on a deadline so a stalled "
            "cloud request can never hang the evaluate (and the lock) forever"
        )


class TestCloudApiLockDeadline:
    """Lock acquisition must fail fast instead of blocking forever."""

    @pytest.mark.asyncio
    async def test_lock_acquisition_times_out_when_held_elsewhere(
        self, tmp_path, monkeypatch
    ):
        lock_path = tmp_path / "cloud-api.lock"
        monkeypatch.setattr(SupernoteUploader, "CLOUD_API_LOCK_FILE", lock_path)
        monkeypatch.setattr(SupernoteUploader, "CLOUD_API_LOCK_TIMEOUT", 0.2)
        uploader = SupernoteUploader()

        # flock is per open-file-description: a second fd in the same process
        # blocks the uploader exactly like another process would.
        blocker = lock_path.open("a+")
        try:
            fcntl.flock(blocker.fileno(), fcntl.LOCK_EX)
            with pytest.raises(RuntimeError, match="cloud-api lock"):
                async with uploader._cloud_api_lock():
                    pass
        finally:
            fcntl.flock(blocker.fileno(), fcntl.LOCK_UN)
            blocker.close()

    @pytest.mark.asyncio
    async def test_lock_still_works_when_free(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            SupernoteUploader, "CLOUD_API_LOCK_FILE", tmp_path / "cloud-api.lock"
        )
        monkeypatch.setattr(SupernoteUploader, "CLOUD_API_LOCK_TIMEOUT", 2.0)
        uploader = SupernoteUploader()
        async with uploader._cloud_api_lock():
            pass  # acquired + released without error


class TestStopIsBounded:
    """stop() must not hang when the playwright transport is wedged."""

    @pytest.mark.asyncio
    async def test_stop_returns_when_closes_hang(self, monkeypatch):
        monkeypatch.setattr(SupernoteUploader, "STOP_TIMEOUT", 0.1)
        uploader = SupernoteUploader()

        async def hang_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        context = Mock()
        context.storage_state = hang_forever
        context.close = hang_forever
        browser = Mock()
        browser.close = hang_forever
        playwright = Mock()
        playwright.stop = hang_forever

        uploader.context = context
        uploader.browser = browser
        uploader._playwright = playwright

        await asyncio.wait_for(uploader.stop(), timeout=2.0)  # returns, not hangs
        assert uploader.context is None
        assert uploader.browser is None
        assert uploader._playwright is None


class TestLauncherLoopRunnerDeadline:
    """Organizer request threads must drain on a deadline, not hang forever."""

    @pytest.mark.asyncio
    async def test_result_timeout_raises_timeout_error(self):
        loop = asyncio.get_running_loop()
        runner = paia_main.make_launcher_loop_runner(loop, timeout=0.1)

        async def never():
            await asyncio.Event().wait()

        # Model production: the runner blocks a handler thread, not the loop.
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(runner, never())

    @pytest.mark.asyncio
    async def test_result_returns_when_coroutine_completes(self):
        loop = asyncio.get_running_loop()
        runner = paia_main.make_launcher_loop_runner(loop, timeout=5.0)

        async def quick():
            return 42

        assert await asyncio.to_thread(runner, quick()) == 42


class TestQuietDisconnectNoise:
    """Client-disconnect BrokenPipe/ConnectionReset must not spam tracebacks."""

    def test_handle_error_silences_broken_pipe(self):
        server = paia_main.QuietThreadingHTTPServer(
            ("127.0.0.1", 0), lambda *a, **k: None  # handler class never used here
        )
        try:
            server.socket.close()  # do not hold the probe port open
            try:
                raise BrokenPipeError
            except BrokenPipeError:
                server.handle_error(None, ("127.0.0.1", 1))  # must not raise/print
        finally:
            server.server_close()

    def test_quiet_server_is_threading_http_server(self):
        assert issubclass(paia_main.QuietThreadingHTTPServer, ThreadingHTTPServer)
