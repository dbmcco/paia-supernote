"""Shared SQLite connection helpers for the Supernote state stores.

All ledger/page-state tables live in one database file that is opened
per-operation. Centralizing connection setup here ensures every connection
runs with the same concurrency pragmas:

* ``journal_mode=WAL`` — readers never block on writers (and vice versa), so an
  ingest ``apply_snapshot`` write transaction overlapping an agent read no
  longer raises ``database is locked``. WAL is persistent on the DB file, but
  re-stating it is idempotent and self-heals a file that was opened in another
  mode.
* ``busy_timeout=5000`` — for the remaining writer/writer contention, wait up
  to 5s for a lock instead of failing fast.

``connect`` returns a plain ``sqlite3.Connection`` so it is a drop-in for the
existing ``with sqlite3.connect(...) as conn:`` call sites.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, List, Tuple, Union

DbPath = Union[Path, str]

#: A single schema migration: (version, apply). ``apply`` runs inside the same
#: connection and may assume its target table already exists (owning store has
#: called init_schema). Migrations run once per DB file, guarded by user_version.
Migration = Tuple[int, Callable[[sqlite3.Connection], None]]

#: Ordered schema migrations. Empty today (schema is current); append future
#: ``ALTER TABLE`` migrations here as ``(version, apply_fn)`` tuples. Each store
#: calls :func:`migrate` from its ``init_schema`` after its CREATE TABLE, so a
#: migration applies automatically the first time any owning store opens.
MIGRATIONS: List[Migration] = []


def connect(db_path: DbPath) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + busy_timeout for safe concurrency."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(
    conn: sqlite3.Connection,
    *,
    migrations: List[Migration] | None = None,
) -> None:
    """Apply pending schema migrations, stamped via ``PRAGMA user_version``.

    Each migration runs at most once per database file (its version is recorded
    in ``user_version``). Migrations whose target table does not yet exist are
    deferred — left un-applied and un-versioned — so that on a shared database
    opened by several stores, a migration only runs once the owning store has
    created its table; the next ``migrate`` call (from any store) then applies
    it. Idempotent and safe to call on every ``init_schema``.
    """
    if migrations is None:
        migrations = MIGRATIONS
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, apply_fn in migrations:
        if version <= current:
            continue
        try:
            apply_fn(conn)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                # Target table not created yet by its owning store; defer.
                continue
            raise
        conn.execute(f"PRAGMA user_version = {int(version)}")
        current = int(version)
