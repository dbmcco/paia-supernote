"""supernote-ocr must write its own life to the service log.

Why this file exists (task apps/services-bug-supernote-ocr-started-at-08-51-b,
2026-11-15): the monitor measures a service by ``mgmt log <service>
alive|output|finish`` lines. This runner predates that regime — ``svc-wrap`` writes
``start`` once when the daemon comes up and ``finish`` when it exits, and nothing in
between — so a healthy multi-day ingest run reads as silent and files a false bug,
and a REAL silent stop would look exactly the same. The manager's spot-check found
the process up since 8 November with events flowing and page 6 re-inked at
13:04:09Z: the ingest was fine, only invisible.

So the runner writes its own lifecycle:

* ``start`` when a job (an OCR batch) begins, naming what started;
* ``output`` per page finished, naming the pages done;
* ``alive`` at least once per five minutes while a long run is in progress;
* ``finish`` when the batch ends;
* ``error`` with the exit code for a caught failure.

And the rule that makes this safe to add to a live service: a logging call that
fails must never break the ingest. Every call here is best-effort and silent on
failure.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from paia_supernote import svc_log
from paia_supernote.ingest_service import IngestService
from tests.test_ingest_change_ledger import FlakyReader, ListingUploader

FIVE_MINUTES = 300.0


class RecordingRunner:
    """A ``mgmt log`` stand-in that records the call instead of running it."""

    def __init__(self, *, rc: int = 0, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.rc = rc
        self.raises = raises

    def __call__(self, argv, env) -> int:
        self.calls.append((list(argv), dict(env)))
        if self.raises is not None:
            raise self.raises
        return self.rc

    @property
    def kinds(self) -> list[str]:
        return [argv[3] for argv, _ in self.calls]

    @property
    def details(self) -> list[str | None]:
        return [argv[4] if len(argv) > 4 else None for argv, _ in self.calls]


def _log(runner: RecordingRunner, **kwargs) -> svc_log.ServiceLog:
    kwargs.setdefault("mgmt_bin", "/opt/test/mgmt")
    return svc_log.ServiceLog(runner=runner, **kwargs)


def _svc(
    tmp_path: Path,
    reader: FlakyReader,
    service_log: svc_log.ServiceLog,
    *,
    uploader: ListingUploader | None = None,
    config: dict | None = None,
) -> IngestService:
    base = {
        "events_url": "http://events.invalid",
        "state_db_path": str(tmp_path / "state.db"),
        "zai_vision_model": "glm-test-vision",
        "poll_interval": 999,
        "folio_sync_notebooks": [],
        "cloud_change_ledger_notebooks": ["Quick"],
    }
    base.update(config or {})
    return IngestService(
        config=base,
        reader=reader,  # type: ignore[arg-type]
        uploader=uploader,
        service_log=service_log,
    )


class FakeEvents:
    """Capture publishes instead of talking to the bus."""

    async def publish_ocr_retry_status(self, *, stalled, entries):  # pragma: no cover
        pass

    async def publish_ocr_blank_terminal(self, *, entries):  # pragma: no cover
        pass


# --- the log line itself ---------------------------------------------------


def test_start_names_the_service_and_what_started() -> None:
    runner = RecordingRunner()

    assert _log(runner).start("OCR batch Quick pages=2") is True

    argv, _ = runner.calls[0]
    assert argv == [
        "/opt/test/mgmt",
        "log",
        "supernote-ocr",
        "start",
        "OCR batch Quick pages=2",
    ]


def test_the_actor_is_mgmt_actor_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MGMT_ACTOR", "apps-svc-log")
    runner = RecordingRunner()

    _log(runner).alive()

    _, env = runner.calls[0]
    assert env["MGMT_ACTOR"] == "apps-svc-log"


def test_the_actor_falls_back_to_the_service_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MGMT_ACTOR", raising=False)
    runner = RecordingRunner()

    _log(runner).alive()

    _, env = runner.calls[0]
    assert env["MGMT_ACTOR"] == "supernote-ocr"


def test_the_binary_can_be_pointed_elsewhere_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MGMT_BIN", "/elsewhere/mgmt")
    runner = RecordingRunner()

    svc_log.ServiceLog(runner=runner).output("1 page")

    assert runner.calls[0][0][0] == "/elsewhere/mgmt"


def test_an_error_carries_its_exit_code() -> None:
    runner = RecordingRunner()

    _log(runner).error("cloud session expired", exit_code=3)

    argv, _ = runner.calls[0]
    assert argv[3] == "error"
    assert argv[4] == "cloud session expired"
    assert argv[5:] == ["--exit", "3"]


@pytest.mark.parametrize(
    "raises",
    [
        OSError("no mgmt"),
        subprocess.TimeoutExpired(cmd="mgmt", timeout=15),
        RuntimeError("anything at all"),
    ],
)
def test_a_logging_failure_never_raises(raises: BaseException) -> None:
    """The rule that makes this safe on a live ingest: logging cannot break it."""
    runner = RecordingRunner(raises=raises)

    result = _log(runner).output("1 page")

    assert result is False


def test_a_refused_log_call_is_reported_not_raised() -> None:
    runner = RecordingRunner(rc=2)

    assert _log(runner).finish("exit 2") is False


def test_an_unknown_kind_is_not_written() -> None:
    runner = RecordingRunner()

    assert _log(runner).line("heartbeat") is False
    assert runner.calls == []


def test_the_default_binary_is_the_mgmt_cli() -> None:
    assert svc_log.DEFAULT_MGMT_BIN == "/Users/braydon/projects/personal/mgmt/mgmt"
    assert svc_log.DEFAULT_ALIVE_INTERVAL_SECONDS == FIVE_MINUTES


def test_the_suite_never_writes_the_live_service_log() -> None:
    """The conftest guard, pinned: an un-injected ServiceLog cannot reach mgmt."""
    assert svc_log.ServiceLog().alive("test") is False


# --- how the runner uses it ------------------------------------------------


@pytest.mark.asyncio
async def test_a_batch_logs_start_per_page_output_and_finish(tmp_path: Path) -> None:
    runner = RecordingRunner()
    svc = _svc(
        tmp_path,
        FlakyReader(
            snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages=set()
        ),
        _log(runner),
    )
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)

    assert runner.kinds[0] == "start"
    assert "Quick" in runner.details[0]
    assert runner.kinds[-1] == "finish"
    assert runner.kinds.count("output") == 2
    assert "2" in runner.details[-1]


@pytest.mark.asyncio
async def test_a_batch_with_nothing_due_writes_nothing(tmp_path: Path) -> None:
    """Only real work gets a start/finish pair, so the log stays readable."""
    runner = RecordingRunner()
    svc = _svc(
        tmp_path,
        FlakyReader(snapshots={b"rev1": [("P0", "h0")]}, fail_pages=set()),
        _log(runner),
    )
    svc.events = FakeEvents()
    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    runner.calls.clear()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)

    assert "start" not in runner.kinds
    assert "finish" not in runner.kinds


@pytest.mark.asyncio
async def test_a_sweep_pass_that_did_work_writes_an_output_line(
    tmp_path: Path,
) -> None:
    """A clock-driven pass is progress too: it must move the log off silent."""
    runner = RecordingRunner()
    reader = FlakyReader(
        snapshots={b"rev1": [("P0", "h0"), ("P1", "h1")]}, fail_pages={1}
    )
    uploader = ListingUploader(
        entries=[{"fileName": "Quick.note", "updateTime": 100}],
        downloads={"Quick.note": b"rev1"},
    )
    svc = _svc(tmp_path, reader, _log(runner), uploader=uploader)
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)
    import sqlite3
    from datetime import datetime, timedelta, timezone

    with sqlite3.connect(str(svc.page_state._db_path)) as conn:
        conn.execute(
            "UPDATE page_state SET next_retry_at = ? "
            "WHERE notebook = 'Quick' AND page = 1",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )
    reader.fail_pages = set()
    runner.calls.clear()

    await svc.retry_sweep_once()

    assert "output" in runner.kinds
    assert "1" in runner.details[runner.kinds.index("output")]


@pytest.mark.asyncio
async def test_a_broken_mgmt_never_breaks_the_ingest(tmp_path: Path) -> None:
    """mgmt missing, Postgres down, or a hung psql: the OCR still lands."""
    runner = RecordingRunner(raises=OSError("mgmt is not there"))
    svc = _svc(
        tmp_path,
        FlakyReader(snapshots={b"rev1": [("P0", "h0")]}, fail_pages=set()),
        _log(runner),
    )
    svc.events = FakeEvents()

    await svc._on_cloud_note_changed("Quick", b"rev1", update_time=100)

    assert {p.page_index: p.ocr_status for p in svc.ledger.current_pages("Quick")} == {
        0: "ready"
    }
    assert runner.calls  # it did try, and it did not take the ingest down


@pytest.mark.asyncio
async def test_a_long_run_stays_visible_at_least_once_per_five_minutes(
    tmp_path: Path,
) -> None:
    """The heartbeat, at test speed: the interval is what keeps silence honest."""
    runner = RecordingRunner()
    svc = _svc(
        tmp_path,
        FlakyReader(snapshots={b"rev1": [("P0", "h0")]}, fail_pages=set()),
        _log(runner, alive_interval=0.01),
    )
    svc.events = FakeEvents()
    svc._alive_interval_seconds = 0.01

    task = asyncio.create_task(svc._alive_loop())
    await asyncio.sleep(0.08)
    svc._shutdown_event.set()
    await asyncio.wait_for(task, timeout=2)

    assert runner.kinds.count("alive") >= 2


@pytest.mark.asyncio
async def test_the_heartbeat_stops_when_the_service_stops(tmp_path: Path) -> None:
    runner = RecordingRunner()
    svc = _svc(
        tmp_path,
        FlakyReader(snapshots={b"rev1": [("P0", "h0")]}, fail_pages=set()),
        _log(runner, alive_interval=0.01),
    )
    svc.events = FakeEvents()
    svc._alive_interval_seconds = 0.01

    task = asyncio.create_task(svc._alive_loop())
    await asyncio.sleep(0.05)
    svc._shutdown_event.set()
    await asyncio.wait_for(task, timeout=2)
    beats = runner.kinds.count("alive")

    await asyncio.sleep(0.05)

    assert runner.kinds.count("alive") == beats
