#!/usr/bin/env python3
"""
Font size calibration script for Supernote A5X (1404x1872 @ 226 DPI).

Renders test pages for each agent font at several sizes, producing .note-compatible
RATTA_RLE encoded bitmaps. Optionally uploads to Supernote Cloud for on-device review.

Usage:
    python scripts/calibrate_fonts.py --dry-run          # render only, save PNGs
    python scripts/calibrate_fonts.py --output-dir out/   # custom output directory
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Add src to path so we can import paia_supernote
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from paia_supernote import ratta_rle

# Supernote A5X device specs
DEVICE_WIDTH = 1404
DEVICE_HEIGHT = 1872
DEVICE_DPI = 226

# Agent fonts from design spec
AGENT_FONTS = {
    "Sam": "Bradley Hand",
    "Caroline": "Noteworthy",
    "Ingrid": "Chalkduster",
}

# Test sizes (px)
TEST_SIZES = [32, 40, 48, 56, 64]

# Proportional scaling for date/signature relative to body size
DATE_SCALE = 0.55
SIGNATURE_SCALE = 0.55

MARGIN = 80
LINE_SPACING = 12

SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump."
)


def load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font by name, trying common macOS paths and name variants."""
    # Name variants to try (e.g. "Bradley Hand" -> "Bradley Hand Bold")
    variants = [font_name, f"{font_name} Bold"]

    for name in variants:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass

        for path in (
            f"/Library/Fonts/{name}.ttf",
            f"/Library/Fonts/{name}.ttc",
            f"/System/Library/Fonts/{name}.ttf",
            f"/System/Library/Fonts/{name}.ttc",
            f"/System/Library/Fonts/Supplemental/{name}.ttf",
            f"/System/Library/Fonts/Supplemental/{name}.ttc",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    print(f"  WARNING: Could not load '{font_name}', using default")
    return ImageFont.load_default(size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_test_page(agent: str, font_name: str, body_size: int) -> Image.Image:
    """Render a single calibration test page."""
    img = Image.new("L", (DEVICE_WIDTH, DEVICE_HEIGHT), color=255)
    draw = ImageDraw.Draw(img)

    date_size = max(16, int(body_size * DATE_SCALE))
    sig_size = max(16, int(body_size * SIGNATURE_SCALE))

    body_font = load_font(font_name, body_size)
    date_font = load_font(font_name, date_size)
    sig_font = load_font(font_name, sig_size)

    # --- Title bar: font name + size ---
    title = f"{font_name} @ {body_size}px (date/sig: {date_size}px)"
    title_font = ImageFont.load_default(size=20)
    draw.text((MARGIN, MARGIN // 2), title, fill=0, font=title_font)

    # --- Date top-right ---
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    date_w = date_bbox[2] - date_bbox[0]
    draw.text(
        (DEVICE_WIDTH - MARGIN - date_w, MARGIN),
        date_str,
        fill=0,
        font=date_font,
    )

    # --- Body text with wrapping ---
    y = MARGIN + 80
    max_width = DEVICE_WIDTH - 2 * MARGIN

    # Repeat sample text to fill most of the page
    full_text = (SAMPLE_TEXT + "\n") * 6
    for line in full_text.split("\n"):
        wrapped = wrap_text(draw, line, body_font, max_width)
        for wline in wrapped:
            if y > DEVICE_HEIGHT - MARGIN - 80:
                break
            draw.text((MARGIN, y), wline, fill=0, font=body_font)
            line_bbox = draw.textbbox((0, 0), wline or "A", font=body_font)
            y += (line_bbox[3] - line_bbox[1]) + LINE_SPACING

    # --- Agent signature bottom-left ---
    sig = f"— {agent}"
    draw.text(
        (MARGIN, DEVICE_HEIGHT - MARGIN - 30),
        sig,
        fill=0,
        font=sig_font,
    )

    return img


def main() -> None:
    parser = argparse.ArgumentParser(description="Font calibration for Supernote A5X")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render pages and save as PNG only (no .note generation or upload)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("calibration_output"),
        help="Directory for output files (default: calibration_output/)",
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=TEST_SIZES,
        help=f"Font sizes to test (default: {TEST_SIZES})",
    )
    args = parser.parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Supernote A5X Font Calibration")
    print(f"Device: {DEVICE_WIDTH}x{DEVICE_HEIGHT} @ {DEVICE_DPI} DPI")
    print(f"Sizes: {args.sizes}")
    print(f"Output: {output_dir.resolve()}")
    print()

    for agent, font_name in AGENT_FONTS.items():
        print(f"Agent: {agent} ({font_name})")
        for size in args.sizes:
            img = render_test_page(agent, font_name, size)

            # Always save PNG for preview
            png_path = output_dir / f"{agent}_{font_name}_{size}px.png"
            img.save(str(png_path))
            print(f"  {size}px -> {png_path.name}")

            if not args.dry_run:
                # Encode to RATTA_RLE (raw page layer data)
                rle_data = ratta_rle.encode(img)
                rle_path = output_dir / f"{agent}_{font_name}_{size}px.rle"
                rle_path.write_bytes(rle_data)
                print(f"          -> {rle_path.name} ({len(rle_data)} bytes)")

        print()

    print("-" * 60)
    if args.dry_run:
        print("DRY RUN complete. PNG previews saved.")
        print(f"Review images in: {output_dir.resolve()}")
    else:
        print("Calibration renders complete.")
        print(f"Files saved to: {output_dir.resolve()}")
        print()
        print("NEXT STEPS:")
        print("  1. Transfer .rle files to Supernote (or view PNGs at 100% zoom)")
        print("  2. View pages on the A5X and note which size is most readable")
        print("  3. Update docs/font-calibration-results.md with your picks")
        print("  4. Run: python scripts/calibrate_fonts.py --update-writer")
        print("     to apply the chosen sizes to writer.py")


if __name__ == "__main__":
    main()
