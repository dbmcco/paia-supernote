# Adversarial Review — Supernote Cloud Change Ledger & Agent Contracts

Review task: `supernote-cloud-ledger.adversarial-review`
Bounded to two rounds. No source code modified. No new HTTP server or broad
organizer work proposed.

## Scope and method

Boundaries attacked (as tasked):

1. Stale base-revision writes and uploader call ordering.
2. Cursor replay/advance and failed-query behavior.
3. Allowlist bypass including explicit empty allowlist.
4. Reorder-only OCR.
5. Structured error guidance / redaction / `mutation_applied`.
6. Dependency / lockfile drift.
7. Accidental live Cloud mutation in tests.

Evidence gathered: full read of the approved design
(`docs/supernote-cloud-change-ledger-contracts.md`,
`docs/superpowers/specs/2026-07-19-write-safety-limitations.md`), the
implementation-contract and final-gates, the new modules
(`cloud_change_ledger.py`, `config.py`, `contract_errors.py`,
`agent_write_contracts.py`, `agent_read_contracts.py`), the changed
`main.py`/`ingest_service.py`/`cloud_poller.py`/`events.py` diffs, all new
test files, and the two offline integration suites. Commands run:
`uv run pytest -q` (414 passed, 17 skipped, 15 deselected), scoped integration
suite (11 passed), `uv lock --check` (pass), scoped `ruff check` (pass),
`git diff --check` (pass), and a direct probe of the `.note`-suffix allowlist
edge case.

## Result: NO BLOCKERS

Every attacked boundary holds. The implementation matches the approved design
and the documented operational contract. Details per boundary below.

### 1. Stale base-revision writes and uploader call ordering — PASS

`SupernoteService._handle_write_request` calls
`validate_agent_write_request(...)` (main.py:657) BEFORE any
`download_notebook`, `append_page_to_notebook`, `upload_notebook`, or
`_replace_pages_with_uploader` call. The `AgentWriteRevisionError` handler
(main.py:840) is declared before the generic `except Exception` and publishes a
structured `write.failed` event; it never reaches a mutation primitive. Tests
prove the uploader boundary is not called for missing/stale/mismatched/unknown
base revisions (`test_write_concurrency.py`,
`tests/integration/test_agent_contract_surfaces.py::test_service_stale_revision_blocks_upload_and_publishes_structured_conflict`,
`tests/integration/test_cloud_change_contract.py::test_stale_write_conflict_returns_structured_guidance_and_never_uploads`).
`_ensure_service_uploader_started()` is also deferred until after the guard.

### 2. Cursor replay/advance and failed-query behavior — PASS

`cmd_changes` (cli.py:359) calls `get_changes` first and only advances with
`response.next_cursor` afterward, so a failed query (disallowed/unknown/invalid
cursor) raises before any advance. Ledger `advance_agent_cursor` is monotonic
(`existing[0] >= change_id` ⇒ no-op) and isolated per (agent, notebook). The
contract's "advance only after a successful, complete response" is honored by
construction. Advancing beyond `latest_cursor` raises `invalid_cursor`
(`test_advance_agent_cursor_beyond_latest_raises_structured_error`).

### 3. Allowlist bypass including explicit empty allowlist — PASS (with Note N1)

Explicit empty `cloud_change_ledger_notebooks = []` correctly disables all
processing (`resolve_ledger_notebooks` returns `[]` because the key is present;
poller watches the empty set; read/write contracts reject with
`disallowed_notebook`). No bypass found: the write path overwrites
`payload["notebook"]` from the resolved value, then the guard's
`_canonical_allowed_notebook` is the single membership gate before mutation.
The `task_page_curate` delegation and the `tasks` rejection both run after the
guard. See Note N1 for a `.note`-suffix membership inconsistency (not a bypass).

### 4. Reorder-only OCR — PASS

`_ocr_page_indexes` (ingest_service.py) selects only `CHANGE_ADDED` and
`CHANGE_UPDATED` pages; `CHANGE_REORDER` and `CHANGE_REMOVED` never trigger OCR.
The diff (`_compute_diff`) emits `CHANGE_REORDER` only for same-hash pages that
changed relative position, so a content change is always `CHANGE_UPDATED`.
Unit + ingest tests prove reorder-only and mixed diffs OCR only new/updated
pages (`test_cloud_ingest_records_reorder_without_ocr`,
`test_cloud_ingest_adds_and_reorder_ocrs_only_added_pages`,
`test_ocr_page_indexes_excludes_reorder_and_removed_changes`).

### 5. Structured error guidance / redaction / `mutation_applied` — PASS

