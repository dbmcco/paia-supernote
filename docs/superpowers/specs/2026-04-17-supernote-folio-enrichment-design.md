# Supernote Folio Enrichment Design

**Date:** 2026-04-17

## Goal

Make `paia-supernote` a durable always-on service that:

- runs under `launchd` rather than ad hoc terminal processes
- ingests Supernote page updates durably
- enriches each page into text plus renderable diagram data
- upserts one Folio object per Supernote page
- overwrites the Folio page object when the source page changes

## Scope

This design covers two connected concerns:

1. Durable service lifecycle for Supernote ingestion and enrichment
2. Folio page representation for text plus arbitrary diagrams

It does not add page image persistence, append-only page history in Folio, or a separate media artifact pipeline.

## Approved Architecture

The long-term shape is two `launchd`-managed services backed by one durable local SQLite state database.

- `com.paia.supernote.ingest`
- `com.paia.supernote.enrich`

`ingest` is responsible for polling Supernote Cloud, downloading changed notebooks, extracting page text with OCR, and updating durable per-page state.

`enrich` is responsible for reading dirty page rows, generating normalized page text plus diagram outputs, and upserting the corresponding Folio page object.

The SQLite database is the source of truth for local processing state. It replaces in-memory queues and ensures crash-safe restart behavior.

## Service Lifecycle

`paia-supernote` should ship the same operational pattern already used in `paia-meetings`:

- repo-local `scripts/service.sh`
- one `plist` per long-lived agent
- `RunAtLoad = true`
- `KeepAlive = true`
- dedicated stdout and stderr logs under `~/Library/Logs/paia-supernote/`

The service wrapper should manage both launch agents together for install, uninstall, start, stop, status, and log viewing.

This keeps the system durable across terminal closure, crashes, and machine reboot.

## Durable State Model

The local database should maintain one row per logical page keyed by `notebook + page`.

Each row should track at least:

- `notebook`
- `page`
- `source_revision`
- `raw_text`
- `ocr_model`
- `ocr_updated_at`
- `dirty_for_enrichment`
- `last_enriched_revision`
- `last_folio_object_id`
- `retry_count`
- `next_retry_at`
- `last_error`
- `last_error_stage`
- `updated_at`

The row is overwrite-oriented. If the same Supernote page changes again, the newest source revision replaces the pending payload for that page.

## Processing Flow

### Ingest

The ingest service should:

1. Poll Supernote Cloud for changed notebooks
2. Download the current `.note` payload
3. Enumerate notebook pages
4. Compute the current page revision marker from source metadata plus page content hash
5. OCR the page
6. Upsert the page row in SQLite

When a page revision changes, the ingest service marks that page dirty for enrichment and overwrites the pending source payload for that page.

### Enrich

The enrich service should:

1. Select the next dirty page row
2. Ask the model for:
   - normalized readable page text
   - structured diagram scene JSON
   - optional Mermaid source when the page maps cleanly
   - short diagram summary
   - extraction confidence
3. Re-check the page row before write
4. Drop stale work if the source revision changed during enrichment
5. Upsert the Folio object if the revision still matches
6. Mark `last_enriched_revision = source_revision`

The pipeline is revision-driven. Newer page revisions always win over older in-flight work.

## Model-Mediated Boundary

This feature should stay model-mediated.

Code is responsible for:

- polling
- downloading
- OCR invocation
- durable state writes
- retry bookkeeping
- Folio upsert execution

The model is responsible for:

- deciding how to normalize page text
- deciding whether a page has a meaningful diagram
- deciding whether Mermaid is a good fit
- producing the canonical structured diagram scene

The code should not accumulate handwritten rules such as "if page has boxes, emit Mermaid" or "if arrows exceed N, call it a flowchart." Those are model decisions.

## Folio Object Shape

Each Supernote page maps to one Folio object with a stable path:

`supernote/<notebook>/page-<page>`

The page object should be updated in place rather than duplicated.

Recommended object layout:

- `title`: `<Notebook> — page <n>`
- `path`: stable page path
- `object_type`: `supernote-page`
- `content`: normalized readable markdown for the page
- `properties.raw_text`: raw OCR output
- `properties.diagram`: renderable diagram bundle
- `properties.source`: notebook, page, revision, timestamps, model metadata

Folio is the durable latest-state mirror of the Supernote page, not a version history store.

## Diagram Representation

Mermaid is not the canonical storage format.

The canonical diagram payload should live in `properties.diagram` with this structure:

- `kind`: `"none" | "mermaid" | "scene"`
- `mermaid`: optional Mermaid source
- `scene`: canonical structured diagram JSON
- `summary`: short textual description
- `confidence`: extraction confidence
- `render_version`: schema version

The `scene` payload is the durable representation for arbitrary drawings. It should support:

- nodes
- edges
- groups or swimlanes
- labels
- freeform regions or strokes for sketch-like diagrams
- normalized layout coordinates

This allows simple flowcharts to remain portable through Mermaid while preserving more freeform diagrams that do not map cleanly to Mermaid.

## Folio Rendering Contract

Folio must be able to render any stored page diagram.

Rendering precedence:

1. If `properties.diagram.scene` exists, render it with a dedicated page diagram component
2. If Mermaid also exists, expose it as an alternate or exportable view
3. If only Mermaid exists, render Mermaid directly
4. If no usable diagram exists, render the page text only

If diagram rendering fails, the page must remain readable. A broken renderer should degrade to text plus inspectable raw diagram payload, not a blank page.

## Update Semantics

Supernote pages can change over time. The system should capture and overwrite those changes.

Rules:

- one Folio object per Supernote page
- stable Folio path per page
- overwrite the existing object when the source page revision changes
- resolve writes by stable page path, then update the existing Folio object id if present or create it if missing
- no append-only page version history in Folio
- do not persist page images in Folio for this feature

If a page changes multiple times before enrichment finishes, stale enrichment results are discarded and only the latest revision is written.

## Failure Handling

Failure should be isolated to the page level rather than the whole service.

- OCR failure on one page records an error and does not stop ingestion of other pages
- enrichment failure on one page records retry metadata and does not stop processing of other pages
- Folio write failure records retry metadata and leaves the page dirty

Transient failures should retry with backoff stored in SQLite. Retries for stale revisions should be skipped once a newer revision exists.

## Observability

The system should emit structured logs for:

- `page_seen`
- `ocr_succeeded`
- `ocr_failed`
- `enrich_started`
- `enrich_succeeded`
- `enrich_failed`
- `folio_upsert_succeeded`
- `folio_upsert_failed`

It should also expose a small local status command that reports:

- dirty page count
- OCR failure count
- enrichment failure count
- last successful ingest time
- last successful enrich time

Launchd logs are the first operational surface, but the SQLite state database remains the source of truth for actual page state.

## Validation

Implementation should verify behavior at four layers:

1. SQLite state tests
   - page upsert overwrites pending work by `notebook + page`
   - revision changes mark pages dirty
   - retry and stale-revision logic behave correctly
2. Enrichment tests
   - model output is persisted into `content`, `properties.raw_text`, and `properties.diagram`
   - stale in-flight revisions are dropped before Folio write
3. Folio integration tests
   - first write creates the page object
   - subsequent writes patch the same page object by stable path
4. Service lifecycle smoke tests
   - launchd install and status succeed
   - both services restart cleanly with preserved SQLite state

## Result

The resulting system is a durable two-stage Supernote pipeline:

- launchd keeps it running
- SQLite keeps it durable
- the model decides how each page should be represented
- Folio stores one latest-state page object
- Folio can render both conventional Mermaid diagrams and arbitrary hand-drawn page structures
