# RoboRev — Supernote Cloud Change Ledger

Machine-readable adversarial review summary for task
`supernote-cloud-ledger.adversarial-review`. Full narrative in
`adversarial-review.md`.

## Verdict: APPROVE — no blockers

| Boundary | Result | Evidence |
|---|---|---|
| Stale base-revision + uploader ordering | PASS | main.py guard at :657 before any download/apply/upload; 7 write-concurrency tests + 2 integration tests assert uploader not called |
| Cursor replay/advance + failed-query | PASS | cli.py get_changes before advance; monotonic ledger cursor; invalid_cursor on overshoot |
| Allowlist bypass + explicit empty | PASS | empty `[]` disables processing; resolved notebook overwrites payload; single canonical gate |
| Reorder-only OCR | PASS | `_ocr_page_indexes` selects only added/updated; reorder/removed excluded; 5 tests |
| Structured error/redaction/mutation_applied | PASS | mutation_applied=False always; redact_mapping at depth; content/pages summarized |
| Dependency/lockfile drift | PASS | `uv lock --check` green; rfc8785 is sibling paia-contracts only |
| Live Cloud mutation in tests | PASS | fakes/spies/mocks; integration asserts upload_calls==[] |

## Blocking findings

None.

## Nonblocking findings

| ID | Severity | Summary | Smallest fix | Owner |
|---|---|---|---|---|
| N1 | low–medium | `.note`-suffix allowlist inconsistency: poller/`notebook_is_ledger_allowlisted` do not strip `.note`, but read/write contracts do. Configuring `["Quick.note"]` silently disables ingest while reads/writes accept the name. Violates contract §1. Not a bypass. | Strip `.note` in `resolve_ledger_notebooks` (config.py) so all 4 membership paths agree. | existing fix-loop task |

## Accepted residual

| ID | Summary | Disposition |
|---|---|---|
| R1 | TOCTOU: guard checks cached revision not live Cloud; post-write does not bump ledger; duplicate/concurrent writes with same base revision can both pass until next poll. | Accepted — documented in write-safety-limitations.md §6. |

## Commands run

| Command | Result |
|---|---|
| `uv run pytest -q` | PASS — 414 passed, 17 skipped, 15 deselected |
| `uv run pytest -q -m integration tests/integration/` | PASS — 11 passed |
| `uv lock --check` | PASS |
| `uv run ruff check <new contract modules>` | PASS |
| `git diff --check` | PASS |
| `git diff --cached --name-only` | empty (no staged files) |
| probe: `notebook_is_ledger_allowlisted` vs `_canonical_allowed_notebook` with `.note` config | confirms N1 |
