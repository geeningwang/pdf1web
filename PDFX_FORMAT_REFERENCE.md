# PDFX Format Reference

**Version**: 1.1  
**Date**: May 2026  
**Status**: Normative — matches `exporter.py` and `linker.py` as of 360-PDF stress test (278/278 non-linearized binary-exact, 0 errors)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Directory Layout](#2-directory-layout)
3. [Artifact Reference](#3-artifact-reference)
   - 3.1 [pdfx_manifest.json](#31-pdfx_manifestjson)
   - 3.2 [header.txt](#32-headertxt)
   - 3.3 [xref_raw.bin](#33-xref_rawbin)
   - 3.4 [eof_tail.bin](#34-eof_tailbin)
   - 3.5 [xref.txt](#35-xreftxt)
   - 3.6 [trailer.pdfjson](#36-trailerpdfJson)
   - 3.7 [objects/obj_NNNNN_G.pdfjson](#37-objectsobj_nnnnn_gpdfjson)
   - 3.8 [objects/obj_NNNNN_G.pdfo](#38-objectsobj_nnnnn_gpdfo)
   - 3.9 [objects/obj_NNNNN_G.pdfs](#39-objectsobj_nnnnn_gpdfs)
   - 3.10 [resources/](#310-resources)
4. [JSON Value Encoding](#4-json-value-encoding)
5. [Exporter Algorithm](#5-exporter-algorithm)
6. [Linker Algorithm](#6-linker-algorithm)
7. [xref Handling](#7-xref-handling)
8. [ObjStm (Compressed Object Streams)](#8-objstm-compressed-object-streams)
9. [Edge Cases](#9-edge-cases)
10. [Binary-Exact Guarantee](#10-binary-exact-guarantee)

---

## 1. Overview

PDFX is an exploded directory format for PDF files. Each indirect object in the PDF becomes a set of files in the `objects/` subdirectory. The format has three layers per object, mirroring the C compiler pipeline:

| Layer | File suffix | Analogy | Purpose |
|---|---|---|---|
| Source | `.pdfjson` | `.c` source | AI-editable JSON dictionary of the object's structure |
| Stream | `.pdfs` | `.s` assembly | Decoded stream body (text streams only), AI-editable |
| Object | `.pdfo` | `.o` object file | Verbatim bytes from the original PDF, ready to link |

The **exporter** (`exporter.py`) reads a PDF and produces a PDFX directory. The **linker** (`linker.py`) reads a PDFX directory and produces a PDF. For unmodified objects the linker writes `.pdfo` verbatim, guaranteeing byte-exact output at the original byte offset.

**Binary-exact scope**:
- ✓ All non-linearized PDFs (table-xref and stream-xref)
- ✗ Linearized PDFs — valid de-linearized output (not byte-identical)
- ✗ Encrypted PDFs — not supported

---

## 2. Directory Layout

```
<stem>.pdfx/
│
├── pdfx_manifest.json       REQUIRED — root index, sha256 checksums, object list
│
├── header.txt               REQUIRED — escaped-line encoding of verbatim pre-first-object bytes
│
├── xref_raw.bin             table-xref, non-linearized PDFs only

│
├── eof_tail.bin             stream-xref PDFs only
│                            verbatim bytes from 'startxref' keyword to EOF
│
├── xref.txt                 informational only — not read by linker
├── trailer.pdfjson          trailer dictionary (JSON)
│
├── objects/
│   ├── obj_NNNNN_G.pdfjson  JSON source dict for each InUse or ObjStm-hosted object
│   ├── obj_NNNNN_G.pdfo     verbatim bytes for each InUse object
│   └── obj_NNNNN_G.pdfs     decoded text stream (text-stream objects only)
│
└── resources/
    ├── font_NNNNN_G.{ttf,otf,pfb,cff,bin}
    ├── image_NNNNN_G.{jpg,jp2,jbig2,bin}
    ├── icc_NNNNN_G.icc
    ├── attachment_NNNNN_G.<ext>
    ├── signature_NNNNN_G.sig
    ├── 3d_NNNNN_G.{u3d,prc,bin}
    └── sound_NNNNN_G.bin
```

**Naming convention**: `obj_{NNNNN}_{G}` — object number zero-padded to 5 digits, underscore, generation number. Example: object 5 generation 0 → `obj_00005_0`.

---

## 3. Artifact Reference

### 3.1 `pdfx_manifest.json`

The root index. Written last by the exporter after all object files are complete.

#### Top-level fields

| Field | Type | Description |
|---|---|---|
| `pdfx_version` | string | Always `"1.0"` |
| `source_filename` | string | Original PDF filename |
| `source_sha256` | string | SHA-256 hex digest of the original PDF bytes |
| `pdf_version` | string | PDF version string, e.g. `"1.7"` |
| `pdf_size_bytes` | int | Original PDF file size in bytes |
| `object_count` | int | Total number of xref entries (InUse + Compressed + Free) |
| `xref_type` | string | `"table"` or `"stream"` |
| `linearized` | bool | `true` if the PDF is linearized (linker uses modified path) |
| `encrypted` | bool | `true` if the PDF has an `/Encrypt` dict (currently always false) |
| `trailer` | object | Subset of trailer dict: `{Size, Root, Info}` |
| `startxref` | int | Byte offset of the xref section in the original PDF |
| `objects` | array | One entry per exported object (see below) |

#### `xref_type` detection

`xref_type` is determined by inspecting `data[startxref_val : startxref_val+10]`:
- Starts with `xref` → `"table"`
- Otherwise → `"stream"` (an indirect object is at that offset)

This is more reliable than checking for ObjStm compressed entries, since a stream-xref PDF may contain no ObjStm objects.

#### Per-object entry in `objects`

Each entry in `objects` describes one exported object. Objects are sorted by `(num, gen)` in the manifest.

| Field | Type | Description |
|---|---|---|
| `num` | int | Object number |
| `gen` | int | Generation number (almost always 0) |
| `byte_offset` | int | Byte offset of `N G obj` in the original PDF |
| `byte_length` | int | Byte count of this object's `.pdfo` file (extends to next object boundary; includes gap bytes) |
| `obj_sha256` | string | SHA-256 of the `.pdfo` file — linker verifies before linking |
| `src_sha256` | string | SHA-256 of `.pdfjson` at export time — linker compares to detect edits |
| `pdfs_sha256` | string\|null | SHA-256 of `.pdfs` at export time; `null` for non-text-stream objects |
| `type` | string | `"dict"`, `"stream"`, `"array"`, `"int"`, `"real"`, `"bool"`, `"string"`, `"name"`, `"null"` |
| `pdf_type` | string\|null | Value of `/Type` name, e.g. `"Page"`, `"Font"`, `"XObject"` |
| `pdf_subtype` | string\|null | Value of `/Subtype` name |
| `has_stream` | bool | Whether the object has a stream body |
| `stream_encoding` | array\|null | Filter chain list, e.g. `["FlateDecode"]`, or `null` |
| `stream_length` | int\|null | Value of `/Length` in the original object dict |
| `stream_type` | string\|null | `"text"`, `"binary"`, or `null` |
| `resource_file` | string\|null | Relative path to the extracted resource file, e.g. `"resources/font_00066_0.ttf"` |
| `is_signature` | bool | `true` for `/Type /Sig` digital signature objects |
| `in_objstm` | bool | `true` if this object is hosted inside an ObjStm |
| `objstm_host` | int\|null | Object number of the ObjStm host (when `in_objstm` is true) |
| `objstm_index` | int\|null | Position index within the ObjStm (0-based, when `in_objstm` is true) |

**Modification detection**: The linker computes `sha256(current .pdfjson)` and `sha256(current .pdfs)` and compares them to `src_sha256` / `pdfs_sha256`. If either differs, the object is considered modified and is re-serialized from source. Signature objects (`is_signature: true`) always use the `.pdfo` path regardless of checksum.

#### Example

```json
{
  "pdfx_version": "1.0",
  "source_filename": "report.pdf",
  "source_sha256": "a3f12c...",
  "pdf_version": "1.7",
  "pdf_size_bytes": 102400,
  "object_count": 88,
  "xref_type": "table",
  "linearized": false,
  "encrypted": false,
  "trailer": { "Size": 88, "Root": "1 0 R", "Info": "2 0 R" },
  "startxref": 98123,
  "objects": [
    {
      "num": 5, "gen": 0,
      "byte_offset": 1024, "byte_length": 312,
      "obj_sha256": "b2c3...", "src_sha256": "d4e5...", "pdfs_sha256": null,
      "type": "dict", "pdf_type": "Font", "pdf_subtype": "Type1",
      "has_stream": false, "stream_encoding": null, "stream_length": null,
      "stream_type": null, "resource_file": null, "is_signature": false,
      "in_objstm": false, "objstm_host": null, "objstm_index": null
    },
    {
      "num": 45, "gen": 0,
      "byte_offset": 44100, "byte_length": 1960,
      "obj_sha256": "f9a1...", "src_sha256": "c3b2...", "pdfs_sha256": "e7d8...",
      "type": "stream", "pdf_type": null, "pdf_subtype": null,
      "has_stream": true, "stream_encoding": ["FlateDecode"], "stream_length": 1842,
      "stream_type": "text", "resource_file": null, "is_signature": false,
      "in_objstm": false, "objstm_host": null, "objstm_index": null
    }
  ]
}
```

---

### 3.2 `header.txt`

**Verbatim bytes from byte 0 of the original PDF up to (not including) the first valid object, encoded as a single escaped line.**

The file contains exactly one line of escaped text followed by a single LF file-terminator.

#### Encoding rules

| Byte | Encoded as |
|---|---|
| Printable ASCII `0x20–0x7E` (except `\`) | literal character |
| `0x0D` (CR) | `\r` |
| `0x0A` (LF) | `\n` |
| `0x5C` (backslash) | `\\` |
| All other bytes | `\xNN` (two lowercase hex digits) |

#### Examples

**Standard LF PDF with 4-byte binary comment:**
```
%PDF-1.7\n%\xe2\xe3\xcf\xd3\n
```

**CRLF PDF with 4-byte binary comment:**
```
%PDF-1.7\r\n%\xa1\xb3\xc5\xd7\r\n
```

**PDF with gap byte after binary comment (double LF):**
```
%PDF-1.7\n%\x81\x81\x81\x81\n\n
```

**PDF with single-byte binary comment:**
```
%PDF-1.5\n%\x8f\n
```

**PDF with space-prefixed binary comment:**
```
%PDF-1.4\n% \xe2\xe3\xcf\xd3\n
```

**PDF with CRLF and no binary comment:**
```
%PDF-1.4\r\n
```

(The trailing `\n` at the end of the file is the file-terminator, not part of the header data.)

#### First-valid-object boundary

The exporter computes `_header_end` by parsing the `%PDF-X.Y` version line and optional binary comment from `data[0:]`. Any InUse xref entry with `offset < _header_end` is skipped as corrupt (e.g. ghost entries at offset=0). The `header.txt` content covers `data[0 : _first_obj_offset]` where `_first_obj_offset = min(offset for valid InUse objects)`.

#### Linker decoding

The linker reads `header.txt`, strips the trailing file-level `\n`, then decodes the escape sequences:
- `\r` → byte `0x0D`
- `\n` → byte `0x0A`
- `\\` → byte `0x5C`
- `\xNN` → byte with hex value `NN`
- Any other character → `latin-1` encode

The result is written verbatim to the beginning of the output PDF.

---

### 3.3 `xref_raw.bin`

**Present only for**: table-xref (`"xref_type": "table"`), non-linearized (`"linearized": false`) PDFs.

**Content**: verbatim bytes from `startxref_val` to the end of the file.

```
xref\r\n
0 88\r\n
0000000000 65535 f \r\n
0000000009 00000 n \r\n
...
trailer\r\n
<<\r\n
/Size 88\r\n
/Root 1 0 R\r\n
>>\r\n
startxref\r\n
98123\r\n
%%EOF\r\n
```

**Linker behavior (unmodified path)**: written verbatim without any modification. Since all objects are at their original offsets, `xref_pos == startxref_val` always, so the offset already in `xref_raw.bin` is correct. This preserves exact EOL style (CRLF vs LF) and any trailing bytes after `%%EOF`.

**Linker behavior (modified/linearized path)**: `xref_raw.bin` is ignored; a fresh xref table is built from measured object offsets using `_build_xref_table()`.

---

### 3.5 `eof_tail.bin`

**Present only for**: stream-xref (`"xref_type": "stream"`) PDFs where `scan_backward` found `startxref`.

**Content**: verbatim bytes from the `startxref` keyword to the end of the file.

```
startxref\n
171722\n
%%EOF\n
```

**Why needed**: The xref stream object's `.pdfo` extends to the `startxref` keyword position (i.e., its boundary is `_sx_offset`, not the end of file). The `startxref...%%EOF` tail must be preserved verbatim to maintain EOL style. On the unmodified path the linker:

1. Finds the original xref offset number in `eof_tail.bin` (by scanning for digits after `startxref\n`)
2. Replaces only that number with the measured `xref_pos`
3. Writes the result

Since no objects were modified, `xref_pos == original startxref_val`, so the bytes are unchanged.

**Linker behavior (modified path)**: if `eof_tail.bin` exists, uses the same replacement logic. If absent, falls back to `f"startxref\n{xref_pos}\n%%EOF\n".encode()`.

---

### 3.6 `xref.txt`

**Informational only — not read by the linker.**

Human-readable dump of the original xref table.

```
%% PDFX Cross-reference table (informational)
%% xref_type: table
%% object_count: 88
%%
%% num    gen   offset      status
  0       65535 0           free
  1       0     9           in-use
  2       0     58          in-use
  5       0     1024        in-use
  ...
```

For ObjStm-hosted objects, the offset column shows `(objstm:NNN)` where NNN is the host object number.

---

### 3.7 `trailer.pdfjson`

The PDF trailer dictionary serialized as JSON using the standard PDFX JSON type conventions (see §4).

```json
{
  "Size": 88,
  "Root": "1 0 R",
  "Info": "2 0 R",
  "ID": [{"$hex": "a3f1..."}, {"$hex": "a3f1..."}]
}
```

The linker reads this and incorporates it into the rebuilt xref table or xref stream trailer.

---

### 3.8 `objects/obj_NNNNN_G.pdfjson`

The AI-editable source file for each object. Written for all InUse objects and all ObjStm-hosted objects.

Keys prefixed with `_` are PDFX metadata. The linker strips all `_`-prefixed keys before serializing the PDF object bytes.

#### PDFX metadata keys

| Key | Description |
|---|---|
| `_obj` | `"N G"` — object number and generation |
| `_type` | Human-readable type hint, e.g. `"Page"`, `"Font / TrueType"` |
| `_stream` | `"text"` \| `"binary"` \| `"none"` |
| `_stream_file` | Relative path to `.pdfs` decoded stream (text streams) |
| `_resource` | Relative path to binary resource file (binary streams) |
| `_stream_encoding` | Original filter chain, e.g. `["FlateDecode"]` |

#### Example — dict object (no stream)

```json
{
  "_obj": "5 0",
  "_type": "Font / Type1",
  "_stream": "none",
  "Type": "/Font",
  "Subtype": "/Type1",
  "BaseFont": "/Helvetica-Bold",
  "Encoding": "/WinAnsiEncoding",
  "FirstChar": 32,
  "LastChar": 255,
  "Widths": [278, 278, 355, 556, 556, 889],
  "FontDescriptor": "6 0 R"
}
```

#### Example — text stream object (page content stream)

```json
{
  "_obj": "45 0",
  "_type": "Content stream",
  "_stream": "text",
  "_stream_file": "obj_00045_0.pdfs",
  "_stream_encoding": ["FlateDecode"],
  "Length": 1842,
  "Filter": "/FlateDecode"
}
```

#### Example — binary stream object (font program)

```json
{
  "_obj": "66 0",
  "_type": "FontFile2 (TrueType)",
  "_stream": "binary",
  "_resource": "resources/font_00066_0.ttf",
  "_stream_encoding": ["FlateDecode"],
  "Length": 18100,
  "Filter": "/FlateDecode",
  "Length1": 44728
}
```

#### Modification rules

- Edit any PDF dict value. The linker re-serializes the dict to PDF syntax.
- Leave `/Length` as-is — the linker recomputes it after re-encoding the stream.
- Do not change `_obj`, `_stream`, `_stream_file`, or `_resource` — these are linker directives.
- Do not modify `_stream_encoding` unless you intend to change the compression filter chain.

---

### 3.9 `objects/obj_NNNNN_G.pdfo`

**Verbatim bytes from the original PDF for each InUse object.**

**Byte range**: from the object's `byte_offset` (`N G obj` token) to the next valid object's offset, or:
- For the last regular object in a table-xref PDF: up to `startxref_val`
- For the xref stream object in a stream-xref PDF: up to `_sx_offset` (the `startxref` keyword position)

This range **includes inter-object gap bytes** (whitespace, newlines, comments) between the end of `endobj` and the start of the next object. Including gap bytes is essential: when the linker writes `.pdfo` files sequentially, each one must land at the original offset for the xref to match.

`byte_length` in the manifest reflects this extended range, not just `N G obj...endobj\n`.

**Not written** for ObjStm-hosted objects (compressed entries) — those objects have no independent byte offset in the PDF.

---

### 3.10 `objects/obj_NNNNN_G.pdfs`

**Decoded stream content for text-stream objects.**

Written when `stream_type == "text"` — i.e., the decoded stream is valid UTF-8 text. This includes:
- Page content streams (PDF operators)
- ToUnicode CMaps
- XMP metadata (`application/rdf+xml`)
- JavaScript actions
- PostScript XObjects

The file contains raw decoded bytes — no BOM, no added headers. Line endings are preserved exactly as decoded from the PDF stream.

**Linker behavior**: re-encodes the `.pdfs` bytes using `_stream_encoding` filter chain from `.pdfjson`, then writes as the stream payload. Updates `/Length` in the object dict automatically.

**Modification**: edit PDF content operators, text strings, or XML metadata directly. The linker re-encodes after modification.

---

### 3.11 `resources/`

Binary stream content, stored decoded (filters stripped).

| Tag | Extensions | PDF stream type |
|---|---|---|
| `font` | `.ttf`, `.otf`, `.pfb`, `.cff`, `.bin` | TrueType, OpenType, Type 1, CFF font programs |
| `image` | `.jpg`, `.jp2`, `.jbig2`, `.bin` | Image XObjects (DCTDecode → .jpg preserved as-is) |
| `icc` | `.icc` | ICCBased color profile streams |
| `attachment` | `.<ext>` (from /Subtype), `.bin` | EmbeddedFile streams |
| `signature` | `.sig` | `/Type /Sig` byte-range PKCS#7 blobs |
| `3d` | `.u3d`, `.prc`, `.bin` | `/Type /3D` 3D model streams |
| `sound` | `.bin` | `/Type /Sound` audio streams |

**Special case — JPEG images**: DCTDecode stream bytes are the JPEG data verbatim. Stored as `.jpg` without re-encoding. The linker re-applies DCTDecode encoding (which is a no-op since the bytes are already JPEG-compressed).

**Special case — signatures**: Always linked from `.pdfo`, never re-serialized. The `is_signature: true` flag causes the linker to force the unmodified path regardless of checksum comparison.

---

## 4. JSON Value Encoding

All `.pdfjson` files use these conventions for PDF value types:

| PDF type | JSON representation | Example |
|---|---|---|
| Name | string prefixed with `/` | `"/Font"` |
| Integer | JSON number (no decimal) | `12` |
| Real | JSON number (with decimal or float) | `1.5`, `0.0` |
| Boolean | JSON boolean | `true`, `false` |
| Null | JSON null | `null` |
| Literal string | plain JSON string | `"Hello, World"` |
| Hex string | `{"$hex": "<hex digits>"}` | `{"$hex": "a3f1b200"}` |
| Indirect reference | JSON string matching `N G R` | `"6 0 R"` |
| Array | JSON array | `[278, 556, 889]` |
| Dictionary | JSON object | `{"Type": "/Font"}` |

**Name normalization**: PDF names use `#NN` escape sequences for non-regular characters. The `.pdfjson` representation stores the decoded name with the leading `/`. The linker re-encodes non-regular characters when serializing to PDF bytes.

**Real numbers**: The linker uses the shortest exact decimal representation (no unnecessary trailing zeros, no scientific notation) to minimize PDF size changes on re-serialization.

---

## 5. Exporter Algorithm

```
export_pdf(pdf_path, output_dir):

1. Parse PDF
   - Read raw bytes
   - Parse xref (table or stream) to get all xref entries
   - Resolve trailer dict
   - Separate InUse, Free, and Compressed (ObjStm-hosted) entries

2. Pre-computation (before main object loop)
   a. Scan backward from EOF for last 'startxref' keyword → _sx_offset, _startxref_val
   b. Detect linearized: any(xe.offset > _startxref_val for xe in in_use.values())
   c. Detect xref type: bytes at _startxref_val → "table" if starts with 'xref', else "stream"
   d. For stream-xref: find which InUse object has offset == _startxref_val → _xref_stream_num
   e. Compute _header_end: parse '%PDF-X.Y' + optional binary comment line from data[0:]
   f. Build _valid_sorted_inuse: InUse objects sorted by offset, filtered to xe.offset >= _header_end
   g. Compute _first_obj_offset = _valid_sorted_inuse[0].offset (or _header_end if empty)
   h. Compute _next_boundaries dict (maps object num → first byte after its pdfo zone):
      - For non-linearized, regular objects: boundary = next object's offset
      - For non-linearized, last regular object (table-xref): boundary = _startxref_val
      - For xref stream object: boundary = _sx_offset
      - For linearized PDFs: _next_boundaries is empty (fall back to _get_raw_object_bytes)

3. Write header.txt: encode data[0 : _first_obj_offset] as single escaped line
   - CR (0x0D) → \r
   - LF (0x0A) → \n
   - Backslash (0x5C) → \\
   - Printable ASCII (0x20-0x7E) → literal
   - All other bytes → \xNN
   - Append one LF as file-terminator

4. For each InUse object (sorted by offset):
   a. Skip if xe.offset < _header_end (corrupt xref entry)
   b. Resolve object: doc.resolve_num(num, gen)
   c. Compute pdfo bytes:
      - If _next_boundaries has boundary for this num: data[xe.offset : boundary]
      - Else: _get_raw_object_bytes(data, xe.offset, obj)
   d. Write objects/obj_NNNNN_G.pdfo
   e. Build pdfjson dict (JSON-encoded object dict with _* metadata keys)
   f. If has_stream:
      - Classify stream (text vs binary) → stream_type, resource_ext
      - Decode stream (apply filter chain in reverse)
      - If text: write objects/obj_NNNNN_G.pdfs
      - If binary: write resources/<tag>_NNNNN_G.<ext>
   g. Write objects/obj_NNNNN_G.pdfjson
   h. Append entry to manifest_objects list

5. For each Compressed (ObjStm-hosted) object:
   a. Resolve object
   b. Build pdfjson dict
   c. Write objects/obj_NNNNN_G.pdfjson  (no .pdfo for compressed objects)
   d. Append entry to manifest_objects (in_objstm=true, objstm_host, objstm_index)

6. Write trailer.pdfjson

7. Write xref.txt (informational)

8. Write header.txt: encode data[0 : _first_obj_offset] as single escaped line
   (CR→\r, LF→\n, backslash→\\, printable ASCII literal, other→\xNN; append LF file-terminator)

9. Write xref_raw.bin = data[_startxref_val :]
   (only if xref_type=="table" and not linearized and startxref_val > 0)

10. Write eof_tail.bin = data[_sx_offset :]
    (only if xref_type=="stream" and _sx_offset >= 0)

11. Write pdfx_manifest.json
```

---

## 6. Linker Algorithm

```
link_pdf(pdfx_dir, output_path):

1. Read pdfx_manifest.json
   - xref_type, is_linearized, orig_startxref
   - Split objects into inuse_objs (not in_objstm) and compressed_objs (in_objstm)
   - Sort inuse_objs by byte_offset
   - Find xref_obj_num: object entry whose pdf_type == "XRef" (stream-xref only)

2. Detect modifications
   - For each InUse object: compare sha256(.pdfjson) vs src_sha256, sha256(.pdfs) vs pdfs_sha256
   - For each Compressed object: same check
   - Ignore is_signature objects (always unmodified)
   - any_modified = bool(modified_in_use or modified_compressed)

3. Build ObjStm group map: objstm_groups[host_num] = [(index, member_num), ...]

4. Open output BytesIO buffer
   - Decode header.txt escape sequences → raw bytes → write to output

5. Write InUse objects in byte_offset order:
   For each inuse_obj:
     - If num in repacked_hosts: write _pack_objstm(pdfx_dir, obj, members)
     - Elif modified: write _build_modified_object(pdfx_dir, entry, trailer_json)
     - Else: write pdfo.read_bytes()  [verbatim — binary-exact path]
     - Record actual byte offset in recorded[num]

6. Record Compressed objects (no bytes written — inside ObjStm):
   recorded[co_num] = (host_num, 0, "c", co_index)

7. Write xref section:

   TABLE-XREF, unmodified, non-linearized:
     xref_raw = xref_raw.bin.read_bytes()
     out.write(xref_raw)   ← verbatim, no modification

   TABLE-XREF, modified or linearized:
     xref_bytes = _build_xref_table(recorded, trailer_json)
     xref_pos = out.tell()
     out.write(xref_bytes)
     out.write(f"startxref\n{xref_pos}\n%%EOF\n".encode())

   STREAM-XREF, unmodified:
     xref_pos = offset already recorded (xref stream written as pdfo in step 5)
     eof_tail = eof_tail.bin.read_bytes()
     Replace digits after 'startxref\n' in eof_tail with str(xref_pos)
     out.write(result)

   STREAM-XREF, modified:
     xref_bytes = _build_xref_stream(recorded, trailer_json, xref_obj_num)
     xref_pos = out.tell()
     out.write(xref_bytes)
     if eof_tail.bin exists: (replacement as above)
     else: out.write(f"startxref\n{xref_pos}\n%%EOF\n".encode())

8. Write output_path from BytesIO buffer
```

---

## 7. xref Handling

### Table-xref reconstruction (`_build_xref_table`)

Builds a classic `xref` section + `trailer` dict from recorded offsets.

```
xref\n
0 {max_num+1}\n
OOOOOOOOOO GGGGG f \n    ← free entries
OOOOOOOOOO GGGGG n \n    ← in-use entries
...
trailer\n
<<\n
  /Size {max_num+1}\n
  /Root {root}\n
  ...
>>\n
```

All offsets are 10-digit zero-padded decimal. Generation numbers are 5-digit zero-padded. Entry lines end with ` \n` (space + LF), 20 bytes each.

### Stream-xref reconstruction (`_build_xref_stream`)

Builds a PDF 1.5+ xref stream object with FlateDecode compression.

- **W field**: `[1, 4, 2]` — type (1 byte), offset (4 bytes), generation (2 bytes)
- **Type values**: 0 = free, 1 = in-use, 2 = ObjStm-hosted
- Object num: `xref_obj_num` (same as original xref stream object)
- Trailer fields: `/Size`, `/Root`, `/Info`, `/ID` from `trailer.pdfjson`

---

## 8. ObjStm (Compressed Object Streams)

PDF 1.5+ PDFs may store multiple objects inside a compressed `/Type /ObjStm` stream. The exporter unpacks these to individual `obj_NNNNN_G.pdfjson` files (no `.pdfo`, since they have no independent byte offset).

### Exporter behavior

- InUse xref entries: normal processing (`.pdfo` + `.pdfjson`)
- Compressed xref entries: only `.pdfjson` written; `in_objstm=true`, `objstm_host=N`, `objstm_index=K` in manifest

### Linker behavior (unmodified)

The host ObjStm object has a `.pdfo` file. Its pdfo bytes are written verbatim (as with any unmodified InUse object). The compressed member objects inside are recorded in `recorded` with type `"c"` for xref stream generation; they are **not** written separately.

### Linker behavior (modified ObjStm members)

If any member object in an ObjStm group is modified, the entire group is re-packed using `_pack_objstm()`:

1. Serialize each member object from its `.pdfjson` (JSON → PDF dict bytes)
2. Build the ObjStm header string: `"num1 off1 num2 off2 ..."` followed by a newline
3. Concatenate header + all member bytes → stream content
4. Re-apply the original filter chain (from the host's `.pdfjson` `_stream_encoding`)
5. Write as `N G obj\n<< /Type /ObjStm /N K /First F /Length L /Filter ... >>\nstream\n...\nendstream\nendobj\n`

Member objects are written in `objstm_index` order within the stream.

---

## 9. Edge Cases

### 9.1 Corrupt xref entries (offset=0 or offset < header_end)

Some PDFs have xref table entries pointing to offset 0 or other positions inside the PDF header. These are ghost entries — the xref is stale or malformed but the object exists at a valid offset elsewhere.

**Exporter**: `_find_header_end()` computes the byte position after `%PDF-X.Y` + optional binary comment. Any InUse entry with `xe.offset < _header_end` is silently skipped — not written to `.pdfo` or `.pdfjson`, not included in the manifest. This prevents the PDF header bytes from being duplicated in the roundtrip output.

### 9.2 Linearized PDFs

Detected by: `any(xe.offset > startxref_val for xe in in_use.values())`. A linearized PDF's xref is near the beginning of the file (low `startxref_val`), but most objects appear after it.

**Exporter**: sets `"linearized": true` in manifest, does NOT write `xref_raw.bin` (the xref section covers almost the entire file offset range, making it impractical). Does NOT compute `_next_boundaries` (inter-object gaps in linearized PDFs may contain partial xref sections).

**Linker**: `is_linearized` forces the modified path — always builds a fresh xref, producing a valid de-linearized PDF.

### 9.3 Stream-xref with no ObjStm objects (PDF 1.5 table-compatible)

A PDF can use a stream-xref (`N G obj` at `startxref_val`) but contain no compressed/ObjStm objects. In this case `bool(compressed)` would be `False`, which would incorrectly classify it as `"table"`. The exporter checks the actual bytes at `startxref_val` to avoid this misclassification.

### 9.4 Multiple `startxref` occurrences (incremental updates)

The exporter uses `scan_backward` — scanning backward from EOF to find the last `startxref`. This correctly selects the most recent xref in an incrementally-updated PDF. Earlier xref sections are part of the verbatim object bytes and preserved in `.pdfo` files.

### 9.5 CRLF vs LF line endings in xref tables

Windows-generated PDFs often use CRLF throughout, including in the xref section. `xref_raw.bin` is written and read verbatim, preserving exact EOL style without any translation. The linker's unmodified table-xref path writes `xref_raw.bin` directly with no rewriting.

### 9.6 Digital signatures (`/Type /Sig`)

The exporter sets `is_signature: true` in the manifest for these objects. The linker forces the `.pdfo` path (verbatim write). Re-serializing a signature object invalidates the PKCS#7 signature.

### 9.7 Gap bytes between objects

PDFs commonly have 1-2 bytes of whitespace (`\n`, `\r\n`) between `endobj` and the next `N G obj` marker. These bytes are not part of any object according to the PDF spec, but they are part of the file's byte layout. Including them in `.pdfo` is necessary for binary-exact output:

- Object A's `.pdfo` ends at object B's start offset
- Writing A's `.pdfo` verbatim places the output stream pointer exactly at B's expected start
- Writing B's `.pdfo` verbatim from that position → B is at its original offset

Without gap bytes, each gap would reduce the output file size by 1-2 bytes, shifting all subsequent offsets.

---

## 10. Binary-Exact Guarantee

### Proof (unmodified non-linearized PDFs)

1. `header.txt` is decoded to the exact original bytes before the first object — output starts identically.
2. Each InUse object's `.pdfo` extends from its `byte_offset` to the next object's `byte_offset`, inclusive of gap bytes.
3. Writing `.pdfo` files in `byte_offset` order places each object at its original position in the output stream.
4. Since every object occupies exactly `byte_length` bytes (matching the original), all subsequent objects maintain their original offsets.
5. **Table-xref, unmodified**: `xref_raw.bin` is written verbatim. All offsets in the xref table are unchanged (objects are at original positions). The startxref value in `xref_raw.bin` is already correct.
6. **Stream-xref, unmodified**: The xref stream object's `.pdfo` is written at its original position. `eof_tail.bin` is written verbatim (offset number unchanged since `xref_pos == original startxref_val`).
7. Output is byte-for-byte identical to input. ∎

### Scope

| PDF type | Result |
|---|---|
| Non-linearized, table-xref | ✓ Binary-exact |
| Non-linearized, stream-xref | ✓ Binary-exact |
| Non-linearized, stream-xref + ObjStm | ✓ Binary-exact (ObjStm pdfo written verbatim) |
| Linearized | Valid de-linearized PDF (not byte-identical) |
| Encrypted | Not supported |
| Incrementally updated | ✓ Binary-exact (latest xref only; earlier xref sections in pdfo) |
| Corrupt offset=0 xref entries | ✓ Binary-exact (corrupt entries skipped; real object at correct offset) |

### Stress test results (360 PDFs, May 2026)

| Category | Count |
|---|---|
| Binary-exact match | 278 |
| Linearized (valid, not exact) | 82 |
| Non-linearized mismatch | 0 |
| Error | 0 |
| **Total** | **360** |
