# Supernote OCR eval — 2026-10 (gate WAIVED by owner; cutover complete)

## Decision

The §4.4.2 WER gate was **waived by owner ruling on 2026-10-06**:

> "it's fine going forward — I can always clarify with an agent."

The owner reviewed the 24 local-model drafts in `eval-corpus/truth-drafts/`
("they look great — just a couple little glitches") and accepted residual OCR
error operationally: going forward, ambiguous transcriptions get clarified
conversationally with an agent rather than pre-verified by a scored eval.

No WER numbers were computed. The hosted comparator leg (AtlasCloud
`openai/gpt-5.6-luna`, ≈$0.10 for 24 pages) was never billed.

## Evidence in lieu of the gate

- **Live smoke through the organizer reader path** (Quick-p06, 2026-10-06):
  44s, 190 chars, 0 `[illegible]` markers → strong local, no hosted spend,
  no spurious escalation.
- **Live comparator smoke** (same page): AtlasCloud gpt-5.6-luna read
  "PERSONAL INTENTS JUL12" correctly where local misread "JU112" —
  escalation quality confirmed on real handwriting.
- **Owner read of all 24 drafts**: near-verbatim, "a couple little glitches."
- **Escalation contract tested**: 12 tests in `tests/test_ocr_escalation.py`
  (weak local → exactly-one hosted call; hosted failure → keep local text;
  no key → local-only; strong local → zero hosted calls).

## Corpus retained

`eval-corpus/` (24 PNGs + cached local transcriptions + draft truths) stays on
disk. If a scored eval is ever wanted, the remaining work is owner truth
correction (~15–30 min; workflow in `eval-corpus/README.md`) and one command:
`uv run scripts/ocr_eval.py --pages eval-corpus --engines local,luna --report <file>`.

## Cutover (Task 8)

2026-10-06: `DEFAULT_CONFIG["vision_backend"]` flipped `zai` → `ollama`
(model `qwen3-vl:8b`, escalation → AtlasCloud `openai/gpt-5.6-luna`).
Applies to the main pipeline service, ingest, and CLI via `build_reader`.
Rollback levers: `SUPERNOTE_VISION_BACKEND=zai` env override, or a pinned
`vision_backend` in `~/.paia/supernote/config.toml`. Rewrite backend stays
`zai` (glm-5.3, owner routing ruling 2026-10-06).

### Cutover evidence (2026-10-06 20:23–20:30)

- Restarted `organizer`, `service`, `ingest`, `enrich` via `launchctl
  kickstart`; all stable PIDs, organizer answers HTTP 200 on :8766, clean
  stderr.
- Golden pages through the live wiring (`build_reader(load_config())` —
  exactly what the services construct), vs cached eval outputs:
  - `Dev-p30`: 13.3s, 76 chars, similarity 0.99
  - `Quick-p06`: 27.2s, 179 chars, similarity 0.81 (same glitch class:
    DISTRO/DISTR, Oliana/Oliara)
  - `Mgmt-p00`: 27.2s, 509 chars vs 521 cached (same structure/length;
    different glitch distribution — run-to-run sampling variance on a dense
    checkbox page, e.g. LFN↔LFW)
  - 0 `[illegible]` markers on all three → no spurious escalation, zero
    hosted spend.
- Full suite: 494 passed / 17 skipped / 16 deselected (baseline parity).

Follow-up option (not blocking): pin ollama sampling (e.g. `temperature: 0`)
in the transcription payload to reduce run-to-run variance.
