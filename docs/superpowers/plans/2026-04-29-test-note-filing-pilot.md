# Test Note Filing Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the Quick-note filing workflow using only `Test Note 1.note` as the source and `Test Note 2.note` as the destination before touching real notebooks.

**Architecture:** Add an isolated filing pipeline in `paia-supernote` with four small units: page marker detection, header routing, an operation ledger, and notebook page mutation. The pilot is opt-in, defaults to dry-run, and only operates on configured test notebook names.

**Tech Stack:** Python 3.12, pytest, SQLite, `supernotelib`, existing `SupernoteUploader`, existing `SupernoteReader`, existing launchd/cloud service patterns.

---

## File Map

- Create `src/paia_supernote/quick_filing.py`
  - Parses page headers.
  - Applies tag-to-destination routing.
  - Groups multi-page bundles.
  - Produces deterministic `FilingCandidate` objects.

- Create `src/paia_supernote/filing_ledger.py`
  - Owns the SQLite `filing_operations` table.
  - Provides idempotent create/update/resume behavior.

- Create `src/paia_supernote/note_page_ops.py`
  - Loads `.note` bytes with `supernotelib.parser.load_notebook`.
  - Copies existing page objects from source to destination.
  - Removes pages from source after target write succeeds.
  - Clears stale recognition metadata before reconstruction.

- Create `src/paia_supernote/quick_filing_service.py`
  - Orchestrates the pilot.
  - Downloads `Test Note 1.note` and `Test Note 2.note`.
  - Runs dry-run or live move.
  - Writes ledger transitions.

- Create `scripts/quick_filing_pilot.py`
  - Manual runner for dry-run/live validation.
  - Defaults to `--dry-run`.

- Modify `src/paia_supernote/cloud_poller.py`
  - Add `Test Note 1` and `Test Note 2` to watched notebooks only if needed for live pilot observation.

- Tests:
  - `tests/test_quick_filing.py`
  - `tests/test_filing_ledger.py`
  - `tests/test_note_page_ops.py`
  - `tests/test_quick_filing_service.py`

---

## Task 1: Header Parsing And Routing

**Files:**
- Create: `src/paia_supernote/quick_filing.py`
- Test: `tests/test_quick_filing.py`

- [ ] **Step 1: Write failing parser tests**

Add:

```python
from paia_supernote.quick_filing import parse_filing_header, route_page


def test_parse_filing_header_with_bundle_marker() -> None:
    parsed = parse_filing_header(
        "2026-04-29 #test #meeting 1/2\nGene King check-in\nBody text"
    )

    assert parsed.note_date == "2026-04-29"
    assert parsed.tags == ["test", "meeting"]
    assert parsed.bundle_index == 1
    assert parsed.bundle_total == 2
    assert parsed.title == "Gene King check-in"


def test_route_page_requires_known_destination_tag() -> None:
    parsed = parse_filing_header("2026-04-29 #unknown\nUntitled")

    routed = route_page(
        notebook="Test Note 1",
        page=0,
        source_revision="rev-1",
        text="2026-04-29 #unknown\nUntitled",
        starred=True,
        destination_map={"test": "Test Note 2"},
    )

    assert routed.status == "needs_review"
    assert routed.target_notebook is None
    assert "no known destination tag" in routed.reason


def test_route_page_uses_test_destination_when_starred() -> None:
    routed = route_page(
        notebook="Test Note 1",
        page=3,
        source_revision="rev-1",
        text="2026-04-29 #test #meeting\nPilot page",
        starred=True,
        destination_map={"test": "Test Note 2"},
    )

    assert routed.status == "ready"
    assert routed.target_notebook == "Test Note 2"
    assert routed.source_pages == [3]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_quick_filing.py -q
```

Expected: import failure for `paia_supernote.quick_filing`.

- [ ] **Step 3: Implement parser and route model**

