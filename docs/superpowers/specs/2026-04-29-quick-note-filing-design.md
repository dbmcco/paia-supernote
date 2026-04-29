# Quick Note Filing Design

## Goal

Turn `Quick.note` into a real inbox: Braydon writes whole-page notes there, marks pages ready with Supernote's native star, and PAIA files those pages into the correct destination notebook while keeping an auditable record of each operation.

## Current Context

`paia-supernote` already watches Supernote Cloud, downloads changed notebooks, OCRs pages, stores durable page state, writes Folio page objects, and uploads rewritten notebooks. It can append generated pages and replace stable artifact notebooks such as `Walk.note` and `Meetings.note`.

It does not currently have a cloud API that moves an existing handwritten page from one notebook to another. Supernote's user-facing device UI supports moving pages within and across notebooks. The documented plugin APIs include useful page primitives such as star search, page insertion, and page removal, but those APIs appear device-side because they operate on local paths like `/storage/emulated/0/Note/demo.note`.

## User Convention

Braydon writes routable notes as whole pages in `Quick.note`.

The page header should be lightweight and handwritten-friendly:

```text
2026-04-29 #lfw #meeting 1/2
Gene King check-in
```

Single-page notes omit the bundle marker:

```text
2026-04-29 #navicyte #idea
Navi pre-seed positioning
```

The native Supernote star is the readiness marker. A page is not eligible to move unless it is starred. For bundles, every page in the bundle must be present and starred before any page moves.

## Routing Model

Routing is page-level, not section-level.

The processor reads OCR text and extracts:

- note date
- tags
- optional bundle marker such as `1/2`
- optional title line
- source notebook, page number, and source revision

Known tags map to configured destination notebooks:

- `#lfw` -> `LFW.note`
- `#synthyra` -> `Synth.note`
- `#navicyte` -> `Navicyte.note`
- `#idea` -> `Ideas.note`
- additional mappings live in configuration, not code branches

If routing is ambiguous, the page remains in `Quick.note` and the ledger records `needs_review`.

## Move Strategy

### V1: Cloud-Side Emulated Move

The first implementation should use the existing Mac/cloud integration.

For each ready route group:

1. Download the current `Quick.note`.
2. Download the current destination notebook.
3. Verify both revisions still match the revision used during detection.
4. Copy the source page objects into the destination notebook.
5. Upload the destination notebook.
6. Re-download or verify the destination changed as expected.
7. Remove the moved pages from `Quick.note`.
8. Upload `Quick.note`.
9. Record completion in the ledger.

The source page is never removed until the destination write has succeeded and the ledger has recorded that destination write.

If destination upload succeeds but Quick cleanup fails, the page remains in Quick. The ledger marks the operation `target_written_source_pending` so the next run can finish cleanup without duplicating the page.

### Research Lane: Device-Side Plugin Move

Investigate whether a Supernote plugin can perform the move more natively using the device-side page APIs and star detection. This is not required for v1.

The research output should answer:

- Can a plugin enumerate starred pages in `Quick.note`?
- Can it copy existing page content, not just insert blank/template pages?
- Can it insert into another notebook and remove from Quick atomically enough to trust?
- Can it communicate operation status back to PAIA?

If the plugin path is viable, it can replace the cloud-side emulated move later.

## Operation Ledger

Every detected or attempted move writes a durable operation record. This is system memory, not a duplicate note archive.

Fields:

- `operation_id`
- `created_at`
- `updated_at`
- `status`
- `source_notebook`
- `source_pages`
- `source_revision`
- `detected_header`
- `detected_tags`
- `bundle_key`
- `target_notebook`
- `target_insert_position`
- `target_revision_before`
- `target_revision_after`
- `quick_revision_after`
- `routing_reason`
- `confidence`
- `error`
- `completed_at`

Statuses:

- `detected`
- `ready`
- `needs_review`
- `target_written`
- `target_written_source_pending`
- `source_removed`
- `completed`
- `failed`

The ledger must be idempotent. A repeated scan of the same starred page and source revision should resume the existing operation rather than create a duplicate move.

## Safety Rules

- Star required for all moves.
- Known destination tag required for automatic moves.
- All pages in a bundle must be present and starred.
- Source revision and target revision must be checked immediately before writing.
- Destination write happens before source removal.
- A failed cleanup must never trigger a second destination append.
- `Walk.note` and `Meetings.note` remain generated artifacts and are not inputs to this filing workflow.
- Quick remains the only inbox source for this v1.

## Folio Behavior

Folio should index the filed destination content and the ledger metadata. It should not treat the old Quick page as canonical after a completed move.

After completion, Folio page state for the original Quick page should indicate that the page was filed and point to the operation id and destination notebook. The destination page object should include the same operation id so search can explain where the note came from.

## Testing Strategy

Unit tests:

- header/tag/bundle parsing
- starred-page eligibility
- tag-to-destination routing
- bundle completeness
- ledger idempotency and resume behavior
- page-copy/page-remove notebook mutations

Integration tests:

- target upload succeeds, Quick upload succeeds -> `completed`
- target upload succeeds, Quick upload fails -> `target_written_source_pending`
- stale source revision -> no write and `needs_review`
- repeated scan after partial success does not duplicate destination pages

Manual validation:

- Create a starred `Quick.note` page tagged `#lfw`.
- Run filing dry-run and inspect ledger.
- Run live filing against test notebooks.
- Confirm destination notebook contains the moved page and Quick no longer does.

## References

- Supernote support: moving pages within and across notebooks.
- Supernote support: native headings, keywords, and stars.
- Supernote plugin docs: `searchFiveStars`, `removeNotePage`, `insertNotePage`, element APIs.
- Supernote blog: current note files are large packages and sync conflicts are a known concern, which is why cloud-side rewriting needs revision checks and a ledger.
