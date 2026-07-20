"""Durable SQLite-backed Cloud change ledger for allowlisted Supernote notebooks.

Storage slice only. This module persists notebook snapshots, ordered page
revisions keyed by stable Supernote page IDs, ordered change events, and
per-agent cursors. It performs deterministic diffing between successive
snapshots (added / updated / removed / reordered) but performs no Cloud I/O
and no OCR.

Page IDs are the primary identity; page indexes are positional evidence only.
Repeated polls of identical notebook content are idempotent and create no
duplicate change records.
"""

from __future__ import annotations

import hashlib
import sqlite3
from .db import connect, migrate
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: Change-type constants for ``page_change`` rows.
CHANGE_ADDED = "added"
CHANGE_UPDATED = "updated"
CHANGE_REMOVED = "removed"
CHANGE_REORDER = "reorder"


@dataclass(frozen=True, slots=True)
class PageRevision:
    """A single page within a notebook snapshot, keyed by its stable page ID."""

    page_id: str
    page_index: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class NotebookSnapshot:
    """Input snapshot of an allowlisted notebook at a Cloud revision.

    ``pages`` must be ordered by ``page_index`` by the caller; the ledger uses
    the supplied ``page_index`` as positional evidence and never derives page
    identity from it.
    """

    notebook: str
    cloud_revision: str
    cloud_update_time: int | None
    pages: Sequence[PageRevision]


@dataclass(frozen=True, slots=True)
class PageChangeRecord:
    """A persisted change event for one page between two snapshots."""

    change_id: int
    notebook: str
    revision: str
    page_id: str
    change_type: str
    old_hash: str | None
    new_hash: str | None
    old_index: int | None
    new_index: int | None
    observed_at: str


@dataclass(frozen=True, slots=True)
class StoredNotebookState:
    """The current cached ledger state for one notebook."""

    notebook: str
    cloud_revision: str
    notebook_hash: str
    cloud_update_time: int | None
    page_count: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class StoredPageState:
    """The current cached ledger state for one notebook page."""

    notebook: str
    page_id: str
    page_index: int
    content_hash: str
    first_seen_revision: str
    last_seen_revision: str
    ocr_status: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class _PendingChange:
    page_id: str
    change_type: str
    old_hash: str | None
    new_hash: str | None
    old_index: int | None
    new_index: int | None


