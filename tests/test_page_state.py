"""Tests for the durable SQLite-backed page-state store."""

from __future__ import annotations

from pathlib import Path

import pytest

from paia_supernote.page_state import PageStateStore


def test_upsert_page_overwrites_same_notebook_page_and_marks_dirty(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()

    store.upsert_ocr_page(
        notebook="Quick",
        page=19,
        source_revision="rev-1",
        raw_text="first",
        ocr_model="glm-4.5v",
    )
    store.upsert_ocr_page(
        notebook="Quick",
        page=19,
        source_revision="rev-2",
        raw_text="second",
        ocr_model="glm-4.5v",
    )

    row = store.get_page("Quick", 19)
    assert row.source_revision == "rev-2"
    assert row.raw_text == "second"
    assert row.dirty_for_enrichment is True


def test_mark_enriched_only_updates_matching_revision(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-2", "second", "glm-4.5v")

    updated = store.mark_enriched(
        notebook="Quick",
        page=19,
        source_revision="rev-1",
        folio_object_id="abc",
    )

    row = store.get_page("Quick", 19)
    assert updated is False
    assert row.last_enriched_revision is None


def test_next_dirty_page_skips_future_retry_rows(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 19, "rev-2", "second", "glm-4.5v")
    store.mark_failed("Quick", 19, "enrich", "timeout", retry_delay_seconds=300)

    assert store.next_dirty_page() is None
