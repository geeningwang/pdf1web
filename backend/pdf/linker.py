"""
PDFX Linker — reconstructs a PDF from a PDFX export directory.

This is the inverse of exporter.py (Stage 2 of the export/link pipeline).

Algorithm:
  1. Read pdfx_manifest.json.
  2. Write the PDF header (decoded from header.txt).
  3. For each InUse object in original byte-offset order:
       - If unmodified (SHA-256 matches manifest): write .pdfo verbatim → binary-exact.
       - If modified: re-serialize from .pdfjson + .pdfs / resource file.
  4. Re-pack any ObjStm host whose compressed members were modified.
  5. Reconstruct the cross-reference (table or stream) from recorded byte offsets.
  6. Write startxref + %%EOF.

Binary-exact guarantee (unmodified path):
  Every object is written at the same byte offset as in the original PDF.
  The xref encodes the same offsets → the output is byte-for-byte identical.
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import zlib
from pathlib import Path
from typing import Any


# ── Utilities ─────────────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_obj_modified(pdfx_dir: Path, entry: dict) -> bool:
    """Return True if the object's source files differ from the manifest checksums."""
    if entry["is_signature"]:
        return False  # signatures are always written verbatim

    num, gen = entry["num"], entry["gen"]
    fname = f"obj_{num:05d}_{gen}"

    pdfjson = pdfx_dir / "objects" / f"{fname}.pdfjson"
    if pdfjson.exists() and entry.get("src_sha256"):
        if _sha256(pdfjson.read_bytes()) != entry["src_sha256"]:
            return True

    if entry.get("pdfs_sha256"):
        pdfs = pdfx_dir / "objects" / f"{fname}.pdfs"
        if pdfs.exists() and _sha256(pdfs.read_bytes()) != entry["pdfs_sha256"]:
            return True

    return False


# ── Header ────────────────────────────────────────────────────────────────────

def _read_header(pdfx_dir: Path) -> bytes:
    """Return verbatim header bytes (bytes before the first object).

    Uses ``header.bin`` when present (exact original bytes).  Falls back to
    reconstructing from ``header.txt`` for PDFX exports created before
    ``header.bin`` was introduced.
    """
    header_bin = pdfx_dir / "header.bin"
    if header_bin.exists():
        return header_bin.read_bytes()
    return _parse_header(pdfx_dir / "header.txt")


def _parse_header(header_path: Path) -> bytes:
    """Reconstruct raw PDF header bytes from the header.txt file.

    header.txt uses ``\\xNN`` escape sequences for non-ASCII bytes in the
    binary-comment line (e.g. ``%\\xbf\\xf7\\xa2\\xfe``).
    """
    out = io.BytesIO()
    for line in header_path.read_text(encoding="utf-8").splitlines():
        if "\\x" in line:
            raw = bytearray()
            i = 0
            while i < len(line):
                if line[i : i + 2] == "\\x" and i + 3 < len(line):
                    raw.append(int(line[i + 2 : i + 4], 16))
                    i += 4
                else:
                    raw.extend(line[i].encode("latin-1"))
                    i += 1
            out.write(bytes(raw))
        else:
            out.write(line.encode("latin-1"))
        out.write(b"\n")
    return out.getvalue()


# ── JSON → PDF serialization ──────────────────────────────────────────────────

def _is_ref(s: str) -> bool:
    parts = s.split()
    return len(parts) == 3 and parts[2] == "R" and parts[0].isdigit() and parts[1].isdigit()


def _encode_name(name: str) -> bytes:
    """Encode a PDF name (already has leading /) with # escaping."""
    out = bytearray(b"/")
    for ch in name[1:].encode("latin-1", errors="replace"):
        if ch < 33 or ch > 126 or ch in b"#()<>[]{}/%":
            out.extend(f"#{ch:02X}".encode())
        else:
            out.append(ch)
    return bytes(out)


def _encode_literal_string(s: str) -> bytes:
    out = bytearray(b"(")
    for ch in s.encode("latin-1", errors="replace"):
        if ch == ord("\\"):
            out.extend(b"\\\\")
        elif ch == ord("("):
            out.extend(b"\\(")
        elif ch == ord(")"):
            out.extend(b"\\)")
        elif ch == ord("\r"):
            out.extend(b"\\r")
        elif ch == ord("\n"):
            out.extend(b"\\n")
        else:
            out.append(ch)
    out.append(ord(")"))
    return bytes(out)


