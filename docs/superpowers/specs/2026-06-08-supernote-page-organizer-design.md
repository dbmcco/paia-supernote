# Supernote Cloud Page Organizer Design

## Goal

Build a web interface for organizing Supernote `.note` pages from the cloud. The interface should show a zoomable grid of pages in a notebook, expose native metadata such as stars, headings, keywords, and links, support page filtering, and eventually allow dragging pages within and across notebooks.

V1 will implement the safe foundation: read-only browsing plus metadata-aware page reorder within one notebook. Cross-notebook moves and custom visual markers are later phases.

## Design Premise

Supernote supports moving pages on the device, but this project is building that capability for the cloud side. The system will download `.note` files from Supernote Cloud, parse the notebook, rewrite page order and notebook metadata, upload a replacement file, and rely on normal Supernote Cloud/device sync for the device to receive the new notebook.

The current repository already has most of the low-level plumbing:

- `SupernoteUploader` downloads and uploads notebooks through Supernote Cloud.
- `supernotelib` parses and reconstructs `.note` files.
- `SupernoteReader` renders pages to images with `ImageConverter`.
- `quick_filing_service` demonstrates copy-then-remove page movement with a durable ledger.
- `note_page_ops` proves page append/remove mutations work, but is not sufficient for the final organizer because it clears `EXTERNALLINKINFO` and does not yet remap notebook-level title, keyword, or link collections.

## Non-Goals For V1

- No local Partner App sync writes.
- No device plugin dependency.
- No custom shape or sticker filing markers.
- No automatic cross-notebook move.
- No merge UI when the notebook changed after the snapshot loaded.
- No editing page handwriting or page content.

Custom shapes/stickers should remain a later extension point. V1 actionable metadata is native Supernote metadata already represented in the `.note` file, especially stars.

## Architecture

The organizer has three layers.

### Backend API

The backend API sits beside the existing `paia-supernote` service. It owns notebook listing, snapshot creation, cached page images, operation submission, and operation status.

Core responsibilities:

- list cloud notebooks available in the Supernote Note folder
- download a notebook and build an immutable snapshot
- serve page thumbnails and full-size page images
- expose page metadata for filtering and badges
- accept reorder operations by page ID
- reject writes when the base snapshot is stale
- call the rewrite engine and uploader

### Rewrite Engine

The rewrite engine performs whole-notebook rewrites. Its first write operation is reorder within a single notebook.

Core responsibilities:

- parse notebook bytes into page and metadata records
- reorder `notebook.pages`
- synchronize `notebook.metadata.pages`
- remap notebook-level titles/headings to new page positions
- remap notebook-level keywords to new page positions
- remap notebook-level links to new page positions
- preserve page IDs during reorder
- clear only metadata proven to become invalid after reconstruction
- reconstruct and re-parse the output before upload

### Web UI

The UI is an organizer, not an editor.

Core responsibilities:

- show notebook picker
- show page grid
- zoom from overview thumbnails to readable page previews
- show metadata badges for star, heading, keyword, and link
- filter pages by metadata type
- drag pages to create a pending reorder
- apply or undo pending reorder
- show conflict and validation errors clearly

## Data Model

Use page IDs as canonical identity. Page numbers are only the current projection of `page_order`.

```text
NotebookSnapshot
- notebook_name
- cloud_file_id
- cloud_update_time
- note_sha256
- page_order: [page_id]
- pages: {page_id -> PageRecord}
- metadata: NoteMetadataIndex
```

```text
PageRecord
- page_id
- page_index
- image_cache_key
- starred
- headings[]
- keywords[]
- outgoing_links[]
- incoming_links[]
- page_metadata
- content_hash
```

```text
NoteMetadataIndex
- titles/headings grouped by page_id
- keywords grouped by page_id
- five-star markers grouped by page_id
- links grouped by page_id
- raw notebook-level metadata for validation/debugging
```

```text
ReorderNotebookOperation
- operation_id
- notebook_name
- base_revision
- ordered_page_ids
- status
- error
- created_at
- updated_at
- completed_at
```

The UI and API submit page IDs, never page numbers, for write operations.

## Metadata Rules

### Stars

Native stars are exposed from page metadata and/or notebook metadata. V1 can display and filter by native stars. Reorder must preserve star state on each page.

### Headings/Titles

Supernote headings are represented as notebook-level title records with page association. Reorder must keep each heading attached to the same `page_id` and update its generated page number during reconstruction.

### Keywords

Keywords are notebook-level records with page association. Reorder must keep each keyword attached to the same `page_id` and update its generated page number during reconstruction.

### Links

Links are the highest-risk metadata. Reorder within one notebook should preserve link source and target identity when the target is page-ID based. The engine must validate that link records still point to valid pages after reconstruction.

If a notebook contains link metadata that can be parsed but not safely remapped, V1 should block write and show `unsupported_metadata_for_write`. A later version can downgrade this to a warning only after fixture tests and device validation prove safe round-tripping.

### Recognition Metadata

The existing code clears recognition-related offsets during page copy/append because reconstruction can make stale offsets invalid. V1 should take the same conservative posture for recognition fields unless a round-trip fixture proves preservation is safe.

## Write Workflow

Reorder within one notebook:

1. UI loads a `NotebookSnapshot`.
2. User drags pages into a new order.
3. UI submits `notebook_name`, `base_revision`, and `ordered_page_ids`.
4. Backend verifies the current cloud notebook revision still matches `base_revision`.
5. If stale, backend rejects the write with `conflict_requires_refresh`.
6. Rewrite engine reorders pages and metadata.
7. Rewrite engine reconstructs `.note` bytes.
8. Rewrite engine re-parses reconstructed bytes and validates output.
9. Backend uploads the replacement notebook through `SupernoteUploader`.
10. Backend records completion in the operation ledger.
11. UI refreshes to the new snapshot.

