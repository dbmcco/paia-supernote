#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from paia_supernote.main import load_config
from paia_supernote.quick_filing import notebook_name_to_tag
from paia_supernote.quick_filing_service import QuickFilingService
from paia_supernote.reader import SupernoteReader
from paia_supernote.uploader import SupernoteUploader


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="Test Note 1")
    parser.add_argument("--target", default="Test Note 2")
    parser.add_argument("--tag", default=None)
    parser.add_argument(
        "--ledger",
        default=str(Path.home() / ".paia" / "supernote" / "filing-ledger.db"),
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    config = load_config()
    uploader = SupernoteUploader()
    await uploader.start()
    try:
        reader = SupernoteReader(
            vision_backend=config["vision_backend"],
            ollama_model=config["ollama_model"],
            ollama_url=config["ollama_url"],
            zai_api_key=config["zai_api_key"],
            zai_base_url=config["zai_base_url"],
            zai_vision_model=config["zai_vision_model"],
            zai_text_model=config["zai_text_model"],
        )
        service = QuickFilingService(
            uploader=uploader,
            ledger_db_path=Path(args.ledger),
            source_notebook=args.source,
            destination_map={args.tag or notebook_name_to_tag(args.target): args.target},
            dry_run=not args.live,
            reader=reader,
        )
        result = await service.run_once()
        print(result)
    finally:
        await uploader.stop()


if __name__ == "__main__":
    asyncio.run(_main())
