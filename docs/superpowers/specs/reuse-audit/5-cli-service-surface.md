# Reuse Audit — CLI / Service / Config / Script Surface

Scope: existing CLI/service/config/script surface for the new `supernote` agent CLI.
Repo: `/Users/braydon/projects/experiments/paia-supernote`
Design spec: `docs/superpowers/specs/2026-07-10-supernote-agent-cli-design.md`
Mode: READ-ONLY audit. No files modified.

---

## 1. Confirmed reusable

### Config loading — `src/paia_supernote/main.py`

- **Config file path:** `main.py:51` — `DEFAULT_CONFIG_PATH = Path("~/.paia/supernote/config.toml").expanduser()`.
- **TOML parse:** `main.py:21` `import tomllib`; `load_config()` at `main.py:100` opens with `open(path, "rb")` + `tomllib.load(f)` (`main.py:108-110`). Confirmed: yes, tomllib.
- **Precedence:** env vars > TOML > defaults, as documented in the docstring (`main.py:104`). Nested TOML sections are flattened manually into a flat config dict: `[supernote]` (`main.py:111`), `[services]` (`main.py:138`), `[linear]` (`main.py:145`), `[filing]` (`main.py:152`), `[agents]` (`main.py:165`). Env overrides follow (`main.py:167`+).
- **Reusability for CLI:** `load_config(config_path)` is a standalone function the new CLI can import directly (`from .main import load_config, DEFAULT_CONFIG_PATH`). It is **not** coupled to the daemon — it takes an optional path and returns a plain dict. The new `supernote` CLI should reuse it verbatim, NOT re-implement. The `--config` flag pattern is already established by `build_parser` (`main.py:928`).
- **CAUTION (note, not blocker):** `load_config` eagerly resolves model defaults via `model_config.default_zai_*()` at `main.py:57-59` inside the `DEFAULT_CONFIG` literal. These call into the `paia_agent_runtime` cognition registry at import/eval time. A lightweight read-only CLI (`supernote ls`, `supernote plan`) that doesn't need OCR does not need model routes resolved, but will pay that cost unless `DEFAULT_CONFIG` is restructured. Minor; acceptable for v1.

### State-db and ledger path resolution — `src/paia_supernote/main.py`

- **state_db_path:** default `~/.paia/supernote/supernote-state.db` at `main.py:69`; overridable via `SUPERNOTE_STATE_DB_PATH` env (`main.py:168` / duplicated at `main.py:183`) or `[supernote] state_db_path` TOML key (`main.py:125`). Consumed by `PageStateStore` (`render_status` at `main.py:1014`, `IngestService`/`EnrichService`).
- **filing_ledger_db_path:** default `~/.paia/supernote/filing-ledger.db` at `main.py:73-75`; exposed as `config["filing_ledger_db_path"]` and consumed by `QuickFilingService` via `FilingLedger(Path(...))` (`main.py:498`). Overridable via `[filing] ledger_db_path` TOML key (`main.py:152-163`). The new CLI's `move`/`plan` verbs must read this same key for ledger consistency.

### Model routes — `src/paia_supernote/model_config.py`

- Centralized via `paia_agent_runtime` cognition registry (`model_config.py:13-30`). Key reusable functions:
  - `resolve_supernote_zai_api_key()` — `model_config.py:63` (registry-assigned env var, with legacy `ZAI_API_KEY` fallback).
  - `default_zai_base_url()` — `model_config.py:75`.
  - `default_zai_vision_model()` / `default_zai_text_model()` — `model_config.py:79` / `model_config.py:83`.
  - `supernote_vision_route()` / `supernote_text_route()` — `model_config.py:32` / `model_config.py:37`.
- **Reusability:** The `SupernoteReader` is constructed from these config keys (see `SupernoteService.__init__` `main.py:313-322` and `quick_filing_pilot.py:27-34`). The `read`/`show --render`/`move --by-stars` verbs will construct a `SupernoteReader` the same way — this is a confirmed reusable construction pattern (already replicated identically in 3 places: `main.py`, `ingest_service.py`, `quick_filing_pilot.py`). **Recommendation:** extract a `build_reader(config)` factory to stop the 4th copy; the CLI is the natural home or a shared helper.

### Existing entry points — `pyproject.toml` `[project.scripts]`

```
paia-supernote      = "paia_supernote.main:main"        # daemon + operator subcommands
paia-supernote-board = "paia_supernote.user_board:cli"  # interactive TUI board
```