Create `src/paia_supernote/quick_filing.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class FilingHeader:
    note_date: str | None
    tags: list[str]
    bundle_index: int | None
    bundle_total: int | None
    title: str | None
    raw_header: str


@dataclass(slots=True)
class FilingCandidate:
    status: str
    source_notebook: str
    source_pages: list[int]
    source_revision: str
    detected_tags: list[str]
    target_notebook: str | None
    bundle_key: str | None
    title: str | None
    reason: str
    confidence: float


_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_TAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_-]*)")
_BUNDLE_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")


def parse_filing_header(text: str) -> FilingHeader:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    header = lines[0] if lines else ""
    title = lines[1] if len(lines) > 1 else None
    date_match = _DATE_RE.search(header)
    bundle_match = _BUNDLE_RE.search(header)
    tags = [tag.lower() for tag in _TAG_RE.findall(header)]
    return FilingHeader(
        note_date=date_match.group(1) if date_match else None,
        tags=tags,
        bundle_index=int(bundle_match.group(1)) if bundle_match else None,
        bundle_total=int(bundle_match.group(2)) if bundle_match else None,
        title=title,
        raw_header=header,
    )


def route_page(
    *,
    notebook: str,
    page: int,
    source_revision: str,
    text: str,
    starred: bool,
    destination_map: dict[str, str],
) -> FilingCandidate:
    header = parse_filing_header(text)
    bundle_key = None
    if header.note_date and header.bundle_total:
        tag_key = "-".join(header.tags)
        title_key = re.sub(r"[^a-z0-9]+", "-", (header.title or "untitled").lower()).strip("-")
        bundle_key = f"{header.note_date}:{tag_key}:{title_key}:{header.bundle_total}"
    if not starred:
        return FilingCandidate("detected", notebook, [page], source_revision, header.tags, None, bundle_key, header.title, "page is not starred", 0.0)
    for tag in header.tags:
        target = destination_map.get(tag)
        if target:
            return FilingCandidate("ready", notebook, [page], source_revision, header.tags, target, bundle_key, header.title, f"matched #{tag}", 1.0)
    return FilingCandidate("needs_review", notebook, [page], source_revision, header.tags, None, bundle_key, header.title, "no known destination tag", 0.0)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_quick_filing.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/quick_filing.py tests/test_quick_filing.py
git commit -m "feat: parse quick filing headers"
```

---

## Task 2: Operation Ledger

**Files:**
- Create: `src/paia_supernote/filing_ledger.py`
- Test: `tests/test_filing_ledger.py`

- [ ] **Step 1: Write failing ledger tests**

Add:

```python
from pathlib import Path

from paia_supernote.filing_ledger import FilingLedger


def test_ledger_creates_idempotent_operation(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()

    first = ledger.upsert_detected(
        source_notebook="Test Note 1",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29 #test",
        detected_tags=["test"],
        bundle_key=None,
        target_notebook="Test Note 2",
        routing_reason="matched #test",
        confidence=1.0,
    )
    second = ledger.upsert_detected(
        source_notebook="Test Note 1",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29 #test",
        detected_tags=["test"],
        bundle_key=None,
        target_notebook="Test Note 2",
        routing_reason="matched #test",
        confidence=1.0,
    )

    assert second.operation_id == first.operation_id
    assert second.status == "detected"


def test_ledger_records_partial_success(tmp_path: Path) -> None:
    ledger = FilingLedger(tmp_path / "filing.db")
    ledger.init_schema()
    op = ledger.upsert_detected(
        source_notebook="Test Note 1",
        source_pages=[0],
        source_revision="rev-1",
        detected_header="2026-04-29 #test",
        detected_tags=["test"],
        bundle_key=None,
        target_notebook="Test Note 2",
        routing_reason="matched #test",
        confidence=1.0,
    )

    ledger.mark_target_written(op.operation_id, target_revision_after="target-rev-2")
    updated = ledger.get(op.operation_id)

    assert updated.status == "target_written"
    assert updated.target_revision_after == "target-rev-2"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_filing_ledger.py -q
```

Expected: import failure for `paia_supernote.filing_ledger`.

- [ ] **Step 3: Implement ledger**

Create a SQLite-backed ledger with these public methods:

```python
class FilingLedger:
    def __init__(self, db_path: Path) -> None: ...
    def init_schema(self) -> None: ...
    def upsert_detected(...) -> FilingOperation: ...
    def mark_target_written(self, operation_id: str, *, target_revision_after: str) -> None: ...
    def mark_source_removed(self, operation_id: str, *, quick_revision_after: str) -> None: ...
    def mark_completed(self, operation_id: str) -> None: ...
    def mark_failed(self, operation_id: str, *, error: str) -> None: ...
    def get(self, operation_id: str) -> FilingOperation: ...
```

