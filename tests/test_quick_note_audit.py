from pathlib import Path

from paia_supernote.page_state import PageStateStore
from paia_supernote.quick_note_audit import (
    QuickAuditPage,
    QuickAuditTaxonomy,
    classify_quick_page,
)


def test_page_state_store_lists_pages_for_notebook_in_page_order(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 2, "rev-2", "second page", "test-model")
    store.upsert_ocr_page("LFW", 0, "rev-lfw", "other notebook", "test-model")
    store.upsert_ocr_page("Quick", 0, "rev-0", "first page", "test-model")

    pages = store.list_pages("Quick")

    assert [page.page for page in pages] == [0, 2]
    assert [page.raw_text for page in pages] == ["first page", "second page"]
    assert [page.source_revision for page in pages] == ["rev-0", "rev-2"]


def test_classify_quick_page_routes_paia_system_thinking() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=47,
        source_revision="rev",
        raw_text="Speed rush\nHow do I make this more of an entire harness?\nwork graph",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "PAIA"
    assert "system/workgraph" in decision.tags
    assert decision.confidence >= 0.7


def test_classify_quick_page_routes_working_items_to_mgmt() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=42,
        source_revision="rev",
        raw_text="Projects/focus to define\noutreach engine\nMeetup engine\n260430",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "Mgmt"
    assert "work/current" in decision.tags


def test_classify_quick_page_routes_decomp_frameworks_to_decomp_note() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=8,
        source_revision="rev",
        raw_text="Loops of Work\nMAIN/overall\nSub loop 1\nSub loop 2",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "(de)comp"
    assert "thought/decomp" in decision.tags


def test_classify_quick_page_marks_short_scraps_for_review() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=56,
        source_revision="rev",
        raw_text="F# B E D G C# B",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "needs_review"
    assert decision.target_notebook is None
    assert decision.confidence < 0.5