- **`paia-supernote` (`main:main`, `main.py:1003`):** an argparse daemon with subcommands `service | ingest | enrich | status | organizer | login` (`build_parser` `main.py:926`). Default mode `service`. It runs long-lived asyncio loops (CloudPoller, EventsClient, TasksSync) with SIGINT/SIGTERM handlers (`main.py:1043-1054`). It is **operator/infrastructure-oriented**, not agent-oriented: no `ls`/`show`/`read`/`move`/`plan`/`append` verbs, no `--dry-run`, no `--json`. `status` only prints dirty-page/error counts (`render_status` `main.py:906`).
- **`paia-supernote-board` (`user_board:cli`, `user_board.py:631`):** a synchronous entry that calls `asyncio.run(main())` for a full-screen interactive TUI (ANSI-styled, `input()`-loop, auto-refresh). It is a human interactive board, not a scriptable agent surface. Its `write` path publishes `supernote.write.requested` events over HTTP to paia-events rather than doing direct cloud writes. **Not reusable** as a basis for the agent CLI.

---

## 2. Gaps needing NEW code

### No existing agent-facing command structure to extend

- The spec is correct: the new `supernote` binary must be **built fresh**. The existing `main.build_parser()` (`main.py:926`) is daemon-shaped (long-running service modes), and the design explicitly requires the agent CLI to be "kept **separate from the existing `paia-supernote` daemon entry**." Extending `main.py`'s subparser tree would entangle agent verbs with daemon lifecycle.
- **Minimal seam (recommended):**
  1. New module `src/paia_supernote/cli.py` with a synchronous `main(argv=None)` entry + an argparse (or click) subcommand tree for `ls | show | read | append | move | remove | plan | auth`.
  2. One new line under `[project.scripts]` in `pyproject.toml`:
     `supernote = "paia_supernote.cli:main"`
  3. Optionally `src/paia_supernote/__main__.py` already routes to the daemon `main` (`__main__.py:1-3`); leave it — `python -m paia_supernote` stays the daemon. The agent binary is the `supernote` script target, not `-m`.
- **What the new `cli.py` reuses (no new logic needed):** `load_config` (`main.py:100`), `DEFAULT_CONFIG_PATH` (`main.py:51`), `SupernoteUploader` (`uploader.py:50`), `SupernoteReader` (`reader.py`), `build_snapshot_from_notebook` (`note_snapshot.py:61`), `StarDetector` (`quick_filing.py:142`), `note_page_ops.copy_pages_to_end`/`remove_pages` (`note_page_ops.py:25`/`:41`), `append_page_to_notebook` (`notebook_writer.py:36`), `SupernoteWriter.render_page` (`writer.py:101`), `FilingLedger` (`filing_ledger.py`), and `QuickFilingService` (partial — see below).

### Backup step is NEW code (no existing helper)

- **Finding:** the spec's safe-pipeline step 3 (timestamped backup of every affected notebook to `~/.paia/supernote/backups/<ts>/`) has **no existing implementation**. `grep -i backup` across `src/paia_supernote/` returns **zero matches**. The existing `QuickFilingService.run_once()` does NOT back up before mutating. This backup+hash-verify ceremony must be written fresh in the new CLI command layer. Treat it as genuine new code, not "wiring."

### Re-download/SHA-256 verify step is also NEW

- Same audit: `QuickFilingService` uploads then marks the ledger; it does **not** re-download to verify page counts + SHA-256 against staged bytes. The spec's step 8 verify loop is new orchestration.

### Partial reuse: `QuickFilingService` already encodes most of the move pipeline

- `quick_filing_service.py:108` `run_once()` already implements: download source → detect starred candidates (`_detect_candidates` `:58`) → `FilingLedger.operation_id_for` idempotency check → `_write_target_if_needed` → `remove_pages` + upload source → ledger state machine (`mark_target_written_source_pending` `filing_ledger.py:147`, `mark_completed` `:167`).
- **Gap vs spec:** it lacks the backup, snapshot capture, local page-id/content-hash verification, post-upload re-download verification, and the verbose agent-facing stdout. The CLI's `move --by-stars` can delegate the core mutate+ledger flow to `QuickFilingService` (or its extracted core) but must wrap it with backup+verify+verbose-reporting. **Decision point for the planner:** wrap `QuickFilingService` vs. extract its internals into a shared pipeline the CLI owns. Recommend extracting, so the CLI controls the full ceremony and the daemon's `_run_note_filing_if_configured` (`main.py:489`) stays as-is.

