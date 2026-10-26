"""Shared test fixtures for the paia-supernote test suite.

ABOUTME: Hermetic isolation from live service state.
ABOUTME: Tests must never contend on the real cloud-api lock or session
ABOUTME: file with the running paia-supernote launchd services.
"""

from __future__ import annotations

import pytest

from paia_supernote.uploader import SupernoteUploader


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
