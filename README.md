# supernote-py

**Bidirectional Python integration for [Supernote](https://supernote.com) e-ink devices.**

Reads `.note` files from Supernote Cloud, OCRs handwritten pages with vision models, writes rendered pages back to notebooks, and optionally files/organizes notes automatically.

## What It Does

| Capability | Module | Description |
|---|---|---|
| **Cloud sync** | `cloud_poller` | Polls the Supernote Cloud API for changed `.note` files — no Partner app or local USB sync required |
| **Page OCR** | `reader` | Extracts page images via `supernotelib` and transcribes handwriting with a vision LLM |
| **Page writing** | `writer`, `notebook_writer` | Renders text/agent content to RATTA_RLE bitmaps and appends or replaces pages in `.note` files |
| **Cloud upload** | `uploader` | Three-step upload flow (apply → S3 PUT → finish) with Playwright browser context auth |
| **Quick filing** | `quick_filing`, `quick_filing_service` | Detects starred pages, routes them to target notebooks, and files them via the cloud API |
| **File watching** | `watcher` | FSEvents-based local watcher as an alternative to cloud polling (requires Partner app sync) |
| **Enrichment** | `enrich_service`, `enrichment` | Normalizes raw OCR text into structured Markdown, extracts diagrams, and syncs to a knowledge store |
| **RATTA_RLE codec** | `ratta_rle` | Pure-Python encoder for the Supernote bitmap format — the inverse of `supernotelib`'s decoder |

## Architecture

```
Supernote Cloud  ──►  CloudPoller  ──►  IngestService  ──►  PageStateStore (SQLite)
                         │                                       │
                         ▼                                       ▼
                    SupernoteReader                        EnrichService
                    (vision OCR via                        (LLM normalization +
                     OpenAI-compatible API)                 knowledge store sync)
                                                                │
                    ── write path ◄────────────────────────────┘
                    SupernoteWriter  ──►  NotebookWriter  ──►  SupernoteUploader
                    (RATTA_RLE rendering)  (page ops)       (cloud upload)
```

## Quick Start

### Requirements

- Python 3.12+
- A Supernote account (for cloud features)
- An OpenAI-compatible vision API endpoint (for OCR)

### Install

```bash
git clone https://github.com/dbmcco/paia-supernote.git
cd paia-supernote
uv sync          # or: pip install -e ".[dev]"
```

### Configure

Create `~/.paia/supernote/config.toml`:

```toml
# Cloud polling
poll_interval = 60

# Vision / OCR backend (any OpenAI-compatible endpoint)
vision_backend = "openai_compatible"
zai_base_url = "https://api.openai.com/v1"    # or your compatible endpoint
zai_api_key = "sk-..."
zai_vision_model = "gpt-4o"
zai_text_model = "gpt-4o-mini"

# Notebooks to watch and sync
folio_sync_notebooks = ["My Notebook"]

# State database (tracks page revisions, OCR results, enrichment status)
state_db_path = "~/.paia/supernote/supernote-state.db"

# Quick filing (optional — auto-organize starred pages)
filing_enabled = false
filing_dry_run = true
filing_source_notebooks = ["Inbox"]
filing_destination_notebooks = ["Archive", "Projects"]
```

### Run

```bash
# Start all services (ingest + enrich + filing)
paia-supernote

# Or use the launchd service manager (macOS)
scripts/service.sh install
scripts/service.sh start
scripts/service.sh status
scripts/service.sh logs
```

## Standalone Modules

Several modules have **zero PAIA-specific dependencies** and can be used independently:

### RATTA_RLE Encoder

```python
from paia_supernote.ratta_rle import encode_image
from PIL import Image

img = Image.new("L", (1404, 1872), 255)  # blank A5X page
rle_bytes = encode_image(img)
```

### Supernote File Reader

```python
from paia_supernote.reader import SupernoteReader

reader = SupernoteReader()
pages = await reader.read_note(notebook_bytes)  # returns list of page images
text = await reader.transcribe_page(page_image)  # vision OCR
```

### Supernote File Writer

```python
from paia_supernote.writer import SupernoteWriter
from paia_supernote.notebook_writer import append_page_to_notebook

writer = SupernoteWriter()
page_rle = writer.render_page("assistant", "Hello from Python!")
updated_notebook = append_page_to_notebook(original_bytes, page_rle)
```

### Page Operations

```python
from paia_supernote.note_page_ops import copy_pages_to_end, remove_pages

# Copy pages 2-4 from source to end of target
merged = copy_pages_to_end(source_bytes, target_bytes, source_pages=[2, 3, 4])

# Remove pages 0 and 1 from a notebook
trimmed = remove_pages(notebook_bytes, page_indices=[0, 1])
```

### Cloud Poller

```python
from paia_supernote.cloud_poller import CloudPoller

async def on_change(name: str, data: bytes, update_time: int | None):
    print(f"Notebook {name} changed ({len(data)} bytes)")

poller = CloudPoller(
    on_note_changed=on_change,
    watched_notebooks=["My Notebook"],
)
await poller.start()
```

### Cloud Uploader

```python
from paia_supernote.uploader import SupernoteUploader

async with SupernoteUploader() as uploader:
    await uploader.upload("My Notebook", updated_notebook_bytes)
```

### Quick Filing

```python
from paia_supernote.quick_filing import StarDetector, FilingHeader

detector = StarDetector()
header = detector.detect_header(page_text)
# header.tags, header.title, header.bundle_index, etc.
```

### File Watcher (Local Sync)

```python
from paia_supernote.watcher import SupernoteWatcher

watcher = SupernoteWatcher(
    on_changed=lambda name, path: print(f"Changed: {name}"),
    sync_path=Path("~/Library/Containers/com.ratta.supernote/.../Note/").expanduser(),
)
watcher.start()  # blocking
```

## Dependencies

### Core

| Package | Purpose |
|---|---|
| `supernotelib` | Parse and manipulate Supernote `.note` binary format |
| `Pillow` | Image rendering for page bitmaps |
| `httpx` | Async HTTP client (cloud API, S3 uploads) |
| `playwright` | Browser automation for Supernote Cloud auth |
| `watchdog` | Filesystem event watching (local sync mode) |
| `pydantic` | Data validation |
| `structlog` | Structured logging |

### Optional / Ecosystem

These are only needed if you use the full PAIA integration (enrichment, events, task sync):

- `paia-agent-runtime` — model route registry and Linear tool wrapper
- `anthropic` — Anthropic API (used in snippet detection and task curation)
- A running [paia-events](https://github.com/dbmcco/paia-events) service for event pub/sub

The standalone modules above **do not require** these packages.

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run tests
pytest

# Run tests with coverage
pytest --cov=paia_supernote

# Lint
ruff check src/ tests/

# Font calibration (generate test pages for on-device review)
python scripts/calibrate_fonts.py --dry-run
```

## Supernote .note Format Notes

- Pages use a custom **RATTA_RLE** bitmap encoding (run-length with color codes)
- Page resolution is **1404×1872** at **226 DPI** (A5X)
- Recognition metadata (`RECOGNTEXT`, `RECOGNFILE`, `TOTALPATH`) must be zeroed when copying pages between notebooks
- The `FIVESTAR` metadata field marks starred/favorite pages
- `supernotelib` handles parsing; this project adds the **encoder** (inverse of their decoder)

## Supernote Cloud API

The upload flow is a three-step process calibrated against the live API:

1. **Apply**: `POST /api/file/upload/apply` → presigned S3 URL + auth headers
2. **Upload**: `PUT <s3_url>` → direct S3 upload with presigned credentials
3. **Finish**: `POST /api/file/upload/finish` → finalize with S3 object key

Authentication uses browser cookies obtained via Playwright (interactive login flow).

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `poll_interval` | `60` | Seconds between cloud polls |
| `vision_backend` | `"zai"` | Vision OCR backend identifier |
| `zai_base_url` | — | OpenAI-compatible API base URL |
| `zai_api_key` | — | API key for vision/text models |
| `zai_vision_model` | — | Model ID for handwriting OCR |
| `zai_text_model` | — | Model ID for text tasks |
| `state_db_path` | `~/.paia/supernote/supernote-state.db` | SQLite state database |
| `folio_sync_notebooks` | `[]` | Notebooks to watch and OCR |
| `filing_enabled` | `false` | Enable auto-filing of starred pages |
| `filing_dry_run` | `true` | Log filing decisions without executing |
| `filing_source_notebooks` | `[]` | Notebooks to file from |
| `filing_destination_notebooks` | `[]` | Target notebooks for filing |
| `filing_ledger_db_path` | `~/.paia/supernote/filing-ledger.db` | Filing audit database |
| `events_url` | `http://localhost:3511` | PAIA events service URL (optional) |

## License

MIT

---

## AGENTS.md

<!-- This section provides context for AI coding agents working on this repository. -->

### Project Overview

`supernote-py` (package: `paia_supernote`) is a Python service for bidirectional integration with Supernote e-ink tablets. It reads `.note` files from Supernote Cloud (or local filesystem), OCRs handwritten pages, and writes rendered content back to notebooks.

### Key Technical Details

- **Binary format**: Supernote `.note` files use `supernotelib` for parsing. We add a RATTA_RLE **encoder** (`ratta_rle.py`) — the inverse of supernotelib's decoder.
- **Page dimensions**: 1404×1872 at 226 DPI (A5X).
- **Cloud upload**: Three-step flow via Playwright browser auth → presigned S3 PUT → finish.
- **State management**: SQLite databases track page revisions (`page_state.py`) and filing operations (`filing_ledger.py`).
- **Model config**: `model_config.py` resolves model routes from `paia-agent-runtime` cognition registry. Standalone modules don't depend on this.

### Module Dependency Graph

```
Standalone (no PAIA deps):
  ratta_rle ← writer ← notebook_writer ← note_page_ops
  cloud_poller, uploader, watcher, reader (vision deps only)

PAIA-integrated:
  model_config → paia_agent_runtime
  events → paia-events HTTP API
  enrich_service → folio knowledge store
  task_curator, tasks_sync → Linear via paia_agent_runtime
  snippet_detector → Anthropic API + events
  user_board → events + all services
```

### Conventions

- `ABOUTME` docstring convention: first two lines describe identity and purpose.
- Async-first: all I/O is `async def` using `asyncio`.
- `structlog` for all logging — no bare `print()` in library code.
- Tests in `tests/` mirror `src/` structure. Run with `pytest -m "not integration"`.
- Config loaded from `~/.paia/supernote/config.toml` via `tomllib`.

### Common Tasks

- **Add a new page operation**: Implement in `note_page_ops.py`, add tests in `test_note_page_ops.py`. Zero recognition metadata on all moved pages.
- **Add a new cloud feature**: Auth flows go through `uploader.py`. API base is configurable.
- **Change model routing**: Edit `model_config.py`. Routes resolve from the central registry; don't hardcode model IDs.
- **Add filing rules**: Edit `quick_filing.py` for detection heuristics, `quick_filing_service.py` for the execution flow. Always support `dry_run` mode.
