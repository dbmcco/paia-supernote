from __future__ import annotations

import hashlib
import json
import sqlite3
from .db import connect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class FilingOperation:
    operation_id: str
    created_at: str
    updated_at: str
    status: str
    source_notebook: str
    source_pages: list[int]
    source_revision: str
    detected_header: str
    detected_tags: list[str]
    bundle_key: str | None
    target_notebook: str | None
    target_insert_position: str | None
    target_revision_before: str | None
    target_revision_after: str | None
    quick_revision_after: str | None
    routing_reason: str
    confidence: float
    error: str | None
    completed_at: str | None


class FilingLedger:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def init_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS filing_operations (
                    operation_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_notebook TEXT NOT NULL,
                    source_pages TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    detected_header TEXT NOT NULL,
                    detected_tags TEXT NOT NULL,
                    bundle_key TEXT,
                    target_notebook TEXT,
                    target_insert_position TEXT,
                    target_revision_before TEXT,
                    target_revision_after TEXT,
                    quick_revision_after TEXT,
                    routing_reason TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    error TEXT,
                    completed_at TEXT
                )
                """
            )

    @staticmethod
    def operation_id_for(
        *,
        source_notebook: str,
        source_pages: list[int],
        source_revision: str,
        target_notebook: str | None,
    ) -> str:
        parts = [
            source_notebook,
            json.dumps(source_pages, separators=(",", ":")),
            source_revision,
            target_notebook or "",
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def upsert_detected(
        self,
        *,
        source_notebook: str,
        source_pages: list[int],
        source_revision: str,
        detected_header: str,
        detected_tags: list[str],
        bundle_key: str | None,
        target_notebook: str | None,
        routing_reason: str,
        confidence: float,
        target_insert_position: str | None = "end",
        target_revision_before: str | None = None,
    ) -> FilingOperation:
        self.init_schema()
        operation_id = self.operation_id_for(
            source_notebook=source_notebook,
            source_pages=source_pages,
            source_revision=source_revision,
            target_notebook=target_notebook,
        )
        now = _now()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO filing_operations (
                    operation_id, created_at, updated_at, status,
                    source_notebook, source_pages, source_revision,
                    detected_header, detected_tags, bundle_key,
                    target_notebook, target_insert_position,
                    target_revision_before, routing_reason, confidence
                ) VALUES (?, ?, ?, 'detected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO NOTHING
                """,
                (
                    operation_id,
                    now,
                    now,
                    source_notebook,
                    json.dumps(source_pages),
                    source_revision,
                    detected_header,
                    json.dumps(detected_tags),
                    bundle_key,
                    target_notebook,
                    target_insert_position,
                    target_revision_before,
                    routing_reason,
                    confidence,
                ),
            )
        return self.get(operation_id)

    def mark_target_written(
        self, operation_id: str, *, target_revision_after: str
    ) -> None:
        self._update(
            operation_id,
            status="target_written",
            target_revision_after=target_revision_after,
            error=None,
        )

    def mark_target_written_source_pending(
        self, operation_id: str, *, target_revision_after: str, error: str
    ) -> None:
        self._update(
            operation_id,
            status="target_written_source_pending",
            target_revision_after=target_revision_after,
            error=error,
        )

    def mark_source_removed(
        self, operation_id: str, *, quick_revision_after: str
    ) -> None:
        self._update(
            operation_id,
            status="source_removed",
            quick_revision_after=quick_revision_after,
            error=None,
        )

    def mark_completed(self, operation_id: str) -> None:
        self._update(operation_id, status="completed", completed_at=_now(), error=None)

    def mark_failed(self, operation_id: str, *, error: str) -> None:
        self._update(operation_id, status="failed", error=error)

    def get(self, operation_id: str) -> FilingOperation:
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT operation_id, created_at, updated_at, status,
                       source_notebook, source_pages, source_revision,
                       detected_header, detected_tags, bundle_key,
                       target_notebook, target_insert_position,
                       target_revision_before, target_revision_after,
                       quick_revision_after, routing_reason, confidence,
                       error, completed_at
                FROM filing_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return FilingOperation(
            operation_id=row[0],
            created_at=row[1],
            updated_at=row[2],
            status=row[3],
            source_notebook=row[4],
            source_pages=json.loads(row[5]),
            source_revision=row[6],
            detected_header=row[7],
            detected_tags=json.loads(row[8]),
            bundle_key=row[9],
            target_notebook=row[10],
            target_insert_position=row[11],
            target_revision_before=row[12],
            target_revision_after=row[13],
            quick_revision_after=row[14],
            routing_reason=row[15],
            confidence=row[16],
            error=row[17],
            completed_at=row[18],
        )

    def _update(self, operation_id: str, **values: str | None) -> None:
        if not values:
            return
        values["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), operation_id]
        with connect(self._db_path) as conn:
            cur = conn.execute(
                f"UPDATE filing_operations SET {assignments} WHERE operation_id = ?",
                params,
            )
        if cur.rowcount != 1:
            raise KeyError(operation_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
