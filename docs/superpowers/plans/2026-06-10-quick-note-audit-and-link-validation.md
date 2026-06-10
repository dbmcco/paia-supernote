# Quick Note Audit And Link Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the safe first pass for `Quick.note` reorganization: a read-only audit ledger/report and a fail-closed native Supernote link validation probe.

**Architecture:** Add a pure classification module for audit records, a page-state listing API, a read-only audit service/CLI, and a separate native-link probe that reports whether index-link generation is validated. No real notebook moves and no real `Quick.note` index writes happen in this plan.

**Tech Stack:** Python 3.12, SQLite, dataclasses, JSON, Markdown, pytest, existing `PageStateStore`, existing `supernotelib==0.7.1`.

---

## File Structure

- Create `src/paia_supernote/quick_note_audit.py`: audit dataclasses, destination taxonomy, deterministic baseline classifier, JSON/Markdown report rendering, and read-only audit service.
- Modify `src/paia_supernote/page_state.py`: add a `list_pages()` method so the audit can read OCR state without raw SQL in the CLI.
- Create `src/paia_supernote/native_link_probe.py`: fail-closed native-link capability report for Supernote cross-notebook index links.
- Create `scripts/quick_note_audit.py`: command-line read-only audit runner.
- Create `scripts/supernote_link_probe.py`: command-line native-link validation probe.
- Create `tests/test_quick_note_audit.py`: unit tests for classification, report rendering, and audit service behavior.
- Create `tests/test_native_link_probe.py`: unit tests proving the probe fails closed when link creation is not validated.
- Modify `README.md`: document the read-only audit command and the native-link gate.

## Scope Boundaries

This plan does not move pages, regenerate `Quick.note`, write index pages, create Folio tags, or mutate real notebooks. It produces two deliverables:

1. A reviewable audit ledger for all OCR-known `Quick` pages.
2. A native-link validation report that blocks real index mutation until disposable fixture validation passes.

This plan is OCR-first. It records low-confidence or ambiguous pages as `needs_review`, which is the handoff point for a follow-up vision escalation pass using rendered page images before approved page moves.

### Task 1: Page State Listing API

**Files:**
- Modify: `src/paia_supernote/page_state.py`
- Test: `tests/test_quick_note_audit.py`

- [ ] **Step 1: Write the failing test**

Append this test to a new file `tests/test_quick_note_audit.py`:

```python
from pathlib import Path

from paia_supernote.page_state import PageStateStore


def test_page_state_store_lists_pages_for_notebook_in_page_order(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 2, "rev-2", "second page", "test-model")
    store.upsert_ocr_page("LFW", 0, "rev-lfw", "other notebook", "test-model")
    store.upsert_ocr_page("Quick", 0, "rev-0", "first page", "test-model")

    pages = store.list_pages("Quick")

    assert [page.page for page in pages] == [0, 2]
    assert [page.raw_text for page in pages] == ["first page", "second page"]
    assert [page.source_revision for page in pages] == ["rev-0", "rev-2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py::test_page_state_store_lists_pages_for_notebook_in_page_order -v
```

Expected: FAIL with `AttributeError: 'PageStateStore' object has no attribute 'list_pages'`.

- [ ] **Step 3: Implement `list_pages()`**

In `src/paia_supernote/page_state.py`, add this method inside `PageStateStore` after `get_page()`:

```python
    def list_pages(self, notebook: str) -> list[PageState]:
        with sqlite3.connect(self._db_path) as conn:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py::test_page_state_store_lists_pages_for_notebook_in_page_order -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/page_state.py tests/test_quick_note_audit.py
git commit -m "Add page state listing for audits"
```

### Task 2: Quick Note Audit Classification Records

**Files:**
- Create: `src/paia_supernote/quick_note_audit.py`
- Modify: `tests/test_quick_note_audit.py`

- [ ] **Step 1: Write failing classification tests**

Append these tests to `tests/test_quick_note_audit.py`:

