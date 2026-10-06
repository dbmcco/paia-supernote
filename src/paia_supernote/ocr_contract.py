"""Exact-transcription OCR contract for Supernote vision calls.

Version-controlled prompt + escalation policy shared by the organizer's OCR
path and the offline eval harness (`scripts/ocr_eval.py`). Spec:
docs/superpowers/specs/2026-10-06-model-routing-split-design.md §4.4.
"""

from __future__ import annotations

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
