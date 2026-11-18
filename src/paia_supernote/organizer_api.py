from __future__ import annotations

import os
import tempfile
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from PIL import Image

from paia_supernote import note_page_ops, note_reorder
from paia_supernote.note_snapshot import NotebookSnapshot, PageRecord
from paia_supernote.uploader import UploadAuthError


SnapshotLoader = Callable[[str, bytes], NotebookSnapshot]
PageRenderer = Callable[[NotebookSnapshot, str], Image.Image]

# Listing freshness: two requests within this window share one cloud folder
# walk. Bounds how long an external change (device sync, a write from another
# process) can hide before the change check sees the new updateTime/size.
LISTING_TTL_S = 5.0

# Distrust ceiling for the snapshot cache: the cache is invalidated by the
# notebook's cloud (updateTime, size) moving, but if that metadata ever lied
# about a content change, a cached snapshot is re-downloaded after this bound
# anyway so a stale page cannot pin forever.
SNAPSHOT_MAX_AGE_S = 600.0


@dataclass(slots=True)
class _ListingCache:
    entries: list[dict[str, Any]]
    expires_at: float


@dataclass(slots=True)
class _SnapshotCacheEntry:
    fingerprint: tuple[Any, Any] | None
    payload: dict[str, Any]
    created_at: float


class OrganizerApi:
    def __init__(
        self,
        *,
        uploader: Any,
        snapshot_loader: SnapshotLoader,
        image_cache: Any,
        page_renderer: PageRenderer,
        listing_ttl_s: float = LISTING_TTL_S,
        snapshot_max_age_s: float = SNAPSHOT_MAX_AGE_S,
    ) -> None:
        self.uploader = uploader
        self.snapshot_loader = snapshot_loader
        self.image_cache = image_cache
        self.page_renderer = page_renderer
        self.listing_ttl_s = listing_ttl_s
        self.snapshot_max_age_s = snapshot_max_age_s
        self._snapshots: dict[tuple[str, str], NotebookSnapshot] = {}
        self._listing: _ListingCache | None = None
        self._snapshot_payloads: dict[str, _SnapshotCacheEntry] = {}

    async def list_notebooks(self) -> list[dict[str, Any]]:
        entries = await self._listing_entries()
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

    async def _call_with_reauth(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run a cloud operation, reloading the session once on auth failure.

        The organizer holds its uploader session in memory. If another process
        (poller, back-fill) refreshes session.json, or the session simply
        expires, the next cloud call raises UploadAuthError. Re-authenticate
        once and retry instead of surfacing a hard 500 to the browser.
        """
        try:
            return await operation()
        except UploadAuthError:
            ensure = getattr(self.uploader, "ensure_authenticated", None)
            if ensure is None:
                raise
            await ensure()
            return await operation()

    async def _listing_entries(self) -> list[dict[str, Any]]:
        """Cloud folder-walk entries, shared across requests within the TTL.

        A single GET /organizer used to walk the folder tree for the sidebar
        and again to resolve the download; a poller asking every few seconds
        made each answer pay the walk (plus a full notebook download). The
        walk is now cached for listing_ttl_s — the staleness bound for the
        notebook list and for the snapshot change check below.
        """
        cached = self._listing
        if cached is not None and time.monotonic() < cached.expires_at:
            return cached.entries
        entries = await self._call_with_reauth(
            lambda: _list_note_entries(self.uploader)
        )
        self._listing = _ListingCache(
            entries=entries,
            expires_at=time.monotonic() + self.listing_ttl_s,
        )
        return entries

    async def get_snapshot(self, notebook_name: str) -> dict[str, Any]:
        """Serialized snapshot, cached while the cloud says the file is unchanged.

        The change check is the notebook's (updateTime, size) from the
        TTL-bounded listing — a cheap metadata read instead of re-downloading
        the full .note (tens of MB). The organizer's own writes invalidate
        locally; external changes surface when the listing refreshes, within
        listing_ttl_s plus one walk. snapshot_max_age_s bounds the case where
        the cloud metadata does not move on a real change.
        """
        fingerprint = await self._snapshot_fingerprint(notebook_name)
        if fingerprint is not None:
            cached = self._snapshot_payloads.get(notebook_name)
            if (
                cached is not None
                and cached.fingerprint == fingerprint
                and time.monotonic() - cached.created_at < self.snapshot_max_age_s
            ):
                return cached.payload
        snapshot = await self._call_with_reauth(
            lambda: self._load_snapshot(notebook_name)
        )
        payload = serialize_snapshot(snapshot)
        if fingerprint is not None:
            self._snapshot_payloads[notebook_name] = _SnapshotCacheEntry(
                fingerprint=fingerprint,
                payload=payload,
                created_at=time.monotonic(),
            )
        return payload

    async def _snapshot_fingerprint(self, notebook_name: str) -> tuple[Any, Any] | None:
        """(updateTime, size) for the notebook, or None when it cannot be
        validated (not listed, or the listing itself failed) — None means the
        caller re-downloads rather than serving an unvalidatable cache."""
        try:
            entries = await self._listing_entries()
        except Exception:
            return None
        target = f"{notebook_name}.note"
        for entry in entries:
            if entry.get("isFolder") == "Y":
                continue
            if str(entry.get("fileName") or "") == target:
                return (entry.get("updateTime"), entry.get("size"))
        return None

    async def get_page_image(
        self,
        notebook_name: str,
        page_id: str,
        *,
        scale: float,
        revision: str | None = None,
    ) -> dict[str, Any]:
        snapshot = await self._call_with_reauth(lambda: self._snapshot_for_image(notebook_name, revision))
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
        self._invalidate_cached_page(notebook_name)
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
            # The target really changed on the cloud even though this move
            # reports failure — its cached snapshot must not be served again.
            self._invalidate_cached_page(target_notebook)
            return {
                "ok": False,
                "reason": "partial_move_target_uploaded_source_failed",
                "source_notebook": source_notebook,
                "target_notebook": target_notebook,
                "page_id": page_id,
                "error": str(exc),
            }

        self._invalidate_cached_page(source_notebook, target_notebook)
        return {
            "ok": True,
            "source_notebook": source_notebook,
            "target_notebook": target_notebook,
            "page_id": page_id,
            "source_revision": hashlib.sha256(source_bytes_without_page).hexdigest(),
            "target_revision": hashlib.sha256(target_bytes_with_page).hexdigest(),
        }

    def _invalidate_cached_page(self, *notebook_names: str) -> None:
        """Drop caches after this process changed the cloud itself.

        The organizer's own writes must be visible to the next render at once,
        not within the listing TTL: the affected notebooks' cached snapshots
        and the cached listing (its updateTime just moved) are dropped so the
        next request re-reads the cloud it just wrote.
        """
        for name in notebook_names:
            self._snapshot_payloads.pop(name, None)
        self._listing = None

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
    # Prefer the recursive walker so notebooks living in cloud subfolders
    # (e.g. cos/) appear alongside root notebooks. Falls back to the root-only
    # listing for uploaders that don't tree-walk.
    list_note_files_recursive = getattr(uploader, "list_note_files_recursive", None)
    if list_note_files_recursive is not None:
        return list(await list_note_files_recursive())
    list_note_files = getattr(uploader, "list_note_files", None)
    if list_note_files is not None:
        return list(await list_note_files())
    raise AttributeError(
        "uploader must expose list_notebooks, list_note_files_recursive, or list_note_files"
    )
