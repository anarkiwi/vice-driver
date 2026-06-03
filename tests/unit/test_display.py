"""Unit tests for vice_driver.display — DISPLAY_GET / PALETTE_GET parsing,
RGB extraction and the stdlib PNG writer. No emulator required; synthetic
responses are built in-process."""

from __future__ import annotations

import struct
import zlib

import pytest

from vice_driver.display import (
    DISPLAY_FIELD_BYTES,
    DisplaySnapshot,
    parse_display_response,
    parse_palette_response,
    write_png,
)


def _build_display_body(
    bitmap: bytes,
    dw: int,
    dh: int,
    xo: int = 0,
    yo: int = 0,
    iw: int | None = None,
    ih: int | None = None,
    field_len: int = DISPLAY_FIELD_BYTES,
) -> bytes:
    iw = dw if iw is None else iw
    ih = dh if ih is None else ih
    return (
        struct.pack("<I", field_len)
        + struct.pack("<HHHHHH", dw, dh, xo, yo, iw, ih)
        + bytes([8])  # bits per pixel
        + struct.pack("<I", len(bitmap))
        + bitmap
    )


def _build_palette_body(colors: list[tuple[int, int, int]]) -> bytes:
    body = struct.pack("<H", len(colors))
    for r, g, b in colors:
        body += bytes([3, r, g, b])
    return body


def test_parse_display_response_dimensions_and_bitmap() -> None:
    bitmap = bytes(range(4 * 3))  # 4x3 indexed
    snap = parse_display_response(_build_display_body(bitmap, dw=4, dh=3, xo=1, yo=1, iw=2, ih=1))
    assert (snap.debug_width, snap.debug_height) == (4, 3)
    assert (snap.x_offset, snap.y_offset) == (1, 1)
    assert (snap.inner_width, snap.inner_height) == (2, 1)
    assert snap.bits_per_pixel == 8
    assert snap.bitmap == bitmap


def test_parse_display_response_too_short() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse_display_response(b"\x00\x00")


def test_parse_display_response_truncated_bitmap() -> None:
    # bitmap_len field claims 12 bytes (4x3) but only 4 follow.
    body = (
        struct.pack("<I", DISPLAY_FIELD_BYTES)
        + struct.pack("<HHHHHH", 4, 3, 0, 0, 4, 3)
        + bytes([8])
        + struct.pack("<I", 12)
        + b"\x00\x00\x00\x00"
    )
    with pytest.raises(ValueError, match="truncated"):
        parse_display_response(body)


def test_parse_palette_response() -> None:
    pal = parse_palette_response(_build_palette_body([(0, 0, 0), (255, 1, 2), (3, 4, 5)]))
    assert pal == [(0, 0, 0), (255, 1, 2), (3, 4, 5)]


def test_parse_palette_response_too_short() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse_palette_response(b"\x00")


def test_to_rgb_full_and_crop() -> None:
    # 2x2 indices; palette maps each to a distinct colour.
    bitmap = bytes([0, 1, 2, 3])
    snap = parse_display_response(_build_display_body(bitmap, dw=2, dh=2, xo=1, yo=1, iw=1, ih=1))
    pal = [(10, 10, 10), (20, 20, 20), (30, 30, 30), (40, 40, 40)]
    w, h, rgb = snap.to_rgb(pal)
    assert (w, h) == (2, 2)
    assert rgb == bytes([10, 10, 10, 20, 20, 20, 30, 30, 30, 40, 40, 40])
    # crop to the inner 1x1 at (1,1) -> index 3 -> colour 40.
    w, h, rgb = snap.to_rgb(pal, crop_inner=True)
    assert (w, h) == (1, 1)
    assert rgb == bytes([40, 40, 40])


def test_to_rgb_index_out_of_palette_falls_back_to_black() -> None:
    snap = parse_display_response(_build_display_body(bytes([5]), dw=1, dh=1))
    _, _, rgb = snap.to_rgb([(1, 2, 3)])  # index 5 not in palette
    assert rgb == bytes([0, 0, 0])


def test_write_png_signature_and_ihdr(tmp_path) -> None:
    path = tmp_path / "out.png"
    write_png(str(path), 2, 1, bytes([1, 2, 3, 4, 5, 6]))
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    width, height, depth, ctype = struct.unpack(">IIBB", data[16:26])
    assert (width, height, depth, ctype) == (2, 1, 8, 2)  # 8-bit truecolour


def test_write_png_round_trips_pixels(tmp_path) -> None:
    path = tmp_path / "px.png"
    rgb = bytes([0, 0, 0, 255, 255, 255, 10, 20, 30, 40, 50, 60])
    write_png(str(path), 2, 2, rgb)
    data = path.read_bytes()
    # pull the IDAT chunk back out and inflate it, drop per-row filter bytes.
    idat_start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[idat_start - 8 : idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start : idat_start + length])
    stride = 2 * 3
    recovered = b"".join(raw[i + 1 : i + 1 + stride] for i in range(0, len(raw), stride + 1))
    assert recovered == rgb


def test_write_png_rejects_wrong_length(tmp_path) -> None:
    with pytest.raises(ValueError, match="expected"):
        write_png(str(tmp_path / "bad.png"), 2, 2, b"\x00\x00\x00")


def test_save_png_uses_palette(tmp_path) -> None:
    snap = DisplaySnapshot(1, 1, 0, 0, 1, 1, 8, bytes([1]))
    out = tmp_path / "s.png"
    w, h = snap.save_png(str(out), [(0, 0, 0), (9, 9, 9)])
    assert (w, h) == (1, 1)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
