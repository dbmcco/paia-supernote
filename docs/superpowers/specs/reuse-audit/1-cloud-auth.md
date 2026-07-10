# Reuse Audit — Slice 1: Cloud I/O + Auth

**Scope:** `src/paia_supernote/uploader.py`, `src/paia_supernote/cloud_poller.py`
**Question:** Is the new `supernote` CLI genuinely a thin layer over existing primitives?
**Verdict:** Largely YES for cloud I/O primitives. Two real gaps: (a) no bytes-based upload, and (b) no pure read-only "auth status" check + a headless-interactive-login trap that the safe pipeline must explicitly avoid. Auth state DOES persist across CLI invocations, so per-call login is NOT required.

Note: `plan.md` / `progress.md` do not exist in this repo (not blocking; design spec read instead).

---

## 1. Confirmed reusable

### a) Download a notebook to bytes — USABLE AS-IS
- `SupernoteUploader.download_notebook(target_name: str) -> bytes` — `uploader.py:155`
- Returns raw `.note` bytes directly (good for the in-memory safe pipeline; no temp file needed on the read side).
- Auth self-recovery: catches `UploadAuthError` from the list lookup and calls `_restart_browser_session()` once, then retries (`uploader.py:174-177`). On a *genuinely* expired session the second attempt re-raises `UploadAuthError`, which the CLI can catch and translate to guidance. This is the clean, headless-friendly failure path.
- Test coverage exists: `tests/test_uploader.py:440` (restart-after-auth-error), `:481` (happy path), `:432/:513` (not-found / no-url).

### b) Upload bytes back — USABLE, but takes a PATH not bytes (see §2a)
- `SupernoteUploader.upload_notebook(notebook_path: str, target_name: str) -> bool` — `uploader.py:109`
- Full three-step safe-replace flow (conflict check → delete old → apply → S3 PUT → finish → settle), conflict/sync-in-progress handling, and `return True` on success. Reusable as-is *given a file path*.
- Internally self-checks auth: `await self._ensure_authenticated()` at `uploader.py:127`, and `_initiate_upload_with_recovery` re-auths on 401/403 (`uploader.py:428-431`). ⚠ This re-auth is **interactive** — see §4 (headless trap).

### c) List notebooks — USABLE, but private + no page count (see §2c)
- `SupernoteUploader._list_note_files() -> list[dict[str, Any]]` — `uploader.py:210`
- Returns `userFileVOList` entries for the Note folder (`NOTE_FOLDER_ID` at `uploader.py:52`). Each entry exposes `id`, `fileName`, `isFolder`, `updateTime`, `size`.
- `CloudPoller._list_notes()` (`cloud_poller.py:164`) wraps the *same* `/api/file/list/query` call and additionally converts 401/403 into a degraded-health signal + guidance string. The CLI's `ls`/`auth status` can model on this error handling.

### d) Check / refresh auth — usable, but `_ensure_authenticated` is NOT a pure check (see §2b)
- `SupernoteUploader._ensure_authenticated() -> None` — `uploader.py:255`. Navigates to the cloud home, inspects HTTP status + URL (`uploader.py:264-271`), and calls `_interactive_reauth()` if expired (`uploader.py:272`).
- `_refresh_csrf_token()` — `uploader.py:276` (also auto-called from `start()`). `_api_call` auto-refreshes CSRF once on any 403 (`uploader.py:332-337`). CSRF/XSRF token is read from the cookie inside the browser context (`uploader.py:313-318`).

### e) Interactive re-auth — usable, requires `headless=False`
- `SupernoteUploader._interactive_reauth() -> None` — `uploader.py:286`. Opens the login route and `wait_for_function` polls the hash for up to **300 s** (`uploader.py:293-295`), then persists the session (`uploader.py:299-300`). Needs a *visible* browser (the user completes the form), i.e. `headless=False`.

**Net:** download, list, auth-check, interactive re-auth, and the upload primitive all exist and are exercised by tests. The CLI wraps, it does not re-implement cloud transport.

---

## 2. Gaps needing NEW code

