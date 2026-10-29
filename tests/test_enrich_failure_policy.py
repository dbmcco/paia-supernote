"""Enrich failure policy — categories, never-empty errors, bounded retry backoff.

Regression coverage for the 2026-08-10 → 2026-10-28 incident: pages failed
every minute with an empty ``error`` because ``httpx.ReadTimeout`` carries an
empty message and ``str(exc)`` was stored verbatim; retry delay was a flat
60s forever.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from paia_supernote.enrich_service import (
    categorize_enrich_exception,
    enrich_retry_delay_seconds,
    format_enrich_error,
    EnrichService,
)
from paia_supernote.page_state import PageStateStore

from tests.test_pipeline_services import DEFAULT_CONFIG


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://gateway.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status_code}'", request=request, response=response
    )


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (httpx.ReadTimeout(""), "timeout"),
        (httpx.ConnectTimeout(""), "timeout"),
        (_http_status_error(429), "rate_limited"),
        (_http_status_error(503), "http_error"),
        (httpx.ConnectError("All connection attempts failed"), "network"),
        (httpx.RemoteProtocolError("peer closed"), "network"),
        (ValueError("could not parse"), "response_shape"),
        (KeyError("choices"), "response_shape"),
        (IndexError(), "response_shape"),
        (RuntimeError("boom"), "unknown"),
    ],
)
def test_categorize_enrich_exception(exc: BaseException, category: str) -> None:
    assert categorize_enrich_exception(exc) == category


def test_format_enrich_error_is_never_empty() -> None:
    # httpx.ReadTimeout() carries an empty message — the exact incident shape.
    assert str(httpx.ReadTimeout("")) == ""
    assert format_enrich_error(httpx.ReadTimeout("")) == "ReadTimeout:timeout"
    assert format_enrich_error(RuntimeError("boom")) == "RuntimeError:unknown"


@pytest.mark.parametrize(
    ("retry_count", "expected_delay"),
    [
        (0, 60),
        (1, 120),
        (2, 240),
        (5, 1920),
        (10, 61440),
        (11, 86400),
        (20, 86400),
        (100, 86400),
    ],
)
def test_enrich_retry_backoff_is_exponential_then_daily(
    retry_count: int, expected_delay: int
) -> None:
    assert enrich_retry_delay_seconds(retry_count) == expected_delay


def _make_service(tmp_path: Path, *, enricher, store=None) -> tuple[EnrichService, PageStateStore]:
    config = dict(DEFAULT_CONFIG)
    config["state_db_path"] = str(tmp_path / "state.db")
    state = store or PageStateStore(Path(config["state_db_path"]))
    state.init_schema()
    return (
        EnrichService(config=config, page_state=state, enricher=enricher),
        state,
    )


@pytest.mark.asyncio
async def test_run_once_records_typed_category_for_empty_message_timeout(
    tmp_path: Path,
) -> None:
    """The incident: ReadTimeout with empty str must still record a real error."""
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 12, "rev-1", "raw", "qwen3-vl:8b")

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = httpx.ReadTimeout("")

    service = EnrichService(
        config={**DEFAULT_CONFIG, "state_db_path": str(tmp_path / "state.db")},
        page_state=store,
        enricher=mock_enricher,
    )
    wrote = await service.run_once()

    row = store.get_page("Quick", 12)
    assert wrote is False
    assert row.retry_count == 1
    assert row.last_error == "ReadTimeout:timeout"  # non-empty, typed, categorized
    assert row.last_error_stage == "enrich"


@pytest.mark.asyncio
async def test_run_once_backs_off_exponentially_with_retry_count(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 12, "rev-1", "raw", "qwen3-vl:8b")
    for _ in range(4):  # retry_count == 4 before this failure
        # delay=0 keeps the page *due* — next_dirty_page's next_retry_at gate
        # is the backoff mechanism under test indirectly; a future-dated setup
        # row would (correctly) never be selected by run_once.
        store.mark_failed("Quick", 12, "enrich", "x", retry_delay_seconds=0)
    before = store.get_page("Quick", 12)
    assert before.retry_count == 4

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = httpx.ReadTimeout("")
    service = EnrichService(
        config={**DEFAULT_CONFIG, "state_db_path": str(tmp_path / "state.db")},
        page_state=store,
        enricher=mock_enricher,
    )
    await service.run_once()

    row = store.get_page("Quick", 12)
    assert row.retry_count == 5
    assert row.next_retry_at is not None
    # next_retry_at ≈ now + 60*2^5 = 960s — not the flat 60s anymore.
    from datetime import datetime, timezone

    retry_at = datetime.fromisoformat(row.next_retry_at)
    delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
    assert 900 < delta <= 960


@pytest.mark.asyncio
async def test_run_once_caps_backoff_at_once_per_day(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 12, "rev-1", "raw", "qwen3-vl:8b")
    for _ in range(30):
        # delay=0: prior failures are aged/due (see exponential-backoff test).
        store.mark_failed("Quick", 12, "enrich", "x", retry_delay_seconds=0)

    mock_enricher = AsyncMock()
    mock_enricher.enrich_page.side_effect = httpx.ReadTimeout("")
    service = EnrichService(
        config={**DEFAULT_CONFIG, "state_db_path": str(tmp_path / "state.db")},
        page_state=store,
        enricher=mock_enricher,
    )
    await service.run_once()

    from datetime import datetime, timezone

    row = store.get_page("Quick", 12)
    assert row.retry_count == 31
    retry_at = datetime.fromisoformat(row.next_retry_at)
    delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
    assert 86000 < delta <= 86400  # once a day, never more often


def test_status_surfaces_backoff_capped_enrich_pages(tmp_path: Path) -> None:
    from paia_supernote.main import render_status

    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 12, "rev-1", "raw", "qwen3-vl:8b")
    for _ in range(12):
        store.mark_failed("Quick", 12, "enrich", "x", retry_delay_seconds=60)

    rendered = render_status(tmp_path / "state.db")
    assert "enrich_backoff_capped=1" in rendered
