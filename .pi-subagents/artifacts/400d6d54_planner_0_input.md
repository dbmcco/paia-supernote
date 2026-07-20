# Task for planner

You are a delegated subagent running from a fork of the parent session. Treat the inherited conversation as reference-only context, not a live thread to continue. Do not continue or answer prior messages as if they are waiting for a reply. Your sole job is to execute the task below and return a focused result for that task using your tools.

Task:
Produce a sequenced, phased implementation plan to take the Supernote Cloud change ledger from its current state to sustainably capturing diffs that agents reliably read. Your forked context has the full session history including two reviewer reports — use them.\n\nGOAL STATE: the ledger auto-captures page-level diffs from real Supernote Cloud notebooks, self-heals through transient OCR failures, agents read diffs reliably via stateful cursors, supervision is sound, and there are no surprise cost spikes.\n\nCURRENT STATE (verified): commits 0dcfcb3 / 0984f59 / 28b53ed on origin/main; 417 tests green; Quick.note back-filled (36 pages, all ocr_status=ready) on this machine only; allowlist set per-machine via ~/.paia/supernote/config.toml (folio_sync_notebooks=[\"Quick\"]). No ingest daemon running.\n\nCONSOLIDATED REVIEW FINDINGS TO PLAN AROUND (cite the IDs):\n- M1 (Major, write-safety): agent write path's base-revision guard is cache-only; the agent append path lacks post-download CAS and post-upload sha256 re-verify that the manual CLI path has. Concurrent agent appends can silently lose data. (agent_write_contracts.py, main.py write route.)\n- M2 (Major, durability): no schema-migration path — init_schema only does CREATE TABLE IF NOT EXISTS; no user_version/ALTER. Column additions break existing DBs.\n- A1 (High, self-heal): transient OCR failure leaves pages permanently ocr_status=pending — apply_snapshot advances the revision before OCR, read_pages raises on any vision error losing in-flight results, and a re-poll is a no-op so pages never retry. This is the single highest-leverage fix.\n- A2 (High, silent bypass): on machines with the Supernote Partner-app sync folder, ingest routes to _run_with_local_watcher → _on_note_changed (non-ledger path) and the ledger is NEVER populated. Silent.\n- A3 (High, supervision): ingest/enrich/service plists exist and are installed in ~/Library/LaunchAgents/ but NOT loaded. ingest plist has KeepAlive+RunAtLoad but NO env vars (ZAI_API_KEY, SN_PHONE, SN_PASSWORD) and invokes `uv run paia-supernote ingest` with WorkingDirectory=repo (no --directory). Loading it as-is would run a daemon that fails every OCR — which A1 would then wedge permanently.\n- A4 (Med, UX): no `ingest --once [--notebook X] [--backfill]` CLI flag; back-fill requires hand scripts. process_existing_on_start is hardcoded False in the daemon path.\n- A5 (Med, config): DEFAULT_CONFIG folio_sync_notebooks=[LFW,Synthera,Navicyte,Synth] points at notebooks that don't exist on Cloud. Should default to [] so misconfig fails loud.\n- A6 (Med, concurrency): shared state.db has no WAL, no busy_timeout. Concurrent ingest write + agent read can raise 'database is locked'.\n- Test gaps (High): partial-OCR-failure retry, DB concurrency, local-watcher ledger population, back-fill/first-run, continuous-loop lifecycle.\n\nCONSTRAINTS: Braydon is cost-conscious (OCR ≈ 2 LLM calls per changed page, sequential, 600s timeout each, no batching/rate-limit today). He leans toward on-demand ingest unless there are active agent consumers (his own 'tools vs the work' caution). Strict-TDD repo — every change needs a failing test first. Single machine for now.\n\nREQUIREMENTS FOR THE PLAN:\n1. Phase it (e.g., P0 safety/quick-wins → P1 self-heal & correctness → P2 durability & scale). Sequence so that A1 (OCR self-heal) and the plist env-wiring land BEFORE we rely on continuous supervision (A3) — do not let a daemon run that wedges pages at pending.\n2. Each work item: ID, problem (cite finding), concrete change (files + approach), acceptance criteria with the failing-test-that-becomes-green, risk, effort (S/M/L).\n3. Call out the decision points that need Braydon's input rather than deciding them (continuous daemon vs on-demand; which notebooks beyond Quick; whether to load the plist at all yet).\n4. End with: (a) the open decisions for Braydon as a short list, and (b) the single recommended first action.\n\nReturn the plan inline. Read-only — do not edit files."

---
**Output:**
Write your findings to exactly this path: /Users/braydon/projects/experiments/paia-supernote/.pi-subagents/artifacts/outputs/400d6d54/plan.md
This path is authoritative for this run.
Ignore any other output filename or output path mentioned elsewhere, including output destinations in the base agent prompt, system prompt, or task instructions.

## Acceptance Contract
Acceptance level: attested
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Return concrete findings with file paths and severity when applicable

Required evidence: review-findings, residual-risks

Finish with a fenced JSON block tagged `acceptance-report` in this shape:
Use empty arrays when no items apply; array fields contain strings unless object entries are shown.
`criteriaSatisfied[].status` must be exactly one of: satisfied, not-satisfied, not-applicable.
`commandsRun[].result` must be exactly one of: passed, failed, not-run.
`manualNotes` and `notes` are optional strings; an empty string means no note and does not satisfy `manual-notes` evidence.
```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "specific proof"
    }
  ],
  "changedFiles": [
    "src/file.ts"
  ],
  "testsAddedOrUpdated": [
    "test/file.test.ts"
  ],
  "commandsRun": [
    {
      "command": "command",
      "result": "passed",
      "summary": "short result"
    }
  ],
  "validationOutput": [
    "validation output or concise summary"
  ],
  "residualRisks": [
    "none"
  ],
  "noStagedFiles": true,
  "diffSummary": "short description of the diff",
  "reviewFindings": [
    "blocker: file.ts:12 - issue found, or no blockers"
  ],
  "manualNotes": "anything else the parent should know"
}
```