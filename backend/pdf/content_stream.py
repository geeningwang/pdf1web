"""Parse PDF page content stream operators for visualization.

Returns a structured operator list and category summary for rendering a
colour-coded structure bar and an operator table — analogous to what
jpeg.py does for JPEG streams.
"""
from __future__ import annotations

# ── Operator catalogue ────────────────────────────────────────────────────────
# op → (category, description)
_OPS: dict[str, tuple[str, str]] = {
    # Text object
    'BT':  ('text_state', 'Begin text object'),
    'ET':  ('text_state', 'End text object'),
    # Text state
    'Tf':  ('text_state', 'Select font and size'),
    'Tc':  ('text_state', 'Set character spacing'),
    'Tw':  ('text_state', 'Set word spacing'),
    'Tz':  ('text_state', 'Set horizontal scaling'),
    'TL':  ('text_state', 'Set text leading'),
    'Tr':  ('text_state', 'Set text rendering mode'),
    'Ts':  ('text_state', 'Set text rise'),
    # Text positioning
    'Td':  ('text_pos',   'Move text position'),
    'TD':  ('text_pos',   'Move text position and set leading'),
    'Tm':  ('text_pos',   'Set text matrix and line matrix'),
    'T*':  ('text_pos',   'Move to start of next line'),
    # Text showing
    'Tj':  ('text_show',  'Show text string'),
    'TJ':  ('text_show',  'Show text with individual glyph offsets'),
    "'":   ('text_show',  'Move to next line and show text'),
    '"':   ('text_show',  'Set word/char spacing, move to next line, show text'),
    # Graphics state
    'q':   ('gstate',     'Save graphics state'),
    'Q':   ('gstate',     'Restore graphics state'),
    'cm':  ('gstate',     'Concatenate matrix to CTM'),
    'gs':  ('gstate',     'Set parameters from graphics state resource'),
    'w':   ('gstate',     'Set line width'),
    'J':   ('gstate',     'Set line cap style'),
    'j':   ('gstate',     'Set line join style'),
    'M':   ('gstate',     'Set miter limit'),
    'd':   ('gstate',     'Set line dash pattern'),
    'ri':  ('gstate',     'Set color rendering intent'),
    'i':   ('gstate',     'Set flatness tolerance'),
    # Color
    'cs':  ('color',      'Set non-stroking color space'),
    'CS':  ('color',      'Set stroking color space'),
    'sc':  ('color',      'Set non-stroking color'),
    'SC':  ('color',      'Set stroking color'),
    'scn': ('color',      'Set non-stroking color (extended)'),
    'SCN': ('color',      'Set stroking color (extended)'),
    'g':   ('color',      'Set gray level (non-stroking)'),
    'G':   ('color',      'Set gray level (stroking)'),
    'rg':  ('color',      'Set RGB color (non-stroking)'),
    'RG':  ('color',      'Set RGB color (stroking)'),
    'k':   ('color',      'Set CMYK color (non-stroking)'),
    'K':   ('color',      'Set CMYK color (stroking)'),
    # Path construction
    'm':   ('path',       'Begin new subpath (move to)'),
    'l':   ('path',       'Append straight line segment'),
    'c':   ('path',       'Append cubic Bézier curve (full)'),
    'v':   ('path',       'Append cubic Bézier (first cp = current point)'),
    'y':   ('path',       'Append cubic Bézier (last cp = final point)'),
    'h':   ('path',       'Close current subpath'),
    're':  ('path',       'Append rectangle'),
    # Path painting
    'S':   ('path',       'Stroke path'),
    's':   ('path',       'Close and stroke path'),
    'f':   ('path',       'Fill path (nonzero winding rule)'),
    'F':   ('path',       'Fill path (nonzero winding rule, obsolete)'),
    'f*':  ('path',       'Fill path (even-odd rule)'),
    'B':   ('path',       'Fill and stroke (nonzero winding rule)'),
    'B*':  ('path',       'Fill and stroke (even-odd rule)'),
    'b':   ('path',       'Close, fill and stroke (nonzero)'),
    'b*':  ('path',       'Close, fill and stroke (even-odd)'),
    'n':   ('path',       'End path without painting'),
    # Clipping
    'W':   ('clip',       'Set clipping path (nonzero winding rule)'),
    'W*':  ('clip',       'Set clipping path (even-odd rule)'),
    # XObject
    'Do':  ('xobject',    'Invoke named XObject'),
    # Shading
    'sh':  ('shading',    'Paint shading pattern'),
    # Marked content
    'BMC': ('marked',     'Begin marked-content sequence'),
    'BDC': ('marked',     'Begin marked-content sequence with property list'),
    'EMC': ('marked',     'End marked-content sequence'),
    'MP':  ('marked',     'Define marked-content point'),
    'DP':  ('marked',     'Define marked-content point with property list'),
    # Inline image
    'BI':  ('inline_img', 'Begin inline image object'),
    'ID':  ('inline_img', 'Begin inline image data'),
    'EI':  ('inline_img', 'End inline image object'),
    # Compatibility
    'BX':  ('compat',     'Begin compatibility section'),
    'EX':  ('compat',     'End compatibility section'),
}

