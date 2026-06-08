from __future__ import annotations

from PIL import Image
import pytest


def _images_module():
    try:
        from paia_supernote import organizer_images
    except ImportError as exc:
        pytest.fail(f"expected organizer_images module to exist: {exc}")
    return organizer_images


def test_page_image_cache_renders_once_for_same_revision_page_and_scale(tmp_path) -> None:
    organizer_images = _images_module()
    cache = organizer_images.PageImageCache(tmp_path)
    render_calls = 0

    def render_page() -> Image.Image:
        nonlocal render_calls
        render_calls += 1
        return Image.new("RGB", (20, 10), "white")

    first = cache.get_or_render(
        notebook_name="LFW",
        revision="rev-1",
        page_id="page-a",
        scale=0.5,
        renderer=render_page,
    )
    second = cache.get_or_render(
        notebook_name="LFW",
        revision="rev-1",
        page_id="page-a",
        scale=0.5,
        renderer=render_page,
    )

    assert render_calls == 1
    assert first.path == second.path
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.width == 10
    assert first.height == 5
    assert first.path.read_bytes().startswith(b"\x89PNG")


def test_page_image_cache_uses_scale_in_cache_key(tmp_path) -> None:
    organizer_images = _images_module()
    cache = organizer_images.PageImageCache(tmp_path)
    render_calls = 0

    def render_page() -> Image.Image:
        nonlocal render_calls
        render_calls += 1
        return Image.new("RGB", (20, 10), "white")

    small = cache.get_or_render(
        notebook_name="LFW",
        revision="rev-1",
        page_id="page-a",
        scale=0.5,
        renderer=render_page,
    )
    full = cache.get_or_render(
        notebook_name="LFW",
        revision="rev-1",
        page_id="page-a",
        scale=1.0,
        renderer=render_page,
    )

    assert render_calls == 2
    assert small.path != full.path
    assert (small.width, small.height) == (10, 5)
    assert (full.width, full.height) == (20, 10)


def test_page_image_cache_uses_revision_in_cache_key(tmp_path) -> None:
    organizer_images = _images_module()
    cache = organizer_images.PageImageCache(tmp_path)

    first = cache.cache_path(
        notebook_name="LFW",
        revision="rev-1",
        page_id="page-a",
        scale=1.0,
    )
    second = cache.cache_path(
        notebook_name="LFW",
        revision="rev-2",
        page_id="page-a",
        scale=1.0,
    )

    assert first != second
