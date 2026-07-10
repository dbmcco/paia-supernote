# Supernote Agent CLI + Skill

**Status:** Design — pending user review
**Date:** 2026-07-10
**Author:** Avery (for Braydon)

## Problem

Reading or changing a Supernote note today is a fight. The library can do almost
everything — cloud read/write, OCR, page moves, star-aware filing, idempotency —
but there is no single, safe, agent-facing surface. To "get a note" or "make an
update" an agent (or Braydon) must compose Python modules or hand-run one-off
scripts, and the only documented workflow (`CLAUDE.md`) is a deliberate,
seven-step manual ceremony: download fresh, snapshot, backup, dry-run, mutate,
upload targets before source, re-download and verify SHA-256. That caution is
earned — one bad write scrambles `Quick.note` — but paying that ceremony by hand,
every time, is the friction this project removes.

## Decision

Build a **self-describing CLI** (`supernote`) that an agent drives, plus a thin
**skill** (`supernote`) that is the entry point. When Braydon says something like
"on my supernote…", the agent loads the skill; the skill tells the agent to drive
the `supernote` CLI; the CLI's own verbose output (rich `--help`, contextual
stdout that states what it found / did / skipped and why, suggested next command,
recovery hints on errors) guides the model so it learns the tool by using it.

- **CLI only. No MCP server.** (Confirmed by the user.) The CLI is the single
  source of truth for both capability and instruction.
- **Source: cloud only.** The physical device reaches the machine through
  Supernote Cloud; local/USB Partner-app sync is out of scope.
- **Write posture: invisible ceremony.** A single high-level command runs the
  whole safe pipeline internally and reports the result. The seven steps become
  implementation, not a manual checklist.

## Architecture

```
"on my supernote, move all starred notes"      natural language
        │
        ▼
  supernote SKILL  ──(description match)──►  agent loads skill
        │  "drive the `supernote` CLI; run `supernote --help`"
        ▼
  `supernote` CLI   ──►  verbose, self-describing guidance to the model
        │
        ▼
  command layer     (new, thin orchestration; this design)
        │
        ▼
  existing primitives (already built & tested):
    SupernoteUploader.download_notebook / upload_notebook / _list_note_files
    build_snapshot_from_notebook  (page-id, star, heading, content-hash)
    SupernoteReader.read_pages / resolve_filing_destination
    note_page_ops: copy_pages_to_end / remove_pages
    notebook_writer.append_page_to_notebook + SupernoteWriter.render_page
    StarDetector / parse_filing_header   (star + adjacent destination name)
    FilingLedger: operation_id_for / mark_* / get   (idempotency + resume)
```

The new code is a **thin command + UX layer** over tested primitives. It adds no
new cloud or binary-format capability. That keeps the project small and low-risk.

## Component: the `supernote` skill

A SKILL.md whose **description is the trigger**. It must match the way Braydon
actually refers to the device so agents select it without prompting — phrases
like "Supernote," "on my supernote," "my notes / notebook," "Quick.note," "move
starred pages," "append to tasks.note." The skill body is deliberately short:

- One line on what it does (agent-facing page & note management across the cloud
  notebook, driven through the `supernote` CLI).
- "Run `supernote --help` and `supernote <command> --help` to discover commands;
  the CLI guides you and prints the next step and recovery hints."
- A short table of the verbs (below) so the agent knows the shape without
  guessing.
- The hard conventions from `CLAUDE.md` that the CLI does not override by itself:
  preserve original handwritten pages; don't put generated summaries into real
  `.note` files; native cross-notebook index links stay gated behind the link
  probe; a handwritten notebook name next to a star is the destination.

The skill ships in the repo (e.g. `skill/supernote/SKILL.md`) and is linked /
installed into the agent skills path so it is discoverable by Avery, Caroline,
Ingrid, and Derek. The CLI is the capability; the skill is the door.

## Component: the `supernote` CLI

New agent-facing binary `supernote`, added under `[project.scripts]` and kept
**separate from the existing `paia-supernote` daemon entry**. Built with a
standard argparse-style subcommand layout. Every command is verbose by default
(human- and model-readable), with `--json` for machine consumption and `-q` for
quiet. Every write command accepts `--dry-run`.

### Read commands

| Command | Backed by | What it prints |
|---|---|---|
| `supernote ls` | `uploader._list_note_files` | cloud notebooks: name, page count, modified |
| `supernote show <notebook> [--pages 3-8]` | `build_snapshot_from_notebook` + ledger | per page: index, ★ starred, heading, OCR preview line, short content-hash, filing status (e.g. *"filed to Mgmt on Jul 9"*) |
| `supernote read <notebook> [--pages ...] [--render]` | `reader.read_pages` | full OCR text per page; `--render` writes page PNGs to `/tmp` (zero-based) for visual review |

`show` is "get a note." `read` is the long-form / visual version.

### Write commands (invisible ceremony — full safe pipeline runs internally)

