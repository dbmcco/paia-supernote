# enrich-fix (2026-10-28 → 10-29, branch enrich-fix)

- Diagnosis: empty `error` = `httpx.ReadTimeout('')` (empty args) at the enrich call's
  60s timeout vs gateway glm-5.3 reasoning latency; `str(exc)` was stored verbatim.
- Implemented (all in this worktree; live tree untouched):
  - enrich_service.py: `categorize_enrich_exception` (fixed categories: timeout /
    rate_limited / http_error / network / response_shape / unknown — never free text),
    `format_enrich_error` (`Type:category`, never-empty), `enrich_retry_delay_seconds`
    (60s * 2^retry_count, capped at once per day), `enrich_backoff_cap_retry_count`;
    `run_once` failure path records the typed token and backoff delay, and logs
    `enrich_failed` with `category` + `retry_delay_seconds`.
  - enrichment.py: timeout 60s → 300s (root cause), `thinking: {"type":"disabled"}`
    mirroring the reader path, bounded in-call 429 retry (1 + 2 retries) honoring
    Retry-After.
  - page_state.py + main.py: `backoff_capped_count`; `render_status` now surfaces
    `enrich_backoff_capped=N` so the health check sees day-capped pages.
- Test-suite notes: `tests/test_pipeline_services.py` stale assertion updated
  (`"boom"` → `"RuntimeError:unknown"` — the lesson-70 never-free-text change).
  Two failure-policy tests' setups used `retry_delay_seconds=60`, which left the page
  not-yet-due under the pre-existing `next_retry_at` selection gate, so `run_once`
  correctly never ran; setup changed to `0` (aged/due) — intent (delay = 60*2^4 = 960s,
  cap = 86400s) unchanged. The gate itself is the backoff mechanism and stays intact.
- Full hermetic suite (env -u ZAI_API_KEY): 539 passed, 17 skipped, 16 deselected.
