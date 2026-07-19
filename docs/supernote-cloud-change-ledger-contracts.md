# Supernote Cloud Change Ledger & Agent Contracts

User-facing reference for the Supernote Cloud change ledger, the cached agent
read/change contracts, the base-revision guarded write route, and the structured
error envelope. This is the operational contract for agents and operators; the
design rationale lives in
[`docs/superpowers/specs/2026-07-18-supernote-cloud-change-ledger-design.md`](superpowers/specs/2026-07-18-supernote-cloud-change-ledger-design.md)
and the write-safety analysis in
[`docs/superpowers/specs/2026-07-19-write-safety-limitations.md`](superpowers/specs/2026-07-19-write-safety-limitations.md).

The ledger is the only source an agent reads for cached Cloud state. Supernote
Cloud remains the canonical source of truth; the ledger is a durable, locally
cached projection of allowlisted notebooks that is refreshed by the background
ingest/poll loop.

## 1. Explicit Cloud notebook allowlist

Only notebooks explicitly listed in configuration are processed by the change
ledger. Everything else — detection, parse, OCR, and mutation — is ignored.

| Config key | Behavior |
|---|---|
| `cloud_change_ledger_notebooks` | **Explicit allowlist.** When present (even empty `[]`), it fully controls which notebooks the ledger processes. |
| `folio_sync_notebooks` | **Legacy fallback.** Used only when the explicit allowlist key is unset, so existing deployments keep their current behavior during migration. |

Resolution precedence (see `paia_supernote.config.resolve_ledger_notebooks`):

- The explicit `cloud_change_ledger_notebooks` list wins whenever the key is
  present. Setting it to `[]` is an intentional opt-out that disables ledger
  processing even when `folio_sync_notebooks` is set.
- When the explicit key is absent, the ledger falls back to
  `folio_sync_notebooks` so legacy configs are preserved unchanged.

Membership is case-insensitive and ignores a trailing `.note` suffix, so
`Quick`, `quick`, and `Quick.note` all match the same allowlisted notebook.

Non-allowlisted notebooks are skipped **before** Cloud download, parse, OCR, and
any write. The read and write contracts reject a non-allowlisted notebook with a
structured `disallowed_notebook` error rather than guessing or silently
processing it.

```toml
# Explicit allowlist (recommended)
cloud_change_ledger_notebooks = ["Quick", "Dev", "Home planning"]

# Legacy-only config keeps working unchanged when the allowlist key is unset:
#   folio_sync_notebooks = ["Quick"]
```

## 2. Cached reads (`supernote changes` / `supernote read`)

Agent-facing reads are served from the local SQLite ledger cache. The cached
read contract never instantiates the Cloud poller or uploader and never contacts
Supernote Cloud. This makes reads fast, replayable, and safe to retry.

```text
supernote changes <notebook>                          # all cached changes since the start
supernote changes <notebook> --since <change-id|iso>  # replayable explicit cursor
supernote changes <notebook> --latest                 # latest cached notebook/page state + OCR
supernote read <notebook> --pages <indexes>           # full cached OCR text for a page
```

- Every changes response returns `next_cursor` (a monotonic change ID) and the
  current `notebook_revision`. Pass that cursor back as `--since` for an
  auditable, replayable read.
- `--latest` returns the current cached snapshot: notebook revision, page count,
  ordered pages with their stable page IDs, content hashes, OCR status, and text
  preview.
- Repeated reads of unchanged cached state are idempotent and produce no new
  change records.
- If no cached snapshot exists yet for an allowlisted notebook, the read returns
  a structured `unknown_notebook` error telling you to run or wait for ingest.

The cache is refreshed by the background poll/ingest loop (see Section 6 for its
freshness bound). A read never triggers an unscheduled Cloud download or
re-OCR.

## 3. Per-agent cursors

Each agent gets an independent cursor per notebook so multiple agents can consume
changes at their own pace without clobbering each other.

```text
supernote changes <notebook> --agent <agent>           # changes after this agent's cursor
supernote changes <notebook> --agent <agent> --advance # acknowledge and advance the cursor
```

- `--agent <agent>` returns changes after that agent's last acknowledged cursor.
- `--advance` acknowledges the batch **only after** a successful, complete
  response has been produced. A failed query never advances the cursor.
- Cursors are isolated per agent and per notebook. Advancing one agent's cursor
  has no effect on any other agent.
- Cursors advance monotonically forward. Re-acknowledging the same or an earlier
  cursor is a no-op, and advancing beyond the latest cached change fails with a
  structured `invalid_cursor` error.

## 4. Base-revision guarded writes

