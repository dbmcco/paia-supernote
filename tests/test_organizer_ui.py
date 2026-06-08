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


def test_render_index_sidebar_links_select_notebooks_through_organizer_route() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick Note"}],
        snapshot=_snapshot_payload(),
    )

    assert 'href="/organizer?notebook=LFW"' in html
    assert 'href="/organizer?notebook=Quick%20Note"' in html
    assert 'data-drop-target="notebook"' in html
    assert 'data-notebook-name="LFW"' in html
    assert 'data-notebook-name="Quick Note"' in html
    assert 'href="/notebooks/' not in html


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
    assert 'src="/api/notebooks/LFW/pages/page-a/image?scale=0.25&amp;revision=rev-1"' in html
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html


def test_render_index_includes_drag_apply_controls_and_contract() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}],
        snapshot=_snapshot_payload(),
    )

    assert 'id="undo-order"' in html
    assert 'id="apply-order"' in html
    assert 'id="organizer-status"' in html
    assert 'draggable="true"' in html
    assert html.count('draggable="true"') == 2
    assert html.count('class="drag-handle"') == 2
    assert 'aria-label="Drag page"' in html
    assert "touch-action: none" in html
    assert "/reorder/preview" in html
    assert "/reorder/apply" in html
    assert "Applied. Refreshing..." in html
    assert "expected_revision" in html
    assert "page_order" in html
    assert "dragstart" in html
    assert "pointerdown" in html
    assert "pointermove" in html
    assert "setPointerCapture" in html
    assert "closest('.page-tile')" in html
    assert "drop" in html


def test_render_index_includes_cross_note_move_drop_contract() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert "movePageToNotebook" in html
    assert "/move" in html
    assert "Moving page to" in html
    assert "Moved page to" in html
    assert "Apply or undo the current reorder before moving pages to another note." in html
    assert "partial_move_target_uploaded_source_failed" in html
    assert "dragover" in html
    assert "dragenter" in html
    assert "dragleave" in html


def test_render_index_supports_pointer_drop_to_sidebar_notebook() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert "function notebookDropTargetFromPointer" in html
    assert "function updatePointerDropTarget" in html
    assert "pointerDropTarget" in html
    assert "await movePageToNotebook(draggedTile, target.dataset.notebookName)" in html
    assert "target.classList.add('drop-hover')" in html
    assert "target.classList.remove('drop-hover')" in html


def test_render_index_includes_shared_searchable_move_picker_for_other_notes() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[
            {"name": "LFW", "update_time": 30},
            {"name": "Project Notes", "update_time": 10},
            {"name": "Quick", "update_time": 20},
        ],
        snapshot=_snapshot_payload(),
    )

    assert html.count('class="move-dialog"') == 1
    assert html.count('id="move-search"') == 1
    assert "Search notes" in html
    assert ">Recent notes<" in html
    assert html.count('class="move-target-list"') == 1
    assert html.count('class="move-button"') == 2
    assert html.count('aria-label="Move page to another note"') == 2
    assert html.count('<button type="button" data-move-target="notebook"') == 2
    assert 'data-move-notebook="Quick"' in html
    assert 'data-move-notebook="Project Notes"' in html
    assert 'data-move-notebook="LFW"' not in html
    assert '>Quick</span>' in html
    assert '>Project Notes</span>' in html
    assert 'class="move-target-name">Quick</span>' in html
    assert 'class="move-target-meta">Modified' in html
    assert html.index('data-move-notebook="Quick"') < html.index(
        'data-move-notebook="Project Notes"'
    )


def test_render_index_move_picker_uses_existing_cross_note_move_flow() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert "closest('[data-move-target=\"notebook\"]')" in html
    assert "movePageToNotebook(activeMoveTile, target.dataset.moveNotebook)" in html
    assert "activeMoveTile = tile" in html
    assert "event.stopPropagation()" in html
    assert "openMoveDialog" in html
    assert "closeMoveDialog" in html
    assert "filterMoveTargets" in html


def test_render_index_cross_note_moves_block_preexisting_unapplied_reorder() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert "if (isDirty() && (!draggedTile || draggedTile !== tile || dragStartedDirty))" in html
    assert "Apply or undo the current reorder before moving pages to another note." in html


def test_render_index_uses_deterministic_drag_slots_and_live_page_numbers() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}],
        snapshot=_snapshot_payload(),
    )

    assert html.count('class="page-number"') == 2
    assert "function insertionIndexFromPointer" in html
    assert "function renumberTiles" in html
    assert "renumberTiles();" in html
    assert "clientX > rect.left + rect.width / 2" not in html


def test_render_index_allows_dragging_from_card_except_move_menu() -> None:
    organizer_ui = _ui_module()

    html = organizer_ui.render_index(
        notebooks=[{"name": "LFW"}, {"name": "Quick"}],
        snapshot=_snapshot_payload(),
    )

    assert "event.target.closest('.page-tile')" in html
    assert "event.target.closest('.move-button, .move-dialog')" in html
    assert "event.target.closest('.drag-handle')" not in html
    assert "cursor: grab" in html


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
