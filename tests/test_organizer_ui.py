from __future__ import annotations

import pytest


def _ui_module():
    try:
        from paia_supernote import organizer_ui
    except ImportError as exc:
        pytest.fail(f"expected organizer_ui module to exist: {exc}")
    return organizer_ui


def _snapshot_payload() -> dict:
    return {
        "notebook_name": "LFW",
        "revision": "rev-1",
        "page_order": ["page-a", "page-b"],
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
                "incoming_link_count": 1,
            },
            "page-b": {
                "page_id": "page-b",
                "page_index": 1,
                "starred": False,
                "image_width": 1404,
                "image_height": 1872,
                "heading_count": 0,
                "keyword_count": 2,
                "outgoing_link_count": 1,
                "incoming_link_count": 0,
            },
        },
    }


def test_render_index_includes_page_grid_zoom_and_filters() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert '<main class="page-grid"' in html
    assert 'type="range"' in html
    assert 'data-filter="starred"' in html
    assert 'data-filter="headings"' in html
    assert 'data-filter="keywords"' in html
    assert 'data-filter="links"' in html
    assert html.count('class="page-tile"') == 2


def test_render_index_marks_metadata_badges_per_page() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}],
        snapshot=_snapshot_payload(),
    )

    assert 'data-page-id="page-a"' in html
    assert 'data-starred="true"' in html
    assert '>Star<' in html
    assert '>Heading<' in html
    assert '>Keyword<' in html
    assert '>Link<' in html
    assert 'src="/api/notebooks/LFW/pages/page-a/image?scale=0.25"' in html


def test_render_index_escapes_notebook_and_page_values() -> None:
    organizer_ui = _ui_module()
    snapshot = _snapshot_payload()
    snapshot["notebook_name"] = 'LFW"><script>'

    html = organizer_ui.render_index(
        notebooks=[{"name": 'LFW"><script>'}],
        snapshot=snapshot,
    )

    assert 'LFW"><script>' not in html
    assert "LFW&quot;&gt;&lt;script&gt;" in html
