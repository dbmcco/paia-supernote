#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from paia_supernote.quick_filing_service import QuickFilingService
from paia_supernote.uploader import SupernoteUploader


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="Test Note 1")
    parser.add_argument("--target", default="Test Note 2")
    parser.add_argument("--tag", default="test")
    parser.add_argument(
        "--ledger",
        default=str(Path.home() / ".paia" / "supernote" / "filing-ledger.db"),
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    uploader = SupernoteUploader()
    await uploader.start()
    try:
        service = QuickFilingService(
            uploader=uploader,
            ledger_db_path=Path(args.ledger),
            source_notebook=args.source,
            destination_map={args.tag: args.target},
            dry_run=not args.live,
        )
        result = await service.run_once()
        print(result)
    finally:
        await uploader.stop()


if __name__ == "__main__":
    asyncio.run(_main())