### a) No bytes-based upload — upload takes a file PATH. (Severity: low — idiom exists)
`upload_notebook(notebook_path, ...)` reads from disk (`uploader.py:109`, e.g. `Path(file_path).read_bytes()` at `:393/:519`). The safe pipeline mutates in *memory* (`note_page_ops` / `notebook_writer.append_page_to_notebook` → bytes). The bridge already used by every existing caller is: write mutated bytes to a `tempfile.mkstemp(suffix=".note")`, then `await uploader.upload_notebook(tmp_path, name)`. This is a 3-line shim, not new capability — but it is the 6th copy of the idiom (see §3). Recommend: add one shared `upload_notebook_bytes(uploader, name, data)` helper for the CLI to call, so the safe pipeline never re-implements the tempfile dance.

### b) No pure read-only "auth status" check. (Severity: medium)
The spec's `supernote auth status` must report auth state **without** launching an interactive browser login. `_ensure_authenticated` (`uploader.py:255`) performs the check *and then auto-triggers `_interactive_reauth`* (`:272`) — there is no mode that returns a boolean and stops. The check logic is inline in its `do_check` closure (`uploader.py:259-271`). NEW thin code needed: a `is_authenticated() -> bool` (or a `reauth=False` flag on `_ensure_authenticated`) that does the navigate-and-inspect but returns status instead of opening login. This is the single genuine functional gap in this slice.

### c) `_list_note_files` is private and has no page count. (Severity: low)
It's underscore-prefixed (`uploader.py:210`) and the list entries carry no page count (only `size`/`updateTime`). The spec's `ls` wants "name, page count, modified". Page count requires parsing the notebook (download + parse) — heavyweight for a list — or drop page count / show `size`. Minor adaptation; the listing itself is reusable.

### d) `auth login` needs `headless=False` wiring. (Severity: none — reuse target exists)
The pattern to force a visible browser for login already exists as `_run_login()` in `main.py:959-975` (`SupernoteUploader(headless=False)` at `:964`, `start()`→`_ensure_authenticated()`→print session path→`stop()`). See §3.

---

## 3. Duplication risk

### REUSE: wrap `_run_login()` for `supernote auth login` — do NOT build new.
`main.py:959-975` is already the exact `auth login` implementation (visible browser → ensure-auth → persist session → report path → stop). The design spec maps `auth login` to `uploader._ensure_authenticated`, but the correct reuse target is the **full lifecycle wrapper** `_run_login`, which is the only place that sets `headless=False` and prints the saved session path. The CLI should call/factor this (e.g. move `_run_login` into a shared module both the old and new entry points import), not reimplement it. The spec's `paia-supernote login` subcommand (`main.py` arg at `:954`) and the poller's own hint ("Run 'paia-supernote login'" at `cloud_poller.py:187`) confirm login is already a command concept.

### REUSE: the download→mutate→tempfile→upload idiom — do NOT add a 6th ad-hoc copy.
The exact sequence the safe pipeline needs is already implemented in five places:
- `tasks_sync.py:99-118` (download tasks.note → append → tempfile → upload)
- `main.py:776-783` (`_replace_pages_with_uploader`: NamedTemporaryFile → upload)
- `main.py:834-849` (download Quick.note → mutate → tempfile → upload)
- `task_curator.py:97-104` (tempfile → upload)
- `quick_filing_service.py:92-113, 211-230` (mutate → tempfile → upload)

The CLI's write pipeline should extract ONE shared helper (download bytes, mutate, stage to temp, upload targets-before-source) rather than copy the pattern again. This is the highest-value consolidation in the slice.

---

## 4. Sync/async & lifecycle

### All primitives are async; CLI must run an event loop
Every uploader method is `async` and the object requires a live Playwright browser. The one-shot pattern already in the repo is `asyncio.run(coro)` (`main.py:1018` for login, `:1022` for organizer). The threaded-server bridge `asyncio.run_coroutine_threadsafe(...).result()` (`main.py:986`) is for the long-lived organizer and is NOT the right shape for a CLI. **CLI lifecycle = `asyncio.run(one_shot(uploader))` where `one_shot` does `await uploader.start()` → work → `await uploader.stop()` in a `try/finally`.**

