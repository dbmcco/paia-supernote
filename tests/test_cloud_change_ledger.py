"""Tests for the durable Supernote Cloud change ledger (storage slice).

Covers initial/unchanged/add/update/remove/reorder snapshots, no-op polls,
cursor persistence and per-agent isolation, migration idempotency alongside
existing page_state rows, and the notebook allowlist config helpers.
"""

from __future__ import annotations

from pathlib import Path

from paia_supernote.agent_read_contracts import SupernoteReadContract
from paia_supernote.agent_write_contracts import _canonical_allowed_notebook
from paia_supernote.cloud_change_ledger import (
    CHANGE_ADDED,
    CHANGE_REMOVED,
    CHANGE_REORDER,
    CHANGE_UPDATED,
    CloudChangeLedger,
    NotebookSnapshot,
    PageRevision,
)
from paia_supernote.config import (
    notebook_is_ledger_allowlisted,
    resolve_ledger_notebooks,
)
from paia_supernote.page_state import PageStateStore


def _snapshot(
    notebook: str,
    revision: str,
    pages: list[tuple[str, str]],
    update_time: int | None = None,
) -> NotebookSnapshot:
    """Build a snapshot where page_index follows list position."""
    return NotebookSnapshot(
        notebook=notebook,
        cloud_revision=revision,
        cloud_update_time=update_time,
        pages=[
            PageRevision(page_id=pid, page_index=idx, content_hash=content_hash)
            for idx, (pid, content_hash) in enumerate(pages)
        ],
    )


def _types(changes: list) -> list[str]:
    return sorted(c.change_type for c in changes)


# --- snapshot diffing --------------------------------------------------------


def test_initial_snapshot_records_added_change_for_every_page(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()

    changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")])
    )

    assert _types(changes) == [CHANGE_ADDED, CHANGE_ADDED]
    assert {c.page_id for c in changes} == {"p0", "p1"}
    assert ledger.latest_change_id("Quick") is not None


def test_unchanged_snapshot_is_noop_and_creates_no_duplicate_changes(
    tmp_path: Path,
) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    snapshot = _snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")])
    ledger.apply_snapshot(snapshot)

    second = ledger.apply_snapshot(snapshot)

    assert second == []
    assert ledger.latest_change_id("Quick") is not None


def test_added_pages_produce_added_changes(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0")]))

    changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-2", [("p0", "h0"), ("p1", "h1"), ("p2", "h2")])
    )

    assert _types(changes) == [CHANGE_ADDED, CHANGE_ADDED]
    assert {c.page_id for c in changes} == {"p1", "p2"}
    assert all(c.new_hash is not None and c.old_hash is None for c in changes)


def test_updated_page_produces_updated_change_with_stable_page_id(
    tmp_path: Path,
) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))

    changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-2", [("p0", "h0-changed"), ("p1", "h1")])
    )

    assert _types(changes) == [CHANGE_UPDATED]
    assert changes[0].page_id == "p0"
    assert changes[0].old_hash == "h0"
    assert changes[0].new_hash == "h0-changed"


def test_removed_pages_are_marked_removed(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))

    changes = ledger.apply_snapshot(_snapshot("Quick", "rev-2", [("p0", "h0")]))

    assert _types(changes) == [CHANGE_REMOVED]
    assert changes[0].page_id == "p1"
    assert changes[0].old_hash == "h1"
    assert changes[0].new_hash is None


def test_reorder_only_diff_marks_reorder_not_updated(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    # Same ids + hashes, rotated so every page changes relative position.
    ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("a", "hA"), ("b", "hB"), ("c", "hC")])
    )
    reorder_snapshot = NotebookSnapshot(
        notebook="Quick",
        cloud_revision="rev-2",
        cloud_update_time=None,
        pages=[
            PageRevision(page_id="b", page_index=0, content_hash="hB"),
            PageRevision(page_id="c", page_index=1, content_hash="hC"),
            PageRevision(page_id="a", page_index=2, content_hash="hA"),
        ],
    )

    changes = ledger.apply_snapshot(reorder_snapshot)

    assert _types(changes) == [CHANGE_REORDER, CHANGE_REORDER, CHANGE_REORDER]
    assert not any(c.change_type == CHANGE_UPDATED for c in changes)


