"""Parse ICC profile binary data and return structured data for visualization."""
from __future__ import annotations
import struct

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_TAG_NAMES: dict[str, str] = {
    'A2B0': 'AToB LUT 0',   'A2B1': 'AToB LUT 1',   'A2B2': 'AToB LUT 2',
    'B2A0': 'BToA LUT 0',   'B2A1': 'BToA LUT 1',   'B2A2': 'BToA LUT 2',
    'bkpt': 'Black Point',   'bTRC': 'Blue TRC',      'bXYZ': 'Blue Primary XYZ',
    'calt': 'Calibration Date', 'chad': 'Chromatic Adaptation',
    'chrm': 'Chromaticity',  'clro': 'Colorant Order', 'clrt': 'Colorant Table',
    'cprt': 'Copyright',     'desc': 'Profile Description',
    'devs': 'Device Settings', 'dmdd': 'Device Model Desc.',
    'dmnd': 'Device Manufacturer Desc.',
    'gamt': 'Gamut',         'gTRC': 'Green TRC',     'gXYZ': 'Green Primary XYZ',
    'kTRC': 'Gray TRC',      'lumi': 'Luminance',     'meas': 'Measurement',
    'mft1': 'LUT8',          'mft2': 'LUT16',
    'ncl2': 'Named Colors 2',
    'pre0': 'Preview 0',     'pre1': 'Preview 1',     'pre2': 'Preview 2',
    'resp': 'Output Response', 'rTRC': 'Red TRC',     'rXYZ': 'Red Primary XYZ',
    'scrd': 'Screening Desc.', 'scrn': 'Screening',
    'tech': 'Technology',    'vued': 'Viewing Conditions Desc.',
    'view': 'Viewing Conditions', 'wtpt': 'White Point',
}

# Color group per tag sig — controls structure-bar color
_TAG_COLOR: dict[str, str] = {
    'desc': 'desc', 'cprt': 'desc', 'dmnd': 'desc', 'dmdd': 'desc', 'vued': 'desc',
    'wtpt': 'xyz',  'bkpt': 'xyz',  'rXYZ': 'xyz',  'gXYZ': 'xyz',  'bXYZ': 'xyz',
    'lumi': 'xyz',
    'rTRC': 'trc',  'gTRC': 'trc',  'bTRC': 'trc',  'kTRC': 'trc',
    'tech': 'tech', 'meas': 'tech', 'view': 'tech',
}

_TECH_NAMES: dict[str, str] = {
    'CRT ': 'Cathode Ray Tube',    'kpcd': 'Photo CD',
    'dcam': 'Digital Camera',      'fscn': 'Film Scanner',
    'rscn': 'Reflective Scanner',  'ijet': 'Ink Jet Printer',
    'twax': 'Thermal Wax Printer', 'epho': 'Electrophotographic',
    'esta': 'Electrostatic',       'dsub': 'Dye Sublimation',
    'rpho': 'Photographic Paper',  'fprn': 'Film Writer',
    'vidm': 'Video Monitor',       'vidc': 'Video Camera',
    'pjtv': 'Projection TV',       'AMD ': 'Active Matrix Display',
    'imgs': 'Photo Imagesetter',   'grav': 'Gravure',
    'offs': 'Offset Lithography',  'silk': 'Silkscreen',
    'flex': 'Flexography',
}

_OBSERVER_NAMES: dict[int, str] = {
    0: 'Unknown', 1: 'CIE 1931 2°', 2: 'CIE 1964 10°',
}

_ILLUMINANT_NAMES: dict[int, str] = {
    0: 'Unknown', 1: 'D50', 2: 'D65', 3: 'D93',
    4: 'F2', 5: 'D55', 6: 'A', 7: 'Equi-Power (E)', 8: 'F8',
}

_PROFILE_CLASS: dict[str, str] = {
    'scnr': 'Input (Scanner)', 'mntr': 'Display Monitor',
    'prtr': 'Output (Printer)', 'link': 'Device Link',
    'spac': 'Color Space',     'abst': 'Abstract', 'nmcl': 'Named Color',
}



