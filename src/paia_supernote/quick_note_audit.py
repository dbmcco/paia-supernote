from __future__ import annotations

import json
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
    if score == 0 or word_count < 3:
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
    return f"{normalized[: limit - 1].rstrip()}..."


def _links_for_tags(tags: set[str]) -> list[str]:
    links = []
    if "thought/decomp" in tags:
        links.append("(de)comp")
    if "system/workgraph" in tags:
        links.append("Workgraph")
    if "system/agents" in tags:
        links.append("Agents")
    return links


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