def test_reorder_only_diff_preserves_content_hashes(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("a", "hA"), ("b", "hB"), ("c", "hC")])
    )

    changes = ledger.apply_snapshot(
        NotebookSnapshot(
            notebook="Quick",
            cloud_revision="rev-2",
            cloud_update_time=None,
            pages=[
                PageRevision(page_id="c", page_index=0, content_hash="hC"),
                PageRevision(page_id="a", page_index=1, content_hash="hA"),
                PageRevision(page_id="b", page_index=2, content_hash="hB"),
            ],
        )
    )

    for change in changes:
        assert change.change_type == CHANGE_REORDER
        assert change.old_hash == change.new_hash
        assert change.old_hash in {"hA", "hB", "hC"}


def test_partial_reorder_only_marks_moved_pages(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("a", "hA"), ("b", "hB"), ("c", "hC")])
    )
    # Only b and c swap; a stays in relative position 0.
    changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-2", [("a", "hA"), ("c", "hC"), ("b", "hB")])
    )

    assert _types(changes) == [CHANGE_REORDER, CHANGE_REORDER]
    assert {c.page_id for c in changes} == {"b", "c"}


def test_cloud_update_time_change_without_content_change_is_noop(
    tmp_path: Path,
) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("p0", "h0")], update_time=1000)
    )

    changes = ledger.apply_snapshot(
        _snapshot("Quick", "rev-1", [("p0", "h0")], update_time=2000)
    )

    assert changes == []


def test_zero_page_snapshot_then_add(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    assert ledger.apply_snapshot(_snapshot("Quick", "rev-1", [])) == []

    changes = ledger.apply_snapshot(_snapshot("Quick", "rev-2", [("p0", "h0")]))
    assert _types(changes) == [CHANGE_ADDED]


# --- cursors -----------------------------------------------------------------


def test_agent_cursor_persists_and_advances(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))

    latest = ledger.latest_change_id("Quick")
    assert latest is not None
    assert ledger.get_agent_cursor("Sam", "Quick") is None

    advanced = ledger.advance_agent_cursor("Sam", "Quick", latest)

    assert advanced is True
    assert ledger.get_agent_cursor("Sam", "Quick") == latest


def test_agent_cursor_only_advances_monotonically(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))
    latest = ledger.latest_change_id("Quick")
    assert latest is not None

    assert ledger.advance_agent_cursor("Sam", "Quick", latest) is True
    # Re-advancing to the same or earlier cursor is a no-op.
    assert ledger.advance_agent_cursor("Sam", "Quick", latest) is False
    assert ledger.advance_agent_cursor("Sam", "Quick", latest - 1) is False


def test_agent_cursors_are_isolated_per_agent(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))
    latest = ledger.latest_change_id("Quick")
    assert latest is not None

    ledger.advance_agent_cursor("Sam", "Quick", latest)

    assert ledger.get_agent_cursor("Sam", "Quick") == latest
    assert ledger.get_agent_cursor("Caroline", "Quick") is None


def test_changes_since_returns_ordered_changes_after_cursor(tmp_path: Path) -> None:
    ledger = CloudChangeLedger(tmp_path / "ledger.db")
    ledger.init_schema()
    ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0"), ("p1", "h1")]))
    cursor = ledger.latest_change_id("Quick")
    assert cursor is not None
    second = ledger.apply_snapshot(
        _snapshot("Quick", "rev-2", [("p0", "h0-x"), ("p1", "h1"), ("p2", "h2")])
    )

    delta = ledger.changes_since("Quick", cursor)

    assert [c.change_id for c in delta] == [c.change_id for c in second]
    assert _types(delta) == sorted(
        CHANGE_ADDED if c.page_id == "p2" else CHANGE_UPDATED for c in delta
    )


# --- migration / coexistence -------------------------------------------------


def test_migration_is_idempotent_and_preserves_page_state_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    page_store = PageStateStore(db_path)
    page_store.init_schema()
    page_store.upsert_ocr_page("Quick", 19, "rev-1", "raw-19", "glm-4.5v")
    page_store.upsert_ocr_page("Quick", 20, "rev-1", "raw-20", "glm-4.5v")

    ledger = CloudChangeLedger(db_path)
    ledger.init_schema()  # adds ledger tables alongside page_state
    ledger.init_schema()  # idempotent re-run

    # Existing page_state rows must survive untouched.
    assert page_store.get_page("Quick", 19).raw_text == "raw-19"
    assert page_store.get_page("Quick", 20).raw_text == "raw-20"
    # Ledger is functional on the shared database.
    changes = ledger.apply_snapshot(_snapshot("Quick", "rev-1", [("p0", "h0")]))
    assert _types(changes) == [CHANGE_ADDED]


