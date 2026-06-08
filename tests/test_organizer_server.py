from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
        self.preview_result: dict = {"ok": True}
        self.apply_result: dict = {"ok": True, "snapshot": {}}
        self.move_result: dict = {"ok": True}
        self.preview_requests: list[tuple[str, str, list[str]]] = []
        self.apply_requests: list[tuple[str, str, list[str]]] = []
        self.move_requests: list[tuple[str, str, str, str]] = []

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

    async def preview_reorder(
        self,
        notebook_name: str,
        *,
        expected_revision: str,
        page_order: list[str],
    ) -> dict:
        self.preview_requests.append((notebook_name, expected_revision, page_order))
        return self.preview_result

    async def apply_reorder(
        self,
        notebook_name: str,
        *,
        expected_revision: str,
        page_order: list[str],
    ) -> dict:
        self.apply_requests.append((notebook_name, expected_revision, page_order))
        return self.apply_result

    async def move_page_to_notebook(
        self,
        source_notebook: str,
        page_id: str,
        *,
        source_revision: str,
        target_notebook: str,
    ) -> dict:
        self.move_requests.append(
            (source_notebook, page_id, source_revision, target_notebook)
        )
        return self.move_result


class _RunningServer:
    def __init__(self, api: _FakeOrganizerApi, *, async_runner=None) -> None:
        organizer_server = _server_module()
        handler = organizer_server.make_organizer_handler(api, async_runner=async_runner)
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


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_api_routes_use_injected_async_runner(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    calls = []

    def runner(awaitable):
        calls.append(awaitable)
        awaitable.close()
        return [{"name": "Injected", "file_name": "Injected.note"}]

    with _RunningServer(api, async_runner=runner) as server:
        notebooks = json.loads(_get_text(f"{server.base_url}/api/notebooks"))

    assert notebooks == [{"name": "Injected", "file_name": "Injected.note"}]
    assert len(calls) == 1


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


def test_preview_reorder_post_route_returns_api_result(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.preview_result = {"ok": True, "revision": "rev-1"}

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/reorder/preview",
            {"expected_revision": "rev-1", "page_order": ["page-a"]},
        )

    assert status == 200
    assert body == {"ok": True, "revision": "rev-1"}
    assert api.preview_requests == [("LFW", "rev-1", ["page-a"])]


def test_apply_reorder_post_route_returns_api_result(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.apply_result = {"ok": True, "snapshot": {"notebook_name": "LFW"}}

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/reorder/apply",
            {"expected_revision": "rev-1", "page_order": ["page-a"]},
        )

    assert status == 200
    assert body == {"ok": True, "snapshot": {"notebook_name": "LFW"}}
    assert api.apply_requests == [("LFW", "rev-1", ["page-a"])]


def test_move_page_post_route_returns_api_result(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.move_result = {
        "ok": True,
        "source_notebook": "LFW",
        "target_notebook": "Quick",
        "page_id": "page-a",
    }

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/pages/page-a/move",
            {"source_revision": "rev-1", "target_notebook": "Quick"},
        )

    assert status == 200
    assert body == {
        "ok": True,
        "source_notebook": "LFW",
        "target_notebook": "Quick",
        "page_id": "page-a",
    }
    assert api.move_requests == [("LFW", "page-a", "rev-1", "Quick")]


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [
        ("stale_revision", 409),
        ("same_notebook", 422),
        ("unknown_page_id", 422),
        ("partial_move_target_uploaded_source_failed", 500),
    ],
)
def test_move_page_post_route_maps_domain_failures(
    tmp_path: Path,
    reason: str,
    expected_status: int,
) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.move_result = {"ok": False, "reason": reason, "error": "move failed"}

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/pages/page-a/move",
            {"source_revision": "rev-1", "target_notebook": "Quick"},
        )

    assert status == expected_status
    assert body["reason"] == reason


def test_move_page_post_route_rejects_missing_payload_fields(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/pages/page-a/move",
            {"source_revision": "rev-1"},
        )

    assert status == 400
    assert "source_revision and target_notebook are required" in body["error"]


def test_reorder_post_route_maps_stale_revision_to_conflict(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.preview_result = {
        "ok": False,
        "reason": "stale_revision",
        "current_revision": "rev-2",
    }

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/reorder/preview",
            {"expected_revision": "rev-1", "page_order": ["page-a"]},
        )

    assert status == 409
    assert body["reason"] == "stale_revision"


def test_reorder_post_route_maps_invalid_order_to_unprocessable(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    api.preview_result = {
        "ok": False,
        "reason": "invalid_page_order",
        "error": "bad order",
    }

    with _RunningServer(api) as server:
        status, body = _post_json(
            f"{server.base_url}/api/notebooks/LFW/reorder/preview",
            {"expected_revision": "rev-1", "page_order": ["page-a"]},
        )

    assert status == 422
    assert body["reason"] == "invalid_page_order"


def test_reorder_post_route_rejects_malformed_json(tmp_path: Path) -> None:
    api = _FakeOrganizerApi(tmp_path / "page.png")
    request = Request(
        "http://example.invalid",
        data=b"{not-json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with _RunningServer(api) as server:
        request.full_url = f"{server.base_url}/api/notebooks/LFW/reorder/preview"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=5)

    assert exc_info.value.code == 400
