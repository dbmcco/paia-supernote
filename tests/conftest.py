"""Shared test fixtures for the paia-supernote test suite.

ABOUTME: Hermetic isolation from live service state.
ABOUTME: Tests must never contend on the real cloud-api lock or session
ABOUTME: file with the running paia-supernote launchd services.
"""

from __future__ import annotations

import pytest

from paia_supernote import svc_log
from paia_supernote.uploader import SupernoteUploader


@pytest.fixture(autouse=True)
def _no_live_service_log(monkeypatch):
    """Never write a test's service-log lines into the live ``svc_log`` table.

    The ingest runner logs its own lifecycle, and ``svc_log`` is the production
    record the monitor reads. A test that drives an ingest with the default runner
    would write fabricated start/output/finish/error lines for supernote-ocr into
    that table — which happened once, on 2026-11-15, and made a silent service look
    healthy (see the worklog for
    apps/services-bug-supernote-ocr-started-at-08-51-b). The default runner is
    replaced by one that refuses, so a test that wants to assert the log must inject
    its own runner and see the calls.
    """

    def _refused(argv, env):
        return 1

    monkeypatch.setattr(svc_log, "_default_runner", _refused, raising=True)


@pytest.fixture(autouse=True)
def _isolated_cloud_api_lock(tmp_path):
    """Point the cross-process cloud-api lock at a per-test tmp file.

    The organizer/ingest/enrich services all flock the real
    ~/.paia/supernote/cloud-api.lock. When one of them is wedged on that
    lock (2026-10-26 incident), any test that reaches _api_call would block
    on flock against the live service — stalling the whole suite. Tests get
    their own lock file instead.
    """
    original = SupernoteUploader.CLOUD_API_LOCK_FILE
    SupernoteUploader.CLOUD_API_LOCK_FILE = tmp_path / "cloud-api.lock"
    try:
        yield
    finally:
        SupernoteUploader.CLOUD_API_LOCK_FILE = original
