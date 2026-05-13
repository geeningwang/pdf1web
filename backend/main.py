"""
FastAPI backend for pdf1web — PDF Structure Analyzer Web App.
"""
from __future__ import annotations

import io
import logging
import os
import struct
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from pdf.document import PdfDocument, _decode_stream, _detail, _is_binary
from pdf.icc import parse_icc_profile
from pdf.jpeg import parse_jpeg
from pdf.ccitt import parse_ccitt
from pdf.flat import parse_flat_image
from pdf.content_stream import parse_content_stream, is_content_stream as _is_content_stream_data
from pdf.objects import PdfObjType, PdfObject
from pdf.xref import XrefEntryType

app = FastAPI(title="pdf1web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store: upload_id -> PdfDocument
_sessions: dict[str, PdfDocument] = {}
# Reverse-reference cache: upload_id -> {obj_num -> [{from_num, from_gen, key_path, type_name}]}
_backref_cache: dict[str, dict[int, list[dict]]] = {}
_MAX_SESSIONS = 20  # evict oldest if exceeded

# Directory where uploaded PDFs and their analysis logs are persisted
_UPLOADS_DIR = Path(__file__).parent / "uploads"
_UPLOADS_DIR.mkdir(exist_ok=True)

# Directory for persistently stored PDFs
_STORE_DIR = Path(__file__).parent / "store"
_STORE_DIR.mkdir(exist_ok=True)

# Serve built frontend static files if the dist folder exists
_DIST = Path(__file__).parent.parent / "frontend" / "dist"


def _make_id() -> str:
    return str(uuid.uuid4())


def _evict_if_needed() -> None:
    if len(_sessions) >= _MAX_SESSIONS:
        oldest = next(iter(_sessions))
        del _sessions[oldest]
        _backref_cache.pop(oldest, None)


def _build_backref_index(doc: PdfDocument) -> dict[int, list[dict]]:
    """Build a full reverse-reference index for a parsed document."""
    from pdf.xref import XrefEntryType
    index: dict[int, list[dict]] = {}

    for num, entry in doc._xref.entries.items():
        if entry.etype == XrefEntryType.Free:
            continue
        obj = doc.resolve_num(num, entry.gen)
        if obj is None:
            continue
        # Get the Type name of the referencing object for display
        type_v = obj.get("Type") if (obj.is_dict() or obj.type == PdfObjType.Stream) else None
        type_name = type_v.sval if (type_v and type_v.is_name()) else obj.type.name

        # BFS through dict / array values to find all outgoing references
        pending: list[tuple[Any, list[str]]] = []
        if obj.is_dict() or obj.type == PdfObjType.Stream:
            for key, val in obj.dict.items():
                pending.append((val, [key]))
        elif obj.is_array():
            for i, val in enumerate(obj.arr):
                pending.append((val, [f"[{i}]"]))

        while pending:
            cur, cur_path = pending.pop()
            if cur.type == PdfObjType.Reference:
                tgt = cur.ref.num
                if tgt not in index:
                    index[tgt] = []
                index[tgt].append({
                    "from_num": num,
                    "from_gen": entry.gen,
                    "key_path": ".".join(cur_path),
                    "type_name": type_name,
                })
            elif cur.is_dict() or cur.type == PdfObjType.Stream:
                for k, v in cur.dict.items():
                    pending.append((v, cur_path + [k]))
            elif cur.is_array():
                for i, v in enumerate(cur.arr):
                    pending.append((v, cur_path + [f"[{i}]"]))

    return index


def _save_upload(upload_id: str, filename: str, data: bytes, doc: PdfDocument) -> None:
    """Persist the raw PDF and an analysis log under uploads/<upload_id>/."""
    upload_dir = _UPLOADS_DIR / upload_id
    upload_dir.mkdir(exist_ok=True)

    # Save raw PDF bytes
    (upload_dir / filename).write_bytes(data)

    # Build analysis log
    lines: list[str] = []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines.append(f"=== pdf1web analysis log ===")
    lines.append(f"timestamp : {ts}")
    lines.append(f"upload_id : {upload_id}")
    lines.append(f"filename  : {filename}")
    lines.append(f"file_size : {len(data)} bytes")
    lines.append(f"pdf_version: {doc.version()}")
    lines.append("")

    # XRef summary
    entries = doc._xref.entries
    in_use = sum(1 for e in entries.values() if e.etype == XrefEntryType.InUse)
    free = sum(1 for e in entries.values() if e.etype == XrefEntryType.Free)
    compressed = sum(1 for e in entries.values() if e.etype == XrefEntryType.Compressed)
    lines.append("--- xref table ---")
    lines.append(f"total entries : {len(entries)}")
    lines.append(f"in-use        : {in_use}")
    lines.append(f"free          : {free}")
    lines.append(f"compressed    : {compressed}")
    lines.append("")

    # Trailer dictionary
    lines.append("--- trailer ---")
    lines.append(_detail(doc._trailer))
    lines.append("")

    # Object-by-object summary
    lines.append("--- objects ---")
    for obj_num in sorted(entries):
        xe = entries[obj_num]
        obj = doc.resolve_num(obj_num, xe.gen)
        if obj is None:
            lines.append(f"obj {obj_num} {xe.gen} R  [could not resolve]")
            continue
        type_name = obj.type.name
        extra = ""
        if obj.is_dict() or obj.type == PdfObjType.Stream:
            subtype = obj.get("Subtype")
            type_key = obj.get("Type")
            parts = []
            if type_key.is_name():
                parts.append(f"Type=/{type_key.sval}")
            if subtype.is_name():
                parts.append(f"Subtype=/{subtype.sval}")
            if parts:
                extra = "  " + ", ".join(parts)
        if obj.type == PdfObjType.Stream:
            extra += f"  raw={len(obj.stream_raw)}B"
        lines.append(f"obj {obj_num} {xe.gen} R  {type_name}{extra}")
    lines.append("")

    (upload_dir / "analysis.log").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a PDF file and return the parsed object tree as JSON."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    data = await file.read()
    if len(data) < 8:
        raise HTTPException(400, "File too small to be a PDF")

    try:
        doc = PdfDocument.from_bytes(data, filename=file.filename)
    except Exception as exc:
        raise HTTPException(400, f"Failed to parse PDF: {exc}") from exc

    upload_id = _make_id()
    _evict_if_needed()
    _sessions[upload_id] = doc
    _backref_cache[upload_id] = _build_backref_index(doc)

    try:
        _save_upload(upload_id, file.filename, data, doc)
    except Exception as exc:
        logging.warning("Could not persist upload %s: %s", upload_id, exc)

    root = doc.root()
    return {
        "id": upload_id,
        "version": doc.version(),
        "filename": file.filename,
        "tree": root.to_dict() if root else None,
    }


@app.post("/api/store")
async def store_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a PDF and save it permanently to the store folder."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    data = await file.read()
    if len(data) < 8:
        raise HTTPException(400, "File too small to be a PDF")

    # Sanitize filename: keep only safe characters
    safe_name = Path(file.filename).name
    dest = _STORE_DIR / safe_name

    # If a file with that name already exists, add a numeric suffix
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = _STORE_DIR / f"{stem}_{counter}{suffix}"
            counter += 1

    dest.write_bytes(data)
    return {"filename": dest.name, "size": len(data)}


@app.get("/api/store")
def list_store() -> dict[str, Any]:
    """Return a list of PDF files in the store folder."""
    files = sorted(
        [
            {"filename": f.name, "size": f.stat().st_size}
            for f in _STORE_DIR.iterdir()
            if f.is_file() and f.suffix.lower() == ".pdf"
        ],
        key=lambda x: x["filename"].lower(),
    )
    return {"files": files}


@app.post("/api/open_from_store/{filename}")
def open_from_store(filename: str) -> dict[str, Any]:
    """Parse a PDF from the store folder and return its tree (like /api/upload)."""
    # Prevent path traversal
    safe_name = Path(filename).name
    pdf_path = _STORE_DIR / safe_name
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(404, f"File '{safe_name}' not found in store")

    data = pdf_path.read_bytes()
    try:
        doc = PdfDocument.from_bytes(data, filename=safe_name)
    except Exception as exc:
        raise HTTPException(400, f"Failed to parse PDF: {exc}") from exc

    upload_id = _make_id()
    _evict_if_needed()
    _sessions[upload_id] = doc
    _backref_cache[upload_id] = _build_backref_index(doc)

    try:
        _save_upload(upload_id, safe_name, data, doc)
    except Exception as exc:
        logging.warning("Could not persist upload %s: %s", upload_id, exc)

    root = doc.root()
    return {
        "id": upload_id,
        "version": doc.version(),
        "filename": safe_name,
        "tree": root.to_dict() if root else None,
    }


@app.get("/api/object/{upload_id}/{num}/{gen}")
def get_object(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return the detailed text for a specific PDF object (lazy load)."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found — please re-upload the PDF")

    detail = doc.get_object_detail(num, gen)
    obj = doc.resolve_num(num, gen)
    is_image = False
    is_icc_profile = False
    is_content_stream = False
    is_palette = False
    is_tounicode = False
    is_font_descriptor = False
    is_ttf = False
    image_filter: str | None = None
    if obj and (obj.is_dict() or obj.type == PdfObjType.Stream):
        st = obj.get("Subtype")
        is_image = st.is_name() and st.sval == "Image"
        # FontDescriptor dict
        type_obj2 = obj.get("Type")
        if type_obj2.is_name() and type_obj2.sval == "FontDescriptor":
            is_font_descriptor = True
        if obj.type == PdfObjType.Stream:
            from pdf.filters import flat_decode
            fobj = obj.get("Filter")
            if fobj.is_name():
                image_filter = fobj.sval if is_image else None
            if fobj.is_name() and fobj.sval == "FlateDecode":
                decoded = flat_decode(obj.stream_raw)
                if decoded and not is_image:
                    type_obj = obj.get("Type")
                    not_special = not (type_obj.is_name() and type_obj.sval in ('ObjStm', 'XRef'))
                    if not_special and not _is_binary(decoded) and _is_content_stream_data(decoded):
                        is_content_stream = True

    # Signal 3 — Reference chain detections
    backref_index = _backref_cache.get(upload_id, {})
    for ref in backref_index.get(num, []):
        kp, tn = ref["key_path"], ref["type_name"]
        # TrueType/OTF font file: FontDescriptor.FontFile2 → stream
        if kp == "FontFile2" and tn == "FontDescriptor":
            is_ttf = True
        # ToUnicode CMap: Font.ToUnicode → stream
        if kp == "ToUnicode" and tn == "Font":
            is_tounicode = True
        # ICC profile: [/ICCBased stream_ref] array at index [1]
        if kp == "[1]":
            parent = doc.resolve_num(ref["from_num"], ref["from_gen"])
            if (parent and parent.is_array() and len(parent.arr) >= 2
                    and parent.arr[0].is_name() and parent.arr[0].sval == "ICCBased"):
                is_icc_profile = True
        # Indexed palette lookup stream: [/Indexed base hival stream_ref] array at [3]
        if kp == "[3]":
            parent = doc.resolve_num(ref["from_num"], ref["from_gen"])
            if (parent and parent.is_array() and len(parent.arr) >= 4
                    and parent.arr[0].is_name() and parent.arr[0].sval == "Indexed"):
                is_palette = True

    return {
        "detail": detail,
        "is_image": is_image,
        "is_icc_profile": is_icc_profile,
        "is_content_stream": is_content_stream,
        "is_palette": is_palette,
        "is_tounicode": is_tounicode,
        "is_font_descriptor": is_font_descriptor,
        "is_ttf": is_ttf,
        "image_filter": image_filter,
        "obj_num": num,
        "gen_num": gen,
    }


@app.get("/api/backrefs/{upload_id}/{num}")
def get_backrefs(upload_id: str, num: int) -> dict[str, Any]:
    """Return all objects that contain a reference to the given object number."""
    if upload_id not in _sessions:
        raise HTTPException(404, "Session not found — please re-upload the PDF")
    index = _backref_cache.get(upload_id, {})
    refs = index.get(num, [])
    return {"obj_num": num, "refs": refs}


@app.get("/api/icc/{upload_id}/{num}/{gen}")
def get_icc_profile(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return parsed ICC profile data for a FlateDecode stream object."""
    from pdf.filters import flat_decode

    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    fobj = obj.get("Filter")
    if fobj.is_name() and fobj.sval == "FlateDecode":
        decoded = flat_decode(obj.stream_raw)
    else:
        decoded = obj.stream_raw

    if decoded is None:
        raise HTTPException(422, "Could not decode stream")

    icc = parse_icc_profile(decoded)
    if icc is None:
        raise HTTPException(422, "Not a valid ICC profile")

    return icc


@app.get("/api/content_stream/{upload_id}/{num}/{gen}")
def get_content_stream(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return parsed operator data for a PDF content stream."""
    from pdf.filters import flat_decode

    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    fobj = obj.get("Filter")
    if fobj.is_name() and fobj.sval == "FlateDecode":
        decoded = flat_decode(obj.stream_raw)
    elif fobj.is_null():
        decoded = obj.stream_raw
    else:
        raise HTTPException(422, "Unsupported filter for content stream")

    if decoded is None:
        raise HTTPException(422, "Failed to decode stream")

    result = parse_content_stream(decoded)
    if result is None:
        raise HTTPException(422, "Not a valid content stream")

    return result


@app.get("/api/palette/{upload_id}/{num}/{gen}")
def get_palette(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return palette entries for an Indexed color space array object."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None:
        raise HTTPException(404, "Object not found")

    # If called with the lookup stream directly (e.g. obj 341), find the parent
    # [/Indexed base hival lookup_ref] array via the backref index.
    if not obj.is_array():
        index = _backref_cache.get(upload_id, {})
        parent_array = None
        for ref in index.get(num, []):
            if ref["key_path"] == "[3]":
                candidate = doc.resolve_num(ref["from_num"], ref["from_gen"])
                if (candidate and candidate.is_array() and len(candidate.arr) >= 4
                        and candidate.arr[0].is_name() and candidate.arr[0].sval == "Indexed"):
                    parent_array = candidate
                    break
        if parent_array is None:
            raise HTTPException(422, "Not an Indexed color space array")
        # Use the raw bytes of this stream as the lookup table directly
        hival = int(parent_array.arr[2].ival) if parent_array.arr[2].is_int() else None
        raw = obj.stream_raw
    else:
        # Expect [/Indexed base_cs hival lookup]
        if len(obj.arr) < 4 or not obj.arr[0].is_name() or obj.arr[0].sval != "Indexed":
            raise HTTPException(422, "Not an Indexed color space array")

        hival = int(obj.arr[2].ival) if obj.arr[2].is_int() else None
        lookup_ref = obj.arr[3]

        # Resolve the lookup table (stream reference or inline string)
        if lookup_ref.is_ref():
            lookup = doc.resolve_num(lookup_ref.ref.num, lookup_ref.ref.gen)
            if lookup is None:
                raise HTTPException(404, "Palette lookup stream not found")
            raw = lookup.stream_raw
        elif lookup_ref.is_str():
            raw = lookup_ref.sval.encode("latin-1")
        else:
            raise HTTPException(422, "Unsupported palette lookup format")

    # Trim to exact entry count using hival if available
    if hival is not None:
        raw = raw[: (hival + 1) * 3]
    else:
        raw = raw.rstrip(b'\x00\x09\x0a\x0c\x0d\x20')

    if len(raw) == 0 or len(raw) % 3 != 0:
        raise HTTPException(422, "Not a valid RGB palette")

    entries = []
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        entries.append({
            "index": i // 3,
            "r": r, "g": g, "b": b,
            "hex": f"#{r:02x}{g:02x}{b:02x}",
            "dark_bg": lum < 128,
        })

    return {
        "entry_count": len(entries),
        "channels": 3,
        "raw_size": len(raw),
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# ToUnicode CMap endpoint
# ---------------------------------------------------------------------------

@app.get("/api/tounicode/{upload_id}/{num}/{gen}")
def get_tounicode(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Parse a ToUnicode CMap stream and return the CID→Unicode mapping table."""
    import re as _re
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    from pdf.filters import flat_decode
    decoded = flat_decode(obj.stream_raw)
    if decoded is None:
        raise HTTPException(422, "Cannot decompress stream")
    text = decoded.decode("latin-1", errors="replace")

    mappings: list[dict] = []

    # begincoderangechar / bfchar sections: <src_hex> <dst_hex>
    for block in _re.findall(r'beginbfchar(.*?)endbfchar', text, _re.DOTALL):
        for m in _re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            src = int(m.group(1), 16)
            dst_bytes = bytes.fromhex(m.group(2))
            try:
                char = dst_bytes.decode("utf-16-be")
            except Exception:
                char = "?"
            code_point = int(m.group(2), 16) if len(dst_bytes) == 2 else ord(char[0]) if char else 0
            mappings.append({
                "src_hex": m.group(1).upper(),
                "src_int": src,
                "dst_hex": m.group(2).upper(),
                "code_point": code_point,
                "char": char,
            })

    # begincoderangechar / bfrange sections: <lo> <hi> <start_dst>
    for block in _re.findall(r'beginbfrange(.*?)endbfrange', text, _re.DOTALL):
        for m in _re.finditer(
            r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',
            block,
        ):
            lo = int(m.group(1), 16)
            hi = int(m.group(2), 16)
            base_bytes = bytes.fromhex(m.group(3))
            base_cp = int.from_bytes(base_bytes, "big")
            for i, src in enumerate(range(lo, hi + 1)):
                cp = base_cp + i
                try:
                    char = chr(cp)
                except Exception:
                    char = "?"
                mappings.append({
                    "src_hex": format(src, "04X"),
                    "src_int": src,
                    "dst_hex": format(cp, "04X"),
                    "code_point": cp,
                    "char": char,
                })

    mappings.sort(key=lambda x: x["src_int"])

    # Extract CMap name and type
    name_m = _re.search(r'/CMapName\s+(/\S+)', text)
    type_m = _re.search(r'/CMapType\s+(\d+)', text)
    registry_m = _re.search(r'/Registry\s*\(([^)]+)\)', text)
    ordering_m = _re.search(r'/Ordering\s*\(([^)]+)\)', text)

    return {
        "cmap_name": name_m.group(1) if name_m else None,
        "cmap_type": int(type_m.group(1)) if type_m else None,
        "registry": registry_m.group(1) if registry_m else None,
        "ordering": ordering_m.group(1) if ordering_m else None,
        "total_mappings": len(mappings),
        "mappings": mappings[:2000],  # cap to avoid huge payloads
    }


# ---------------------------------------------------------------------------
# FontDescriptor endpoint
# ---------------------------------------------------------------------------

_FONT_FLAGS = [
    (1, "FixedPitch",  "All glyphs have the same width"),
    (2, "Serif",       "Glyphs have serifs"),
    (3, "Symbolic",    "Contains characters not in standard Latin"),
    (4, "Script",      "Script/cursive glyphs"),
    (6, "Nonsymbolic", "Uses standard Latin character set"),
    (7, "Italic",      "Glyphs are italic"),
    (17, "AllCap",     "All glyphs are upper case"),
    (18, "SmallCap",   "Uses small-cap glyphs"),
    (19, "ForceBold",  "Bold glyphs painted with extra pixels at small sizes"),
]

@app.get("/api/fontdescriptor/{upload_id}/{num}/{gen}")
def get_fontdescriptor(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return parsed FontDescriptor metrics for visualization."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or not obj.is_dict():
        raise HTTPException(404, "Object not found or not a dict")

    def _num(key: str) -> float | None:
        v = obj.get(key)
        if v.is_int(): return float(v.ival)
        if v.type == PdfObjType.Real: return v.dval
        return None

    def _name(key: str) -> str | None:
        v = obj.get(key)
        return v.sval if v.is_name() else None

    flags_raw = int(obj.get("Flags").ival) if obj.get("Flags").is_int() else 0
    active_flags = [
        {"bit": bit, "name": name, "desc": desc}
        for bit, name, desc in _FONT_FLAGS
        if (flags_raw >> (bit - 1)) & 1
    ]

    bbox_obj = obj.get("FontBBox")
    bbox = [int(v.ival) if v.is_int() else float(v.dval) if v.type == PdfObjType.Real else 0
            for v in bbox_obj.arr] if bbox_obj.is_array() else None

    # Resolve FontFile2 ref for linking
    ff2 = obj.get("FontFile2")
    ff2_num = ff2.ref.num if ff2.type == PdfObjType.Reference else None
    cidset = obj.get("CIDSet")
    cidset_num = cidset.ref.num if cidset.type == PdfObjType.Reference else None

    return {
        "font_name": _name("FontName"),
        "flags_raw": flags_raw,
        "flags": active_flags,
        "ascent": _num("Ascent"),
        "descent": _num("Descent"),
        "cap_height": _num("CapHeight"),
        "x_height": _num("XHeight"),
        "italic_angle": _num("ItalicAngle"),
        "stem_v": _num("StemV"),
        "stem_h": _num("StemH"),
        "font_weight": _num("FontWeight"),
        "bbox": bbox,
        "font_file2_num": ff2_num,
        "cidset_num": cidset_num,
        "missing_width": _num("MissingWidth"),
    }


# ---------------------------------------------------------------------------
# TrueType table directory endpoint
# ---------------------------------------------------------------------------

@app.get("/api/ttf_tables/{upload_id}/{num}/{gen}")
def get_ttf_tables(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Parse a TrueType/OTF font stream and return the table directory."""
    import struct as _struct
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    from pdf.filters import flat_decode
    data = _decode_stream(obj)
    if data is None or len(data) < 12:
        raise HTTPException(422, "Cannot decode font stream or too short")

    sfver = data[:4]
    if sfver not in (b'\x00\x01\x00\x00', b'true', b'OTTO', b'ttcf'):
        raise HTTPException(422, "Not a recognised TrueType/OTF font")

    num_tables = _struct.unpack_from(">H", data, 4)[0]

    _TABLE_DESCS: dict[str, str] = {
        "cmap": "Character code to glyph index mapping",
        "glyf": "Glyph outline data (TrueType)",
        "head": "Font header — version, units per em, bounding box",
        "hhea": "Horizontal header — ascender, descender, line gap",
        "hmtx": "Horizontal metrics — advance width and left side bearing per glyph",
        "loca": "Index to location — offsets into 'glyf' table",
        "maxp": "Maximum profile — number of glyphs, stack depths",
        "name": "Naming table — font name, copyright, version strings",
        "post": "PostScript name and glyph name index",
        "OS/2": "OS/2 and Windows metrics — weight class, panose, unicode ranges",
        "cvt ": "Control value table for hinting",
        "fpgm": "Font program — hinting bytecode run at font load",
        "prep": "Control value program — hinting bytecode run per size",
        "gasp": "Grid-fitting and scan-conversion procedure table",
        "kern": "Kerning pairs",
        "CFF ": "Compact Font Format outlines (OTF/CFF)",
        "GDEF": "Glyph definition — base, ligature, mark, component classes",
        "GPOS": "Glyph positioning — kerning, mark attachment",
        "GSUB": "Glyph substitution — ligatures, alternates",
        "VDMX": "Vertical device metrics",
        "DSIG": "Digital signature",
    }

    tables = []
    total_size = len(data)
    for i in range(min(num_tables, 128)):
        off = 12 + i * 16
        if off + 16 > len(data):
            break
        tag_b, checksum, tbl_off, tbl_len = _struct.unpack_from(">4sIII", data, off)
        tag = tag_b.decode("latin-1")
        tables.append({
            "tag": tag,
            "checksum": f"0x{checksum:08X}",
            "offset": tbl_off,
            "length": tbl_len,
            "desc": _TABLE_DESCS.get(tag.rstrip(), _TABLE_DESCS.get(tag, "—")),
        })

    tables.sort(key=lambda t: t["offset"])

    sfver_str = {
        b'\x00\x01\x00\x00': "TrueType 1.0",
        b'true': "TrueType (Apple)",
        b'OTTO': "OpenType/CFF",
        b'ttcf': "TrueType Collection",
    }.get(sfver, sfver.decode("latin-1"))

    return {
        "sfVersion": sfver_str,
        "num_tables": num_tables,
        "total_size": total_size,
        "tables": tables,
    }


@app.get("/api/image_detail/{upload_id}/{num}/{gen}")
def get_image_detail(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return structured metadata and JPEG segment info for an XObject image."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    # PDF dictionary fields
    w_obj = obj.get("Width")
    h_obj = obj.get("Height")
    bpc_obj = obj.get("BitsPerComponent")
    cs_obj = obj.get("ColorSpace")
    fobj = obj.get("Filter")

    width  = w_obj.ival  if w_obj.is_int()  else None
    height = h_obj.ival  if h_obj.is_int()  else None
    bpc    = bpc_obj.ival if bpc_obj.is_int() else None

    # Colour space as brief string
    from pdf.document import _brief
    cs_str = _brief(cs_obj) if not cs_obj.is_null() else None

    # Filter name (handle both /Name and [/Name] forms)
    filter_name: str | None = None
    if fobj.is_name():
        filter_name = fobj.sval
    elif fobj.is_array() and fobj.arr and fobj.arr[0].is_name():
        filter_name = fobj.arr[0].sval

    raw_size = len(obj.stream_raw)

    # Decoded size: compute from JPEG frame info (avoids full pixel decode)
    decoded_size: int | None = None
    jpeg_data: dict | None = None

    if filter_name == "DCTDecode" and len(obj.stream_raw) >= 4 and obj.stream_raw[:2] == b'\xFF\xD8':
        jpeg_info = parse_jpeg(obj.stream_raw)
        if jpeg_info:
            fi = jpeg_info.get("frame_info")
            if fi:
                decoded_size = fi["width"] * fi["height"] * fi["components"]
            jpeg_data = {
                "segments": jpeg_info["segments"],
                "structure": jpeg_info["structure"],
                "frame_info": fi,
            }

    ccitt_data: dict | None = None

    if filter_name == "CCITTFaxDecode":
        dp = obj.get("DecodeParms")
        k = -1
        columns = width or 1728
        rows_param: int | None = None
        end_of_block = True
        end_of_line = False
        encoded_byte_align = False
        black_is_1 = False
        damaged_rows = 0
        if dp.is_dict():
            k_obj = dp.get("K")
            if k_obj.is_int():
                k = int(k_obj.ival)
            col_obj = dp.get("Columns")
            if col_obj.is_int():
                columns = int(col_obj.ival)
            rows_obj = dp.get("Rows")
            if rows_obj.is_int():
                rows_param = int(rows_obj.ival)
            eob_obj = dp.get("EndOfBlock")
            if eob_obj.type == PdfObjType.Boolean:
                end_of_block = eob_obj.bval
            eol_obj = dp.get("EndOfLine")
            if eol_obj.type == PdfObjType.Boolean:
                end_of_line = eol_obj.bval
            eba_obj = dp.get("EncodedByteAlign")
            if eba_obj.type == PdfObjType.Boolean:
                encoded_byte_align = eba_obj.bval
            bi1_obj = dp.get("BlackIs1")
            if bi1_obj.type == PdfObjType.Boolean:
                black_is_1 = bi1_obj.bval
            drbe_obj = dp.get("DamagedRowsBeforeError")
            if drbe_obj.is_int():
                damaged_rows = int(drbe_obj.ival)
        if height is not None:
            import math
            decoded_size = math.ceil(columns * height / 8)
        ccitt_data = parse_ccitt(
            obj.stream_raw,
            k=k, columns=columns, rows=rows_param,
            end_of_block=end_of_block, end_of_line=end_of_line,
            encoded_byte_align=encoded_byte_align, black_is_1=black_is_1,
            damaged_rows_before_error=damaged_rows,
        )

    flat_data: dict | None = None
    if filter_name == "FlateDecode":
        try:
            _flat_decoded = zlib.decompress(obj.stream_raw)
            if decoded_size is None:
                decoded_size = len(_flat_decoded)
        except Exception:
            pass
        dp = obj.get("DecodeParms")
        if dp.type == PdfObjType.Reference:
            dp_resolved = doc.resolve_num(dp.ref.num, dp.ref.gen)
            if dp_resolved is not None:
                dp = dp_resolved
        predictor = 1
        flat_cols = width
        flat_colors = 1
        flat_bpc = bpc or 8
        if dp.is_dict():
            pred_obj = dp.get("Predictor")
            if pred_obj.is_int():
                predictor = int(pred_obj.ival)
            cols_obj = dp.get("Columns")
            if cols_obj.is_int():
                flat_cols = int(cols_obj.ival)
            colors_obj = dp.get("Colors")
            if colors_obj.is_int():
                flat_colors = int(colors_obj.ival)
            bpc_obj2 = dp.get("BitsPerComponent")
            if bpc_obj2.is_int():
                flat_bpc = int(bpc_obj2.ival)
        flat_data = parse_flat_image(
            obj.stream_raw,
            predictor=predictor,
            columns=flat_cols,
            colors=flat_colors,
            bpc=flat_bpc,
        )

    return {
        "width": width,
        "height": height,
        "bits_per_component": bpc,
        "color_space": cs_str,
        "filter": filter_name,
        "raw_size": raw_size,
        "decoded_size": decoded_size,
        "jpeg": jpeg_data,
        "ccitt": ccitt_data,
        "flat": flat_data,
    }


@app.get("/api/image/{upload_id}/{num}/{gen}")
def get_image(upload_id: str, num: int, gen: int) -> Response:
    """Return image data for an XObject image node."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    # Check filter to decide format
    filter_obj = obj.get("Filter")
    filter_name = ""
    if filter_obj.is_name():
        filter_name = filter_obj.sval
    elif filter_obj.is_array() and filter_obj.arr and filter_obj.arr[0].is_name():
        filter_name = filter_obj.arr[0].sval

    # DCTDecode → JPEG passthrough
    if filter_name == "DCTDecode":
        return Response(content=obj.stream_raw, media_type="image/jpeg")

    # CCITTFaxDecode → decode via PIL TIFF wrapper (Group 3 / Group 4)
    if filter_name == "CCITTFaxDecode":
        png_data = _ccitt_fax_to_png(obj)
        if png_data is None:
            raise HTTPException(422, "Cannot decode CCITTFax image stream")
        return Response(content=png_data, media_type="image/png")

    # Try to decode and serve as PNG
    decoded = obj.stream_decoded if obj.stream_decoded else _decode_stream(obj)
    if decoded is not None and not obj.stream_decoded:
        obj.stream_decoded = decoded  # cache so second request is free
    if decoded is None:
        raise HTTPException(422, "Cannot decode image stream")

    w_obj = obj.get("Width")
    h_obj = obj.get("Height")
    bpc_obj = obj.get("BitsPerComponent")
    cs_obj = obj.get("ColorSpace")

    if not w_obj.is_int() or not h_obj.is_int():
        raise HTTPException(422, "Image has no Width/Height")

    width = int(w_obj.ival)
    height = int(h_obj.ival)
    bpc = int(bpc_obj.ival) if bpc_obj.is_int() else 8

    # Apply FlateDecode predictor un-filtering if needed
    if filter_name == "FlateDecode":
        dp = obj.get("DecodeParms")
        # DecodeParms may be an indirect reference; resolve it
        if dp.type == PdfObjType.Reference:
            dp_resolved = doc.resolve_num(dp.ref.num, dp.ref.gen)
            if dp_resolved is not None:
                dp = dp_resolved
        if dp.is_dict():
            pred_obj = dp.get("Predictor")
            predictor = int(pred_obj.ival) if pred_obj.is_int() else 1
            cols_obj = dp.get("Columns")
            pred_cols = int(cols_obj.ival) if cols_obj.is_int() else width
            colors_obj = dp.get("Colors")
            pred_colors = int(colors_obj.ival) if colors_obj.is_int() else 1
            if predictor >= 10:
                # PNG predictors — strip filter bytes and undo row filters
                decoded = _apply_png_predictor(decoded, pred_cols, pred_colors, bpc)
            elif predictor == 2:
                # TIFF horizontal differencing
                decoded = _apply_tiff_predictor(decoded, pred_cols, pred_colors, bpc)

    # Fully resolve ColorSpace (may be indirect reference)
    cs_resolved = _resolve_cs(doc, cs_obj)

    # Detect Indexed color space: [/Indexed base_cs hival lookup]
    if (cs_resolved.is_array() and cs_resolved.arr
            and cs_resolved.arr[0].is_name()
            and cs_resolved.arr[0].sval == "Indexed"):
        rgb_pixels, channels = _apply_indexed_palette(doc, cs_resolved.arr, decoded)
        if rgb_pixels is not None:
            decoded = rgb_pixels
            bpc = 8
        else:
            channels = 1  # fall back to grayscale indices
    else:
        cs = ""
        if cs_resolved.is_name():
            cs = cs_resolved.sval
        elif cs_resolved.is_array() and cs_resolved.arr and cs_resolved.arr[0].is_name():
            cs = cs_resolved.arr[0].sval
        channels = 3 if cs in ("DeviceRGB", "RGB", "CalRGB") else 1

    # Expand packed 1-bit data to 8-bit so _raw_to_png can handle it
    if bpc == 1:
        decoded = _expand_1bit(decoded, width, height, channels)
        bpc = 8

    try:
        png_data = _raw_to_png(decoded, width, height, channels, bpc)
    except Exception as exc:
        raise HTTPException(422, f"Cannot convert to PNG: {exc}") from exc

    return Response(content=png_data, media_type="image/png")


# ---------------------------------------------------------------------------
# Color space helpers
# ---------------------------------------------------------------------------

def _resolve_cs(doc: Any, cs_obj: PdfObject) -> PdfObject:
    """Follow indirect references for ColorSpace and return the resolved object."""
    if cs_obj.type == PdfObjType.Reference:
        resolved = doc.resolve_num(cs_obj.ref.num, cs_obj.ref.gen)
        return resolved if resolved is not None else cs_obj
    return cs_obj


def _apply_indexed_palette(
    doc: Any, cs_arr: list, raw_pixels: bytes
) -> tuple[bytes, int] | tuple[None, None]:
    """Map Indexed color space pixel indices through the palette to RGB/gray bytes.

    cs_arr is the array [/Indexed, base_cs, hival, lookup].
    Returns (mapped_bytes, channels) or (None, None) on failure.
    """
    if len(cs_arr) < 4:
        return None, None

    base_cs_obj = _resolve_cs(doc, cs_arr[1])
    lookup_obj = cs_arr[3]

    # Determine number of channels from the base color space
    cs_name = ""
    if base_cs_obj.is_name():
        cs_name = base_cs_obj.sval
    elif base_cs_obj.is_array() and base_cs_obj.arr and base_cs_obj.arr[0].is_name():
        cs_name = base_cs_obj.arr[0].sval
        # ICCBased: check N in stream dict
        if cs_name == "ICCBased" and len(base_cs_obj.arr) >= 2:
            icc_ref = _resolve_cs(doc, base_cs_obj.arr[1])
            if icc_ref.type == PdfObjType.Stream:
                n_obj = icc_ref.dict.get("N")
                channels = int(n_obj.ival) if n_obj and n_obj.is_int() else 3
            else:
                channels = 3
        else:
            channels = 3
    else:
        channels = 1

    if cs_name in ("DeviceRGB", "CalRGB"):
        channels = 3
    elif cs_name == "DeviceCMYK":
        channels = 4
    elif cs_name in ("DeviceGray", "CalGray"):
        channels = 1
    # (ICCBased already handled above, default is 3)

    # Resolve lookup table
    lookup_obj = _resolve_cs(doc, lookup_obj)
    if lookup_obj.type == PdfObjType.Stream:
        palette = _decode_stream(lookup_obj) or lookup_obj.stream_raw
    elif lookup_obj.is_str():
        palette = lookup_obj.sval.encode("latin-1")
    else:
        return None, None

    if not palette:
        return None, None

    # Map each index byte through the palette
    result = bytearray(len(raw_pixels) * channels)
    for i, idx in enumerate(raw_pixels):
        src = idx * channels
        dst = i * channels
        if src + channels <= len(palette):
            result[dst:dst + channels] = palette[src:src + channels]

    return bytes(result), channels


# ---------------------------------------------------------------------------
# CCITTFax decoder (Group 3 / Group 4) via PIL TIFF wrapper
# ---------------------------------------------------------------------------

def _ccitt_fax_to_png(obj: Any) -> bytes | None:
    """Decode a CCITTFaxDecode image stream and return PNG bytes.

    Builds a minimal TIFF container around the raw fax data so PIL/libtiff
    can decompress it, then encodes the result as a grayscale PNG.
    """
    w_obj = obj.get("Width")
    h_obj = obj.get("Height")
    if not w_obj.is_int() or not h_obj.is_int():
        return None

    width = int(w_obj.ival)
    height = int(h_obj.ival)

    # K=-1 → CCITT Group 4 (T.6); K=0 → Group 3 1D; K>0 → Group 3 2D
    k = -1
    dp = obj.get("DecodeParms")
    if dp.is_dict():
        k_obj = dp.get("K")
        if k_obj.is_int():
            k = int(k_obj.ival)

    # TIFF compression: 4 = Group 4, 3 = Group 3
    tiff_compression = 4 if k < 0 else 3

    SHORT, LONG = 3, 4
    num_tags = 10
    ifd_offset = 8
    data_offset = ifd_offset + 2 + num_tags * 12 + 4

    header = struct.pack("<2sHI", b"II", 42, ifd_offset)
    tags = [
        (256, SHORT, 1, width),
        (257, SHORT, 1, height),
        (258, SHORT, 1, 1),                   # BitsPerSample = 1
        (259, SHORT, 1, tiff_compression),    # Compression
        (262, SHORT, 1, 0),                   # PhotometricInterpretation: WhiteIsZero
        (273, LONG,  1, data_offset),         # StripOffsets
        (278, LONG,  1, height),              # RowsPerStrip
        (279, LONG,  1, len(obj.stream_raw)), # StripByteCounts
        (280, SHORT, 1, 0),
        (281, SHORT, 1, 1),
    ]
    ifd = struct.pack("<H", num_tags)
    for tid, tt, cnt, val in tags:
        ifd += struct.pack("<HHII", tid, tt, cnt, val)
    ifd += struct.pack("<I", 0)

    tiff_bytes = header + ifd + obj.stream_raw

    try:
        img = Image.open(io.BytesIO(tiff_bytes))
        # CCITT G4 data in PDF is encoded bottom-row-first (PDF image origin is
        # lower-left).  PDF viewers compensate with a negative-d CTM; we must
        # apply the same vertical flip here so the image appears right-side up.
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        raw_pixels = img.convert("L").tobytes()
    except Exception:
        return None

    return _raw_to_png(raw_pixels, width, height, 1, 8)


def _expand_1bit(data: bytes, width: int, height: int, channels: int = 1) -> bytes:
    """Expand packed 1-bit image data to 8-bit per channel (0 or 255)."""
    stride = (width * channels + 7) // 8
    result = bytearray()
    for row in range(height):
        row_data = data[row * stride: (row + 1) * stride]
        bits_written = 0
        for byte in row_data:
            for bit_idx in range(7, -1, -1):
                if bits_written >= width * channels:
                    break
                result.append(0 if (byte >> bit_idx) & 1 else 255)
                bits_written += 1
    return bytes(result)


def _apply_png_predictor(data: bytes, width: int, channels: int, bpc: int = 8) -> bytes:
    """Reverse the PNG row filter bytes from FlateDecode+PNGPredictor streams.

    Each decoded row starts with a filter-type byte (0-4) followed by
    ``width * channels * (bpc // 8)`` bytes.  Returns the raw pixel bytes with
    filter bytes stripped and prediction undone.
    """
    bytes_per_sample = max(1, bpc // 8)
    bpp = channels * bytes_per_sample  # bytes per pixel
    stride = width * channels * bytes_per_sample
    row_len = stride + 1  # +1 for the filter byte
    num_rows = len(data) // row_len
    out = bytearray()
    prev_row = bytearray(stride)
    for r in range(num_rows):
        row_start = r * row_len
        ftype = data[row_start]
        raw = bytearray(data[row_start + 1: row_start + 1 + stride])
        if ftype == 0:  # None
            pass
        elif ftype == 1:  # Sub
            for i in range(bpp, stride):
                raw[i] = (raw[i] + raw[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                raw[i] = (raw[i] + prev_row[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = raw[i - bpp] if i >= bpp else 0
                raw[i] = (raw[i] + (left + prev_row[i]) // 2) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = raw[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                up_left = prev_row[i - bpp] if i >= bpp else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                if pa <= pb and pa <= pc:
                    pr = left
                elif pb <= pc:
                    pr = up
                else:
                    pr = up_left
                raw[i] = (raw[i] + pr) & 0xFF
        out.extend(raw)
        prev_row = raw
    return bytes(out)


def _apply_tiff_predictor(data: bytes, width: int, channels: int, bpc: int = 8) -> bytes:
    """Undo TIFF predictor 2 (horizontal differencing)."""
    bytes_per_sample = max(1, bpc // 8)
    bpp = channels * bytes_per_sample
    stride = width * bpp
    out = bytearray(data)
    for r in range(len(data) // stride):
        base = r * stride
        for i in range(base + bpp, base + stride):
            out[i] = (out[i] + out[i - bpp]) & 0xFF
    return bytes(out)


# ---------------------------------------------------------------------------
# Minimal PNG encoder (no Pillow required)
# ---------------------------------------------------------------------------

def _raw_to_png(
    raw: bytes, width: int, height: int, channels: int, bpc: int
) -> bytes:
    """Encode raw pixel data as a PNG byte string."""
    # PNG supports bit depths 1, 2, 4, 8, 16; clamp to 8 or 16
    bit_depth = 16 if bpc >= 16 else 8
    bytes_per_sample = bit_depth // 8
    color_type = 2 if channels == 3 else 0  # 2=RGB, 0=grayscale

    # Build IDAT raw data (add filter byte 0x00 per scanline)
    bytes_per_row = width * channels * bytes_per_sample
    idat_raw = bytearray()
    for row in range(height):
        idat_raw.append(0)  # filter type None
        start = row * bytes_per_row
        idat_raw.extend(raw[start:start + bytes_per_row])

    idat_compressed = zlib.compress(bytes(idat_raw), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        payload = tag + data
        crc = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        return length + payload + crc

    ihdr_data = struct.pack(
        ">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0
    )
    png_sig = b"\x89PNG\r\n\x1a\n"
    return (
        png_sig
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_compressed)
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Serve the built React frontend from /  (must be mounted last)
# ---------------------------------------------------------------------------
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
