---
name: supernote
description: Use when the user refers to their Supernote device or notes — "on my supernote", "my supernote", "move starred pages", "append to tasks.note / Mgmt.note / Quick.note", "file these notes", or any read/write of Supernote Cloud notebooks. Drives the `supernote` CLI for safe page & note management (list, show, read, append, move, plan, remove, auth).
---

# Supernote — agent page & note management via the `supernote` CLI

This skill is the **door**. The `supernote` CLI is the **capability**: it lists,
reads, appends, and moves pages across Supernote Cloud notebooks and runs the full
safe pipeline (backup → mutate → verify → upload → re-verify) internally so you
never scramble a `.note` file by hand.

## First step — always

Run `supernote --help` and `supernote <command> --help`. The CLI is verbose by
default: it tells you what it found, what it did, what it skipped and why, and
prints the next command to run. Add `--json` for machine-readable output or `-q`
to suppress prose.

## Verbs

| Command | What it does |
|---|---|
| `supernote ls` | List cloud notebooks. |
| `supernote show <notebook> [--pages 3-8]` | Per-page overview: index, ★, heading, OCR preview line, content-hash. ("get a note") |
| `supernote read <notebook> [--pages ...] [--render]` | Full OCR text per page; `--render` writes page PNGs to /tmp. |
| `supernote append <notebook> (--text \| --file \| --stdin) [--agent Avery]` | Append a rendered page to a notebook. |
| `supernote move <notebook> --by-stars [--to <notebook>]` | File every starred page to its handwritten destination, through the safe pipeline. |
| `supernote move <notebook> --pages 3,4,5 --to <notebook>` | Explicit idempotent move. |
| `supernote plan <notebook> [--by-stars]` | Read-only move preview: page → target, would-move vs already-moved. **Zero writes.** |
| `supernote remove <notebook> --pages ...` | Remove pages (safe pipeline). |
| `supernote auth status` / `supernote auth login` | Check / fix cloud auth. |

Every write command accepts `--dry-run`.

## Hard conventions (the CLI enforces most of these; you must respect the rest)

- **Starred pages have a handwritten destination name beside the star.** `move
  --by-stars` reads that name and files the page there. `--to` overrides when no
  name is written.
- **Moves are idempotent.** If a page was already filed, `move` skips it and says
  so. Never try to re-move a page the ledger already marks complete.
- **Original handwritten pages are preserved.** The CLI only moves/removes whole
  pages; it never rewrites a handwritten page in place.
- **Do not put generated summaries into real `.note` files.** `append` adds a
  clearly agent-signed page; do not fabricate content as if Braydon wrote it.
- **Native cross-notebook index links stay gated** behind the existing link probe;
  do not create generated index pages.
- **Run `plan` before `move --by-stars`** the first time on a notebook so the
  routing is visible before anything changes.
- **Auth 403 → `supernote auth login`, then retry.** On stale auth the CLI prints
  this and changes nothing.

## How to think about a typical request

> "on my supernote, move all starred notes"

1. `supernote plan Quick.note --by-stars` — see where each starred page would go.
2. If routing looks right: `supernote move Quick.note --by-stars`.
3. Read the summary (backup path, before/after page counts, operation ids).
4. `supernote show <notebook>` to confirm.

If `plan` reports `needs review` for a page (no destination detected), ask Braydon
where it should go and use `move <notebook> --pages N --to <target>` explicitly.
