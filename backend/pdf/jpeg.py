"""Parse JPEG stream structure for visualization.

Returns segment metadata and a structure map for rendering a colour-coded
bar and an annotated hex dump — analogous to what icc.py does for ICC profiles.
"""
from __future__ import annotations

import struct

# marker_byte → (short_name, long description)
JPEG_MARKERS: dict[int, tuple[str, str]] = {
    0xC0: ('SOF0',  'Baseline DCT Frame'),
    0xC1: ('SOF1',  'Extended Sequential DCT Frame'),
    0xC2: ('SOF2',  'Progressive DCT Frame'),
    0xC3: ('SOF3',  'Lossless Sequential Frame'),
    0xC4: ('DHT',   'Huffman Table'),
    0xC5: ('SOF5',  'Differential Sequential Frame'),
    0xC6: ('SOF6',  'Differential Progressive Frame'),
    0xC7: ('SOF7',  'Differential Lossless Frame'),
    0xCA: ('SOF10', 'Progressive Frame (Arithmetic)'),
    0xCB: ('SOF11', 'Lossless Frame (Arithmetic)'),
    0xCC: ('DAC',   'Arithmetic Conditioning Table'),
    0xD8: ('SOI',   'Start of Image'),
    0xD9: ('EOI',   'End of Image'),
    0xDA: ('SOS',   'Start of Scan'),
    0xDB: ('DQT',   'Quantization Table'),
    0xDC: ('DNL',   'Define Number of Lines'),
    0xDD: ('DRI',   'Restart Interval'),
    0xE0: ('APP0',  'JFIF Application Data'),
    0xE1: ('APP1',  'EXIF / XMP Data'),
    0xE2: ('APP2',  'Application Data 2'),
    0xE3: ('APP3',  'Application Data 3'),
    0xE4: ('APP4',  'Application Data 4'),
    0xE5: ('APP5',  'Application Data 5'),
    0xE6: ('APP6',  'Application Data 6'),
    0xE7: ('APP7',  'Application Data 7'),
    0xE8: ('APP8',  'Application Data 8'),
    0xE9: ('APP9',  'Application Data 9'),
    0xEA: ('APP10', 'Application Data 10'),
    0xEB: ('APP11', 'Application Data 11'),
    0xEC: ('APP12', 'Application Data 12'),
    0xED: ('APP13', 'Photoshop / IPTC'),
    0xEE: ('APP14', 'Adobe Application Data'),
    0xEF: ('APP15', 'Application Data 15'),
    0xFE: ('COM',   'Comment'),
}

# colour key used by the structure bar in the frontend
_MARKER_COLOR: dict[str, str] = {
    'SOI': 'soi', 'EOI': 'eoi',
    'SOF0': 'frame', 'SOF1': 'frame', 'SOF2': 'frame', 'SOF3': 'frame',
    'SOF5': 'frame', 'SOF6': 'frame', 'SOF7': 'frame',
    'SOF10': 'frame', 'SOF11': 'frame',
    'DHT': 'huffman',
    'DQT': 'quant',
    'SOS': 'sos',
    'scan_data': 'scan',
    'APP0': 'app', 'APP1': 'app', 'APP2': 'app', 'APP3': 'app',
    'APP4': 'app', 'APP5': 'app', 'APP6': 'app', 'APP7': 'app',
    'APP8': 'app', 'APP9': 'app', 'APP10': 'app', 'APP11': 'app',
    'APP12': 'app', 'APP13': 'app', 'APP14': 'app', 'APP15': 'app',
    'DRI': 'misc', 'DNL': 'misc', 'DAC': 'misc', 'COM': 'misc',
}


# ---------------------------------------------------------------------------
# Summary helpers per marker type
# ---------------------------------------------------------------------------