The existing `supernote.write.requested` event route remains the only mutation
path. It is hardened with a fail-closed base-revision check that runs **before**
any download, apply, S3 PUT, or upload call (see
`paia_supernote.agent_write_contracts.validate_agent_write_request`).

A write request must carry:

- `agent` — a known, configured agent.
- `notebook` — an allowlisted target notebook, **or** `use_agent_default_notebook: true`
  to intentionally opt into the agent's configured default notebook. An empty
  notebook without that opt-in returns a structured `missing_notebook` error.
- `base_notebook_revision` — the `notebook_revision` the agent observed on its
  last read/change response (aliases `base_revision`, `notebook_revision`,
  `ledger_revision` are accepted).

The guard fails closed for:

| `error_code` | Trigger |
|---|---|
| `missing_notebook` | No notebook and no `use_agent_default_notebook` opt-in. |
| `missing_base_revision` | No base revision supplied. |
| `invalid_write_request` | Malformed request. |
| `disallowed_notebook` | Notebook is not in the allowlist. |
| `notebook_revision_mismatch` | The supplied `base_notebook` names a different notebook. |
| `unknown_notebook_revision` | Allowlisted notebook has no cached snapshot yet. |
| `stale_base_revision` | Cached revision is newer than the agent's base revision. |

A write with a matching current revision passes the guard and continues through
the existing backup → upload → re-verify pipeline unchanged. The guard is the
single gate before mutation; the low-level `SupernoteUploader.upload_notebook`
remains an internal primitive for non-agent pipelines and does **not** perform
the check.

## 5. Structured error guidance

All CLI, event, and agent-facing failures use one shared Pydantic envelope
(`paia_supernote.contract_errors.AgentError`), so expected failures never leak
tracebacks, cookies, tokens, or large semantic payloads:

| Field | Meaning |
|---|---|
| `error_code` | Stable machine-readable code (see tables above). |
| `message` | Concise human/model-readable explanation. |
| `field` | The offending field, when applicable. |
| `received` / `expected` | What was sent vs. what is required. |
| `valid_examples` | Concrete corrected requests. |
| `retryable` | Whether retrying the same request is safe. |
| `next_step` / `next_actions` | Exact recovery steps. |
| `mutation_applied` | Always `false` for these guards — nothing was written. |
| `details` | Additional structured context (e.g. revision comparison). |

- `--json` output is the canonical machine surface; human-readable CLI prose is
  derived from the same model and includes the code, explanation, and next
  action. CLI failures exit with status `2`.
- Secret-bearing fields (`authorization`, `cookie`, `session`, `token`,
  `api_key`, …) are redacted at any depth in `received`/`expected`/`details`.
  Write `content`/`pages` are summarized rather than echoed.
- Expired Supernote Cloud auth surfaces a retryable `cloud_auth_required` error
  with the refresh command as its next step.

## 6. Poll-interval bounded TOCTOU window (write-safety limitation)

The write guard validates `base_notebook_revision` against the **latest cached
ledger snapshot**, not against a live Cloud fetch issued at write time. This
leaves a residual time-of-check-to-time-of-use (TOCTOU) window:

- A human, device, or other agent change that lands **after** the last successful
  ledger poll but **before** the guarded write may not be visible until the next
  poll.
- The freshness bound is therefore the configured `poll_interval` plus any ingest
  delay — **not** a live compare-and-swap guarantee.

Concretely: a write can pass the guard and still overwrite a Cloud change that
arrived inside that window. Treat base-revision checks as "the notebook has not
changed since the last poll," not "the notebook cannot have changed." If your
workflow needs a tighter guarantee, run an on-demand poll immediately before
re-reading the revision. Do not represent this guard as live Cloud conflict
prevention.

## Durable state

The shared SQLite state database (`state_db_path`) gains durable ledger tables
alongside the existing `page_state` OCR rows; migrations are idempotent and
preserve existing rows. The ledger records:

- `notebook_snapshot` — notebook name, Cloud revision/hash, observed time, Cloud
  update time, page count, snapshot status.
- `page_snapshot` — notebook, stable page ID, page index, content hash,
  first/last-seen revisions, current/removed state, OCR status.
- `page_change` — monotonic change ID, notebook, revision, page ID, change type
  (`added`/`updated`/`removed`/`reorder`), old/new hashes and indexes, observed
  time.
- `agent_cursor` — agent identity, notebook, last acknowledged change ID.

Page IDs are the primary identity; page indexes are positional evidence only.
OCR runs only for pages whose stable ID is new or whose content hash changed —
reorder-only diffs never trigger OCR.
