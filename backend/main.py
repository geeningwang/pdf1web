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
    is_content_stream = False
    is_palette = False
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
                if decoded and not is_icc_profile and not is_image:
                    type_obj = obj.get("Type")
                    not_special = not (type_obj.is_name() and type_obj.sval in ('ObjStm', 'XRef'))
                    if not_special and not _is_binary(decoded) and _is_content_stream_data(decoded):
                        is_content_stream = True
            # Detect indexed color palette: check if this object is referenced as
            # an Indexed CS lookup stream elsewhere in the document (reliable).
            if not is_image and not is_icc_profile and not is_content_stream:
                if obj.type == PdfObjType.Stream and doc.is_palette_lookup(num):
                    is_palette = True

    return {
        "detail": detail,
        "is_image": is_image,
        "is_icc_profile": is_icc_profile,
        "is_content_stream": is_content_stream,
        "is_palette": is_palette,
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
    """Return palette entries for an Indexed color space lookup stream."""
    doc = _sessions.get(upload_id)
    if doc is None:
        raise HTTPException(404, "Session not found")

    obj = doc.resolve_num(num, gen)
    if obj is None or obj.type != PdfObjType.Stream:
        raise HTTPException(404, "Object not found or not a stream")

    raw = obj.stream_raw.rstrip(b'\x00\x09\x0a\x0c\x0d\x20')
    if len(raw) == 0 or len(raw) % 3 != 0:
        raise HTTPException(422, "Not a valid RGB palette stream")

    entries = []
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        # Compute luminance to decide label text color
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
        "raw_size": len(obj.stream_raw),
        "entries": entries,
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
