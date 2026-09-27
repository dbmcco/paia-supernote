# Fix-Loop — Supernote Cloud Change Ledger (Adversarial Finding N1)

Bounded repair of the single nonblocking finding N1 from
`adversarial-review.md` / `roborev.md`. No source beyond `config.py` was
modified, no commit was made, and the off-limits files
(`uv.lock`, scratch, organizer) were not touched.

## Finding

**N1 — `.note`-suffix allowlist inconsistency (low–medium).**
`resolve_ledger_notebooks` (config.py) and `notebook_is_ledger_allowlisted`
operated on the raw allowlist strings, while the read contract
(`_canonical_notebook`) and write contract (`_canonical_allowed_notebook`)
each strip a trailing `.note` suffix from both the allowlisted and requested
names before comparing.

Concrete split when configured with
`cloud_change_ledger_notebooks = ["Quick.note"]`:

- **Cloud poller / ingest** — the poller's `_watched_notebook_keys` became
  `{"quick.note"}` while the Cloud-extracted stem is `"quick"`, so the notebook
  was skipped *before* download and `notebook_is_ledger_allowlisted(cfg, "Quick")`
  returned `False`.
- **Read / write contracts** — both stripped the suffix and accepted `"Quick"`,
  then failed later with `unknown_notebook` / `unknown_notebook_revision` because
  no snapshot was ever ingested.

This silently disabled Cloud ingest for a suffix-configured allowlist and
contradicted contract §1: *"Membership is case-insensitive and ignores a
trailing `.note` suffix, so `Quick`, `quick`, and `Quick.note` all match the
same allowlisted notebook."* It was **not** a security bypass (writes still
failed closed at `unknown_notebook_revision`).

## Repair

Normalized a trailing `.note` suffix inside `resolve_ledger_notebooks`
(`src/paia_supernote/config.py`) so the canonical resolved allowlist always
carries bare stems. This is the smallest fix named by the review and makes all
four membership paths agree on a single normalized name:

- added `_strip_note_suffix(name)` (case-insensitive on the suffix, matching
  the pattern already used in both contract modules);
- replaced the old whitespace-only `_clean` with `_normalize`, which strips
  whitespace **and** a trailing `.note`;
- `resolve_ledger_notebooks` now returns bare stems while preserving the
  original casing for display, the explicit-empty-allowlist semantics (`[]`
  still disables processing), case-insensitive matching in
  `notebook_is_ledger_allowlisted`, and the legacy `folio_sync_notebooks`
  fallback.

After the fix, with `cloud_change_ledger_notebooks = ["Quick.note"]`:

```
resolved = ["Quick"]                       # poller watch set = {"quick"}
notebook_is_ledger_allowlisted(cfg, "Quick")  -> True   # ingest
_canonical_allowed_notebook(cfg, "Quick")     -> "Quick"  # write
contract._canonical_notebook("Quick")         -> "Quick"  # read
```

Scope held: the only source change is `config.py`. The read/write contract
`_strip_note_suffix` calls become idempotent no-ops on the now-normalized list.
A bounded follow-up also normalized the public
`notebook_is_ledger_allowlisted` input through the same helper, so direct calls
with `Quick.note` agree with the poller, ingest, read, and write paths.

## Regression test

`tests/test_cloud_change_ledger.py::test_note_suffix_in_allowlist_agrees_across_all_membership_paths`
configures `cloud_change_ledger_notebooks = ["Quick.note"]` and asserts that
the poller watch set resolves to `{"quick"}`, ingest membership accepts
both bare and suffixed names, and the read/write canonical forms all resolve to
`"Quick"` (accepting both `"Quick"` and `"Quick.note"` requests, rejecting
`"Missing"`).

## Commands run

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_cloud_change_ledger.py::test_note_suffix_in_allowlist_agrees_across_all_membership_paths` | PASS — 1 passed |
| `uv run pytest -q tests/test_cloud_change_ledger.py` | PASS — 23 passed |
| `uv run pytest -q tests/test_agent_read_contracts.py` | PASS — 8 passed |
| `uv run pytest -q tests/test_write_concurrency.py` | PASS — 10 passed |
| `uv run pytest -q tests/test_structured_errors.py` | PASS — 8 passed |
| `uv run pytest -q tests/test_ingest_change_ledger.py` | PASS — 6 passed |
| `uv run pytest -q tests/test_cloud_poller_health.py` | PASS (within consolidated suite) |
| `uv run pytest -q tests/test_cli.py` | PASS — 22 passed (within consolidated suite) |
| `uv run pytest -q -m integration tests/integration/test_cloud_change_contract.py tests/integration/test_agent_contract_surfaces.py` | PASS — 11 passed |
| consolidated focused suite (config/ledger/read/write/errors/ingest/poller/cli) | PASS — 86 passed |
| `uv run ruff check src/paia_supernote/config.py tests/test_cloud_change_ledger.py` | PASS — All checks passed |
| `git diff --cached --name-only` | empty (no staged files) |

## Residual risk

- **R1 — poll-interval bounded TOCTOU (accepted, unchanged).** The write guard
  validates `base_notebook_revision` against the latest *cached* ledger
  snapshot, not a live Cloud fetch, and does not bump the ledger revision after
  a successful write. Two concurrent writes with the same base revision can
  both pass until the next poll refreshes the cache. This is the documented
  freshness bound (`poll_interval` + ingest delay) in
  `docs/superpowers/specs/2026-07-19-write-safety-limitations.md` §6 and the
  contracts doc §6. Accepted as designed; **not addressed** by this fix.
- **N1 residual nuance (closed).** The helper `notebook_is_ledger_allowlisted`
  originally did not strip a trailing `.note` from its *input*, so
  `notebook_is_ledger_allowlisted(cfg, "Quick.note")` returned `False`. This
  was harmless in production (the poller always passes bare stems) and out of
  scope for the original N1 repair, so it was left open. A bounded follow-up
  has since **closed** it: the helper now normalizes its `notebook` argument
  through the same `_normalize` helper used by `resolve_ledger_notebooks`
  (whitespace strip + trailing `.note` strip, case-insensitive suffix match), so
  `notebook_is_ledger_allowlisted(cfg, "Quick.note")` is now `True` and all
  four membership paths agree even when the helper is called directly with a
  suffixed name. The N1 regression test gained one assertion proving
  `notebook_is_ledger_allowlisted(config, "Quick.note") is True`. No other
  source was touched and no commit was made.
