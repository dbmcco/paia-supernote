# Implementation Plan — Supernote Cloud Change Ledger: Sustainable Operation

## Goal
Take the Supernote Cloud change ledger from "manually seeded, Quick-only, OCR-fragile" to a state where it auto-captures page-level diffs from real Cloud notebooks, self-heals through transient OCR failures, exposes reliable stateful agent cursors, runs under sound supervision, and has no surprise cost spikes — without a daemon ever wedging pages at `pending`.

## Current State (verified)
- Commits `0dcfcb3` / `0984f59` / `28b53ed` on `origin/main`; 417 tests green.
- `Quick.note` back-filled on this machine only: 36 pages, all `ocr_status=ready`, `page_change=36`.
- Per-machine allowlist via `~/.paia/supernote/config.toml` (`folio_sync_notebooks=["Quick"]`).
- No ingest daemon running. Plists exist + installed in `~/Library/LaunchAgents/` but **not loaded**.

## How the review findings map to phasing
The two reviewers converged: **the ledger algorithm, diff engine, and agent read/write contracts are sound and well-tested; the gaps are operational wiring and resilience, not core correctness.** That lets us sequence resilience (A1) and env-wiring **before** continuous supervision (A3), so we never run a daemon that fails OCR with no retry path. The ordering invariant: **A1 + plist env-wiring land before A3 load.**

---

## P0 — Safety, correctness floor, and quick wins (no daemon yet)

### P0.1 — OCR self-heal: pending pages must retry (A1)  · effort L · **highest leverage**
- **Problem:** `_on_cloud_note_changed` calls `ledger.apply_snapshot(...)` (advancing the revision) **before** `reader.read_pages(...)`. `read_pages` raises on any single vision error, discarding already-read pages; the revision is already advanced, so a re-poll detects no change and pages sit at `ocr_status=pending` forever.
- **Change:**
  - `src/paia_supernote/ingest_service.py` — decouple snapshot-apply from OCR: apply the structural snapshot, compute `pages_to_ocr`, then OCR **per page in a try/except** so one failure doesn't lose the others; record a `last_error`/`retry_count` and leave failing pages `pending` with a backoff timestamp (`page_state` already has `retry_count`, `next_retry_at`, `last_error`, `last_error_stage` columns — use them).
  - Add a "re-OCR due pending pages" sweep: when a poll runs, in addition to changed pages, pick up pages whose `next_retry_at` has passed. This makes `pending` recoverable instead of terminal.
  - `src/paia_supernote/reader.py` — make `read_pages` resilient: catch per-page transcription errors and return a partial result set + error metadata rather than raising atomically (preserve the successes already read).
- **Acceptance (TDD):**
  1. Failing test: a vision mock that raises on page 5 of 10 → pages 0–4 are still persisted `ready`, page 5 is `pending` with `retry_count=1` and a `next_retry_at`; no exception propagates. (Goes red first.)
  2. Failing test: a `pending` page whose `next_retry_at` has passed is re-OCR'd on the next sweep and flips to `ready` when the vision mock now succeeds.
- **Risk:** Changing `read_pages` return shape touches callers — audit all call sites (CLI `read`, `show`-adjacent, back-fill script). Medium risk; contained by the per-page tests.