```python
from paia_supernote.quick_note_audit import (
    QuickAuditPage,
    QuickAuditTaxonomy,
    classify_quick_page,
)


def test_classify_quick_page_routes_paia_system_thinking() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=47,
        source_revision="rev",
        raw_text="Speed rush\nHow do I make this more of an entire harness?\nwork graph",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "PAIA"
    assert "system/workgraph" in decision.tags
    assert decision.confidence >= 0.7


def test_classify_quick_page_routes_working_items_to_mgmt() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=42,
        source_revision="rev",
        raw_text="Projects/focus to define\noutreach engine\nMeetup engine\n260430",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "Mgmt"
    assert "work/current" in decision.tags


def test_classify_quick_page_routes_decomp_frameworks_to_decomp_note() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=8,
        source_revision="rev",
        raw_text="Loops of Work\nMAIN/overall\nSub loop 1\nSub loop 2",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "move"
    assert decision.target_notebook == "(de)comp"
    assert "thought/decomp" in decision.tags


def test_classify_quick_page_marks_short_scraps_for_review() -> None:
    page = QuickAuditPage(
        source_notebook="Quick",
        page=56,
        source_revision="rev",
        raw_text="F# B E D G C# B",
        ocr_model="test-model",
    )

    decision = classify_quick_page(page, QuickAuditTaxonomy.default())

    assert decision.action == "needs_review"
    assert decision.target_notebook is None
    assert decision.confidence < 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'paia_supernote.quick_note_audit'`.

- [ ] **Step 3: Add audit dataclasses and classifier**

Create `src/paia_supernote/quick_note_audit.py`:

