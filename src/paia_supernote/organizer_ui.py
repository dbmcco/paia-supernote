from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote


def render_index(*, notebooks: list[dict[str, Any]], snapshot: dict[str, Any]) -> str:
    notebook_name = str(snapshot.get("notebook_name") or "")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(notebook_name)} - Supernote Organizer</title>
  <style>{_CSS}</style>
</head>
<body>
  <aside class="sidebar">
    <section>
      <h2>Notebooks</h2>
      <nav class="notebook-list">{_render_notebooks(notebooks, notebook_name)}</nav>
    </section>
    <section>
      <h2>Filters</h2>
      <label><input type="checkbox" data-filter="starred"> Starred</label>
      <label><input type="checkbox" data-filter="headings"> Headings</label>
      <label><input type="checkbox" data-filter="keywords"> Keywords</label>
      <label><input type="checkbox" data-filter="links"> Links</label>
    </section>
  </aside>
  <section class="workspace">
    <header class="toolbar">
      <h1>{escape(notebook_name)}</h1>
      <div class="toolbar-actions">
        <button type="button">Refresh</button>
        <button id="undo-order" type="button" disabled>Undo</button>
        <button id="apply-order" type="button" disabled>Apply</button>
        <label class="zoom">Zoom <input type="range" min="160" max="420" value="220"></label>
      </div>
    </header>
    <div id="organizer-status" class="status" role="status" aria-live="polite"></div>
    <main class="page-grid" data-notebook="{escape(notebook_name)}" data-revision="{escape(str(snapshot.get("revision") or ""))}">
      {_render_pages(notebook_name, snapshot, notebooks)}
    </main>
  </section>
  {_render_move_dialog(notebook_name, notebooks)}
  <script>{_JS}</script>
</body>
</html>
"""


def _render_notebooks(notebooks: list[dict[str, Any]], current: str) -> str:
    items = []
    for notebook in notebooks:
        name = str(notebook.get("name") or "")
        active = " active" if name == current else ""
        items.append(
            f'<a class="notebook{active}" href="/organizer?notebook={quote(name)}" data-drop-target="notebook" data-notebook-name="{escape(name)}">{escape(name)}</a>'
        )
    return "\n".join(items)


def _render_pages(
    notebook_name: str, snapshot: dict[str, Any], notebooks: list[dict[str, Any]]
) -> str:
    pages = snapshot.get("pages") or {}
    page_order = snapshot.get("page_order") or []
    revision = str(snapshot.get("revision") or "")
    tiles = []
    for index, page_id in enumerate(page_order):
        page = dict(pages.get(page_id) or {})
        page_number = int(page.get("page_index", index)) + 1
        starred = bool(page.get("starred"))
        heading_count = int(page.get("heading_count") or 0)
        keyword_count = int(page.get("keyword_count") or 0)
        link_count = int(page.get("outgoing_link_count") or 0) + int(
            page.get("incoming_link_count") or 0
        )
        badges = _render_badges(
            starred=starred,
            heading_count=heading_count,
            keyword_count=keyword_count,
            link_count=link_count,
        )
        image_src = (
            f"/api/notebooks/{quote(notebook_name)}/pages/{quote(str(page_id))}"
            f"/image?scale=0.25&amp;revision={quote(revision)}"
        )
        tiles.append(
            f"""<article class="page-tile" draggable="true" data-page-id="{escape(str(page_id))}" data-position="{index + 1}" data-starred="{str(starred).lower()}" data-headings="{heading_count}" data-keywords="{keyword_count}" data-links="{link_count}">
  <div class="page-meta"><button class="drag-handle" type="button" aria-label="Drag page" title="Drag page">::::</button><span class="page-number">Page {page_number}</span>{badges}<button class="move-button" type="button" aria-label="Move page to another note" title="Move page to another note">Move</button></div>
  <img src="{image_src}" alt="Page {page_number}" loading="lazy" decoding="async">
