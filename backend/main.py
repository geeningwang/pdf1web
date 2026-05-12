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

from pdf.document import PdfDocument, _decode_stream, _detail
from pdf.icc import parse_icc_profile
from pdf.jpeg import parse_jpeg
from pdf.ccitt import parse_ccitt
from pdf.objects import PdfObjType
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
_MAX_SESSIONS = 20  # evict oldest if exceeded

# Directory where uploaded PDFs and their analysis logs are persisted
_UPLOADS_DIR = Path(__file__).parent / "uploads"
_UPLOADS_DIR.mkdir(exist_ok=True)

# Serve built frontend static files if the dist folder exists
_DIST = Path(__file__).parent.parent / "frontend" / "dist"


def _make_id() -> str:
    return str(uuid.uuid4())


def _evict_if_needed() -> None:
    if len(_sessions) >= _MAX_SESSIONS:
        oldest = next(iter(_sessions))
        del _sessions[oldest]


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
    image_filter: str | None = None
    if obj and (obj.is_dict() or obj.type == PdfObjType.Stream):
        st = obj.get("Subtype")
        is_image = st.is_name() and st.sval == "Image"
        if obj.type == PdfObjType.Stream:
            from pdf.filters import flat_decode
            fobj = obj.get("Filter")
            if fobj.is_name():
                image_filter = fobj.sval if is_image else None
            if fobj.is_name() and fobj.sval == "FlateDecode":
                decoded = flat_decode(obj.stream_raw)
                if decoded and len(decoded) >= 40 and decoded[36:40] == b'acsp':
                    is_icc_profile = True

    return {
        "detail": detail,
        "is_image": is_image,
        "is_icc_profile": is_icc_profile,
        "image_filter": image_filter,
        "obj_num": num,
        "gen_num": gen,
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

    cs = cs_obj.sval if cs_obj.is_name() else ""
    # Resolve indirect ColorSpace reference if needed
    if cs_obj.is_array() and cs_obj.arr and cs_obj.arr[0].is_name():
        cs = cs_obj.arr[0].sval

    channels = 3 if cs in ("DeviceRGB", "RGB") else 1  # grayscale default

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
