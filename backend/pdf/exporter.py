"""
PDFX Exporter — exports a PDF file to a PDFX directory.

Output structure:
  <stem>.pdfx/
    pdfx_manifest.json
    header.txt
    trailer.pdfjson
    xref.txt
    objects/
      obj_NNNNN_G.pdfjson   ← JSON source dict (AI-editable)
      obj_NNNNN_G.pdfo      ← verbatim bytes from original PDF (InUse objects only)
      obj_NNNNN_G.pdfs      ← decoded text stream content (text-stream objects only)
    resources/
      font_NNNNN_G.{ttf,otf,pfb,cff}
      image_NNNNN_G.{jpg,jp2,jbig2,bin}
      icc_NNNNN_G.icc
      attachment_NNNNN_G.<ext>
      signature_NNNNN_G.sig
      3d_NNNNN_G.{u3d,prc,bin}
      sound_NNNNN_G.{bin,...}
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .document import PdfDocument, _decode_stream, _is_binary
from .objects import PdfObject, PdfObjType
from .xref import XrefEntryType


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _obj_filename(num: int, gen: int) -> str:
    return f"obj_{num:05d}_{gen}"


def _obj_to_json_value(obj: PdfObject) -> Any:
    """Recursively convert a PdfObject to a JSON-serialisable value.

    PDF type conventions:
      Name         →  "/Name"
      Reference    →  "N G R"
      HexString    →  {"$hex": "<hex digits>"}
      LiteralStr   →  plain Python str
    """
    t = obj.type
    if t == PdfObjType.Null:
        return None
    if t == PdfObjType.Boolean:
        return obj.bval
    if t == PdfObjType.Integer:
        return obj.ival
    if t == PdfObjType.Real:
        return obj.dval
    if t == PdfObjType.Name:
        return f"/{obj.sval}"
    if t == PdfObjType.LiteralString:
        return obj.sval
    if t == PdfObjType.HexString:
        return {"$hex": obj.sval.encode("latin-1").hex()}
    if t == PdfObjType.Reference:
        return f"{obj.ref.num} {obj.ref.gen} R"
    if t == PdfObjType.Array:
        return [_obj_to_json_value(v) for v in obj.arr]
    if t in (PdfObjType.Dictionary, PdfObjType.Stream):
        return {k: _obj_to_json_value(v) for k, v in obj.dict.items()}
    return None


def _get_filter_chain(obj: PdfObject) -> list[str]:
    f_obj = obj.get("Filter")
    if f_obj.is_name():
        return [f_obj.sval]
    if f_obj.is_array():
        return [f.sval for f in f_obj.arr if f.is_name()]
    return []


def _find_header_end(data: bytes) -> int:
    """Return the offset of the first byte after the PDF header comment lines.

    A PDF header consists of:
      - The version line:  %PDF-X.Y<EOL>
      - An optional binary hint comment: %<high-byte>...<EOL>

    Returns the offset right after these lines (i.e. where real objects begin).
    """
    i = 0
    # Skip version line
    while i < len(data) and data[i] not in (0x0A, 0x0D):
        i += 1
    if i < len(data) and data[i] == 0x0D:
        i += 1
    if i < len(data) and data[i] == 0x0A:
        i += 1
    # Skip optional binary hint comment (%<high-byte>...)
    if i < len(data) and data[i] == 0x25 and i + 1 < len(data) and data[i + 1] >= 0x80:
        while i < len(data) and data[i] not in (0x0A, 0x0D):
            i += 1
        if i < len(data) and data[i] == 0x0D:
            i += 1
        if i < len(data) and data[i] == 0x0A:
            i += 1
    return i


def _get_raw_object_bytes(data: bytes, offset: int, obj: PdfObject) -> bytes:
    """Extract the verbatim bytes for an indirect object from the PDF data.

    Covers the range from 'N G obj' through 'endobj' (inclusive of one
    trailing line ending).  For stream objects we jump past the stream payload
    before searching for 'endobj' so we never mis-match a literal 'endobj'
    inside stream data.
    """
    if obj.type == PdfObjType.Stream and obj.stream_offset >= 0 and obj.stream_raw:
        # Skip past the encoded stream bytes before searching
        search_start = obj.stream_offset + len(obj.stream_raw)
    else:
        search_start = offset

    endobj_pos = data.find(b"endobj", search_start)
    if endobj_pos < 0:
        # Fallback: return everything from object start to EOF
        return data[offset:]

    end = endobj_pos + 6  # len("endobj") == 6
    # Include one trailing line ending (CR, LF, or CRLF)
    if end < len(data):
        if data[end] == 0x0D and end + 1 < len(data) and data[end + 1] == 0x0A:
            end += 2
        elif data[end] in (0x0A, 0x0D):
            end += 1

    return data[offset:end]


# ── Stream classification ─────────────────────────────────────────────────────

_MIME_TO_EXT: dict[str, str] = {
    "application/zip": "zip",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/plain": "txt",
    "image/jpeg": "jpg",
    "image/png": "png",
}


def _classify_stream(obj: PdfObject, decoded: bytes | None) -> tuple[str, str | None]:
    """Determine how to store a stream object's payload.

    Returns ``(stream_type, resource_ext)`` where:
      stream_type   : "text" | "binary"
      resource_ext  : file extension for binary resources, or None for text
    """
    filters = _get_filter_chain(obj)
    pdf_type = obj.get("Type").sval if obj.get("Type").is_name() else ""
    pdf_subtype = obj.get("Subtype").sval if obj.get("Subtype").is_name() else ""

    # ── Special structural types ──────────────────────────────────────────
    # ObjStm / XRef: their binary structure is preserved via the host object's
    # .pdfo; we still write the dict JSON but mark stream as binary.
    if pdf_type in ("ObjStm", "XRef"):
        return "binary", "bin"

    # Digital signatures
    if pdf_type == "Sig":
        return "binary", "sig"

    # XMP metadata — always text (UTF-8 XML)
    if pdf_type == "Metadata":
        return "text", None

    # CMap (includes ToUnicode)
    if pdf_type == "CMap":
        return "text", None

    # Embedded file attachments
    if pdf_type == "EmbeddedFile":
        subtype_obj = obj.get("Subtype")
        mime = subtype_obj.sval.lower() if subtype_obj.is_name() else ""
        return "binary", _MIME_TO_EXT.get(mime, "bin")

    # 3D model data
    if pdf_type == "3D":
        ext = {"U3D": "u3d", "PRC": "prc"}.get(pdf_subtype, "bin")
        return "binary", ext

    # Sound
    if pdf_type == "Sound":
        enc = obj.get("Encoding").sval if obj.get("Encoding").is_name() else ""
        return "binary", "au" if enc in ("muLaw", "ALaw") else "bin"

    # Images (XObject/Image)
    is_image = pdf_subtype == "Image" or (pdf_type == "XObject" and pdf_subtype == "Image")
    if is_image:
        if "DCTDecode" in filters:
            return "binary", "jpg"
        if "JPXDecode" in filters:
            return "binary", "jp2"
        if "JBIG2Decode" in filters:
            return "binary", "jbig2"
        return "binary", "bin"

    # Form XObjects — PostScript-like content (text)
    if pdf_subtype == "Form":
        return "text", None

    # Font programs: Length1 + Length2 → Type1 (.pfb)
    #                Length1 only      → TrueType (.ttf)
    #                Subtype Type1C    → CFF (.cff)
    #                Subtype OpenType  → OpenType (.otf)
    has_l1 = obj.get("Length1").is_int()
    has_l2 = obj.get("Length2").is_int()
    if has_l1 and has_l2:
        return "binary", "pfb"
    if has_l1:
        return "binary", "ttf"
    if pdf_subtype in ("Type1C", "CIDFontType0C"):
        return "binary", "cff"
    if pdf_subtype == "OpenType":
        return "binary", "otf"

    # ICC profiles: magic 'acsp' at byte offset 36
    if decoded and len(decoded) >= 40 and decoded[36:40] == b"acsp":
        return "binary", "icc"

    # Fallback: use the binary heuristic on decoded content
    if decoded is None or _is_binary(decoded):
        return "binary", "bin"
    return "text", None


def _resource_path(pdf_type: str, pdf_subtype: str, num: int, gen: int, ext: str) -> str:
    """Return a relative resource path under resources/."""
    tag_map = {
        "Sig": "signature",
        "EmbeddedFile": "attachment",
        "3D": "3d",
        "Sound": "sound",
    }
    tag = tag_map.get(pdf_type)
    if tag:
        return f"resources/{tag}_{num:05d}_{gen}.{ext}"
    if ext == "icc":
        return f"resources/icc_{num:05d}_{gen}.icc"
    if ext in ("ttf", "otf", "pfb", "cff"):
        return f"resources/font_{num:05d}_{gen}.{ext}"
    if ext in ("jpg", "jp2", "jbig2") or pdf_subtype == "Image":
        return f"resources/image_{num:05d}_{gen}.{ext}"
    return f"resources/stream_{num:05d}_{gen}.{ext}"


# ── Main export function ──────────────────────────────────────────────────────

def export_pdf(pdf_path: str | Path, output_dir: str | Path) -> Path:
    """Export *pdf_path* to a PDFX directory inside *output_dir*.

    Returns the path to the created ``<stem>.pdfx/`` directory.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)

    pdfx_dir = output_dir / (pdf_path.stem + ".pdfx")
    (pdfx_dir / "objects").mkdir(parents=True, exist_ok=True)
    (pdfx_dir / "resources").mkdir(parents=True, exist_ok=True)

    # ── Load PDF ──────────────────────────────────────────────────────────
    pdf_data = pdf_path.read_bytes()
    doc = PdfDocument.from_bytes(pdf_data, str(pdf_path))
    data = doc._reader.data  # type: ignore[union-attr]
    xref = doc._xref

    # ── Header ────────────────────────────────────────────────────────────
    header_lines: list[str] = [f"%PDF-{doc._version}"]
    if doc._binary_marker is not None:
        # Store non-ASCII bytes as \xNN escape sequences so the file stays UTF-8
        escaped = "%" + "".join(f"\\x{b:02x}" for b in doc._binary_marker)
        header_lines.append(escaped)
    (pdfx_dir / "header.txt").write_text(
        "\n".join(header_lines) + "\n", encoding="utf-8"
    )

    # ── Separate InUse and Compressed entries ─────────────────────────────
    in_use = {
        num: xe
        for num, xe in xref.entries.items()
        if xe.etype == XrefEntryType.InUse
    }
    compressed = {
        num: xe
        for num, xe in xref.entries.items()
        if xe.etype == XrefEntryType.Compressed
    }

    manifest_objects: list[dict] = []

    # ── Pre-compute xref info and per-object pdfo boundaries ─────────────
    # These are needed to extend each object's .pdfo to include the gap
    # bytes between it and the next object (for binary-exact roundtrip).
    # We must do this BEFORE the main loop.
    _startxref_val = 0
    _sx_offset = doc._reader.scan_backward(len(data), b"startxref", 2048)  # type: ignore[union-attr]
    if _sx_offset >= 0:
        _i = _sx_offset + 9
        while _i < len(data) and data[_i] in (0x20, 0x09, 0x0D, 0x0A):
            _i += 1
        _j = _i
        while _j < len(data) and 48 <= data[_j] <= 57:
            _j += 1
        if _j > _i:
            _startxref_val = int(data[_i:_j])

    # Detect linearized: xref appears before some InUse objects (objects exist
    # after the xref offset).  Gap bytes in linearized PDFs can contain partial
    # xref sections, so we do NOT extend pdfo in that case.
    _in_use_offsets_all = [xe.offset for xe in in_use.values()]
    _is_linearized_pre = (
        _startxref_val > 0
        and any(xe.offset > _startxref_val for xe in in_use.values())
    )

    # For stream-xref, determine which object IS the xref stream.
    # Detect xref type by inspecting the bytes at startxref_val: table-xref
    # starts with the keyword 'xref'; stream-xref is an indirect object ('N G obj').
    # Do NOT rely solely on bool(compressed) — a PDF 1.5 stream-xref may contain
    # no ObjStm objects, making bool(compressed) falsely indicate table-xref.
    if _startxref_val > 0 and _startxref_val < len(data):
        _at_sx = data[_startxref_val : _startxref_val + 10].lstrip(b"\r\n")
        _xref_type_str_pre = "table" if _at_sx[:4] == b"xref" else "stream"
    elif bool(compressed):
        _xref_type_str_pre = "stream"
    else:
        _xref_type_str_pre = "table"
    _xref_stream_num_pre: int | None = None
    if _xref_type_str_pre == "stream":
        for _n, _xe in in_use.items():
            if _xe.offset == _startxref_val:
                _xref_stream_num_pre = _n
                break

    # Compute the actual header end (past %PDF-X.Y and optional binary comment).
    # This is the offset where real PDF objects begin.
    _header_end = _find_header_end(data)

    # Sorted InUse entries for boundary computation.
    # Filter out entries whose offset falls inside the header (corrupt xref
    # entries that point at offset 0 / before the first real object).
    _sorted_inuse = sorted(in_use.items(), key=lambda kv: kv[1].offset)
    _valid_sorted_inuse = [
        (_n, _xe) for (_n, _xe) in _sorted_inuse if _xe.offset >= _header_end
    ]

    # next_boundaries[num] = first byte AFTER this object's pdfo zone.
    # For regular objects (non-xref-stream, non-linearized): extends to the
    # next object's offset (or startxref_val for the last regular object in
    # a table-xref PDF).
    # For xref stream objects: extends to the 'startxref' keyword position
    # (_sx_offset) so the gap between 'endobj' and 'startxref' is captured.
    # None → fall back to _get_raw_object_bytes().
    _next_boundaries: dict[int, int | None] = {}
    if not _is_linearized_pre:
        for _idx, (_n, _xe) in enumerate(_valid_sorted_inuse):
            if _n == _xref_stream_num_pre:
                # Xref stream: extend pdfo to include gap bytes before 'startxref'
                _next_boundaries[_n] = _sx_offset if _sx_offset >= 0 else None
                continue
            if _idx + 1 < len(_valid_sorted_inuse):
                _next_n, _next_xe = _valid_sorted_inuse[_idx + 1]
                _next_boundaries[_n] = _next_xe.offset
            else:
                # Last object in table-xref: extend to xref table start
                _next_boundaries[_n] = _startxref_val

    # ── Process InUse objects ─────────────────────────────────────────────
    for num, xe in sorted(in_use.items(), key=lambda kv: kv[1].offset):
        # Skip objects whose xref offset falls inside the PDF header — these are
        # corrupt xref entries (e.g. offset=0 pointing to the %PDF- line).
        if xe.offset < _header_end:
            continue
        obj = doc.resolve_num(num, xe.gen)
        if obj is None:
            continue

        fname = _obj_filename(num, xe.gen)

        # Decode stream (if present)
        decoded: bytes | None = None
        if obj.type == PdfObjType.Stream:
            decoded = _decode_stream(obj)

        # Classify stream content
        stream_type: str | None = None
        resource_ext: str | None = None
        resource_file: str | None = None
        is_signature = False

        if obj.type == PdfObjType.Stream:
            stream_type, resource_ext = _classify_stream(obj, decoded)
            pdf_type_val = obj.get("Type").sval if obj.get("Type").is_name() else ""
            is_signature = (pdf_type_val == "Sig")

        # ── Build JSON source dict ────────────────────────────────────────
        pdf_type_str = obj.get("Type").sval if obj.get("Type").is_name() else ""
        pdf_subtype_str = obj.get("Subtype").sval if obj.get("Subtype").is_name() else ""
        type_hint = pdf_type_str
        if pdf_subtype_str:
            type_hint += f" / {pdf_subtype_str}"

        filters = _get_filter_chain(obj) if obj.type == PdfObjType.Stream else []

        meta: dict[str, Any] = {
            "_obj": f"{num} {xe.gen}",
            "_type": type_hint or obj.type.name,
            "_stream": stream_type or "none",
        }
        if stream_type == "text":
            meta["_stream_file"] = f"{fname}.pdfs"
        if filters:
            meta["_stream_encoding"] = filters

        # dict values (stream dict keys are included; stream_raw excluded)
        dict_values = _obj_to_json_value(obj)
        if isinstance(dict_values, dict):
            # Remove Length — linker recomputes it
            dict_values.pop("Length", None)
        else:
            dict_values = {}

        source_json: dict[str, Any] = {**meta, **dict_values}

        # ── Handle stream payload ─────────────────────────────────────────
        pdfs_sha256: str | None = None

        if obj.type == PdfObjType.Stream and stream_type is not None:
            if stream_type == "text":
                if decoded is not None:
                    pdfs_bytes = decoded
                    (pdfx_dir / "objects" / f"{fname}.pdfs").write_bytes(pdfs_bytes)
                    pdfs_sha256 = _sha256(pdfs_bytes)

            elif stream_type == "binary" and resource_ext is not None:
                # For JPEG/JP2/JBIG2 images and signatures: store raw (encoded) bytes —
                # these are already valid file formats and require no re-encoding.
                # For everything else: store decoded bytes.
                keep_encoded = resource_ext in ("jpg", "jp2", "jbig2", "sig")
                resource_bytes = obj.stream_raw if keep_encoded else (
                    decoded if decoded is not None else obj.stream_raw
                )

                resource_file = _resource_path(pdf_type_str, pdf_subtype_str, num, xe.gen, resource_ext)
                (pdfx_dir / resource_file).write_bytes(resource_bytes)

                source_json["_resource"] = resource_file

        # Write .pdfjson (may have been updated with _resource)
        src_bytes = json.dumps(source_json, indent=2, ensure_ascii=False).encode("utf-8")
        (pdfx_dir / "objects" / f"{fname}.pdfjson").write_bytes(src_bytes)

        # Write .pdfo (verbatim bytes, extended to next object's offset for
        # binary-exact gap preservation on the unmodified reconstruction path).
        _boundary = _next_boundaries.get(num) if _next_boundaries else None
        if _boundary is not None and _boundary > xe.offset:
            raw_bytes = data[xe.offset:_boundary]
        else:
            raw_bytes = _get_raw_object_bytes(data, xe.offset, obj)
        (pdfx_dir / "objects" / f"{fname}.pdfo").write_bytes(raw_bytes)

        # Checksums
        obj_sha256 = _sha256(raw_bytes)
        src_sha256 = _sha256(src_bytes)

        obj_type_str = (
            "stream" if obj.type == PdfObjType.Stream
            else "dict" if obj.is_dict()
            else obj.type.name.lower()
        )

        manifest_objects.append({
            "num": num,
            "gen": xe.gen,
            "byte_offset": xe.offset,
            "byte_length": len(raw_bytes),
            "obj_sha256": obj_sha256,
            "src_sha256": src_sha256,
            "pdfs_sha256": pdfs_sha256,
            "type": obj_type_str,
            "pdf_type": pdf_type_str or None,
            "pdf_subtype": pdf_subtype_str or None,
            "has_stream": obj.type == PdfObjType.Stream,
            "stream_encoding": filters or None,
            "stream_length": len(obj.stream_raw) if obj.type == PdfObjType.Stream else None,
            "stream_type": stream_type,
            "resource_file": resource_file,
            "is_signature": is_signature,
            "in_objstm": False,
            "objstm_host": None,
            "objstm_index": None,
        })

    # ── Process Compressed objects (inside ObjStm) ────────────────────────
    for num, xe in sorted(compressed.items()):
        obj = doc.resolve_num(num)
        if obj is None:
            continue

        fname = _obj_filename(num, 0)  # compressed objects always have gen 0

        pdf_type_str = obj.get("Type").sval if obj.get("Type").is_name() else ""
        pdf_subtype_str = obj.get("Subtype").sval if obj.get("Subtype").is_name() else ""
        type_hint = pdf_type_str
        if pdf_subtype_str:
            type_hint += f" / {pdf_subtype_str}"

        dict_values = _obj_to_json_value(obj)
        if not isinstance(dict_values, dict):
            dict_values = {}

        meta = {
            "_obj": f"{num} 0",
            "_type": type_hint or obj.type.name,
            "_stream": "none",
            "_in_objstm": True,
            "_objstm_host": int(xe.offset),   # xe.offset == host ObjStm obj num
            "_objstm_index": int(xe.index_in_stm),
        }
        source_json = {**meta, **dict_values}
        src_bytes = json.dumps(source_json, indent=2, ensure_ascii=False).encode("utf-8")
        (pdfx_dir / "objects" / f"{fname}.pdfjson").write_bytes(src_bytes)
        src_sha256 = _sha256(src_bytes)

        obj_type_str = "dict" if obj.is_dict() else obj.type.name.lower()

        manifest_objects.append({
            "num": num,
            "gen": 0,
            "byte_offset": None,
            "byte_length": None,
            "obj_sha256": None,
            "src_sha256": src_sha256,
            "pdfs_sha256": None,
            "type": obj_type_str,
            "pdf_type": pdf_type_str or None,
            "pdf_subtype": pdf_subtype_str or None,
            "has_stream": False,
            "stream_encoding": None,
            "stream_length": None,
            "stream_type": None,
            "resource_file": None,
            "is_signature": False,
            "in_objstm": True,
            "objstm_host": int(xe.offset),
            "objstm_index": int(xe.index_in_stm),
        })

    # ── Trailer ───────────────────────────────────────────────────────────
    trailer_json = _obj_to_json_value(doc._trailer) or {}
    (pdfx_dir / "trailer.pdfjson").write_bytes(
        json.dumps(trailer_json, indent=2, ensure_ascii=False).encode("utf-8")
    )

    # ── xref.txt (informational) ──────────────────────────────────────────
    # Reuse the xref type already determined in the pre-computation section.
    xref_type_str = _xref_type_str_pre
    xref_lines: list[str] = [
        "%% PDFX Cross-reference table (informational)",
        f"%% xref_type: {xref_type_str}",
        f"%% object_count: {len(xref.entries)}",
        "%%",
        "%% num    gen   offset      status",
    ]
    for n, xe in sorted(xref.entries.items()):
        if xe.etype == XrefEntryType.Free:
            xref_lines.append(f"  {n:<7} {xe.gen:<5} {'0':<11} free")
        elif xe.etype == XrefEntryType.InUse:
            xref_lines.append(f"  {n:<7} {xe.gen:<5} {xe.offset:<11} in-use")
        else:
            xref_lines.append(
                f"  {n:<7} {'0':<5} {'(objstm:' + str(int(xe.offset)) + ')':<11} compressed"
            )
    (pdfx_dir / "xref.txt").write_text(
        "\n".join(xref_lines) + "\n", encoding="utf-8"
    )

    # ── Manifest ──────────────────────────────────────────────────────────
    # Re-use values already computed in the pre-computation section above.
    startxref_val = _startxref_val
    is_linearized = _is_linearized_pre

    # ── xref_raw.bin (table xref only) ───────────────────────────────────
    # For table-xref PDFs the entire xref+trailer section starting at
    # startxref_val is stored verbatim so the linker can reproduce it
    # byte-for-byte on the unmodified path.
    #
    # For LINEARIZED PDFs skip: the xref can appear before most objects so
    # data[startxref_val:] would cover almost the whole file.
    # Store the raw pre-first-object bytes so the linker can write them
    # verbatim (preserves EOLs, binary comment, and any gap whitespace).
    # Use the first valid object's offset (min of _valid_sorted_inuse offsets)
    # rather than _header_end, which is only used to filter corrupt xref entries.
    _first_obj_offset = _valid_sorted_inuse[0][1].offset if _valid_sorted_inuse else _header_end
    (pdfx_dir / "header.bin").write_bytes(data[:_first_obj_offset])

    if xref_type_str == "table" and not is_linearized and startxref_val > 0 and startxref_val < len(data):
        (pdfx_dir / "xref_raw.bin").write_bytes(data[startxref_val:])

    # For stream-xref PDFs store the verbatim 'startxref...%%EOF' tail so the
    # linker can reproduce it exactly (preserving EOL style and any trailing bytes).
    if xref_type_str == "stream" and _sx_offset >= 0:
        (pdfx_dir / "eof_tail.bin").write_bytes(data[_sx_offset:])

    trailer_obj = doc._trailer
    manifest: dict[str, Any] = {
        "pdfx_version": "1.0",
        "source_filename": pdf_path.name,
        "source_sha256": _sha256(pdf_data),
        "pdf_version": doc._version,
        "pdf_size_bytes": len(pdf_data),
        "object_count": len(xref.entries),
        "xref_type": xref_type_str,
        "linearized": is_linearized,
        "encrypted": False,    # TODO: detect /Encrypt in trailer
        "trailer": {
            "Size": trailer_obj.get("Size").ival if trailer_obj.get("Size").is_int() else None,
            "Root": _obj_to_json_value(trailer_obj.get("Root")),
            "Info": _obj_to_json_value(trailer_obj.get("Info")),
        },
        "startxref": startxref_val,
        "objects": sorted(manifest_objects, key=lambda e: (e["num"], e["gen"])),
    }

    (pdfx_dir / "pdfx_manifest.json").write_bytes(
        json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    )

    return pdfx_dir
