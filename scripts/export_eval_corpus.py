#!/usr/bin/env python3
"""One-off corpus exporter: download notebooks, render selected pages to PNG.

No OCR — pages render locally via supernotelib; truth drafts come separately
from the owner. Usage from repo root:

  set -a; source ~/projects/.env; set +a
  uv run python scripts/export_eval_corpus.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paia_supernote.cli import load_cli_config  # noqa: E402
from paia_supernote.uploader import SupernoteUploader  # noqa: E402
from supernotelib import converter as sl_converter  # noqa: E402
from supernotelib import load_notebook  # noqa: E402
import tempfile  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "eval-corpus"

# notebook -> zero-based page indices (text-heavy, mixed sources)
SELECTION: dict[str, list[int]] = {
    "Quick": [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11],
    "Mgmt": [0, 1, 2, 3],
    "Dev": [26, 28, 29, 30, 32, 33, 34, 35, 36],
    "Zettle": [],
    "PKM": [],
    "Technology": [],
}


async def main() -> int:
    load_cli_config(None)  # validates env/config early
    CORPUS.mkdir(parents=True, exist_ok=True)
    uploader = SupernoteUploader()
    await uploader.start()
    for notebook, pages in SELECTION.items():
        if not pages:
            continue
        data = await uploader.download_notebook(f"{notebook}.note")
        fd, tmp = tempfile.mkstemp(suffix=".note")
        with __import__("os").fdopen(fd, "wb") as fh:
            fh.write(data)
        nb = load_notebook(tmp)
        conv = sl_converter.ImageConverter(nb)
        total = nb.get_total_pages()
        print(f"{notebook}: {total} pages total, exporting {pages}")
        for idx in pages:
            if idx >= total:
                print(f"  !! {notebook} p{idx} out of range", flush=True)
                continue
            img = conv.convert(idx)
            out = CORPUS / f"{notebook}-p{idx:02d}.png"
            img.save(str(out))
            print(f"  wrote {out.name}", flush=True)
    print(f"\nCorpus dir: {CORPUS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