</article>"""
        )
    return "\n".join(tiles)


def _render_badges(
    *,
    starred: bool,
    heading_count: int,
    keyword_count: int,
    link_count: int,
) -> str:
    badges = []
    if starred:
        badges.append("Star")
    if heading_count:
        badges.append("Heading")
    if keyword_count:
        badges.append("Keyword")
    if link_count:
        badges.append("Link")
    return "".join(f'<span class="badge">{badge}</span>' for badge in badges)


def _move_targets(current: str, notebooks: list[dict[str, Any]]) -> list[str]:
    return [
        name
        for name in (str(notebook.get("name") or "") for notebook in notebooks)
        if name and name != current
    ]


def _render_move_dialog(current: str, notebooks: list[dict[str, Any]]) -> str:
    targets = _move_targets(current, notebooks)
    buttons = "".join(
        (
            '<button type="button" data-move-target="notebook" '
            f'data-move-notebook="{escape(name)}" '
            f'data-move-search="{escape(name.lower())}">Move to {escape(name)}</button>'
        )
        for name in targets
    )
    empty = '<div class="move-empty" hidden>No matching notes</div>'
    return (
        '<div class="move-dialog" role="dialog" aria-modal="true" '
        'aria-labelledby="move-dialog-title" hidden>'
        '<div class="move-panel">'
        '<div class="move-header">'
        '<h2 id="move-dialog-title">Move page</h2>'
        '<button class="move-close" type="button" aria-label="Close move dialog">Close</button>'
        "</div>"
        '<input id="move-search" class="move-search" type="search" '
        'placeholder="Find note" autocomplete="off">'
        f'<div class="move-target-list">{buttons}{empty}</div>'
        "</div>"
        "</div>"
    )


_CSS = """
:root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; grid-template-columns: 240px 1fr; color: #1f2933; background: #f6f7f8; }
.sidebar { border-right: 1px solid #d6d9dd; background: #ffffff; padding: 16px; display: flex; flex-direction: column; gap: 22px; }
h1, h2 { margin: 0; font-weight: 650; letter-spacing: 0; }
h1 { font-size: 20px; }
h2 { font-size: 12px; text-transform: uppercase; color: #69717c; margin-bottom: 10px; }
.notebook-list, .sidebar section { display: flex; flex-direction: column; gap: 8px; }
.notebook { color: #26313d; text-decoration: none; padding: 7px 8px; border-radius: 6px; }
.notebook.active { background: #e8edf2; }
.notebook.drop-hover { outline: 2px solid #6b8fab; outline-offset: 2px; background: #eef5f8; }
label { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.workspace { min-width: 0; display: flex; flex-direction: column; }
.toolbar { height: 64px; border-bottom: 1px solid #d6d9dd; background: #ffffff; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 18px; }
.toolbar-actions { display: flex; align-items: center; gap: 10px; }
button { border: 1px solid #b8c0c9; background: #ffffff; color: #1f2933; min-height: 34px; padding: 0 11px; border-radius: 6px; font: inherit; }
button:disabled { color: #8b949e; background: #f0f2f4; }
.zoom { min-width: 190px; }
.status { min-height: 28px; padding: 6px 18px 0; font-size: 13px; color: #43505c; }
.status.error { color: #9f2d20; }
.status.success { color: #266947; }
.page-grid { --tile-width: 220px; padding: 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--tile-width), 1fr)); gap: 14px; align-items: start; overflow: auto; }
.page-tile { position: relative; background: #ffffff; border: 1px solid #d6d9dd; border-radius: 8px; min-width: 0; overflow: visible; cursor: grab; }
.page-tile:active { cursor: grabbing; }
.page-tile.dragging { opacity: .55; border-color: #6b8fab; }
.page-meta { height: 34px; padding: 0 9px; display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #e2e5e8; font-size: 13px; white-space: nowrap; background: #ffffff; border-radius: 8px 8px 0 0; }
.drag-handle { min-height: 24px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; cursor: grab; touch-action: none; color: #5c6874; background: #f8f9fa; border-color: #ccd3da; font-size: 11px; line-height: 1; }
.drag-handle:active { cursor: grabbing; }
.badge { border: 1px solid #bac4cc; border-radius: 999px; padding: 2px 6px; font-size: 11px; color: #43505c; }
.move-button { min-height: 24px; margin-left: auto; padding: 0 8px; font-size: 12px; cursor: auto; }
.move-dialog { position: fixed; inset: 0; z-index: 60; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(31, 41, 51, .28); }
.move-dialog[hidden] { display: none; }
.move-panel { width: min(420px, 100%); max-height: min(620px, calc(100vh - 48px)); display: flex; flex-direction: column; border: 1px solid #b8c0c9; border-radius: 8px; background: #ffffff; box-shadow: 0 18px 40px rgba(31, 41, 51, .22); overflow: hidden; }
.move-header { min-height: 48px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 12px 0 14px; border-bottom: 1px solid #e2e5e8; }
.move-header h2 { margin: 0; text-transform: none; color: #1f2933; font-size: 16px; }
.move-close { min-height: 30px; padding: 0 9px; }
.move-search { margin: 12px; min-height: 36px; border: 1px solid #b8c0c9; border-radius: 6px; padding: 0 10px; font: inherit; }
.move-target-list { overflow: auto; padding: 0 8px 8px; }
.move-target-list button { width: 100%; justify-content: flex-start; text-align: left; border: 0; border-radius: 4px; min-height: 34px; background: transparent; }
.move-target-list button:hover { background: #eef5f8; }
.move-empty { padding: 12px 8px; color: #69717c; font-size: 13px; }
.page-tile img { display: block; width: 100%; aspect-ratio: 1404 / 1872; object-fit: contain; background: #f9fafb; border-radius: 0 0 8px 8px; }
@media (max-width: 760px) {
  body { grid-template-columns: 1fr; }
  .sidebar { border-right: 0; border-bottom: 1px solid #d6d9dd; }
  .toolbar { height: auto; min-height: 64px; align-items: flex-start; flex-direction: column; padding: 12px; }
  .toolbar-actions { flex-wrap: wrap; }
}
"""


_JS = """
const grid = document.querySelector('.page-grid');
const zoom = document.querySelector('.zoom input');
const undoOrder = document.querySelector('#undo-order');
const applyOrder = document.querySelector('#apply-order');
const statusEl = document.querySelector('#organizer-status');
let originalOrder = currentOrder();
let draggedTile = null;
let pointerDragging = false;
let movingPage = false;
let dragStartedDirty = false;
let dragSlots = [];
let pointerDropTarget = null;
let activeMoveTile = null;

zoom?.addEventListener('input', () => {
  grid?.style.setProperty('--tile-width', `${zoom.value}px`);
});

function currentOrder() {
  return [...document.querySelectorAll('.page-tile')].map((tile) => tile.dataset.pageId);
}

function orderedTiles() {
  return grid ? [...grid.querySelectorAll('.page-tile')] : [];
}

function visibleOrderTiles() {
  return orderedTiles().filter((tile) => !tile.hidden);
}

function setStatus(message, kind = '') {
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function isDirty() {
  return currentOrder().join('\\u0000') !== originalOrder.join('\\u0000');
}

function updateOrderButtons() {
  const dirty = isDirty();
  if (undoOrder) undoOrder.disabled = !dirty;
  if (applyOrder) applyOrder.disabled = !dirty || movingPage;
}

function renumberTiles() {
  orderedTiles().forEach((tile, index) => {
    const pageNumber = index + 1;
    tile.dataset.position = String(pageNumber);
    const label = tile.querySelector('.page-number');
    const image = tile.querySelector('img');
    if (label) label.textContent = `Page ${pageNumber}`;
    if (image) image.alt = `Page ${pageNumber}`;
  });
}

function buildInsertionSlots() {
  if (!grid || !draggedTile) return;
  const tiles = visibleOrderTiles().filter((tile) => tile !== draggedTile);
  dragSlots = [];
  tiles.forEach((tile, index) => {
    const rect = tile.getBoundingClientRect();
    const y = rect.top + rect.height / 2;
    dragSlots.push({ index, x: rect.left, y });
    dragSlots.push({ index: index + 1, x: rect.right, y });
  });
}

function insertionIndexFromPointer(clientX, clientY) {
  if (!dragSlots.length) buildInsertionSlots();
  if (!dragSlots.length) return 0;
  let best = dragSlots[0];
  let bestDistance = Number.POSITIVE_INFINITY;
  dragSlots.forEach((slot) => {
    const distance = Math.hypot(clientX - slot.x, clientY - slot.y);
    if (distance < bestDistance) {
      best = slot;
      bestDistance = distance;
    }
  });
  return best.index;
}

function moveDraggedTile(clientX, clientY) {
  if (!grid || !draggedTile) return;
  const tiles = visibleOrderTiles().filter((tile) => tile !== draggedTile);
  const insertIndex = insertionIndexFromPointer(clientX, clientY);
  grid.insertBefore(draggedTile, tiles[insertIndex] || null);
  renumberTiles();
  updateOrderButtons();
}

function startDrag(tile) {
  draggedTile = tile;
  dragStartedDirty = isDirty();
  tile.classList.add('dragging');
  buildInsertionSlots();
}

function clearDrag() {
  draggedTile?.classList.remove('dragging');
  draggedTile = null;
  dragStartedDirty = false;
  dragSlots = [];
  updatePointerDropTarget(null);
}

function restoreOriginalOrder() {
  if (!grid) return;
  const byId = Object.fromEntries([...grid.querySelectorAll('.page-tile')].map((tile) => [tile.dataset.pageId, tile]));
  originalOrder.forEach((pageId) => {
    if (byId[pageId]) grid.appendChild(byId[pageId]);
  });
  renumberTiles();
  setStatus('');
  updateOrderButtons();
}

function resetToOriginalOrderForMove() {
  if (!grid) return;
  const byId = Object.fromEntries([...grid.querySelectorAll('.page-tile')].map((tile) => [tile.dataset.pageId, tile]));
  originalOrder.forEach((pageId) => {
    if (byId[pageId]) grid.appendChild(byId[pageId]);
  });
  renumberTiles();
}

function endpoint(action) {
  const paths = {
    preview: '/reorder/preview',
    apply: '/reorder/apply',
  };
  return `/api/notebooks/${encodeURIComponent(grid.dataset.notebook)}${paths[action]}`;
}

async function postOrder(action) {
  const response = await fetch(endpoint(action), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_revision: grid.dataset.revision,
      page_order: currentOrder(),
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || payload.reason || 'reorder failed');
  }
  return payload;
}

async function applyReorder() {
  if (!isDirty()) return;
  if (applyOrder) applyOrder.disabled = true;
  setStatus('Applying...');
  try {
    await postOrder('preview');
    await postOrder('apply');
    setStatus('Applied. Refreshing...', 'success');
    window.location.assign(`/organizer?notebook=${encodeURIComponent(grid.dataset.notebook)}`);
  } catch (error) {
    setStatus(error.message, 'error');
    updateOrderButtons();
  }
}

function moveEndpoint(pageId) {
  return `/api/notebooks/${encodeURIComponent(grid.dataset.notebook)}/pages/${encodeURIComponent(pageId)}/move`;
}

async function postMove(pageId, targetNotebook) {
  const response = await fetch(moveEndpoint(pageId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_revision: grid.dataset.revision,
      target_notebook: targetNotebook,
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    const message = payload.reason === 'partial_move_target_uploaded_source_failed'
      ? `Page may now exist in both notes: ${payload.error || payload.reason}`
      : payload.error || payload.reason || 'move failed';
    throw new Error(message);
  }
  return payload;
}

async function movePageToNotebook(tile, targetNotebook) {
  if (!grid || !tile || movingPage) return;
  if (targetNotebook === grid.dataset.notebook) return;
  if (isDirty() && (!draggedTile || draggedTile !== tile || dragStartedDirty)) {
    setStatus('Apply or undo the current reorder before moving pages to another note.', 'error');
    return;
  }
  movingPage = true;
  updateOrderButtons();
  setStatus(`Moving page to ${targetNotebook}...`);
  try {
    resetToOriginalOrderForMove();
    const pageId = tile.dataset.pageId;
    const payload = await postMove(pageId, targetNotebook);
    tile.remove();
    if (payload.source_revision) grid.dataset.revision = payload.source_revision;
    originalOrder = currentOrder();
    renumberTiles();
    setStatus(`Moved page to ${targetNotebook}.`, 'success');
  } catch (error) {
    setStatus(error.message, 'error');
  } finally {
    movingPage = false;
    clearDrag();
    updateOrderButtons();
  }
}

function filterMoveTargets() {
  const dialog = document.querySelector('.move-dialog');
  const query = document.querySelector('#move-search')?.value.trim().toLowerCase() || '';
  const targets = [...document.querySelectorAll('[data-move-target="notebook"]')];
  let visibleCount = 0;
  targets.forEach((target) => {
    const visible = !query || (target.dataset.moveSearch || '').includes(query);
    target.hidden = !visible;
    if (visible) visibleCount += 1;
  });
  const empty = dialog?.querySelector('.move-empty');
  if (empty) empty.hidden = visibleCount !== 0;
}

function openMoveDialog(tile) {
  const dialog = document.querySelector('.move-dialog');
  if (!dialog || !tile) return;
  activeMoveTile = tile;
  dialog.hidden = false;
  const search = dialog.querySelector('#move-search');
  if (search) {
    search.value = '';
    filterMoveTargets();
    search.focus();
  }
}

function closeMoveDialog() {
  const dialog = document.querySelector('.move-dialog');
  if (dialog) dialog.hidden = true;
  activeMoveTile = null;
}

function notebookDropTargetFromPointer(clientX, clientY) {
  const target = document.elementFromPoint(clientX, clientY)?.closest('[data-drop-target="notebook"]');
  if (!target || target.dataset.notebookName === grid?.dataset.notebook) return null;
  return target;
}

function updatePointerDropTarget(target) {
  if (pointerDropTarget === target) return;
  pointerDropTarget?.classList.remove('drop-hover');
  pointerDropTarget = target;
  pointerDropTarget?.classList.add('drop-hover');
}

grid?.addEventListener('dragstart', (event) => {
  if (event.target.closest('.move-button, .move-dialog')) {
    event.preventDefault();
    return;
  }
  const tile = event.target.closest('.page-tile');
  if (!tile) return;
  startDrag(tile);
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', tile.dataset.pageId || '');
});

grid?.addEventListener('dragover', (event) => {
  event.preventDefault();
  moveDraggedTile(event.clientX, event.clientY);
});

grid?.addEventListener('drop', (event) => {
  event.preventDefault();
  setStatus('');
  updateOrderButtons();
});

grid?.addEventListener('dragend', () => {
  clearDrag();
  updateOrderButtons();
});

grid?.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || event.target.closest('.move-button, .move-dialog')) return;
  const tile = event.target.closest('.page-tile');
  if (!tile) return;
  event.preventDefault();
  startDrag(tile);
  pointerDragging = true;
  event.target.setPointerCapture?.(event.pointerId);
});

grid?.addEventListener('pointermove', (event) => {
  if (!pointerDragging) return;
  event.preventDefault();
  const target = notebookDropTargetFromPointer(event.clientX, event.clientY);
  updatePointerDropTarget(target);
  if (target) return;
  moveDraggedTile(event.clientX, event.clientY);
});

async function endPointerDrag(event) {
  if (!pointerDragging) return;
  const target = pointerDropTarget || notebookDropTargetFromPointer(event.clientX, event.clientY);
  pointerDragging = false;
  if (target && draggedTile) {
    await movePageToNotebook(draggedTile, target.dataset.notebookName);
    updatePointerDropTarget(null);
    return;
  }
  clearDrag();
  setStatus('');
  updateOrderButtons();
}

grid?.addEventListener('pointerup', endPointerDrag);
grid?.addEventListener('pointercancel', endPointerDrag);

undoOrder?.addEventListener('click', restoreOriginalOrder);
applyOrder?.addEventListener('click', applyReorder);

grid?.addEventListener('click', async (event) => {
  const moveButton = event.target.closest('.move-button');
  if (moveButton) {
    event.preventDefault();
    event.stopPropagation();
    const tile = moveButton.closest('.page-tile');
    activeMoveTile = tile;
    openMoveDialog(tile);
    return;
  }
});

document.addEventListener('click', async (event) => {
  const target = event.target.closest('[data-move-target="notebook"]');
  if (!target) return;
  event.preventDefault();
  event.stopPropagation();
  await movePageToNotebook(activeMoveTile, target.dataset.moveNotebook);
  closeMoveDialog();
});

document.addEventListener('click', (event) => {
  if (event.target.closest('.move-panel') || event.target.closest('.move-button')) return;
  closeMoveDialog();
});

document.querySelector('.move-close')?.addEventListener('click', closeMoveDialog);
document.querySelector('#move-search')?.addEventListener('input', filterMoveTargets);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMoveDialog();
});

document.querySelectorAll('[data-drop-target="notebook"]').forEach((target) => {
  target.addEventListener('dragenter', (event) => {
    if (!draggedTile || target.dataset.notebookName === grid?.dataset.notebook) return;
    event.preventDefault();
    target.classList.add('drop-hover');
  });
  target.addEventListener('dragover', (event) => {
    if (!draggedTile || target.dataset.notebookName === grid?.dataset.notebook) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  });
  target.addEventListener('dragleave', () => {
    target.classList.remove('drop-hover');
  });
  target.addEventListener('drop', async (event) => {
    if (!draggedTile || target.dataset.notebookName === grid?.dataset.notebook) return;
    event.preventDefault();
    target.classList.remove('drop-hover');
    await movePageToNotebook(draggedTile, target.dataset.notebookName);
  });
});

function applyFilters() {
  const active = [...document.querySelectorAll('[data-filter]:checked')].map((el) => el.dataset.filter);
  document.querySelectorAll('.page-tile').forEach((tile) => {
    const visible = active.every((filter) => {
      if (filter === 'starred') return tile.dataset.starred === 'true';
      if (filter === 'headings') return Number(tile.dataset.headings) > 0;
      if (filter === 'keywords') return Number(tile.dataset.keywords) > 0;
      if (filter === 'links') return Number(tile.dataset.links) > 0;
      return true;
    });
    tile.hidden = !visible;
  });
}

document.querySelectorAll('[data-filter]').forEach((el) => el.addEventListener('change', applyFilters));
"""
