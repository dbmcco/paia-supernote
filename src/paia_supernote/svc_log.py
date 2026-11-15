"""The service-log lines supernote-ocr writes about itself.

ABOUTME: ``mgmt log <service> alive|output|error|start|finish`` for the ingest
ABOUTME: runner. Best-effort by design: a failed call never breaks an ingest.

Why this module exists (task
apps/services-bug-supernote-ocr-started-at-08-51-b, 2026-11-15). The monitor judges
a service by these lines: a service whose newest ``alive``/``output``/``finish`` is
older than its silence limit is reported silent, and a service with no lines at all
is reported as never logging. ``svc-wrap`` writes ``start`` once when the daemon
comes up and ``finish`` when it exits, which is invisible from the inside of a run
that lasts days — so a healthy ingest read as silent, and a real stop would have
read exactly the same. The runner now writes its own lifecycle (see
``ingest_service.IngestService``), through this small wrapper.

Two rules this module holds:

* **It never raises.** mgmt missing, Postgres down, a hung ``psql``: the call
  returns ``False`` and the ingest carries on. Logging is evidence, not a
  dependency.
* **It never guesses the actor.** ``MGMT_ACTOR`` is inherited from the environment
  when the process has one, so the record shows which worker wired the run; the
  service name is the fallback.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Mapping, Sequence

SERVICE = "supernote-ocr"

# launchd's PATH is not trusted, so the CLI is named by absolute path; MGMT_BIN
# (honoured by svc-wrap too) overrides it for a scratch or moved install.
DEFAULT_MGMT_BIN = "/Users/braydon/projects/personal/mgmt/mgmt"

# The silence limit registered for this service is 15 minutes; a beat every five
# keeps a healthy long run comfortably inside it, and keeps a hung run honest.
DEFAULT_ALIVE_INTERVAL_SECONDS = 300.0

# One call may not hang the caller. The call runs in its own subprocess, so the
# timeout is a kill, not a cancel.
DEFAULT_CALL_TIMEOUT_SECONDS = 15.0

KINDS = ("alive", "output", "error", "start", "finish")

# argv, env -> exit status. Injected by the tests; never by production code.
Runner = Callable[[Sequence[str], Mapping[str, str]], int]


def _default_runner(argv: Sequence[str], env: Mapping[str, str]) -> int:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(argv),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=DEFAULT_CALL_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode


class ServiceLog:
    """Best-effort writer of this service's lines in the one service log."""

    def __init__(
        self,
        service: str = SERVICE,
        *,
        mgmt_bin: str | None = None,
        actor: str | None = None,
        runner: Runner | None = None,
        alive_interval: float = DEFAULT_ALIVE_INTERVAL_SECONDS,
    ) -> None:
        self.service = service
        self.mgmt_bin = (
            mgmt_bin or os.environ.get("MGMT_BIN") or DEFAULT_MGMT_BIN
        )
        self.actor = (actor or os.environ.get("MGMT_ACTOR") or "").strip() or service
        self.alive_interval = float(alive_interval)
        self._runner: Runner = runner or _default_runner

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["MGMT_ACTOR"] = self.actor
        return env

    def line(
        self,
        kind: str,
        detail: str | None = None,
        *,
        exit_code: int | None = None,
    ) -> bool:
        """Write one line. Returns False on any failure; never raises."""
        if kind not in KINDS:
            return False
        argv = [self.mgmt_bin, "log", self.service, kind]
        if detail:
            argv.append(str(detail))
        if exit_code is not None:
            argv.extend(["--exit", str(int(exit_code))])
        try:
            return self._runner(argv, self._env()) == 0
        except Exception:  # noqa: BLE001 - logging must never break the ingest
            return False

    def start(self, detail: str | None = None) -> bool:
        """A job began: name what started."""
        return self.line("start", detail)

    def alive(self, detail: str | None = None) -> bool:
        """Still running."""
        return self.line("alive", detail)

    def output(self, detail: str | None = None) -> bool:
        """Progress: name how much is done."""
        return self.line("output", detail)

    def finish(
        self, detail: str | None = None, *, exit_code: int | None = None
    ) -> bool:
        """A job ended."""
        return self.line("finish", detail, exit_code=exit_code)

    def error(self, detail: str | None = None, *, exit_code: int | None = None) -> bool:
        """A failure was caught: one line and the exit code."""
        return self.line("error", detail, exit_code=exit_code)
