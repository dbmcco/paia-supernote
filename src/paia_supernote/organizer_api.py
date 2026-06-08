from __future__ import annotations

import os
import tempfile
import hashlib
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from paia_supernote import note_page_ops, note_reorder
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
        self._snapshots: dict[tuple[str, str], NotebookSnapshot] = {}

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
        revision: str | None = None,
    ) -> dict[str, Any]:
        snapshot = await self._snapshot_for_image(notebook_name, revision)
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
        await self._upload_note_bytes(reordered_bytes, target_name)
        return {
            "ok": True,
            "revision": hashlib.sha256(reordered_bytes).hexdigest(),
        }

    async def move_page_to_notebook(
        self,
        source_notebook: str,
        page_id: str,
        *,
        source_revision: str,
        target_notebook: str,
    ) -> dict[str, Any]:
        if source_notebook == target_notebook:
            return {"ok": False, "reason": "same_notebook"}

        source_bytes = await self.uploader.download_notebook(f"{source_notebook}.note")
        source_snapshot = self.snapshot_loader(source_notebook, source_bytes)
        if source_snapshot.revision != source_revision:
            return {
                "ok": False,
                "reason": "stale_revision",
                "current_revision": source_snapshot.revision,
            }
        if page_id not in source_snapshot.page_order:
            return {
                "ok": False,
                "reason": "unknown_page_id",
                "page_id": page_id,
            }

        source_page_index = source_snapshot.page_order.index(page_id)
        target_bytes = await self.uploader.download_notebook(f"{target_notebook}.note")
        target_bytes_with_page = note_page_ops.copy_pages_to_end(
            source_bytes,
            target_bytes,
            source_pages=[source_page_index],
        )
        source_bytes_without_page = note_page_ops.remove_pages(
            source_bytes,
            pages=[source_page_index],
        )

        await self._upload_note_bytes(target_bytes_with_page, f"{target_notebook}.note")
        try:
            await self._upload_note_bytes(
                source_bytes_without_page,
                f"{source_notebook}.note",
            )
        except Exception as exc:
            return {
                "ok": False,
                "reason": "partial_move_target_uploaded_source_failed",
                "source_notebook": source_notebook,
                "target_notebook": target_notebook,
                "page_id": page_id,
                "error": str(exc),
            }

        return {
            "ok": True,
            "source_notebook": source_notebook,
            "target_notebook": target_notebook,
            "page_id": page_id,
            "source_revision": hashlib.sha256(source_bytes_without_page).hexdigest(),
            "target_revision": hashlib.sha256(target_bytes_with_page).hexdigest(),
        }

    async def _load_snapshot(self, notebook_name: str) -> NotebookSnapshot:
        note_bytes = await self.uploader.download_notebook(f"{notebook_name}.note")
        snapshot = self.snapshot_loader(notebook_name, note_bytes)
        self._snapshots[(snapshot.notebook_name, snapshot.revision)] = snapshot
        return snapshot

    async def _snapshot_for_image(
        self,
        notebook_name: str,
        revision: str | None,
    ) -> NotebookSnapshot:
        if revision:
            cached = self._snapshots.get((notebook_name, revision))
            if cached is not None:
                return cached
            snapshot = await self._load_snapshot(notebook_name)
            if snapshot.revision != revision:
                raise KeyError(
                    f"cached snapshot not found for {notebook_name}@{revision}"
                )
            return snapshot
        return await self._load_snapshot(notebook_name)

    async def _upload_note_bytes(self, note_bytes: bytes, target_name: str) -> None:
        tmp_path = _write_temp_note(note_bytes)
        try:
            await self.uploader.upload_notebook(tmp_path, target_name)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


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
