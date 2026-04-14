"""
ABOUTME: Creates a fresh test.note with a Sam-authored page and uploads to cloud.
ABOUTME: Used for the read/write roundtrip test.

Run from paia-supernote root:
    uv run python scripts/create_test_note.py
"""

import asyncio
import copy
import hashlib
import tempfile
import os
import uuid
from pathlib import Path

import supernotelib.parser as sn_parser
import supernotelib.manipulator as sn_manip

from paia_supernote.writer import SupernoteWriter
from paia_supernote.uploader import SupernoteUploader

SYNC_BASE = SupernoteUploader.SYNC_BASE
TEMPLATE_NOTE = SYNC_BASE / "Personal.note"


def create_single_page_note(ratta_rle_bytes: bytes) -> bytes:
    """Create a .note file with a single page containing ratta_rle_bytes.

    Clones the header/metadata from Personal.note but replaces all pages
    with one new page containing our content.
    """
    notebook = sn_parser.load_notebook(str(TEMPLATE_NOTE))

    # Use page 0 as the structural template — deepcopy it
    template_page = copy.deepcopy(notebook.get_page(0))

    # Fresh page ID
    template_page.metadata["PAGEID"] = str(uuid.uuid4()).replace("-", "").upper()[:32]

    # Clear recognition data
    template_page.set_recogn_file(None)
    template_page.set_recogn_text(None)
    template_page.set_totalpath(None)
    template_page.metadata["RECOGNSTATUS"] = str(template_page.RECOGNSTATUS_NONE)

    # Set MAINLAYER to our content
    if template_page.is_layer_supported():
        template_page.get_layer(0).set_content(ratta_rle_bytes)
        # Clear other non-background layers
        for i in range(1, len(template_page.get_layers())):
            layer = template_page.get_layers()[i]
            if layer.get_name() and layer.get_name() != "BGLAYER":
                layer.set_content(b"")
    else:
        template_page.set_content(ratta_rle_bytes)

    # Replace notebook pages with just this one page
    notebook.pages = [template_page]

    return sn_manip.reconstruct(notebook)


async def main() -> None:
    print("=== Creating test.note for roundtrip test ===\n")

    # Render Sam's page
    print("Step 1: Render Sam's page...")
    writer = SupernoteWriter()
    content = (
        "□ Test roundtrip — Sam writing\n"
        "○ Braydon edits this on device\n"
        "○ Sam reads back and adds more\n"
        "\n"
        "This is the initial write from\n"
        "paia-supernote. Edit on device,\n"
        "then sync back."
    )
    ratta_rle_bytes = writer.render_page("Sam", content)
    print(f"  Rendered {len(ratta_rle_bytes):,} RATTA_RLE bytes")

    # Build single-page .note
    print("\nStep 2: Build test.note...")
    note_bytes = create_single_page_note(ratta_rle_bytes)
    print(f"  Note size: {len(note_bytes):,} bytes")

    # Write to temp file and upload
    print("\nStep 3: Upload test.note to Supernote Cloud...")
    with tempfile.NamedTemporaryFile(suffix=".note", delete=False, dir="/tmp") as tmp:
        tmp.write(note_bytes)
        tmp_path = tmp.name

    try:
        uploader = SupernoteUploader()
        await uploader.start()
        try:
            success = await uploader.upload_notebook(tmp_path, "test.note")
            print(f"  Upload: {'OK' if success else 'FAILED'}")
        finally:
            await uploader.stop()
    finally:
        os.unlink(tmp_path)

    print("\n=== Done — sync your device to see test.note ===")
    print("Edit it on the Supernote, then sync back so we can read it.")


if __name__ == "__main__":
    asyncio.run(main())
