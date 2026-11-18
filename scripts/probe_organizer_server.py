"""
ABOUTME: Run the organizer like the live service, on a probe port, isolated session.
ABOUTME: For measuring render cost on m3 without touching the live organizer.

Run on m3 (SN_PHONE/SN_PASSWORD must be in the environment so the probe can
re-authenticate on its own, like the live service does):

    set -a; source /Users/braydon/projects/.env; set +a
    PROBE_SESSION_FILE=/tmp/organizer-render-probe/session.json \
    PROBE_PORT=18766 \
    PYTHONPATH=src \
    /Users/braydon/projects/experiments/paia-supernote/.venv/bin/python \
    scripts/probe_organizer_server.py

The uploader subclass only redirects SESSION_FILE (where a re-authenticated
session would be persisted) — the probe must never write the live session
file. The cloud-api lock stays shared with the live service on purpose: cloud
calls must serialize across processes.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from paia_supernote.main import _run_organizer
from paia_supernote.uploader import SupernoteUploader


def _probe_uploader() -> SupernoteUploader:
    session_file = Path(os.environ["PROBE_SESSION_FILE"]).expanduser()

    class ProbeUploader(SupernoteUploader):
        def __init__(self) -> None:
            # instance attribute shadows the class default; stop()/re-auth
            # persist to the isolated copy, never the live session.json
            self.SESSION_FILE = session_file
            super().__init__(headless=True)

    return ProbeUploader()


def main() -> None:
    port = int(os.environ.get("PROBE_PORT") or 18766)
    cache_dir = Path(
        os.environ.get("PROBE_CACHE_DIR") or "/tmp/organizer-render-probe/images"
    )
    asyncio.run(
        _run_organizer(
            host="127.0.0.1",
            port=port,
            cache_dir=cache_dir,
            uploader_factory=_probe_uploader,
        )
    )


if __name__ == "__main__":
    main()
