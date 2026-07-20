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
from typing import Union

DbPath = Union[Path, str]


def connect(db_path: DbPath) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + busy_timeout for safe concurrency."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
