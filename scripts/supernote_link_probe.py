#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from paia_supernote.native_link_probe import probe_native_links


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe native Supernote cross-notebook link validation status."
    )
    parser.add_argument("--quick-fixture", type=Path)
    parser.add_argument("--target-fixture", type=Path)
    args = parser.parse_args()

    result = probe_native_links(
        quick_fixture=args.quick_fixture,
        target_fixture=args.target_fixture,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if result.real_note_writes_allowed is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
