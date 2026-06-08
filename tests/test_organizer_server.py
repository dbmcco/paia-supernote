from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from PIL import Image


def _server_module():
    try:
        from paia_supernote import organizer_server
    except ImportError as exc:
        pytest.fail(f"expected organizer_server module to exist: {exc}")
    return organizer_server


class _FakeOrganizerApi:
    def __init__(self, image_path: Path) -> None:
        Image.new("RGB", (12, 8), "white").save(image_path, format="PNG")
        self.image_path = image_path
        self.snapshots: list[str] = []
        self.images: list[tuple[str, str, float]] = []

    async def list_notebooks(self) -> list[dict]:
        return [
            {"name": "LFW", "file_name": "LFW.note"},
            {"name": "Quick", "file_name": "Quick.note"},
        ]

    async def get_snapshot(self, notebook_name: str) -> dict:
        self.snapshots.append(notebook_name)
        return {
            "notebook_name": notebook_name,
            "revision": "rev-1",
            "page_order": ["page-a"],
            "pages": {
                "page-a": {
                    "page_id": "page-a",
                    "page_index": 0,
                    "starred": True,
                    "image_width": 1404,
                    "image_height": 1872,
                    "heading_count": 1,
                    "keyword_count": 0,
                    "outgoing_link_count": 0,
                    "incoming_link_count": 0,
                }
            },
        }

    async def get_page_image(
        self, notebook_name: str, page_id: str, *, scale: float
    ) -> dict:
        self.images.append((notebook_name, page_id, scale))
        return {
            "path": str(self.image_path),
            "media_type": "image/png",
            "width": 12,
            "height": 8,
            "cache_hit": False,
        }


class _RunningServer:
    def __init__(self, api: _FakeOrganizerApi) -> None:
        organizer_server = _server_module()
        handler = organizer_server.make_organizer_handler(api)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "_RunningServer":
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def _get_text(url: str) -> str:
    with urlopen(url, timeout=5) as response:
        assert response.status == 200
        return response.read().decode("utf-8")


def test_organizer_route_renders_selected_notebook_grid(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")

    with _RunningServer(api) as server:
        html = _get_text(f"{server.base_url}/organizer?notebook=Quick")

    assert '<main class="page-grid"' in html
    assert "Quick - Supernote Organizer" in html
    assert api.snapshots == ["Quick"]


def test_api_routes_return_notebooks_and_snapshot_json(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")

    with _RunningServer(api) as server:
        notebooks = json.loads(_get_text(f"{server.base_url}/api/notebooks"))
        snapshot = json.loads(_get_text(f"{server.base_url}/api/notebooks/LFW/snapshot"))

    assert notebooks[0]["name"] == "LFW"
    assert snapshot["notebook_name"] == "LFW"
    assert snapshot["page_order"] == ["page-a"]


def test_page_image_route_streams_cached_png(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")

    with _RunningServer(api) as server:
        with urlopen(
            f"{server.base_url}/api/notebooks/LFW/pages/page-a/image?scale=0.5",
            timeout=5,
        ) as response:
            body = response.read()
            content_type = response.headers["Content-Type"]

    assert content_type == "image/png"
    assert body.startswith(b"\x89PNG")
    assert api.images == [("LFW", "page-a", 0.5)]


def test_unknown_route_returns_404(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")

    with _RunningServer(api) as server:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"{server.base_url}/missing", timeout=5)

    assert exc_info.value.code == 404
