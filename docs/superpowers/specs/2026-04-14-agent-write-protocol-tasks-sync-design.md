# Agent Write Protocol + tasks.note Lane Sync — Design Spec
*2026-04-14*

## Problem

Agents have no defined contract for writing to Supernote notebooks. There is also no mechanism to keep the Supernote device in sync with the paia-work task board — focus, inbox, orbit, and parking lanes exist in paia-work but are invisible on the device.

## Solution

Two related additions to paia-supernote:

1. **Agent write protocol** — a documented event contract for how any agent triggers a notebook write
2. **TasksSync** — an internal component that polls paia-work and keeps a dedicated `tasks.note` notebook in sync with all four task lanes, one page per lane

## Out of Scope

- Agents do not decide *which* notebook to write to at runtime — routing is fixed in config (`agent_mappings`)
- tasks.note is not editable by agents — it is owned entirely by TasksSync
- No support for reordering or deleting existing pages — writes are append-only for agent notes; tasks.note is always fully rebuilt from scratch
- No paia-work API design changes — TasksSync consumes whatever paia-work exposes for lane queries

## Agent Write Protocol

### Event schema

Any agent writes to a notebook by publishing a `supernote.write_requested` event to paia-events:

```json
{
  "agent": "Sam",
  "notebook": "Quick",
  "content": "...",
  "mode": "append"
}
```

Fields:
- `agent`: must match a key in `agent_mappings` config
- `notebook`: target notebook stem (e.g. `"Quick"`, not `"Quick.note"`)
- `content`: plain text to render — newlines preserved, markdown not supported
- `mode`: always `"append"` for agent writes

### Notebook routing

Routing is defined in `agent_mappings` in `config.toml` (or `DEFAULT_CONFIG` in `main.py`):

```toml
[agents.Sam]
font = "Bradley Hand"
notebook = "Quick"

[agents.Caroline]
font = "Noteworthy"
notebook = "LFW"

[agents.Ingrid]
font = "Chalkduster"
notebook = "Synth"
```

paia-supernote looks up agent → notebook and font from this config at write time.

### Write execution

`_handle_write_request` in `main.py`:
1. Validates agent exists in `agent_mappings`
2. Downloads current notebook from cloud
3. Renders content as RATTA_RLE via `SupernoteWriter.render_page(agent, content)`
4. Appends page via `append_page_to_notebook`
5. Uploads, replacing the existing cloud file (delete-first strategy)

### When agents write

Agents decide independently. The convention:
- Write in response to a transcription or snippet from the agent's assigned notebook
- Write when delivering a substantive response, not for acknowledgements
- Write at most once per detected change (no write loops)

## tasks.note Lane Sync

### Structure

`tasks.note` is a dedicated notebook with exactly 4 pages, one per paia-work lane:

| Page | Lane | Content |
|------|------|---------|
| 1 | Focus | Active, high-priority tasks |
| 2 | Inbox | New, untriaged tasks |
| 3 | Orbit | Watching, not active |
| 4 | Parking | Someday / maybe |

### TasksSync component

A new `TasksSync` class in `tasks_sync.py` runs as a background asyncio task alongside `CloudPoller`. It:

1. Polls paia-work on a configurable interval (default: same as `poll_interval`)
2. Fetches all four lanes in a single query (or four queries if the API requires it)
3. Computes a hash of each lane's task list
4. If any hash changed since last render, rebuilds all 4 pages and uploads `tasks.note`

Lane change detection uses MD5 of the serialized task list per lane. A change to any lane triggers a full rebuild (all 4 pages) to keep page numbers stable.

### Render format

Tasks pages use a clean structured layout, not a handwriting font. `SupernoteWriter.render_page` gets a new `content_type="tasks"` mode:

- Lane name as a bold header at top
- Tasks as a checklist: `□ Task title  [task_id]`
- Completed tasks shown as: `☑ Task title  [task_id]`
- Task ID rendered in small text at end of line for read-back matching
- Monospace or clean sans-serif font (not agent personality fonts)

### Bi-directional sync

**Write direction** (paia-work → tasks.note): TasksSync rebuilds on lane change.

**Read direction** (tasks.note → paia-work): The cloud poller already detects changes to `tasks.note` (added to `WATCHED_NOTEBOOKS`). The reader fires `supernote.checkbox_completed` events when a □ becomes ☑. A new handler in `main.py` intercepts these events for `tasks.note` specifically, parses the task ID from the transcribed text, and calls paia-work to mark the task done.

### paia-work API contract (assumed)

TasksSync requires paia-work to expose:
- `GET /v1/tasks?lane=<focus|inbox|orbit|parking>` — returns task list for a lane
- `PATCH /v1/tasks/<id>` with `{"status": "done"}` — marks a task complete

If the actual paia-work API differs, TasksSync adapts at implementation time.

## Success Criteria

- An agent can write to its notebook by publishing a single event — no other configuration needed
- `tasks.note` on the device shows current paia-work lanes within two poll cycles of a change
- Checking off a task on the device marks it done in paia-work within one poll cycle
- No agent can write to `tasks.note` (it is owned by TasksSync)
- All four tasks pages open correctly on the device (no immediate close)

## Key Constraints

- Append-only for agent writes — no page deletion or reordering
- tasks.note is always fully rebuilt on any lane change — no partial page updates
- Write path goes through the existing delete-first upload strategy
- Recognition metadata must be zeroed on all pages before reconstruct (existing fix in `notebook_writer.py`)