```python
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .page_state import PageState, PageStateStore


@dataclass(slots=True)
class QuickAuditPage:
    source_notebook: str
    page: int
    source_revision: str
    raw_text: str
    ocr_model: str


@dataclass(slots=True)
class QuickAuditDecision:
    source_notebook: str
    page: int
    source_revision: str
    action: str
    target_notebook: str | None
    tags: list[str]
    links: list[str]
    confidence: float
    reason: str
    excerpt: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class QuickAuditReport:
    source_notebook: str
    generated_at: str
    decisions: list[QuickAuditDecision]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_notebook": self.source_notebook,
            "generated_at": self.generated_at,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(slots=True)
class QuickAuditTaxonomy:
    destinations: list[str]
    aliases: dict[str, str]
    keyword_tags: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "QuickAuditTaxonomy":
        return cls(
            destinations=[
                "Mgmt",
                "PAIA",
                "LFW",
                "Synth",
                "Navicyte",
                "(de)comp",
                "Ideas",
                "Archive",
            ],
            aliases={
                "mgmt": "Mgmt",
                "management": "Mgmt",
                "meeting prep": "Mgmt",
                "projects/focus": "Mgmt",
                "what am i stuck": "Mgmt",
                "paia": "PAIA",
                "agent": "PAIA",
                "agents": "PAIA",
                "work graph": "PAIA",
                "workgraph": "PAIA",
                "speedrift": "PAIA",
                "speed rush": "PAIA",
                "supernote": "PAIA",
                "folio": "PAIA",
                "lfw": "LFW",
                "synth": "Synth",
                "synthera": "Synth",
                "navicyte": "Navicyte",
                "decomp": "(de)comp",
                "decomposition": "(de)comp",
                "composition": "(de)comp",
                "loops of work": "(de)comp",
                "info assessment": "(de)comp",
                "info meaning": "(de)comp",
                "info boundary": "(de)comp",
                "article ideas": "Ideas",
            },
            keyword_tags={
                "work graph": ["system/workgraph"],
                "workgraph": ["system/workgraph"],
                "speedrift": ["system/workgraph"],
                "agent": ["system/agents"],
                "agents": ["system/agents"],
                "meeting": ["work/meetings"],
                "projects/focus": ["work/current"],
                "stuck": ["work/current"],
                "loops of work": ["thought/decomp"],
                "decomp": ["thought/decomp"],
                "decomposition": ["thought/decomp"],
                "info assessment": ["thought/information"],
                "info meaning": ["thought/information"],
                "info boundary": ["thought/information"],
            },
        )


def classify_quick_page(
    page: QuickAuditPage,
    taxonomy: QuickAuditTaxonomy | None = None,
) -> QuickAuditDecision:
    taxonomy = taxonomy or QuickAuditTaxonomy.default()
    text = _normalize(page.raw_text)
    target_scores = {destination: 0 for destination in taxonomy.destinations}
    matched_aliases: list[str] = []
    tags: set[str] = set()

    for alias, destination in taxonomy.aliases.items():
        if alias in text and destination in target_scores:
            target_scores[destination] += 1
            matched_aliases.append(alias)

    for keyword, keyword_tags in taxonomy.keyword_tags.items():
        if keyword in text:
            tags.update(keyword_tags)

    if target_scores["Mgmt"] > 0:
        tags.add("work/current")
    if target_scores["PAIA"] > 0:
        tags.add("domain/paia")
    if target_scores["LFW"] > 0:
        tags.add("domain/lfw")
    if target_scores["Synth"] > 0:
        tags.add("domain/synth")
    if target_scores["Navicyte"] > 0:
        tags.add("domain/navicyte")
    if target_scores["(de)comp"] > 0:
        tags.add("thought/decomp")

    target, score = max(target_scores.items(), key=lambda item: item[1])
    word_count = len(re.findall(r"[a-z0-9]+", text))
    if score == 0 or word_count < 4:
        return _decision(
            page=page,
            action="needs_review",
            target_notebook=None,
            tags=sorted(tags),
            links=[],
            confidence=0.2 if word_count else 0.0,
            reason="No strong domain signal was found in OCR text.",
        )

    confidence = min(0.95, 0.55 + (score * 0.15))
    return _decision(
        page=page,
        action="move",
        target_notebook=target,
        tags=sorted(tags),
        links=_links_for_tags(tags),
        confidence=confidence,
        reason=f"Matched domain signals: {', '.join(sorted(set(matched_aliases)))}.",
    )


def build_audit_report(
    pages: Iterable[QuickAuditPage],
    *,
    source_notebook: str = "Quick",
    taxonomy: QuickAuditTaxonomy | None = None,
) -> QuickAuditReport:
    return QuickAuditReport(
        source_notebook=source_notebook,
        generated_at=datetime.now(timezone.utc).isoformat(),
        decisions=[
            classify_quick_page(page, taxonomy)
            for page in sorted(pages, key=lambda item: item.page)
        ],
    )


def page_from_state(state: PageState) -> QuickAuditPage:
    return QuickAuditPage(
        source_notebook=state.notebook,
        page=state.page,
        source_revision=state.source_revision,
        raw_text=state.raw_text,
        ocr_model=state.ocr_model,
    )


class QuickNoteAuditService:
    def __init__(
        self,
        *,
        page_state_store: PageStateStore,
        source_notebook: str = "Quick",
        taxonomy: QuickAuditTaxonomy | None = None,
    ) -> None:
        self.page_state_store = page_state_store
        self.source_notebook = source_notebook
        self.taxonomy = taxonomy or QuickAuditTaxonomy.default()

    def run(self) -> QuickAuditReport:
        states = self.page_state_store.list_pages(self.source_notebook)
        pages = [page_from_state(state) for state in states]
        return build_audit_report(
            pages,
            source_notebook=self.source_notebook,
            taxonomy=self.taxonomy,
        )


def _decision(
    *,
    page: QuickAuditPage,
    action: str,
    target_notebook: str | None,
    tags: list[str],
    links: list[str],
    confidence: float,
    reason: str,
) -> QuickAuditDecision:
    return QuickAuditDecision(
        source_notebook=page.source_notebook,
        page=page.page,
        source_revision=page.source_revision,
        action=action,
        target_notebook=target_notebook,
        tags=tags,
        links=links,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        reason=reason,
        excerpt=_excerpt(page.raw_text),
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _excerpt(text: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _links_for_tags(tags: set[str]) -> list[str]:
    links = []
    if "thought/decomp" in tags:
        links.append("(de)comp")
    if "system/workgraph" in tags:
        links.append("Workgraph")
    if "system/agents" in tags:
        links.append("Agents")
    return links
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/quick_note_audit.py tests/test_quick_note_audit.py
git commit -m "Add Quick note audit classifier"
```

### Task 3: JSON And Markdown Audit Reports

**Files:**
- Modify: `src/paia_supernote/quick_note_audit.py`
- Modify: `tests/test_quick_note_audit.py`

- [ ] **Step 1: Write failing report rendering tests**

Append these tests to `tests/test_quick_note_audit.py`:

```python
import json

from paia_supernote.quick_note_audit import (
    QuickAuditDecision,
    QuickAuditReport,
    report_to_json,
    report_to_markdown,
)


def test_report_to_json_contains_reviewable_decisions() -> None:
    report = QuickAuditReport(
        source_notebook="Quick",
        generated_at="2026-06-10T12:00:00+00:00",
        decisions=[
            QuickAuditDecision(
                source_notebook="Quick",
                page=47,
                source_revision="rev",
                action="move",
                target_notebook="PAIA",
                tags=["domain/paia", "system/workgraph"],
                links=["Workgraph"],
                confidence=0.85,
                reason="Matched domain signals: workgraph.",
                excerpt="Speedrift harness",
            )
        ],
    )

    payload = json.loads(report_to_json(report))

    assert payload["source_notebook"] == "Quick"
    assert payload["decisions"][0]["target_notebook"] == "PAIA"
    assert payload["decisions"][0]["tags"] == ["domain/paia", "system/workgraph"]


def test_report_to_markdown_groups_move_and_review_items() -> None:
    report = QuickAuditReport(
        source_notebook="Quick",
        generated_at="2026-06-10T12:00:00+00:00",
        decisions=[
            QuickAuditDecision(
                source_notebook="Quick",
                page=47,
                source_revision="rev",
                action="move",
                target_notebook="PAIA",
                tags=["domain/paia"],
                links=["Workgraph"],
                confidence=0.85,
                reason="Matched domain signals: workgraph.",
                excerpt="Speedrift harness",
            ),
            QuickAuditDecision(
                source_notebook="Quick",
                page=56,
                source_revision="rev",
                action="needs_review",
                target_notebook=None,
                tags=[],
                links=[],
                confidence=0.2,
                reason="No strong domain signal was found in OCR text.",
                excerpt="F# B E D",
            ),
        ],
    )

    markdown = report_to_markdown(report)

    assert "# Quick Note Audit" in markdown
    assert "| 47 | move | PAIA | 0.85 |" in markdown
    assert "| 56 | needs_review |  | 0.20 |" in markdown
    assert "Matched domain signals: workgraph." in markdown
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py::test_report_to_json_contains_reviewable_decisions tests/test_quick_note_audit.py::test_report_to_markdown_groups_move_and_review_items -v
```

Expected: FAIL with `ImportError` for `report_to_json` and `report_to_markdown`.

- [ ] **Step 3: Implement report rendering**

Append this code to `src/paia_supernote/quick_note_audit.py`:

```python
import json


def report_to_json(report: QuickAuditReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


def report_to_markdown(report: QuickAuditReport) -> str:
    lines = [
        "# Quick Note Audit",
        "",
        f"- Source notebook: `{report.source_notebook}`",
        f"- Generated at: `{report.generated_at}`",
        f"- Page count: `{len(report.decisions)}`",
        "",
        "## Decisions",
        "",
        "| Page | Action | Target | Confidence | Tags | Links | Excerpt | Reason |",
        "|---:|---|---|---:|---|---|---|---|",
    ]
    for decision in report.decisions:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(decision.page),
                    _md_cell(decision.action),
                    _md_cell(decision.target_notebook or ""),
                    f"{decision.confidence:.2f}",
                    _md_cell(", ".join(decision.tags)),
                    _md_cell(", ".join(decision.links)),
                    _md_cell(decision.excerpt),
                    _md_cell(decision.reason),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
```

Move the existing `import json` to the top of the file if the formatter reports import ordering problems.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paia_supernote/quick_note_audit.py tests/test_quick_note_audit.py
git commit -m "Render Quick note audit reports"
```

### Task 4: Read-Only Audit CLI

**Files:**
- Create: `scripts/quick_note_audit.py`
- Modify: `tests/test_quick_note_audit.py`

- [ ] **Step 1: Write failing audit service test**

Append this test to `tests/test_quick_note_audit.py`:

```python
from paia_supernote.quick_note_audit import QuickNoteAuditService


def test_quick_note_audit_service_reads_page_state(tmp_path: Path) -> None:
    store = PageStateStore(tmp_path / "state.db")
    store.init_schema()
    store.upsert_ocr_page("Quick", 0, "rev-0", "work graph cleanup", "test-model")
    store.upsert_ocr_page("Quick", 1, "rev-1", "Projects/focus to define", "test-model")

    report = QuickNoteAuditService(page_state_store=store).run()

    assert report.source_notebook == "Quick"
    assert [decision.page for decision in report.decisions] == [0, 1]
    assert [decision.action for decision in report.decisions] == ["move", "move"]