def _real_to_bytes(v: float) -> bytes:
    """Format a PDF real number without scientific notation."""
    if v == int(v):
        return str(int(v)).encode()
    s = f"{v:.10g}"
    return s.encode()


def _json_to_pdf(value: Any) -> bytes:
    """Recursively convert a JSON value back to PDF token bytes."""
    if value is None:
        return b"null"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return str(value).encode()
    if isinstance(value, float):
        return _real_to_bytes(value)
    if isinstance(value, str):
        if _is_ref(value):
            return value.encode()
        if value.startswith("/"):
            return _encode_name(value)
        return _encode_literal_string(value)
    if isinstance(value, dict):
        if "$hex" in value:
            return b"<" + value["$hex"].encode() + b">"
        # Dictionary — skip metadata keys (start with _)
        parts = [b"<<"]
        for k, v in value.items():
            if not k.startswith("_"):
                parts.append(b"/" + k.encode() + b" " + _json_to_pdf(v))
        parts.append(b">>")
        return b"\n".join(parts)
    if isinstance(value, list):
        return b"[" + b" ".join(_json_to_pdf(v) for v in value) + b"]"
    return b"null"


def _serialize_inuse_object(
    num: int, gen: int, obj_json: dict, stream_bytes: bytes | None
) -> bytes:
    """Produce the full ``N G obj ... endobj`` bytes for an InUse object."""
    clean = {k: v for k, v in obj_json.items() if not k.startswith("_")}
    if stream_bytes is not None:
        clean["Length"] = len(stream_bytes)

    result = f"{num} {gen} obj\n".encode()
    result += _json_to_pdf(clean) + b"\n"
    if stream_bytes is not None:
        result += b"stream\n"
        result += stream_bytes
        if not stream_bytes.endswith(b"\n"):
            result += b"\n"
        result += b"endstream\n"
    result += b"endobj\n"
    return result


# ── Stream encoding ───────────────────────────────────────────────────────────

def _encode_stream(raw: bytes, filter_chain: list[str]) -> bytes:
    """Re-encode stream content by applying filter_chain (outermost filter last)."""
    data = raw
    for f in reversed(filter_chain):
        if f == "FlateDecode":
            data = zlib.compress(data, level=6)
        elif f == "ASCIIHexDecode":
            data = data.hex().upper().encode() + b">"
        elif f == "ASCII85Decode":
            data = _ascii85_encode(data)
        # Other filters (JBIG2, CCITT, etc.) are not encodeable here;
        # they're only used for binary resources that are stored pre-encoded.
    return data


def _ascii85_encode(data: bytes) -> bytes:
    out = io.BytesIO()
    for i in range(0, len(data), 4):
        group = data[i : i + 4]
        if len(group) == 4:
            val = struct.unpack(">I", group)[0]
            if val == 0:
                out.write(b"z")
                continue
            chars = []
            for _ in range(5):
                chars.append(val % 85 + 33)
                val //= 85
            out.write(bytes(reversed(chars)))
        else:
            padded = group + b"\x00" * (4 - len(group))
            val = struct.unpack(">I", padded)[0]
            chars = []
            for _ in range(5):
                chars.append(val % 85 + 33)
                val //= 85
            out.write(bytes(reversed(chars))[: len(group) + 1])
    out.write(b"~>")
    return out.getvalue()


# ── Object re-serialization (modified path) ───────────────────────────────────

def _build_modified_object(
    pdfx_dir: Path, entry: dict
) -> bytes:
    """Re-serialize a modified InUse object from its .pdfjson + .pdfs / resource."""
    num, gen = entry["num"], entry["gen"]
    fname = f"obj_{num:05d}_{gen}"

    obj_json: dict = json.loads(
        (pdfx_dir / "objects" / f"{fname}.pdfjson").read_text(encoding="utf-8")
    )

    stream_bytes: bytes | None = None
    stream_type = obj_json.get("_stream", "none")

    if stream_type == "text":
        pdfs_path = pdfx_dir / "objects" / f"{fname}.pdfs"
        raw = pdfs_path.read_bytes()
        filter_chain: list[str] = obj_json.get("_stream_encoding") or []
        stream_bytes = _encode_stream(raw, filter_chain)

    elif stream_type == "binary":
        resource = obj_json.get("_resource")
        if resource:
            resource_path = pdfx_dir / resource
            raw = resource_path.read_bytes()
            filter_chain = obj_json.get("_stream_encoding") or []
            ext = resource_path.suffix.lstrip(".")
            # JPEG/JP2/JBIG2/sig are stored pre-encoded → write as-is
            if ext in ("jpg", "jp2", "jbig2", "sig"):
                stream_bytes = raw
            else:
                stream_bytes = _encode_stream(raw, filter_chain)

    return _serialize_inuse_object(num, gen, obj_json, stream_bytes)