_CAT_COLOR: dict[str, str] = {
    'text_state': 'text-state',
    'text_pos':   'text-pos',
    'text_show':  'text-show',
    'gstate':     'gstate',
    'color':      'color',
    'path':       'path',
    'clip':       'clip',
    'xobject':    'xobject',
    'shading':    'shading',
    'marked':     'marked',
    'inline_img': 'inline',
    'compat':     'compat',
    'unknown':    'unknown',
}

_CAT_LABEL: dict[str, str] = {
    'text_state': 'Text state',
    'text_pos':   'Text positioning',
    'text_show':  'Text show',
    'gstate':     'Graphics state',
    'color':      'Color',
    'path':       'Path',
    'clip':       'Clipping',
    'xobject':    'XObject',
    'shading':    'Shading',
    'marked':     'Marked content',
    'inline_img': 'Inline image',
    'compat':     'Compatibility',
    'unknown':    'Unknown',
}

_CAT_ORDER = [
    'text_state', 'text_pos', 'text_show',
    'gstate', 'color', 'path', 'clip',
    'xobject', 'shading', 'marked', 'inline_img', 'compat', 'unknown',
]

# ── Tokenizer ─────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[tuple[str, str]]:
    """Return (type, raw_value) tokens from a PDF content stream.
    Types: op, num, name, string, array, dict_val
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        # Whitespace / null
        if c in ' \t\r\n\x00\x0c':
            i += 1
            continue
        # Comment
        if c == '%':
            j = i + 1
            while j < n and text[j] not in '\r\n':
                j += 1
            i = j
            continue
        # Literal string  (...)
        if c == '(':
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                ch = text[j]
                if ch == '\\':
                    j += 2
                elif ch == '(':
                    depth += 1; j += 1
                elif ch == ')':
                    depth -= 1; j += 1
                else:
                    j += 1
            tokens.append(('string', text[i:j]))
            i = j
            continue
        # Dict  <<...>>  or hex string  <...>
        if c == '<':
            if i + 1 < n and text[i + 1] == '<':
                j = text.find('>>', i + 2)
                j = (j + 2) if j >= 0 else n
                tokens.append(('dict_val', text[i:j]))
            else:
                j = text.find('>', i + 1)
                j = (j + 1) if j >= 0 else n
                tokens.append(('string', text[i:j]))
            i = j
            continue
        # Array  [...]
        if c == '[':
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                ch = text[j]
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                j += 1
            tokens.append(('array', text[i:j]))
            i = j
            continue
        # Name  /...
        if c == '/':
            j = i + 1
            while j < n and text[j] not in ' \t\r\n\x00()[]{}<>/%':
                j += 1
            tokens.append(('name', text[i:j]))
            i = j
            continue
        # Number (integer or real, possibly signed)
        if c.isdigit() or ((c in '+-') and i + 1 < n and (text[i + 1].isdigit() or text[i + 1] == '.')):
            j = i + 1
            while j < n and (text[j].isdigit() or text[j] == '.'):
                j += 1
            tokens.append(('num', text[i:j]))
            i = j
            continue
        if c == '.' and i + 1 < n and text[i + 1].isdigit():
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(('num', text[i:j]))
            i = j
            continue
        # Operator / keyword  (letters + * ' ")
        if c.isalpha() or c in "*'\"":
            j = i + 1
            while j < n and (text[j].isalpha() or text[j] == '*'):
                j += 1
            tokens.append(('op', text[i:j]))
            i = j
            continue
        # Skip unknown character
        i += 1
    return tokens


# ── Operand summary ───────────────────────────────────────────────────────────

def _str_content(raw: str) -> str:
    """Extract printable content from a literal string token like (Hello)."""
    if raw.startswith('(') and raw.endswith(')'):
        s = raw[1:-1]
        # Unescape basic sequences
        s = s.replace('\\n', ' ').replace('\\r', ' ').replace('\\t', ' ')
        s = s.replace('\\(', '(').replace('\\)', ')')
        s = s.replace('\\\\', '\\')
        # Replace non-printable bytes with middle dot
        return ''.join(c if 0x20 <= ord(c) < 0x7F else '·' for c in s)
    return raw


def _operand_summary(op: str, operands: list[tuple[str, str]]) -> str:
    """One-line human-readable description of the operator's operands."""
    vals = [v for _, v in operands]
    if not vals:
        return ''
    if op == 'Tf' and len(vals) >= 2:
        return f'{vals[0]}  size {vals[1]}'
    if op in ('Td', 'TD') and len(vals) >= 2:
        return f'dx={vals[0]}  dy={vals[1]}'
    if op == 'Tm' and len(vals) >= 6:
        return f'[{", ".join(vals[:6])}]'
    if op == 'cm' and len(vals) >= 6:
        return f'[{", ".join(vals[:6])}]'
    if op in ('Tj', "'"):
        s = _str_content(vals[-1]) if vals else ''
        return (s[:60] + '…') if len(s) > 60 else s
    if op == '"' and len(vals) >= 3:
        s = _str_content(vals[-1])
        return (s[:60] + '…') if len(s) > 60 else s
    if op == 'TJ' and vals:
        # Array of strings and numbers
        s = vals[0]
        # Extract visible text from the array
        import re
        parts = re.findall(r'\(([^)]*)\)', s)
        text = ''.join(
            ''.join(c if 0x20 <= ord(c) < 0x7F else '·' for c in p)
            for p in parts
        )
        return (text[:60] + '…') if len(text) > 60 else text
    if op in ('rg', 'RG') and len(vals) >= 3:
        return f'rgb({vals[0]}, {vals[1]}, {vals[2]})'
    if op in ('k', 'K') and len(vals) >= 4:
        return f'cmyk({vals[0]}, {vals[1]}, {vals[2]}, {vals[3]})'
    if op in ('g', 'G') and vals:
        return f'gray={vals[0]}'
    if op == 're' and len(vals) >= 4:
        return f'x={vals[0]}  y={vals[1]}  w={vals[2]}  h={vals[3]}'
    if op == 'Do' and vals:
        return vals[0]
    if op in ('gs', 'cs', 'CS', 'ri') and vals:
        return vals[0]
    if op in ('scn', 'SCN', 'sc', 'SC'):
        return '  '.join(vals[:4])
    if op in ('w', 'Tc', 'Tw', 'Tz', 'TL', 'Tr', 'Ts', 'i', 'M') and vals:
        return vals[0]
    if op == 'J' and vals:
        return {0: '0 butt', 1: '1 round', 2: '2 projecting square'}.get(
            int(float(vals[0])), vals[0]) if vals[0].lstrip('-').isdigit() else vals[0]
    if op == 'j' and vals:
        return {0: '0 miter', 1: '1 round', 2: '2 bevel'}.get(
            int(float(vals[0])), vals[0]) if vals[0].lstrip('-').isdigit() else vals[0]
    if op in ('BMC', 'BDC', 'MP', 'DP') and vals:
        return vals[0]
    return '  '.join(vals[:3])


