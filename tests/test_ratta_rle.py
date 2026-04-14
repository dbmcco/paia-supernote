"""Tests for RATTA_RLE encoder.

Roundtrip tests use supernotelib's RattaRleDecoder to verify the encoder
produces bytes that the Supernote ecosystem can actually decode.
"""

import pytest
from PIL import Image
from supernotelib.decoder import RattaRleDecoder

from paia_supernote.ratta_rle import (
    COLORCODE_BACKGROUND,
    COLORCODE_BLACK,
    COLORCODE_DARK_GRAY,
    COLORCODE_GRAY,
    COLORCODE_WHITE,
    encode,
)

# Supernote A5X dimensions
PAGE_W, PAGE_H = 1404, 1872

# supernotelib default grayscale palette values (what the decoder outputs)
GRAY_BLACK = 0x00
GRAY_DARK_GRAY = 0x9D
GRAY_GRAY = 0xC9
GRAY_WHITE = 0xFE
GRAY_TRANSPARENT = 0xFF  # background


def _roundtrip(encoded: bytes, w: int, h: int) -> bytes:
    """Decode RATTA_RLE bytes via supernotelib and return raw grayscale pixels."""
    decoder = RattaRleDecoder()
    raw, (rw, rh), bpp = decoder.decode(encoded, w, h)
    assert rw == w
    assert rh == h
    assert bpp == 8
    return raw


class TestAllBlackPage:
    """All-black page encodes correctly."""

    def test_roundtrip_full_page(self):
        img = Image.new("L", (PAGE_W, PAGE_H), GRAY_BLACK)
        encoded = encode(img)
        raw = _roundtrip(encoded, PAGE_W, PAGE_H)
        assert raw == bytes([GRAY_BLACK]) * (PAGE_W * PAGE_H)

    def test_small_all_black(self):
        img = Image.new("L", (10, 10), GRAY_BLACK)
        encoded = encode(img)
        raw = _roundtrip(encoded, 10, 10)
        assert raw == bytes([GRAY_BLACK]) * 100


class TestAllWhitePage:
    """All-white (background) page encodes correctly."""

    def test_roundtrip_full_page(self):
        img = Image.new("L", (PAGE_W, PAGE_H), GRAY_TRANSPARENT)
        encoded = encode(img)
        raw = _roundtrip(encoded, PAGE_W, PAGE_H)
        assert raw == bytes([GRAY_TRANSPARENT]) * (PAGE_W * PAGE_H)

    def test_small_all_white(self):
        img = Image.new("L", (10, 10), GRAY_TRANSPARENT)
        encoded = encode(img)
        raw = _roundtrip(encoded, 10, 10)
        assert raw == bytes([GRAY_TRANSPARENT]) * 100


class TestRoundtrip:
    """Encode -> decode with supernotelib -> pixel match."""

    def test_binary_image_stripe(self):
        """1-bit image (black text on white background) roundtrips exactly."""
        pixels = [GRAY_TRANSPARENT] * (PAGE_W * PAGE_H)
        for y in range(100, 200):
            for x in range(PAGE_W):
                pixels[y * PAGE_W + x] = GRAY_BLACK
        img = Image.new("L", (PAGE_W, PAGE_H))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, PAGE_W, PAGE_H)
        assert raw == bytes(pixels)

    def test_all_five_colors(self):
        """Image using all five palette values roundtrips exactly."""
        w, h = 50, 10
        palette_values = [
            GRAY_BLACK,
            GRAY_DARK_GRAY,
            GRAY_GRAY,
            GRAY_WHITE,
            GRAY_TRANSPARENT,
        ]
        pixels = [palette_values[i % 5] for i in range(w * h)]
        img = Image.new("L", (w, h))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, w, h)
        assert raw == bytes(pixels)

    def test_grayscale_blocks(self):
        """Blocks of each gray level roundtrip to correct palette values."""
        w, h = 100, 5
        pixels = (
            [GRAY_BLACK] * 100
            + [GRAY_DARK_GRAY] * 100
            + [GRAY_GRAY] * 100
            + [GRAY_WHITE] * 100
            + [GRAY_TRANSPARENT] * 100
        )
        img = Image.new("L", (w, h))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, w, h)
        assert raw == bytes(pixels)

    def test_single_pixel_black(self):
        img = Image.new("L", (1, 1), GRAY_BLACK)
        encoded = encode(img)
        raw = _roundtrip(encoded, 1, 1)
        assert raw == bytes([GRAY_BLACK])

    def test_single_pixel_white(self):
        img = Image.new("L", (1, 1), GRAY_TRANSPARENT)
        encoded = encode(img)
        raw = _roundtrip(encoded, 1, 1)
        assert raw == bytes([GRAY_TRANSPARENT])

    def test_supernote_dimensions_with_black_square(self):
        """Full-page image with a small black square in the corner."""
        pixels = [GRAY_TRANSPARENT] * (PAGE_W * PAGE_H)
        for y in range(10):
            for x in range(10):
                pixels[y * PAGE_W + x] = GRAY_BLACK
        img = Image.new("L", (PAGE_W, PAGE_H))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, PAGE_W, PAGE_H)
        assert raw == bytes(pixels)


