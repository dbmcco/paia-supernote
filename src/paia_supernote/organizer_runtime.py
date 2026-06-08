from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import supernotelib.parser as sn_parser
from supernotelib.converter import ImageConverter

from paia_supernote.note_snapshot import (
    NotebookSnapshot,
    build_snapshot_from_notebook,
)
from paia_supernote.organizer_api import OrganizerApi
from paia_supernote.organizer_images import PageImageCache


class OrganizerRuntime:
    def __init__(self) -> None:
        self._notebooks: dict[tuple[str, str], Any] = {}

    def snapshot_loader(self, notebook_name: str, note_bytes: bytes) -> NotebookSnapshot:
        revision = _revision(note_bytes)
        notebook = load_notebook_from_bytes(note_bytes)
        snapshot = build_snapshot_from_notebook(
            notebook,
            notebook_name=notebook_name,
            revision=revision,
        )
        self._notebooks[(notebook_name, revision)] = notebook
        return snapshot

    def page_renderer(self, snapshot: NotebookSnapshot, page_id: str):
        if page_id not in snapshot.page_order:
            raise KeyError(f"unknown page_id: {page_id}")
        notebook = self.cached_notebook(snapshot.notebook_name, snapshot.revision)
        if notebook is None:
            raise KeyError(
                f"cached notebook not found for {snapshot.notebook_name}@{snapshot.revision}"
            )
        page_index = snapshot.page_order.index(page_id)
        return ImageConverter(notebook).convert(page_index)

    def cached_notebook(self, notebook_name: str, revision: str) -> Any | None:
        return self._notebooks.get((notebook_name, revision))


def create_organizer_api(
    *,
    uploader: Any,
    cache_dir: Path | str | None = None,
) -> OrganizerApi:
    runtime = OrganizerRuntime()
    return OrganizerApi(
        uploader=uploader,
        snapshot_loader=runtime.snapshot_loader,
        image_cache=PageImageCache(Path(cache_dir) if cache_dir else _default_cache_dir()),
        page_renderer=runtime.page_renderer,
    )


def load_notebook_from_bytes(note_bytes: bytes) -> Any:
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(note_bytes)
        return sn_parser.load_notebook(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _revision(note_bytes: bytes) -> str:
    return hashlib.sha256(note_bytes).hexdigest()


def _default_cache_dir() -> Path:
    return Path("~/.cache/paia-supernote/organizer-images").expanduser()
