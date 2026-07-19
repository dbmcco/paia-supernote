# Supernote Write Safety Limitations

The agent write guard validates `base_notebook_revision` against the latest cached
Cloud change ledger snapshot before any apply/upload/S3 mutation call. This fails
closed for missing, stale, mismatched, disallowed, or unknown cached revisions.

Residual TOCTOU window: the guard does not perform a live Supernote Cloud metadata
fetch immediately before upload. A human/device/agent change that lands after the
last successful ledger poll but before the guarded write may not be visible until
the next poll. The freshness bound is therefore the configured Cloud poll interval
plus any ingest delay, not a live compare-and-swap guarantee.

Direct `SupernoteUploader.upload_notebook` calls remain a low-level internal
primitive for non-agent/manual pipelines. Agent-facing paths must call the
Pydantic write guard first.