### P0.2 — DB concurrency hardening (A6) · effort S
- **Problem:** shared `state.db` has no WAL and no `busy_timeout`; a concurrent ingest write + agent read can raise `database is locked`.
- **Change:** open every connection with `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. Centralize this in a small `_connect()` helper used by `cloud_change_ledger.py`, `page_state.py`, `filing_ledger.py` (and any other sqlite open sites) so the pragma is applied uniformly.
- **Acceptance (TDD):** failing test that spawns a writer + reader on the same temp DB concurrently and asserts no `OperationalError: database is locked` under WAL + busy_timeout.
- **Risk:** Low. WAL is backward-compatible; verify the backup/re-verify path still works (it reads raw bytes, unaffected).

### P0.3 — Fail-loud default allowlist (A5) · effort S
- **Problem:** `DEFAULT_CONFIG["folio_sync_notebooks"] = ["LFW","Synthera","Navicyte","Synth"]` points at notebooks that don't exist on Cloud; a fresh machine silently ingests nothing.
- **Change:** `src/paia_supernote/main.py` — default `folio_sync_notebooks` to `[]`. Allowlist must be set explicitly (config.toml or env). `resolve_ledger_notebooks([])` already yields the structured `disallowed_notebook` error, so misconfig is loud.
- **Acceptance (TDD):** failing test asserting `load_config()` with no config file + no env yields an empty allowlist; and that `changes <real-or-any-notebook>` on a fresh config returns the structured `disallowed_notebook` envelope (not a silent empty success).
- **Risk:** Low. This machine already overrides via config.toml; behavior change is "loud failure on unconfigured," which is the intent.

---

## P1 — Back-fill UX, silent-bypass fix, and write-safety (still no daemon)

### P1.1 — `ingest --once [--notebook X] [--backfill]` CLI flag (A4) · effort M
- **Problem:** back-fill requires a hand script; `process_existing_on_start` is hardcoded `False` in the daemon path, so a fresh ledger never seeds existing notebooks.
- **Change:**
  - `src/paia_supernote/main.py` — add args to the `ingest` subparser: `--once`, `--notebook NAME`, `--backfill`.
    - `--once`: run exactly one poll cycle (or a single-notebook ingest) then exit 0.
    - `--backfill`: force `process_existing_on_start=True` for that run so existing notebooks are seeded.
    - `--notebook X`: restrict the one-shot to a single notebook.
  - Extract a reusable `_ingest_one(service, uploader, name, backfill)` helper so the CLI, the daemon, and (eventually) a test can all call it. This also retires the throwaway back-fill script as a supported path.
- **Acceptance (TDD):**
  1. Failing test: `ingest --once --notebook X --backfill` against a fake uploader + fresh temp DB seeds a snapshot + page changes and exits without looping.
  2. Failing test: plain `ingest --once` (no backfill) does **not** seed pre-existing notebooks (preserves the cost guard) but does capture a newly-changed one.
- **Risk:** Medium. Must not regress the daemon loop; keep the default (no flags) behavior identical to today.

### P1.2 — Local-watcher must populate the ledger (A2) · effort M
- **Problem:** when the Partner-app sync folder is present, `_run_with_local_watcher` routes to `_on_note_changed` (non-ledger) and the ledger is never populated — silently.
- **Change:** `src/paia_supernote/ingest_service.py` — make the local-watcher path flow through the **same** ledger-aware `_on_cloud_note_changed` (rename/generalize as needed) so both Cloud-poll and local-sync write diffs. Gate by allowlist identically.
- **Acceptance (TDD):** failing test simulating the local-watcher path with a sync-folder fixture asserts `page_change`/`notebook_snapshot` rows are written for an allowlisted notebook.
- **Risk:** Medium. Preserve the existing local-watcher event semantics; ensure no double-ingest when both paths could fire.

### P1.3 — Agent write path: post-download CAS + post-upload re-verify (M1) · effort M
- **Problem:** the agent write/append route's base-revision guard is cache-only; it lacks the post-download CAS check and the post-upload sha256 re-verify that the manual CLI move path has. Concurrent agent appends can silently lose data.
- **Change:** `src/paia_supernote/agent_write_contracts.py` + the write route in `main.py` — mirror the manual path: after download, assert the cloud revision still matches the agent's claimed base (else reject with a structured `stale_base_revision` error); after upload, re-download and sha256-verify (reuse `_reverify_sha256`). Make the rejection a first-class structured error so agents can retry.
- **Acceptance (TDD):**
  1. Failing test: two concurrent appends off the same base → the second is rejected with `stale_base_revision` (not a silent overwrite).
  2. Failing test: post-upload sha256 mismatch raises/reports rather than reporting success.
- **Risk:** Medium. This is the data-loss-class fix; get the structured-error contract right and add an integration test.

---

## P2 — Durability and scale (once P0/P1 are green)

### P2.1 — Schema migrations via `user_version` (M2) · effort M
- **Problem:** `init_schema` only does `CREATE TABLE IF NOT EXISTS`; no `PRAGMA user_version` / `ALTER TABLE`. Adding a column breaks existing DBs.
- **Change:** add a lightweight migration layer: stamp `PRAGMA user_version`, and on open run idempotent migrations (`ALTER TABLE ... ADD COLUMN ... ` guarded by the current version). Centralize in the `_connect()` helper from P0.2 so every store opens through it.
- **Acceptance (TDD):** failing test that opens a DB at `user_version=N`, applies a migration adding a column, and confirms `user_version=N+1` + the new column exists + old data intact.
- **Risk:** Medium. Get the version-bump ordering right; never run a migration that assumes a column exists.

### P2.2 — Back-fill batching / rate-limit (cost guard) · effort M
- **Problem:** first-run back-fill OCRs every page sequentially with a 600s timeout each and no batching/rate-limit; large notebooks are a real cost/time spike.
- **Change:** add a configurable page-batch size and a soft inter-batch delay for back-fill; surface a pre-flight estimate ("notebook X has N pages, ≈M OCR calls") that requires `--yes` (or a `--max-pages` cap) before proceeding.
- **Acceptance (TDD):** failing test asserting the estimate is printed and that exceeding `--max-pages` aborts before any OCR.
- **Risk:** Low. Cost guard only; no correctness impact.

### P2.3 — Supervision wiring + load (A3)  · effort S (once P0.1 + env-wiring done) · **gated on decisions**
- **Problem:** plists installed but not loaded; ingest plist has no env vars, so loading as-is fails every OCR (wedged by A1 until P0.1).
- **Change:**
  - Update `scripts/paia-supernote-ingest.plist` (and the installed copy) to carry required env: `ZAI_API_KEY`, `SN_PHONE`, `SN_PASSWORD` (and any `SUPERNOTE_*`), or have the daemon source `/Users/braydon/projects/.env` on start. Also ensure the invocation resolves the project (the plist uses `uv run paia-supernote ingest` with `WorkingDirectory=repo`, which is fine, but verify `~/.local/bin/uv` path and that `--once`/loop semantics match the chosen model).
  - Only **after** P0.1 (self-heal) is merged: `launchctl load ~/Library/LaunchAgents/com.paia.supernote.ingest.plist` and confirm logs at `~/Library/Logs/paia-supernote/ingest.*.log` show healthy polls + successful OCRs, not `cloud_auto_reauth_failed`/empty OCR.
- **Acceptance (manual + log):** loaded daemon captures a real change to an allowlisted notebook within one poll interval, OCRs the changed page to `ready`, and a transient vision failure self-heals on a later sweep (no page stuck `pending`).
- **Risk:** High if done out of order. **Hard gate: do not load until P0.1 + env-wiring are verified on this machine.**

---

## Cross-cutting: test coverage to add (TDD, fold into the items above)
- Partial-OCR-failure retry (P0.1).
- DB concurrency / `database is locked` under WAL (P0.2).
- Local-watcher ledger population (P1.2).
- Back-fill / first-run seeding via `--once --backfill` (P1.1).
- Continuous-loop lifecycle: one poll → ingest → diff → `ready`; cursor advance boundaries; `pageSize` truncation behavior.
- Default-config sanity / fail-loud allowlist (P0.3).

---

## Open Decisions for Braydon (do not decide these)
1. **Supervision model:** continuous daemon (load the plist, KeepAlive, OCR-per-change) **vs.** on-demand ingest (run `ingest --once` when you want a refresh). Given the "tools vs the work" caution and that there are no active agent consumers yet, the plan assumes **on-demand** until a consumer exists; loading the plist (P2.3) is explicitly gated.
2. **Notebook set beyond Quick:** which of `Mgmt, Dev, Meetings, Walk, Home planning, Personal, Stories` to add to the allowlist. Each add = one config line + a back-fill. Recommend starting with the actively-mutated work notebooks (`Mgmt`, `Dev`, `Meetings`) and expanding as consumers appear.
3. **Whether to load any plist now at all.** Recommendation: **no** — not until P0.1 + env-wiring are in, to avoid a daemon that wedges pages at `pending`.

## Recommended First Action
**Implement P0.1 (OCR self-heal) under TDD** — it is the single highest-leverage fix: it converts a terminal `pending` state into a recoverable one, and it is the hard prerequisite for ever safely running continuous supervision. Start with the failing per-page-OCR test, then the re-OCR-due-pending sweep test, then make them green.