Use a deterministic operation key:

```python
sha256(f"{source_notebook}|{source_pages}|{source_revision}|{target_notebook}")
```

Store list fields as JSON text. Use the statuses from the design spec exactly.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_filing_ledger.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/filing_ledger.py tests/test_filing_ledger.py
git commit -m "feat: add quick filing ledger"
```

---

## Task 3: Star Detection Spike

**Files:**
- Modify: `src/paia_supernote/quick_filing.py`
- Test: `tests/test_quick_filing.py`
- Optional script: `scripts/inspect_note_stars.py`

- [ ] **Step 1: Add a conservative star detector interface test**

Add:

```python
from paia_supernote.quick_filing import StarDetector


def test_star_detector_defaults_to_no_star_when_metadata_unknown() -> None:
    detector = StarDetector()

    assert detector.starred_pages_from_metadata({}) == set()
```

- [ ] **Step 2: Run test**

Run:

```bash
uv run pytest tests/test_quick_filing.py::test_star_detector_defaults_to_no_star_when_metadata_unknown -q
```

Expected: failure because `StarDetector` does not exist.

- [ ] **Step 3: Implement detector scaffold**

Add to `quick_filing.py`:

```python
class StarDetector:
    """Conservative native-star detector for downloaded .note metadata."""

    def starred_pages_from_metadata(self, metadata: dict) -> set[int]:
        return set()
