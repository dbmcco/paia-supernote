# Task for worker

Implement the bounded follow-up task supernote-cloud-ledger.cached-full-text. Add a `--include-text` option to the existing `supernote changes <notebook> --latest` CLI path, pass it into LatestNotebookStateRequest(include_text=True), keep default latest output preview-only, and add/update focused offline tests proving full cached OCR is returned without constructing SupernoteUploader. Update README.md and docs/supernote-cloud-change-ledger-contracts.md to document `supernote changes <notebook> --latest --include-text --json` as the full cached page-read command. Do not touch write/Cloud mutation paths, uv.lock, scratch, organizer, or commit. Run the task validation commands and return files/tests.

---
**Output:**
Write your findings to exactly this path: /tmp/supernote-full-text-worker.json
This path is authoritative for this run.
Ignore any other output filename or output path mentioned elsewhere, including output destinations in the base agent prompt, system prompt, or task instructions.

## Acceptance Contract
Acceptance level: checked
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope

Required evidence: changed-files, tests-added, commands-run, residual-risks, no-staged-files

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