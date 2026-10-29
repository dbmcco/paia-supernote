# enrich-fix WIP checkpoint (2026-10-28 ~22:10 local)

- Diagnosis complete: empty error = httpx.ReadTimeout('') (empty args) at enrich timeout=60s vs glm-5.3 reasoning latency; root cause + categories + exponential-then-daily backoff all specced in red tests (tests/test_enrich_failure_policy.py, tests/test_enrichment.py).
- Next: implement in src (enrich_service.py categories/backoff, enrichment.py 300s timeout + thinking disabled + bounded 429 retry, page_state/main.py backoff-capped health count), then full hermetic suite (env -u ZAI_API_KEY).
- Tests currently RED (TDD), uncommitted work only touches tests/ in worktree; live tree untouched.