```

This intentionally does not guess. The pilot must prove native star extraction with a real starred `Test Note 1.note` before live moves are enabled.

- [ ] **Step 4: Add inspection script**

Create `scripts/inspect_note_stars.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import supernotelib.parser as sn_parser


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: inspect_note_stars.py /path/to/file.note", file=sys.stderr)
        return 2
    notebook = sn_parser.load_notebook(str(Path(sys.argv[1])))
    payload = {
        "total_pages": notebook.get_total_pages(),
        "footer": notebook.metadata.footer,
        "page_metadata": [notebook.get_page(i).metadata for i in range(notebook.get_total_pages())],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Manual star extraction validation**

Create a one-page `Test Note 1.note`, star it on the Supernote, sync it, download it, and run:

```bash
uv run python scripts/inspect_note_stars.py /tmp/Test-Note-1.note > /tmp/test-note-1-star-metadata.json
```

Expected: the metadata dump shows a stable field or footer entry distinguishing the starred page. If it does not, stop before Task 5 and use the cloud-side manual marker fallback only for test notebooks.

- [ ] **Step 6: Commit**

```bash
git add src/paia_supernote/quick_filing.py tests/test_quick_filing.py scripts/inspect_note_stars.py
git commit -m "chore: add native star detection spike"
```

---

## Task 4: Notebook Page Copy And Removal

**Files:**
- Create: `src/paia_supernote/note_page_ops.py`
- Test: `tests/test_note_page_ops.py`

- [ ] **Step 1: Write failing page operation tests**

Add tests using `tests/fixtures/Quick.note`:

```python
from pathlib import Path

import supernotelib.parser as sn_parser

from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages


FIXTURE = Path("tests/fixtures/Quick.note")


def _page_count(note_bytes: bytes, tmp_path: Path) -> int:
    path = tmp_path / "note.note"
    path.write_bytes(note_bytes)
    return sn_parser.load_notebook(str(path)).get_total_pages()


def test_copy_pages_to_end_increases_destination_count(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    target = FIXTURE.read_bytes()
    original_target_count = _page_count(target, tmp_path)

    updated = copy_pages_to_end(source, target, source_pages=[0])

    assert _page_count(updated, tmp_path) == original_target_count + 1


def test_remove_pages_decreases_source_count(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    original_count = _page_count(source, tmp_path)

    updated = remove_pages(source, pages=[0])

    assert _page_count(updated, tmp_path) == original_count - 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_note_page_ops.py -q
```

Expected: import failure for `paia_supernote.note_page_ops`.

- [ ] **Step 3: Implement page operations**

Create `note_page_ops.py` with:

```python
from __future__ import annotations

import copy
import os
import tempfile

import supernotelib.manipulator as sn_manip
import supernotelib.parser as sn_parser

from paia_supernote.notebook_writer import clear_recognition_metadata


def _load_from_bytes(note_bytes: bytes):
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        os.write(fd, note_bytes)
        os.close(fd)
        return sn_parser.load_notebook(path)
    finally:
        os.unlink(path)


def _sync_metadata_pages(notebook) -> None:
    notebook.metadata.pages = [page.metadata for page in notebook.pages]


def copy_pages_to_end(source_bytes: bytes, target_bytes: bytes, *, source_pages: list[int]) -> bytes:
    source = _load_from_bytes(source_bytes)
    target = _load_from_bytes(target_bytes)
    for i in range(source.get_total_pages()):
        clear_recognition_metadata(source.get_page(i))
    for i in range(target.get_total_pages()):
        clear_recognition_metadata(target.get_page(i))
    for page_index in source_pages:
        page = copy.deepcopy(source.get_page(page_index))
        clear_recognition_metadata(page)
        target.pages.append(page)
    _sync_metadata_pages(target)
    return sn_manip.reconstruct(target)


def remove_pages(source_bytes: bytes, *, pages: list[int]) -> bytes:
    source = _load_from_bytes(source_bytes)
    remove_set = set(pages)
    for i in range(source.get_total_pages()):
        clear_recognition_metadata(source.get_page(i))
    source.pages = [page for index, page in enumerate(source.pages) if index not in remove_set]
    if not source.pages:
        raise ValueError("cannot remove all pages from a notebook")
    _sync_metadata_pages(source)
    return sn_manip.reconstruct(source)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_note_page_ops.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/note_page_ops.py tests/test_note_page_ops.py
git commit -m "feat: add notebook page move primitives"
```

---

## Task 5: Test-Only Filing Service

**Files:**
- Create: `src/paia_supernote/quick_filing_service.py`
- Test: `tests/test_quick_filing_service.py`

- [ ] **Step 1: Write failing orchestration tests**

Add:

```python
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from paia_supernote.quick_filing_service import QuickFilingService


@pytest.mark.asyncio
async def test_service_dry_run_does_not_upload(tmp_path: Path) -> None:
    uploader = AsyncMock()
    uploader.download_notebook.side_effect = [b"source", b"target"]
    service = QuickFilingService(
        uploader=uploader,
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Test Note 1",
        destination_map={"test": "Test Note 2"},
        dry_run=True,
    )
    service._detect_candidates = lambda _bytes: []

    result = await service.run_once()

    assert result["status"] == "ok"
    uploader.upload_notebook.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_refuses_non_test_source(tmp_path: Path) -> None:
    service = QuickFilingService(
        uploader=AsyncMock(),
        ledger_db_path=tmp_path / "filing.db",
        source_notebook="Quick",
        destination_map={"lfw": "LFW"},
        dry_run=True,
    )

    with pytest.raises(ValueError, match="pilot only supports test notebooks"):
        await service.run_once()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_quick_filing_service.py -q
```

Expected: import failure for `paia_supernote.quick_filing_service`.

- [ ] **Step 3: Implement dry-run-safe service**

Create `quick_filing_service.py` with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from paia_supernote.filing_ledger import FilingLedger


class QuickFilingService:
    def __init__(
        self,
        *,
        uploader: Any,
        ledger_db_path: Path,
        source_notebook: str,
        destination_map: dict[str, str],
        dry_run: bool = True,
    ) -> None:
        self.uploader = uploader
        self.ledger = FilingLedger(ledger_db_path)
        self.source_notebook = source_notebook
        self.destination_map = destination_map
        self.dry_run = dry_run

    def _validate_pilot_scope(self) -> None:
        allowed = {"Test Note 1", "Test Note 2", "test", "Test"}
        names = {self.source_notebook, *self.destination_map.values()}
        if any(name not in allowed for name in names):
            raise ValueError("pilot only supports test notebooks")

    def _detect_candidates(self, source_bytes: bytes) -> list:
        return []

    async def run_once(self) -> dict[str, Any]:
        self._validate_pilot_scope()
        self.ledger.init_schema()
        source_bytes = await self.uploader.download_notebook(f"{self.source_notebook}.note")
        candidates = self._detect_candidates(source_bytes)
        if self.dry_run:
            return {"status": "ok", "dry_run": True, "candidate_count": len(candidates)}
        return {"status": "ok", "dry_run": False, "candidate_count": len(candidates)}
```

This is intentionally not live-moving yet. Live move wiring happens only after native star detection is proven in Task 3 and page operations pass in Task 4.

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_quick_filing_service.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/quick_filing_service.py tests/test_quick_filing_service.py
git commit -m "feat: add test-only quick filing service"
```

---

## Task 6: Manual Pilot Runner

**Files:**
- Create: `scripts/quick_filing_pilot.py`
- Test manually; no live writes until dry-run output is inspected.

- [ ] **Step 1: Add runner**

Create:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from paia_supernote.quick_filing_service import QuickFilingService
from paia_supernote.uploader import SupernoteUploader


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="Test Note 1")
    parser.add_argument("--target", default="Test Note 2")
    parser.add_argument("--tag", default="test")
    parser.add_argument("--ledger", default=str(Path.home() / ".paia" / "supernote" / "filing-ledger.db"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    uploader = SupernoteUploader()
    await uploader.start()
    try:
        service = QuickFilingService(
            uploader=uploader,
            ledger_db_path=Path(args.ledger),
            source_notebook=args.source,
            destination_map={args.tag: args.target},
            dry_run=not args.live,
        )
        result = await service.run_once()
        print(result)
    finally:
        await uploader.stop()


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 2: Run dry-run against test notebooks**

Run:

```bash
uv run python scripts/quick_filing_pilot.py
```

Expected: prints `{'status': 'ok', 'dry_run': True, ...}` and does not upload any notebooks.

- [ ] **Step 3: Commit**

```bash
git add scripts/quick_filing_pilot.py
git commit -m "chore: add quick filing pilot runner"
```

---

## Task 7: Live Test-Notebook Move

**Files:**
- Modify: `src/paia_supernote/quick_filing_service.py`
- Test: `tests/test_quick_filing_service.py`

- [ ] **Step 1: Write partial-failure test**

Add a test where target upload succeeds and source upload fails. Expected ledger status is `target_written_source_pending`; destination upload is not repeated on retry.

- [ ] **Step 2: Implement live move**

Use:

- `copy_pages_to_end(...)` for target write.
- `remove_pages(...)` for source cleanup.
- ledger `mark_target_written(...)` before source cleanup.
- ledger idempotency to skip destination append if status is already `target_written`.

- [ ] **Step 3: Run focused tests**

Run:

```bash
uv run pytest tests/test_quick_filing_service.py tests/test_filing_ledger.py tests/test_note_page_ops.py tests/test_quick_filing.py -q
```

Expected: all pass.

- [ ] **Step 4: Manual live test**

Only after dry-run and tests pass:

```bash
uv run python scripts/quick_filing_pilot.py --live
```

Expected:

- `Test Note 2.note` receives the filed page.
- `Test Note 1.note` no longer has that page.
- Ledger row is `completed`.
- No `Quick.note`, `LFW.note`, `Synth.note`, `Navicyte.note`, `Ideas.note`, `Walk.note`, or `Meetings.note` writes occur.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/quick_filing_service.py tests/test_quick_filing_service.py
git commit -m "feat: enable live test-note filing"
```

---

## Self-Review

Spec coverage:

- Star-gated moves: Task 3 proves detection before Task 7 live move.
- Whole-page routing: Task 1 and Task 4.
- Bundles: Task 1 starts model; bundle completeness should be added before moving any multi-page pilot.
- Ledger: Task 2 and Task 7.
- Cloud-side emulated move: Task 4 and Task 7.
- Real notebook safety: Task 5 refuses non-test notebook names.

Known implementation constraint:

- Native star detection from downloaded `.note` metadata is not proven yet. Task 3 must be completed before live moving. If native stars are not available cloud-side, do not substitute real-note movement; either use a test-only OCR marker for pilot or pause for the device-plugin research lane.
