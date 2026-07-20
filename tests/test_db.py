"""Concurrency pragmas for the shared Supernote state database."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from paia_supernote.db import connect


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