# ── ObjStm packing ────────────────────────────────────────────────────────────

def _pack_objstm(
    pdfx_dir: Path,
    host_entry: dict,
    members: list[tuple[int, int]],  # [(index, num), ...]
) -> bytes:
    """Repack a modified ObjStm and return the full ``N G obj ... endobj`` bytes.

    members is sorted by (index, num).
    """
    num, gen = host_entry["num"], host_entry["gen"]
    fname = f"obj_{num:05d}_{gen}"
    host_json: dict = json.loads(
        (pdfx_dir / "objects" / f"{fname}.pdfjson").read_text(encoding="utf-8")
    )

    # Serialize each compressed member to its raw PDF value
    obj_bodies: list[bytes] = []
    obj_nums: list[int] = []
    for _idx, m_num in sorted(members):
        m_fname = f"obj_{m_num:05d}_0"
        m_json: dict = json.loads(
            (pdfx_dir / "objects" / f"{m_fname}.pdfjson").read_text(encoding="utf-8")
        )
        clean = {k: v for k, v in m_json.items() if not k.startswith("_")}
        obj_bodies.append(_json_to_pdf(clean))
        obj_nums.append(m_num)

    # Build the offset header: "num1 off1 num2 off2 ..."
    # Offsets are relative to the start of the data section (after the header).
    # We accumulate offsets as we go, then build the header string.
    data_parts: list[bytes] = []
    offsets: list[int] = []
    cursor = 0
    for body in obj_bodies:
        offsets.append(cursor)
        data_parts.append(body)
        data_parts.append(b"\n")
        cursor += len(body) + 1

    header_parts = []
    for obj_num, off in zip(obj_nums, offsets):
        header_parts.append(f"{obj_num} {off}")
    header_bytes = " ".join(header_parts).encode() + b"\n"

    first_offset = len(header_bytes)
    stream_content = header_bytes + b"".join(data_parts)

    # Apply filter chain from original host dict
    filter_chain: list[str] = host_json.get("_stream_encoding") or []
    encoded = _encode_stream(stream_content, filter_chain)

    # Build updated host dict
    clean_host = {k: v for k, v in host_json.items() if not k.startswith("_")}
    clean_host["N"] = len(obj_nums)
    clean_host["First"] = first_offset
    clean_host["Length"] = len(encoded)

    result = f"{num} {gen} obj\n".encode()
    result += _json_to_pdf(clean_host) + b"\n"
    result += b"stream\n"
    result += encoded
    if not encoded.endswith(b"\n"):
        result += b"\n"
    result += b"endstream\nendobj\n"
    return result


# ── Xref reconstruction ───────────────────────────────────────────────────────

# recorded_offsets: {num: (offset_or_host, gen_or_0, 'n'|'f'|'c', idx_or_None)}
_OffsetEntry = tuple[int, int, str, int | None]


def _build_xref_table(recorded: dict[int, _OffsetEntry], trailer_json: dict) -> bytes:
    """Build a classic xref table + trailer dict bytes.

    Each entry is exactly 20 bytes: 10-digit-offset SP 5-digit-gen SP status CR LF
    """
    max_num = max(recorded.keys()) if recorded else 0
    lines: list[bytes] = [b"xref\n", f"0 {max_num + 1}\n".encode()]

    for n in range(max_num + 1):
        if n in recorded:
            offset, gen, status, _ = recorded[n]
        else:
            offset, gen, status = 0, 65535, "f"
        # 20 bytes exactly: OOOOOOOOOO SP GGGGG SP X CR LF
        lines.append(f"{offset:010d} {gen:05d} {status}\r\n".encode())

    clean_trailer = {k: v for k, v in trailer_json.items() if not k.startswith("_")}
    lines.append(b"trailer\n")
    lines.append(_json_to_pdf(clean_trailer) + b"\n")
    return b"".join(lines)


