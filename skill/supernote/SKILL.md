---
name: supernote
description: Use when the user refers to their Supernote device or notes — "on my supernote", "my supernote", "move starred pages", "append to tasks.note / Mgmt.note / Quick.note", "file these notes", "read page N", or any read/write of Supernote Cloud notebooks. Drives the `supernote` CLI for safe page & note management (list, show, read, append, move, plan, remove, auth).
---

# Supernote — agent page & note management via the `supernote` CLI

You operate Braydon's **Supernote Cloud** notebooks through one CLI: `supernote`.
The CLI is the single source of truth for both capability and instruction — it is
verbose by default and runs the full safe pipeline (backup → mutate → verify →
upload → re-verify) internally, so you never scramble a `.note` file by hand.

## Step zero — credentials and auth (read this before you run anything)

Auth is **automatic**. You should almost never need to do anything by hand.

- The session lives at `~/.paia/supernote/session.json` and refreshes itself.
- The phone + password are read from environment variables **`SN_PHONE`** and
  **`SN_PASSWORD`**, defined in the workspace env file the daemon already
  sources (`/Users/braydon/projects/.env`).
- When the session expires, the CLI logs back in silently using those env vars.
  You do nothing. The Supernote organizer/ingest daemons self-heal the same way.
- For OCR (`read`, `show`) a vision key is also needed — `ZAI_API_KEY` (or set
  `SUPERNOTE_VISION_BACKEND=anthropic` + `ANTHROPIC_API_KEY`). These are already
  in the same workspace `.env`. If `read` returns empty/blank text, you almost
  certainly forgot to source the env: `set -a; source /Users/braydon/projects/.env; set +a`.

**Do not** open a browser or try to log in interactively. If the env vars are
absent, `supernote auth login` opens a visible browser and waits for a human —
that is a fallback, not the normal path. Flag it to the user instead of stalling.

To check health at any time: `supernote auth status`. It prints whether the
session is alive and whether auto-login is armed (`SN_PHONE/SN_PASSWORD set`).

## The first command to run

`supernote --help` — the epilog restates the conventions below. Then
`supernote ls` to see which notebooks exist, and `supernote show <notebook>` for a
per-page overview (index, star, heading, OCR preview, content-hash). Every
command accepts `--json` for machine-readable output or `-q` to suppress prose.

## Commands

| Command | What it does |
|---|---|
| `supernote ls` | List cloud notebooks. |
| `supernote show <notebook> [--pages 3-8]` | Per-page overview: index, ★, heading, OCR preview line, content-hash. ("get a note") |
| `supernote read <notebook> [--pages ...] [--render]` | Full OCR text per page; `--render` writes page PNGs to `/tmp` and prints each path. |
| `supernote append <notebook> (--text \| --file \| --stdin) [--agent Avery]` | Append a rendered, agent-signed page to a notebook. |
| `supernote move <notebook> --by-stars [--to <notebook>]` | File every starred page to its handwritten destination, through the safe pipeline. |
| `supernote move <notebook> --pages 3,4,5 --to <notebook>` | Explicit idempotent move. |
| `supernote plan <notebook> [--by-stars]` | Read-only move preview: page → target, would-move vs already-moved. **Zero writes.** |
| `supernote remove <notebook> --pages ...` | Remove pages (safe pipeline). |
| `supernote auth status` / `supernote auth login` | Check health / refresh the session (normally automatic). |

Every write command accepts `--dry-run`.

## Conventions you must respect

- **Pages are ZERO-BASED.** Page 29 is the 30th page in the Supernote app. The
  app shows human page numbers starting at 1; the CLI uses the index. When the
  user says "page 30", pass `--pages 29`.
- **Notebook names may be bare or carry `.note`:** `Quick` and `Quick.note` are
  the same notebook. The CLI normalizes both.
- **Starred pages carry a handwritten destination name beside the star.**
  `move --by-stars` reads that name and files the page there. Use `--to` to
  override when no name is written.
- **Moves are idempotent.** If a page was already filed, `move` skips it and says
  so. Never re-move a page the ledger already marks complete.
- **Original handwritten pages are preserved.** The CLI only moves/removes whole
  pages; it never rewrites a handwritten page in place.
- **Do not put generated summaries into real `.note` files.** `append` adds a
  clearly agent-signed page; do not fabricate content as if Braydon wrote it.
- **Run `plan` before `move --by-stars`** the first time on a notebook so the
  routing is visible before anything changes.

## How to think about a typical request

> "on my supernote, move all starred notes"

1. `supernote plan Quick.note --by-stars` — see where each starred page would go.
2. If routing looks right: `supernote move Quick.note --by-stars`.
3. Read the summary (backup path, before/after page counts, operation ids).
4. `supernote show <notebook>` to confirm.

If `plan` reports `needs review` for a page (no destination detected), ask Braydon
where it should go and use `move <notebook> --pages N --to <target>` explicitly.

## When something goes wrong

- **Exit code 2 / a "stale (403)" message:** auth couldn't be refreshed. If
  `SN_PHONE/SN_PASSWORD` are set, the message will say login failed — check the
  credentials. If they're unset, the message tells you to set them or run
  `supernote auth login`. **No notes were changed** on this path; it is safe to
  retry once auth is fixed.
- **`<notebook> not found in Note folder`:** run `supernote ls` to confirm the
  real cloud name. Handwritten names sometimes differ from the cloud filename
  (e.g. handwritten `DEV` may be `Dev.note`, "home note" may be `Home planning.note`).
- **`read` returns blank text:** the vision env (`ZAI_API_KEY`) isn't loaded —
  source the workspace `.env` and retry.
- **`Quick.note.note not found`:** you doubled the suffix; just use `Quick`.
  (The CLI normalizes this now, but if you see it, pass the bare name.)

## Safety net

Every write is backed up under `~/.paia/supernote/backups/<timestamp>/` and
re-verified by SHA-256 after upload. If anything fails mid-pipeline, the original
cloud notebook is untouched. You can always roll back from the backup directory.