def test_ledger_tables_created_on_fresh_database(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    ledger = CloudChangeLedger(db_path)
    ledger.init_schema()
    assert db_path.exists()


# --- config allowlist --------------------------------------------------------


def test_resolve_ledger_notebooks_falls_back_to_folio_sync_when_allowlist_unset(
    tmp_path: Path,
) -> None:
    config = {"folio_sync_notebooks": ["LFW", "Synthera"]}
    assert resolve_ledger_notebooks(config) == ["LFW", "Synthera"]


def test_resolve_ledger_notebooks_uses_explicit_allowlist_when_set(
    tmp_path: Path,
) -> None:
    config = {
        "folio_sync_notebooks": ["LFW"],
        "cloud_change_ledger_notebooks": ["Quick", "Dev"],
    }
    assert resolve_ledger_notebooks(config) == ["Quick", "Dev"]


def test_resolve_ledger_notebooks_empty_when_neither_set(tmp_path: Path) -> None:
    assert resolve_ledger_notebooks({}) == []


def test_notebook_is_ledger_allowlisted_case_insensitive_and_excludes_others(
    tmp_path: Path,
) -> None:
    config = {"cloud_change_ledger_notebooks": ["Quick", "Dev"]}
    assert notebook_is_ledger_allowlisted(config, "quick") is True
    assert notebook_is_ledger_allowlisted(config, "DEV") is True
    assert notebook_is_ledger_allowlisted(config, "LFW") is False


def test_legacy_only_folio_sync_config_still_resolves(tmp_path: Path) -> None:
    """A config with only the legacy key behaves exactly as before."""
    legacy_config = {"folio_sync_notebooks": ["LFW", "Synthera", "Navicyte"]}
    assert resolve_ledger_notebooks(legacy_config) == ["LFW", "Synthera", "Navicyte"]
    assert notebook_is_ledger_allowlisted(legacy_config, "synthera") is True
    assert notebook_is_ledger_allowlisted(legacy_config, "Synth") is False


def test_explicit_empty_allowlist_overrides_folio_sync_fallback(
    tmp_path: Path,
) -> None:
    """Setting the explicit allowlist to [] disables the ledger even when the
    legacy ``folio_sync_notebooks`` key is present.  Explicit presence (even
    empty) must win over the legacy fallback so operators can intentionally
    opt out of ledger processing during migration.
    """
    config = {
        "folio_sync_notebooks": ["LFW", "Synthera"],
        "cloud_change_ledger_notebooks": [],
    }
    assert resolve_ledger_notebooks(config) == []
    assert notebook_is_ledger_allowlisted(config, "LFW") is False
    assert notebook_is_ledger_allowlisted(config, "Synthera") is False


def test_note_suffix_in_allowlist_agrees_across_all_membership_paths(
    tmp_path: Path,
) -> None:
    """Regression for adversarial finding N1: configuring an allowlist entry
    with a trailing ``.note`` suffix must not split membership decisions.

    Before the fix ``resolve_ledger_notebooks`` kept the suffix, so the Cloud
    poller watch set held ``{"quick.note"}`` while the extracted Cloud stem was
    ``"quick"`` and the notebook was skipped before download — yet the
    read/write contracts stripped the suffix and accepted the name. Stripping
    the suffix in ``resolve_ledger_notebooks`` makes the poller watch set, the
    ingest allowlist gate, and the read/write canonical forms all agree on the
    bare stem.
    """
    config = {"cloud_change_ledger_notebooks": ["Quick.note"]}

    # Poller watch set / ingest membership consume bare stems.
    resolved = resolve_ledger_notebooks(config)
    assert resolved == ["Quick"]
    poller_keys = {name.casefold() for name in resolved}
    assert poller_keys == {"quick"}  # matches the Cloud-extracted stem "Quick"
    assert notebook_is_ledger_allowlisted(config, "Quick") is True
    assert notebook_is_ledger_allowlisted(config, "quick") is True
    assert notebook_is_ledger_allowlisted(config, "Quick.note") is True
    assert notebook_is_ledger_allowlisted(config, "Missing") is False

    # Write contract canonicalization accepts both forms and resolves to the
    # bare stem, consistent with the now-normalized allowlist.
    assert _canonical_allowed_notebook(config, "Quick") == "Quick"
    assert _canonical_allowed_notebook(config, "Quick.note") == "Quick"
    assert _canonical_allowed_notebook(config, "Missing") is None

    # Read contract canonicalization agrees on the same bare stem.
    contract = SupernoteReadContract(config, tmp_path / "state.db")
    assert contract._canonical_notebook("Quick") == "Quick"
    assert contract._canonical_notebook("Quick.note") == "Quick"
