# Supernote Cross-Note Page Move Design

## Goal

Add a cloud-side organizer action that moves one page from the currently open `.note` to a different `.note` by dragging the page tile onto a notebook in the left sidebar.

The moved page is appended to the end of the target note. The user remains in the source note after the move completes.

## User Behavior

- The page grid remains the primary current-note view.
- Sidebar notebook rows become drop targets for page tiles.
- The current notebook is not a valid drop target.
- Dropping a page tile on another notebook immediately starts a cloud move operation.
- Same-note drag reorder still behaves as it does now: drag within the grid, then click Apply.
- Cross-note moves do not use the same-note Apply button.
- Cross-note moves are disabled while the current note has an unapplied same-note reorder. The user must Apply or Undo the same-note reorder first.
- After a successful cross-note move, the source grid removes the moved page, renumbers the remaining pages, and shows a status such as `Moved page to Quick.`
- The UI does not navigate to the target notebook.
- There is no local Undo for a completed cross-note move. Moving the page back is a separate operation.

## Backend API

Add a move endpoint:

```text
POST /api/notebooks/{source_notebook}/pages/{page_id}/move
```

Payload:

```json
{
  "source_revision": "sha-or-cloud-revision",
  "target_notebook": "Quick"
}
```

Success response:

```json
{
  "ok": true,
  "source_notebook": "LFW",
  "target_notebook": "Quick",
  "page_id": "page-id",
  "source_revision": "new-source-revision",
  "target_revision": "new-target-revision"
}
```

Failure responses use the existing JSON error pattern. Domain failures should set `ok: false` and a stable `reason`.

## Move Workflow

1. UI submits source notebook, source revision, page ID, and target notebook.
2. Backend downloads the source notebook.
3. Backend builds a source snapshot and verifies `source_revision` still matches.
4. Backend validates the page ID exists in the source snapshot.
5. Backend rejects moves where source and target notebooks are the same.
6. Backend downloads the target notebook.
7. Backend appends a copy of the source page to the end of the target note.
8. Backend uploads the updated target note.
9. Backend removes the page from the source note.
10. Backend uploads the updated source note.
11. Backend returns success with new source and target revisions.
12. UI removes the page tile from the current grid, renumbers remaining pages, clears same-note reorder dirty state, and stays in the source note.

Target upload happens before source removal so a failure before the source upload does not lose the page.

## Metadata Policy

V1 cross-note moves preserve page visual content and page background/content bytes.

V1 cross-note moves clear metadata that is unsafe to carry between notebooks:

- recognition/OCR offset fields
- recognition status fields
- native star/filing marker metadata

Notebook-local headings, keywords, and links are not carried across notebooks in V1 unless they are contained directly on the copied page and already preserved by the low-level page copy routine. The backend must not attempt to remap notebook-level target metadata for cross-note moves in this phase.

This policy matches the existing conservative `copy_pages_to_end` and `remove_pages` helpers.

## Partial Failure Handling

The move is not truly atomic because it replaces two separate cloud files.

If target upload succeeds but source upload fails, the response must make the partial state explicit:

```json
{
  "ok": false,
  "reason": "partial_move_target_uploaded_source_failed",
  "target_notebook": "Quick",
  "source_notebook": "LFW",
  "page_id": "page-id",
  "error": "source upload error"
}
```

The UI should show a clear error that the page may now exist in both notes. It should not remove the page tile locally unless the backend reports full success.

If the source revision is stale, the backend returns a conflict and performs no upload.

If target download, page copy, or target upload fails, the source note remains untouched.

## UI Details

Sidebar links remain normal notebook navigation when clicked.

During a page drag:

- notebook rows other than the current notebook receive a drop affordance
- hovering a valid target highlights that row
- hovering the current notebook shows no valid-drop highlight
- if the current note has a pending same-note reorder, sidebar drop targets are inactive and the UI shows `Apply or undo the current reorder before moving pages to another note.`
- dropping outside a valid target leaves the current same-note drag behavior unchanged

During move:

- disable Apply and Undo controls
- show `Moving page to <target>...`
- prevent starting a second cross-note move until the first finishes

After success:

- remove the moved tile from the DOM
- update the local `originalOrder` baseline to the new source order
- renumber page labels and image alt text
- clear dirty state
- show `Moved page to <target>.`

After failure:

- keep the source grid unchanged
- restore controls
- show the returned error or reason

## Testing

Unit tests:

- page operation tests cover append-then-remove helpers used by the move.
- API tests cover success, stale source revision, same-target rejection, missing page rejection, and partial failure when source upload fails after target upload.
- Server route tests cover the new move endpoint and status mapping.
- UI render tests cover sidebar notebook drop target metadata and move endpoint wiring.

Integration smoke:

- start organizer on the tailnet high port
- verify `/organizer` returns 200
- verify rendered sidebar rows contain drop target notebook names
- do not run a destructive real cross-note move as an automated smoke without an explicit disposable fixture note

## Non-Goals

- No automatic navigation to the target note.
- No batch cross-note move queue.
- No cross-note metadata remapping for notebook-level headings, keywords, or links.
- No atomic two-file transaction guarantee.
- No automatic cleanup operation after a partial failure.