def parse_icc_profile(data: bytes) -> dict | None:
    """Return structured ICC profile data, or None if *data* is not a valid profile."""
    if len(data) < 132:
        return None
    # The 'acsp' signature lives at offset 36
    if data[36:40] != b'acsp':
        return None

    profile_class = data[12:16].decode('latin1', errors='replace').strip()
    color_space = data[16:20].decode('latin1').strip()
    pcs = data[20:24].decode('latin1').strip()

    # Build raw tag directory
    tag_count = struct.unpack('>I', data[128:132])[0]
    raw_tags: list[tuple[str, int, int]] = []  # (sig, offset, size)
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
            raw_tags.append((sig, offset, size))

    # ------------------------------------------------------------------ helpers

    def get_type_sig(off: int) -> str:
        if off + 4 <= len(data):
            try:
                return data[off:off + 4].decode('latin1')
            except Exception:
                pass
        return '????'

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

    def read_desc_str(tag: str) -> str | None:
        if tag not in tags:
            return None
        off, sz = tags[tag]
        if off + 8 > len(data):
            return None
        sig = data[off:off + 4]
        if sig == b'desc':
            ascii_len = struct.unpack('>I', data[off + 8:off + 12])[0]
            if 0 < ascii_len < 1024 and off + 12 + ascii_len <= len(data):
                return data[off + 12:off + 12 + ascii_len].rstrip(b'\x00').decode('latin1', errors='replace')
        elif sig == b'mluc':
            if off + 24 > len(data):
                return None
            rec_length = struct.unpack('>I', data[off + 16:off + 20])[0]
            rec_offset = struct.unpack('>I', data[off + 20:off + 24])[0]
            start = off + rec_offset
            if rec_length < 2048 and start + rec_length <= len(data):
                return data[start:start + rec_length].decode('utf-16-be', errors='replace')
        elif sig == b'text':
            # 'text' type: no length field — raw string after 8-byte header
            return data[off + 8:off + sz].rstrip(b'\x00').decode('latin1', errors='replace')
        return None

    def _trc_summary_for(tag: str) -> str:
        if tag not in tags:
            return ''
        off, _ = tags[tag]
        if off + 12 > len(data):
            return ''
        sig = data[off:off + 4]
        if sig == b'curv':
            count = struct.unpack('>I', data[off + 8:off + 12])[0]
            if count == 0:
                return 'identity (γ=1.0)'
            if count == 1:
                gamma = struct.unpack('>H', data[off + 12:off + 14])[0] / 256.0
                return f'γ={gamma:.2f}'
            return f'{count}-point curve'
        if sig == b'para':
            if off + 16 <= len(data):
                ftype = struct.unpack('>H', data[off + 8:off + 10])[0]
                if ftype == 0 and off + 16 <= len(data):
                    gamma = struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0
                    return f'γ={gamma:.2f} (parametric)'
        return sig.decode('latin1', errors='replace').strip()

    def read_trc(tag: str) -> list[float] | None:
        if tag not in tags:
            return None
        off, _ = tags[tag]
        if off + 12 > len(data):
            return None
        sig = data[off:off + 4]
        if sig == b'curv':
            count = struct.unpack('>I', data[off + 8:off + 12])[0]
            if count == 0:
                return [0.0, 1.0]
            if count == 1:
                gamma = struct.unpack('>H', data[off + 12:off + 14])[0] / 256.0
                return [pow(i / 255.0, gamma) for i in range(256)]
            raw = struct.unpack(f'>{count}H', data[off + 12:off + 12 + count * 2])
            step = max(1, count // 256)
            return [raw[i] / 65535.0 for i in range(0, count, step)]
        if sig == b'para':
            if off + 16 > len(data):
                return None
            ftype = struct.unpack('>H', data[off + 8:off + 10])[0]
            if ftype == 0:
                gamma = struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0
                return [pow(i / 255.0, gamma) for i in range(256)]
        return None

    def xyz_d50_to_srgb8(xyz: list[float]) -> list[int]:
        x, y, z = xyz
        # Bradford D50 → D65
        rx =  0.9555766 * x - 0.0230393 * y + 0.0631636 * z
        ry = -0.0282895 * x + 1.0099416 * y + 0.0210077 * z
        rz =  0.0122982 * x - 0.0204830 * y + 1.3299098 * z
        # XYZ D65 → linear sRGB
        lr =  3.2404542 * rx - 1.5371385 * ry - 0.4985314 * rz
        lg = -0.9692660 * rx + 1.8760108 * ry + 0.0415560 * rz
        lb =  0.0556434 * rx - 0.2040259 * ry + 1.0572252 * rz

        def g(v: float) -> int:
            v = max(0.0, min(1.0, v))
            e = 12.92 * v if v <= 0.0031308 else 1.055 * pow(v, 1.0 / 2.4) - 0.055
            return round(e * 255)

        return [g(lr), g(lg), g(lb)]

    # ------------------------------------------------------------------ extended fields

    # Technology (sig  type tag)
    tech_code = ''
    if 'tech' in tags:
        off, _ = tags['tech']
        if off + 12 <= len(data) and data[off:off + 4] == b'sig ':
            tech_code = data[off + 8:off + 12].decode('latin1', errors='replace')

    # Luminance Y in cd/m² (absolute XYZ, so no D50 normalisation)
    luminance_y: float | None = None
    if 'lumi' in tags:
        off, _ = tags['lumi']
        if off + 16 <= len(data):
            luminance_y = round(struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0, 2)

    # Measurement observer
    observer_id: int | None = None
    if 'meas' in tags:
        off, _ = tags['meas']
        if off + 12 <= len(data) and data[off:off + 4] == b'meas':
            observer_id = struct.unpack('>I', data[off + 8:off + 12])[0]

    # Viewing conditions — illuminant type at byte 32 of the view block
    view_illuminant_id: int | None = None
    if 'view' in tags:
        off, sz = tags['view']
        if off + 36 <= len(data) and data[off:off + 4] == b'view':
            view_illuminant_id = struct.unpack('>I', data[off + 32:off + 36])[0]

    # ------------------------------------------------------------------ per-tag summaries

    def _tag_summary(sig: str, off: int, sz: int) -> str:
        ts = get_type_sig(off)
        if ts in ('XYZ ', 'XYZ\x00') and off + 20 <= len(data):
            x = struct.unpack('>i', data[off + 8:off + 12])[0] / 65536.0
            y = struct.unpack('>i', data[off + 12:off + 16])[0] / 65536.0
            z = struct.unpack('>i', data[off + 16:off + 20])[0] / 65536.0
            return f'X={x:.4f}  Y={y:.4f}  Z={z:.4f}'
        if ts in ('curv', 'para'):
            return _trc_summary_for(sig)
        if ts in ('desc', 'mluc', 'text'):
            s = read_desc_str(sig)
            return (s[:50] + '…') if s and len(s) > 50 else (s or '')
        if ts == 'sig ' and off + 12 <= len(data):
            code = data[off + 8:off + 12].decode('latin1', errors='replace').strip()
            return _TECH_NAMES.get(code, code)
        if ts == 'meas' and off + 12 <= len(data):
            oid = struct.unpack('>I', data[off + 8:off + 12])[0]
            return _OBSERVER_NAMES.get(oid, f'observer {oid}')
        if ts == 'view' and off + 36 <= len(data):
            vid = struct.unpack('>I', data[off + 32:off + 36])[0]
            return _ILLUMINANT_NAMES.get(vid, f'illuminant {vid}')
        return ''

    tags_directory = [
        {
            'sig': sig,
            'name': _TAG_NAMES.get(sig, sig),
            'type_sig': get_type_sig(off),
            'offset': off,
            'size': sz,
            'summary': _tag_summary(sig, off, sz),
        }
        for (sig, off, sz) in raw_tags
    ]

    # ------------------------------------------------------------------ structure regions

    tag_dir_end = 132 + tag_count * 12

    # Group tags sharing the same data block (same offset)
    blocks: dict[int, tuple[int, list[str]]] = {}
    for (sig, off, sz) in raw_tags:
        if off not in blocks:
            blocks[off] = (sz, [sig])
        else:
            blocks[off][1].append(sig)

    structure: list[dict] = [
        {'label': 'Header', 'offset': 0, 'size': 128, 'color': 'hdr'},
        {
            'label': f'Tag Directory  ({tag_count} entries)',
            'offset': 128,
            'size': tag_dir_end - 128,
            'color': 'tagdir',
        },
    ]

    prev_end = tag_dir_end
    for off, (sz, sigs) in sorted(blocks.items()):
        if off > prev_end:
            structure.append({
                'label': 'padding',
                'offset': prev_end,
                'size': off - prev_end,
                'color': 'gap',
            })
        sigs_str = ' · '.join(sigs[:3]) + (' …' if len(sigs) > 3 else '')
        names = ' / '.join(_TAG_NAMES.get(s, s) for s in sigs[:2])
        if len(sigs) > 2:
            names += f' (+{len(sigs) - 2})'
        ts = get_type_sig(off).strip()
        structure.append({
            'label': f'[{sigs_str}]  {names}  [{ts}]',
            'offset': off,
            'size': sz,
            'color': _TAG_COLOR.get(sigs[0], 'other'),
        })
        prev_end = off + sz

    # ------------------------------------------------------------------ assemble

    r_xyz = read_xyz('rXYZ')
    g_xyz = read_xyz('gXYZ')
    b_xyz = read_xyz('bXYZ')
    w_xyz = read_xyz('wtpt')

    return {
        'description': read_desc_str('desc'),
        'copyright': read_desc_str('cprt'),
        'manufacturer_desc': read_desc_str('dmnd'),
        'device_model_desc': read_desc_str('dmdd'),
        'viewing_conditions_desc': read_desc_str('vued'),
        'technology': tech_code.strip() or None,
        'technology_name': _TECH_NAMES.get(tech_code, tech_code.strip()) or None,
        'luminance_y': luminance_y,
        'observer': _OBSERVER_NAMES.get(observer_id, None) if observer_id is not None else None,
        'view_illuminant': _ILLUMINANT_NAMES.get(view_illuminant_id, None) if view_illuminant_id is not None else None,
        'profile_class': _PROFILE_CLASS.get(profile_class, profile_class),
        'color_space': color_space,
        'pcs': pcs,
        'total_size': len(data),
        'white_point': w_xyz,
        'black_point': read_xyz('bkpt'),
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
        'trc_summary': _trc_summary_for('rTRC'),
        'tags_directory': tags_directory,
        'structure': structure,
    }
