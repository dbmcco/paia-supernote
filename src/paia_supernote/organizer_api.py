from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from paia_supernote import note_reorder
from paia_supernote.note_snapshot import NotebookSnapshot, PageRecord


SnapshotLoader = Callable[[str, bytes], NotebookSnapshot]
PageRenderer = Callable[[NotebookSnapshot, str], Image.Image]


class OrganizerApi:
    def __init__(
        self,
        *,
        uploader: Any,
        snapshot_loader: SnapshotLoader,
        image_cache: Any,
        page_renderer: PageRenderer,
    ) -> None:
        self.uploader = uploader
        self.snapshot_loader = snapshot_loader
        self.image_cache = image_cache
        self.page_renderer = page_renderer

    async def list_notebooks(self) -> list[dict[str, Any]]:
        entries = await _list_note_entries(self.uploader)
        notebooks: list[dict[str, Any]] = []
        for entry in entries:
            file_name = str(entry.get("fileName") or "")
            if entry.get("isFolder") == "Y" or not file_name.endswith(".note"):
                continue
            notebooks.append(
                {
                    "name": file_name[:-5],
                    "file_name": file_name,
                    "file_id": entry.get("id"),
                    "update_time": entry.get("updateTime"),
                }
            )
        return notebooks

    async def get_snapshot(self, notebook_name: str) -> dict[str, Any]:
        snapshot = await self._load_snapshot(notebook_name)
        return serialize_snapshot(snapshot)

    async def get_page_image(
        self,
        notebook_name: str,
        page_id: str,
        *,
        scale: float,
    ) -> dict[str, Any]:
        snapshot = await self._load_snapshot(notebook_name)
        if page_id not in snapshot.pages:
            raise KeyError(f"unknown page_id: {page_id}")
        cached = self.image_cache.get_or_render(
            notebook_name=notebook_name,
            revision=snapshot.revision,
            page_id=page_id,
            scale=scale,
            renderer=lambda: self.page_renderer(snapshot, page_id),
        )
        return {
            "path": str(Path(cached.path)),
            "cache_hit": bool(cached.cache_hit),
            "width": int(cached.width),
            "height": int(cached.height),
            "media_type": cached.media_type,
        }

    async def preview_reorder(
        self,
        notebook_name: str,
        *,
        expected_revision: str,
        page_order: list[str],
    ) -> dict[str, Any]:
        note_bytes = await self.uploader.download_notebook(f"{notebook_name}.note")
        snapshot = self.snapshot_loader(notebook_name, note_bytes)
        if snapshot.revision != expected_revision:
            return {
                "ok": False,
                "reason": "stale_revision",
                "current_revision": snapshot.revision,
            }
        try:
            note_reorder.reorder_pages(note_bytes, page_order=page_order)
        except note_reorder.UnsupportedLinkMetadataError as exc:
            return {
                "ok": False,
                "reason": "unsupported_link_metadata",
                "error": str(exc),
            }
        except ValueError as exc:
            return {
                "ok": False,
                "reason": "invalid_page_order",
                "error": str(exc),
            }
        return {
            "ok": True,
            "revision": snapshot.revision,
            "page_order": list(page_order),
        }

    async def apply_reorder(
        self,
        notebook_name: str,
        *,
        expected_revision: str,
        page_order: list[str],
    ) -> dict[str, Any]:
        note_bytes = await self.uploader.download_notebook(f"{notebook_name}.note")
        snapshot = self.snapshot_loader(notebook_name, note_bytes)
        if snapshot.revision != expected_revision:
            return {
                "ok": False,
                "reason": "stale_revision",
                "current_revision": snapshot.revision,
            }
        try:
            reordered_bytes = note_reorder.reorder_pages(note_bytes, page_order=page_order)
        except note_reorder.UnsupportedLinkMetadataError as exc:
            return {
                "ok": False,
                "reason": "unsupported_link_metadata",
                "error": str(exc),
            }
        except ValueError as exc:
            return {
                "ok": False,
                "reason": "invalid_page_order",
                "error": str(exc),
            }

        target_name = f"{notebook_name}.note"
        tmp_path = _write_temp_note(reordered_bytes)
        try:
            await self.uploader.upload_notebook(tmp_path, target_name)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        return {
            "ok": True,
            "snapshot": await self.get_snapshot(notebook_name),
        }

    async def _load_snapshot(self, notebook_name: str) -> NotebookSnapshot:
        note_bytes = await self.uploader.download_notebook(f"{notebook_name}.note")
        return self.snapshot_loader(notebook_name, note_bytes)


def _write_temp_note(note_bytes: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".note")
    with os.fdopen(fd, "wb") as file:
        file.write(note_bytes)
    return path


def serialize_snapshot(snapshot: NotebookSnapshot) -> dict[str, Any]:
    return {
        "notebook_name": snapshot.notebook_name,
        "revision": snapshot.revision,
        "page_order": list(snapshot.page_order),
        "pages": {
            page_id: serialize_page_record(page)
            for page_id, page in snapshot.pages.items()
        },
    }


def serialize_page_record(page: PageRecord) -> dict[str, Any]:
    return {
        "page_id": page.page_id,
        "page_index": page.page_index,
        "starred": page.starred,
        "content_hash": page.content_hash,
        "image_width": page.image_width,
        "image_height": page.image_height,
        "heading_count": len(page.headings),
        "keyword_count": len(page.keywords),
        "outgoing_link_count": len(page.outgoing_links),
        "incoming_link_count": len(page.incoming_links),
    }


async def _list_note_entries(uploader: Any) -> list[dict[str, Any]]:
    list_notebooks = getattr(uploader, "list_notebooks", None)
    if list_notebooks is not None:
        return list(await list_notebooks())
    list_note_files = getattr(uploader, "_list_note_files", None)
    if list_note_files is not None:
        return list(await list_note_files())
    raise AttributeError("uploader must expose list_notebooks or _list_note_files")