# ── Main parser ───────────────────────────────────────────────────────────────

# PDF operator keywords used for quick content-stream detection
_DETECTION_OPS = frozenset(
    'BT ET Tf Tm Td TD Tj TJ q Q cm gs rg RG cs CS scn SCN re m l h S f Do w'.split()
)


def is_content_stream(decoded: bytes) -> bool:
    """Quick heuristic: does *decoded* look like a PDF content stream?"""
    try:
        sample = decoded[:400].decode('latin-1', errors='replace')
    except Exception:
        return False
    found = 0
    for tok in sample.split():
        if tok in _DETECTION_OPS:
            found += 1
            if found >= 3:
                return True
    return False


def parse_content_stream(data: bytes) -> dict | None:
    """Parse PDF content stream operators from decoded *data*.

    Returns None if the data does not look like a content stream.
    Returns a dict with:
      operations  – list of operator dicts (capped at _MAX_OPS)
      total_ops   – true count of all operators found
      truncated   – True if more operators exist beyond _MAX_OPS
      structure   – list of {label, color, count, size} for the structure bar
      category_counts – dict of category → count
    """
    try:
        text = data.decode('latin-1', errors='replace')
    except Exception:
        return None

    tokens = _tokenize(text)

    # Quick check: must contain at least three known operators
    op_count = sum(1 for t, v in tokens if t == 'op' and v in _OPS)
    if op_count < 3:
        return None

    operations: list[dict] = []
    operands: list[tuple[str, str]] = []
    skip_to_ei = False  # skip raw inline image data between ID and EI

    for ttype, tval in tokens:
        if skip_to_ei:
            if ttype == 'op' and tval == 'EI':
                cat, desc = _OPS['EI']
                operations.append({
                    'op': 'EI',
                    'category': cat,
                    'color': _CAT_COLOR[cat],
                    'desc': desc,
                    'summary': '',
                })
                operands = []
                skip_to_ei = False
            continue

        if ttype == 'op':
            cat, desc = _OPS.get(tval, ('unknown', 'Unknown operator'))
            operations.append({
                'op': tval,
                'category': cat,
                'color': _CAT_COLOR.get(cat, 'unknown'),
                'desc': desc,
                'summary': _operand_summary(tval, operands),
            })
            operands = []
            if tval == 'ID':
                skip_to_ei = True
        else:
            operands.append((ttype, tval))

    if not operations:
        return None

    # Category counts
    counts: dict[str, int] = {}
    for op in operations:
        c = op['category']
        counts[c] = counts.get(c, 0) + 1

    structure = [
        {
            'label': _CAT_LABEL.get(cat, cat),
            'color': _CAT_COLOR[cat],
            'count': counts[cat],
            'size': counts[cat],
        }
        for cat in _CAT_ORDER
        if cat in counts
    ]

    total = len(operations)
    return {
        'operations': operations,
        'total_ops': total,
        'truncated': False,
        'structure': structure,
        'category_counts': counts,
    }