| Command | Backed by | Use |
|---|---|---|
| `supernote append <notebook> (--text \| --file \| --stdin)` | `SupernoteWriter.render_page` + `append_page_to_notebook` | "add a page to tasks.note with my daily task list" |
| `supernote move <source> <target> --pages 3,4,5` | `copy_pages_to_end` + `remove_pages` + ledger | explicit, idempotent move |
| `supernote move <source> --by-stars [--to <notebook>]` | `StarDetector.starred_pages_from_metadata` → `reader.read_pages` → `reader.resolve_filing_destination` | "move all starred notes": `StarDetector` yields the zero-based indices of starred pages (from `FIVESTAR` metadata); those pages are OCR'd and the handwritten destination name beside each star is resolved to a real notebook (`resolve_filing_destination`; may match header tags via `notebook_name_to_tag`). `--to` overrides when no name is written. |
| `supernote remove <notebook> --pages ...` | `remove_pages` | remove pages |
| `supernote plan <source> [--by-stars]` | snapshot + detection + ledger, read-only | prints the move map: page → target, confidence, and *"already moved to X on <date>"* vs *"would move"*. **Zero cloud writes.** Lets the model reason before acting. |
| `supernote auth status` / `supernote auth login` | `uploader._ensure_authenticated` | surface and fix stale cloud auth interactively |

### The safe pipeline (every write command)

1. Download latest source (and targets) fresh via `download_notebook` — never use stale OCR state.
2. Snapshot; capture source page-ids + content-hashes.
3. Timestamped backup of every affected notebook to `~/.paia/supernote/backups/<ts>/`.
4. Idempotency check via `FilingLedger.operation_id_for(source_notebook=, source_pages=, source_revision=, target_notebook=)` — the source notebook, the page indices being moved, that notebook's revision stamp, and the target:
   - `completed` → skip, report *"already moved to <target> on <date> (op <id>)"*.
   - `target_written_source_pending` → **resume at the source-removal step** (do not re-append; no duplicate page in target).
5. Mutate in memory: `copy_pages_to_end` into targets, then `remove_pages` from source; zero recognition metadata on moved pages (existing helper).
6. Verify locally: moved page-ids present in targets; remaining source page-ids match the kept list; content hashes preserved.
7. Upload targets before source (`upload_notebook`), with conflict/auth retry.
8. Re-download every changed notebook; verify page counts + SHA-256 vs staged bytes.
9. Advance the ledger at each step: `mark_target_written` → `mark_source_removed` → `mark_completed`; on failure `mark_failed(error)` at the exact boundary.
10. Print a human-readable summary: backup path, before/after page counts, hashes, operation ids, and the suggested next command.

On any step failure: stop at the exact boundary, point at the backup, leave the
ledger in its honest partial state so the next run resumes rather than
double-applying. No silent partial state.

### Idempotency

Keyed on `FilingLedger.operation_id_for(source_notebook, source_pages, source_revision, target_notebook)`
— the source notebook, the page indices being moved, that notebook's revision
stamp, and the target. Re-running `move --by-stars` is a no-op for already-filed
pages (same source revision → same operation id → `completed`). If the source
notebook has since been edited and re-uploaded, its revision changes, producing a
new operation id — which is correct, because the state is genuinely new. A crash
between the target write and the source removal is recovered, not repeated. This
is the user's explicit requirement: *"if it has moved the page already, it does
not try to move it again."*

### Auth and the 403 trap

On stale-session 403 / csrf-expiry, the CLI prints actionable guidance — *"Supernote
Cloud auth stale. Run `supernote auth login`, then retry. No notes were changed."*
— instead of a raw `403` or a stack trace. `supernote auth status` / `auth login`
surface and fix it. This removes the "list/query returned 403 = restart the
organizer" mystery documented in `CLAUDE.md`.

### Verbose guidance is the product

Each command's stdout is written for a model to read: what it did, what it
skipped and why, and the next command to run. Errors include the recovery
command. Each subcommand's `--help` shows a worked example. This is how the model
learns the tool by using it, which is the whole point of "the CLI provides the
model with verbose guidance."

## Testing

The repo is TDD with quality gates. The command layer is thin orchestration over
already-tested primitives, so unit tests mock the cloud + OCR and assert:

- Pipeline order for a move (download → snapshot → backup → mutate → upload targets before source → verify → ledger).
- Idempotent skip on re-run (already-completed operation is a no-op, reported).
- Partial-failure resume (`target_written_source_pending` → resumes at removal, no duplicate append).
- No cloud write occurs on auth failure; the guidance message names the recovery command.
- `plan` touches nothing (zero uploads).
- `move --by-stars` routes each starred page to the destination parsed beside the star, and skips pages whose ledger entry is completed.

Existing fixture `.note` files (`calibration_output/`, `scripts/create_test_note.py`)
cover snapshot + mutate verification. Live-cloud tests stay marked `integration`
(existing convention via `pytest -m "not integration"`).

## Out of scope (v1)

- **MCP server.** CLI only, per the user's decision.
- **Local / USB Partner-app sync.** Cloud only.
- **Native cross-notebook index links.** Stay gated behind the existing
  `supernote_link_probe`; the CLI must not create generated index pages while the
  probe reports `real_note_writes_allowed: false`.
- **create-notebook, star/unstar, reorder.** Fast-follow verbs, not v1.

## Open / trivial decisions

- **Binary name:** `supernote` (recommended). Trivially changeable in `pyproject.toml`; does not affect the design.
- **Skill install path:** ship in-repo under `skill/supernote/`, linked into the
  agent skills path. Exact link mechanism confirmed at implementation time
  (matches how other workspace skills are registered).

## Acceptance

The project is done when an agent, given *"on my supernote, move all starred
notes,"* loads the skill, runs `supernote move Quick.note --by-stars`, and the CLI
files each starred page to its handwritten destination, skips any page already
moved, backs up every affected notebook, verifies the result, and prints a clear
summary — with no manual steps and no risk of a double-move.
