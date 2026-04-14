# paia-supernote Design Spec
**Date:** 2026-04-14  
**Status:** Approved for implementation planning

---

## Overview

`paia-supernote` is a PAIA service that makes Braydon's Supernote device a bidirectional surface for his agents. Agents write native `.note` format pages into his existing notebooks in their own handwriting styles. The device syncs automatically. Braydon writes back — tasks, strategy fragments, half-formed ideas — and agents read, react, and follow up.

The notebooks stay Braydon's. Agents participate when they have something worth contributing.

---

## Agent-to-Notebook Mapping

| Agent | Font | Primary Notebook | Write Trigger |
|---|---|---|---|
| Sam | Bradley Hand | Quick.note (+ any) | Task page curation, on-demand |
| Caroline | Noteworthy | LFW.note | On-demand, post-discussion write-back |
| Ingrid | Chalkduster | Synth.note | On-demand, post-discussion write-back |

**No scheduled daily briefs.** Agents write when they have signal worth writing. Notebooks are not cluttered with routine updates.

---

## Architecture

Single Python daemon (`paia-supernote`) integrated with `paia-events` at port 3511. Agents never touch Supernote directly — they publish events and receive events.

### Event Interface

**Inbound (agents → service):**
- `supernote.write_requested` — `{agent, notebook, content_type, content}`
- `supernote.write_requested` with `content_type: "task_page_curate"` — Sam triggers a full task page read-reason-rewrite cycle; fired by Sam on a schedule (e.g. morning) or when paia-work task state changes significantly

**Outbound (service → agents/integrations):**
- `supernote.note_transcribed` — `{notebook, page, text, timestamp}` → folio
- `supernote.checkbox_completed` — `{task_id, notebook, page}` → paia-work
- `supernote.snippet_detected` — `{notebook, page, text, agent}` → Caroline or Ingrid

### Four Internal Modules

**`writer.py`**  
Renders content to a `.note` page layer:
- PIL renders text in the agent's assigned font
- RATTA_RLE encoder converts bitmap to Supernote's native layer format
- Page layout: date top-right in small system font, agent signature bottom-left
- Pages appended at the back of the target notebook via `supernotelib.merge()`
- Font sizes require device calibration pass before finalizing (Supernote A5X: 1404×1872 @ 226 DPI)

**`uploader.py`**  
Pushes merged `.note` files to Supernote Cloud:
- Playwright browser session handles CSRF + session cookie auth
- Three-step upload: `upload/apply` → S3 PUT (presigned URL, no auth) → `upload/finish`
- Session persisted to disk; Playwright re-authenticates interactively when token expires
- Targets correct notebook path in cloud by agent mapping

**`watcher.py`**  
Monitors local `.note` files for changes:
- FSEvents on `~/Library/Containers/com.ratta.supernote/Data/Library/Application Support/com.ratta.supernote/908410628964298752/Supernote/Note/`
- 5-second debounce to avoid partial-sync fires
- Fires only on `.note` file modification (not tmp files)
- Tracks per-file checksums to detect real changes vs spurious events

**`reader.py`**  
Processes changed `.note` files:
- `supernotelib` extracts modified pages as PNG images
- Claude vision (Sonnet) transcribes text; handles Braydon's handwriting reliably
- Diffs checkbox state against prior snapshot — detects newly checked boxes
- Classifies content: task (□/○ marker), strategy snippet, or general note

---

## Task Page (Quick.note)

The task page is Sam's primary output surface and Braydon's task input channel.

**Sam's role — intelligent curation:**
Sam reads the full task page via vision, reasons about its current state, and rewrites it with the best organization: grouping by focus/orbit, moving completed items, inserting new paia-work tasks where they fit. She has agency to reorganize — she's curating, not just appending. When the page fills, overflow to a new page.

**Braydon's input — handwritten tasks:**
Braydon writes tasks anywhere on the page. Each task is marked with:
- **□** (box) = focus task
- **○** (circle) = orbit task

On next sync, Sam's reader detects new handwritten □/○ items, extracts task text + tag, and adds them to paia-work with the appropriate label.

**Checkbox completion:**
When Braydon checks a box with his pen, the watcher detects the `.note` change, vision reads the page, checkbox diff identifies the newly-marked item, and `supernote.checkbox_completed` fires → paia-work marks the task done.

---

## Strategic Snippet Detection (LFW.note + Synth.note)

When Braydon writes strategy fragments, meeting prep notes, or half-formed ideas in these notebooks, Caroline and Ingrid actively review.

**Flow:**
1. Watcher fires on LFW.note or Synth.note change
2. Vision transcribes changed pages
3. Agent reviews for snippets worth following up (strategy fragments, open threads, meeting context that needs developing)
4. If clarification warranted → agent initiates in browser: *"I was reviewing LFW.note and on page 3 I noticed your comments on Aberdeen positioning — worth talking through before the call?"*
5. Braydon and agent discuss in browser
6. Agent writes the clarified/developed thinking back into the notebook page
7. Adds resolved insight to folio

**Routing:** LFW.note changes → Caroline reviews. Synth.note changes → Ingrid reviews. Both use the same detection + outreach pattern.

**What counts as a snippet:** incomplete strategic thought, named client/project with an open question, meeting prep that lacks context, or something the agent recognizes as relevant to an active workstream.

**What doesn't trigger it:** fully formed notes, task items (handled by task system), or general journaling.

---

## Read Path → Folio

All transcribed content from all watched notebooks flows to folio, indexed by:
- Notebook name
- Page number
- Date of capture
- Agent who reviewed (if applicable)

Folio is the primary destination for read content. paia-memory receives content via folio's existing ingestion pipeline rather than a direct write from this service.

---

## Sync Architecture

**Supernote Cloud is the sync bus.** No USB required, no Partner app dependency for the write path. The Partner app continues to sync device → Mac for the read path (local `.note` files).

Read path dependency on Partner app is acceptable — it's already installed and auto-syncs on WiFi.

---

## Known Open Items

1. **Font size calibration** — render a test page at 1404×1872 and validate readability on actual device before finalizing sizes
2. **RATTA_RLE encoder** — needs implementation; decoder source in `supernotelib` is the reference
3. **Session persistence** — Playwright session storage format to be determined during implementation
4. **Snippet classifier** — heuristic vs. LLM-based detection; LLM likely needed for reliability
5. **Merge conflict handling** — if Braydon edits a page while Sam is generating an update, last-write wins; acceptable for now
6. **Caroline/Ingrid write-back** — post-discussion write-back uses same `write_requested` event as other agent writes

---

## Out of Scope

- Synthesized handwriting (vector stroke generation) — future consideration
- PDF delivery path — superseded by native `.note` format
- Other agents beyond Sam, Caroline, Ingrid
- Supernote devices other than Braydon's A5X