```

- [ ] **Step 2: Run service test**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py::test_quick_note_audit_service_reads_page_state -v
```

Expected: PASS if Task 2 already added `QuickNoteAuditService`. If it fails, fix the service before adding the CLI.

- [ ] **Step 3: Create the CLI**

Create `scripts/quick_note_audit.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from paia_supernote.main import DEFAULT_CONFIG_PATH, load_config
from paia_supernote.page_state import PageStateStore
from paia_supernote.quick_note_audit import (
    QuickNoteAuditService,
    report_to_json,
    report_to_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a read-only Quick.note reorganization audit."
    )
    parser.add_argument("--notebook", default="Quick")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("quick-note-audit.md"),
    )
    args = parser.parse_args()

    config = load_config(args.config)
    store = PageStateStore(Path(config["state_db_path"]).expanduser())
    report = QuickNoteAuditService(
        page_state_store=store,
        source_notebook=args.notebook,
    ).run()
    rendered = report_to_json(report) if args.format == "json" else report_to_markdown(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.format} audit for {args.notebook} to {args.output}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the CLI against local state**

Run:

```bash
uv run python scripts/quick_note_audit.py --notebook Quick --format markdown --output /tmp/quick-note-audit.md
```

Expected: exits 0 and prints `Wrote markdown audit for Quick to /tmp/quick-note-audit.md`.

Then inspect the first lines:

```bash
sed -n '1,30p' /tmp/quick-note-audit.md
```

Expected: output starts with `# Quick Note Audit` and includes a decisions table.

- [ ] **Step 5: Commit**

```bash
git add scripts/quick_note_audit.py tests/test_quick_note_audit.py
git commit -m "Add Quick note audit CLI"
```

### Task 5: Native Link Probe

**Files:**
- Create: `src/paia_supernote/native_link_probe.py`
- Create: `tests/test_native_link_probe.py`
- Create: `scripts/supernote_link_probe.py`

- [ ] **Step 1: Write failing probe tests**

Create `tests/test_native_link_probe.py`:

```python
from paia_supernote.native_link_probe import NativeLinkProbeResult, probe_native_links


def test_native_link_probe_fails_closed_without_fixture_paths() -> None:
    result = probe_native_links()

    assert result.status == "blocked"
    assert result.real_note_writes_allowed is False
    assert "fixture notebooks are required" in result.reason


def test_native_link_probe_result_serializes_to_dict() -> None:
    result = NativeLinkProbeResult(
        status="blocked",
        real_note_writes_allowed=False,
        reason="fixture notebooks are required",
        evidence=["supernotelib 0.7.1 has no public link constructor"],
    )

    assert result.to_dict() == {
        "status": "blocked",
        "real_note_writes_allowed": False,
        "reason": "fixture notebooks are required",
        "evidence": ["supernotelib 0.7.1 has no public link constructor"],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_native_link_probe.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'paia_supernote.native_link_probe'`.

- [ ] **Step 3: Implement fail-closed probe**

Create `src/paia_supernote/native_link_probe.py`:

```python
from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import supernotelib


@dataclass(slots=True)
class NativeLinkProbeResult:
    status: str
    real_note_writes_allowed: bool
    reason: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_native_links(
    *,
    quick_fixture: Path | None = None,
    target_fixture: Path | None = None,
) -> NativeLinkProbeResult:
    evidence = [
        f"supernotelib version: {getattr(supernotelib, '__version__', 'unknown')}",
        *_public_link_symbols(),
    ]
    if quick_fixture is None or target_fixture is None:
        return NativeLinkProbeResult(
            status="blocked",
            real_note_writes_allowed=False,
            reason="fixture notebooks are required before native index links can be validated",
            evidence=evidence,
        )
    if not quick_fixture.exists() or not target_fixture.exists():
        return NativeLinkProbeResult(
            status="blocked",
            real_note_writes_allowed=False,
            reason="fixture notebook paths must exist before native index links can be validated",
            evidence=evidence,
        )
    return NativeLinkProbeResult(
        status="blocked",
        real_note_writes_allowed=False,
        reason=(
            "native link creation is blocked because no validated Supernote "
            "cross-notebook link constructor is available in this codebase"
        ),
        evidence=evidence,
    )


def _public_link_symbols() -> list[str]:
    symbols: list[str] = []
    for module_name in ("supernotelib", "supernotelib.parser", "supernotelib.manipulator"):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:
            symbols.append(f"{module_name}: import failed: {exc}")
            continue
        link_names = [
            name
            for name in dir(module)
            if "link" in name.lower() and not name.startswith("_")
        ]
        if not link_names:
            symbols.append(f"{module_name}: no public link symbols")
            continue
        for name in link_names:
            value = getattr(module, name)
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = "no signature"
            symbols.append(f"{module_name}.{name}{signature}")
    return symbols
```

