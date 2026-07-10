"""``supernote`` — agent-facing CLI for Supernote Cloud page & note management.

The CLI is the single source of truth for both capability and instruction: every
command is verbose by default (human- and model-readable), states what it found /
did / skipped and why, and suggests the next command. Write commands run the full
safe pipeline (backup → mutate → verify → upload → re-verify) internally via the
``move_pipeline`` orchestrator, so an agent only has to say "move all starred
notes".

Cloud source only. Auth persists to ``~/.paia/supernote/session.json``; a valid
session needs no per-call login. On stale auth the CLI prints an actionable
recovery message instead of a raw 403.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .main import DEFAULT_CONFIG_PATH, _filing_destination_map, load_config
from .move_pipeline import (
    MovePlan,
    MoveResult,
    _load_notebook,
    execute_move_plan,
    plan_explicit_move,
    plan_starred_moves,
    verify_notebook_bytes,
)
from .note_page_ops import remove_pages
from .note_snapshot import build_snapshot_from_notebook
from .notebook_writer import append_page_to_notebook
from .page_state import PageStateStore
from .quick_filing_service import QuickFilingService
from .reader import SupernoteReader
from .uploader import SupernoteUploader, UploadAuthError
from .writer import SupernoteWriter

BACKUPS_ROOT = Path("~/.paia/supernote/backups").expanduser()


@dataclass
class CliConfig:
    ledger_db_path: Path
    state_db_path: Path
    backups_root: Path
    destination_map: dict[str, str]
    reader: Any


def load_cli_config(config_path: Path | None = None) -> CliConfig:
    raw = load_config(config_path)
    return CliConfig(
        ledger_db_path=Path(raw["filing_ledger_db_path"]),
        state_db_path=Path(raw["state_db_path"]),
        backups_root=BACKUPS_ROOT,
        destination_map=_filing_destination_map(raw),
        reader=SupernoteReader(
            vision_backend=raw["vision_backend"],
            ollama_model=raw["ollama_model"],
            ollama_url=raw["ollama_url"],
            zai_api_key=raw["zai_api_key"],
            zai_base_url=raw["zai_base_url"],
            zai_vision_model=raw["zai_vision_model"],
            zai_text_model=raw["zai_text_model"],
        ),
    )


def _build_service(
    uploader: Any, config: CliConfig, notebook: str
) -> QuickFilingService:
    return QuickFilingService(
        uploader=uploader,
        ledger_db_path=config.ledger_db_path,
        source_notebook=notebook,
        destination_map=config.destination_map,
        dry_run=False,
        reader=config.reader,
    )


def auth_recovery_message() -> str:
    return (
        "Supernote Cloud auth is stale (403). Run `supernote auth login`, then retry. "
        "No notes were changed."
    )


def _parse_pages(spec: str | None) -> list[int] | None:
    """Parse a page spec like '3,4,5' or '3-8' into a sorted list of zero-based ints."""
    if not spec:
        return None
    pages: set[int] = set()
    try:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                pages.update(range(int(start), int(end) + 1))
            else:
                pages.add(int(part))
    except ValueError:
        raise SystemExit(f"invalid --pages spec {spec!r}: use '3,4,5' or '3-8'")
    return sorted(pages)


# ---------------------------------------------------------------------------
# Commands (each takes injected deps so the cloud/model boundary is mockable)
# ---------------------------------------------------------------------------


async def cmd_ls(uploader: Any) -> list[dict]:
    files = await uploader._list_note_files()
    return [
        {"name": f.get("fileName"), "id": f.get("id")}
        for f in files
        if f.get("isFolder") != "Y" and str(f.get("fileName") or "").endswith(".note")
    ]


async def cmd_show(
    config: CliConfig, uploader: Any, notebook: str, *, pages: list[int] | None = None
) -> list[dict]:
    source_bytes = await uploader.download_notebook(f"{notebook}.note")
    notebook_obj = _load_notebook(source_bytes)
    revision = hashlib.sha256(source_bytes).hexdigest()
    snapshot = build_snapshot_from_notebook(
        notebook_obj, notebook_name=notebook, revision=revision
    )
    page_state = PageStateStore(config.state_db_path)
    cached = {state.page: state for state in page_state.list_pages(notebook)}

    total = len(snapshot.page_order)
    indices = pages if pages is not None else list(range(total))
    rows: list[dict] = []
    for idx in indices:
        if idx < 0 or idx >= total:
            continue
        page_id = snapshot.page_order[idx]
        rec = snapshot.pages[page_id]
        state = cached.get(idx)
        raw = getattr(state, "raw_text", "") if state else ""
        preview = (raw.splitlines()[0] if raw else "")[:80]
        heading = rec.headings[0].label if rec.headings else None
        rows.append(
            {
                "page": idx,
                "starred": rec.starred,
                "heading": heading,
                "preview": preview,
                "content_hash": rec.content_hash[:8],
            }
        )
    return rows


async def cmd_read(
    config: CliConfig,
    uploader: Any,
    notebook: str,
    *,
    pages: list[int] | None = None,
    render: bool = False,
    render_dir: str | None = None,
) -> list[dict]:
    source_bytes = await uploader.download_notebook(f"{notebook}.note")
    reader = config.reader
    if pages is not None:
        results = await reader.read_pages(source_bytes, notebook, pages=pages)
    else:
        results = await reader.read_all_pages(source_bytes, notebook)
    out_dir = Path(render_dir or tempfile.gettempdir())
    rows: list[dict] = []
    for result in results:
        row = {"page": int(result.page_num), "text": str(result.text)}
        if render and getattr(result, "page_image", None) is not None:
            img_path = out_dir / f"{notebook}-page-{int(result.page_num)}.png"
            result.page_image.save(str(img_path))
            row["image"] = str(img_path)
        rows.append(row)
    return rows


async def _upload_bytes(uploader: Any, name: str, data: bytes) -> bool:
    fd, path = tempfile.mkstemp(suffix=".note")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        return bool(await uploader.upload_notebook(path, name))
    finally:
        if os.path.exists(path):
            os.unlink(path)


async def _reverify_sha256(uploader: Any, name: str, expected: bytes) -> None:
    """Re-download and confirm the cloud holds exactly the bytes we uploaded."""
    redownloaded = await uploader.download_notebook(name)
    if hashlib.sha256(redownloaded).hexdigest() != hashlib.sha256(expected).hexdigest():
        raise RuntimeError(f"{name}: post-upload re-verify failed (sha256 mismatch)")


def _backup(
    config: CliConfig, name: str, data: bytes, *, now: datetime | None = None
) -> Path:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = config.backups_root / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / name).write_bytes(data)
    return backup_dir


async def cmd_append(
    config: CliConfig,
    uploader: Any,
    notebook: str,
    text: str,
    *,
    agent: str = "Avery",
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    writer = SupernoteWriter()
    rle = writer.render_page(agent, text)
    target = f"{notebook}.note"
    notebook_bytes = await uploader.download_notebook(target)
    if dry_run:
        return {"notebook": notebook, "dry_run": True, "would_append": True}
    backup_dir = _backup(config, target, notebook_bytes, now=now)
    updated = append_page_to_notebook(notebook_bytes, rle)
    verify_notebook_bytes(target, updated)
    ok = await _upload_bytes(uploader, target, updated)
    await _reverify_sha256(uploader, target, updated)
    return {"notebook": notebook, "uploaded": bool(ok), "backup_dir": backup_dir}


async def cmd_plan(
    config: CliConfig,
    uploader: Any,
    notebook: str,
    *,
    by_stars: bool = False,
    pages: list[int] | None = None,
    to: str | None = None,
) -> MovePlan:
    service = _build_service(uploader, config, notebook)
    source_bytes = await uploader.download_notebook(f"{notebook}.note")
    if by_stars:
        return await plan_starred_moves(service, source_bytes, to_override=to)
    if not pages or to is None:
        raise SystemExit("explicit plan requires --pages and --to")
    return plan_explicit_move(
        service, source_bytes=source_bytes, pages=list(pages), target=to
    )


async def cmd_move(
    config: CliConfig,
    uploader: Any,
    notebook: str,
    *,
    by_stars: bool = False,
    pages: list[int] | None = None,
    to: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> MoveResult:
    service = _build_service(uploader, config, notebook)
    source_bytes = await uploader.download_notebook(f"{notebook}.note")
    if by_stars:
        plan = await plan_starred_moves(service, source_bytes, to_override=to)
    else:
        if not pages or to is None:
            raise SystemExit("explicit move requires --pages and --to")
        plan = plan_explicit_move(
            service, source_bytes=source_bytes, pages=list(pages), target=to
        )
    return await execute_move_plan(
        service,
        plan,
        source_bytes=source_bytes,
        backups_root=config.backups_root,
        dry_run=dry_run,
        now=now,
    )


async def cmd_remove(
    config: CliConfig,
    uploader: Any,
    notebook: str,
    *,
    pages: list[int],
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    target = f"{notebook}.note"
    notebook_bytes = await uploader.download_notebook(target)
    if dry_run:
        return {"notebook": notebook, "dry_run": True, "would_remove": list(pages)}
    backup_dir = _backup(config, target, notebook_bytes, now=now)
    updated = remove_pages(notebook_bytes, pages=list(pages))
    verify_notebook_bytes(target, updated)
    ok = await _upload_bytes(uploader, target, updated)
    await _reverify_sha256(uploader, target, updated)
    return {
        "notebook": notebook,
        "uploaded": bool(ok),
        "backup_dir": backup_dir,
        "removed": list(pages),
    }


async def cmd_auth_status(uploader: Any) -> dict:
    await uploader._ensure_authenticated()
    return {"authenticated": True, "session_file": str(uploader.SESSION_FILE)}


# ---------------------------------------------------------------------------
# Verbose formatting (the guidance the model reads)
# ---------------------------------------------------------------------------


def format_ls(notebooks: list[dict]) -> str:
    if not notebooks:
        return "No notebooks found in the cloud Note folder."
    lines = [f"Found {len(notebooks)} notebook(s):"]
    for nb in notebooks:
        lines.append(f"  {nb['name']}")
    lines.append("")
    lines.append("Next: `supernote show <notebook>` for a per-page overview.")
    return "\n".join(lines)


def format_show(notebook: str, rows: list[dict]) -> str:
    if not rows:
        return f"{notebook} has no pages."
    lines = [f"{notebook} — {len(rows)} page(s):", ""]
    for row in rows:
        star = "★" if row["starred"] else " "
        head = row["heading"] or ""
        preview = row["preview"] or ""
        lines.append(
            f"  {row['page']:>3} {star} {head:<20} {preview}  ({row['content_hash']})"
        )
    starred = [r for r in rows if r["starred"]]
    if starred:
        lines.append("")
        lines.append(
            f"{len(starred)} starred page(s). Next: "
            f"`supernote plan {notebook} --by-stars` to preview filing."
        )
    return "\n".join(lines)


def format_plan(plan: MovePlan) -> str:
    lines = [
        f"Move plan for {plan.source_notebook} ({len(plan.annotations)} page(s)).",
        "This is a preview — nothing was changed.",
        "",
    ]
    if not plan.annotations:
        lines.append("Nothing to move.")
        return "\n".join(lines)
    for ann in plan.annotations:
        target = ann.target_notebook or "—"
        if ann.ledger_status == "already_moved":
            state = "already moved"
        elif ann.target_notebook is None:
            state = "needs review"
        else:
            state = "would move"
        lines.append(
            f"  page {ann.page} → {target}  [{state}]  "
            f"conf {ann.confidence:.2f}  op {ann.operation_id[:8]}"
        )
    ready = [
        a
        for a in plan.annotations
        if a.target_notebook and a.ledger_status != "already_moved"
    ]
    if ready:
        lines.append("")
        lines.append(
            f"Next: `supernote move {plan.source_notebook} --by-stars` to file "
            f"{len(ready)} page(s) through the safe pipeline."
        )
    return "\n".join(lines)


def format_move_result(result: MoveResult) -> str:
    lines: list[str] = []
    if result.dry_run:
        lines.append("Dry run — nothing was changed.")
    else:
        lines.append(f"Backed up to: {result.backup_dir}")
    for outcome in result.outcomes:
        lines.append(
            f"  {outcome.notebook}: {outcome.before_pages} → "
            f"{outcome.after_pages} pages"
        )
    if result.completed_pages:
        lines.append(f"Moved pages: {result.completed_pages}")
    if result.skipped_pages:
        lines.append(f"Skipped (already moved): {result.skipped_pages}")
    if result.needs_review_pages:
        lines.append(f"Needs review: {result.needs_review_pages}")
    if result.operation_ids:
        lines.append("Operation ids: " + ", ".join(result.operation_ids))
    lines.append("")
    lines.append("Next: `supernote show <notebook>` to verify the result.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# argparse wiring + dispatch
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supernote",
        description=(
            "Agent-facing Supernote Cloud CLI. " "Run `supernote <command> --help`."
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ls", help="list cloud notebooks")

    p_show = sub.add_parser("show", help="per-page overview of a notebook")
    p_show.add_argument("notebook")
    p_show.add_argument("--pages", help="page spec, e.g. '3,4,5' or '3-8'")

    p_read = sub.add_parser("read", help="full OCR text of pages")
    p_read.add_argument("notebook")
    p_read.add_argument("--pages")
    p_read.add_argument("--render", action="store_true", help="write page PNGs to /tmp")

    p_append = sub.add_parser("append", help="append a page to a notebook")
    p_append.add_argument("notebook")
    p_append.add_argument("--text")
    p_append.add_argument("--file", type=Path)
    p_append.add_argument("--stdin", action="store_true")
    p_append.add_argument("--agent", default="Avery")
    p_append.add_argument("--dry-run", action="store_true")

    p_move = sub.add_parser("move", help="move pages through the full safe pipeline")
    p_move.add_argument("notebook")
    p_move.add_argument("--by-stars", action="store_true")
    p_move.add_argument("--pages", help="explicit page spec (with --to)")
    p_move.add_argument("--to", help="target notebook name")
    p_move.add_argument("--dry-run", action="store_true")

    p_plan = sub.add_parser("plan", help="read-only move preview (no writes)")
    p_plan.add_argument("notebook")
    p_plan.add_argument("--by-stars", action="store_true")
    p_plan.add_argument("--pages")
    p_plan.add_argument("--to")

    p_remove = sub.add_parser("remove", help="remove pages (safe pipeline)")
    p_remove.add_argument("notebook")
    p_remove.add_argument("--pages", required=True)
    p_remove.add_argument("--dry-run", action="store_true")

    p_auth = sub.add_parser("auth", help="cloud auth")
    auth_sub = p_auth.add_subparsers(dest="auth_command", required=True)
    auth_sub.add_parser("status")
    auth_sub.add_parser("login")
    return parser


def _read_append_text(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        return Path(args.file).read_text()
    if args.text is None:
        raise SystemExit("append requires one of --text, --file, or --stdin")
    return str(args.text)


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list) and obj and is_dataclass(obj[0]):
        return [asdict(item) for item in obj]
    return obj


async def _run_command(
    args: argparse.Namespace, config: CliConfig, uploader: Any
) -> None:
    def emit(payload: Any, text: str) -> None:
        if args.json:
            print(json.dumps(_jsonable(payload), default=str, indent=2))
        elif not args.quiet:
            print(text)

    if args.command == "ls":
        rows = await cmd_ls(uploader)
        emit(rows, format_ls(rows))
    elif args.command == "show":
        rows = await cmd_show(
            config, uploader, args.notebook, pages=_parse_pages(args.pages)
        )
        emit(rows, format_show(args.notebook, rows))
    elif args.command == "read":
        rows = await cmd_read(
            config,
            uploader,
            args.notebook,
            pages=_parse_pages(args.pages),
            render=args.render,
        )
        emit(
            rows,
            "\n\n".join(f"[page {r['page']}]\n{r['text']}" for r in rows) or "(empty)",
        )
    elif args.command == "append":
        text = _read_append_text(args)
        result = await cmd_append(
            config,
            uploader,
            args.notebook,
            text,
            agent=args.agent,
            dry_run=args.dry_run,
        )
        msg = (
            "Dry run — no page appended."
            if result.get("dry_run")
            else (
                f"Appended a page to {result['notebook']}. "
                f"Backed up to {result['backup_dir']}."
            )
        )
        emit(result, msg)
    elif args.command == "plan":
        plan = await cmd_plan(
            config,
            uploader,
            args.notebook,
            by_stars=args.by_stars,
            pages=_parse_pages(args.pages),
            to=args.to,
        )
        emit(plan.annotations, format_plan(plan))
    elif args.command == "move":
        move_result = await cmd_move(
            config,
            uploader,
            args.notebook,
            by_stars=args.by_stars,
            pages=_parse_pages(args.pages),
            to=args.to,
            dry_run=args.dry_run,
        )
        emit(move_result, format_move_result(move_result))
    elif args.command == "remove":
        result = await cmd_remove(
            config,
            uploader,
            args.notebook,
            pages=_parse_pages(args.pages) or [],
            dry_run=args.dry_run,
        )
        msg = (
            "Dry run — nothing removed."
            if result.get("dry_run")
            else (
                f"Removed {result['removed']} from {result['notebook']}. "
                f"Backed up to {result['backup_dir']}."
            )
        )
        emit(result, msg)
    elif args.command == "auth":
        if args.auth_command == "status":
            result = await cmd_auth_status(uploader)
            emit(result, f"Authenticated. Session at {result['session_file']}.")
        else:
            raise SystemExit("Use `supernote auth login` which opens a browser.")


async def _dispatch(args: argparse.Namespace, config: CliConfig) -> int:
    if args.command == "auth" and args.auth_command == "login":
        # Login needs a visible browser, so it does not share the headless session.
        uploader = SupernoteUploader(headless=False)
        try:
            await uploader.start()
            await uploader._ensure_authenticated()
            print(f"Session saved to {uploader.SESSION_FILE}")
        finally:
            await uploader.stop()
        return 0

    uploader = SupernoteUploader()
    try:
        await uploader.start()
        await _run_command(args, config, uploader)
    finally:
        await uploader.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_cli_config(args.config)
    try:
        return asyncio.run(_dispatch(args, config))
    except UploadAuthError:
        print(auth_recovery_message(), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