All guards set `mutation_applied=False`. `redact_mapping` redacts
secret-bearing keys (`authorization`, `cookie`, `token`, `api_key`, …) at any
depth and coerces non-serializable values. Write content/`pages` are summarized
via `_safe_received`. The shared `AgentError` envelope is reused by CLI
(exit 2, `--json` to stderr), event payload, and both contracts
(`test_structured_errors.py`, `test_cli_structured_errors.py`). No traceback
leakage asserted.

### 6. Dependency / lockfile drift — PASS

`uv lock --check` passes. The `uv.lock` change is confined to the sibling
editable `paia-contracts` (1.0.0→1.1.0, adds `rfc8785`). `paia-supernote` does
not import `paia_contracts`, does not depend on it in `pyproject.toml`, and
never imports `rfc8785`; its serialization uses `json.dumps`. The change is
unrelated to this feature and was flagged as pre-existing in the
implementation-contract and final-gates.

### 7. Accidental live Cloud mutation in tests — PASS

All new tests use fake uploaders (`SpyUploader`, `MultiPollListingUploader`,
`ListingUploader`), fake readers (OCR spies), temporary SQLite, and HTTP mocks.
Integration tests assert `upload_calls == []` and
`download_notebook.assert_not_awaited()`. `test_changes_cli_json_reads_cache_without_starting_uploader`
monkeypatches `SupernoteUploader` to fail if instantiated. No real Cloud
credentials, browser, or model calls.

## Nonblocking findings

### N1 — `.note`-suffix allowlist inconsistency (low–medium)

`resolve_ledger_notebooks` (config.py) and
`notebook_is_ledger_allowlisted` do NOT strip a trailing `.note` suffix, but
the read contract (`_canonical_notebook`) and write contract
(`_canonical_allowed_notebook`) DO strip it. Configuring the allowlist with a
suffix, e.g. `cloud_change_ledger_notebooks = ["Quick.note"]`, silently breaks
Cloud ingest — the poller's `_watched_notebook_keys` hold `{"quick.note"}` while
the extracted notebook name is `"quick"`, so the notebook is skipped before
download — yet reads/writes targeting `"Quick"` pass their allowlist check and
then fail with `unknown_notebook` / `unknown_notebook_revision` because no
snapshot exists.

This contradicts the documented contract (§1): *"Membership is case-insensitive
and ignores a trailing `.note` suffix, so `Quick`, `quick`, and `Quick.note` all
match the same allowlisted notebook."*

Confirmed by direct probe:
```
cfg = {'cloud_change_ledger_notebooks': ['Quick.note', 'Dev']}
notebook_is_ledger_allowlisted(cfg, 'Quick')  -> False   # ingest rejects
_canonical_allowed_notebook(cfg, 'Quick')     -> 'Quick' # write/read accept
```

Not a security bypass (writes still fail closed at `unknown_notebook_revision`),
and the recommended config uses bare names, but it is a silent functional
failure that violates the explicit contract promise and is untested.

**Smallest fix:** strip `.note` inside `resolve_ledger_notebooks` so all four
membership paths (poller watch set, `notebook_is_ledger_allowlisted`, read
canonical, write canonical) agree on a single normalized name. This belongs in
the existing fix-loop task.

## Accepted residual risks

### R1 — Poll-interval bounded TOCTOU (accepted, documented)

The write guard validates `base_notebook_revision` against the latest cached
ledger snapshot, not a live Cloud fetch. After a successful agent write the
change ledger revision is NOT bumped (main.py never calls `ledger.apply_snapshot`
post-upload), so a duplicate write with the same base revision passes the guard
until the next poll refreshes the cache. Two concurrent writes with the same
base revision can both pass. This is the documented freshness bound
(`poll_interval` + ingest delay) in
`docs/superpowers/specs/2026-07-19-write-safety-limitations.md` and §6 of the
contracts doc. Accepted as designed.

## Notes

- **Ruff:** repository-wide ruff is red (264 pre-existing errors in untouched
  legacy files); scoped checks on the new contract modules and changed files
  pass. Recorded in final-gates.md; no unrelated cleanup performed.
- **uv.lock:** the `rfc8785`/`paia-contracts` 1.1.0 change is a sibling-package
  lock entry, not used by this feature. `uv lock --check` is green.
- **Cosmetic:** in `_handle_write_request`, the `except AgentWriteRevisionError`
  handler references `notebook` which is `None` for the `missing_notebook`
  path; harmless (the event tolerates `None`), though the structured conflict
  carries `notebook=""` while the published event carries `notebook=None`.
