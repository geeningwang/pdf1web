"""Parse ICC profile binary data and return structured data for visualization."""
from __future__ import annotations
import struct


def parse_icc_profile(data: bytes) -> dict | None:
    """Return structured ICC profile data, or None if *data* is not a valid profile."""
    if len(data) < 132:
        return None
    # The 'acsp' signature lives at offset 36
    if data[36:40] != b'acsp':
        return None

    color_space = data[16:20].decode('latin1').strip()
    pcs = data[20:24].decode('latin1').strip()

    # Build tag directory
    tag_count = struct.unpack('>I', data[128:132])[0]
    tags: dict[str, tuple[int, int]] = {}
    for i in range(min(tag_count, 200)):
        base = 132 + i * 12
        if base + 12 > len(data):
            break
        try:
            sig = data[base:base + 4].decode('latin1')
        except Exception:
            continue
        offset = struct.unpack('>I', data[base + 4:base + 8])[0]
        size = struct.unpack('>I', data[base + 8:base + 12])[0]
        if offset + size <= len(data):
            tags[sig] = (offset, size)

    # ------------------------------------------------------------------ helpers

    def read_xyz(tag: str) -> list[float] | None:
        if tag not in tags:
            return None
        off, _ = tags[tag]
        if off + 20 > len(data):
            return None
        x = struct.unpack('>i', data[off + 8:off + 12])[0] / 65536.0
        y = struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0
        z = struct.unpack('>i', data[off + 16:off + 20])[0] / 65536.0
        return [x, y, z]

    def read_trc(tag: str) -> list[float] | None:
        """Return a list of ~256 normalized (0–1) output values for input 0–1."""
        if tag not in tags:
            return None
        off, _ = tags[tag]
        if off + 12 > len(data):
            return None
        sig = data[off:off + 4]
        if sig == b'curv':
            count = struct.unpack('>I', data[off + 8:off + 12])[0]
            if count == 0:
                return [0.0, 1.0]  # identity
            if count == 1:
                # Single u8.8 gamma value
                gamma = struct.unpack('>H', data[off + 12:off + 14])[0] / 256.0
                return [pow(i / 255.0, gamma) for i in range(256)]
            raw = struct.unpack(f'>{count}H', data[off + 12:off + 12 + count * 2])
            step = max(1, count // 256)
            return [raw[i] / 65535.0 for i in range(0, count, step)]
        if sig == b'para':
            # Parametric curve — only decode type 0 (simple gamma) for now
            if off + 16 > len(data):
                return None
            ftype = struct.unpack('>H', data[off + 8:off + 10])[0]
            if ftype == 0:
                gamma = struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0
                return [pow(i / 255.0, gamma) for i in range(256)]
        return None

    def read_desc(tag: str) -> str | None:
        if tag not in tags:
            return None
        off, _ = tags[tag]
        if off + 8 > len(data):
            return None
        sig = data[off:off + 4]
        if sig == b'desc':
            ascii_len = struct.unpack('>I', data[off + 8:off + 12])[0]
            if ascii_len > 0 and off + 12 + ascii_len <= len(data):
                return (
                    data[off + 12:off + 12 + ascii_len]
                    .rstrip(b'\x00')
                    .decode('latin1', errors='replace')
                )
        elif sig == b'mluc':
            if off + 24 > len(data):
                return None
            rec_offset = struct.unpack('>I', data[off + 20:off + 24])[0]
            rec_length = struct.unpack('>I', data[off + 16:off + 20])[0]
            start = off + rec_offset
            if start + rec_length <= len(data):
                return data[start:start + rec_length].decode('utf-16-be', errors='replace')
        return None

    def xyz_d50_to_srgb8(xyz: list[float]) -> list[int]:
        """Convert XYZ (D50-adapted) to display sRGB [0–255]."""
        x, y, z = xyz
        # Bradford D50 → D65
        rx =  0.9555766 * x - 0.0230393 * y + 0.0631636 * z
        ry = -0.0282895 * x + 1.0099416 * y + 0.0210077 * z
        rz =  0.0122982 * x - 0.0204830 * y + 1.3299098 * z
        # XYZ D65 → linear sRGB
        lr =  3.2404542 * rx - 1.5371385 * ry - 0.4985314 * rz
        lg = -0.9692660 * rx + 1.8760108 * ry + 0.0415560 * rz
        lb =  0.0556434 * rx - 0.2040259 * ry + 1.0572252 * rz

        def gamma(v: float) -> int:
            v = max(0.0, min(1.0, v))
            e = 12.92 * v if v <= 0.0031308 else 1.055 * pow(v, 1.0 / 2.4) - 0.055
            return round(e * 255)

        return [gamma(lr), gamma(lg), gamma(lb)]

    # ------------------------------------------------------------------ assemble

    r_xyz = read_xyz('rXYZ')
    g_xyz = read_xyz('gXYZ')
    b_xyz = read_xyz('bXYZ')
    w_xyz = read_xyz('wtpt')

    return {
        'description': read_desc('desc'),
        'color_space': color_space,
        'pcs': pcs,
        'white_point': w_xyz,
        'primaries': {
            'r_xyz': r_xyz,
            'g_xyz': g_xyz,
            'b_xyz': b_xyz,
            'r_display': xyz_d50_to_srgb8(r_xyz) if r_xyz else None,
            'g_display': xyz_d50_to_srgb8(g_xyz) if g_xyz else None,
            'b_display': xyz_d50_to_srgb8(b_xyz) if b_xyz else None,
        },
        'trc': {
            'r': read_trc('rTRC'),
            'g': read_trc('gTRC'),
            'b': read_trc('bTRC'),
        },
    }