### Auth state IS persisted across invocations (NOT a showstopper)
- Persisted to `SESSION_FILE = ~/.paia/supernote/session.json` (`uploader.py:48`) as a Playwright `storage_state` (cookies + localStorage, including `XSRF-TOKEN`).
- **Restore on start:** `start()` does `browser.new_context(storage_state=str(SESSION_FILE))` when the file exists (`uploader.py:73-76`).
- **Persist on stop:** `stop()` writes `context.storage_state(path=SESSION_FILE)` (`uploader.py:87-88`), and `_interactive_reauth` also persists after a successful login (`uploader.py:299-300`).
- ✅ Therefore a CLI invocation with a still-valid session requires NO login. Login is only needed when the session genuinely expires (days/weeks), surfaced via `UploadAuthError` / `403`. This satisfies the spec's "not every call" requirement.

### Start/stop lifecycle a CLI must manage
1. `uploader = SupernoteUploader(headless=True)` (`uploader.py:60`) — headless for all one-shot commands; `headless=False` ONLY for `auth login`.
2. `await uploader.start()` (`uploader.py:68`): launches Chromium + restores `session.json` + runs `_refresh_csrf_token()` which **navigates to the cloud home (`wait_until="networkidle"`)** — i.e. a network round-trip and multi-hundred-ms browser launch on EVERY CLI call.
3. Do the work (download/list/upload), each call serialized through the cross-process fcntl lock `_cloud_api_lock()` at `~/.paia/supernote/cloud-api.lock` (`uploader.py:49`, `:341-349`). This is GOOD (prevents races with the running daemon) but means a CLI call blocks behind the daemon if it holds the lock.
4. `await uploader.stop()` (`uploader.py:83`) in `finally`: persists session, closes browser/playwright. `stop()` is safely idempotent (every close wrapped in try/except, `:84-100`).

### ⚠ THE HEADLESS INTERACTIVE-LOGIN TRAP (must be designed around — this is the "403 trap")
There is an asymmetry between download and upload auth handling:
- **Download** (`uploader.py:174-177`): on `UploadAuthError` → `_restart_browser_session()` (restores the *same* `session.json`) once, then re-raises on a genuine expiry. Clean for headless — the CLI catches `UploadAuthError` and prints guidance. ✅
- **Upload** (`uploader.py:127` → `_ensure_authenticated`; `:428-431` → `_interactive_reauth`): on expiry it auto-launches `_interactive_reauth()`, which opens the login page and **blocks up to 300 s** (`uploader.py:293-295`) waiting for a human. In a **headless** one-shot CLI the user cannot complete the form → the command **hangs for 5 minutes** instead of failing fast.

This is exactly the "403 trap" the spec calls out. For the CLI's write pipeline to behave as designed ("prints actionable guidance … No notes were changed" instead of hanging), the CLI must run write commands **headless and must NOT let the auto-`_interactive_reauth` path run** during one-shot execution. Concretely, the safe pipeline should wrap uploads so that `UploadAuthError`/`403` is caught and reported with the `supernote auth login` recovery command, rather than relying on the uploader's built-in interactive retry. Today there is no "report-don't-reauth" switch on `_ensure_authenticated` (this is gap §2b again). This is the most important lifecycle design point for the implementer.

---

## Summary for the implementation planner
- Reuse `download_notebook` (bytes) as-is; it fails cleanly in headless.
- Reuse `upload_notebook` via a shared **bytes→tempfile→upload** helper (close gap §2a, consolidate §3 idiom).
- Reuse `_run_login` (`main.py:959`) as `auth login` with `headless=False`.
- Add ONE new primitive: a pure `is_authenticated() -> bool` (gap §2b) for `auth status`.
- Lifecycle: `asyncio.run(start→work→stop)`; session persists in `~/.paia/supernote/session.json`, so no per-call login.
- Hard requirement: prevent the headless `_interactive_reauth` 300 s hang during one-shot write commands — catch auth errors and emit guidance instead.
- Expect a Chromium launch + one network navigation per CLI invocation (latency note, not a correctness issue).
