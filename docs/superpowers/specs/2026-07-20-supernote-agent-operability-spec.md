# Supernote Agent Operability Spec — Drift Anchor
*Generated for the speedrift run on 2026-07-20. Not a deliverable — specdrift reference only.*

## Problem
Braydon wants his agent fleet (Samantha, Derek, Ingrid, Caroline) to *operate* the
Supernote system for him — read the change ledger, read notebook content, write to
any notebook, and back-fill — not just push notes into one notebook. Today three
walls block that: notebooks in Cloud subfolders are invisible to the whole pipeline;
the agent tool is write-only; and the read surface isn't exposed to agents.

## Solution
Make the Cloud folder tree first-class and give agents a read surface.

1. **Folder support** (keystone): the uploader walks the Cloud folder tree so
   download/upload/list resolve a notebook by name regardless of subfolder
   (`cos/LFW.note`, `cos/Synth.note`, `cos/Navicyte.note`, `know/…`). Writes to a
   notebook land back in its own folder, not a new root copy. The poller lists
   subfolder notebooks so allowlisted cos/ notebooks are watched.
2. **cos/ back-fill**: once reachable, back-fill LFW, Synth, Navicyte into the
   change ledger (capped with `--max-pages`).
3. **Agent read surface**: extend `SupernoteTool` in paia-agent-runtime with
   read actions (`changes`/`show`/`read`/`ls`/`plan`) that wrap the `supernote`
   CLI, so all four agents can read the ledger and notebook content. Cross-repo:
   the tool lives in `experiments/paia-agent-runtime`.
4. **Verify**: an agent reads the Mgmt/Dev ledger and writes to LFW end-to-end.

## Out of Scope
- Moving/flattening notebooks out of their folders (Braydon files by purpose).
- Changing the vision/OCR model choice.
- Auto-starting the ingest daemon or a plist (on-demand ingest only).
- Rebuilding the wg binary (upstream TUI commits broke the build; follow-up).

## Success Criteria
- `supernote show LFW` (and Synth/Navicyte) returns pages without "not found".
- LFW/Synth/Navicyte appear in the change ledger after back-fill.
- `SupernoteTool` exposes a working `changes`/`read` action; an agent call
  returns ledger diffs without a shell.
- An agent successfully writes to LFW (in cos/) end-to-end.

## Key Constraints
- Notebook names are case-insensitive, `.note` suffix optional (existing `_normalize`).
- The ledger allowlist already includes LFW/Synth/Navicyte; only reachability is missing.
- Per-page OCR persist + retry-sweep recovery already landed — back-fills are resumable.

## Dissenting Concerns
- Folder traversal must not regress the root-only fast path or break the
  write-path CAS (`_raise_if_cloud_conflict_exists`, `_find_blocking_sibling_names`).
- Cross-repo read-surface task touches paia-agent-runtime; drift checks run per-repo,
  so that task's verification is manual/CLI-based, not paia-supernote's pytest.
