from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


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
    detected_header: str
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


def _bundle_key(header: FilingHeader) -> str | None:
    if header.note_date is None or header.bundle_total is None:
        return None
    tag_key = "-".join(header.tags)
    title_key = re.sub(r"[^a-z0-9]+", "-", (header.title or "untitled").lower()).strip("-")
    return f"{header.note_date}:{tag_key}:{title_key}:{header.bundle_total}"


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
    bundle_key = _bundle_key(header)
    if not starred:
        return FilingCandidate(
            status="detected",
            source_notebook=notebook,
            source_pages=[page],
            source_revision=source_revision,
            detected_header=header.raw_header,
            detected_tags=header.tags,
            target_notebook=None,
            bundle_key=bundle_key,
            title=header.title,
            reason="page is not starred",
            confidence=0.0,
        )

    for tag in header.tags:
        target = destination_map.get(tag)
        if target:
            return FilingCandidate(
                status="ready",
                source_notebook=notebook,
                source_pages=[page],
                source_revision=source_revision,
                detected_header=header.raw_header,
                detected_tags=header.tags,
                target_notebook=target,
                bundle_key=bundle_key,
                title=header.title,
                reason=f"matched #{tag}",
                confidence=1.0,
            )

    return FilingCandidate(
        status="needs_review",
        source_notebook=notebook,
        source_pages=[page],
        source_revision=source_revision,
        detected_header=header.raw_header,
        detected_tags=header.tags,
        target_notebook=None,
        bundle_key=bundle_key,
        title=header.title,
        reason="no known destination tag",
        confidence=0.0,
    )


class StarDetector:
    """Conservative native-star detector for downloaded .note metadata."""

    def starred_pages_from_metadata(self, metadata: dict[str, Any]) -> set[int]:
        pages = metadata.get("page_metadata")
        if not isinstance(pages, list):
            return set()
        starred: set[int] = set()
        for index, page_metadata in enumerate(pages):
            if not isinstance(page_metadata, dict):
                continue
            value = page_metadata.get("FIVESTAR")
            if value and str(value).strip() not in {"0", "[]", "None", "none"}:
                starred.add(index)
        return starred
