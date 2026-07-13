# Supernote CLI — Post-Implementation Audit

**Status:** PASS (one low-severity cleanup deferred)
**Date:** 2026-07-12
**Author:** Avery (for Braydon)
**Scope:** Verify the built `supernote` CLI against
`2026-07-10-supernote-agent-cli-design.md` and the pre-implementation
`reuse-audit/` findings. READ-ONLY — no source modified.
**Repo:** `/Users/braydon/projects/experiments/paia-supernote`

## Context

The pre-implementation reuse audit (`reuse-audit/5-cli-service-surface.md`)
recommended a clean seam — a new `cli.py` + one `[project.scripts]` line over a
shared move pipeline — and flagged specific gaps to watch. The CLI was then
built across four commits (`0d270b1` → `32cc14f`, 2026-07-10). This audit
confirms the build matches the design and closes the flagged gaps.

## Method

- Ran the CLI test surface: `tests/test_cli.py`, `test_move_pipeline.py`,
  `test_pipeline_services.py`.
- Confirmed the self-describing surface: `supernote --help` epilog and the
  per-verb flags.
- Grepped the flagged gaps (backup, SHA-256 re-verify, reader-construction
  duplication, private-method coupling) against the implementation.
- Read `move_pipeline.execute_move_plan` end-to-end for the safe-pipeline
  ceremony, and the installed skill.

## Result — design coverage

| Design verb | Implemented | Notes |
|---|---|---|
| `ls` | ✅ | `_list_note_files` → name/id list |
| `show [--pages]` | ✅ | snapshot + page-state; per-page index/★/heading/preview/hash |
| `read [--pages] [--render]` | ✅ | OCR text; `--render` writes PNGs to `/tmp` |
| `append (--text/--file/--stdin)` | ✅ | render → backup → append → verify → upload → re-verify |
| `move --by-stars` | ✅ | detection → idempotency → backup → mutate → upload → re-verify → ledger |
| `move --pages N --to` | ✅ | explicit, idempotent |
| `plan [--by-stars]` | ✅ | read-only move map; **zero writes** |
| `remove --pages` | ✅ | safe pipeline (backup → verify → upload → re-verify) |
| `auth status` / `auth login` | ✅ | headless when `SN_PHONE/SN_PASSWORD` set; browser fallback otherwise |

Cross-cutting requirements, all present: `--dry-run` on every write;
`--json` / `-q` on every command; verbose "next command" guidance on prose
output; zero-based pages documented in the epilog; `.note` suffix normalization;
`UploadAuthError` → actionable recovery message (exit 2), no notes changed.

## Result — pre-impl audit gaps

| Gap (from reuse-audit/5) | Severity | Status |
|---|---|---|
| Backup step had zero implementation (`grep backup` = 0) | medium | ✅ **Resolved** — `_backup()` (`cli.py:228`) + full per-notebook backup pass in `execute_move_plan` (`move_pipeline.py:249-266`) |
| Re-download + SHA-256 verify was new code | medium | ✅ **Resolved** — `_reverify_sha256()` (`cli.py:222`) on append/remove; move path re-downloads and SHA-compares staged bytes (`move_pipeline.py:299-312`) |
| Extract a shared pipeline rather than wrap `QuickFilingService` | — | ✅ **Done** — `move_pipeline.py` owns the ceremony; daemon's `_run_note_filing_if_configured` untouched |
| Skill shipped in-repo + linked into agent skills path | — | ✅ **Done** — `skill/supernote/SKILL.md` symlinked into `~/.agents/skills/supernote` (discoverable by Avery/Caroline/Ingrid/Derek) |
| Extract `build_reader(config)` to stop 3×→4× duplication | low | ⚠️ **Open** — `SupernoteReader(...)` still constructed inline in 4 places: `cli.py:73`, `main.py:257`, `ingest_service.py:39`, `quick_filing_service.py:41`. Divergence risk remains. |
| Promote `_list_note_files` / `_ensure_authenticated` to public API | low | ⚠️ **Open** — CLI still calls the underscored methods directly (`cli.py:139`, `:338`). Functional, but couples the agent surface to private API. |

## Result — quality gate

`uv run pytest tests/test_cli.py tests/test_move_pipeline.py tests/test_pipeline_services.py -q`
→ **50 passed**. Pipeline order, idempotent skip, partial-failure resume, no-write
on auth failure, and `plan`-touches-nothing are all covered by tests.

## Verdict

The CLI faithfully implements the design and closes both medium-severity gaps the
pre-implementation audit raised (backup + re-verify). The two open items are the
low-severity cleanups the audit itself rated "low": the reader-construction
factory and the private-API promotion. Neither blocks use.

## Recommended follow-up (optional)

1. Extract `build_reader(config)` in `model_config.py` (or a small factory) and
   collapse the four `SupernoteReader(...)` call sites onto it. Small, isolated,
   test-covered.
2. Promote `SupernoteUploader._list_note_files` → `list_note_files` and
   `_ensure_authenticated` → `ensure_authenticated` (or add public aliases), then
   update the two CLI call sites. Removes agent-surface reliance on underscored
   API.
