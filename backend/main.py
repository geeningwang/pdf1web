"""
FastAPI backend for pdf1web — PDF Structure Analyzer Web App.
"""
from __future__ import annotations

import io
import os
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from pdf.document import PdfDocument, _decode_stream
from pdf.objects import PdfObjType

app = FastAPI(title="pdf1web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store: upload_id -> PdfDocument
_sessions: dict[str, PdfDocument] = {}
_MAX_SESSIONS = 20  # evict oldest if exceeded

# Serve built frontend static files if the dist folder exists
_DIST = Path(__file__).parent.parent / "frontend" / "dist"


def _make_id() -> str:
    return str(uuid.uuid4())


def _evict_if_needed() -> None:
    if len(_sessions) >= _MAX_SESSIONS:
        oldest = next(iter(_sessions))
        del _sessions[oldest]


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

    root = doc.root()
    return {
        "id": upload_id,
        "version": doc.version(),
        "filename": file.filename,
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
    if obj and obj.is_dict():
        st = obj.get("Subtype")
        is_image = st.is_name() and st.sval == "Image"

    return {"detail": detail, "is_image": is_image, "obj_num": num, "gen_num": gen}


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

    cs = cs_obj.sval if cs_obj.is_name() else ""
    # Resolve indirect ColorSpace reference if needed
    if cs_obj.is_array() and cs_obj.arr and cs_obj.arr[0].is_name():
        cs = cs_obj.arr[0].sval

    channels = 3 if cs in ("DeviceRGB", "RGB") else 1  # grayscale default

    try:
        png_data = _raw_to_png(decoded, width, height, channels, bpc)
    except Exception as exc:
        raise HTTPException(422, f"Cannot convert to PNG: {exc}") from exc

    return Response(content=png_data, media_type="image/png")


# ---------------------------------------------------------------------------
# Minimal PNG encoder (no Pillow required)
# ---------------------------------------------------------------------------

def _raw_to_png(
    raw: bytes, width: int, height: int, channels: int, bpc: int
) -> bytes:
    """Encode raw pixel data as a PNG byte string."""
    bit_depth = min(bpc, 8)
    color_type = 2 if channels == 3 else 0  # 2=RGB, 0=grayscale

    # Build IDAT raw data (add filter byte 0x00 per scanline)
    bytes_per_row = width * channels * (bit_depth // 8)
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
