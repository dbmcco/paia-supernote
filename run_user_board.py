#!/usr/bin/env python3
"""
Simple launcher for the PAIA Supernote User Board.

This script provides an easy way to start the user board when the
CLI entry point isn't available in the current environment.
"""

import sys
from pathlib import Path

# Add src to path so we can import the module
script_dir = Path(__file__).parent if __name__ == "__main__" else Path.cwd()
src_path = script_dir / "src"
sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    import asyncio
    try:
        from paia_supernote.user_board import main
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ Error starting user board: {e}")
        sys.exit(1)