---

## 3. Duplication risk — scripts as proto-commands

Each script in `scripts/`. One line + verdict.

| Script | What it does (one line) | Verdict |
|---|---|---|
| **quick_note_audit.py** | `argparse` front-end over `QuickNoteAuditService` (`src/.../quick_note_audit.py:201`); loads config, writes a read-only markdown/json reorg audit to a file | **REUSE → informs `plan` verb.** Already the right shape (argparse + `load_config`). The audit report is close to `supernote plan`'s "move map: page → target, confidence, already-moved vs would-move." Extract its service; do not re-derive. |
| **append_mgmt_list.py** | Renders hardcoded text via `SupernoteWriter.render_page`, downloads `Mgmt.note`, `append_page_to_notebook`, uploads via `SupernoteUploader` | **REUSE pattern, THROWAWAY content.** This is a working prototype of exactly `supernote append` (download→render→append→upload). The hardcoded `CONTENT` is throwaway, but the 4-step write flow is the `append` verb verbatim — port it into the CLI with `--text/--file/--stdin`. |
| **inspect_note_stars.py** | Loads a local `.note` via `supernotelib.parser`, dumps total pages + per-page metadata (footer, FIVESTAR) as JSON to stdout | **THROWAWAY (debug), informs `show`.** Logic is subsumed by `StarDetector.starred_pages_from_metadata` (`quick_filing.py:145`) and `build_snapshot_from_notebook`. Do NOT re-implement; use the classes. Keep only as a debug aid. |
| **quick_filing_pilot.py** | `argparse` driver for `QuickFilingService.run_once()` with `--source/--target/--tag/--ledger/--live` (dry-run by default) | **REUSE → informs `move` + `plan`.** This is the live prototype of `supernote move --by-stars` / `plan --by-stars`. It already wires `load_config` + `SupernoteReader` + `QuickFilingService`. Port the flags; fold into the CLI. |
| **probe_cloud_api.py** | Raw Playwright probe: list Note-folder files via `/api/file/list/query`, find/inspect `Personal.note`, with a `delete_file` helper | **THROWAWAY (exploration), informs `ls`/`auth` indirectly.** All this API surface is already encapsulated in `SupernoteUploader._list_note_files` (`uploader.py:210`), `_api_call`, `_delete_by_ids`. The new `ls`/`auth` verbs call those methods, not this script. |
| **create_test_note.py** | Builds a single-page `test.note` from a cloned `Personal.note` template page, uploads to cloud — fixture/roundtrip generator | **THROWAWAY (test fixture).** Used for write-path fixtures. Stays as a test helper; not a CLI verb. Do not convert. |
| **e2e_write_test.py** | Smoke test: render → append to local `Personal.note` → upload; prints md5 before/after | **THROWAWAY (smoke test).** The append+upload path it exercises is already production code (`main._handle_write_request` `main.py:607`). It is a manual integration check, not a CLI verb. |

**Net:** Two scripts (`quick_filing_pilot.py`, `append_mgmt_list.py`) are near-ready CLI verbs; one (`quick_note_audit.py`) is the `plan` verb's engine. The rest are debug/exploration/test artifacts already superseded by library classes. Porting the three "REUSE" scripts prevents rebuilding logic that exists.

---

## 4. Sync/async & lifecycle

- **Established entry pattern:** synchronous `main()` that dispatches to `asyncio.run(async_body)` per command. Confirmed in 4 places:
  - `main.main()` `main.py:1003` → `asyncio.run(_run_login())` `main.py:1018`, `asyncio.run(_run_organizer(...))` `main.py:1022` for the one-shot commands; long-running modes use a manual `asyncio.new_event_loop()` + signal handlers (`main.py:1043`+).
  - `user_board.cli()` `user_board.py:631` → `asyncio.run(main())`.
