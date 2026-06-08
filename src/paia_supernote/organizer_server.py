from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from paia_supernote.organizer_ui import render_index


def make_organizer_handler(
    api: Any,
    *,
    async_runner: Any | None = None,
) -> type[BaseHTTPRequestHandler]:
    class OrganizerRequestHandler(BaseHTTPRequestHandler):
        organizer_api = api
        organizer_async_runner = staticmethod(async_runner or _run_async)

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

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                self._route_post(parsed.path, self._read_json_body())
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
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
                notebooks = self._run_async(self.organizer_api.list_notebooks())
                self._send_json(notebooks)
                return

            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "notebooks"] and parts[3] == "snapshot":
                snapshot = self._run_async(self.organizer_api.get_snapshot(parts[2]))
                self._send_json(snapshot)
                return
            if (
                len(parts) == 6
                and parts[:2] == ["api", "notebooks"]
                and parts[3] == "pages"
                and parts[5] == "image"
            ):
                scale = _first_float(query.get("scale"), default=0.25)
                revision = _first(query.get("revision"))
                image = self._run_async(
                    self.organizer_api.get_page_image(
                        parts[2],
                        parts[4],
                        scale=scale,
                        revision=revision,
                    )
                )
                self._send_file(
                    Path(image["path"]),
                    image.get("media_type", "image/png"),
                    cache_control=(
                        "public, max-age=31536000, immutable"
                        if revision
                        else None
                    ),
                )
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def _route_post(self, path: str, payload: dict[str, Any]) -> None:
            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            if (
                len(parts) == 6
                and parts[:2] == ["api", "notebooks"]
                and parts[3] == "pages"
                and parts[5] == "move"
            ):
                source_revision = str(payload.get("source_revision") or "")
                target_notebook = str(payload.get("target_notebook") or "")
                if not source_revision or not target_notebook:
                    raise ValueError(
                        "source_revision and target_notebook are required"
                    )
                result = self._run_async(
                    self.organizer_api.move_page_to_notebook(
                        parts[2],
                        parts[4],
                        source_revision=source_revision,
                        target_notebook=target_notebook,
                    )
                )
                self._send_move_result(result)
                return
            if (
                len(parts) == 5
                and parts[:2] == ["api", "notebooks"]
                and parts[3] == "reorder"
                and parts[4] in {"preview", "apply"}
            ):
                expected_revision = str(payload.get("expected_revision") or "")
                page_order = payload.get("page_order")
                if not expected_revision or not isinstance(page_order, list):
                    raise ValueError("expected_revision and page_order are required")
                page_order = [str(page_id) for page_id in page_order]
                if parts[4] == "preview":
                    result = self._run_async(
                        self.organizer_api.preview_reorder(
                            parts[2],
                            expected_revision=expected_revision,
                            page_order=page_order,
                        )
                    )
                else:
                    result = self._run_async(
                        self.organizer_api.apply_reorder(
                            parts[2],
                            expected_revision=expected_revision,
                            page_order=page_order,
                        )
                    )
                self._send_reorder_result(result)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _send_organizer(self, query: dict[str, list[str]]) -> None:
            notebooks = self._run_async(self.organizer_api.list_notebooks())
            selected = _first(query.get("notebook"))
            if selected is None and notebooks:
                selected = str(notebooks[0].get("name") or "")
            snapshot = (
                self._run_async(self.organizer_api.get_snapshot(selected))
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

        def _run_async(self, awaitable: Any) -> Any:
            return self.organizer_async_runner(awaitable)

        def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_reorder_result(self, result: dict[str, Any]) -> None:
            status = HTTPStatus.OK
            if result.get("ok") is False:
                reason = result.get("reason")
                if reason == "stale_revision":
                    status = HTTPStatus.CONFLICT
                elif reason in {"invalid_page_order", "unsupported_link_metadata"}:
                    status = HTTPStatus.UNPROCESSABLE_ENTITY
            self._send_json(result, status=status)

        def _send_move_result(self, result: dict[str, Any]) -> None:
            status = HTTPStatus.OK
            if result.get("ok") is False:
                reason = result.get("reason")
                if reason == "stale_revision":
                    status = HTTPStatus.CONFLICT
                elif reason in {"same_notebook", "unknown_page_id"}:
                    status = HTTPStatus.UNPROCESSABLE_ENTITY
                elif reason == "partial_move_target_uploaded_source_failed":
                    status = HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json(result, status=status)

        def _send_file(
            self,
            path: Path,
            media_type: str,
            *,
            cache_control: str | None = None,
        ) -> None:
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(length)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("malformed JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

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
