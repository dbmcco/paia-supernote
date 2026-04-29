#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import supernotelib.parser as sn_parser


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: inspect_note_stars.py /path/to/file.note", file=sys.stderr)
        return 2
    notebook = sn_parser.load_notebook(str(Path(sys.argv[1])))
    payload: dict[str, Any] = {
        "total_pages": notebook.get_total_pages(),
        "footer": notebook.metadata.footer,
        "page_metadata": [
            notebook.get_page(index).metadata
            for index in range(notebook.get_total_pages())
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