class TestLongRuns:
    """Mixed content with long runs (multi-byte holder+continuation encoding)."""

    def test_run_exactly_128(self):
        """128-pixel run uses single normal pair (2 bytes)."""
        img = Image.new("L", (128, 1), GRAY_BLACK)
        encoded = encode(img)
        assert len(encoded) == 2
        assert encoded[0] == COLORCODE_BLACK
        assert encoded[1] == 0x7F  # 128 - 1
        raw = _roundtrip(encoded, 128, 1)
        assert raw == bytes([GRAY_BLACK]) * 128

    def test_run_exactly_129(self):
        """129-pixel run requires holder+continuation (4 bytes)."""
        img = Image.new("L", (129, 1), GRAY_BLACK)
        encoded = encode(img)
        assert len(encoded) == 4
        raw = _roundtrip(encoded, 129, 1)
        assert raw == bytes([GRAY_BLACK]) * 129

    def test_run_16384(self):
        """16384-pixel run uses a single holder+continuation chunk."""
        w = 16384
        img = Image.new("L", (w, 1), GRAY_BLACK)
        encoded = encode(img)
        assert len(encoded) == 4
        raw = _roundtrip(encoded, w, 1)
        assert raw == bytes([GRAY_BLACK]) * w

    def test_run_exceeding_16384(self):
        """Runs > 16384 are split into multiple chunks."""
        w = 20000
        img = Image.new("L", (w, 1), GRAY_TRANSPARENT)
        encoded = encode(img)
        # 16384 chunk (4 bytes) + 3616 chunk (4 bytes) = 8 bytes
        assert len(encoded) == 8
        raw = _roundtrip(encoded, w, 1)
        assert raw == bytes([GRAY_TRANSPARENT]) * w

    def test_full_page_half_and_half(self):
        """Full-page image split between black and white with long runs."""
        half = (PAGE_W * PAGE_H) // 2
        pixels = [GRAY_BLACK] * half + [GRAY_TRANSPARENT] * (PAGE_W * PAGE_H - half)
        img = Image.new("L", (PAGE_W, PAGE_H))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, PAGE_W, PAGE_H)
        assert raw == bytes(pixels)

    def test_alternating_single_pixels(self):
        """Many short alternating runs encode and decode correctly."""
        w = 200
        pixels = [GRAY_BLACK if i % 2 == 0 else GRAY_TRANSPARENT for i in range(w)]
        img = Image.new("L", (w, 1))
        img.putdata(pixels)

        encoded = encode(img)
        raw = _roundtrip(encoded, w, 1)
        assert raw == bytes(pixels)


class TestEdgeCases:

    def test_empty_image(self):
        img = Image.new("L", (0, 0))
        encoded = encode(img)
        assert encoded == b""

    def test_1bit_mode_converts(self):
        """Mode '1' images are handled correctly."""
        img = Image.new("1", (10, 10), 0)  # all black
        encoded = encode(img)
        raw = _roundtrip(encoded, 10, 10)
        assert raw == bytes([GRAY_BLACK]) * 100

    def test_rgb_mode_converts(self):
        """Non-grayscale images are converted to L before encoding."""
        img = Image.new("RGB", (10, 10), (0, 0, 0))  # all black
        encoded = encode(img)
        raw = _roundtrip(encoded, 10, 10)
        assert raw == bytes([GRAY_BLACK]) * 100

    def test_encoding_never_uses_0xff_length(self):
        """Encoder avoids 0xFF length byte (context-dependent in decoder)."""
        img = Image.new("L", (PAGE_W, PAGE_H), GRAY_BLACK)
        encoded = encode(img)
        # Walk the byte stream and verify no length byte is 0xFF
        it = iter(encoded)
        try:
            while True:
                _color = next(it)
                length = next(it)
                assert length != 0xFF, "Encoder must not produce 0xFF length marker"
                if length & 0x80:
                    # holder: next pair is the continuation
                    _color2 = next(it)
                    _lo = next(it)
        except StopIteration:
            pass

    def test_quantization_boundary_values(self):
        """Pixel values at quantization boundaries map to expected color codes."""
        test_cases = [
            (0, COLORCODE_BLACK),
            (78, COLORCODE_BLACK),       # boundary: 78 < midpoint(0,157)=78.5
            (79, COLORCODE_DARK_GRAY),   # boundary: 79 > 78.5
            (157, COLORCODE_DARK_GRAY),  # exact palette value
            (178, COLORCODE_DARK_GRAY),  # boundary: 178 < midpoint(157,201)=179
            (179, COLORCODE_DARK_GRAY),  # tie: equidistant, min() returns first
            (180, COLORCODE_GRAY),       # 180 > 179
            (201, COLORCODE_GRAY),       # exact palette value
            (227, COLORCODE_GRAY),       # boundary: 227 < midpoint(201,254)=227.5
            (228, COLORCODE_WHITE),      # 228 > 227.5
            (254, COLORCODE_WHITE),      # exact palette value
            (255, COLORCODE_BACKGROUND), # exact palette value
        ]
        for pixel_val, expected_cc in test_cases:
            img = Image.new("L", (1, 1), pixel_val)
            encoded = encode(img)
            assert encoded[0] == expected_cc, (
                f"pixel {pixel_val}: expected color code {hex(expected_cc)}, "
                f"got {hex(encoded[0])}"
            )
