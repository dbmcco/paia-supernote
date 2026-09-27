#!/usr/bin/env bash
set -euo pipefail

wg add 'supernote-organizer:snapshot-model: Build page-id snapshot model' --description '```wg-contract
touch-set: [src/paia_supernote/note_snapshot.py, tests/test_note_snapshot.py]
verify: uv run pytest tests/test_note_snapshot.py -q
model-route: implementation
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, datadrift, yagnidrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:snapshot-model --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:snapshot-model --write-log --create-followups
```

Create the read-only snapshot model and metadata extractor for Supernote notebooks. Start with tests that express the page_id-based contract, then implement the minimal production code.

Acceptance:
- Tests fail first for missing snapshot model.
- NotebookSnapshot can be built from fake notebook objects and/or .note bytes.
- Page records are keyed by page_id and include page index, page metadata, star state, headings, keywords, links, content hash, and image dimensions where available.
- No notebook bytes are mutated.
'
wg add 'supernote-organizer:image-cache: Add organizer image cache' --description '```wg-contract
touch-set: [src/paia_supernote/organizer_images.py, tests/test_organizer_images.py]
verify: uv run pytest tests/test_organizer_images.py -q
model-route: implementation
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, datadrift, yagnidrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:image-cache --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:image-cache --write-log --create-followups
```

Add page image rendering/cache primitives for the organizer grid without introducing cloud dependencies into unit tests.

Acceptance:
- Image cache tests fail first for missing cache behavior.
- Rendered page images are cached by notebook revision, page_id, and scale.
- Existing SupernoteReader rendering code is reused where practical.
- Focused tests pass without cloud access.
' --blocked-by supernote-organizer:snapshot-model
wg add 'supernote-organizer:backend-api: Create organizer backend API' --description '```wg-contract
touch-set: [src/paia_supernote/organizer_api.py, src/paia_supernote/main.py, pyproject.toml, tests/test_organizer_api.py]
verify: uv run pytest tests/test_organizer_api.py -q
model-route: implementation
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, datadrift, yagnidrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:backend-api --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:backend-api --write-log --create-followups
```

Create the organizer backend API with dependency injection around uploader, snapshot builder, and image cache.

Acceptance:
- API contract tests fail first.
- Backend exposes notebook list, snapshot metadata, and image endpoints through a lightweight local app surface.
- Endpoints are injectable/testable with fake uploader and fake snapshot builder.
- No live Supernote Cloud calls occur in unit tests.
' --blocked-by supernote-organizer:snapshot-model --blocked-by supernote-organizer:image-cache
wg add 'supernote-organizer:web-ui: Build read-only organizer UI' --description '```wg-contract
touch-set: [src/paia_supernote/organizer_ui.py, src/paia_supernote/static/, tests/test_organizer_ui.py]
verify: uv run pytest tests/test_organizer_ui.py -q
model-route: implementation
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, uxdrift, yagnidrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:web-ui --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:web-ui --write-log --create-followups
```

Build the first read-only web UI for the organizer grid using the backend API. Keep styling compact and utility-focused.

Acceptance:
- UI is available as a local web surface launched from the backend or CLI.
- Grid, zoom, metadata badges, and filters are present.
- No in-app explanatory marketing copy is used.
- Basic UX smoke verification is captured.
' --blocked-by supernote-organizer:backend-api
wg add 'supernote-organizer:reorder-engine: Implement metadata-aware reorder groundwork' --description '```wg-contract
touch-set: [src/paia_supernote/note_reorder.py, tests/test_note_reorder.py]
verify: uv run pytest tests/test_note_reorder.py -q
model-route: implementation
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, datadrift, yagnidrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:reorder-engine --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:reorder-engine --write-log --create-followups
```

Implement the local reorder engine groundwork behind tests. Keep upload/apply wiring out of this task.

Acceptance:
- Reorder tests fail first.
- Reorder accepts full ordered page_id list and rejects missing, duplicate, or unknown page IDs.
- Page order and metadata.page order are synchronized.
- Unsupported link metadata blocks write behavior rather than dropping data.
- No upload is performed by this task.
' --blocked-by supernote-organizer:snapshot-model
wg add 'supernote-organizer:final-verification: Verify organizer slice' --description '```wg-contract
touch-set: [src/paia_supernote/, tests/]
verify: uv run pytest tests/test_note_snapshot.py tests/test_organizer_images.py tests/test_organizer_api.py tests/test_organizer_ui.py tests/test_note_reorder.py tests/test_note_page_ops.py -q
model-route: review
```

```specdrift
spec: /Users/braydon/projects/experiments/paia-supernote/docs/superpowers/specs/2026-06-08-build-the-supernote-cloud-page-organizer-with-speedrift-and-test-spec.md
check: does this task remain consistent with the end-state contract?
```

```speedrift
modules: [coredrift, specdrift, uxdrift, fixdrift]
pre-task: ./.workgraph/drifts check --task supernote-organizer:final-verification --write-log --create-followups
post-task: ./.workgraph/drifts check --task supernote-organizer:final-verification --write-log --create-followups
```

Run final verification for the selected organizer slice and prepare the handoff.

Acceptance:
- Focused organizer tests pass.
- Relevant existing regression tests pass.
- Post-task drift checks have run and advisory findings are tracked.
- Unrelated pre-existing dirty files are not reverted or silently included.
' --blocked-by supernote-organizer:backend-api --blocked-by supernote-organizer:web-ui --blocked-by supernote-organizer:reorder-engine
