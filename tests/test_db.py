"""Concurrency pragmas for the shared Supernote state database."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from paia_supernote.db import connect, migrate


def test_connect_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    conn = connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert mode == "wal"
    assert timeout == 5000


def test_reader_does_not_lock_during_concurrent_write(tmp_path: Path) -> None:
    """Under WAL a read overlapping a write must succeed, not raise 'database is locked'."""
    db = str(tmp_path / "state.db")
    setup = connect(db)
    setup.execute("CREATE TABLE t (x INTEGER)")
    setup.commit()
    setup.close()

    held = threading.Event()
    release = threading.Event()
    errors: list[sqlite3.OperationalError] = []

    def writer() -> None:
        # Raw writer holding an IMMEDIATE write lock until released.
        c = sqlite3.connect(db)
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO t VALUES (1)")
        held.set()
        release.wait(timeout=5)
        c.commit()
        c.close()

    def reader() -> None:
        held.wait(timeout=5)
        try:
            c = connect(db)
            c.execute("SELECT COUNT(*) FROM t").fetchone()
            c.close()
        except sqlite3.OperationalError as exc:
            errors.append(exc)

    tw = threading.Thread(target=writer)
    tr = threading.Thread(target=reader)
    tw.start()
    tr.start()
    tr.join(timeout=10)
    release.set()
    tw.join(timeout=5)
    assert not errors, f"reader raised during concurrent write: {errors}"


# --- schema migrations (PRAGMA user_version) --------------------------------


def test_migrate_applies_pending_migration_adding_column(tmp_path: Path) -> None:
    """Opening a DB at user_version=N applies migrations > N, bumping the version
    and adding the column while preserving existing rows."""
    conn = connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO foo (id) VALUES (7)")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()

    def add_bar(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE foo ADD COLUMN bar TEXT")

    migrate(conn, migrations=[(1, add_bar)])

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    cols = {row[1] for row in conn.execute("PRAGMA table_info(foo)")}
    assert "bar" in cols
    # Old data intact; new column defaulted.
    assert conn.execute("SELECT id, bar FROM foo").fetchone() == (7, None)
    conn.close()


def test_migrate_skips_already_applied_migrations(tmp_path: Path) -> None:
    """A migration whose version <= current user_version is not re-run."""
    conn = connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()

    calls: list[int] = []

    def add_x(c: sqlite3.Connection) -> None:
        calls.append(1)
        c.execute("ALTER TABLE foo ADD COLUMN x INTEGER")

    migrate(conn, migrations=[(3, add_x)])  # version 3 already applied
    assert calls == []
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    conn.close()


def test_migrate_defers_when_target_table_does_not_exist(tmp_path: Path) -> None:
    """A migration whose target table isn't created yet (cross-store ordering on
    a shared DB) must defer — leave user_version unchanged — so the owning store's
    later init_schema can create the table and the migration applies next time."""
    conn = connect(tmp_path / "state.db")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()

    def add_to_ghost(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE ghost ADD COLUMN x TEXT")

    migrate(conn, migrations=[(1, add_to_ghost)])  # no such table: ghost

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0  # deferred
    conn.close()


def test_store_init_schema_applies_registered_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration registered in db.MIGRATIONS applies automatically when any
    store opens via init_schema — the wiring that makes future ALTERs land
    without manual intervention."""
    import paia_supernote.db as db_module
    from paia_supernote.page_state import PageStateStore

    def add_test_col(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE page_state ADD COLUMN migration_probe TEXT")

    monkeypatch.setattr(db_module, "MIGRATIONS", [(1, add_test_col)])

    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()

    conn = connect(tmp_path / "state.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(page_state)")}
    assert "migration_probe" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()
