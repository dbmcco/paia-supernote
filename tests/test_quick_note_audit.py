from pathlib import Path

from paia_supernote.page_state import PageStateStore


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
