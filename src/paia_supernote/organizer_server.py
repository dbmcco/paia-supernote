from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from paia_supernote.organizer_ui import render_index


def make_organizer_handler(api: Any) -> type[BaseHTTPRequestHandler]:
    class OrganizerRequestHandler(BaseHTTPRequestHandler):
        organizer_api = api

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                self._route_get(parsed.path, parse_qs(parsed.query))
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _route_get(self, path: str, query: dict[str, list[str]]) -> None:
            if path in {"/", "/organizer"}:
                self._send_organizer(query)
                return
            if path == "/api/notebooks":
                notebooks = _run_async(self.organizer_api.list_notebooks())
                self._send_json(notebooks)
                return

            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "snapshot":
                snapshot = _run_async(self.organizer_api.get_snapshot(parts[2]))
                self._send_json(snapshot)
                return
            if (
                len(parts) == 6
                and parts[:2] == ["api", "notebooks"]
                and parts[3] == "pages"
                and parts[5] == "image"
            ):
                scale = _first_float(query.get("scale"), default=0.25)
                image = _run_async(
                    self.organizer_api.get_page_image(parts[2], parts[4], scale=scale)
                )
                self._send_file(Path(image["path"]), image.get("media_type", "image/png"))
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def _send_organizer(self, query: dict[str, list[str]]) -> None:
            notebooks = _run_async(self.organizer_api.list_notebooks())
            selected = _first(query.get("notebook"))
            if selected is None and notebooks:
                selected = str(notebooks[0].get("name") or "")
            snapshot = (
                _run_async(self.organizer_api.get_snapshot(selected))
                if selected
                else _empty_snapshot()
            )
            html = render_index(notebooks=notebooks, snapshot=snapshot)
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, media_type: str) -> None:
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return OrganizerRequestHandler


def _run_async(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _first_float(values: list[str] | None, *, default: float) -> float:
    value = _first(values)
    if value is None:
        return default
    return float(value)


def _empty_snapshot() -> dict[str, Any]:
    return {
        "notebook_name": "",
        "revision": "",
        "page_order": [],
        "pages": {},
    }