def _marker_summary(mb: int, name: str, payload: bytes) -> str:
    """One-line human-readable summary of a JPEG marker's payload."""
    if name in ('SOF0', 'SOF1', 'SOF2', 'SOF3') and len(payload) >= 6:
        prec = payload[0]
        h = struct.unpack('>H', payload[1:3])[0]
        w = struct.unpack('>H', payload[3:5])[0]
        nc = payload[5]
        comp_label = {1: 'Grayscale', 3: 'YCbCr', 4: 'CMYK'}.get(nc, f'{nc} components')
        return f'{w}×{h}, {nc} component{"s" if nc != 1 else ""} ({comp_label}), {prec}-bit'

    if name == 'DQT':
        parts: list[str] = []
        i = 0
        while i < len(payload):
            prec_id = payload[i]
            tid = prec_id & 0x0F
            is16 = (prec_id >> 4) == 1
            label = 'Luma' if tid == 0 else ('Chroma' if tid == 1 else f'Table {tid}')
            parts.append(f'{label} (ID={tid}, {"16-bit" if is16 else "8-bit"})')
            i += 1 + (128 if is16 else 64)
            if i > len(payload):
                break
        return ', '.join(parts)

    if name == 'DHT':
        parts = []
        i = 0
        while i < len(payload):
            tc_th = payload[i]
            tc = (tc_th >> 4) & 0x01   # 0 = DC, 1 = AC
            th = tc_th & 0x0F
            parts.append(f'{"AC" if tc else "DC"} table {th}')
            if i + 17 > len(payload):
                break
            n_codes = sum(payload[i + 1:i + 17])
            i += 1 + 16 + n_codes
            if i > len(payload):
                break
        return ', '.join(parts)

    if name == 'SOS' and len(payload) >= 1:
        nc = payload[0]
        return f'{nc} component{"s" if nc != 1 else ""} in scan'

    if name == 'APP0' and payload[:4] == b'JFIF':
        if len(payload) >= 9:
            major, minor = payload[5], payload[6]
            units_byte = payload[7]
            xd = struct.unpack('>H', payload[8:10])[0] if len(payload) >= 10 else 0
            yd = struct.unpack('>H', payload[10:12])[0] if len(payload) >= 12 else 0
            unit_str = ['(no units)', 'dpi', 'dpcm'][units_byte] if units_byte < 3 else '?'
            return f'JFIF {major}.{minor:02d}, {xd}×{yd} {unit_str}'

    if name == 'APP0' and payload[:5] == b'JFXX\x00':
        return 'JFIF extension'

    if name == 'APP1':
        if payload[:6] == b'Exif\x00\x00':
            return 'EXIF metadata'
        if b'xpacket' in payload[:20] or payload[:5] == b'<?xpa':
            return 'XMP metadata'

    if name == 'APP2' and payload[:12] == b'ICC_PROFILE\x00':
        ci = payload[12] if len(payload) > 12 else '?'
        ct = payload[13] if len(payload) > 13 else '?'
        return f'Embedded ICC Profile chunk {ci}/{ct}'

    if name == 'APP13' and b'Photoshop' in payload[:20]:
        return 'Photoshop / IPTC metadata'

    if name == 'APP14' and payload[:5] == b'Adobe':
        t = payload[11] if len(payload) > 11 else None
        tname = {0: 'Unknown (RGB/CMYK)', 1: 'YCbCr', 2: 'YCCK'}.get(t, str(t)) if t is not None else '?'
        return f'Adobe, color transform: {tname}'

    if name == 'DRI' and len(payload) >= 2:
        iv = struct.unpack('>H', payload[:2])[0]
        return f'Restart every {iv} MCUs'

    if name == 'COM':
        try:
            return payload.decode('utf-8', errors='replace').strip('\x00')[:100]
        except Exception:
            pass

    return ''


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_jpeg(data: bytes) -> dict | None:
    """Parse JPEG marker structure from *data*.

    Returns a dict with:
      segments  – list of segment dicts (marker, name, desc, offset, size,
                  summary, color, is_scan)
      structure – list of {label, offset, size, color, is_scan} for the bar
      frame_info – dict from the first SOF marker, or None
    Returns None if *data* is not a valid JPEG stream.
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None

    segments: list[dict] = []
    frame_info: dict | None = None
    pos = 0
    total = len(data)

    while pos + 1 < total:
        if data[pos] != 0xFF:
            break  # stream is corrupt or we're inside scan data — shouldn't happen here

        mb = data[pos + 1]
        seg_start = pos

        # ---- SOI -------------------------------------------------------
        if mb == 0xD8:
            segments.append({
                'marker': 'FFD8', 'name': 'SOI', 'desc': 'Start of Image',
                'offset': seg_start, 'size': 2, 'summary': '',
                'color': 'soi', 'is_scan': False,
            })
            pos += 2
            continue

        # ---- EOI -------------------------------------------------------
        if mb == 0xD9:
            segments.append({
                'marker': 'FFD9', 'name': 'EOI', 'desc': 'End of Image',
                'offset': seg_start, 'size': 2, 'summary': '',
                'color': 'eoi', 'is_scan': False,
            })
            break

        # ---- RSTn ------------------------------------------------------
        if 0xD0 <= mb <= 0xD7:
            n = mb - 0xD0
            segments.append({
                'marker': f'FF{mb:02X}', 'name': f'RST{n}', 'desc': f'Restart Marker {n}',
                'offset': seg_start, 'size': 2, 'summary': '',
                'color': 'misc', 'is_scan': False,
            })
            pos += 2
            continue

        # ---- Markers with a length field --------------------------------
        if pos + 3 >= total:
            break

        length = struct.unpack('>H', data[pos + 2:pos + 4])[0]  # includes the 2-byte field itself
        seg_size = 2 + length                                    # 0xFF+marker + length field + payload
        payload = data[pos + 4: pos + 2 + length]               # length - 2 bytes of actual data

        name, desc = JPEG_MARKERS.get(mb, (f'FF{mb:02X}', 'Unknown Marker'))
        summary = _marker_summary(mb, name, payload)
        color = _MARKER_COLOR.get(name, 'misc')

        # Capture frame dimensions from first SOF marker
        if name in ('SOF0', 'SOF1', 'SOF2', 'SOF3') and frame_info is None and len(payload) >= 6:
            frame_info = {
                'type': name,
                'precision': payload[0],
                'height': struct.unpack('>H', payload[1:3])[0],
                'width':  struct.unpack('>H', payload[3:5])[0],
                'components': payload[5],
            }

        segments.append({
            'marker': f'FF{mb:02X}', 'name': name, 'desc': desc,
            'offset': seg_start, 'size': seg_size, 'summary': summary,
            'color': color, 'is_scan': False,
        })

        pos += seg_size

        # ---- SOS: compressed scan data immediately follows the header ---
        if mb == 0xDA:
            scan_start = pos
            scan_end = total  # default: data runs to EOF

            i = scan_start
            while i + 1 < total:
                if data[i] == 0xFF:
                    nb = data[i + 1]
                    if nb == 0x00:
                        i += 2; continue          # byte stuffing: 0xFF 0x00 → 0xFF
                    if 0xD0 <= nb <= 0xD7:
                        i += 2; continue          # RST marker inside scan
                    scan_end = i                   # real marker found
                    break
                i += 1

            scan_size = scan_end - scan_start
            if scan_size > 0:
                segments.append({
                    'marker': '', 'name': 'scan_data', 'desc': 'Compressed Scan Data',
                    'offset': scan_start, 'size': scan_size,
                    'summary': f'{scan_size} bytes of entropy-coded image data',
                    'color': 'scan', 'is_scan': True,
                })
            pos = scan_end

    if not segments:
        return None

    structure = [
        {
            'label': (
                f"{s['marker']} {s['name']}  {s['desc']}"
                + (f" — {s['summary']}" if s['summary'] else '')
            ),
            'offset': s['offset'],
            'size': s['size'],
            'color': s['color'],
            'is_scan': s['is_scan'],
        }
        for s in segments
    ]

    return {
        'segments': segments,
        'structure': structure,
        'frame_info': frame_info,
    }