@dataclass(frozen=True, slots=True)
class _StoredPage:
    page_id: str
    page_index: int
    content_hash: str
    first_seen_revision: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_notebook_hash(pages: Sequence[PageRevision]) -> str:
    """Deterministic content hash over the ordered page revisions.

    Captures stable page IDs, positional indexes, and content hashes so that a
    no-op poll (identical content) yields the same hash while any content or
    positional change yields a different one.
    """
    digest = hashlib.sha256()
    for page in pages:
        digest.update(page.page_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(page.page_index).encode("ascii"))
        digest.update(b"\x1f")
        digest.update(page.content_hash.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


class CloudChangeLedger:
    """SQLite-backed durable ledger for allowlisted notebook change history."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def init_schema(self) -> None:
        """Create ledger tables. Idempotent and safe alongside ``page_state``."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notebook_snapshot (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook TEXT NOT NULL,
                    cloud_revision TEXT NOT NULL,
                    notebook_hash TEXT NOT NULL,
                    cloud_update_time INTEGER,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'current',
                    observed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_notebook_snapshot_notebook_status
                    ON notebook_snapshot (notebook, status)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_snapshot (
                    notebook TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    first_seen_revision TEXT NOT NULL,
                    last_seen_revision TEXT NOT NULL,
                    removed INTEGER NOT NULL DEFAULT 0,
                    ocr_status TEXT NOT NULL DEFAULT 'pending',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (notebook, page_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_change (
                    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    old_hash TEXT,
                    new_hash TEXT,
                    old_index INTEGER,
                    new_index INTEGER,
                    observed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_page_change_notebook_id
                    ON page_change (notebook, change_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_cursor (
                    agent TEXT NOT NULL,
                    notebook TEXT NOT NULL,
                    last_change_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent, notebook)
                )
                """
            )
            migrate(conn)

    def apply_snapshot(
        self, snapshot: NotebookSnapshot
    ) -> list[PageChangeRecord]:
        """Persist a snapshot and return the change records it produced.

        A poll whose content hash matches the last current snapshot is a no-op
        and returns an empty list without creating duplicate change records.
        """
        notebook = snapshot.notebook
        revision = snapshot.cloud_revision
        observed_at = _now_iso()
        notebook_hash = _compute_notebook_hash(snapshot.pages)
        new_pages = {p.page_id: p for p in snapshot.pages}

        with connect(self._db_path) as conn:
            prev = conn.execute(
                """
                SELECT notebook_hash FROM notebook_snapshot
                WHERE notebook = ? AND status = 'current'
                ORDER BY snapshot_id DESC LIMIT 1
                """,
                (notebook,),
            ).fetchone()
            if prev is not None and prev[0] == notebook_hash:
                return []

            old_rows = conn.execute(
                """
                SELECT page_id, page_index, content_hash, first_seen_revision
                FROM page_snapshot
                WHERE notebook = ? AND removed = 0
                """,
                (notebook,),
            ).fetchall()
            old_pages = {
                row[0]: _StoredPage(
                    page_id=row[0],
                    page_index=row[1],
                    content_hash=row[2],
                    first_seen_revision=row[3],
                )
                for row in old_rows
            }

            pending = _compute_diff(old_pages, new_pages)
            records = self._persist_diff(
                conn,
                notebook=notebook,
                revision=revision,
                observed_at=observed_at,
                pending=pending,
            )
            removed_ids = {
                change.page_id
                for change in pending
                if change.change_type == CHANGE_REMOVED
            }
            self._persist_page_snapshots(
                conn,
                notebook=notebook,
                revision=revision,
                observed_at=observed_at,
                new_pages=new_pages,
                removed_ids=removed_ids,
            )
            conn.execute(
                """
                UPDATE notebook_snapshot SET status = 'superseded'
                WHERE notebook = ? AND status = 'current'
                """,
                (notebook,),
            )
            conn.execute(
                """
                INSERT INTO notebook_snapshot (
                    notebook, cloud_revision, notebook_hash, cloud_update_time,
                    page_count, status, observed_at
                ) VALUES (?, ?, ?, ?, ?, 'current', ?)
                """,
                (
                    notebook,
                    revision,
                    notebook_hash,
                    snapshot.cloud_update_time,
                    len(snapshot.pages),
                    observed_at,
                ),
            )
        return records

    def latest_notebook_state(self, notebook: str) -> StoredNotebookState | None:
        """Return the current cached notebook snapshot, if any."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT notebook, cloud_revision, notebook_hash, cloud_update_time,
                       page_count, observed_at
                FROM notebook_snapshot
                WHERE notebook = ? AND status = 'current'
                ORDER BY snapshot_id DESC LIMIT 1
                """,
                (notebook,),
            ).fetchone()
        if row is None:
            return None
        return StoredNotebookState(
            notebook=row[0],
            cloud_revision=row[1],
            notebook_hash=row[2],
            cloud_update_time=row[3],
            page_count=row[4],
            observed_at=row[5],
        )

    def current_pages(self, notebook: str) -> list[StoredPageState]:
        """Return current non-removed pages ordered by cached page index."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT notebook, page_id, page_index, content_hash,
                       first_seen_revision, last_seen_revision,
                       ocr_status, updated_at
                FROM page_snapshot
                WHERE notebook = ? AND removed = 0
                ORDER BY page_index ASC
                """,
                (notebook,),
            ).fetchall()
        return [
            StoredPageState(
                notebook=row[0],
                page_id=row[1],
                page_index=row[2],
                content_hash=row[3],
                first_seen_revision=row[4],
                last_seen_revision=row[5],
                ocr_status=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]

    def latest_change_id(self, notebook: str) -> int | None:
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT MAX(change_id) FROM page_change WHERE notebook = ?",
                (notebook,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]

    def changes_since(
        self, notebook: str, after_change_id: int
    ) -> list[PageChangeRecord]:
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT change_id, notebook, revision, page_id, change_type,
                       old_hash, new_hash, old_index, new_index, observed_at
                FROM page_change
                WHERE notebook = ? AND change_id > ?
                ORDER BY change_id ASC
                """,
                (notebook, after_change_id),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def changes_after_observed_at(
        self, notebook: str, observed_after: str
    ) -> list[PageChangeRecord]:
        """Return changes observed after an ISO timestamp string."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT change_id, notebook, revision, page_id, change_type,
                       old_hash, new_hash, old_index, new_index, observed_at
                FROM page_change
                WHERE notebook = ? AND observed_at > ?
                ORDER BY change_id ASC
                """,
                (notebook, observed_after),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def get_agent_cursor(self, agent: str, notebook: str) -> int | None:
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT last_change_id FROM agent_cursor
                WHERE agent = ? AND notebook = ?
                """,
                (agent, notebook),
            ).fetchone()
        if row is None:
            return None
        return row[0]

    def advance_agent_cursor(
        self, agent: str, notebook: str, change_id: int
    ) -> bool:
        """Advance an agent cursor. Only monotonically forward moves apply."""
        observed_at = _now_iso()
        with connect(self._db_path) as conn:
            existing = conn.execute(
                """
                SELECT last_change_id FROM agent_cursor
                WHERE agent = ? AND notebook = ?
                """,
                (agent, notebook),
            ).fetchone()
            if existing is not None and existing[0] >= change_id:
                return False
            conn.execute(
                """
                INSERT INTO agent_cursor (agent, notebook, last_change_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent, notebook) DO UPDATE SET
                    last_change_id = excluded.last_change_id,
                    updated_at = excluded.updated_at
                """,
                (agent, notebook, change_id, observed_at),
            )
        return True

    def mark_page_ocr_status(
        self, notebook: str, page_id: str, status: str
    ) -> bool:
        """Record OCR readiness for the current stored page revision."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE page_snapshot
                SET ocr_status = ?, updated_at = ?
                WHERE notebook = ? AND page_id = ? AND removed = 0
                """,
                (status, _now_iso(), notebook, page_id),
            )
        return cur.rowcount == 1

    @staticmethod
    def _persist_diff(
        conn: sqlite3.Connection,
        *,
        notebook: str,
        revision: str,
        observed_at: str,
        pending: list[_PendingChange],
    ) -> list[PageChangeRecord]:
        records: list[PageChangeRecord] = []
        for change in pending:
            cur = conn.execute(
                """
                INSERT INTO page_change (
                    notebook, revision, page_id, change_type,
                    old_hash, new_hash, old_index, new_index, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notebook,
                    revision,
                    change.page_id,
                    change.change_type,
                    change.old_hash,
                    change.new_hash,
                    change.old_index,
                    change.new_index,
                    observed_at,
                ),
            )
            records.append(
                PageChangeRecord(
                    change_id=cur.lastrowid,
                    notebook=notebook,
                    revision=revision,
                    page_id=change.page_id,
                    change_type=change.change_type,
                    old_hash=change.old_hash,
                    new_hash=change.new_hash,
                    old_index=change.old_index,
                    new_index=change.new_index,
                    observed_at=observed_at,
                )
            )
        return records

    @staticmethod
    def _persist_page_snapshots(
        conn: sqlite3.Connection,
        *,
        notebook: str,
        revision: str,
        observed_at: str,
        new_pages: dict[str, PageRevision],
        removed_ids: set[str],
    ) -> None:
        for page in new_pages.values():
            conn.execute(
                """
                INSERT INTO page_snapshot (
                    notebook, page_id, page_index, content_hash,
                    first_seen_revision, last_seen_revision, removed,
                    ocr_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'pending', ?)
                ON CONFLICT(notebook, page_id) DO UPDATE SET
                    page_index = excluded.page_index,
                    ocr_status = CASE
                        WHEN page_snapshot.content_hash != excluded.content_hash
                            THEN 'pending'
                        ELSE page_snapshot.ocr_status
                    END,
                    content_hash = excluded.content_hash,
                    last_seen_revision = excluded.last_seen_revision,
                    removed = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    notebook,
                    page.page_id,
                    page.page_index,
                    page.content_hash,
                    revision,
                    revision,
                    observed_at,
                ),
            )
        for page_id in removed_ids:
            conn.execute(
                """
                UPDATE page_snapshot
                SET removed = 1, updated_at = ?
                WHERE notebook = ? AND page_id = ?
                """,
                (observed_at, notebook, page_id),
            )


def _compute_diff(
    old_pages: dict[str, _StoredPage],
    new_pages: dict[str, PageRevision],
) -> list[_PendingChange]:
    """Deterministically diff two page sets keyed by stable page ID.

    Reorder is detected on relative order among common pages whose content hash
    is unchanged, so adds/removes never produce spurious reorder events.
    """
    old_ids = set(old_pages)
    new_ids = set(new_pages)

    pending: list[_PendingChange] = []

    for page_id in sorted(new_ids - old_ids):
        page = new_pages[page_id]
        pending.append(
            _PendingChange(
                page_id=page_id,
                change_type=CHANGE_ADDED,
                old_hash=None,
                new_hash=page.content_hash,
                old_index=None,
                new_index=page.page_index,
            )
        )

    for page_id in sorted(old_ids - new_ids):
        page = old_pages[page_id]
        pending.append(
            _PendingChange(
                page_id=page_id,
                change_type=CHANGE_REMOVED,
                old_hash=page.content_hash,
                new_hash=None,
                old_index=page.page_index,
                new_index=None,
            )
        )

    common_ids = old_ids & new_ids
    same_hash_common = [
        page_id
        for page_id in common_ids
        if old_pages[page_id].content_hash == new_pages[page_id].content_hash
    ]
    old_relative = {
        page_id: pos
        for pos, page_id in enumerate(
            sorted(same_hash_common, key=lambda pid: old_pages[pid].page_index)
        )
    }
    new_relative = {
        page_id: pos
        for pos, page_id in enumerate(
            sorted(same_hash_common, key=lambda pid: new_pages[pid].page_index)
        )
    }

    for page_id in sorted(common_ids):
        old_page = old_pages[page_id]
        new_page = new_pages[page_id]
        if old_page.content_hash != new_page.content_hash:
            pending.append(
                _PendingChange(
                    page_id=page_id,
                    change_type=CHANGE_UPDATED,
                    old_hash=old_page.content_hash,
                    new_hash=new_page.content_hash,
                    old_index=old_page.page_index,
                    new_index=new_page.page_index,
                )
            )
        elif old_relative[page_id] != new_relative[page_id]:
            pending.append(
                _PendingChange(
                    page_id=page_id,
                    change_type=CHANGE_REORDER,
                    old_hash=old_page.content_hash,
                    new_hash=new_page.content_hash,
                    old_index=old_page.page_index,
                    new_index=new_page.page_index,
                )
            )

    return pending


def _row_to_record(row: sqlite3.Row | tuple) -> PageChangeRecord:
    return PageChangeRecord(
        change_id=row[0],
        notebook=row[1],
        revision=row[2],
        page_id=row[3],
        change_type=row[4],
        old_hash=row[5],
        new_hash=row[6],
        old_index=row[7],
        new_index=row[8],
        observed_at=row[9],
    )