def _build_xref_stream(
    recorded: dict[int, _OffsetEntry],
    trailer_json: dict,
    xref_obj_num: int,
) -> bytes:
    """Build a PDF 1.5+ cross-reference stream object.

    Uses W=[1, 4, 2] (type:1 byte, field2:4 bytes, field3:2 bytes).
    """
    max_num = max(recorded.keys()) if recorded else 0

    stream_data = bytearray()
    for n in range(max_num + 1):
        if n in recorded:
            field2, field3_val, status, idx = recorded[n]
            if status == "f":
                typ = 0
                f2, f3 = field2, field3_val
            elif status == "c":
                typ = 2
                f2 = field2  # host ObjStm object number
                f3 = idx or 0
            else:  # 'n'
                typ = 1
                f2 = field2  # byte offset
                f3 = field3_val  # gen
        else:
            typ, f2, f3 = 0, 0, 65535

        stream_data.append(typ)
        stream_data.extend(struct.pack(">I", f2))   # 4 bytes
        stream_data.extend(struct.pack(">H", f3))   # 2 bytes

    compressed = zlib.compress(bytes(stream_data), level=6)

    # Build xref stream dict (merge with trailer fields)
    clean = {k: v for k, v in trailer_json.items() if not k.startswith("_")}
    # Overwrite structural fields
    clean["Type"] = "/XRef"
    clean["W"] = [1, 4, 2]
    clean["Index"] = [0, max_num + 1]
    clean["Size"] = max_num + 1
    clean["Filter"] = "/FlateDecode"
    clean["Length"] = len(compressed)
    # Remove stale fields that we're regenerating
    clean.pop("DecodeParms", None)

    result = f"{xref_obj_num} 0 obj\n".encode()
    result += _json_to_pdf(clean) + b"\n"
    result += b"stream\n"
    result += compressed
    if not compressed.endswith(b"\n"):
        result += b"\n"
    result += b"endstream\nendobj\n"
    return result


# ── Main link function ────────────────────────────────────────────────────────

