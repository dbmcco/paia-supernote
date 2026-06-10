# Quick Note Reorganization Design

## Goal

Reorganize `Quick.note` from a mixed archive into a lightweight inbox for active scratch work. Older pages should be reviewed, moved to better domain notebooks, tagged and linked in Folio, and recorded in an audit ledger. The target notebooks must receive only the original handwritten page.

## Operating Model

Supernote `.note` files are the canonical thinking-surface archive. A moved page should remain visually and materially the same handwritten artifact in its destination notebook.

Folio is the semantic layer. It carries OCR text, tags, backlinks, cross-domain meaning, summaries, and search metadata.

The filing ledger is the control layer. It records source page, destination, confidence, model evidence, review status, and completed operations.

## Notebook Roles

`Quick.note` is an inbox, not an archive. It should keep:

- one active or latest scratch page
- one generated `Quick Index / Recently Filed` page with native Supernote links to moved pages
- unresolved pages that still need human review

Primary destination notebooks:

- `Mgmt.note`: active work state, current priorities, stuck points, delegation, meeting prep, and operating cadence
- `PAIA.note`: PAIA system design, agents, Supernote, Folio, workgraph, model-mediated systems, and related product/system design
- `LFW.note`: LFW client, product, BD, legal, and commercial work
- `Synth.note`: Synth/Synthera thinking
- `Navicyte.note`: Navicyte thinking
- `(de)comp.note`: reusable decomposition/composition frameworks
- `Ideas.note`: coherent ideas without a clear domain home
- `Archive.note`: low-signal scraps when a non-destructive destination is needed

## Filing Rules

Each filed page has exactly one canonical destination `.note`.

The destination notebook receives only the original handwritten page. The system must not insert generated summaries, generated labels, or companion pages into target notebooks during filing.

Roughness is not a reason to leave a page in `Quick.note`. If the page belongs to a domain, it moves even when the thought is incomplete, exploratory, or partly illegible.

Cross-cutting meaning should be represented in Folio tags and links, not by duplicating the handwritten page across multiple `.note` files.

## Read-Only Review Pass

The first pass must be read-only.

1. Load existing OCR for all `Quick` pages from page state.
2. Render page images for pages with weak OCR, ambiguous destination, or important diagram structure.
3. Use the configured vision/text model to classify each page.
4. Produce a review ledger before any notebook mutation.
5. Wait for explicit approval before moving pages or regenerating the index page.

The review ledger should include:

- source notebook and page
- OCR excerpt
- suggested action: `move`, `keep_active`, `needs_review`, or `archive`
- suggested target notebook
- suggested Folio tags
- suggested Folio links or concepts
- confidence
- reason/evidence

## Classification Guidance

Use project/domain as the primary destination signal.

Examples:

- Speedrift, workgraph, agents, Supernote organizer, Folio, PAIA architecture -> `PAIA.note`
- current priorities, stuck points, work cleanup, delegation, meeting prep, what-is-next pages -> `Mgmt.note`
- reusable decomposition/composition concepts -> `(de)comp.note`
- decomposition of a specific PAIA problem -> `PAIA.note`, with Folio tag `thought/decomp`
- coherent idea with no stable domain -> `Ideas.note`

## Quick Index Page

`Quick.note` should contain a generated index page that points to recently filed pages and unresolved pages. This index is navigational, not canonical.

The index page must use native Supernote links to the moved page in the destination `.note` file when those links can be generated and validated safely.

The index should stay capped, typically 20-30 entries, so `Quick.note` does not become cluttered.

Example content:

```text
Recently filed from Quick

- PAIA p.42 - Speedrift harness
- Mgmt p.18 - Projects/focus to define
- (de)comp p.3 - Loops of work

Needs review
- Quick p.12 - unclear destination
```

The visible text should be plain and compact. The native link target is attached to the destination reference, not implemented as generated content in the target notebook.

## Native Link Requirement

Native Supernote links are required for the generated `Quick Index / Recently Filed` page.

This is a separate capability from moving the original page. The move operation should remain conservative and should not attempt to preserve or synthesize notebook-level metadata in the moved page unless proven safe.

Before enabling index regeneration against real notes, the implementation must validate native link creation on disposable fixture notebooks:

1. Create or copy a disposable source notebook and target notebook.
2. Move one original page into the target notebook.
3. Generate a `Quick Index` page with a native link to the moved page.
4. Upload both notebooks.
5. Confirm on Supernote after sync that tapping the index link opens the expected destination page.
6. Confirm a round-trip download preserves the link metadata.

If native cross-notebook link generation cannot be validated, the system must stop before mutating real `Quick.note` and report the blocker. It should not silently downgrade to plain text links.

## Safety Rules

- The audit pass is read-only.
- No page move happens without explicit human approval of the review ledger.
- Destination writes happen before source removal.
- `Quick.note` cleanup happens only after destination write succeeds.
- Page duplicates caused by partial failure must be recorded clearly in the ledger.
- Existing user-created pages must not be overwritten by a generated index unless the target page is known to be the managed index page.
- Generated index updates must be revision-guarded.
- Links must be validated on fixtures before real-note index generation.

## Folio Behavior

Folio should index both the original moved page and the filing metadata.

The destination page object should carry:

- original source notebook and page
- filing operation id
- target notebook and target page
- tags and concept links approved during review

The old `Quick` page should stop being treated as canonical after a completed move, but Folio should preserve enough provenance to explain where the page came from.

## Non-Goals

- No generated summaries in target notebooks.
- No duplicate canonical copies of a handwritten page.
- No automatic cross-domain copying.
- No automatic taxonomy expansion beyond the agreed destination notebooks.
- No native-link writes to real notebooks until fixture/device validation passes.
