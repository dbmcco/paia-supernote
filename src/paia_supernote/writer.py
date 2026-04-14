"""
ABOUTME: Supernote page writer module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Renders content to PIL bitmaps using agent-specific fonts for Supernote pages
"""

from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from . import ratta_rle


class SupernoteWriter:
    """Renders text content to page bitmaps for Supernote notebooks."""

    # Agent font mapping from design spec
    AGENT_FONTS = {
        "Sam": "Bradley Hand",
        "Caroline": "Noteworthy",
        "Ingrid": "Chalkduster",
    }

    # Device specs for Supernote A5X
    DEVICE_WIDTH = 1404
    DEVICE_HEIGHT = 1872
    DEVICE_DPI = 226

    # Font size constants (configurable)
    BODY_FONT_SIZE = 36
    DATE_FONT_SIZE = 20
    SIGNATURE_FONT_SIZE = 20

    MARGIN = 80
    LINE_SPACING = 12

    def render_page(self, agent: str, content: str, content_type: str = "text") -> bytes:
        """
        Render content to a .note page in the agent's font.

        Returns bytes representing a .note page. Layout:
        - Date top-right in small system font
        - Body text in agent's assigned font
        - Agent signature bottom-left
        """
        img = Image.new("L", (self.DEVICE_WIDTH, self.DEVICE_HEIGHT), color=255)
        draw = ImageDraw.Draw(img)

        body_font = self._load_font(agent, size=self.BODY_FONT_SIZE)
        small_font = self._load_font(None, size=self.DATE_FONT_SIZE)

        # Date top-right
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_bbox = draw.textbbox((0, 0), date_str, font=small_font)
        date_w = date_bbox[2] - date_bbox[0]
        draw.text(
            (self.DEVICE_WIDTH - self.MARGIN - date_w, self.MARGIN),
            date_str,
            fill=0,
            font=small_font,
        )

        # Body text with word wrapping
        y = self.MARGIN + 60
        max_width = self.DEVICE_WIDTH - 2 * self.MARGIN
        for line in content.split("\n"):
            wrapped = self._wrap_text(draw, line, body_font, max_width)
            for wline in wrapped:
                if y > self.DEVICE_HEIGHT - self.MARGIN - 60:
                    break
                draw.text((self.MARGIN, y), wline, fill=0, font=body_font)
                line_bbox = draw.textbbox((0, 0), wline or "A", font=body_font)
                y += (line_bbox[3] - line_bbox[1]) + self.LINE_SPACING

        # Agent signature bottom-left
        sig_font = self._load_font(None, size=self.SIGNATURE_FONT_SIZE)
        sig = f"— {agent}"
        draw.text(
            (self.MARGIN, self.DEVICE_HEIGHT - self.MARGIN - 30),
            sig,
            fill=0,
            font=sig_font,
        )

        # Convert PIL image to RATTA_RLE encoded bytes
        return ratta_rle.encode(img)

    def _load_font(self, agent: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        """Load font for agent, falling back to default if not available."""
        font_name = self.AGENT_FONTS.get(agent, "") if agent else ""
        if font_name:
            try:
                return ImageFont.truetype(font_name, size)
            except OSError:
                # Try common macOS font paths
                for path in (
                    f"/Library/Fonts/{font_name}.ttf",
                    f"/System/Library/Fonts/Supplemental/{font_name}.ttf",
                    f"/System/Library/Fonts/{font_name}.ttf",
                ):
                    try:
                        return ImageFont.truetype(path, size)
                    except OSError:
                        continue
        return ImageFont.load_default(size=size)

    @staticmethod
    def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
        """Word-wrap text to fit within max_width pixels."""
        if not text:
            return [""]
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

