"""Durable SQLite-backed per-page state store for the Supernote ingest pipeline."""

from __future__ import annotations

import sqlite3
from .db import connect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(slots=True)
class PageState:
    notebook: str
    page: int
    source_revision: str
    raw_text: str
    ocr_model: str
    dirty_for_enrichment: bool
    last_enriched_revision: str | None
    last_folio_object_id: str | None
    retry_count: int
    next_retry_at: str | None
    last_error: str | None
    last_error_stage: str | None


class PageStateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def init_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_state (
                    notebook TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    source_revision TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    ocr_model TEXT NOT NULL,
                    ocr_updated_at TEXT NOT NULL,
                    dirty_for_enrichment INTEGER NOT NULL DEFAULT 1,
                    last_enriched_revision TEXT,
                    last_folio_object_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    last_error_stage TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (notebook, page)
                )
                """
            )

    def upsert_ocr_page(
        self,
        notebook: str,
        page: int,
        source_revision: str,
        raw_text: str,
        ocr_model: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO page_state (
                    notebook, page, source_revision, raw_text, ocr_model,
                    ocr_updated_at, dirty_for_enrichment, retry_count,
                    next_retry_at, last_error, last_error_stage, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL, NULL, ?)
                ON CONFLICT(notebook, page) DO UPDATE SET
                    source_revision=excluded.source_revision,
                    raw_text=excluded.raw_text,
                    ocr_model=excluded.ocr_model,
                    ocr_updated_at=excluded.ocr_updated_at,
                    dirty_for_enrichment=1,
                    retry_count=0,
                    next_retry_at=NULL,
                    last_error=NULL,
                    last_error_stage=NULL,
                    updated_at=excluded.updated_at
                """,
                (notebook, page, source_revision, raw_text, ocr_model, now, now),
            )

    def get_page(self, notebook: str, page: int) -> PageState:
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT notebook, page, source_revision, raw_text, ocr_model,
                       dirty_for_enrichment, last_enriched_revision,
                       last_folio_object_id, retry_count, next_retry_at,
                       last_error, last_error_stage
                FROM page_state
                WHERE notebook = ? AND page = ?
                """,
                (notebook, page),
            ).fetchone()
        assert row is not None
        (
            notebook_, page_, source_revision_, raw_text_, ocr_model_,
            dirty_for_enrichment_, last_enriched_revision_,
            last_folio_object_id_, retry_count_, next_retry_at_,
            last_error_, last_error_stage_,
        ) = row
        return PageState(
            notebook=notebook_,
            page=page_,
            source_revision=source_revision_,
            raw_text=raw_text_,
            ocr_model=ocr_model_,
            dirty_for_enrichment=bool(dirty_for_enrichment_),
            last_enriched_revision=last_enriched_revision_,
            last_folio_object_id=last_folio_object_id_,
            retry_count=retry_count_,
            next_retry_at=next_retry_at_,
            last_error=last_error_,
            last_error_stage=last_error_stage_,
        )

    def list_pages(self, notebook: str) -> list[PageState]:
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT notebook, page, source_revision, raw_text, ocr_model,
                       dirty_for_enrichment, last_enriched_revision,
                       last_folio_object_id, retry_count, next_retry_at,
                       last_error, last_error_stage
                FROM page_state
                WHERE notebook = ?
                ORDER BY page ASC
                """,
                (notebook,),
            ).fetchall()
        return [
            PageState(
                notebook=row[0],
                page=row[1],
                source_revision=row[2],
                raw_text=row[3],
                ocr_model=row[4],
                dirty_for_enrichment=bool(row[5]),
                last_enriched_revision=row[6],
                last_folio_object_id=row[7],
                retry_count=row[8],
                next_retry_at=row[9],
                last_error=row[10],
                last_error_stage=row[11],
            )
            for row in rows
        ]

    def next_dirty_page(self) -> PageState | None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT notebook, page, source_revision, raw_text, ocr_model,
                       dirty_for_enrichment, last_enriched_revision,
                       last_folio_object_id, retry_count, next_retry_at,
                       last_error, last_error_stage
                FROM page_state
                WHERE dirty_for_enrichment = 1
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY updated_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        if row is None:
            return None
        (
            notebook_, page_, source_revision_, raw_text_, ocr_model_,
            dirty_for_enrichment_, last_enriched_revision_,
            last_folio_object_id_, retry_count_, next_retry_at_,
            last_error_, last_error_stage_,
        ) = row
        return PageState(
            notebook=notebook_,
            page=page_,
            source_revision=source_revision_,
            raw_text=raw_text_,
            ocr_model=ocr_model_,
            dirty_for_enrichment=bool(dirty_for_enrichment_),
            last_enriched_revision=last_enriched_revision_,
            last_folio_object_id=last_folio_object_id_,
            retry_count=retry_count_,
            next_retry_at=next_retry_at_,
            last_error=last_error_,
            last_error_stage=last_error_stage_,
        )

    def mark_enriched(
        self,
        notebook: str,
        page: int,
        source_revision: str,
        folio_object_id: str,
    ) -> bool:
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE page_state
                SET dirty_for_enrichment = 0,
                    last_enriched_revision = ?,
                    last_folio_object_id = ?,
                    retry_count = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_error_stage = NULL,
                    updated_at = ?
                WHERE notebook = ? AND page = ? AND source_revision = ?
                """,
                (
                    source_revision,
                    folio_object_id,
                    datetime.now(timezone.utc).isoformat(),
                    notebook,
                    page,
                    source_revision,
                ),
            )
        return cur.rowcount == 1

    def mark_enrichment_skipped(
        self,
        notebook: str,
        page: int,
        source_revision: str,
    ) -> bool:
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE page_state
                SET dirty_for_enrichment = 0,
                    last_enriched_revision = ?,
                    last_folio_object_id = NULL,
                    retry_count = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_error_stage = NULL,
                    updated_at = ?
                WHERE notebook = ? AND page = ? AND source_revision = ?
                """,
                (
                    source_revision,
                    datetime.now(timezone.utc).isoformat(),
                    notebook,
                    page,
                    source_revision,
                ),
            )
        return cur.rowcount == 1

    def mark_failed(
        self,
        notebook: str,
        page: int,
        stage: str,
        error: str,
        retry_delay_seconds: int,
    ) -> None:
        retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
        ).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE page_state
                SET retry_count = retry_count + 1,
                    next_retry_at = ?,
                    last_error = ?,
                    last_error_stage = ?,
                    updated_at = ?
                WHERE notebook = ? AND page = ?
                """,
                (
                    retry_at,
                    error,
                    stage,
                    datetime.now(timezone.utc).isoformat(),
                    notebook,
                    page,
                ),
            )

    def record_ocr_failure(
        self,
        notebook: str,
        page: int,
        source_revision: str,
        ocr_model: str,
        error: str,
        retry_delay_seconds: int,
    ) -> None:
        """Record an OCR failure for retry (upsert; safe on first attempt).

        Unlike mark_failed (UPDATE-only), this inserts a placeholder row when
        the page has never been OCR'd successfully, so a first-attempt vision
        failure is still tracked with a backoff. retry_count starts at 1 on
        insert and increments on subsequent failures.
        """
        retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
        ).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO page_state (
                    notebook, page, source_revision, raw_text, ocr_model,
                    ocr_updated_at, dirty_for_enrichment, retry_count,
                    next_retry_at, last_error, last_error_stage, updated_at
                ) VALUES (?, ?, ?, '', ?, ?, 0, 1, ?, ?, 'ocr', ?)
                ON CONFLICT(notebook, page) DO UPDATE SET
                    retry_count = page_state.retry_count + 1,
                    next_retry_at = excluded.next_retry_at,
                    last_error = excluded.last_error,
                    last_error_stage = excluded.last_error_stage,
                    updated_at = excluded.updated_at
                """,
                (notebook, page, source_revision, ocr_model, now, retry_at, error, now),
            )

    def dirty_count(self) -> int:
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM page_state WHERE dirty_for_enrichment = 1"
            ).fetchone()
        return row[0] if row else 0

    def error_count(self, stage: str | None = None) -> int:
        with connect(self._db_path) as conn:
            if stage:
                row = conn.execute(
                    "SELECT COUNT(*) FROM page_state WHERE last_error IS NOT NULL AND last_error_stage = ?",
                    (stage,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM page_state WHERE last_error IS NOT NULL"
                ).fetchone()
        return row[0] if row else 0
