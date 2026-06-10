#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from paia_supernote.main import DEFAULT_CONFIG_PATH, load_config
from paia_supernote.page_state import PageStateStore
from paia_supernote.quick_note_audit import (
    QuickNoteAuditService,
    report_to_json,
    report_to_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a read-only Quick.note reorganization audit."
    )
    parser.add_argument("--notebook", default="Quick")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("quick-note-audit.md"),
    )
    args = parser.parse_args()

    config = load_config(args.config)
    store = PageStateStore(Path(config["state_db_path"]).expanduser())
    report = QuickNoteAuditService(
        page_state_store=store,
        source_notebook=args.notebook,
    ).run()
    rendered = (
        report_to_json(report)
        if args.format == "json"
        else report_to_markdown(report)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.format} audit for {args.notebook} to {args.output}")


if __name__ == "__main__":
    main()
