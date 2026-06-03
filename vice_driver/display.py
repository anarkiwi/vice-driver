"""DISPLAY_GET / PALETTE_GET parsing + true-colour framebuffer extraction.

Where :mod:`vice_driver.screen` (SCREEN_GET, opcode 0x77) returns text-mode
screen codes, DISPLAY_GET (opcode 0x84) returns VICE's own rendered
framebuffer: an 8-bit indexed bitmap covering the full display — borders,
sprites, raster/FLD effects, any video mode — exactly what the emulator
draws. Combined with PALETTE_GET (opcode 0x91) it yields a true-colour
screen grab of *any* C64 program regardless of how it draws.

DISPLAY_GET response body::

    u32  length of the field block that follows (== 17 here)
    u16  debug_width      full bitmap width  (incl. border)
    u16  debug_height     full bitmap height
    u16  x_offset         left edge of the inner (no-border) area
    u16  y_offset         top edge of the inner area
    u16  inner_width
    u16  inner_height
    u8   bits_per_pixel   always 8 (indexed)
    u32  bitmap length
    ...  bitmap (debug_width * debug_height indexed bytes)

PALETTE_GET response body::

    u16  number of entries
    per entry:  u8 size (== 3), then `size` bytes (R, G, B)

PNG writing uses only the standard library (:mod:`zlib`), so the package
stays dependency-free.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

# Bytes of the DISPLAY_GET field block (debug_width .. bitmap length), i.e. the
# value the server reports in the leading u32. The bitmap starts at 4 + this.
DISPLAY_FIELD_BYTES = 17

RGB = tuple  # (int, int, int)


@dataclass
class DisplaySnapshot:
    """Parsed DISPLAY_GET framebuffer (still palette-indexed)."""

    debug_width: int
    debug_height: int
    x_offset: int
    y_offset: int
    inner_width: int
    inner_height: int
    bits_per_pixel: int
    bitmap: bytes  # debug_width * debug_height indexed bytes

    def to_rgb(
        self, palette: list[tuple[int, int, int]], crop_inner: bool = False
    ) -> tuple[int, int, bytes]:
        """Return ``(width, height, rgb_bytes)`` for this frame.

        ``palette`` is a list of ``(r, g, b)`` (see :func:`parse_palette_response`).
        ``crop_inner=True`` drops the border, returning only the
        ``inner_width`` x ``inner_height`` area."""
        if crop_inner:
            w, h, x0, y0 = self.inner_width, self.inner_height, self.x_offset, self.y_offset
        else:
            w, h, x0, y0 = self.debug_width, self.debug_height, 0, 0
        dw = self.debug_width
        bmp = self.bitmap
        npal = len(palette)
        out = bytearray(w * h * 3)
        di = 0
        for y in range(h):
            row = (y0 + y) * dw + x0
            for x in range(w):
                idx = bmp[row + x]
                r, g, b = palette[idx] if idx < npal else (0, 0, 0)
                out[di] = r
                out[di + 1] = g
                out[di + 2] = b
                di += 3
        return w, h, bytes(out)

    def save_png(
        self, path: str, palette: list[tuple[int, int, int]], crop_inner: bool = False
    ) -> tuple[int, int]:
        """Render to RGB via ``palette`` and write ``path`` as a PNG.

        Returns the ``(width, height)`` written."""
        w, h, rgb = self.to_rgb(palette, crop_inner=crop_inner)
        write_png(path, w, h, rgb)
        return w, h


def parse_display_response(body: bytes) -> DisplaySnapshot:
    """Parse a DISPLAY_GET response body into a :class:`DisplaySnapshot`."""
    if len(body) < 21:
        raise ValueError(f"display response too short: {len(body)} bytes")
    field_len = struct.unpack_from("<I", body, 0)[0]
    dw, dh, xo, yo, iw, ih = struct.unpack_from("<HHHHHH", body, 4)
    bpp = body[16]
    bitmap_len = struct.unpack_from("<I", body, 17)[0]
    start = 4 + field_len
    bitmap = body[start : start + bitmap_len]
    if len(bitmap) != bitmap_len:
        raise ValueError(f"display bitmap truncated: {len(bitmap)} of {bitmap_len}")
    return DisplaySnapshot(dw, dh, xo, yo, iw, ih, bpp, bitmap)


def parse_palette_response(body: bytes) -> list[tuple[int, int, int]]:
    """Parse a PALETTE_GET response body into a list of ``(r, g, b)`` tuples."""
    if len(body) < 2:
        raise ValueError(f"palette response too short: {len(body)} bytes")
    count = struct.unpack_from("<H", body, 0)[0]
    palette: list[tuple[int, int, int]] = []
    off = 2
    for _ in range(count):
        size = body[off]
        off += 1
        palette.append((body[off], body[off + 1], body[off + 2]))
        off += size
    return palette


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    """Write 8-bit RGB (``width * height * 3`` bytes) to ``path`` as a PNG.

    Pure standard library (:mod:`zlib`); no Pillow dependency."""
    if len(rgb) != width * height * 3:
        raise ValueError(f"rgb is {len(rgb)} bytes, expected {width * height * 3}")

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # per-scanline filter type 0 (None)
        raw += rgb[y * stride : (y + 1) * stride]
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)
