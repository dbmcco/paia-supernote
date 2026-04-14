"""RATTA_RLE encoder for Supernote .note format.

Encodes PIL images into the RATTA_RLE bitmap format used by Supernote devices.
The format is the inverse of supernotelib's RattaRleDecoder.

Encoding pairs (colorcode_byte, length_byte):
  Normal:   (cc, N-1)              for 1..128 pixels   (N-1 in 0x00..0x7F)
  Holder:   (cc, hi|0x80, cc, lo)  for 129..16384 pixels
  Special:  (cc, 0xFF)             for exactly 16384 pixels (avoided by encoder)

The 0xFF marker is not used by this encoder because its decoded length
depends on context (all_blank flag in supernotelib). Instead, runs up to
16384 use the holder+continuation encoding, and longer runs are chunked.
"""

from __future__ import annotations

from PIL import Image

# Color codes (match supernotelib.decoder.RattaRleDecoder)
COLORCODE_BLACK = 0x61
COLORCODE_BACKGROUND = 0x62
COLORCODE_DARK_GRAY = 0x63
COLORCODE_GRAY = 0x64
COLORCODE_WHITE = 0x65

SPECIAL_LENGTH_MARKER = 0xFF
LENGTH_MARKER = SPECIAL_LENGTH_MARKER
MAX_SINGLE_BYTE_LENGTH = 128

# Max pixels encodable in a single chunk (holder+continuation)
_MAX_CHUNK = 16384

# supernotelib default grayscale palette values
_GRAY_BLACK = 0x00       # 0
_GRAY_DARK_GRAY = 0x9D   # 157
_GRAY_GRAY = 0xC9        # 201
_GRAY_WHITE = 0xFE       # 254
_GRAY_BACKGROUND = 0xFF  # 255

# Build 256-entry LUT: grayscale pixel value -> RATTA_RLE color code
# Each pixel quantizes to the nearest palette value by Euclidean distance.
_PIXEL_TO_COLORCODE: list[int] = [0] * 256
_PALETTE = [
    (_GRAY_BLACK, COLORCODE_BLACK),
    (_GRAY_DARK_GRAY, COLORCODE_DARK_GRAY),
    (_GRAY_GRAY, COLORCODE_GRAY),
    (_GRAY_WHITE, COLORCODE_WHITE),
    (_GRAY_BACKGROUND, COLORCODE_BACKGROUND),
]
for _v in range(256):
    _, _cc = min(_PALETTE, key=lambda t: abs(_v - t[0]))
    _PIXEL_TO_COLORCODE[_v] = _cc


def _encode_run(color: int, length: int, out: bytearray) -> None:
    """Encode a run of identical pixels.

    Chunked into segments of at most _MAX_CHUNK (16384) pixels:
      - 1..128:    normal pair (2 bytes)
      - 129..16384: holder + continuation (4 bytes)
    """
    while length > 0:
        chunk = min(length, _MAX_CHUNK)
        if chunk <= 128:
            out.append(color)
            out.append(chunk - 1)
        else:
            # holder+continuation: total = ((hi & 0x7f) + 1) * 128 + lo + 1
            hi_val = (chunk - 1) // 128   # 1..127
            lo_val = (chunk - 1) % 128    # 0..127
            out.append(color)
            out.append((hi_val - 1) | 0x80)
            out.append(color)
            out.append(lo_val)
        length -= chunk


def encode(image: Image.Image) -> bytes:
    """Encode a PIL image to RATTA_RLE format.

    Args:
        image: PIL Image (1-bit or grayscale, typically 1404x1872)

    Returns:
        RATTA_RLE encoded bytes for a Supernote MAINLAYER
    """
    if image.mode != "L":
        image = image.convert("L")

    pixels = image.getdata()
    total = len(pixels)
    if total == 0:
        return b""

    lut = _PIXEL_TO_COLORCODE
    out = bytearray()
    cur_color = lut[pixels[0]]
    cur_len = 1

    for i in range(1, total):
        cc = lut[pixels[i]]
        if cc == cur_color:
            cur_len += 1
        else:
            _encode_run(cur_color, cur_len, out)
            cur_color = cc
            cur_len = 1

    _encode_run(cur_color, cur_len, out)
    return bytes(out)


def decode(data: bytes, width: int, height: int) -> Image.Image:
    """Decode RATTA_RLE data to a PIL Image for roundtrip testing.

    This mirrors the logic of supernotelib's RattaRleDecoder.

    Args:
        data: RATTA_RLE encoded bytes
        width: Target image width in pixels
        height: Target image height in pixels

    Returns:
        PIL Image in mode 'L' (grayscale)
    """
    # Colorcode to grayscale value mapping
    colormap = {
        COLORCODE_BLACK: _GRAY_BLACK,
        COLORCODE_DARK_GRAY: _GRAY_DARK_GRAY,
        COLORCODE_GRAY: _GRAY_GRAY,
        COLORCODE_WHITE: _GRAY_WHITE,
        COLORCODE_BACKGROUND: _GRAY_BACKGROUND,
    }

    expected_pixels = width * height
    pixels = []
    it = iter(data)
    holder = None

    try:
        while len(pixels) < expected_pixels:
            color = next(it)
            length_byte = next(it)

            if holder is not None:
                # Process held pair + current pair as multi-byte run
                prev_color, prev_len = holder
                holder = None
                if color == prev_color:
                    # Combined run: total = ((hi & 0x7f) + 1) * 128 + lo + 1
                    total_length = ((prev_len & 0x7f) + 1) * 128 + length_byte + 1
                    gray_val = colormap.get(color, 128)
                    pixels.extend([gray_val] * total_length)
                    continue
                else:
                    # Flush previous holder as single run, then process current normally
                    prev_total = ((prev_len & 0x7f) + 1) * 128
                    prev_gray = colormap.get(prev_color, 128)
                    pixels.extend([prev_gray] * prev_total)
                    # Fall through to process current pair

            if length_byte == SPECIAL_LENGTH_MARKER:
                # Special marker: decode as 16384 pixels
                length = 16384
                gray_val = colormap.get(color, 128)
                pixels.extend([gray_val] * length)
            elif length_byte & 0x80:
                # High bit set: this is a holder, wait for continuation
                holder = (color, length_byte)
            else:
                # Normal run: length = length_byte + 1
                length = length_byte + 1
                gray_val = colormap.get(color, 128)
                pixels.extend([gray_val] * length)

    except StopIteration:
        # Handle incomplete data at end
        if holder is not None:
            # Use holder to fill remaining pixels
            prev_color, prev_len = holder
            remaining = expected_pixels - len(pixels)
            if remaining > 0:
                gray_val = colormap.get(prev_color, 128)
                pixels.extend([gray_val] * remaining)

    # Trim to exact size or pad if needed
    if len(pixels) > expected_pixels:
        pixels = pixels[:expected_pixels]
    elif len(pixels) < expected_pixels:
        pixels.extend([_GRAY_BACKGROUND] * (expected_pixels - len(pixels)))

    # Create image
    image = Image.new('L', (width, height))
    image.putdata(pixels)
    return image
