"""LIVE Folio contract test — Supernote owns the upsert contract round trip.

Skipped by default (marker ``live``); run deliberately with::

    env -u ZAI_API_KEY pytest -m live tests/test_folio_live_contract.py

Why this test exists: Folio's agent-tenant covenant (commit d5963e8,
2026-09-27) made POST /api/folio/objects a gated write, and Supernote's
tenant-less upserts 400'd silently 19,531 times over 2026-10-15 → 2026-10-29
because nothing on Supernote's side ever exercised the real payload against
the real server — every other test mocks httpx. This round trip is the
structural fix: a synthetic probe object (no real note content, ever) is
upserted under a reserved contract-probe path, verified accepted, then
deleted. Any future Folio validation change fails HERE, loudly, within a
day — not as an anonymous retry loop in production.
"""

from __future__ import annotations

import os

import httpx
import pytest

from paia_supernote.folio import upsert_supernote_page

PROBE_NOTEBOOK = "__contract_probe__"
PROBE_PAGE = 0


def _folio_url() -> str:
    return os.environ.get("SUPERNOTE_FOLIO_URL") or os.environ.get(
        "PAIA_FOLIO_URL", "http://localhost:3520"
    )


def _tenant_id() -> str:
    return os.environ.get("SUPERNOTE_FOLIO_TENANT_ID", "default")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_folio_accepts_supernote_upsert_round_trip() -> None:
    """The real upsert payload is accepted by the live Folio, then cleaned up."""
    url = _folio_url()
    tenant = _tenant_id()

    # Precondition: the live server must be reachable at all.
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{url}/api/folio/capabilities", timeout=10.0)
        assert resp.status_code == 200, f"Folio not healthy at {url}"

    # The production call path, verbatim: real upsert_supernote_page with a
    # synthetic probe (never real note content).
    result = await upsert_supernote_page(
        notebook=PROBE_NOTEBOOK,
        page=PROBE_PAGE,
        source_revision="contract-probe",
        raw_text="synthetic contract probe payload",
        markdown="# synthetic contract probe",
        diagram={
            "kind": "scene",
            "scene": {"nodes": [], "edges": []},
            "render_version": "1",
        },
        folio_url=url,
        tenant_id=tenant,
    )

    object_id = result["id"]
    assert object_id, "upsert must return the Folio object id"
    assert result.get("tenant_id") == tenant, "object must land in the tenant"

    try:
        # Contract read-back: the object is visible under its path and tenant.
        async with httpx.AsyncClient() as client:
            probe_path = f"supernote/{PROBE_NOTEBOOK}/page-{PROBE_PAGE}"
            listed = await client.get(
                f"{url}/api/folio/objects",
                params={"path_prefix": probe_path, "tenant_id": tenant},
                timeout=10.0,
            )
            assert listed.status_code == 200
            items = listed.json()
            if isinstance(items, dict):  # pagination envelope
                items = items.get("items", [])
            assert any(o.get("id") == object_id for o in items), (
                "probe object must be readable back by path + tenant"
            )
    finally:
        # Cleanup: remove the probe object so the contract test leaves no trace.
        async with httpx.AsyncClient() as client:
            deleted = await client.delete(
                f"{url}/api/folio/objects/{object_id}",
                params={"tenant_id": tenant},
                timeout=10.0,
            )
            assert deleted.status_code == 204, (
                f"probe cleanup failed: DELETE returned {deleted.status_code}"
            )
        async with httpx.AsyncClient() as client:
            gone = await client.get(
                f"{url}/api/folio/objects/{object_id}",
                params={"tenant_id": tenant},
                timeout=10.0,
            )
            assert gone.status_code == 404, "probe object must be gone after cleanup"