- [ ] **Step 4: Add CLI**

Create `scripts/supernote_link_probe.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from paia_supernote.native_link_probe import probe_native_links


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe native Supernote cross-notebook link validation status."
    )
    parser.add_argument("--quick-fixture", type=Path)
    parser.add_argument("--target-fixture", type=Path)
    args = parser.parse_args()

    result = probe_native_links(
        quick_fixture=args.quick_fixture,
        target_fixture=args.target_fixture,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if result.real_note_writes_allowed is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests and CLI**

Run:

```bash
uv run pytest tests/test_native_link_probe.py -v
```

Expected: PASS.

Run:

```bash
uv run python scripts/supernote_link_probe.py
```

Expected: exits 2 and prints JSON with `"status": "blocked"` and `"real_note_writes_allowed": false`.

- [ ] **Step 6: Commit**

```bash
git add src/paia_supernote/native_link_probe.py tests/test_native_link_probe.py scripts/supernote_link_probe.py
git commit -m "Add native Supernote link validation probe"
```

### Task 6: Documentation And Full Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README documentation**

In `README.md`, after the `### Quick Filing` standalone module section, add:

```markdown
### Quick Note Reorganization Audit

Generate a read-only review ledger for `Quick.note` before moving any pages:

```bash
uv run python scripts/quick_note_audit.py \
  --notebook Quick \
  --format markdown \
  --output /tmp/quick-note-audit.md
```

The audit reads OCR state from `~/.paia/supernote/supernote-state.db`, classifies pages into proposed destination notebooks, and writes a Markdown or JSON report. It does not mutate Supernote notebooks.

Native Supernote links for the generated `Quick Index` page are gated by a separate probe:

```bash
uv run python scripts/supernote_link_probe.py
```

The probe fails closed until disposable fixture notebooks prove that generated cross-notebook links survive upload, device sync, tap behavior, and round-trip download. Real `Quick.note` index writes must not run while the probe reports `"real_note_writes_allowed": false`.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/test_quick_note_audit.py tests/test_native_link_probe.py -v
```

Expected: PASS.

- [ ] **Step 3: Run broader regression tests**

Run:

```bash
uv run pytest tests/test_page_state.py tests/test_quick_filing.py tests/test_organizer_api.py tests/test_note_page_ops.py -v
```

Expected: PASS.

- [ ] **Step 4: Run audit CLI against local state**

Run:

```bash
uv run python scripts/quick_note_audit.py --notebook Quick --format json --output /tmp/quick-note-audit.json
```

Expected: exits 0 and prints `Wrote json audit for Quick to /tmp/quick-note-audit.json`.

Run:

```bash
python3 -m json.tool /tmp/quick-note-audit.json >/tmp/quick-note-audit.pretty.json
```

Expected: exits 0, proving valid JSON.

- [ ] **Step 5: Run link probe**

Run:

```bash
uv run python scripts/supernote_link_probe.py
```

Expected: exits 2 and prints a blocked report. This is expected until native link fixture validation is implemented.

- [ ] **Step 6: Commit docs**

```bash
git add README.md
git commit -m "Document Quick note audit workflow"
```

## Final Self-Review Checklist

- The audit pass is read-only.
- No task writes to real `.note` files.
- The generated audit contains source page, OCR excerpt, suggested action, target notebook, tags, links, confidence, and reason.
- Native Supernote index links fail closed until fixture validation proves them safe.
- `Quick.note` index mutation is outside this plan.
- All new behavior has focused tests.
