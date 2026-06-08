from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest


def _runtime_module():
    try:
        from paia_supernote import organizer_runtime
    except ImportError as exc:
        pytest.fail(f"expected organizer_runtime module to exist: {exc}")
    return organizer_runtime


class _FakePage:
    def __init__(self, page_id: str, content: bytes) -> None:
        self.metadata = {"PAGEID": page_id}
        self._content = content

    def get_pageid(self) -> str:
        return self.metadata["PAGEID"]

    def is_layer_supported(self) -> bool:
        return False

    def get_content(self) -> bytes:
        return self._content


class _FakeNotebook:
    def __init__(self) -> None:
        self.pages = [_FakePage("page-a", b"a"), _FakePage("page-b", b"b")]

    def get_total_pages(self) -> int:
        return len(self.pages)

    def get_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def get_width(self) -> int:
        return 1404

    def get_height(self) -> int:
        return 1872

    def get_titles(self) -> list:
        return []

    def get_keywords(self) -> list:
        return []

    def get_links(self) -> list:
        return []


class _FakeConverter:
    def __init__(self, notebook: _FakeNotebook) -> None:
        self.notebook = notebook
        self.converted: list[int] = []

    def convert(self, page_index: int) -> Image.Image:
        self.converted.append(page_index)
        return Image.new("RGB", (20 + page_index, 10), "white")


def _patch_load_notebook(
    monkeypatch: pytest.MonkeyPatch,
    organizer_runtime,
    notebook: _FakeNotebook | None = None,
) -> _FakeNotebook:
    notebook = notebook or _FakeNotebook()
    monkeypatch.setattr(
        organizer_runtime.sn_parser,
        "load_notebook",
        lambda _path: notebook,
    )
    return notebook


def test_snapshot_loader_parses_bytes_and_caches_notebook_by_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_runtime = _runtime_module()
    loaded_paths: list[Path] = []
    notebook = _FakeNotebook()

    def fake_load_notebook(path: str) -> _FakeNotebook:
        loaded_paths.append(Path(path))
        assert Path(path).read_bytes() == b"note-bytes"
        return notebook

    monkeypatch.setattr(organizer_runtime.sn_parser, "load_notebook", fake_load_notebook)
    runtime = organizer_runtime.OrganizerRuntime()

    snapshot = runtime.snapshot_loader("LFW", b"note-bytes")

    assert snapshot.notebook_name == "LFW"
    assert snapshot.page_order == ["page-a", "page-b"]
    assert snapshot.revision
    assert runtime.cached_notebook("LFW", snapshot.revision) is notebook
    assert loaded_paths and not loaded_paths[0].exists()


def test_page_renderer_uses_snapshot_page_order_and_cached_notebook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_runtime = _runtime_module()
    converter_instances: list[_FakeConverter] = []

    def fake_converter(notebook: _FakeNotebook) -> _FakeConverter:
        converter = _FakeConverter(notebook)
        converter_instances.append(converter)
        return converter

    monkeypatch.setattr(organizer_runtime, "ImageConverter", fake_converter)
    runtime = organizer_runtime.OrganizerRuntime()
    _patch_load_notebook(monkeypatch, organizer_runtime)
    snapshot = runtime.snapshot_loader("LFW", b"note-bytes")

    image = runtime.page_renderer(snapshot, "page-b")

    assert image.size == (21, 10)
    assert converter_instances[0].converted == [1]


def test_page_renderer_rejects_unknown_or_uncached_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organizer_runtime = _runtime_module()
    runtime = organizer_runtime.OrganizerRuntime()
    _patch_load_notebook(monkeypatch, organizer_runtime)
    snapshot = runtime.snapshot_loader("LFW", b"note-bytes")

    with pytest.raises(KeyError, match="unknown page_id"):
        runtime.page_renderer(snapshot, "missing")

    runtime = organizer_runtime.OrganizerRuntime()
    with pytest.raises(KeyError, match="cached notebook"):
        runtime.page_renderer(snapshot, "page-a")


def test_create_organizer_api_wires_runtime_dependencies(tmp_path: Path) -> None:
    organizer_runtime = _runtime_module()
    uploader = object()

    api = organizer_runtime.create_organizer_api(
        uploader=uploader,
        cache_dir=tmp_path / "images",
    )

    assert api.uploader is uploader