- **The new `supernote` CLI should follow the one-shot `asyncio.run()` pattern** (NOT the manual-loop service pattern). Each verb resolves to an async coroutine that constructs a `SupernoteUploader`, `await`s `start()`/`stop()`, and calls the async primitives:
  - `SupernoteUploader.download_notebook` / `upload_notebook` / `_list_note_files` / `_ensure_authenticated` — all `async def` (`uploader.py:109`/`:155`/`:210`/`:255`).
  - `SupernoteReader.read_pages` / `resolve_filing_destination` / `process_file` — all `async def` (`reader.py:604`/`:313`/`:101`).
  - Synchronous primitives (`build_snapshot_from_notebook` `note_snapshot.py:61`, `copy_pages_to_end`/`remove_pages` `note_page_ops.py:25`/`:41`, `append_page_to_notebook` `notebook_writer.py:36`, `FilingLedger.*` `filing_ledger.py`) are called inline inside the coroutine — no nesting of event loops.
- **Lifecycle discipline for the CLI:** always pair `uploader.start()` with `uploader.stop()` in a `try/finally` (mirrors `quick_filing_pilot.py:33-41` and `append_mgmt_list.py` finally-block). The uploader owns a Playwright browser context (`uploader.py:50-66`) + a file lock; leaking it hangs the process.
- **Session/auth:** `SupernoteUploader.SESSION_FILE = ~/.paia/supernote/session.json` (`uploader.py:48`). `_ensure_authenticated()` (`uploader.py:255`) is the 403-trap recovery point — `supernote auth login` should call it with `headless=False` exactly as the existing `_run_login()` does (`main.py:958-968`), which prints the session path. The CLI can reuse `_run_login` almost verbatim.

---

## Findings summary

### Correct (already good — reuse as-is)
- `load_config(config_path)` (`main.py:100`) + `DEFAULT_CONFIG_PATH` (`main.py:51`): standalone, dict-returning, no daemon coupling. Reuse verbatim for the `--config` flag.
- `model_config.py` (`:32-83`): centralized route/credential resolution. Reuse for any OCR-bearing verb.
- Path defaults `state_db_path` (`main.py:69`), `filing_ledger_db_path` (`main.py:73`): consistent, env+TOML overridable. New CLI reads same keys for ledger continuity.
- Async primitive surface (`uploader.py`, `reader.py`, `note_page_ops.py`, `notebook_writer.py`, `note_snapshot.py`, `quick_filing.py`, `filing_ledger.py`): all spec-referenced primitives exist with the cited signatures. The CLI is genuinely thin orchestration.
- `QuickFilingService.run_once()` (`quick_filing_service.py:108`): encodes the core move+idempotency+ledger state machine; high reuse for `move --by-stars`.

### Blockers — none
No correctness blockers for adding the `supernote` binary. The seam (new `cli.py` + one `[project.scripts]` line) is clean and non-invasive.

### Notes / risks / follow-ups
- **NOTE — backup step has no implementation.** Spec safe-pipeline step 3 (`~/.paia/supernote/backups/<ts>/`) is entirely new code (`grep backup` = 0 hits in `src/`). Same for step 8 (re-download + SHA-256 verify). The planner must treat these as real new work, not wiring. Severity: medium (scope underestimation risk).
- **NOTE — 3× duplicated reader construction.** `SupernoteReader(...)` is built identically from config in `main.py:313`, `ingest_service.py:38`, and `quick_filing_pilot.py:27`. The CLI will be the 4th. Extract a `build_reader(config)` factory to avoid divergence. Severity: low.
- **NOTE — `_list_note_files` returns raw `userFileVOList` dicts** (`uploader.py:210-224`), not (name, page_count, modified) tuples. The `ls` verb must map raw fields (`fileName`, `updateTime` epoch-ms, `size`) — page count is NOT in the list payload and requires a per-notebook download/parse. Severity: low (implementation detail).
- **NOTE — private-method coupling.** `ls` needs `_list_note_files` (`uploader.py:210`, leading underscore) and `main._handle_write_request` reaches into `uploader._api_call` (`main.py:661`). The CLI will call `_list_note_files`/`_ensure_authenticated` directly. Consider promoting these to public API as part of the CLI work to avoid relying on underscore methods. Severity: low.
- **NOTE — config eager-evaluates model routes.** `DEFAULT_CONFIG` (`main.py:57-59`) calls `default_zai_*()` at dict-eval, hitting the cognition registry even for read-only verbs. Harmless but adds startup latency + a hard dependency on `paia_agent_runtime` for `supernote ls`. Severity: low.
- **NOTE — `__main__.py` routes `python -m paia_supernote` to the daemon** (`__main__.py:1-3`). Leave untouched; the agent surface is the `supernote` script, not `-m`.
