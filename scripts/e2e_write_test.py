"""
ABOUTME: End-to-end write path smoke test.
ABOUTME: Renders a page, appends it to Personal.note, uploads to Supernote Cloud.

Run from paia-supernote root:
    uv run python scripts/e2e_write_test.py
"""

import asyncio
import hashlib
import tempfile
import os
from pathlib import Path

from paia_supernote.writer import SupernoteWriter
from paia_supernote.notebook_writer import append_page_to_notebook
from paia_supernote.uploader import SupernoteUploader

SYNC_BASE = Path(
    "~/Library/Containers/com.ratta.supernote/Data/Library/"
    "Application Support/com.ratta.supernote/908410628964298752/Supernote/Note"
).expanduser()


async def main() -> None:
    print("=== paia-supernote end-to-end write test ===\n")

    # Step 1: Render a test page
    print("Step 1: Render page...")
    writer = SupernoteWriter()
    test_content = "□ Test write from paia-supernote\n○ Verify on device\nSmoke test page"
    ratta_rle_bytes = writer.render_page("Sam", test_content)
    print(f"  Rendered {len(ratta_rle_bytes):,} RATTA_RLE bytes")

    # Step 2: Append to Personal.note (safest test target)
    note_path = SYNC_BASE / "Personal.note"
    print(f"\nStep 2: Append page to {note_path.name}...")
    original_md5 = hashlib.md5(note_path.read_bytes()).hexdigest()
    updated_bytes = append_page_to_notebook(str(note_path), ratta_rle_bytes)
    print(f"  Original: {note_path.stat().st_size:,} bytes  md5={original_md5}")
    print(f"  Updated:  {len(updated_bytes):,} bytes")

    # Step 3: Upload
    print("\nStep 3: Upload to Supernote Cloud...")
    with tempfile.NamedTemporaryFile(suffix=".note", delete=False, dir="/tmp") as tmp:
        tmp.write(updated_bytes)
        tmp_path = tmp.name

    try:
        uploader = SupernoteUploader()
        await uploader.start()
        try:
            success = await uploader.upload_notebook(tmp_path, "Personal.note")
            print(f"  Upload result: {'OK' if success else 'FAILED'}")
        finally:
            await uploader.stop()
    finally:
        os.unlink(tmp_path)

    print("\n=== Done — check Personal.note on device after next sync ===")


if __name__ == "__main__":
    asyncio.run(main())
