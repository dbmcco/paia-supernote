"""Ingest service — OCRs pages and writes durable page state.

Uses the Supernote Partner app local sync folder (FSEvents) when available,
falling back to cloud polling when not. The local path requires no auth.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from .cloud_change_ledger import (
    CHANGE_ADDED,
    CHANGE_UPDATED,
    CloudChangeLedger,
    NotebookSnapshot,
    PageChangeRecord,
    PageRevision,
)
from .cloud_poller import BackfillPageCapExceeded, CloudPoller
from .config import notebook_is_ledger_allowlisted, resolve_ledger_notebooks
from .events import EventsClient
from .page_state import PageStateStore
from .reader import SupernoteReader, build_reader
from .uploader import SupernoteUploader
from .watcher import SupernoteWatcher

log = structlog.get_logger(__name__)


class IngestService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        reader: SupernoteReader | None = None,
        uploader: SupernoteUploader | None = None,
        cloud_poller: CloudPoller | None = None,
    ) -> None:
        from .main import load_config
        self.config = config or load_config()
        self.reader = reader or build_reader(self.config)
        self.events = EventsClient(base_url=self.config["events_url"])
        state_db_path = Path(self.config["state_db_path"])
        self.page_state = PageStateStore(state_db_path)
        self.page_state.init_schema()
        self.ledger = CloudChangeLedger(state_db_path)
        self.ledger.init_schema()
        self._watched_notebook_keys = self._build_watched_notebook_keys()
        self._cloud_watched_notebooks = resolve_ledger_notebooks(self.config)

        # Cloud poller path (used only when Partner app sync unavailable)
        self._uploader: Optional[SupernoteUploader] = uploader
        self._cloud_poller: Optional[CloudPoller] = cloud_poller
        self._local_watcher: Optional[SupernoteWatcher] = None

        self._shutdown_event = asyncio.Event()

        # Back-fill cost guard: when set, _on_cloud_note_changed caps total OCR'd
        # pages across an on-demand ingest_once run and aborts cleanly on overflow.
        self._backfill_budget: int | None = None
        self.backfill_aborted: bool = False
        self.backfill_estimate: dict[str, int] = {}

    def _build_watched_notebook_keys(self) -> set[str]:
        """Notebooks to watch: Walk + tasks always, plus folio_sync_notebooks."""
        keys = {"walk", "tasks"}
        keys.update(
            str(name).strip().casefold()
            for name in self.config.get("folio_sync_notebooks") or []
            if str(name).strip()
        )
        return keys

    def _partner_sync_path_available(self) -> bool:
        """True only when the sync folder exists AND has a recently-modified note file.

        Uses a single stat() on a known file rather than iterdir() to avoid
        InterruptedError from signals hitting os.listdir() during startup.
        """
        import time
        path = SupernoteWatcher.DEFAULT_SYNC_PATH
        if not path.exists():
            return False
        cutoff = time.time() - 7 * 24 * 3600  # 7 days
        for name in ("Quick.note", "Walk.note", "LFW.note", "tasks.note"):
            try:
                mtime = (path / name).stat().st_mtime
                if mtime > cutoff:
                    return True
            except OSError:
                continue
        return False

    async def _on_note_changed(
        self,
        notebook_name: str,
        note_bytes: bytes,
        update_time: int | None = None,
    ) -> None:
        """Local-watcher (Partner-app sync folder) ingest callback.

        Allowlisted notebooks route through the SAME ledger-aware path as the
        Cloud poller so a Partner-app sync machine produces identical
        page_change/page_snapshot rows — agents reading ``changes <notebook>``
        see consistent state regardless of transport. Non-ledger notebooks
        (walk, tasks) keep the direct page_state write since they are not
        diff-tracked.
        """
        if notebook_is_ledger_allowlisted(self.config, notebook_name):
            await self._on_cloud_note_changed(
                notebook_name, note_bytes, update_time
            )
            return

        note_hash = hashlib.sha256(note_bytes).hexdigest()
        persisted_pages: set[int] = set()

        async def persist_result(result) -> None:
            source_revision = f"{update_time or 0}:{note_hash}:{result.page_num}"
            self.page_state.upsert_ocr_page(
                notebook=result.notebook,
                page=result.page_num,
                source_revision=source_revision,
                raw_text=result.text,
                ocr_model=self.config["zai_vision_model"],
            )
            persisted_pages.add(result.page_num)
            log.info("ocr_succeeded", notebook=result.notebook, page=result.page_num)

        results = await self.reader.process_file(
            note_bytes,
            notebook_name,
            on_result=persist_result,
        )
        for result in results:
            if result.page_num in persisted_pages:
                continue
            await persist_result(result)

        await self._publish_walk_feedback(notebook_name, results)

    async def _on_cloud_note_changed(
        self,
        notebook_name: str,
        note_bytes: bytes,
        update_time: int | None = None,
    ) -> None:
        """Ledger-aware Cloud ingest: snapshot first, OCR only changed pages."""
        if not notebook_is_ledger_allowlisted(self.config, notebook_name):
            log.info("cloud_ingest_notebook_not_allowlisted", notebook=notebook_name)
            return

        note_hash = hashlib.sha256(note_bytes).hexdigest()
        cloud_revision = f"{update_time or 0}:{note_hash}"
        note_snapshot = self.reader.build_snapshot(
            note_bytes,
            notebook_name,
            revision=cloud_revision,
        )
        page_by_index = {
            page.page_index: page for page in note_snapshot.pages.values()
        }
        ledger_snapshot = NotebookSnapshot(
            notebook=notebook_name,
            cloud_revision=cloud_revision,
            cloud_update_time=update_time,
            pages=[
                PageRevision(
                    page_id=note_snapshot.pages[page_id].page_id,
                    page_index=note_snapshot.pages[page_id].page_index,
                    content_hash=note_snapshot.pages[page_id].content_hash,
                )
                for page_id in note_snapshot.page_order
            ],
        )
        changes = self.ledger.apply_snapshot(ledger_snapshot)
        pages_to_ocr = _ocr_page_indexes(changes)

        # Back-fill cost guard: surface the per-notebook OCR estimate and, when a
        # cap is active, abort before spending vision budget. The estimate is
        # recorded even on abort so the caller can report why it stopped.
        if pages_to_ocr:
            self.backfill_estimate[notebook_name] = len(pages_to_ocr)
            log.info(
                "backfill_page_estimate",
                notebook=notebook_name,
                pages_to_ocr=len(pages_to_ocr),
            )
            if self._backfill_budget is not None:
                if len(pages_to_ocr) > self._backfill_budget:
                    raise BackfillPageCapExceeded(
                        notebook_name,
                        requested=len(pages_to_ocr),
                        remaining=self._backfill_budget,
                    )
                self._backfill_budget -= len(pages_to_ocr)

        results: list = []
        if pages_to_ocr:
            results.extend(
                await self._ocr_and_persist(
                    note_bytes,
                    notebook_name,
                    page_by_index,
                    cloud_revision,
                    pages_to_ocr,
                )
            )
        else:
            log.info(
                "cloud_ingest_no_ocr_needed",
                notebook=notebook_name,
                changes=len(changes),
            )

        # Retry sweep: recover pages still ledger-pending from prior OCR
        # failures whose backoff has elapsed. Runs even when nothing changed,
        # so a page that wedged at `pending` is re-OCRed the next time this
        # notebook is ingested (we already have the bytes in hand).
        due = self._due_pending_indexes(notebook_name)
        if due:
            results.extend(
                await self._ocr_and_persist(
                    note_bytes,
                    notebook_name,
                    page_by_index,
                    cloud_revision,
                    due,
                )
            )

        await self._publish_walk_feedback(notebook_name, results)

    async def _ocr_and_persist(
        self,
        note_bytes: bytes,
        notebook_name: str,
        page_by_index: dict,
        cloud_revision: str,
        pages: list[int],
    ) -> list:
        """OCR pages one at a time, persisting each before the next.

        Bounds the loss from a slow or interrupted back-fill to the single
        in-flight page. At ~90s/page on the vision backend a multi-page notebook
        can outrun any single process; per-page persistence plus the retry
        sweep's resumption of still-pending pages turns a fragile one-shot batch
        into resumable, incremental progress. ``read_pages_resilient`` still
        isolates per-page failures within each single-page call.
        """
        results: list = []
        for page_index in pages:
            ocr_results, ocr_errors = await self.reader.read_pages_resilient(
                note_bytes, notebook_name, pages=[page_index]
            )
            self._persist_ocr_outcome(
                notebook_name, page_by_index, cloud_revision, ocr_results, ocr_errors
            )
            results.extend(ocr_results)
        return results

    def _persist_ocr_outcome(
        self,
        notebook_name: str,
        page_by_index: dict,
        cloud_revision: str,
        results: list,
        errors: dict[int, str],
    ) -> None:
        """Persist successful OCR (page_state + ledger ready) and record failures for retry."""
        retry_delay = int(self.config.get("ocr_retry_delay_seconds", 300))
        for result in results:
            page = page_by_index.get(result.page_num)
            if page is None:
                continue
            source_revision = f"{cloud_revision}:{page.page_id}:{page.content_hash}"
            self.page_state.upsert_ocr_page(
                notebook=result.notebook,
                page=result.page_num,
                source_revision=source_revision,
                raw_text=result.text,
                ocr_model=self.config["zai_vision_model"],
            )
            self.ledger.mark_page_ocr_status(result.notebook, page.page_id, "ready")
            log.info(
                "ocr_succeeded",
                notebook=result.notebook,
                page=result.page_num,
                page_id=page.page_id,
            )
        for page_num, err in errors.items():
            page = page_by_index.get(page_num)
            if page is None:
                continue
            source_revision = f"{cloud_revision}:{page.page_id}:{page.content_hash}"
            self.page_state.record_ocr_failure(
                notebook=notebook_name,
                page=page_num,
                source_revision=source_revision,
                ocr_model=self.config["zai_vision_model"],
                error=err,
                retry_delay_seconds=retry_delay,
            )
            log.warning(
                "ocr_failed",
                notebook=notebook_name,
                page=page_num,
                error=err,
            )

    def _due_pending_indexes(self, notebook_name: str) -> list[int]:
        """Ledger page indexes still pending whose retry backoff has elapsed."""
        now = datetime.now(timezone.utc)
        due: list[int] = []
        for stored in self.ledger.current_pages(notebook_name):
            if stored.ocr_status != "pending":
                continue
            state = self.page_state.get_page(notebook_name, stored.page_index)
            if state is None or state.next_retry_at is None:
                due.append(stored.page_index)
                continue
            try:
                if datetime.fromisoformat(state.next_retry_at) <= now:
                    due.append(stored.page_index)
            except ValueError:
                due.append(stored.page_index)
        return due

    async def _publish_walk_feedback(self, notebook_name: str, results: list) -> None:
        if notebook_name.casefold() != "walk":
            return
        for result in results:
            await self.events.publish_walk_feedback_detected(
                notebook=result.notebook,
                page=result.page_num,
                text=result.text,
            )

    async def _on_poll_health(
        self, healthy: bool, detail: dict[str, Any]
    ) -> None:
        """Emit a monitoring event when cloud read-poll health changes.

        Makes silent 401/403 feedback-ingest outages observable instead of
        leaving them buried in poller log warnings.
        """
        await self.events.publish_feedback_ingest_status(
            healthy=healthy,
            reason=detail.get("reason"),
            status=detail.get("status"),
            notebooks=detail.get("notebooks"),
        )

    async def start(self) -> None:
        log.info("ingest_service_starting")
        await self.events.start()
        try:
            if self._partner_sync_path_available():
                log.info(
                    "ingest_using_local_watcher",
                    path=str(SupernoteWatcher.DEFAULT_SYNC_PATH),
                )
                await self._run_with_local_watcher()
            else:
                log.info("ingest_using_cloud_poller")
                await self._run_with_cloud_poller()
        finally:
            await self.events.stop()

    async def ingest_once(
        self,
        *,
        process_existing_on_start: bool = False,
        notebooks: list[str] | None = None,
        max_pages: int | None = None,
    ) -> None:
        """Run a single Cloud poll cycle, then exit. Never a continuous loop.

        On-demand back-fill / targeted ingest (``ingest --once``). Always uses
        the Cloud poller path: the Partner-app local watcher is event-driven and
        has no notion of "process existing", so back-fill is a Cloud-only concept.

        Args:
            process_existing_on_start: treat every existing watched file as new
                (the cost-guard override for first-time back-fill). ``False`` by
                default so a plain ``--once`` never OCRs the whole Cloud library.
            notebooks: scope the poll to these stems. Defaults to the resolved
                ledger allowlist. The ledger allowlist still gates which
                downloaded notebooks are written — name allowlisted notebooks.
            max_pages: optional back-fill cap on total pages OCR'd this run.
                When a notebook's new pages would exceed the remaining budget,
                the run aborts before OCR-ing it (``backfill_aborted`` set), so a
                huge notebook can't surprise-spend vision budget.
        """
        self._backfill_budget = max_pages
        self.backfill_aborted = False
        self.backfill_estimate = {}
        await self.events.start()
        try:
            uploader = self._uploader or SupernoteUploader()
            watched = (
                [str(name) for name in notebooks]
                if notebooks
                else list(self._cloud_watched_notebooks)
            )
            cloud_poller = CloudPoller(
                uploader=uploader,
                on_note_changed=self._on_cloud_note_changed,
                poll_interval=self.config["poll_interval"],
                watched_notebooks=watched,
                process_existing_on_start=process_existing_on_start,
                on_poll_health=self._on_poll_health,
            )
            self._uploader = uploader
            self._cloud_poller = cloud_poller
            await uploader.start()
            try:
                await cloud_poller.poll_once()
            except BackfillPageCapExceeded as exc:
                self.backfill_aborted = True
                log.warning(
                    "backfill_aborted_max_pages",
                    notebook=exc.notebook,
                    requested=exc.requested,
                    remaining=exc.remaining,
                    estimate=dict(self.backfill_estimate),
                )
            finally:
                with suppress(Exception):
                    await uploader.stop()
        finally:
            self._backfill_budget = None
            await self.events.stop()

    async def _run_with_local_watcher(self) -> None:
        loop = asyncio.get_running_loop()

        def on_file_changed(path: Path, notebook_name: str) -> None:
            if notebook_name.casefold() not in self._watched_notebook_keys:
                return
            try:
                note_bytes = path.read_bytes()
            except OSError as exc:
                log.warning("local_read_failed", path=str(path), error=str(exc))
                return
            asyncio.run_coroutine_threadsafe(
                self._on_note_changed(notebook_name, note_bytes),
                loop,
            )

        self._local_watcher = SupernoteWatcher(on_note_changed=on_file_changed)
        self._local_watcher.start()
        log.info("ingest_local_watcher_started")
        try:
            await self._shutdown_event.wait()
        finally:
            self._local_watcher.stop()
            self._local_watcher = None
            log.info("ingest_local_watcher_stopped")

    async def _run_with_cloud_poller(self) -> None:
        uploader = self._uploader or SupernoteUploader()
        cloud_poller = self._cloud_poller or CloudPoller(
            uploader=uploader,
            on_note_changed=self._on_cloud_note_changed,
            poll_interval=self.config["poll_interval"],
            watched_notebooks=self._cloud_watched_notebooks,
            process_existing_on_start=False,
            on_poll_health=self._on_poll_health,
        )
        self._uploader = uploader
        self._cloud_poller = cloud_poller
        await uploader.start()
        cloud_poller.start()
        try:
            await self._wait_until_shutdown_or_poller_exit()
        except Exception as exc:
            log.error("ingest_service_exiting_after_poller_failure", error=str(exc))
            raise
        finally:
            with suppress(Exception):
                await cloud_poller.stop()
            with suppress(Exception):
                await uploader.stop()

    async def stop(self) -> None:
        self._shutdown_event.set()

    async def _wait_until_shutdown_or_poller_exit(self) -> None:
        if self._cloud_poller is None:
            await self._shutdown_event.wait()
            return

        poller_wait = getattr(self._cloud_poller, "wait", None)
        if poller_wait is None or not inspect.iscoroutinefunction(poller_wait):
            await self._shutdown_event.wait()
            return

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        poller_task = asyncio.create_task(poller_wait())

        done, pending = await asyncio.wait(
            {shutdown_task, poller_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task

        if poller_task in done:
            poller_task.result()


def _ocr_page_indexes(changes: list[PageChangeRecord]) -> list[int]:
    """Return page indexes that need OCR for new or content-updated pages."""
    indexes = {
        change.new_index
        for change in changes
        if change.change_type in {CHANGE_ADDED, CHANGE_UPDATED}
        and change.new_index is not None
    }
    return sorted(indexes)
