<!-- workgraph-managed -->
# Workgraph

Use Workgraph for task management in this repo's task spine.

**At the start of each session, run `wg quickstart` in your terminal to orient yourself.**
Default posture is `observe`: inspect via `wg show`, `wg list`, `wg status`, `wg viz`
before acting. Do NOT run `wg service start` as a generic session kickoff or against
a broad backlog — use it only for an explicit repo/task scope with a clear contract,
runner, verification, and repair/follow-up policy. Do not manually claim tasks you
have not been scoped to.

## For All Agents

Use `wg` CLI commands for task management in this repo's Workgraph spine. Do not use
built-in TaskCreate/TaskUpdate/TaskList/TaskGet tools — they are a separate system
that does NOT interact with Workgraph.

Subagents and repo-local execution surfaces are fine when they are the explicit
execution surface. Do NOT fan out broad autonomous dispatch against the Workboard/
paia-work backlog — Workboard operating items are not generic daemon food. For repo
implementation work, prefer a repo-local issue surface and repo-local
Workgraph/Speedrift execution; link artifacts and failed attempts back as evidence,
not as operating truth.

### Orchestrating agent role

The orchestrating agent does:
- **Conversation** with the user
- **Inspection** via `wg show`, `wg viz`, `wg list`, `wg status`, and reading files
- **Task creation** via `wg add` with descriptions, dependencies, and context
- **Monitoring** via `wg agents`, `wg service status`, `wg watch`

It does not write code, implement features, or do research itself unless explicitly
scoped. Dispatch only for explicit repo/task scope with a clear contract, runner,
verification, and repair/follow-up policy.

## Manual Supernote Note Triage And Moves

When the user asks to reorganize `Quick.note`, do the manual workflow the user and
agent used on 2026-06-10. Do not start by building a new app or broad automation.
Use the existing repo helpers only to inspect, render, stage, upload, and verify.

Core user conventions:

- `Quick.note` should keep only the latest scratch/current undecided pages.
- A native star on a page means the user already decided the page is ready to move.
  If a notebook name is handwritten beside the star, use that as the destination.
- Preserve the original handwritten page. Do not put generated summaries into target
  `.note` files.
- Use project/domain destinations for now. Thought zones like `(de)comp.note` are
  valid destinations.
- `Mgmt.note` is for working items, current priorities, status, and stuck/on-my-brain
  pages.
- Native Supernote index links are allowed only after a fixture-based link probe
  validates them. If the probe fails closed, do not create a generated index page.

Triage process:

1. Download the current cloud `Quick.note`, not stale OCR state.
2. Render pages or contact sheets to `/tmp`, using zero-based page numbers.
3. Review visually with the user page by page or in small batches.
4. Record a routing ledger while discussing pages. User corrections override OCR,
   classifier output, and model inference.
5. Stop triage when the user says to leave a page and everything after it. Keep those
   pages in `Quick.note`.
6. Before any write, restate the move map and kept-page list in concrete page numbers.

Move process:

1. Treat the confirmed routing ledger as source of truth.
2. Resolve handwritten destination names to actual cloud filenames, for example
   handwritten `DEV` may be `Dev.note`, and "home note" may be `Home planning.note`.
3. Make timestamped backups of every affected cloud notebook under
   `~/.paia/supernote/backups/`.
4. Dry-run locally first:
   - append each destination batch with `copy_pages_to_end`
   - remove moved pages from `Quick.note` with `remove_pages`
   - verify the moved page IDs were appended to each target
   - verify the remaining `Quick.note` page IDs match the kept-page list
   - write a dry-run report with original/staged counts and hashes
5. Upload target notebooks before uploading the rewritten `Quick.note`.
6. After upload, download every changed notebook and verify page counts plus SHA-256
   against the staged bytes.
7. If a cloud/API error appears after uploads, investigate the exact boundary before
   rollback. A `list/query returned 403` from the organizer usually means a stale
   Supernote Cloud auth session; restart/re-auth the organizer before touching notes.

Do not mutate real notebooks if the dry run cannot prove page ID preservation.
If a target notebook is an older Supernote file signature, be conservative: validate
that existing page content hashes are preserved before any reconstructed upload, and
tell the user exactly which notebooks required a local format upgrade.

PNG and temporary artifact cleanup:

- Rendered page PNGs and contact sheets are temporary review artifacts. Put them in
  `/tmp` with names like `/tmp/quick-page-016.png` or
  `/tmp/quick-pages-018-022-contact.png`.
- After the move is complete and verified, delete or leave only disposable `/tmp`
  artifacts. Do not add them to git.
- Do not delete real repo image assets such as checked-in screenshots unless the user
  explicitly asks.
- Keep the timestamped backup directory and its reports. Those are rollback evidence,
  not cleanup clutter.
