"""
ABOUTME: Supernote page writer module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Renders content to PIL bitmaps, sets as MAINLAYER on pages,
         and appends pages to Supernote notebooks via supernotelib
"""

import io
import secrets
import string
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import supernotelib

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

    # Font size constants (calibrated from device feedback 2026-04-14: 36 was too small)
    BODY_FONT_SIZE = 52
    DATE_FONT_SIZE = 36
    SIGNATURE_FONT_SIZE = 48

    MARGIN = 80
    LINE_SPACING = 16

    def render_page(self, agent: str, content: str, content_type: str = "text") -> bytes:
        """
        Render content to RATTA_RLE encoded bytes in the agent's font.

        Returns RATTA_RLE bytes suitable for use as a MAINLAYER bitmap. Layout:
        - Date top-right in small system font (~28px)
        - Body text in agent's assigned font
        - Agent signature bottom-left (~36px)
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

    def build_page(self, rle_content: bytes) -> supernotelib.Page:
        """Create a supernotelib Page with rle_content set as MAINLAYER.

        The returned Page has the correct metadata structure for appending
        to a notebook via reconstruct().
        """
        page_id = self._generate_page_id()

        # MAINLAYER metadata (the only layer with content)
        mainlayer_info = {
            "LAYERTYPE": "NOTE",
            "LAYERPROTOCOL": "RATTA_RLE",
            "LAYERNAME": "MAINLAYER",
            "LAYERPATH": "0",
            "LAYERBITMAP": "0",  # placeholder — packer sets real address
            "LAYERVECTORGRAPH": "0",
            "LAYERRECOGN": "0",
        }

        # BGLAYER references the existing style_white background in the notebook
        bglayer_info = {
            "LAYERTYPE": "NOTE",
            "LAYERPROTOCOL": "RATTA_RLE",
            "LAYERNAME": "BGLAYER",
            "LAYERPATH": "0",
            "LAYERBITMAP": "0",
            "LAYERVECTORGRAPH": "0",
            "LAYERRECOGN": "0",
        }

        # Layers 1-3 are unused (empty dicts → name=None, skipped by packer)
        layers = [mainlayer_info, {}, {}, {}, bglayer_info]

        # Layer info JSON (uses # instead of : per Supernote format)
        layer_info = (
            '[{"layerId"#3,"name"#"Layer 3","isBackgroundLayer"#false,'
            '"isAllowAdd"#false,"isCurrentLayer"#false,"isVisible"#true,'
            '"isDeleted"#true,"isAllowUp"#false,"isAllowDown"#false},'
            '{"layerId"#2,"name"#"Layer 2","isBackgroundLayer"#false,'
            '"isAllowAdd"#false,"isCurrentLayer"#false,"isVisible"#true,'
            '"isDeleted"#true,"isAllowUp"#false,"isAllowDown"#false},'
            '{"layerId"#1,"name"#"Layer 1","isBackgroundLayer"#false,'
            '"isAllowAdd"#false,"isCurrentLayer"#false,"isVisible"#true,'
            '"isDeleted"#true,"isAllowUp"#false,"isAllowDown"#false},'
            '{"layerId"#0,"name"#"Main Layer","isBackgroundLayer"#false,'
            '"isAllowAdd"#false,"isCurrentLayer"#true,"isVisible"#true,'
            '"isDeleted"#false,"isAllowUp"#false,"isAllowDown"#false},'
            '{"layerId"#-1,"name"#"Background Layer","isBackgroundLayer"#true,'
            '"isAllowAdd"#true,"isCurrentLayer"#false,"isVisible"#true,'
            '"isDeleted"#false,"isAllowUp"#false,"isAllowDown"#false}]'
        )

        page_info = {
            "PAGESTYLE": "style_white",
            "PAGESTYLEMD5": "0",
            "LAYERINFO": layer_info,
            "LAYERSEQ": "MAINLAYER,BGLAYER",
            "MAINLAYER": "0",
            "LAYER1": "0",
            "LAYER2": "0",
            "LAYER3": "0",
            "BGLAYER": "0",
            "TOTALPATH": "0",
            "THUMBNAILTYPE": "0",
            "RECOGNSTATUS": "0",
            "RECOGNTEXT": "0",
            "RECOGNFILE": "0",
            "PAGEID": page_id,
            "RECOGNTYPE": "0",
            "RECOGNFILESTATUS": "0",
            "RECOGNLANGUAGE": "none",
            "EXTERNALLINKINFO": "0",
            "IDTABLE": "0",
            "ORIENTATION": supernotelib.Page.ORIENTATION_VERTICAL,
            supernotelib.fileformat.KEY_LAYERS: layers,
        }

        page = supernotelib.Page(page_info)
        # Set MAINLAYER bitmap content (RLE bytes)
        page.get_layer(0).set_content(rle_content)
        return page

    def append_to_notebook(
        self, notebook_bytes: bytes, agent: str, content: str
    ) -> bytes:
        """Render content and append as a new page to an existing notebook.

        Args:
            notebook_bytes: Raw bytes of an existing .note file
            agent: Agent name (Sam, Caroline, Ingrid)
            content: Text content to render

        Returns:
            Modified .note bytes with the new page appended at the back.
            Caller handles persistence (writing to disk / uploading).
        """
        rle_content = self.render_page(agent, content)
        page = self.build_page(rle_content)

        notebook = supernotelib.load(io.BytesIO(notebook_bytes))
        notebook.pages.append(page)
        # Update metadata total-page count so reconstruct sees the new page
        notebook.metadata.pages.append(page.metadata)

        return supernotelib.reconstruct(notebook)

    # Tasks page layout constants
    TASKS_HEADER_SIZE = 64
    TASKS_BODY_SIZE = 40
    TASKS_ID_SIZE = 26
    TASKS_LINE_SPACING = 14

    LANE_LABELS = {
        "focus": "FOCUS",
        "inbox": "INBOX",
        "orbit": "ORBIT",
        "parking": "PARKING",
    }

    def render_tasks_page(self, lane: str, tasks: list) -> bytes:
        """Render a task lane page to RATTA_RLE bytes.

        Layout:
        - Lane name as bold header at top
        - Tasks as checkbox list: □ Title  [id]  or  ☑ Title  [id]
        - Overflow indicated by "+ N more tasks" at bottom
        """
        img = Image.new("L", (self.DEVICE_WIDTH, self.DEVICE_HEIGHT), color=255)
        draw = ImageDraw.Draw(img)

        header_font = self._load_font(None, size=self.TASKS_HEADER_SIZE)
        body_font = self._load_font(None, size=self.TASKS_BODY_SIZE)
        id_font = self._load_font(None, size=self.TASKS_ID_SIZE)

        # Lane header
        label = self.LANE_LABELS.get(lane, lane.upper())
        draw.text((self.MARGIN, self.MARGIN), label, fill=0, font=header_font)

        # Divider line below header
        header_bbox = draw.textbbox((0, 0), label, font=header_font)
        header_h = header_bbox[3] - header_bbox[1]
        line_y = self.MARGIN + header_h + 16
        draw.line(
            [(self.MARGIN, line_y), (self.DEVICE_WIDTH - self.MARGIN, line_y)],
            fill=0,
            width=2,
        )

        y = line_y + 24
        max_text_width = self.DEVICE_WIDTH - 2 * self.MARGIN - 160
        bottom_limit = self.DEVICE_HEIGHT - self.MARGIN - 60
        rendered = 0

        for task in tasks:
            if y >= bottom_limit:
                remaining = len(tasks) - rendered
                draw.text(
                    (self.MARGIN, y),
                    f"+ {remaining} more task{'s' if remaining != 1 else ''}",
                    fill=80,
                    font=id_font,
                )
                break

            done = task.get("status") in ("done", "completed", "closed")
            checkbox = "\u2611" if done else "\u25a1"  # ☑ or □
            title = task.get("title", task.get("name", "Untitled"))
            task_id = str(task.get("id", ""))

            # Truncate title if it's very long
            line = f"{checkbox} {title}"
            wrapped = self._wrap_text(draw, line, body_font, max_text_width)
            first_line = wrapped[0] if wrapped else line

            draw.text((self.MARGIN, y), first_line, fill=0, font=body_font)

            # Task ID in small grey text to the right
            if task_id:
                id_text = f"[{task_id[:12]}]"
                id_bbox = draw.textbbox((0, 0), id_text, font=id_font)
                id_x = self.DEVICE_WIDTH - self.MARGIN - (id_bbox[2] - id_bbox[0])
                id_y = y + (self.TASKS_BODY_SIZE - self.TASKS_ID_SIZE) // 2
                draw.text((id_x, id_y), id_text, fill=120, font=id_font)

            body_bbox = draw.textbbox((0, 0), first_line or "A", font=body_font)
            y += (body_bbox[3] - body_bbox[1]) + self.TASKS_LINE_SPACING
            rendered += 1

        if not tasks:
            draw.text(
                (self.MARGIN, y),
                "— empty —",
                fill=160,
                font=body_font,
            )

        return ratta_rle.encode(img)

    @staticmethod
    def _generate_page_id() -> str:
        """Generate a unique page ID in the Supernote format: P<timestamp><random>."""
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")[:20]
        chars = string.ascii_letters + string.digits
        rand = "".join(secrets.choice(chars) for _ in range(12))
        return f"P{ts}{rand}"

    # System fonts tried (in order) when no agent font is requested.
    # These have good Unicode coverage including □/☑ glyphs.
    _FALLBACK_FONTS = (
        "Arial",
        "Helvetica",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/SF-Pro-Display-Regular.otf",
    )

    def _load_font(self, agent: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        """Load font for agent, falling back to system font then PIL default."""
        font_name = self.AGENT_FONTS.get(agent, "") if agent else ""
        if font_name:
            # Try name variants (e.g. "Bradley Hand" -> "Bradley Hand Bold")
            for name in (font_name, f"{font_name} Bold"):
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
        # No agent font (or agent font failed) — try system fonts with Unicode coverage
        for path in self._FALLBACK_FONTS:
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