Writes are whole-notebook replacements. No write happens during drag. The user must explicitly apply the pending reorder.

## Conflict Handling

Every write is guarded by the base revision from the snapshot. The revision should include at least the notebook SHA-256 and, when available, the cloud update time or file ID/version.

If the cloud notebook changed after the UI loaded it:

- reject the operation
- keep the user's pending order client-side if possible
- ask the user to refresh
- do not attempt an automatic merge in V1

This prevents silent overwrites of edits made on the device or from another agent.

## UI Behavior

Main screen:

- left rail with notebook list and filters
- top bar with refresh, undo, apply reorder, zoom, and snapshot status
- responsive page grid
- page tiles with page image, current page number, and metadata badges
- zoom slider that changes tile size without changing document state

States:

- `clean`: snapshot loaded with no pending edits
- `pending_reorder`: page order differs from snapshot
- `applying`: backend is verifying, rewriting, validating, and uploading
- `conflict`: base revision is stale and refresh is required
- `unsupported_metadata`: notebook contains metadata V1 cannot safely preserve
- `error`: upload, parse, validation, or auth error

Filtering:

- all pages
- starred pages
- pages with headings
- pages with keywords
- pages with links

Filters do not change page order. Applying a reorder should submit the full ordered page ID list, not only visible pages.

## Cross-Notebook Moves Later

Cross-notebook moves should build on the same snapshot, page identity, metadata index, and ledger model.

Safe move flow:

1. download source and destination notebooks
2. verify both revisions
3. copy selected page objects and supported metadata into destination
4. upload destination
5. record `target_written_source_pending`
6. remove source pages
7. upload source
8. mark operation complete

If destination upload succeeds and source cleanup fails, retry must finish source cleanup without duplicating destination pages. The existing quick-filing ledger is the right pattern to reuse.

## Validation

The rewrite engine must prove every rewrite before upload.

Required validation:

- reconstructed bytes can be parsed by `supernotelib`
- page count matches expectation
- page ID set is unchanged for reorder
- page order matches `ordered_page_ids`
- title/header records point to valid pages
- keyword records point to valid pages
- link records point to valid pages or are blocked as unsupported
- star state is preserved per page
- preserved metadata counts match the source unless the operation explicitly moved or removed pages
- representative pages render after rewrite

V1 should block writes when validation cannot prove metadata preservation.

## Testing Strategy

Unit tests:

- build snapshot from fake notebook
- group titles, keywords, stars, and links by `page_id`
- reorder page objects by page ID
- remap title page associations
- remap keyword page associations
- remap link source page associations
- reject duplicate/missing/unknown page IDs
- reject unsupported metadata

Fixture tests:

- real `.note` with stars
- real `.note` with headings/titles
- real `.note` with keywords
- real `.note` with links
- real `.note` with combinations of metadata

Integration tests:

- reorder test notebook and re-parse output
- upload rewritten test notebook through Supernote Cloud
- conflict rejection when cloud revision changes

Manual validation:

- create a test notebook on device with headings, keywords, stars, and links
- download and inspect snapshot in UI
- reorder pages in UI
- upload rewritten notebook
- sync device
- open notebook on device and inspect metadata behavior

## Implementation Phases

### Phase 1: Read-Only Snapshot Browser

- Backend endpoint to list notebooks.
- Backend endpoint to load snapshot.
- Backend endpoint to serve rendered page images.
- UI notebook picker, grid, zoom, and metadata badges.
- Filters for star, heading, keyword, and link.

### Phase 2: Metadata-Aware Reorder

- Snapshot revision guard.
- Page-ID-based reorder operation.
- Metadata remapping for titles, keywords, stars, and supported links.
- Reconstruct, re-parse, validate, upload.
- Operation ledger for reorder attempts.
- UI pending reorder/apply/undo/conflict states.

### Phase 3: Cross-Notebook Move

- Multi-notebook snapshots.
- Destination picker/drop target.
- Copy-then-remove two-phase ledger flow.
- Link compatibility policy for moved pages.
- Device validation fixtures before enabling broadly.

### Phase 4: Custom Markers

- Generic marker model.
- Optional vision-based shape detection.
- Optional device/plugin-based element/sticker detection.
- User-configurable marker conventions.

## Open Risks

- Link metadata may require more than page-number remapping.
- Some notebook metadata may be omitted by `supernotelib` reconstruction.
- Recognition metadata preservation may not be safe after whole-notebook rewrite.
- Supernote Cloud conflict/update semantics may be less precise than a content hash.
- Device sync may treat rewritten files differently from native device edits.

These risks are why V1 starts with read-only browsing and then same-notebook reorder before cross-notebook moves.

## References

- Existing design: `docs/superpowers/specs/2026-04-29-quick-note-filing-design.md`
- Supernote support: Add, insert, copy, and delete note pages: https://support.supernote.com/en_US/organizing/1807651-add-insert-copy-and-delete-note-pages
- Supernote support: How to Move Files and Note Pages: https://support.supernote.com/en_US/organizing/move-files-
- Supernote plugin docs index: https://docs.supernote.com/llms.txt
- Supernote plugin APIs referenced: `getElements`, `getTitles`, `getKeyWords`, `searchFiveStars`, `generateNotePng`, `insertNotePage`, `removeNotePage`, `insertSticker`, `insertGeometry`
- `supernotelib` package used locally: https://pypi.org/project/supernotelib/
