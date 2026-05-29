"""
PdfDocument — top-level model: loads a PDF, resolves objects, builds the node tree.
Port of the C++ PdfDocument / PdfNode.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from .ccitt import parse_ccitt
from .filters import flat_decode, ascii_hex_decode, ascii85_decode, dct_decode
from .icc import parse_icc_profile
from .jpeg import parse_jpeg
from .objects import PdfObject, PdfObjType, PdfRef
from .parser import PdfParser
from .reader import PdfReader
from .xref import PdfXrefTable, XrefEntry, XrefEntryType

# Maximum recursion depth when building the tree (mirrors kMaxDepth=8 in C++)
_MAX_DEPTH = 8


# -----------------------------------------------------------------------
# PdfNode
# -----------------------------------------------------------------------
@dataclass
class PdfNode:
    label: str = ""
    detail: str = ""
    obj_num: int = -1
    gen_num: int = 0
    is_image: bool = False
    type_label: str = ""
    children: list["PdfNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Cap detail text in the tree payload — full detail is fetched lazily
        # via /api/object/... when the user selects a node.
        detail = self.detail[:4096] if len(self.detail) > 4096 else self.detail
        return {
            "label": self.label,
            "detail": detail,
            "obj_num": self.obj_num,
            "gen_num": self.gen_num,
            "is_image": self.is_image,
            "type_label": self.type_label,
            "children": [c.to_dict() for c in self.children],
        }


# -----------------------------------------------------------------------
# Formatting helpers (mirror C++ BriefValue / DetailText)
# -----------------------------------------------------------------------
def _brief(obj: PdfObject) -> str:
    t = obj.type
    if t == PdfObjType.Null:
        return "null"
    if t == PdfObjType.Boolean:
        return "true" if obj.bval else "false"
    if t == PdfObjType.Integer:
        return str(obj.ival)
    if t == PdfObjType.Real:
        return repr(obj.dval)
    if t == PdfObjType.LiteralString:
        safe = obj.sval[:80].replace("\r", "\\r").replace("\n", "\\n")
        return f"({safe})"
    if t == PdfObjType.HexString:
        hex_str = obj.sval.encode("latin-1").hex().upper()
        return f"<{hex_str[:40]}>"
    if t == PdfObjType.Name:
        return f"/{obj.sval}"
    if t == PdfObjType.Reference:
        return f"{obj.ref.num} {obj.ref.gen} R"
    if t == PdfObjType.Array:
        return f"[ {len(obj.arr)} items ]"
    if t == PdfObjType.Dictionary:
        return f"<< {len(obj.dict)} entries >>"
    if t == PdfObjType.Stream:
        return f"<< stream, length={len(obj.stream_raw)} >>"
    return ""


def _brief_inline(obj: PdfObject) -> str:
    """Like _brief but recursively expands nested arrays inline."""
    if obj.type == PdfObjType.Array:
        return "[ " + "  ".join(_brief_inline(item) for item in obj.arr) + " ]"
    return _brief(obj)


def _check_indexed_arr(arr: list[PdfObject], result: set[int]) -> None:
    """If *arr* is [/Indexed, base_cs, hival, lookup_ref], add lookup_ref.num to result."""
    if (len(arr) >= 4
            and arr[0].type == PdfObjType.Name
            and arr[0].sval == "Indexed"
            and arr[3].type == PdfObjType.Reference):
        result.add(arr[3].ref.num)


def _detail(obj: PdfObject, indent: int = 0) -> str:
    pad = "  " * indent
    t = obj.type
    if t == PdfObjType.Null:
        return pad + "null"
    if t == PdfObjType.Boolean:
        return pad + ("true" if obj.bval else "false")
    if t == PdfObjType.Integer:
        return pad + str(obj.ival)
    if t == PdfObjType.Real:
        return pad + repr(obj.dval)
    if t == PdfObjType.LiteralString:
        return pad + f"({obj.sval})"
    if t == PdfObjType.HexString:
        hex_str = obj.sval.encode("latin-1").hex().upper()
        return pad + f"<{hex_str}>"
    if t == PdfObjType.Name:
        return pad + f"/{obj.sval}"
    if t == PdfObjType.Reference:
        return pad + f"{obj.ref.num} {obj.ref.gen} R"
    if t == PdfObjType.Array:
        lines = [pad + "["]
        for item in obj.arr:
            lines.append(_detail(item, indent + 1))
        lines.append(pad + "]")
        return "\n".join(lines)
    if t in (PdfObjType.Dictionary, PdfObjType.Stream):
        lines = [pad + "<<"]
        for k, v in obj.dict.items():
            if v.type == PdfObjType.Array:
                inline = "[ " + "  ".join(_brief_inline(item) for item in v.arr) + " ]"
                lines.append(pad + f"  /{k}  {inline}")
            elif v.type == PdfObjType.Dictionary:
                # Expand nested dicts recursively so entries like Resources/XObject
                # are fully visible rather than showing "<< N entries >>".
                sub_lines = _detail(v, indent + 1).split("\n")
                lines.append(pad + f"  /{k}  " + sub_lines[0].lstrip())
                lines.extend(sub_lines[1:])
            else:
                lines.append(pad + f"  /{k}  {_brief(v)}")
        lines.append(pad + ">>")
        if t == PdfObjType.Stream:
            lines.append("stream")
            lines.append(f"[{len(obj.stream_raw)} bytes raw]")

            # --- Detect filter for annotated display ---
            _f = obj.get("Filter")
            _filter_name = _f.sval if _f.is_name() else ''
            _is_jpeg = (
                _filter_name == 'DCTDecode'
                and len(obj.stream_raw) >= 4
                and obj.stream_raw[:2] == b'\xFF\xD8'
            )
            _is_ccitt = _filter_name == 'CCITTFaxDecode'

            if _is_jpeg:
                jpeg_info = parse_jpeg(obj.stream_raw)
                raw_sz = len(obj.stream_raw)
                if jpeg_info:
                    lines.append(f'--- JPEG stream, annotated ({raw_sz} bytes) ---')
                    lines.append(_jpeg_annotated_hex(obj.stream_raw, jpeg_info['structure']))
                else:
                    capped = raw_sz > _HEX_DUMP_MAX
                    lines.append(f'--- raw hex ({raw_sz} bytes' + (', first 64K shown' if capped else '') + ') ---')
                    lines.append(_hex_dump(obj.stream_raw))
                if obj.stream_decoded:
                    decoded = obj.stream_decoded
                    lines.append(f'[{len(decoded)} bytes decoded — raw pixel bytes]')

            elif _is_ccitt:
                dp = obj.get('DecodeParms')
                k = -1
                columns = 1728
                rows_param: int | None = None
                end_of_block = True
                end_of_line = False
                encoded_byte_align = False
                black_is_1 = False
                damaged_rows = 0
                if dp.is_dict():
                    k_obj = dp.get('K')
                    if k_obj.is_int():
                        k = int(k_obj.ival)
                    col_obj = dp.get('Columns')
                    if col_obj.is_int():
                        columns = int(col_obj.ival)
                    rows_obj = dp.get('Rows')
                    if rows_obj.is_int():
                        rows_param = int(rows_obj.ival)
                    eob_obj = dp.get('EndOfBlock')
                    if eob_obj.type == PdfObjType.Boolean:
                        end_of_block = eob_obj.bval
                    eol_obj = dp.get('EndOfLine')
                    if eol_obj.type == PdfObjType.Boolean:
                        end_of_line = eol_obj.bval
                    eba_obj = dp.get('EncodedByteAlign')
                    if eba_obj.type == PdfObjType.Boolean:
                        encoded_byte_align = eba_obj.bval
                    bi1_obj = dp.get('BlackIs1')
                    if bi1_obj.type == PdfObjType.Boolean:
                        black_is_1 = bi1_obj.bval
                    drbe_obj = dp.get('DamagedRowsBeforeError')
                    if drbe_obj.is_int():
                        damaged_rows = int(drbe_obj.ival)
                if k < 0:
                    scheme = 'CCITT Group 4 / T.6'
                elif k == 0:
                    scheme = 'CCITT Group 3 1D / T.4'
                else:
                    scheme = f'CCITT Group 3 2D / T.4 (K={k})'
                raw_sz = len(obj.stream_raw)
                lines.append(f'--- CCITTFaxDecode  —  {scheme} ---')
                lines.append(f'  K                     {k:>5}   → {scheme}')
                lines.append(f'  Columns               {columns:>5}   pixels per scan line')
                if rows_param is not None:
                    lines.append(f'  Rows (DecodeParms)    {rows_param:>5}   scan lines')
                lines.append(f'  EndOfBlock            {"true" if end_of_block else "false"}   ({"EOFB/RTC terminates stream" if end_of_block else "no end-of-block code"})')
                lines.append(f'  EndOfLine             {"true" if end_of_line else "false"}   ({"EOL codes between lines" if end_of_line else "no EOL between lines"})')
                lines.append(f'  EncodedByteAlign      {"true" if encoded_byte_align else "false"}   ({"lines byte-aligned" if encoded_byte_align else "packed bits, no byte alignment"})')
                lines.append(f'  BlackIs1              {"true" if black_is_1 else "false"}   ({"1 = black" if black_is_1 else "0 = black, 1 = white (default)"})')
                if damaged_rows:
                    lines.append(f'  DamagedRowsBeforeError {damaged_rows}')
                capped = raw_sz > _HEX_DUMP_MAX
                lines.append(f'--- raw compressed hex ({raw_sz} bytes' + (', first 64K shown' if capped else '') + ') ---')
                lines.append(_hex_dump(obj.stream_raw))

            elif obj.stream_decoded:
                decoded = obj.stream_decoded
                lines.append(f"[{len(decoded)} bytes decoded]")
                if _is_binary(decoded):
                    total = len(decoded)
                    # Check for ICC profile — show annotated hex
                    if len(decoded) >= 40 and decoded[36:40] == b'acsp':
                        icc_info = parse_icc_profile(decoded)
                        if icc_info and icc_info.get('structure'):
                            lines.append(f'--- ICC profile, annotated hex ({total} bytes) ---')
                            lines.append(_icc_annotated_hex(decoded, icc_info['structure']))
                        else:
                            lines.append(f'--- decoded hex ({total} bytes) ---')
                            lines.append(_hex_dump(decoded))
                    else:
                        capped = total > _HEX_DUMP_MAX
                        label = f'--- decoded hex ({total} bytes' + (', first 64K shown' if capped else '') + ') ---'
                        lines.append(label)
                        lines.append(_hex_dump(decoded))
                else:
                    show = min(len(decoded), 4096)
                    lines.append(
                        f"--- decoded content (first {show} bytes) ---"
                        if show < len(decoded) else
                        "--- decoded content ---"
                    )
                    lines.append(_bytes_to_printable(decoded[:show]))
            else:
                total = len(obj.stream_raw)
                capped = total > _HEX_DUMP_MAX
                label = f"--- raw hex ({total} bytes" + (", first 64K shown" if capped else "") + ") ---"
                lines.append(label)
                lines.append(_hex_dump(obj.stream_raw))
            lines.append("endstream")
        return "\n".join(lines)
    return pad + "?"


def _is_binary(data: bytes, sample: int = 512, threshold: float = 0.15) -> bool:
    """Return True if more than *threshold* fraction of sampled bytes are non-printable."""
    chunk = data[:sample]
    if not chunk:
        return False
    non_print = sum(
        1 for b in chunk
        if (b < 32 and b not in (9, 10, 13)) or b > 126  # outside printable ASCII
    )
    return (non_print / len(chunk)) > threshold


def _bytes_to_printable(data: bytes) -> str:
    out: list[str] = []
    for b in data:
        if b == ord("\n"):
            out.append("\n")
        elif b == ord("\r"):
            pass
        elif b >= 32 or b == ord("\t"):
            out.append(chr(b))
        else:
            out.append(".")
    return "".join(out)


_HEX_DUMP_MAX = 64 * 1024  # 64 KiB


def _hex_dump_section(data: bytes, start_addr: int, remaining: int) -> tuple[str, int]:
    """Hex dump a byte slice with absolute addresses, limited by *remaining* bytes.

    Returns (text, bytes_consumed).
    """
    lines: list[str] = []
    chunk = data[:remaining]
    for i in range(0, len(chunk), 16):
        row = chunk[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in row)
        lines.append(f'{start_addr + i:04X}: {hex_part}')
    consumed = len(chunk)
    if len(data) > consumed:
        lines.append(f'     … {len(data) - consumed} more bytes …')
    return '\n'.join(lines), consumed


def _icc_annotated_hex(data: bytes, structure: list[dict]) -> str:
    """Generate section-annotated hex dump for an ICC profile, capped at 64K total."""
    lines: list[str] = []
    remaining = _HEX_DUMP_MAX
    for seg in sorted(structure, key=lambda s: s['offset']):
        off = seg['offset']
        sz = seg['size']
        end = off + sz - 1
        lines.append(f"=== {seg['label']} ===")
        lines.append(f'    0x{off:04X} – 0x{end:04X}  ({sz} bytes)')
        if remaining > 0:
            section_hex, consumed = _hex_dump_section(data[off:off + sz], start_addr=off, remaining=remaining)
            lines.append(section_hex)
            remaining -= consumed
        else:
            lines.append(f'     … {sz} bytes (64K global limit reached) …')
        lines.append('')
    return '\n'.join(lines)


def _jpeg_annotated_hex(data: bytes, structure: list[dict]) -> str:
    """Generate section-annotated hex dump for a JPEG stream, capped at 64K total."""
    lines: list[str] = []
    remaining = _HEX_DUMP_MAX
    for seg in structure:  # already in stream order from parse_jpeg
        off = seg['offset']
        sz = seg['size']
        end = off + sz - 1
        lines.append(f"=== {seg['label']} ===")
        lines.append(f'    0x{off:04X} – 0x{end:04X}  ({sz} bytes)')
        if remaining > 0:
            section_hex, consumed = _hex_dump_section(
                data[off:off + sz], start_addr=off, remaining=remaining
            )
            lines.append(section_hex)
            remaining -= consumed
        else:
            lines.append(f'     … {sz} bytes (64K global limit reached) …')
        lines.append('')
    return '\n'.join(lines)


def _hex_dump(data: bytes) -> str:
    truncated = len(data) > _HEX_DUMP_MAX
    chunk = data[:_HEX_DUMP_MAX]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        row = chunk[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in row)
        lines.append(f'{i:04X}: {hex_part}')
    if truncated:
        lines.append(f'... (truncated, only first 64K of {len(data)} bytes shown)')
    return '\n'.join(lines)
    return "\n".join(lines)


# -----------------------------------------------------------------------
# Stream decoder
# -----------------------------------------------------------------------
def _decode_stream(obj: PdfObject) -> bytes | None:
    if obj.type != PdfObjType.Stream:
        return None
    if not obj.stream_raw:
        return b""

    filter_names: list[str] = []
    f_obj = obj.get("Filter")
    if f_obj.is_name():
        filter_names.append(f_obj.sval)
    elif f_obj.is_array():
        for f in f_obj.arr:
            if f.is_name():
                filter_names.append(f.sval)

    if not filter_names:
        return obj.stream_raw

    data = obj.stream_raw
    for name in filter_names:
        decoded: bytes | None = None
        if name == "FlateDecode":
            decoded = flat_decode(data)
        elif name == "ASCIIHexDecode":
            decoded = ascii_hex_decode(data)
        elif name == "ASCII85Decode":
            decoded = ascii85_decode(data)
        elif name == "DCTDecode":
            decoded = dct_decode(data)
        else:
            decoded = None  # unsupported filter — leave as-is

        if decoded is not None:
            data = decoded
        else:
            return None  # decode failed

    return data


# -----------------------------------------------------------------------
# PdfDocument
# -----------------------------------------------------------------------
class PdfDocument:
    def __init__(self) -> None:
        self._reader: PdfReader | None = None
        self._xref = PdfXrefTable()
        self._trailer = PdfObject(type=PdfObjType.Null)
        self._version: str = ""
        self._binary_marker: bytes | None = None
        self._file_path: str = ""
        self._object_cache: dict[int, PdfObject] = {}
        self._resolving: set[int] = set()  # cycle guard for _resolve_compressed
        self._root: PdfNode | None = None
        self._palette_nums: frozenset[int] = frozenset()

    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, filename: str = "") -> "PdfDocument":
        doc = cls()
        doc._file_path = filename
        doc._reader = PdfReader(data)
        doc._parse_document()
        doc._palette_nums = doc._collect_palette_nums()
        doc._root = doc._build_tree()
        return doc

    # ------------------------------------------------------------------
    def root(self) -> PdfNode | None:
        return self._root

    def version(self) -> str:
        return self._version

    def file_path(self) -> str:
        return self._file_path

    def is_palette_lookup(self, num: int) -> bool:
        """Return True if obj *num* is referenced as an Indexed CS palette lookup."""
        return num in self._palette_nums

    def _collect_palette_nums(self) -> frozenset[int]:
        """Scan all objects for Indexed color space arrays and collect lookup stream nums."""
        result: set[int] = set()
        for num in self._xref.entries:
            obj = self.resolve_num(num)
            if obj is None:
                continue
            # Top-level array object: [/Indexed base hival lookup]
            if obj.type == PdfObjType.Array:
                _check_indexed_arr(obj.arr, result)
            # Dict/stream: scan all values for inline or referenced arrays
            if obj.is_dict():
                for v in obj.dict.values():
                    if v.type == PdfObjType.Array:
                        _check_indexed_arr(v.arr, result)
                    elif v.is_ref():
                        # Resolve one level — ColorSpace is often an indirect ref to an array
                        target = self.resolve_num(v.ref.num, v.ref.gen)
                        if target is not None and target.type == PdfObjType.Array:
                            _check_indexed_arr(target.arr, result)
        return frozenset(result)

    # ------------------------------------------------------------------
    def get_object_detail(self, num: int, gen: int = 0) -> str:
        obj = self.resolve_num(num, gen)
        if obj is None:
            return f"(object {num} {gen} R could not be resolved)"
        # Decode the stream now (lazy — only when user explicitly requests detail)
        if obj.type == PdfObjType.Stream and not obj.stream_decoded:
            decoded = _decode_stream(obj)
            if decoded is not None:
                obj.stream_decoded = decoded
        return f"Object {num} {gen} obj\n" + _detail(obj)

    # ------------------------------------------------------------------
    def resolve(self, ref: PdfRef) -> PdfObject | None:
        return self.resolve_num(ref.num, ref.gen)

    def resolve_num(self, num: int, gen: int = 0) -> PdfObject | None:
        if num in self._object_cache:
            return self._object_cache[num]

        xe = self._xref.get_entry(num)
        if xe is None or xe.etype == XrefEntryType.Free:
            return None

        if xe.etype == XrefEntryType.Compressed:
            return self._resolve_compressed(num, xe)

        # Normal in-use object
        assert self._reader is not None
        parser = PdfParser(self._reader)
        out: list[int] = []
        try:
            obj = parser.parse_indirect_object(xe.offset, out)
        except Exception:
            return None

        # If /Length is an indirect reference, resolve it and re-read the stream body.
        # This is legal per PDF spec (§7.3.2) but the parser has no resolver,
        # so streams with indirect Length end up with empty stream_raw.
        if obj.type == PdfObjType.Stream and len(obj.stream_raw) == 0:
            length_obj = obj.get("Length")
            if length_obj.is_ref():
                # Cache the object stub first to avoid infinite recursion
                self._object_cache[num] = obj
                resolved_len = self.resolve_num(length_obj.ref.num, length_obj.ref.gen)
                if resolved_len is not None and resolved_len.is_int():
                    length = int(resolved_len.ival)
                    if length > 0:
                        obj.stream_raw = self._reader.read(obj.stream_offset, length)

        # Do NOT decode streams eagerly here — decode on demand in
        # get_object_detail() / _add_page_children() only when the user
        # actually selects a node.  Eager decoding of all streams during
        # tree construction pinned the CPU for PDFs with many pages.
        self._object_cache[num] = obj
        return obj

    def _resolve_compressed(self, num: int, xe: XrefEntry) -> PdfObject | None:
        """Resolve an object stored inside an object stream."""
        host_num = int(xe.offset)
        if host_num in self._resolving:
            # Cycle detected: ObjStm host references itself or another ObjStm
            # that is already being resolved.  Return None to break the loop.
            return None
        self._resolving.add(host_num)
        try:
            stm_obj = self.resolve_num(host_num)
        finally:
            self._resolving.discard(host_num)
        if stm_obj is None or stm_obj.type != PdfObjType.Stream:
            return None

        # Decode the object stream
        if stm_obj.stream_decoded:
            decoded = stm_obj.stream_decoded
        else:
            decoded = _decode_stream(stm_obj)
            if decoded is None:
                decoded = stm_obj.stream_raw

        if not decoded:
            return None

        n_obj = stm_obj.get("N")
        first_obj = stm_obj.get("First")
        if not n_obj.is_int() or not first_obj.is_int():
            return None

        N = int(n_obj.ival)
        first = int(first_obj.ival)

        # Parse the (objNum, offset) header pairs
        text = decoded.decode("latin-1", errors="replace")
        pairs: list[tuple[int, int]] = []
        pos = 0
        for _ in range(N):
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            start = pos
            while pos < len(text) and text[pos].isdigit():
                pos += 1
            if start == pos:
                break
            on = int(text[start:pos])
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            start = pos
            while pos < len(text) and text[pos].isdigit():
                pos += 1
            if start == pos:
                break
            off = int(text[start:pos])
            pairs.append((on, off))

        # Parse and cache all objects in the stream
        sub_reader = PdfReader(decoded)
        for idx, (on, off) in enumerate(pairs):
            if on in self._object_cache:
                continue
            abs_offset = first + off
            try:
                p = PdfParser(sub_reader)
                p.tok.seek(abs_offset)
                sub_obj = p.parse_object()
                self._object_cache[on] = sub_obj
            except Exception:
                pass

        return self._object_cache.get(num)

    # ------------------------------------------------------------------
    def _parse_document(self) -> None:
        assert self._reader is not None
        data = self._reader.data
        size = self._reader.size

        if size < 8:
            raise ValueError("File too small")
        if data[:5] != b"%PDF-":
            raise ValueError("Not a PDF file (missing %PDF- header)")

        # Parse version string
        i = 5
        while i < size and data[i] not in (ord("\r"), ord("\n"), ord(" ")):
            self._version += chr(data[i])
            i += 1

        # Skip line ending after version, then look for optional binary marker comment
        # (a line starting with % followed by ≥4 bytes all ≥ 0x80, per PDF spec §7.5.2)
        while i < size and data[i] in (ord("\r"), ord("\n")):
            i += 1
        if i < size and data[i] == ord("%"):
            j = i + 1
            line_bytes: list[int] = []
            while j < size and data[j] not in (ord("\r"), ord("\n")):
                line_bytes.append(data[j])
                j += 1
            if len(line_bytes) >= 4 and all(b >= 0x80 for b in line_bytes):
                self._binary_marker = bytes(line_bytes)

        # Find startxref offset (scan last 2048 bytes)
        sx_offset = self._reader.scan_backward(size, b"startxref", 2048)
        if sx_offset < 0:
            raise ValueError("No startxref found")

        # Parse the integer after "startxref"
        num_start = sx_offset + 9  # len("startxref") == 9
        while num_start < size and data[num_start] in (
            ord(" "), ord("\r"), ord("\n")
        ):
            num_start += 1
        num_end = num_start
        while num_end < size and ord("0") <= data[num_end] <= ord("9"):
            num_end += 1
        if num_end == num_start:
            raise ValueError("Cannot parse startxref value")
        xref_offset = int(data[num_start:num_end])

        self._trailer = self._xref.parse(self._reader, xref_offset)

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------
    def _build_tree(self) -> PdfNode:
        root = PdfNode(
            label="PDF Document",
            detail=f"PDF version: {self._version}\nFile: {self._file_path}",
        )

        # --- Header ---  (physical position: start of file)
        hdr = PdfNode(label="Header", detail=f"PDF version: {self._version}")
        hdr.children.append(
            PdfNode(label=f"Version: {self._version}", detail=self._version)
        )
        if self._binary_marker is not None:
            hex_str = self._binary_marker.hex().upper()
            hdr.children.append(
                PdfNode(
                    label="Binary marker",
                    detail=(
                        f"Binary marker comment (PDF spec §7.5.2)\n"
                        f"Signals that the file contains binary data.\n\n"
                        f"Bytes: {' '.join(hex_str[k:k+2] for k in range(0, len(hex_str), 2))}"
                    ),
                )
            )
        root.children.append(hdr)

        # --- Body ---  (physical position: after header, before xref table)
        entries = self._xref.entries
        in_use = sum(1 for e in entries.values() if e.etype == XrefEntryType.InUse)
        free = sum(1 for e in entries.values() if e.etype == XrefEntryType.Free)
        compressed = sum(1 for e in entries.values() if e.etype == XrefEntryType.Compressed)
        body_node = PdfNode(
            label="Body",
            detail=(
                f"PDF body — indirect objects\n"
                f"In-use:     {in_use}\n"
                f"Compressed: {compressed}\n"
                f"Free:       {free}"
            ),
        )
        for obj_num in sorted(entries):
            xe = entries[obj_num]
            if xe.etype == XrefEntryType.Free:
                lbl = f"obj {obj_num}  [free]"
                en = PdfNode(
                    label=lbl,
                    detail=f"Object {obj_num} gen {xe.gen}  FREE",
                    obj_num=obj_num,
                    gen_num=xe.gen,
                )
            elif xe.etype == XrefEntryType.InUse:
                lbl = f"obj {obj_num}  @{xe.offset}"
                en = PdfNode(label=lbl, detail="", obj_num=obj_num, gen_num=xe.gen)
            else:
                lbl = f"obj {obj_num}  in ObjStm {xe.offset}"
                en = PdfNode(label=lbl, detail="", obj_num=obj_num, gen_num=xe.gen)
            body_node.children.append(en)
        root.children.append(body_node)

        # --- XRef Table ---  (physical position: near end of file, after body)
        xref_lines = [
            f"Cross-reference table",
            f"Total entries: {len(entries)}  "
            f"(in-use: {in_use}  free: {free}  compressed: {compressed})",
            "",
            "Columns:",
            "  obj     — object number",
            "  offset  — byte offset from start of file where the object begins",
            "            (for free entries: object number of next free entry in the free list)",
            "  gen     — generation number (0 for most objects; 65535 for permanently free)",
            "  type    — n = in-use (normal object)  |  f = free (deleted/never used)",
            "",
            f"{'obj':>5}  {'offset':>10}  {'gen':>5}  type",
            "-" * 36,
        ]
        for obj_num in sorted(entries):
            xe = entries[obj_num]
            if xe.etype == XrefEntryType.Free:
                xref_lines.append(f"{obj_num:>5}  {0:010d}  {xe.gen:05d}  f")
            elif xe.etype == XrefEntryType.InUse:
                xref_lines.append(f"{obj_num:>5}  {xe.offset:010d}  {xe.gen:05d}  n")
            else:
                xref_lines.append(f"{obj_num:>5}  in ObjStm {xe.offset}")
        xref_node = PdfNode(
            label="XRef Table",
            detail="\n".join(xref_lines),
        )
        root.children.append(xref_node)

        # --- Trailer ---  (physical position: end of file)
        tr_node = PdfNode(label="Trailer", detail=_detail(self._trailer))
        if self._trailer.is_dict():
            for k, v in self._trailer.dict.items():
                lbl = f"/{k}  {_brief(v)}"
                tr_node.children.append(self._build_value_node(lbl, v, 1))
        root.children.append(tr_node)

        return root

    # ------------------------------------------------------------------
    def _build_value_node(
        self, label: str, obj: PdfObject, depth: int
    ) -> PdfNode:
        node = PdfNode(label=label, detail=_detail(obj))
        if depth >= _MAX_DEPTH:
            return node

        if obj.type == PdfObjType.Reference:
            node.obj_num = obj.ref.num
            node.gen_num = obj.ref.gen
            node.detail = f"Reference: {obj.ref.num} {obj.ref.gen} R\n\n(Select to load target object)"
            return node

        if obj.type in (PdfObjType.Dictionary, PdfObjType.Stream):
            for k, v in obj.dict.items():
                child_lbl = f"/{k}  {_brief(v)}"
                if v.is_ref():
                    child = PdfNode(
                        label=child_lbl,
                        obj_num=v.ref.num,
                        gen_num=v.ref.gen,
                        detail=_brief(v),
                    )
                    node.children.append(child)
                else:
                    node.children.append(
                        self._build_value_node(child_lbl, v, depth + 1)
                    )
        elif obj.type == PdfObjType.Array:
            for idx, item in enumerate(obj.arr):
                child_lbl = f"[{idx}]  {_brief(item)}"
                node.children.append(
                    self._build_value_node(child_lbl, item, depth + 1)
                )
        return node

    def _build_object_node(
        self, num: int, gen: int, label: str, depth: int
    ) -> PdfNode:
        node = PdfNode(label=label, obj_num=num, gen_num=gen)

        obj = self.resolve_num(num, gen)
        if obj is None:
            node.detail = "(unresolvable)"
            return node

        node.detail = _detail(obj)

        if depth >= _MAX_DEPTH:
            return node

        # Annotate label with /Type
        if obj.is_dict():
            type_obj = obj.get("Type")
            if type_obj.is_name():
                node.label = f"{label}  [/{type_obj.sval}]"
            sub_obj = obj.get("Subtype")
            if sub_obj.is_name() and sub_obj.sval == "Image":
                node.is_image = True

        # Children from dict entries
        if obj.is_dict():
            for k, v in obj.dict.items():
                child_lbl = f"/{k}  {_brief(v)}"
                if v.is_ref():
                    child = PdfNode(
                        label=child_lbl,
                        obj_num=v.ref.num,
                        gen_num=v.ref.gen,
                        detail=f"Reference: {v.ref.num} {v.ref.gen} R\n\n(Select to load target object)",
                    )
                    node.children.append(child)
                else:
                    node.children.append(
                        self._build_value_node(child_lbl, v, depth + 1)
                    )
        elif obj.is_array():
            for idx, item in enumerate(obj.arr):
                child_lbl = f"[{idx}]  {_brief(item)}"
                node.children.append(
                    self._build_value_node(child_lbl, item, depth + 1)
                )

        # Special handling for Page objects
        if obj.is_dict():
            type_obj = obj.get("Type")
            if type_obj.is_name() and type_obj.sval == "Page":
                self._add_page_children(node, obj)

        return node

    def _add_page_children(self, node: PdfNode, page_obj: PdfObject) -> None:
        """Add Content Stream(s) and XObject nodes to a Page node.

        Streams are NOT decoded here — decoding is deferred until the user
        selects the node and get_object_detail() is called.  Eager decoding
        of all page content streams during tree construction caused severe
        CPU spikes on large PDFs.
        """
        # --- Content Stream(s) ---
        cont_val = page_obj.get("Contents")
        if not cont_val.is_null():
            refs: list[PdfRef] = []
            if cont_val.is_ref():
                # /Contents may be a direct stream reference, OR a reference to
                # an array of stream references (both are valid per PDF spec §7.8.1).
                # Resolve one level to detect the array case.
                resolved = self.resolve_num(cont_val.ref.num, cont_val.ref.gen)
                if resolved is not None and resolved.is_array():
                    for item in resolved.arr:
                        if item.is_ref():
                            refs.append(item.ref)
                else:
                    refs.append(cont_val.ref)
            elif cont_val.is_array():
                for item in cont_val.arr:
                    if item.is_ref():
                        refs.append(item.ref)

            cs_node = PdfNode(label="\u25b6 Content Stream(s)  [text & drawing operators]",
                              detail="(select a stream node to decode and view)")

            for ref in refs:
                stm = self.resolve_num(ref.num, ref.gen)
                raw_len = len(stm.stream_raw) if stm else 0
                sn = PdfNode(
                    label=f"stream  {ref.num} 0 R  [{raw_len} bytes raw]",
                    obj_num=ref.num,
                    gen_num=ref.gen,
                    detail="",  # empty → frontend fetches via /api/object/...
                )
                cs_node.children.append(sn)

            node.children.append(cs_node)

        # --- XObjects (Images & Forms) ---
        res_val = page_obj.get("Resources")
        res: PdfObject | None = None
        if res_val.is_ref():
            res = self.resolve_num(res_val.ref.num, res_val.ref.gen)
        elif res_val.is_dict():
            res = res_val

        if res:
            xo_dict = res.get("XObject")
            xo_ptr: PdfObject | None = None
            if xo_dict.is_ref():
                xo_ptr = self.resolve_num(xo_dict.ref.num, xo_dict.ref.gen)
            elif xo_dict.is_dict():
                xo_ptr = xo_dict

            if xo_ptr and xo_ptr.dict:
                xo_node = PdfNode(
                    label="\U0001f5bc XObjects  (Images & Forms)",
                    detail=f"XObject resources ({len(xo_ptr.dict)} entries)",
                )
                for k, v in xo_ptr.dict.items():
                    xo: PdfObject | None = None
                    xnum, xgen = -1, 0
                    if v.is_ref():
                        xnum, xgen = v.ref.num, v.ref.gen
                        xo = self.resolve_num(xnum, xgen)
                    elif v.is_dict() or v.type == PdfObjType.Stream:
                        xo = v

                    subtype = "?"
                    extra = ""
                    is_img = False
                    if xo:
                        st = xo.get("Subtype")
                        if st.is_name():
                            subtype = st.sval
                        if subtype == "Image":
                            is_img = True
                            w = xo.get("Width")
                            h = xo.get("Height")
                            bpc = xo.get("BitsPerComponent")
                            cs = xo.get("ColorSpace")
                            if w.is_int() and h.is_int():
                                extra += f"  {w.ival}x{h.ival}"
                            if bpc.is_int():
                                extra += f"  {bpc.ival}bpc"
                            if cs.is_name():
                                extra += f"  /{cs.sval}"

                    lbl = f"/{k}  [/{subtype}]{extra}"
                    if xnum >= 0:
                        lbl += f"  ({xnum} 0 R)"

                    xn = PdfNode(
                        label=lbl,
                        obj_num=xnum,
                        gen_num=xgen,
                        detail="",  # fetched lazily
                        is_image=is_img,
                    )
                    xo_node.children.append(xn)
                node.children.append(xo_node)
