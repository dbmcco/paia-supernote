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
        <button type="button" disabled>Undo</button>
        <button type="button" disabled>Apply</button>
        <label class="zoom">Zoom <input type="range" min="160" max="420" value="220"></label>
      </div>
    </header>
    <main class="page-grid" data-revision="{escape(str(snapshot.get("revision") or ""))}">
      {_render_pages(notebook_name, snapshot)}
    </main>
  </section>
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
            f'<a class="notebook{active}" href="/notebooks/{quote(name)}">{escape(name)}</a>'
        )
    return "\n".join(items)


def _render_pages(notebook_name: str, snapshot: dict[str, Any]) -> str:
    pages = snapshot.get("pages") or {}
    page_order = snapshot.get("page_order") or []
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
            "/image?scale=0.25"
        )
        tiles.append(
            f"""<article class="page-tile" data-page-id="{escape(str(page_id))}" data-starred="{str(starred).lower()}" data-headings="{heading_count}" data-keywords="{keyword_count}" data-links="{link_count}">
  <div class="page-meta"><span>Page {page_number}</span>{badges}</div>
  <img src="{image_src}" alt="Page {page_number}">
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
label { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.workspace { min-width: 0; display: flex; flex-direction: column; }
.toolbar { height: 64px; border-bottom: 1px solid #d6d9dd; background: #ffffff; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 18px; }
.toolbar-actions { display: flex; align-items: center; gap: 10px; }
button { border: 1px solid #b8c0c9; background: #ffffff; color: #1f2933; min-height: 34px; padding: 0 11px; border-radius: 6px; font: inherit; }
button:disabled { color: #8b949e; background: #f0f2f4; }
.zoom { min-width: 190px; }
.page-grid { --tile-width: 220px; padding: 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--tile-width), 1fr)); gap: 14px; align-items: start; overflow: auto; }
.page-tile { background: #ffffff; border: 1px solid #d6d9dd; border-radius: 8px; min-width: 0; overflow: hidden; }
.page-meta { height: 34px; padding: 0 9px; display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #e2e5e8; font-size: 13px; white-space: nowrap; }
.badge { border: 1px solid #bac4cc; border-radius: 999px; padding: 2px 6px; font-size: 11px; color: #43505c; }
.page-tile img { display: block; width: 100%; aspect-ratio: 1404 / 1872; object-fit: contain; background: #f9fafb; }
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
zoom?.addEventListener('input', () => {
  grid?.style.setProperty('--tile-width', `${zoom.value}px`);
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
