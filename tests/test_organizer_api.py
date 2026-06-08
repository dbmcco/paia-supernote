from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
import pytest

from paia_supernote.note_snapshot import (
    NoteMetadataIndex,
    NotebookSnapshot,
    PageRecord,
)


def _api_module():
    try:
        from paia_supernote import organizer_api
    except ImportError as exc:
        pytest.fail(f"expected organizer_api module to exist: {exc}")
    return organizer_api


@dataclass
class _FakeImageResult:
    path: Path
    cache_hit: bool
    width: int
    height: int
    media_type: str = "image/png"


class _FakeUploader:
    def __init__(self) -> None:
        self.downloaded: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.uploaded_bytes: list[bytes] = []

    async def list_notebooks(self) -> list[dict]:
        return [
            {"fileName": "LFW.note", "isFolder": "N", "id": "file-1", "updateTime": 10},
            {"fileName": "Archive", "isFolder": "Y", "id": "folder-1", "updateTime": 11},
            {"fileName": "notes.pdf", "isFolder": "N", "id": "file-2", "updateTime": 12},
        ]

    async def download_notebook(self, target_name: str) -> bytes:
        self.downloaded.append(target_name)
        return b"notebook-bytes"

    async def upload_notebook(self, notebook_path: str, target_name: str) -> bool:
        self.uploaded.append((notebook_path, target_name))
        self.uploaded_bytes.append(Path(notebook_path).read_bytes())
        return True


class _FakeCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[dict] = []

    def get_or_render(self, **kwargs) -> _FakeImageResult:
        self.requests.append(kwargs)
        image = kwargs["renderer"]()
        image.save(self.path, format="PNG")
        return _FakeImageResult(
            path=self.path,
            cache_hit=False,
            width=image.width,
            height=image.height,
        )


def _snapshot() -> NotebookSnapshot:
    page_a = PageRecord(
        page_id="page-a",
        page_index=0,
        starred=True,
        page_metadata={"PAGEID": "page-a", "FIVESTAR": "native-star"},
        content_hash="abc",
        image_width=1404,
        image_height=1872,
    )
    page_b = PageRecord(
        page_id="page-b",
        page_index=1,
        starred=False,
        page_metadata={"PAGEID": "page-b"},
        content_hash="def",
        image_width=1404,
        image_height=1872,
    )
    return NotebookSnapshot(
        notebook_name="LFW",
        revision="rev-1",
        page_order=["page-a", "page-b"],
        pages={"page-a": page_a, "page-b": page_b},
        metadata=NoteMetadataIndex(
            headings_by_page_id={"page-a": []},
            keywords_by_page_id={"page-a": []},
            links_by_page_id={"page-a": []},
            stars_by_page_id={"page-a": True},
        ),
    )


@pytest.mark.asyncio
async def test_list_notebooks_returns_cloud_note_files_only(tmp_path) -> None:
    organizer_api = _api_module()
    api = organizer_api.OrganizerApi(
        uploader=_FakeUploader(),
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )

    result = await api.list_notebooks()

    assert result == [
        {
            "name": "LFW",
            "file_name": "LFW.note",
            "file_id": "file-1",
            "update_time": 10,
        }
    ]


@pytest.mark.asyncio
async def test_get_snapshot_downloads_notebook_and_returns_page_metadata(tmp_path) -> None:
    organizer_api = _api_module()
    uploader = _FakeUploader()
    api = organizer_api.OrganizerApi(
        uploader=uploader,
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )

    result = await api.get_snapshot("LFW")

    assert uploader.downloaded == ["LFW.note"]
    assert result["notebook_name"] == "LFW"
    assert result["revision"] == "rev-1"
    assert result["page_order"] == ["page-a", "page-b"]
    assert result["pages"]["page-a"]["starred"] is True
    assert result["pages"]["page-a"]["image_width"] == 1404


@pytest.mark.asyncio
async def test_get_page_image_uses_snapshot_revision_page_id_and_cache(tmp_path) -> None:
    organizer_api = _api_module()
    cache = _FakeCache(tmp_path / "page.png")
    api = organizer_api.OrganizerApi(
        uploader=_FakeUploader(),
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=cache,
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )

    result = await api.get_page_image("LFW", "page-a", scale=0.5)

    assert result["media_type"] == "image/png"
    assert result["width"] == 20
    assert result["height"] == 10
    assert cache.requests[0]["notebook_name"] == "LFW"
    assert cache.requests[0]["revision"] == "rev-1"
    assert cache.requests[0]["page_id"] == "page-a"
    assert cache.requests[0]["scale"] == 0.5


@pytest.mark.asyncio
async def test_preview_reorder_rejects_stale_revision(tmp_path) -> None:
    organizer_api = _api_module()
    api = organizer_api.OrganizerApi(
        uploader=_FakeUploader(),
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )

    result = await api.preview_reorder(
        "LFW",
        expected_revision="old-revision",
        page_order=["page-b", "page-a"],
    )

    assert result == {
        "ok": False,
        "reason": "stale_revision",
        "current_revision": "rev-1",
    }


@pytest.mark.asyncio
async def test_preview_reorder_rejects_invalid_page_order(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_api = _api_module()
    api = organizer_api.OrganizerApi(
        uploader=_FakeUploader(),
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )
    monkeypatch.setattr(
        organizer_api.note_reorder,
        "reorder_pages",
        lambda _bytes, *, page_order: (_ for _ in ()).throw(ValueError("bad order")),
    )

    result = await api.preview_reorder(
        "LFW",
        expected_revision="rev-1",
        page_order=["page-a", "page-a"],
    )

    assert result["ok"] is False
    assert result["reason"] == "invalid_page_order"
    assert "bad order" in result["error"]


@pytest.mark.asyncio
async def test_preview_reorder_rejects_unsupported_link_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_api = _api_module()
    api = organizer_api.OrganizerApi(
        uploader=_FakeUploader(),
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )
    monkeypatch.setattr(
        organizer_api.note_reorder,
        "reorder_pages",
        lambda _bytes, *, page_order: (_ for _ in ()).throw(
            organizer_api.note_reorder.UnsupportedLinkMetadataError("unsafe link")
        ),
    )

    result = await api.preview_reorder(
        "LFW",
        expected_revision="rev-1",
        page_order=["page-b", "page-a"],
    )

    assert result["ok"] is False
    assert result["reason"] == "unsupported_link_metadata"
    assert "unsafe link" in result["error"]


@pytest.mark.asyncio
async def test_apply_reorder_uploads_reordered_bytes_and_returns_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_api = _api_module()
    uploader = _FakeUploader()
    api = organizer_api.OrganizerApi(
        uploader=uploader,
        snapshot_loader=lambda _name, _bytes: _snapshot(),
        image_cache=_FakeCache(tmp_path / "page.png"),
        page_renderer=lambda _snapshot, _page_id: Image.new("RGB", (20, 10), "white"),
    )
    monkeypatch.setattr(
        organizer_api.note_reorder,
        "reorder_pages",
        lambda _bytes, *, page_order: b"reordered-bytes",
    )

    result = await api.apply_reorder(
        "LFW",
        expected_revision="rev-1",
        page_order=["page-b", "page-a"],
    )

    assert result["ok"] is True
    assert result["snapshot"]["notebook_name"] == "LFW"
    assert result["snapshot"]["page_order"] == ["page-a", "page-b"]
    assert uploader.uploaded[0][1] == "LFW.note"
    assert uploader.uploaded_bytes == [b"reordered-bytes"]
    assert uploader.downloaded == ["LFW.note", "LFW.note"]
