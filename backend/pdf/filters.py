"""
Stream filter decoders — port of the C++ Filters.
Python has built-in zlib so FlateDecode works without DLLs.
"""
from __future__ import annotations

import io
import zlib


def flat_decode(data: bytes) -> bytes | None:
    """Decompress FlateDecode (zlib/deflate) stream."""
    if not data:
        return b""
    # Try with automatic header detection (wbits=47 = auto zlib/gzip)
    for wbits in (47, 15, -15):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
    return None


def dct_decode(data: bytes) -> bytes | None:
    """Decode DCTDecode (JPEG) stream to raw pixel bytes via PIL."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return img.tobytes()
    except Exception:
        return None


def ascii_hex_decode(data: bytes) -> bytes | None:
    """Decode ASCIIHexDecode stream."""
    result: list[int] = []
    i = 0
    while i < len(data):
        c = data[i]
        if c in b" \t\r\n":
            i += 1
            continue
        if c == ord(">"):
            break
        hi = _hex_val(c)
        i += 1
        lo = 0
        while i < len(data) and data[i] in b" \t\r\n":
            i += 1
        if i < len(data) and data[i] != ord(">"):
            v = _hex_val(data[i])
            if v >= 0:
                lo = v
                i += 1
        if hi >= 0:
            result.append((hi << 4) | lo)
    return bytes(result)


def ascii85_decode(data: bytes) -> bytes | None:
    """Decode ASCII85Decode stream."""
    result: list[int] = []
    i = 0
    group: list[int] = []
    while i < len(data):
        c = data[i]
        i += 1
        if c in b" \t\r\n\x00":
            continue
        if c == ord("~"):
            break  # end-of-data marker ~>
        if c == ord("z"):
            result.extend([0, 0, 0, 0])
            continue
        group.append(c - 33)
        if len(group) == 5:
            val = (
                group[0] * (85**4)
                + group[1] * (85**3)
                + group[2] * (85**2)
                + group[3] * 85
                + group[4]
            )
            result.extend([(val >> 24) & 0xFF, (val >> 16) & 0xFF,
                            (val >> 8) & 0xFF, val & 0xFF])
            group = []
    # Partial group
    if group:
        n = len(group)
        while len(group) < 5:
            group.append(84)  # pad with 'u' (ASCII 117 - 33)
        val = (
            group[0] * (85**4)
            + group[1] * (85**3)
            + group[2] * (85**2)
            + group[3] * 85
            + group[4]
        )
        for j in range(n - 1):
            result.append((val >> (24 - j * 8)) & 0xFF)
    return bytes(result)


def _hex_val(c: int) -> int:
    if ord("0") <= c <= ord("9"):
        return c - ord("0")
    if ord("a") <= c <= ord("f"):
        return c - ord("a") + 10
    if ord("A") <= c <= ord("F"):
        return c - ord("A") + 10
    return -1
