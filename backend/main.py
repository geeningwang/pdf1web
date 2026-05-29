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
from fastapi.responses import HTMLResponse, Response
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

    # --- 1. All indirect objects ---
    trailer = doc._trailer
    for num, entry in doc._xref.entries.items():
        if entry.etype == XrefEntryType.Free:
            continue
        obj = doc.resolve_num(num, entry.gen)
        if obj is None:
            continue
        type_v = obj.get("Type") if (obj.is_dict() or obj.type == PdfObjType.Stream) else None
        type_name = type_v.sval if (type_v and type_v.is_name()) else obj.type.name

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
                index.setdefault(cur.ref.num, []).append({
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

    # --- 2. Trailer dictionary references (last, matches tree order) ---
    if trailer.is_dict():
        for key, val in trailer.dict.items():
            if val.type == PdfObjType.Reference:
                index.setdefault(val.ref.num, []).append({
                    "from_num": -1,
                    "from_gen": 0,
                    "key_path": key,
                    "type_name": "Trailer",
                })

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
# Centralized object classifier — single source of truth for type labels.
# Used by the tree view, XRef table, and per-object detail endpoint so all
# surfaces always show the same label.
# ---------------------------------------------------------------------------

_IMG_FILTER_NICK: dict[str, str] = {
    "DCTDecode": "JPEG", "JPXDecode": "JPEG 2000",
    "FlateDecode": "Flate", "CCITTFaxDecode": "CCITT", "JBIG2Decode": "JBIG2",
}
_PDF_TYPE_MAP: dict[str, str] = {
    "Page": "Page", "Pages": "Pages Tree", "Catalog": "Catalog",
    "Font": "Font", "FontDescriptor": "Font Descriptor",
    "XObject": "XObject", "ObjStm": "Object Stream",
    "XRef": "Cross-Reference Stream", "Annot": "Annotation",
    "Action": "Action", "Encoding": "Encoding",
    "Pattern": "Pattern", "Shading": "Shading",
    "ExtGState": "Graphics State", "Metadata": "Metadata Stream",
    "OCG": "Optional Content Group", "OCMD": "Optional Content Membership",
    "Outlines": "Outlines", "Filespec": "File Specification",
    "EmbeddedFile": "Embedded File",
}
_SUBTYPE_Q: set[str] = {"Font", "XObject", "Action", "Annot", "Pattern", "Shading"}
_CS_ARRAY_NAMES: set[str] = {
    "Indexed", "ICCBased", "CalRGB", "CalGray", "Lab",
    "Separation", "DeviceN", "Pattern",
}
_PROCSET_NAMES: set[str] = {"PDF", "Text", "ImageB", "ImageC", "ImageI"}
_SCALAR_TYPES: dict = {
    PdfObjType.Integer: "Integer", PdfObjType.Real: "Real",
    PdfObjType.Name: "Name", PdfObjType.LiteralString: "String",
    PdfObjType.HexString: "String", PdfObjType.Boolean: "Boolean",
    PdfObjType.Reference: "Reference", PdfObjType.Null: "Null",
}


def _classify_object(
    num: int,
    gen: int,
    doc: "PdfDocument",
    backref_index: dict[int, list[dict]],
) -> str:
    """Return a human-readable semantic type label for an indirect PDF object."""
    try:
        obj = doc.resolve_num(num, gen)
        if obj is None:
            return "—"
        refs = backref_index.get(num, [])

        # ── Semantic detection via backref paths ──────────────────────
        for r in refs:
            kp = r.get("key_path", "")
            tn = r.get("type_name", "")
            from_num = r.get("from_num", -1)
            if kp == "Contents":
                return "Content Stream (Array)" if obj.is_array() else "Content Stream"
            if kp.startswith("[") and obj.type == PdfObjType.Stream:
                for pr in backref_index.get(from_num, []):
                    if pr.get("key_path") == "Contents":
                        return "Content Stream"
            if kp == "FontFile2" and tn == "FontDescriptor":
                return "Font File (TrueType)"
            if kp == "ToUnicode" and tn == "Font":
                return "ToUnicode CMap"
            if kp == "CIDToGIDMap" and tn == "Font":
                return "CID-to-GID Map"
            if kp == "CIDSet" and tn == "FontDescriptor":
                return "CID Set"
            if kp == "Info" and tn == "Trailer":
                return "Document Info"
            if kp == "PageLabels" and tn == "Catalog":
                return "Page Labels Number Tree"
            if kp.startswith("Nums.") and obj.is_dict():
                parent_refs = backref_index.get(from_num, [])
                if any(pr.get("key_path") == "PageLabels" for pr in parent_refs):
                    return "Page Label"
            if kp == "Length":
                parent = doc.resolve_num(from_num, 0)
                if parent and parent.type == PdfObjType.Stream:
                    return "Stream Length"
            if kp == "DecodeParms":
                return "Decode Parameters"
            if kp == "Resources":
                return "Resource Dictionary"
            # Resource sub-dictionaries (Font, XObject, ColorSpace, etc.) are
            # indirect objects whose parent is a Resource Dictionary (no /Type
            # key → type_name "Dictionary"). Confirm by checking that the parent
            # itself has a "Resources" backref.
            _RES_SUBDICT: dict[str, str] = {
                "Font": "Font Resource Map",
                "XObject": "XObject Resource Map",
                "ColorSpace": "Color Space Map",
                "Pattern": "Pattern Resource Map",
                "Shading": "Shading Resource Map",
                "Properties": "Properties Map",
            }
            if kp in _RES_SUBDICT and tn == "Dictionary":
                parent_refs = backref_index.get(from_num, [])
                if any(pr.get("key_path") == "Resources" for pr in parent_refs):
                    return _RES_SUBDICT[kp]
            if "ExtGState" in kp:
                return "Graphics State"
            if kp == "Thumb":
                return "Page Thumbnail"
            if kp == "[1]":
                parent = doc.resolve_num(from_num, 0)
                if (parent and parent.is_array() and len(parent.arr) >= 2
                        and parent.arr[0].is_name()
                        and parent.arr[0].sval == "ICCBased"):
                    return "ICC Profile"
            if kp == "[3]":
                parent = doc.resolve_num(from_num, 0)
                if (parent and parent.is_array() and len(parent.arr) >= 4
                        and parent.arr[0].is_name()
                        and parent.arr[0].sval == "Indexed"):
                    return "Color Palette"

        # ── Dict / Stream ─────────────────────────────────────────────
        if obj.is_dict() or obj.type == PdfObjType.Stream:
            # Linearization parameter dictionary (first obj in file, no backrefs)
            if obj.is_dict() and obj.get("Linearized").is_int():
                return "Linearization Dictionary"
            st = obj.get("Subtype")
            if st.is_name() and st.sval == "Image":
                fobj = obj.get("Filter")
                nick = _IMG_FILTER_NICK.get(fobj.sval if fobj.is_name() else "", "")
                return f"Image ({nick})" if nick else "Image"
            tk = obj.get("Type")
            sk = obj.get("Subtype")
            if tk.is_name():
                base = _PDF_TYPE_MAP.get(tk.sval, tk.sval)
                sub = sk.sval if sk.is_name() else None
                return f"{base} ({sub})" if sub and tk.sval in _SUBTYPE_Q else base
            if obj.type == PdfObjType.Stream:
                if refs:
                    r0 = refs[0]
                    ptype = r0["type_name"]
                    if ptype == "Array":
                        arr_refs = backref_index.get(r0["from_num"], [])
                        if arr_refs and arr_refs[0].get("key_path") == "Contents":
                            return "Content Stream"
                        up = arr_refs[0]["type_name"] if arr_refs else ""
                        return f"Stream of {up}" if up and up not in ("", "unknown") else "Stream"
                    return f"Stream of {ptype}" if ptype and ptype not in ("", "unknown") else "Stream"
                # No backrefs — check if this is a linearization hint stream
                # (referenced by file offset in /H of a linearization dict, not as an indirect ref)
                xe_self = doc._xref.entries.get(num)
                if xe_self:
                    from pdf.xref import XrefEntryType as _XET
                    for lnum, lxe in doc._xref.entries.items():
                        if lxe.etype not in (_XET.InUse, _XET.Compressed):
                            continue
                        lobj = doc.resolve_num(lnum, lxe.gen)
                        if lobj and lobj.is_dict() and lobj.get("Linearized").is_int():
                            h = lobj.get("H")
                            if h.is_array():
                                for i in range(0, len(h.arr), 2):
                                    if i < len(h.arr) and h.arr[i].is_int() and h.arr[i].ival == xe_self.offset:
                                        return "Linearization Hint Stream"
                return "Stream"
            return "Dictionary"

        # ── Array ─────────────────────────────────────────────────────
        if obj.is_array():
            arr = obj.arr
            if arr and arr[0].is_name() and arr[0].sval in _CS_ARRAY_NAMES:
                return f"Color Space ({arr[0].sval})"
            if arr and all(item.is_name() and item.sval in _PROCSET_NAMES for item in arr):
                return "Procedure Set"
            return "Array"

        return _SCALAR_TYPES.get(obj.type, "Unknown")
    except Exception:
        return "?"


def _annotate_tree_type_labels(root: "PdfNode", doc: PdfDocument,
                                backref_index: dict[int, list[dict]]) -> None:
    """Post-process the tree to add type_label to every object node."""

    def _walk(node: "PdfNode") -> None:
        if node.obj_num >= 0:
            node.type_label = _classify_object(node.obj_num, node.gen_num, doc, backref_index)
        for child in node.children:
            _walk(child)

    _walk(root)


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
    if root is not None:
        _annotate_tree_type_labels(root, doc, _backref_cache[upload_id])
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
    if root is not None:
        _annotate_tree_type_labels(root, doc, _backref_cache[upload_id])
    return {
        "id": upload_id,
        "version": doc.version(),
        "filename": safe_name,
        "tree": root.to_dict() if root else None,
    }


# ---------------------------------------------------------------------------
# PDFX export / link endpoints
# ---------------------------------------------------------------------------

@app.get("/api/export/{upload_id}")
def export_upload(upload_id: str) -> Response:
    """Export a previously uploaded PDF as a PDFX zip archive.

    The upload must have been created by POST /api/upload.  The PDFX directory
    is written to a temporary location, zipped in-memory, and returned as
    application/zip.
    """
    import shutil
    import tempfile
    import zipfile
    from pdf.exporter import export_pdf

    # Locate the persisted PDF under uploads/<upload_id>/
    upload_dir = _UPLOADS_DIR / upload_id
    if not upload_dir.exists():
        raise HTTPException(404, f"Upload '{upload_id}' not found")

    pdf_files = [f for f in upload_dir.iterdir() if f.suffix.lower() == ".pdf"]
    if not pdf_files:
        raise HTTPException(404, f"No PDF found for upload '{upload_id}'")
    pdf_path = pdf_files[0]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            pdfx_dir = export_pdf(str(pdf_path), tmp)
        except Exception as exc:
            raise HTTPException(500, f"Export failed: {exc}") from exc

        # Zip the pdfx directory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(pdfx_dir.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(tmp_path))
        zip_bytes = buf.getvalue()

    stem = pdf_path.stem
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdfx.zip"'},
    )


@app.post("/api/link")
async def link_pdfx(file: UploadFile = File(...)) -> Response:
    """Accept a PDFX zip archive and return the linked PDF.

    The zip must contain a single top-level directory (the .pdfx directory)
    with a valid pdfx_manifest.json.  Returns the reconstructed PDF as
    application/pdf.
    """
    import shutil
    import tempfile
    import zipfile
    from pdf.linker import link_pdf

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Expected a .zip file containing a PDFX directory")

    data = await file.read()
    if len(data) < 4:
        raise HTTPException(400, "File too small")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Extract zip
        try:
            buf = io.BytesIO(data)
            with zipfile.ZipFile(buf, "r") as zf:
                # Security: reject absolute paths and path traversal entries
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise HTTPException(400, f"Unsafe path in zip: {member}")
                zf.extractall(tmp_path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Failed to unzip: {exc}") from exc

        # Find the pdfx directory (first subdir containing pdfx_manifest.json)
        pdfx_dir: Path | None = None
        for candidate in sorted(tmp_path.iterdir()):
            if candidate.is_dir() and (candidate / "pdfx_manifest.json").exists():
                pdfx_dir = candidate
                break
        if pdfx_dir is None:
            # Also check root level
            if (tmp_path / "pdfx_manifest.json").exists():
                pdfx_dir = tmp_path
        if pdfx_dir is None:
            raise HTTPException(400, "No pdfx_manifest.json found in zip")

        out_path = tmp_path / "_linked.pdf"
        try:
            link_pdf(pdfx_dir, out_path)
        except Exception as exc:
            raise HTTPException(500, f"Link failed: {exc}") from exc

        pdf_bytes = out_path.read_bytes()

    # Derive output filename from zip name
    stem = Path(file.filename).stem
    if stem.endswith(".pdfx"):
        stem = stem[:-5]
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )


@app.get("/api/object/{upload_id}/{num}/{gen}")
def get_object(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return the detailed text for a specific PDF object (lazy load)."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found — please re-upload the PDF")

    detail = doc.get_object_detail(num, gen)
    obj = doc.resolve_num(num, gen)
    is_image = False
    is_thumb = False
    is_icc_profile = False
    is_content_stream = False
    is_palette = False
    is_tounicode = False
    is_font_descriptor = False
    is_font = False
    is_type0_font = False
    is_ttf = False
    is_cid_to_gid_map = False
    is_cid_set = False
    image_filter: str | None = None
    if obj and (obj.is_dict() or obj.type == PdfObjType.Stream):
        st = obj.get("Subtype")
        is_image = st.is_name() and st.sval == "Image"
        type_obj2 = obj.get("Type")
        # FontDescriptor dict
        if type_obj2.is_name() and type_obj2.sval == "FontDescriptor":
            is_font_descriptor = True
        # Font dict (any subtype except Type0/CID — those lack a simple encoding)
        if type_obj2.is_name() and type_obj2.sval == "Font":
            st2 = obj.get("Subtype")
            if not (st2.is_name() and st2.sval in ("Type0", "CIDFontType0", "CIDFontType2")):
                is_font = True
            elif st2.is_name() and st2.sval == "Type0":
                is_type0_font = True
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
    # Array of content-stream references (/Contents [11 0 R  12 0 R ...])
    if not is_content_stream and obj and obj.is_array() and len(obj.arr) > 0:
        from pdf.filters import flat_decode
        for item in obj.arr[:4]:  # sample first few items
            if item.is_ref():
                ref_obj = doc.resolve_num(item.ref.num, item.ref.gen)
                if ref_obj and ref_obj.type == PdfObjType.Stream:
                    fobj = ref_obj.get("Filter")
                    if fobj.is_name() and fobj.sval == "FlateDecode":
                        chunk = flat_decode(ref_obj.stream_raw)
                        if chunk and not _is_binary(chunk) and _is_content_stream_data(chunk):
                            is_content_stream = True
                            break
    # Reference chain detections.
    # These indices are fixed by the PDF spec:
    #   [/ICCBased  stream_ref]              → stream is always at [1]
    #   [/Indexed base_cs hival lookup_ref] → lookup is always at [3]
    # The parent.arr[0].sval guard prevents false positives even if the
    # key_path suffix matches by coincidence in an unrelated array.
    backref_index = _backref_cache.get(upload_id, {})
    for ref in backref_index.get(num, []):
        kp, tn = ref["key_path"], ref["type_name"]
        # TrueType/OTF font file: FontDescriptor.FontFile2 → stream
        if kp == "FontFile2" and tn == "FontDescriptor":
            is_ttf = True
        # ToUnicode CMap: Font.ToUnicode → stream
        if kp == "ToUnicode" and tn == "Font":
            is_tounicode = True
        # CIDToGIDMap: CIDFontType2.CIDToGIDMap → stream
        if kp == "CIDToGIDMap" and tn == "Font":
            is_cid_to_gid_map = True
        # CIDSet: FontDescriptor.CIDSet → stream (presence bitmap)
        if kp == "CIDSet" and tn == "FontDescriptor":
            is_cid_set = True
        # ICC profile: [/ICCBased stream_ref] — stream is always at index [1]
        if kp == "[1]":
            parent = doc.resolve_num(ref["from_num"], ref["from_gen"])
            if (parent and parent.is_array() and len(parent.arr) >= 2
                    and parent.arr[0].is_name() and parent.arr[0].sval == "ICCBased"):
                is_icc_profile = True
        # Indexed palette lookup stream: [/Indexed base hival stream_ref]
        # — lookup reference is always at index [3], fixed by the PDF spec.
        if kp == "[3]":
            parent = doc.resolve_num(ref["from_num"], ref["from_gen"])
            if (parent and parent.is_array() and len(parent.arr) >= 4
                    and parent.arr[0].is_name() and parent.arr[0].sval == "Indexed"):
                is_palette = True
        # Page thumbnail: Page.Thumb → image stream (no /Subtype /Image required)
        if kp == "Thumb":
            is_thumb = True
        # Document Information Dictionary: Trailer.Info → dict
        # Graphics State Parameter Dictionary: Resources.ExtGState.xxx → dict
        # (both now handled centrally by _classify_object)
        # Content stream: Page.Contents → stream (direct reference)
        if kp == "Contents" and tn == "Page":
            is_content_stream = True
        # Content stream inside a Contents array: array[n] → stream, array.Contents → Page
        if not is_content_stream:
            parent_obj2 = doc.resolve_num(ref["from_num"], ref["from_gen"])
            if parent_obj2 is not None and parent_obj2.is_array():
                for arr_ref in backref_index.get(ref["from_num"], []):
                    if arr_ref["key_path"] == "Contents" and arr_ref["type_name"] == "Page":
                        is_content_stream = True
                        break

    # Thumb images lack /Subtype /Image but are fully renderable via /api/image
    if is_thumb:
        is_image = True
        if image_filter is None and obj and obj.type == PdfObjType.Stream:
            fobj2 = obj.get("Filter")
            if fobj2.is_name():
                image_filter = fobj2.sval

    # ------------------------------------------------------------------ #
    # Type label — shared classifier keeps all surfaces in sync            #
    # ------------------------------------------------------------------ #
    type_label: str = _classify_object(num, gen, doc, backref_index)

    return {
        "detail": detail,
        "type_label": type_label,
        "is_image": is_image,
        "is_thumb": is_thumb,
        "is_icc_profile": is_icc_profile,
        "is_content_stream": is_content_stream,
        "is_palette": is_palette,
        "is_tounicode": is_tounicode,
        "is_font_descriptor": is_font_descriptor,
        "is_font": is_font,
        "is_type0_font": is_type0_font,
        "is_ttf": is_ttf,
        "is_cid_to_gid_map": is_cid_to_gid_map,
        "is_cid_set": is_cid_set,
        "is_hint_stream": (type_label == "Linearization Hint Stream"),
        "image_filter": image_filter,
        "obj_num": num,
        "gen_num": gen,
    }


def _collect_page_dict_offsets(doc: Any) -> list[int]:
    """Return the file offset of each page's Page-dict xref entry, in page order.

    Traverses Catalog → /Pages → /Kids recursively.  Compressed entries are
    resolved to their parent object stream's file offset so the value is always
    an absolute file position.  Returns [] on any failure.
    """
    def _eff_offset(num: int) -> int:
        xe = doc._xref.get_entry(num)
        if xe is None:
            return 0
        if xe.etype == XrefEntryType.InUse:
            return xe.offset
        if xe.etype == XrefEntryType.Compressed:
            xe2 = doc._xref.get_entry(xe.offset)
            return xe2.offset if xe2 and xe2.etype == XrefEntryType.InUse else 0
        return 0

    def _walk(obj_num: int, out: list[int]) -> None:
        obj = doc.resolve_num(obj_num)
        if obj is None or not obj.is_dict():
            return
        t = obj.get("Type")
        if not t.is_name():
            return
        if t.sval == "Page":
            out.append(_eff_offset(obj_num))
        elif t.sval == "Pages":
            kids = obj.get("Kids")
            if kids.is_array():
                for kid in kids.arr:
                    if kid.is_ref():
                        _walk(kid.ref.num, out)

    try:
        trailer = doc._trailer
        if not trailer.is_dict():
            return []
        root_ref = trailer.get("Root")
        if not root_ref.is_ref():
            return []
        catalog = doc.resolve_num(root_ref.ref.num)
        if catalog is None or not catalog.is_dict():
            return []
        pages_ref = catalog.get("Pages")
        if not pages_ref.is_ref():
            return []
        result: list[int] = []
        _walk(pages_ref.ref.num, result)
        return result
    except Exception:
        return []


def _parse_hint_stream(raw: bytes, n_pages: int, shared_offset: int, first_page_obj: int = 0) -> dict[str, Any]:
    """Parse the binary linearization hint tables from the decoded stream bytes.

    Field sizes follow the qpdf reference implementation (QPDF_linearization.cc):
    - Page hints header: 32+32+16+32+16+32+16+32+16+16+16+16+16 = 288 bits (36 bytes)
    - Each nbits_* field in the header is 16 bits (NOT 4).
    - Per-page rows are each padded to the next byte boundary after all pages.
    - Shared hints header: 32+32+32+32+16+32+16 = 192 bits (24 bytes)
    - first_page_obj: PDF object number of the first page (lin dict /O); groups
      0..nshared_first_page-1 start here, groups nshared_first_page..total-1
      start from first_shared_obj (per qpdf QPDF_linearization.cc checkHSharedObject).
    """
    bits = "".join(f"{b:08b}" for b in raw)
    pos = 0

    def rb(n: int) -> int:
        nonlocal pos
        v = int(bits[pos : pos + n], 2) if n else 0
        pos += n
        return v

    def skip_byte():
        nonlocal pos
        rem = pos % 8
        if rem:
            pos += 8 - rem

    # ── Page Offset Hints Table header ───────────────────────────────────────
    min_nobjects       = rb(32)
    first_page_offset  = rb(32)
    nbits_dn           = rb(16)   # delta nobjects bits
    min_page_length    = rb(32)
    nbits_dl           = rb(16)   # delta page length bits
    min_co_offset      = rb(32)   # content offset (Acrobat always 0)
    nbits_dco          = rb(16)
    min_co_length      = rb(32)   # content length (Acrobat always 0)
    nbits_dcl          = rb(16)
    nbits_ns           = rb(16)   # nshared objects bits
    nbits_sid          = rb(16)   # shared identifier bits
    nbits_snum         = rb(16)   # shared numerator bits
    shared_denom       = rb(16)

    # ── Per-page entries (7 "rows", each padded to byte boundary after all pages)
    entries: list[dict[str, Any]] = [{} for _ in range(n_pages)]

    def load_row(field: str, nbits: int) -> None:
        for i in range(n_pages):
            entries[i][field] = rb(nbits) if nbits else 0
        skip_byte()

    load_row("dn",  nbits_dn)
    load_row("dl",  nbits_dl)
    load_row("ns",  nbits_ns)

    # shared identifiers (variable per page)
    for i in range(n_pages):
        entries[i]["si"] = [rb(nbits_sid) for _ in range(entries[i]["ns"])] if nbits_sid else []
    skip_byte()

    # shared numerators (variable per page)
    for i in range(n_pages):
        entries[i]["sn"] = [rb(nbits_snum) for _ in range(entries[i]["ns"])] if nbits_snum else []
    skip_byte()

    load_row("dco", nbits_dco)
    load_row("dcl", nbits_dcl)

    page_header = {
        "min_nobjects":             min_nobjects,
        "first_page_offset":        first_page_offset,
        "nbits_delta_nobjects":     nbits_dn,
        "min_page_length":          min_page_length,
        "nbits_delta_page_length":  nbits_dl,
        "min_co_offset":            min_co_offset,
        "nbits_delta_co_offset":    nbits_dco,
        "min_co_length":            min_co_length,
        "nbits_delta_co_length":    nbits_dcl,
        "nbits_nshared":            nbits_ns,
        "nbits_shared_id":          nbits_sid,
        "nbits_shared_num":         nbits_snum,
        "shared_denom":             shared_denom,
    }
    pages = [
        {
            "nobjects":        min_nobjects + e["dn"],
            "page_length":     min_page_length + e["dl"],
            "content_offset":  min_co_offset + e["dco"],
            "content_length":  min_co_length + e["dcl"],
            "nshared":         e["ns"],
            "shared_ids":      e["si"],
        }
        for e in entries
    ]

    # ── Shared Objects Hints Table ────────────────────────────────────────────
    spos = shared_offset * 8  # bit offset of shared table

    def srb(n: int) -> int:
        nonlocal spos
        v = int(bits[spos : spos + n], 2) if n else 0
        spos += n
        return v

    def sskip_byte() -> None:
        nonlocal spos
        rem = spos % 8
        if rem:
            spos += 8 - rem

    first_shared_obj      = srb(32)
    first_shared_offset   = srb(32)
    nshared_first_page    = srb(32)
    nshared_total         = srb(32)
    nbits_nobjects        = srb(16)
    min_group_length      = srb(32)
    nbits_delta_gl        = srb(16)

    shared_header = {
        "first_shared_obj":    first_shared_obj,
        "first_shared_offset": first_shared_offset,
        "nshared_first_page":  nshared_first_page,
        "nshared_total":       nshared_total,
        "nbits_nobjects":      nbits_nobjects,
        "min_group_length":    min_group_length,
        "nbits_delta_group_length": nbits_delta_gl,
    }

    # Per-group entries
    n_groups = nshared_total
    delta_gl: list[int] = []
    for _ in range(n_groups):
        delta_gl.append(srb(nbits_delta_gl) if nbits_delta_gl else 0)
    sskip_byte()

    sig_present: list[int] = []
    for _ in range(n_groups):
        sig_present.append(srb(1))
    sskip_byte()

    for sp in sig_present:
        if sp:  # skip 128-bit MD5 hash
            srb(128)

    nobjs_m1: list[int] = []
    for _ in range(n_groups):
        nobjs_m1.append(srb(nbits_nobjects) if nbits_nobjects else 0)
    sskip_byte()

    # Compute first PDF object number for each group.
    # First-page shared groups start at first_page_obj (/O in the lin dict).
    # They are laid out in file order within page 0's section — the same
    # objects that make up page 0's section are the first-page shared groups.
    # Non-first-page shared objects start from first_shared_obj (shared header).
    shared_groups = []
    obj_cursor = first_page_obj
    for i in range(n_groups):
        if i == nshared_first_page:
            obj_cursor = first_shared_obj
        nobjs = nobjs_m1[i] + 1
        shared_groups.append({
            "group_length": min_group_length + delta_gl[i],
            "nobjects":     nobjs,
            "first_obj":    obj_cursor,
            "section":      "first_page" if i < nshared_first_page else "rest",
        })
        obj_cursor += nobjs

    return {
        "page_header":    page_header,
        "pages":          pages,
        "shared_header":  shared_header,
        "shared_groups":  shared_groups,
    }


@app.get("/api/hint_stream/{upload_id}/{num}/{gen}")
def get_hint_stream(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Parse a linearization hint stream and return structured metadata."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    raw = _decode_stream(obj)
    if raw is None:
        raise HTTPException(422, "Cannot decode stream")

    decoded_size = len(raw)
    raw_size = len(obj.stream_raw)

    # /S gives the byte offset of the shared objects hint table in the decoded stream
    s_obj = obj.get("S")
    shared_offset: int | None = s_obj.ival if s_obj.is_int() else None
    page_hints_size = shared_offset if shared_offset is not None else decoded_size
    shared_hints_size = decoded_size - shared_offset if shared_offset is not None else 0

    # Find the linearization parameter dictionary (contains /Linearized key)
    from pdf.xref import XrefEntryType as _XET2
    lin_params: dict[str, Any] = {}
    n_pages: int = 0
    for lnum, lxe in doc._xref.entries.items():
        if lxe.etype not in (_XET2.InUse, _XET2.Compressed):
            continue
        lobj = doc.resolve_num(lnum, lxe.gen)
        if lobj and lobj.is_dict() and lobj.get("Linearized").is_int():
            h_obj = lobj.get("H")
            n_val = lobj.get("N")
            n_pages = n_val.ival if n_val.is_int() else 0
            lin_params = {
                "obj_num": lnum,
                "file_length": lobj.get("L").ival if lobj.get("L").is_int() else None,
                "first_page_obj": lobj.get("O").ival if lobj.get("O").is_int() else None,
                "num_pages": n_pages,
                "end_of_first_page": lobj.get("E").ival if lobj.get("E").is_int() else None,
                "main_xref_offset": lobj.get("T").ival if lobj.get("T").is_int() else None,
                "hint_offset": h_obj.arr[0].ival if h_obj.is_array() and len(h_obj.arr) >= 1 and h_obj.arr[0].is_int() else None,
                "hint_length": h_obj.arr[1].ival if h_obj.is_array() and len(h_obj.arr) >= 2 and h_obj.arr[1].is_int() else None,
            }
            break

    # Parse the binary hint tables when we have both page count and shared offset
    hint_tables: dict[str, Any] = {}
    if n_pages > 0 and shared_offset is not None:
        try:
            first_page_obj = lin_params.get("first_page_obj") or 0
            hint_tables = _parse_hint_stream(raw, n_pages, shared_offset, first_page_obj)
        except Exception:
            pass  # best-effort; leave hint_tables empty on parse failure

    # Compute section_offset per page:
    #   page 0 → first_page_offset from the hint header (absolute file position)
    #   pages 1..N-1 → laid out sequentially starting from lin dict /E (end_of_first_page)
    if hint_tables.get("pages") and hint_tables.get("page_header"):
        fp_offset = hint_tables["page_header"]["first_page_offset"]
        eof1p = lin_params.get("end_of_first_page") or 0
        hint_offset_val = lin_params.get("hint_offset") or 0
        hint_length_val = lin_params.get("hint_length") or 0
        # Apply adjusted_offset correction (qpdf QPDF_linearization.cc):
        # hint table offsets are computed without the hint stream's own bytes.
        # If first_page_offset >= H_offset, the actual file position = fp_offset + H_length.
        actual_fp_offset = (
            fp_offset + hint_length_val
            if hint_offset_val > 0 and fp_offset >= hint_offset_val
            else fp_offset
        )
        hint_pages = hint_tables["pages"]
        if hint_pages:
            hint_pages[0]["section_offset"] = actual_fp_offset

            # For pages 1..N-1 use the xref offset of each page's Page dictionary
            # rather than cumulative page_lengths.  The hint table page_lengths
            # include separator whitespace, causing cumulative drift (e.g. 152 B
            # over 45 pages in NESDoc.pdf) that misattributes objects near page
            # boundaries.  The Page dict is always the first object in a page
            # section in a properly linearized PDF.
            page_dict_offsets = _collect_page_dict_offsets(doc)
            if len(page_dict_offsets) == len(hint_pages):
                for i in range(1, len(hint_pages)):
                    hint_pages[i]["section_offset"] = page_dict_offsets[i]
            else:
                # Fallback: cumulative page_lengths (less accurate)
                cum = eof1p
                for i in range(1, len(hint_pages)):
                    hint_pages[i]["section_offset"] = cum
                    cum += hint_pages[i]["page_length"]

            # Deduce private object IDs per page from the xref table.
            # Strategy: assign each object an "effective" file offset —
            #   InUse objects use their own offset directly;
            #   Compressed objects (stored inside object streams) use the
            #   file offset of their parent object stream so they land in
            #   the right section.
            # Shared objects, document-tail objects, etc. need no explicit
            # exclusion: page_length already bounds each page's private region
            # exactly — shared objects physically reside in different byte
            # ranges (between or after page sections).

            # Map object-stream obj_num → file offset (for resolving Compressed entries)
            stm_offsets: dict[int, int] = {
                num: xe.offset
                for num, xe in doc._xref.entries.items()
                if xe.etype == XrefEntryType.InUse
            }

            all_obj_offsets: list[tuple[int, int]] = []  # (effective_offset, obj_num)
            for num, xe in doc._xref.entries.items():
                if xe.etype == XrefEntryType.InUse:
                    all_obj_offsets.append((xe.offset, num))
                elif xe.etype == XrefEntryType.Compressed:
                    parent_off = stm_offsets.get(xe.offset)
                    if parent_off is not None:
                        all_obj_offsets.append((parent_off, num))
            all_obj_offsets.sort()

            backref_index = _backref_cache.get(upload_id, {})
            for p in hint_pages:
                sec_start = p["section_offset"]
                sec_end = sec_start + p["page_length"]
                nums_in = sorted(
                    num for off, num in all_obj_offsets
                    if sec_start <= off < sec_end
                )
                p["deduced_objects"] = [
                    {"num": num, "obj_type": _classify_object(num, 0, doc, backref_index)}
                    for num in nums_in
                ]

    # Annotate each shared group with a semantic type label for its lead object
    if hint_tables.get("shared_groups"):
        backref_index = _backref_cache.get(upload_id, {})
        for group in hint_tables["shared_groups"]:
            obj_num = group.get("first_obj", 0)
            group["obj_type"] = _classify_object(obj_num, 0, doc, backref_index) if obj_num else "?"

    return {
        "raw_size": raw_size,
        "decoded_size": decoded_size,
        "shared_offset": shared_offset,
        "page_hints_size": page_hints_size,
        "shared_hints_size": shared_hints_size,
        "lin_params": lin_params,
        **hint_tables,
    }


@app.get("/api/cid_set/{upload_id}/{num}/{gen}")
def get_cid_set(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Parse a CIDSet stream and return the presence bitmap."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    raw = _decode_stream(obj)
    if raw is None:
        raise HTTPException(422, "Cannot decode stream")

    total_slots = len(raw) * 8  # total CID slots represented
    present_cids: list[int] = []
    for i, byte in enumerate(raw):
        for bit in range(8):
            if byte & (0x80 >> bit):
                present_cids.append(i * 8 + bit)

    # Last set bit = highest present CID
    last_cid = present_cids[-1] if present_cids else 0

    return {
        "total_slots": total_slots,
        "present_count": len(present_cids),
        "last_cid": last_cid,
        "coverage_hex": raw.hex(),  # raw bitmap bytes as hex (1 bit per CID, MSB first)
    }


@app.get("/api/cid_to_gid/{upload_id}/{num}/{gen}")
def get_cid_to_gid(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Parse a CIDToGIDMap stream and return the CID→GID mapping table."""
    import struct as _s
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    raw = _decode_stream(obj)
    if raw is None:
        raise HTTPException(422, "Cannot decode stream")

    total_cids = len(raw) // 2
    entries = []
    for cid in range(total_cids):
        gid = _s.unpack_from(">H", raw, cid * 2)[0]
        if gid != 0:
            entries.append({"cid": cid, "gid": gid})

    # Build a compact coverage bitmap: 1 bit per CID slot, packed into hex string
    # so the frontend can render a heatmap without sending 19k rows individually.
    # Each byte covers 8 consecutive CIDs (MSB = lowest CID index).
    bmp_bytes = bytearray((total_cids + 7) // 8)
    for e in entries:
        cid = e["cid"]
        bmp_bytes[cid >> 3] |= 0x80 >> (cid & 7)
    coverage_hex = bmp_bytes.hex()

    return {
        "total_cids": total_cids,
        "mapped_count": len(entries),
        "entries": entries[:5000],  # cap to avoid huge payloads
        "coverage_hex": coverage_hex,
    }


@app.get("/api/font/{upload_id}/{num}/{gen}")
def get_font_info(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return metadata and character encoding map for a simple (non-CID) Font dict."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or not obj.is_dict():
        raise HTTPException(404, "Object not found")

    type_key = obj.get("Type")
    if not (type_key.is_name() and type_key.sval == "Font"):
        raise HTTPException(422, "Not a Font object")

    base_font_obj = obj.get("BaseFont")
    subtype_obj = obj.get("Subtype")
    first_char_obj = obj.get("FirstChar")
    last_char_obj = obj.get("LastChar")
    widths_obj = obj.get("Widths")
    enc_obj = obj.get("Encoding")

    first_char = first_char_obj.ival if first_char_obj.is_int() else None
    last_char = last_char_obj.ival if last_char_obj.is_int() else None
    widths: list[float] | None = None
    if widths_obj.is_array():
        widths = []
        for w in widths_obj.arr:
            if w.is_int():
                widths.append(float(w.ival))
            elif w.is_real():
                widths.append(w.dval)
            else:
                widths.append(0.0)

    # Encoding name
    enc_name: str | None = None
    if enc_obj.is_name():
        enc_name = enc_obj.sval
    elif enc_obj.is_dict():
        base_enc = enc_obj.get("BaseEncoding")
        enc_name = f"Custom (base: {base_enc.sval})" if base_enc.is_name() else "Custom"

    # Determine if the font has an embedded font file (via FontDescriptor)
    is_embedded = False
    fd_num: int | None = None
    fd_ref = obj.get("FontDescriptor")
    if fd_ref.is_ref():
        fd_num = fd_ref.ref.num
        fd_obj = doc.resolve_num(fd_ref.ref.num, fd_ref.ref.gen)
        if fd_obj is not None and fd_obj.is_dict():
            for ff_key in ("FontFile", "FontFile2", "FontFile3"):
                if fd_obj.get(ff_key).is_ref():
                    is_embedded = True
                    break

    # ToUnicode reference
    to_unicode_num: int | None = None
    tu_ref = obj.get("ToUnicode")
    if tu_ref.is_ref():
        to_unicode_num = tu_ref.ref.num

    # Build encoding cmap (byte → unicode char)
    raw_cmap = _build_encoding_cmap(obj)
    # JSON keys must be strings
    cmap = {str(k): v for k, v in raw_cmap.items()}

    return {
        "base_font": base_font_obj.sval if base_font_obj.is_name() else None,
        "subtype": subtype_obj.sval if subtype_obj.is_name() else None,
        "encoding": enc_name,
        "first_char": first_char,
        "last_char": last_char,
        "widths": widths,
        "is_embedded": is_embedded,
        "cmap": cmap,
        "font_descriptor_num": fd_num,
        "to_unicode_num": to_unicode_num,
    }


@app.get("/api/type0font/{upload_id}/{num}/{gen}")
def get_type0_font_info(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return metadata for a Type0 (composite/CID) Font dict."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or not obj.is_dict():
        raise HTTPException(404, "Object not found")

    type_key = obj.get("Type")
    if not (type_key.is_name() and type_key.sval == "Font"):
        raise HTTPException(422, "Not a Font object")
    st = obj.get("Subtype")
    if not (st.is_name() and st.sval == "Type0"):
        raise HTTPException(422, "Not a Type0 font")

    base_font_obj = obj.get("BaseFont")
    enc_obj = obj.get("Encoding")
    to_unicode_ref = obj.get("ToUnicode")

    encoding = enc_obj.sval if enc_obj.is_name() else None
    to_unicode_num: int | None = to_unicode_ref.ref.num if to_unicode_ref.is_ref() else None

    # Resolve DescendantFonts[0] — the CIDFont dict
    descendant_num: int | None = None
    cid_subtype: str | None = None
    cid_base_font: str | None = None
    cid_registry: str | None = None
    cid_ordering: str | None = None
    cid_supplement: int | None = None
    default_width: int = 1000
    is_embedded = False
    font_descriptor_num: int | None = None

    df_obj = obj.get("DescendantFonts")
    if df_obj.is_array() and df_obj.arr:
        df0 = df_obj.arr[0]
        df0_dict = None
        if df0.is_ref():
            descendant_num = df0.ref.num
            df0_dict = doc.resolve_num(df0.ref.num, df0.ref.gen)
        elif df0.is_dict():
            df0_dict = df0
        if df0_dict is not None and df0_dict.is_dict():
            cid_st = df0_dict.get("Subtype")
            cid_bf = df0_dict.get("BaseFont")
            cid_subtype = cid_st.sval if cid_st.is_name() else None
            cid_base_font = cid_bf.sval if cid_bf.is_name() else None
            dw_obj = df0_dict.get("DW")
            if dw_obj.is_int():
                default_width = int(dw_obj.ival)
            # CIDSystemInfo
            csi = df0_dict.get("CIDSystemInfo")
            if csi.is_dict():
                reg = csi.get("Registry")
                ord_ = csi.get("Ordering")
                sup = csi.get("Supplement")
                cid_registry = reg.sval if reg.is_name() else (reg.decoded if hasattr(reg, 'decoded') and reg.type == PdfObjType.String else None)
                cid_ordering = ord_.sval if ord_.is_name() else (ord_.decoded if hasattr(ord_, 'decoded') and ord_.type == PdfObjType.String else None)
                cid_supplement = int(sup.ival) if sup.is_int() else None
                # fallback: get string value from sval
                if cid_registry is None and hasattr(reg, 'sval'):
                    cid_registry = reg.sval
                if cid_ordering is None and hasattr(ord_, 'sval'):
                    cid_ordering = ord_.sval
            # FontDescriptor
            fd_ref = df0_dict.get("FontDescriptor")
            if fd_ref.is_ref():
                font_descriptor_num = fd_ref.ref.num
                fd_obj = doc.resolve_num(fd_ref.ref.num, fd_ref.ref.gen)
                if fd_obj is not None and fd_obj.is_dict():
                    for ff_key in ("FontFile", "FontFile2", "FontFile3"):
                        if fd_obj.get(ff_key).is_ref():
                            is_embedded = True
                            break

    # ToUnicode cmap
    cmap: dict[str, str] = {}
    if to_unicode_num is not None:
        tu_obj = doc.resolve_num(to_unicode_num, 0)
        if tu_obj is not None:
            raw_cmap = _parse_cmap_from_stream(tu_obj)
            cmap = {str(k): v for k, v in raw_cmap.items()}

    return {
        "base_font": base_font_obj.sval if base_font_obj.is_name() else None,
        "encoding": encoding,
        "subtype": "Type0",
        "to_unicode_num": to_unicode_num,
        "descendant_num": descendant_num,
        "cid_subtype": cid_subtype,
        "cid_base_font": cid_base_font,
        "cid_registry": cid_registry,
        "cid_ordering": cid_ordering,
        "cid_supplement": cid_supplement,
        "default_width": default_width,
        "is_embedded": is_embedded,
        "font_descriptor_num": font_descriptor_num,
        "cmap": cmap,
    }


@app.get("/api/backrefs/{upload_id}/{num}")
def get_backrefs(upload_id: str, num: int) -> dict[str, Any]:
    """Return all objects that contain a reference to the given object number."""
    if upload_id not in _sessions:
        raise HTTPException(404, "Session not found — please re-upload the PDF")
    index = _backref_cache.get(upload_id, {})
    refs = index.get(num, [])
    return {"obj_num": num, "refs": refs}


@app.get("/api/xref/{upload_id}")
def get_xref_table(upload_id: str) -> dict[str, Any]:
    """Return structured cross-reference table data for the given document."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found — please re-upload the PDF")
    backref_index = _backref_cache.get(upload_id, {})

    entries = doc._xref.entries
    in_use = sum(1 for e in entries.values() if e.etype == XrefEntryType.InUse)
    free   = sum(1 for e in entries.values() if e.etype == XrefEntryType.Free)
    compressed = sum(1 for e in entries.values() if e.etype == XrefEntryType.Compressed)
    file_size = doc._reader.size if doc._reader is not None else 0

    # Compute byte sizes for in-use objects by sorting by offset
    sorted_inuse = sorted(
        [(num, xe) for num, xe in entries.items() if xe.etype == XrefEntryType.InUse],
        key=lambda x: x[1].offset,
    )
    obj_sizes: dict[int, int] = {}
    for i, (num, xe) in enumerate(sorted_inuse):
        next_off = sorted_inuse[i + 1][1].offset if i + 1 < len(sorted_inuse) else file_size
        obj_sizes[num] = next_off - xe.offset

    def _obj_kind(num: int, gen: int) -> str:
        return _classify_object(num, gen, doc, backref_index)

    rows: list[dict[str, Any]] = []
    for obj_num in sorted(entries):
        xe = entries[obj_num]
        if xe.etype == XrefEntryType.Free:
            rows.append({"obj_num": obj_num, "etype": "free", "offset": xe.offset, "gen": xe.gen})
        elif xe.etype == XrefEntryType.InUse:
            rows.append({
                "obj_num": obj_num, "etype": "in_use",
                "offset": xe.offset, "gen": xe.gen,
                "kind": _obj_kind(obj_num, xe.gen),
                "size_bytes": obj_sizes.get(obj_num, 0),
            })
        else:
            rows.append({
                "obj_num": obj_num, "etype": "compressed",
                "offset": 0, "gen": xe.gen,
                "stm_num": xe.offset,
                "stm_index": xe.index_in_stm,
                "kind": _obj_kind(obj_num, xe.gen),
                "size_bytes": 0,
            })

    return {
        "total": len(entries),
        "in_use": in_use,
        "free": free,
        "compressed": compressed,
        "file_size": file_size,
        "entries": rows,
    }


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


def _parse_cmap_from_stream(obj: Any) -> dict[int, str]:
    """Extract {char_code: unicode_char} from a /ToUnicode CMap stream."""
    import re as _re
    from pdf.filters import flat_decode

    fobj = obj.get('Filter') if obj.is_dict() else None
    if fobj is not None and fobj.is_name() and fobj.sval == 'FlateDecode':
        raw = flat_decode(obj.stream_raw)
    else:
        raw = obj.stream_raw
    if not raw:
        return {}
    text = raw.decode('latin-1', errors='replace')
    cmap: dict[int, str] = {}

    for block in _re.findall(r'beginbfchar(.*?)endbfchar', text, _re.DOTALL):
        for m in _re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            src = int(m.group(1), 16)
            if src in cmap:
                continue
            try:
                cmap[src] = bytes.fromhex(m.group(2)).decode('utf-16-be')
            except Exception:
                cmap[src] = '?'

    for block in _re.findall(r'beginbfrange(.*?)endbfrange', text, _re.DOTALL):
        for m in _re.finditer(
            r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',
            block,
        ):
            lo = int(m.group(1), 16)
            hi = int(m.group(2), 16)
            base_cp = int.from_bytes(bytes.fromhex(m.group(3)), 'big')
            for i, src in enumerate(range(lo, hi + 1)):
                if src not in cmap:
                    try:
                        cmap[src] = chr(base_cp + i)
                    except Exception:
                        cmap[src] = '?'

    return cmap


# ---------------------------------------------------------------------------
# Standard PDF encoding helpers
# ---------------------------------------------------------------------------

# Minimal Adobe Glyph List for resolving Encoding /Differences names → unicode
_ADOBE_GLYPH: dict[str, str] = {
    'space': ' ', 'exclam': '!', 'quotedbl': '"', 'numbersign': '#',
    'dollar': '$', 'percent': '%', 'ampersand': '&', 'quotesingle': "\u2019",
    'parenleft': '(', 'parenright': ')', 'asterisk': '*', 'plus': '+',
    'comma': ',', 'hyphen': '-', 'period': '.', 'slash': '/',
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    'colon': ':', 'semicolon': ';', 'less': '<', 'equal': '=', 'greater': '>',
    'question': '?', 'at': '@',
    'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F', 'G': 'G',
    'H': 'H', 'I': 'I', 'J': 'J', 'K': 'K', 'L': 'L', 'M': 'M', 'N': 'N',
    'O': 'O', 'P': 'P', 'Q': 'Q', 'R': 'R', 'S': 'S', 'T': 'T', 'U': 'U',
    'V': 'V', 'W': 'W', 'X': 'X', 'Y': 'Y', 'Z': 'Z',
    'bracketleft': '[', 'backslash': '\\', 'bracketright': ']',
    'asciicircum': '^', 'underscore': '_', 'grave': '`',
    'a': 'a', 'b': 'b', 'c': 'c', 'd': 'd', 'e': 'e', 'f': 'f', 'g': 'g',
    'h': 'h', 'i': 'i', 'j': 'j', 'k': 'k', 'l': 'l', 'm': 'm', 'n': 'n',
    'o': 'o', 'p': 'p', 'q': 'q', 'r': 'r', 's': 's', 't': 't', 'u': 'u',
    'v': 'v', 'w': 'w', 'x': 'x', 'y': 'y', 'z': 'z',
    'braceleft': '{', 'bar': '|', 'braceright': '}', 'asciitilde': '~',
    # Latin Extended
    'Agrave': '\u00c0', 'Aacute': '\u00c1', 'Acircumflex': '\u00c2',
    'Atilde': '\u00c3', 'Adieresis': '\u00c4', 'Aring': '\u00c5',
    'AE': '\u00c6', 'Ccedilla': '\u00c7', 'Egrave': '\u00c8',
    'Eacute': '\u00c9', 'Ecircumflex': '\u00ca', 'Edieresis': '\u00cb',
    'Igrave': '\u00cc', 'Iacute': '\u00cd', 'Icircumflex': '\u00ce',
    'Idieresis': '\u00cf', 'Eth': '\u00d0', 'Ntilde': '\u00d1',
    'Ograve': '\u00d2', 'Oacute': '\u00d3', 'Ocircumflex': '\u00d4',
    'Otilde': '\u00d5', 'Odieresis': '\u00d6', 'multiply': '\u00d7',
    'Oslash': '\u00d8', 'Ugrave': '\u00d9', 'Uacute': '\u00da',
    'Ucircumflex': '\u00db', 'Udieresis': '\u00dc', 'Yacute': '\u00dd',
    'Thorn': '\u00de', 'germandbls': '\u00df',
    'agrave': '\u00e0', 'aacute': '\u00e1', 'acircumflex': '\u00e2',
    'atilde': '\u00e3', 'adieresis': '\u00e4', 'aring': '\u00e5',
    'ae': '\u00e6', 'ccedilla': '\u00e7', 'egrave': '\u00e8',
    'eacute': '\u00e9', 'ecircumflex': '\u00ea', 'edieresis': '\u00eb',
    'igrave': '\u00ec', 'iacute': '\u00ed', 'icircumflex': '\u00ee',
    'idieresis': '\u00ef', 'eth': '\u00f0', 'ntilde': '\u00f1',
    'ograve': '\u00f2', 'oacute': '\u00f3', 'ocircumflex': '\u00f4',
    'otilde': '\u00f5', 'odieresis': '\u00f6', 'divide': '\u00f7',
    'oslash': '\u00f8', 'ugrave': '\u00f9', 'uacute': '\u00fa',
    'ucircumflex': '\u00fb', 'udieresis': '\u00fc', 'yacute': '\u00fd',
    'thorn': '\u00fe', 'ydieresis': '\u00ff',
    # Common typographic
    'fi': '\ufb01', 'fl': '\ufb02', 'ffi': '\ufb03', 'ffl': '\ufb04',
    'endash': '\u2013', 'emdash': '\u2014',
    'quotedblleft': '\u201c', 'quotedblright': '\u201d',
    'quoteleft': '\u2018', 'quoteright': '\u2019',
    'bullet': '\u2022', 'ellipsis': '\u2026',
    'dagger': '\u2020', 'daggerdbl': '\u2021',
    'trademark': '\u2122', 'registered': '\u00ae', 'copyright': '\u00a9',
    'Euro': '\u20ac', 'euro': '\u20ac',
    'degree': '\u00b0', 'mu': '\u00b5', 'paragraph': '\u00b6',
    'periodcentered': '\u00b7', 'middot': '\u00b7',
    'onesuperior': '\u00b9', 'twosuperior': '\u00b2', 'threesuperior': '\u00b3',
    'onehalf': '\u00bd', 'onequarter': '\u00bc', 'threequarters': '\u00be',
    'guillemotleft': '\u00ab', 'guillemotright': '\u00bb',
    'guilsinglleft': '\u2039', 'guilsinglright': '\u203a',
    'ordmasculine': '\u00ba', 'ordfeminine': '\u00aa',
    'section': '\u00a7', 'yen': '\u00a5', 'sterling': '\u00a3', 'cent': '\u00a2',
    'plusminus': '\u00b1', 'notsign': '\u00ac', 'brokenbar': '\u00a6',
    'macron': '\u00af', 'cedilla': '\u00b8', 'acute': '\u00b4',
    'dieresis': '\u00a8', 'circumflex': '\u02c6', 'tilde': '\u02dc',
    'ring': '\u02da', 'exclamdown': '\u00a1', 'questiondown': '\u00bf',
    'dotaccent': '\u02d9', 'caron': '\u02c7', 'breve': '\u02d8',
    'hungarumlaut': '\u02dd', 'ogonek': '\u02db', 'dotlessi': '\u0131',
    'Lslash': '\u0141', 'lslash': '\u0142',
    'Scaron': '\u0160', 'scaron': '\u0161',
    'Zcaron': '\u017d', 'zcaron': '\u017e',
    'OE': '\u0152', 'oe': '\u0153', 'Ydieresis': '\u0178',
    'notdef': '',
}


def _build_cmap_from_encoding_name(name: str) -> dict[int, str]:
    """Return byte→unicode char map for a named PDF encoding."""
    result: dict[int, str] = {}
    if name == 'WinAnsiEncoding':
        for b in range(32, 256):
            try:
                result[b] = bytes([b]).decode('cp1252')
            except (ValueError, UnicodeDecodeError):
                pass
    elif name == 'MacRomanEncoding':
        for b in range(32, 256):
            try:
                result[b] = bytes([b]).decode('mac_roman')
            except (ValueError, UnicodeDecodeError):
                pass
    else:
        # StandardEncoding / PDFDocEncoding / unknown → latin-1 approximation
        for b in range(32, 256):
            try:
                result[b] = bytes([b]).decode('latin-1')
            except (ValueError, UnicodeDecodeError):
                pass
    return result


def _build_encoding_cmap(font_obj: Any) -> dict[int, str]:
    """Build byte→unicode map from a Font dict's /Encoding entry."""
    enc = font_obj.get('Encoding')
    if enc.is_name():
        return _build_cmap_from_encoding_name(enc.sval)
    if enc.is_dict():
        base_enc = enc.get('BaseEncoding')
        base_name = base_enc.sval if base_enc.is_name() else 'StandardEncoding'
        result = _build_cmap_from_encoding_name(base_name)
        # Apply Differences: [code /name /name ... code /name ...]
        diffs = enc.get('Differences')
        if diffs.is_array():
            code = 0
            for item in diffs.arr:
                if item.is_int():
                    code = item.ival
                elif item.is_name():
                    result[code] = _ADOBE_GLYPH.get(item.sval, '?')
                    code += 1
        return result
    # No Encoding entry — default WinAnsi for Latin fonts
    return _build_cmap_from_encoding_name('WinAnsiEncoding')


@app.get("/api/content_stream/{upload_id}/{num}/{gen}")
def get_content_stream(upload_id: str, num: int, gen: int) -> dict[str, Any]:
    """Return parsed operator data for a PDF content stream."""
    from pdf.filters import flat_decode

    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None:
        raise HTTPException(404, "Object not found")

    # /Contents array: concatenate all referenced streams into one byte sequence
    if obj.is_array():
        chunks: list[bytes] = []
        for item in obj.arr:
            if item.is_ref():
                ref_obj = doc.resolve_num(item.ref.num, item.ref.gen)
                if ref_obj and ref_obj.type == PdfObjType.Stream:
                    fobj2 = ref_obj.get("Filter")
                    if fobj2.is_name() and fobj2.sval == "FlateDecode":
                        chunk = flat_decode(ref_obj.stream_raw)
                    elif fobj2.is_null():
                        chunk = ref_obj.stream_raw
                    else:
                        chunk = None
                    if chunk:
                        chunks.append(chunk)
        if not chunks:
            raise HTTPException(422, "Array contains no decodable content streams")
        decoded: bytes | None = b"\n".join(chunks)
    elif obj.type == PdfObjType.Stream:
        fobj = obj.get("Filter")
        if fobj.is_name() and fobj.sval == "FlateDecode":
            decoded = flat_decode(obj.stream_raw)
        elif fobj.is_null():
            decoded = obj.stream_raw
        else:
            raise HTTPException(422, "Unsupported filter for content stream")
    else:
        raise HTTPException(404, "Object is not a stream or array")

    if decoded is None:
        raise HTTPException(422, "Failed to decode stream")

    result = parse_content_stream(decoded)
    if result is None:
        raise HTTPException(422, "Not a valid content stream")

    # ── Resolve page resources & media box for front-end canvas rendering ──────
    def _num_val(v: Any) -> float | None:
        if v.is_int(): return float(v.ival)
        if v.type == PdfObjType.Real: return v.dval
        return None

    def _resolve_res_dict(ref_obj: Any) -> Any:
        """Dereference a resource dict value (may be an indirect ref or inline dict)."""
        if ref_obj.is_ref():
            return doc.resolve_num(ref_obj.ref.num, ref_obj.ref.gen)
        if ref_obj.is_dict():
            return ref_obj
        return None

    resources: dict[str, Any] = {'xobject': {}, 'font': {}}
    media_box: list[float] | None = None

    # Check the stream object itself for BBox / Resources (handles Form XObjects too)
    bbox_val = obj.get('BBox')
    if bbox_val.is_array() and len(bbox_val.arr) >= 4:
        nums_b = [_num_val(v) for v in bbox_val.arr[:4]]
        if all(nb is not None for nb in nums_b):
            media_box = nums_b  # type: ignore[assignment]

    stream_res_obj = _resolve_res_dict(obj.get('Resources'))

    # Also look for an owning Page via the backref cache
    backref_index = _backref_cache.get(upload_id, {})
    page_obj: Any = None
    for ref in backref_index.get(num, []):
        kp = ref['key_path']
        if kp == 'Contents' or kp.startswith('Contents.'):
            candidate = doc.resolve_num(ref['from_num'], ref['from_gen'])
            if candidate is not None:
                tp = candidate.get('Type')
                if tp.is_name() and tp.sval == 'Page':
                    page_obj = candidate
                    break
        else:
            # Handle streams inside a /Contents array: backref key_path is an array
            # index like "[0]". Walk one level up: if the parent is an Array, check
            # whether *that* array is referenced as /Contents of a Page.
            parent_obj = doc.resolve_num(ref['from_num'], ref['from_gen'])
            if parent_obj is not None and parent_obj.is_array():
                for arr_ref in backref_index.get(ref['from_num'], []):
                    arr_kp = arr_ref['key_path']
                    if arr_kp == 'Contents' or arr_kp.startswith('Contents.'):
                        candidate = doc.resolve_num(arr_ref['from_num'], arr_ref['from_gen'])
                        if candidate is not None:
                            tp = candidate.get('Type')
                            if tp.is_name() and tp.sval == 'Page':
                                page_obj = candidate
                                break
            if page_obj is not None:
                break

    crop_box: list[float] | None = None
    if page_obj is not None:
        if media_box is None:
            mb = page_obj.get('MediaBox')
            if mb.is_array() and len(mb.arr) >= 4:
                nums_m = [_num_val(v) for v in mb.arr[:4]]
                if all(nm is not None for nm in nums_m):
                    media_box = nums_m  # type: ignore[assignment]
        cb = page_obj.get('CropBox')
        if cb.is_array() and len(cb.arr) >= 4:
            nums_c = [_num_val(v) for v in cb.arr[:4]]
            if all(nc is not None for nc in nums_c):
                crop_box = nums_c  # type: ignore[assignment]

    # Collect resources from page (lower priority) then stream (higher priority)
    for res_obj in [_resolve_res_dict(page_obj.get('Resources')) if page_obj else None,
                    stream_res_obj]:
        if res_obj is None:
            continue
        xobj_obj = _resolve_res_dict(res_obj.get('XObject'))
        if xobj_obj is not None and xobj_obj.is_dict():
            for name, val in xobj_obj.dict.items():
                if val.is_ref():
                    xobj_resolved = doc.resolve_num(val.ref.num, val.ref.gen)
                    if xobj_resolved is not None:
                        subtype_obj = xobj_resolved.get('Subtype')
                        subtype = subtype_obj.sval if subtype_obj.is_name() else 'Unknown'
                        smask_num: int | None = None
                        smask_gen: int | None = None
                        smask_ref = xobj_resolved.get('SMask')
                        if smask_ref.is_ref():
                            smask_num = smask_ref.ref.num
                            smask_gen = smask_ref.ref.gen
                        resources['xobject'][name] = {
                            'num': val.ref.num,
                            'gen': val.ref.gen,
                            'subtype': subtype,
                            'smask_num': smask_num,
                            'smask_gen': smask_gen,
                        }
        font_obj = _resolve_res_dict(res_obj.get('Font'))
        if font_obj is not None and font_obj.is_dict():
            for name, val in font_obj.dict.items():
                if val.is_ref():
                    font_meta: dict[str, Any] = {'num': val.ref.num, 'gen': val.ref.gen,
                                                  'base_font': None, 'subtype': None,
                                                  'first_char': 0, 'last_char': 255,
                                                  'widths': None,
                                                  'font_file_num': None, 'font_file_gen': None,
                                                  'cid_to_gid_identity': False,
                                                  'cid_to_gid_num': None, 'cid_to_gid_gen': None}
                    font_res = doc.resolve_num(val.ref.num, val.ref.gen)
                    if font_res is not None and font_res.is_dict():
                        bf = font_res.get('BaseFont')
                        st = font_res.get('Subtype')
                        fc = font_res.get('FirstChar')
                        lc_obj = font_res.get('LastChar')
                        wo = font_res.get('Widths')
                        font_meta['base_font'] = bf.sval if bf.is_name() else None
                        font_meta['subtype']    = st.sval if st.is_name() else None
                        font_meta['first_char'] = int(fc.ival)     if fc.is_int() else 0
                        font_meta['last_char']  = int(lc_obj.ival) if lc_obj.is_int() else 255
                        if wo.is_array():
                            wlist: list[float] = []
                            for w in wo.arr:
                                if w.is_int(): wlist.append(float(w.ival))
                                elif w.type == PdfObjType.Real: wlist.append(w.dval)
                                else: wlist.append(0.0)
                            font_meta['widths'] = wlist
                        # Look for embedded font binary via FontDescriptor.
                        # Simple fonts (Type1, TrueType): FontDescriptor is on the font dict directly.
                        # Type0 (composite) fonts: FontDescriptor is on DescendantFonts[0] (the CIDFont).
                        def _find_font_file(fdict: 'PdfObject') -> None:
                            fd = fdict.get('FontDescriptor')
                            # FontDescriptor may be an indirect ref or an inline dict
                            fd_obj: 'PdfObject | None' = None
                            if fd.is_ref():
                                fd_obj = doc.resolve_num(fd.ref.num, fd.ref.gen)
                            elif fd.is_dict():
                                fd_obj = fd
                            if fd_obj is not None and fd_obj.is_dict():
                                for ff_key in ('FontFile2', 'FontFile3', 'FontFile'):
                                    ff_ref = fd_obj.get(ff_key)
                                    if ff_ref.is_ref():
                                        font_meta['font_file_num'] = ff_ref.ref.num
                                        font_meta['font_file_gen'] = ff_ref.ref.gen
                                        return

                        _find_font_file(font_res)
                        # For Type0 fonts, also try DescendantFonts[0]
                        if font_meta['font_file_num'] is None:
                            df = font_res.get('DescendantFonts')
                            if df.is_array() and df.arr:
                                df0 = df.arr[0]
                                if df0.is_ref():
                                    df0_obj = doc.resolve_num(df0.ref.num, df0.ref.gen)
                                    if df0_obj is not None and df0_obj.is_dict():
                                        _find_font_file(df0_obj)
                                elif df0.is_dict():
                                    _find_font_file(df0)
                        # CIDToGIDMap — DescendantFonts[0].CIDToGIDMap, for OpenType.js glyph rendering
                        _df = font_res.get('DescendantFonts')
                        if _df.is_array() and _df.arr:
                            _df0 = _df.arr[0]
                            _df0_obj: 'PdfObject | None' = None
                            if _df0.is_ref():
                                _df0_obj = doc.resolve_num(_df0.ref.num, _df0.ref.gen)
                            elif _df0.is_dict():
                                _df0_obj = _df0
                            if _df0_obj is not None and _df0_obj.is_dict():
                                _cgm = _df0_obj.get('CIDToGIDMap')
                                if _cgm.is_name() and _cgm.sval == 'Identity':
                                    font_meta['cid_to_gid_identity'] = True
                                elif _cgm.is_ref():
                                    font_meta['cid_to_gid_num'] = _cgm.ref.num
                                    font_meta['cid_to_gid_gen'] = _cgm.ref.gen
                    # Resolve /ToUnicode CMap → {char_code: unicode_char}
                    font_meta['cmap'] = None
                    tu_ref = font_res.get('ToUnicode') if font_res is not None else None
                    if tu_ref is not None and tu_ref.is_ref():
                        tu_obj = doc.resolve_num(tu_ref.ref.num, tu_ref.ref.gen)
                        if tu_obj is not None and tu_obj.type == PdfObjType.Stream:
                            font_meta['cmap'] = _parse_cmap_from_stream(tu_obj) or None
                    resources['font'][name] = font_meta

    result['resources'] = resources
    result['media_box'] = media_box
    result['crop_box'] = crop_box
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
        # Decode the lookup stream (may be FlateDecode-compressed)
        hival = int(parent_array.arr[2].ival) if parent_array.arr[2].is_int() else None
        raw = _decode_stream(obj) or obj.stream_raw
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
            raw = _decode_stream(lookup) or lookup.stream_raw
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


def _ensure_ttf_required_tables(data: bytes) -> bytes:
    """Inject minimal stub tables that opentype.js requires unconditionally.

    Embedded CIDFont subsets often omit the 'name' and 'cmap' tables.
    opentype.js calls uncompressTable(data, nameTableEntry) and accesses
    cmap.glyphIndexMap without guarding for their absence, so we add
    zero-entry stubs when they are missing.
    """
    import struct as _s
    if len(data) < 12:
        return data

    num_tables = _s.unpack_from(">H", data, 4)[0]
    # Guard: if the table directory extends beyond the data, the stream is truncated.
    # Return as-is; opentype.js will fail gracefully rather than the server crashing.
    if len(data) < 12 + num_tables * 16:
        return data

    existing = set()
    for i in range(num_tables):
        off = 12 + i * 16
        existing.add(data[off:off + 4])

    stubs: list[tuple[bytes, bytes]] = []

    # Minimal name table: format=0, count=0, stringOffset=6 (6 bytes)
    if b"name" not in existing:
        stubs.append((b"name", b"\x00\x00\x00\x00\x00\x06"))

    # Minimal cmap table: format-4 subtable with just the terminator segment
    # Header (4 B) + 1 encoding record (8 B) + format-4 subtable (24 B) = 36 bytes
    if b"cmap" not in existing:
        cmap_subtable = _s.pack(">HHHHHHHHHHHH",
            4,       # format
            24,      # length of subtable
            0,       # language
            2,       # segCountX2 (1 segment)
            2,       # searchRange
            0,       # entrySelector
            0,       # rangeShift
            0xFFFF,  # endCode[0]  — terminator segment
            0,       # reservedPad
            0xFFFF,  # startCode[0]
            1,       # idDelta[0]  — maps to glyph 0 (.notdef)
            0,       # idRangeOffset[0]
        )
        cmap_header = _s.pack(">HHHHI", 0, 1, 3, 1, 12)  # ver, numTables, winPlatID, unicodeBMP, offset
        stubs.append((b"cmap", cmap_header + cmap_subtable))

    # Minimal post table version 3.0 (no glyph name array).
    # opentype.js constructs GlyphNames(font.tables.post) and then accesses
    # font.glyphNames.names without guarding for a missing post table.
    if b"post" not in existing:
        stubs.append((b"post", _s.pack(">IIhhIIIII",
            0x00030000, 0, 0, 0, 0, 0, 0, 0, 0)))

    if not stubs:
        return data

    # Each stub adds one directory entry (16 B), shifting all existing offsets.
    shift = len(stubs) * 16
    new_num = num_tables + len(stubs)

    p = 1
    while p * 2 <= new_num:
        p *= 2
    search_range   = p * 16
    entry_selector = p.bit_length() - 1
    range_shift    = new_num * 16 - search_range

    # Shift existing table offsets
    dir_entries: list[tuple[bytes, int, int, int]] = []
    for i in range(num_tables):
        off = 12 + i * 16
        tag, chk, tbl_off, tbl_len = _s.unpack_from(">4sIII", data, off)
        dir_entries.append((tag, chk, tbl_off + shift, tbl_len))

    # Append stubs at the end, computing their checksum
    append_offset = len(data) + shift
    for tag_b, stub_bytes in stubs:
        padded = stub_bytes + b"\x00" * ((4 - len(stub_bytes) % 4) % 4)
        chk = sum(_s.unpack_from(">I", padded, j)[0] for j in range(0, len(padded), 4)) & 0xFFFFFFFF
        dir_entries.append((tag_b, chk, append_offset, len(stub_bytes)))
        append_offset += len(stub_bytes)

    dir_entries.sort(key=lambda e: e[0])

    header    = _s.pack(">4sHHHH", data[:4], new_num, search_range, entry_selector, range_shift)
    directory = b"".join(_s.pack(">4sIII", tag, chk, off, ln) for tag, chk, off, ln in dir_entries)
    old_data_start = 12 + num_tables * 16
    stub_data = b"".join(b for _, b in stubs)
    return header + directory + data[old_data_start:] + stub_data


@app.get("/api/raw_stream/{upload_id}/{num}/{gen}")
def get_raw_stream(upload_id: str, num: int, gen: int) -> Response:
    """Return the decoded bytes of any stream object (e.g. CIDToGIDMap)."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")
    data = _decode_stream(obj)
    if data is None:
        raise HTTPException(422, "Cannot decode stream")
    return Response(content=data, media_type="application/octet-stream")


@app.get("/api/ttf_raw/{upload_id}/{num}/{gen}")
def get_ttf_raw(upload_id: str, num: int, gen: int) -> Response:
    """Return the decoded TrueType/OTF font bytes for client-side rendering."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")
    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")
    data = _decode_stream(obj)
    if data is None:
        raise HTTPException(422, "Cannot decode font stream")
    data = _ensure_ttf_required_tables(data)
    return Response(content=data, media_type="application/octet-stream")


# ── CCITT orientation helpers ────────────────────────────────────────────────

def _decode_single_stream(obj: Any) -> bytes | None:
    """Decode a single stream object (FlateDecode or raw)."""
    from pdf.filters import flat_decode
    fobj = obj.get("Filter")
    if fobj.is_name() and fobj.sval == "FlateDecode":
        try:
            return flat_decode(obj.stream_raw)
        except Exception:
            return None
    if fobj.is_null():
        return obj.stream_raw
    return None


def _get_decoded_content_bytes(doc: PdfDocument, contents_obj: Any) -> bytes | None:
    """Decode a page Contents value (direct stream or array of stream refs) to bytes."""
    if contents_obj.type == PdfObjType.Reference:
        contents_obj = doc.resolve_num(contents_obj.ref.num, contents_obj.ref.gen)
    if contents_obj is None:
        return None
    if contents_obj.is_array():
        chunks: list[bytes] = []
        for item in contents_obj.arr:
            if item.type == PdfObjType.Reference:
                ref_obj = doc.resolve_num(item.ref.num, item.ref.gen)
                if ref_obj and ref_obj.type == PdfObjType.Stream:
                    chunk = _decode_single_stream(ref_obj)
                    if chunk is not None:
                        chunks.append(chunk)
        return b"\n".join(chunks) if chunks else None
    if contents_obj.type == PdfObjType.Stream:
        return _decode_single_stream(contents_obj)
    return None


def _scan_cm_d_for_image(stream_bytes: bytes, img_name: str) -> float | None:
    """Token-scan a decoded content stream for the cm.d operand just before Do /img_name."""
    try:
        tokens = stream_bytes.decode("latin-1").split()
    except Exception:
        return None
    last_cm_d: float | None = None
    for i, tok in enumerate(tokens):
        if tok == "cm" and i >= 6:
            try:
                # Operand order: a b c d e f cm → d is at index i-3
                last_cm_d = float(tokens[i - 3])
            except (ValueError, IndexError):
                pass
        elif tok == "Do" and i >= 1:
            name = tokens[i - 1].lstrip("/")
            if name == img_name:
                return last_cm_d
    return None


def _find_image_cm_d(session_id: str, doc: PdfDocument, obj_num: int) -> float | None:
    """Return the cm.d value used to place this image in its page content stream."""
    cache = _backref_cache.get(session_id)
    if cache is None:
        return None
    for ref in cache.get(obj_num, []):
        key_path = ref.get("key_path", "")
        parts = key_path.split(".")
        # key_path can be "Resources.XObject.ImN" (Resources inline in Page)
        # or "XObject.ImN" (Resources is a separate indirect object)
        if len(parts) < 2 or parts[-2] != "XObject":
            continue
        img_name = parts[-1]
        from_num = ref["from_num"]
        from_gen = ref.get("from_gen", 0)

        # The container may be the Page itself (if Resources is inline)
        # or the Resources dict (if Resources is an indirect object).
        # Try the container first; if no Contents, walk up one level.
        candidates = [doc.resolve_num(from_num, from_gen)]
        for parent_ref in cache.get(from_num, []):
            candidates.append(doc.resolve_num(parent_ref["from_num"], parent_ref.get("from_gen", 0)))

        for container in candidates:
            if container is None:
                continue
            contents = container.get("Contents")
            if contents.is_null():
                continue
            stream_bytes = _get_decoded_content_bytes(doc, contents)
            if not stream_bytes:
                continue
            cm_d = _scan_cm_d_for_image(stream_bytes, img_name)
            if cm_d is not None:
                return cm_d
    return None


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

    # For CCITT images stored bottom-to-top (signalled by negative cm.d placement),
    # the ImagePane <img> tag needs a CSS scaleY(-1) to display correctly.
    display_y_flip = False
    if filter_name == "CCITTFaxDecode":
        cm_d = _find_image_cm_d(upload_id, doc, num)
        if cm_d is not None and cm_d < 0:
            display_y_flip = True

    im_obj = obj.get("ImageMask")
    is_image_mask = im_obj.type.name == "Boolean" and im_obj.bval

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
        "display_y_flip": display_y_flip,
        "is_image_mask": is_image_mask,
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
        img_l = img.convert("L")

        # ImageMask=true: stencil mask — black bits paint, white bits are transparent.
        # Serve as RGBA PNG so white pixels are transparent and underlying graphics show through.
        im_obj = obj.get("ImageMask")
        if im_obj.type.name == "Boolean" and im_obj.bval:
            alpha = img_l.point(lambda p: 0 if p > 128 else 255)
            black = Image.new("L", (width, height), 0)
            img_rgba = Image.merge("RGBA", (black, black, black, alpha))
            buf = io.BytesIO()
            img_rgba.save(buf, format="PNG")
            return buf.getvalue()

        raw_pixels = img_l.tobytes()
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
# Serve /static/ directory (downloads, misc files)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="downloads")

# Directory listing for /store
@app.get("/store", response_class=HTMLResponse, include_in_schema=False)
def list_store():
    files = sorted(p.name for p in _STORE_DIR.iterdir() if p.suffix.lower() == ".pdf")
    items = "".join(
        f'<li><a href="/store/{name}" target="_blank">{name}</a></li>\n'
        for name in files
    )
    html = (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<title>PDF Store</title>"
        "<style>body{font-family:sans-serif;padding:2rem}li{margin:.4rem 0}a{font-size:1rem}</style>"
        "</head><body>"
        f"<h1>PDF Store ({len(files)} files)</h1><ul>\n{items}</ul>"
        "</body></html>"
    )
    return HTMLResponse(content=html)

# Serve /store/ directory for direct PDF downloads
app.mount("/store", StaticFiles(directory=str(_STORE_DIR)), name="store")

# ---------------------------------------------------------------------------
# Serve the built React frontend from /  (must be mounted last)
# ---------------------------------------------------------------------------
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
