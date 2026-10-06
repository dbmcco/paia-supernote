"""Exact-transcription OCR contract for Supernote vision calls.

Version-controlled prompt + escalation policy shared by the organizer's OCR
path and the offline eval harness (`scripts/ocr_eval.py`). Spec:
docs/superpowers/specs/2026-10-06-model-routing-split-design.md §4.4.
"""

from __future__ import annotations

from collections.abc import Callable

TRANSCRIPTION_PROMPT = (
    "Transcribe the handwritten text in this image exactly as written.\n"
    "\n"
    "Rules:\n"
    "1. Transcribe every word in order, exactly as it is written.\n"
    "2. Do not rewrite, rephrase, summarize, or correct spelling.\n"
    "3. Preserve the original line breaks: one output line per written line.\n"
    "4. If a word or character is unreadable, write [illegible] in its place.\n"
    "5. Do not infer or guess words you cannot read.\n"
    "6. Copy numbers, symbols, checkboxes, and punctuation literally.\n"
    "7. Return only the transcription - no commentary, no headers, "
    "no markdown fences."
)

# Marker the prompt reserves for unreadable text. Escalation counts these.
ILLEGIBLE_MARKER = "[illegible]"

# A local result with at least this many [illegible] markers (or empty output)
# escalates once to the hosted engine.
ILLEGIBLE_ESCALATION_THRESHOLD = 3


def needs_escalation(text: str) -> bool:
    """True when a local transcription is weak enough to escalate.

    Weak = empty/blank, or at least ILLEGIBLE_ESCALATION_THRESHOLD
    [illegible] markers.
    """
    return (
        not text.strip()
        or text.count(ILLEGIBLE_MARKER) >= ILLEGIBLE_ESCALATION_THRESHOLD
    )


def escalate_if_needed(text: str, route_call: Callable[[str], str]) -> str:
    """Exactly-once hosted escalation for a weak local transcription.

    When ``text`` is weak (see needs_escalation), call ``route_call`` once -
    bound by the caller to the hosted engine - and return whichever result
    has fewer [illegible] markers; a blank/failed hosted result falls back
    to the local text. Strong local results never trigger a hosted call.
    """
    if not needs_escalation(text):
        return text
    local = text.strip()
    hosted = (route_call(text) or "").strip()
    if not hosted:
        return local
    if not local:
        return hosted
    if hosted.count(ILLEGIBLE_MARKER) <= local.count(ILLEGIBLE_MARKER):
        return hosted
    return local