def link_pdf(pdfx_dir: str | Path, output_path: str | Path) -> Path:
    """Reconstruct a PDF from the PDFX directory at *pdfx_dir*.

    Writes the result to *output_path* and returns the path.
    If nothing was modified, the output is byte-for-byte identical to the
    original PDF.
    """
    pdfx_dir = Path(pdfx_dir)
    output_path = Path(output_path)

    # ── Load manifest ──────────────────────────────────────────────────────
    manifest = json.loads((pdfx_dir / "pdfx_manifest.json").read_text(encoding="utf-8"))
    objects: list[dict] = manifest["objects"]
    xref_type: str = manifest.get("xref_type", "table")
    orig_startxref: int = manifest.get("startxref", 0)
    is_linearized: bool = manifest.get("linearized", False)

    by_num: dict[int, dict] = {o["num"]: o for o in objects}

    in_use: list[dict] = sorted(
        [o for o in objects if not o["in_objstm"]],
        key=lambda x: x["byte_offset"],
    )
    compressed_objs: list[dict] = [o for o in objects if o["in_objstm"]]

    # ── Identify xref stream object (stream xref only) ────────────────────
    xref_obj_num: int | None = None
    if xref_type == "stream":
        for o in in_use:
            if o["byte_offset"] == orig_startxref:
                xref_obj_num = o["num"]
                break
        # Fallback: look for Type=XRef in .pdfjson
        if xref_obj_num is None:
            for o in in_use:
                fname = f"obj_{o['num']:05d}_{o['gen']}.pdfjson"
                p = pdfx_dir / "objects" / fname
                if p.exists():
                    d = json.loads(p.read_text())
                    if d.get("Type") == "/XRef":
                        xref_obj_num = o["num"]
                        break

    # ── ObjStm groups ─────────────────────────────────────────────────────
    # {host_num: [(index, compressed_num), ...]}
    objstm_groups: dict[int, list[tuple[int, int]]] = {}
    for co in compressed_objs:
        host = co["objstm_host"]
        objstm_groups.setdefault(host, []).append((co["objstm_index"], co["num"]))
    for host in objstm_groups:
        objstm_groups[host].sort()

    # ── Determine modifications ────────────────────────────────────────────
    modified_in_use: set[int] = {
        o["num"] for o in in_use if _is_obj_modified(pdfx_dir, o)
    }
    modified_compressed: set[int] = {
        o["num"]
        for o in compressed_objs
        if _is_obj_modified(pdfx_dir, o)
    }
    # ObjStm hosts that need repacking (a compressed member is modified)
    repacked_hosts: set[int] = {
        host
        for host, members in objstm_groups.items()
        if any(m_num in modified_compressed for _, m_num in members)
    }
    any_modified = bool(modified_in_use or modified_compressed)

    # ── Trailer ───────────────────────────────────────────────────────────
    trailer_json: dict = json.loads(
        (pdfx_dir / "trailer.pdfjson").read_text(encoding="utf-8")
    )

    # ── Write output ───────────────────────────────────────────────────────
    out = io.BytesIO()
    out.write(_read_header(pdfx_dir))

    # recorded_offsets: {num: (value, gen, status, idx)}
    recorded: dict[int, _OffsetEntry] = {0: (0, 65535, "f", None)}

    def _write_inuse(o: dict) -> None:
        num, gen = o["num"], o["gen"]
        offset = out.tell()

        if num in repacked_hosts:
            # ObjStm host needs repacking
            obj_bytes = _pack_objstm(pdfx_dir, o, objstm_groups[num])
        elif num in modified_in_use:
            obj_bytes = _build_modified_object(pdfx_dir, o)
        else:
            # Verbatim — binary exact
            pdfo = pdfx_dir / "objects" / f"obj_{num:05d}_{gen}.pdfo"
            obj_bytes = pdfo.read_bytes()

        out.write(obj_bytes)
        recorded[num] = (offset, gen, "n", None)

    # Write InUse objects in original offset order (skip xref stream — written last)
    for o in in_use:
        if xref_type == "stream" and o["num"] == xref_obj_num:
            continue
        _write_inuse(o)

    # Record compressed-object xref entries
    for co in compressed_objs:
        recorded[co["num"]] = (co["objstm_host"], 0, "c", co["objstm_index"])

    # ── Write xref + trailer ───────────────────────────────────────────────
    if xref_type == "table":
        xref_pos = out.tell()
        xref_raw_path = pdfx_dir / "xref_raw.bin"
        if not any_modified and not is_linearized and xref_raw_path.exists():
            # Unmodified path: all objects were written at their original byte
            # offsets, so xref_pos == the original startxref value.
            # Write xref_raw.bin verbatim — it already contains the correct
            # startxref value and preserves the original EOL style exactly.
            xref_raw = xref_raw_path.read_bytes()
            out.write(xref_raw)
        else:
            out.write(_build_xref_table(recorded, trailer_json))
            out.write(f"startxref\n{xref_pos}\n%%EOF\n".encode())

    else:  # stream
        xref_obj_entry = by_num.get(xref_obj_num) if xref_obj_num is not None else None
        xref_pos = out.tell()

        if (
            not any_modified
            and xref_obj_entry is not None
            and xref_obj_num not in modified_in_use
        ):
            # Unmodified → write xref stream verbatim (offsets are unchanged).
            # The .pdfo for the xref stream now extends to the 'startxref'
            # keyword position (includes the gap between 'endobj' and 'startxref').
            pdfo = pdfx_dir / "objects" / f"obj_{xref_obj_num:05d}_0.pdfo"
            out.write(pdfo.read_bytes())
        else:
            # Regenerate xref stream with new offsets
            if xref_obj_num is None:
                xref_obj_num = max(recorded.keys()) + 1
            out.write(_build_xref_stream(recorded, trailer_json, xref_obj_num))

        if xref_obj_num is not None:
            recorded[xref_obj_num] = (xref_pos, 0, "n", None)

        # Append 'startxref...%%EOF'.  Use the verbatim eof_tail.bin when
        # present (preserves original EOL style and any trailing bytes).
        # Replace only the numeric startxref value with the current xref_pos.
        eof_tail_path = pdfx_dir / "eof_tail.bin"
        if eof_tail_path.exists():
            eof_tail = eof_tail_path.read_bytes()
            # The tail starts with 'startxref'; find and replace the number.
            sx_start = eof_tail.find(b"startxref")
            if sx_start >= 0:
                num_start = sx_start + 9
                while num_start < len(eof_tail) and eof_tail[num_start] in (0x20, 0x09, 0x0D, 0x0A):
                    num_start += 1
                num_end = num_start
                while num_end < len(eof_tail) and 48 <= eof_tail[num_end] <= 57:
                    num_end += 1
                new_eof_tail = (
                    eof_tail[:num_start]
                    + str(xref_pos).encode()
                    + eof_tail[num_end:]
                )
                out.write(new_eof_tail)
            else:
                out.write(eof_tail)
        else:
            out.write(f"startxref\n{xref_pos}\n%%EOF\n".encode())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(out.getvalue())
    return output_path
