# PDFX — PDF Export Format Design

**Version**: 0.3  
**Status**: Implementation Complete  
**Scope**: Exporter, linker, binary-exact roundtrip, AI-modification workflow

**Implementation status** (360-PDF stress test, May 2026): 278 binary-exact matches, 82 linearized PDFs (valid de-linearized output), 0 errors.

---

## 1. Goals

| Goal | Description |
|---|---|
| **Lossless** | Every bit of the original PDF is preserved in the export |
| **Binary-exact roundtrip** | `compile(export(X)) == X` byte-for-byte when no modifications are made |
| **AI-readable** | All non-binary content is human/AI-readable and editable plain text |
| **Self-documenting** | The format contains enough structure that a reader unfamiliar with PDF can understand the content |
| **Modifiable** | An AI agent can edit exported text files and the compiler produces a valid modified PDF |

---

## 2. High-Level Architecture

```
Original PDF
     │
     ▼ exporter
 export_dir/          ← PDFX directory (one dir per PDF)
     │
     ▼ compiler
 Reconstructed PDF    ← binary identical (if unmodified)
```

The export directory is a flat+structured directory, not a binary archive. Every object in the PDF becomes one or two files. Images and font programs are stored as binary files; everything else is plain text.

---

## 3. Core Concepts

### 3.1 Source / Object / Executable — the C analogy

The design mirrors the C language compile-and-link pipeline:

| C concept | C artifact | PDFX equivalent | File suffix |
|---|---|---|---|
| Source code | `.c` | AI-editable JSON dict describing the object's structure | `.pdfjson` |
| Assembly listing | `.s` | Decoded stream content — human/AI-readable body of a stream object | `.pdfs` |
| Object file | `.o` | Verbatim bytes of the object as found in the original PDF — ready to link | `.pdfo` |
| Executable | binary | Assembled PDF file output by the linker | `.pdf` |

**Exporter = compiler**: reads the original PDF and emits both a `.pdfjson` source file and a `.pdfo` object file for every PDF object — analogous to `gcc -c` producing both a human-readable preprocessed form and a `.pdfo`.

**Compiler = linker**: reads all `.pdfo` / `.pdfjson` files and links them into the final PDF executable — analogous to `ld` (or `gcc` in link mode).

The **linker chooses which input to use** per object:
- If the object is **unmodified** (`.pdfjson` and `.pdfs` checksums both match the manifest): link the `.pdfo` file verbatim → byte-exact output at the same offset.
- If the object is **modified** (`.pdfjson` or `.pdfs` changed by AI/human): re-compile from `.pdfjson` + `.pdfs` sources → produces a valid but non-identical PDF.

### 3.2 Binary-exact guarantee

A PDF file is byte-offset-sensitive: the cross-reference table (xref) maps object numbers to their byte positions in the file. Binary exactness requires:

1. All objects output at identical byte offsets as the original.
2. This holds iff every object occupies the same number of bytes in the output as in the input.
3. Since we link the pre-compiled `.pdfo` files for unmodified objects, the byte count is identical by definition.
4. The xref is **recomputed** from measured byte positions during compilation — it will match the original exactly.

**Caveat**: if any object is modified, the object's byte size changes, shifting the offsets of all subsequent objects. The compiled PDF will be valid but not byte-identical. This is expected and correct behaviour for the modification workflow.

---

## 4. Directory Structure

```
<name>.pdfx/
│
├── pdfx_manifest.json          # Root index — required
│
├── header.txt                  # Escaped-line encoding of the verbatim pre-first-object bytes
│
├── xref_raw.bin                # Verbatim original xref+trailer section (table-xref, non-linearized only)
├── eof_tail.bin                # Verbatim 'startxref...%%EOF' section (stream-xref only)
│
├── objects/
│   ├── obj_0005_0.pdfjson      # Source: AI-editable JSON dict (like .c)
│   ├── obj_0005_0.pdfo         # Object file: verbatim bytes from the original PDF (like .o)
│   │                           #   — extends to next object boundary (includes gap bytes)
│   │
│   ├── obj_0045_0.pdfjson      # JSON dict for object 45 (page content stream)
│   ├── obj_0045_0.pdfo         # Object file for object 45
│   ├── obj_0045_0.pdfs         # Decoded stream content — PDF operators, AI-editable (like .s)
│   │
│   ├── obj_0066_0.pdfjson      # JSON dict for object 66 (font stream)
│   ├── obj_0066_0.pdfo         # Object file for object 66
│   └── obj_0066_0.stream.bin   # Decoded binary stream payload (font program)
│
├── resources/
│   ├── font_0066_0.ttf         # Extracted font file
│   └── image_0063_0.jpg        # Extracted image (original compressed bytes)
│
├── xref.txt                    # Human-readable xref table summary (informational only)
└── trailer.pdfjson              # Trailer dictionary (semantic form)
```

**Naming convention**: `obj_{NNNNN}_{G}` where NNNNN is the object number zero-padded to 5 digits and G is the generation number. Zero-padding makes directory listings sort in object-number order.

---

## 5. File Format Specifications

### 5.1 `pdfx_manifest.json`

The root descriptor. Contains:

```json
{
  "pdfx_version": "1.0",
  "source_filename": "original.pdf",
  "source_sha256": "a3f1...",
  "pdf_version": "1.7",
  "pdf_size_bytes": 102400,
  "object_count": 87,
  "xref_type": "table",
  "linearized": false,
  "encrypted": false,
  "trailer": {
    "Size": 88,
    "Root": "1 0 R",
    "Info": "2 0 R"
  },
  "startxref": 98123,
  "objects": [
    {
      "num": 5,
      "gen": 0,
      "byte_offset": 1024,
      "byte_length": 312,
      "obj_sha256": "b2c3...",
      "src_sha256": "d4e5...",
      "pdfs_sha256": null,
      "type": "dict",
      "pdf_type": "Font",
      "pdf_subtype": "Type1",
      "has_stream": false,
      "stream_encoding": null,
      "stream_length": null,
      "stream_type": null,
      "resource_file": null,
      "is_signature": false
    },
    {
      "num": 66,
      "gen": 0,
      "byte_offset": 44210,
      "byte_length": 18432,
      "obj_sha256": "f9a1...",
      "src_sha256": "c3b2...",
      "pdfs_sha256": null,
      "type": "stream",
      "pdf_type": "FontFile2",
      "pdf_subtype": null,
      "has_stream": true,
      "stream_encoding": ["FlateDecode"],
      "stream_length": 18100,
      "stream_type": "binary",
      "resource_file": "resources/font_0066_0.ttf",
      "is_signature": false
    }
  ]
}
```

Key fields per object entry:

| Field | Description |
|---|---|
| `byte_offset` | Byte position of `N G obj` in the original PDF |
| `byte_length` | Bytes from start of `N G obj` through `endobj\n` inclusive |
| `obj_sha256` | SHA-256 of the `.pdfo` object file — linker verifies integrity before linking |
| `src_sha256` | SHA-256 of the `.pdfjson` source at export time — linker compares current hash to detect edits |
| `pdfs_sha256` | SHA-256 of the `.pdfs` decoded stream file at export time — `null` for non-text-stream objects |
| `stream_encoding` | List of filters applied to the stream in the PDF (e.g. `["FlateDecode"]`) |
| `stream_type` | `"text"`, `"binary"`, or `null` — determines whether `.pdfs` or a resource file holds the stream |
| `resource_file` | Path to the extracted binary resource file, if applicable |
| `is_signature` | `true` if this is a digital signature object — linker refuses to re-serialize modified signatures |

### 5.2 `header.txt`

`header.txt` contains the verbatim bytes of the original PDF from byte 0 up to (not including) the first valid object, encoded as a **single escaped line** followed by one LF file-terminator.

Encoding rules:

| Byte | Encoded as |
|---|---|
| Printable ASCII `0x20–0x7E` (except `\`) | literal character |
| `0x0D` (CR) | `\r` |
| `0x0A` (LF) | `\n` |
| `0x5C` (backslash) | `\\` |
| All other bytes | `\xNN` (two hex digits) |

**Example** — CRLF PDF with binary comment:
```
%PDF-1.7\r\n%\xa1\xb3\xc5\xd7\r\n
```
(The trailing newline in the file is the file-terminator, not part of the header data.)

This format preserves exact EOL style (LF or CRLF), unusual binary comment variants (single-byte, space-prefixed), and any gap bytes between the binary comment and the first object.

**Header boundary**: The exporter computes `_header_end` by parsing the `%PDF-X.Y` line and optional binary comment from the raw bytes. xref entries with `offset < _header_end` are filtered out as corrupt (e.g. offset=0 pointing at the `%PDF-` header). The header boundary written to `header.txt` is `min(offset for valid InUse objects)`, which may include gap bytes after the binary comment.

### 5.3 Object source files: `obj_NNNNN_G.pdfjson`

These are the AI-editable source files, stored as **JSON**. JSON is chosen over custom dialects and YAML because:
- AI agents produce JSON reliably via structured output and tool-calling modes.
- JSON Schema can validate AI output before it reaches the linker — a hard gate before any file is written.
- Parsing is unambiguous — no indentation sensitivity, no implicit type coercion.
- Every major language has a JSON parser; no custom parser needed.

| Format | AI output reliability | Schema validation | Custom parser | Verbosity |
|---|---|---|---|---|
| Custom PDF dialect | Medium (no schema) | No | Required | Low |
| YAML | Medium (indentation errors common) | No | No | Low |
| **JSON** | **High (structured output, tool calls)** | **JSON Schema** | **No** | Medium |

**JSON type conventions for PDF values:**

| PDF type | JSON representation | Example |
|---|---|---|
| Name | string prefixed with `/` | `"/Font"` |
| Integer / Real | number | `12`, `1.5` |
| Boolean | boolean | `true` |
| Null | null | `null` |
| Literal string | plain string | `"Hello"` |
| Hex string | `{"$hex": "..."}` | `{"$hex": "a3f1"}` |
| Indirect reference | string matching `N G R` | `"6 0 R"` |
| Array | JSON array | `[278, 556, 889]` |
| Dictionary | JSON object | `{"Type": "/Font"}` |

Keys prefixed with `_` are PDFX metadata — the linker strips them before serializing the PDF object:

| Key | Description |
|---|---|
| `_obj` | Object number and generation: `"5 0"` |
| `_type` | Human-readable type hint |
| `_stream` | `"text"` \| `"binary"` \| `"none"` |
| `_stream_file` | Path to `.pdfs` decoded stream (text streams only) |
| `_resource` | Path to binary resource file (binary streams only) |
| `_stream_encoding` | Original filter chain, e.g. `["FlateDecode"]` |

**Example — dict object (no stream):**

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

**Example — binary stream object (font program):**

```json
{
  "_obj": "66 0",
  "_type": "FontFile2 (embedded TrueType font program)",
  "_stream": "binary",
  "_resource": "resources/font_0066_0.ttf",
  "_stream_encoding": ["FlateDecode"],
  "Length": 18100,
  "Filter": "/FlateDecode",
  "Length1": 44728
}
```

**Example — text stream object (page content stream):**

```json
{
  "_obj": "45 0",
  "_type": "Content stream (page content)",
  "_stream": "text",
  "_stream_file": "obj_0045_0.pdfs",
  "_stream_encoding": ["FlateDecode"],
  "Length": 1842,
  "Filter": "/FlateDecode"
}
```

### 5.4 Text stream files: `obj_NNNNN_G.pdfs`

For stream objects whose decoded content is valid UTF-8 text (content streams, CMaps, XMP metadata, JavaScript, PostScript XObjects), the decoded content is stored in a separate `.pdfs` file alongside the `.pdfjson` JSON dict.

The name `.pdfs` = "PDF stream (decoded)". It is the assembly listing of the stream — the human/AI-readable intermediate between the JSON dict (`.pdfjson`) and the verbatim object bytes (`.pdfo`), mirroring the `.s` assembly file in the C pipeline.

**Example — `obj_0045_0.pdfs`** (decoded page content stream):

```
BT
/F1 12 Tf
72 720 Td
(Hello, World!) Tj
ET
```

The `.pdfs` file contains only the raw decoded stream bytes — no headers, no markers. The linker re-encodes it with the filter chain from `_stream_encoding` in the `.pdfjson` file and updates `/Length` automatically.

**Modification rules for `.pdfs`:**
- Edit content stream operators, text strings, or CMap entries directly.
- Do not add a BOM or change line endings — the linker treats the file as raw bytes.
- Leave `/Length` in the `.pdfjson` dict as-is — the linker overwrites it after re-encoding.

**Note**: Because the stream is re-encoded after modification, a modified `.pdfs` object will not be byte-identical, but the resulting PDF will be valid.

### 5.5 Binary stream resources

All resource files are stored decoded (filters stripped). The linker re-applies the original filter chain on the way back in.

**Fonts** (`resources/font_NNNNN_G.{ttf,otf,pfb,cff}`):
- TrueType/OpenType fonts: decoded and stored as `.ttf` / `.otf`.
- Type 1 fonts: decoded and stored as `.pfb`.
- CFF fonts: decoded and stored as `.cff`.

**Images** (`resources/image_NNNNN_G.{jpg,png,jbig2,...}`):
- JPEG images (DCTDecode): stored as `.jpg` — compressed bytes are the stream payload verbatim, no re-encoding needed.
- JBIG2 images: stored as `.jbig2`.
- Other images (FlateDecode, LZWDecode, etc.): decoded and stored as `.png` (lossless).

**ICC color profiles** (`resources/icc_NNNNN_G.icc`):
- Appear as `/ColorSpace [/ICCBased N 0 R]` stream objects.
- Binary ICC profile data — store as `.icc`.
- Very common in print and press PDFs.

**Embedded file attachments** (`resources/attachment_NNNNN_G.<ext>`):
- `/Type /EmbeddedFile` stream objects. PDFs can carry arbitrary attached files.
- Extension is taken from the `/Subtype` entry (e.g. `application/zip` → `.zip`) or from the filename in the parent `/Filespec` dictionary.
- Fall back to `.bin` if the type is unknown.

**Digital signatures** (`resources/signature_NNNNN_G.sig`):
- `/Type /Sig` byte-range signature blobs (PKCS#7 / CAdES, DER-encoded).
- **Special rule**: signature objects must always be linked from `.pdfo` — never re-serialized. Re-serializing a signed object invalidates the cryptographic signature. The linker must refuse to process a modified `.pdfjson` for any object flagged `"is_signature": true` in the manifest.

**3D model data** (`resources/3d_NNNNN_G.{u3d,prc}`):
- `/Type /3D` streams used in PDF/E. Stored as `.u3d` or `.prc` depending on `/Subtype`.
- Rare in practice; treat as opaque binary.

**Sound data** (`resources/sound_NNNNN_G.{wav,aiff,bin}`):
- `/Type /Sound` streams. Extension derived from `/Encoding` entry; fall back to `.bin`.
- Rare in modern PDFs.

**Streams that are text, not binary** (stored inline in `.pdfjson`, not as resource files):
- XMP Metadata (`/Type /Metadata`, `application/rdf+xml`) — UTF-8 XML, high AI-modification value.
- JavaScript actions — plain JS source.
- PostScript XObjects — text-based PS content.
- Page content streams, ToUnicode CMaps — PDF operators/text.

When the linker processes a binary stream object:
1. Read the resource file.
2. Re-apply the original filter chain (from `stream_encoding` in the manifest).
3. Use the resulting bytes as the stream payload.
4. Update `/Length` to the re-encoded byte count.

**For JPEG images and signature blobs**, the stored bytes are the original compressed/encoded bytes — no re-encoding, so those stream bytes are bit-exact.

### 5.6 `xref.txt`

Informational only — not used by the compiler (which recomputes xref fresh from measured offsets).

```
%% PDFX Cross-reference table (informational)
%% xref_type: table
%% object_count: 88
%%
%% num  gen  offset      status
   0    65535 0          free
   1    0     9          in-use
   2    0     58         in-use
   5    0     1024       in-use
   ...
```

### 5.7 `trailer.pdfjson`

```
%% PDFX Trailer
<<
  /Size 88
  /Root 1 0 R
  /Info 2 0 R
  /ID [ <a3f1...> <a3f1...> ]
>>
```

---

## 6. Exporter Algorithm

```
function export(pdf_path, output_dir):
  1. Parse PDF: read header, enumerate all objects via xref
  2. Compute `_first_obj_offset = min(valid InUse offsets)`. Encode `data[0:_first_obj_offset]` as single escaped line → write `header.txt`.
  3. For each object (num, gen):
     a. Extract raw bytes → write objects/obj_NNNNN_G.pdfo   (the object file — like gcc -c)
     b. Parse object dict/value → serialize as JSON → write objects/obj_NNNNN_G.pdfjson   (the source)
     c. If object has a stream:
        i.  Read raw stream bytes (still encoded)
        ii. Decode stream (apply filters in reverse)
        iii. Classify decoded content:
             - Is UTF-8 text? → write objects/obj_NNNNN_G.pdfs; set _stream_file in .pdfjson
             - Is font program? → write resources/font_NNNNN_G.{ext}; set _resource in .pdfjson
             - Is image? → write resources/image_NNNNN_G.{ext}; set _resource in .pdfjson
             - Unknown binary? → write objects/obj_NNNNN_G.stream.bin; set _resource in .pdfjson
     d. If object is /Type /ObjStm: unpack contained objects; export each as individual
        obj_NNNNN_G.* files; record in_objstm, objstm_host, objstm_index in manifest
  4. Write trailer.pdfjson
  5. Write xref.txt (informational)
  6. Compute obj_sha256 (.pdfo), src_sha256 (.pdfjson), pdfs_sha256 (.pdfs where present)
  7. Write pdfx_manifest.json
```

**Handling xref streams** (PDF 1.5+ compressed xref):  
The xref stream is an object like any other. Its `.pdfo` file preserves it exactly. The `.pdfjson` source shows the decoded xref entries. The linker always regenerates a fresh xref (table or stream, matching the original format).

---

## 7. Linker Algorithm

```
function link(export_dir, output_pdf):        # analogous to: ld *.pdfo -o program
  1. Read pdfx_manifest.json
  2. Decode `header.txt` escape sequences → raw bytes → write to output
  3. For each object (in original byte_offset order, skipping ObjStm-contained objects):
     a. Check if modified:
        - compare sha256(obj_NNNNN_G.pdfjson) vs src_sha256 in manifest
        - if pdfs_sha256 non-null: compare sha256(obj_NNNNN_G.pdfs) vs pdfs_sha256
        - if is_signature=true: force unmodified path regardless of checksum
     b. If unmodified: write bytes from obj_NNNNN_G.pdfo verbatim   ← link the .pdfo directly
        → object occupies same byte count → same offset for next object
     c. If modified: re-compile from source files                    ← recompile changed .c → .pdfo
        → read JSON dict from obj_NNNNN_G.pdfjson (strip _ keys before serializing)
        → if _stream = "text": read obj_NNNNN_G.pdfs, re-encode with _stream_encoding, update /Length
        → if _stream = "binary": read resource file, re-encode with _stream_encoding, update /Length
        → serialize dict + stream to PDF syntax bytes → write to output
     d. Record actual byte offset of this object (for xref)
  4. Re-pack /ObjStm groups: for each objstm_host, serialize contained objects
     in objstm_index order, apply original filter chain, write as /ObjStm stream
  5. Build xref from recorded offsets          # analogous to: resolve symbol addresses
     → if original used xref table: write classic xref + trailer
     → if original used xref stream: write xref stream object
  6. If linearized: second pass — rewrite Linearization dict and hint stream
     with measured byte offsets from pass 1
  7. Write startxref + %%EOF
```

**Binary-exact proof** (unmodified path):  
- Every object writes exactly `byte_length` bytes (from `.pdfo`).  
- Byte offsets are identical to originals.  
- xref recomputed from identical offsets → identical xref bytes.  
- startxref points to the xref at the same position.  
- Output is byte-for-byte identical to input. ∎

---

## 8. Edge Cases and Constraints

### 8.1 Linearized PDFs

Linearized PDFs place the xref section near the beginning of the file, before most content objects. The linearization hint stream encodes byte offsets throughout the file. Reconstructing a byte-identical linearized PDF requires a two-pass linker that measures offsets in pass 1 and rewrites the hint stream in pass 2.

**Current decision**: De-linearize on export. The exporter detects linearized PDFs by checking whether any InUse object has a byte offset greater than the `startxref` value (which indicates objects exist after the xref section). When `"linearized": true` is in the manifest, the linker always uses the modified path — it writes objects in object-number order, builds a fresh xref table at the end, and produces a valid non-linearized PDF.

Linearized PDFs account for 82 of 360 test PDFs (23%). These produce valid output but not byte-identical output. Binary-exact roundtrip for linearized PDFs (two-pass) is deferred to a future version.

### 8.2 Encrypted PDFs

Encrypted PDFs encode all string and stream bytes under a file-level key derived from the user/owner password. 

**Decision**: Export requires decryption password. Exported files store decrypted content. Re-compiled PDF is unencrypted. 

Binary-exact guarantee does **not** hold for encrypted PDFs (encryption would need to be re-applied with the same key and same IV, which is implementation-defined).

### 8.3 Object streams (PDF 1.5+, `/Type /ObjStm`)

Object streams compress multiple objects together into one stream. The exporter unpacks them: each contained object is exported as an individual `obj_NNNNN_G.*` file. The manifest records `"in_objstm": true` and `"objstm_host": NNN` for these objects.

The linker re-packs contained objects back into object streams with the same grouping recorded in the manifest (`objstm_host` and `objstm_index` fields). Objects are serialized in `objstm_index` order within each group, and the original filter chain is re-applied. This guarantees binary-exact output for PDFs that use compressed object streams.

### 8.4 Generation numbers > 0 (updated/freed objects)

PDFs allow objects to be updated in-place (incremental updates). The exporter handles this by exporting the latest version of each object. Incremental update structure is not preserved in v1.

### 8.5 Stream filter re-encoding

For text streams (content streams, CMaps), the compiler re-encodes with the same filter list. FlateDecode results are deterministic for the same input + compression level. However, different zlib implementations or compression levels may produce different output bytes.

**Decision**: For the binary-exact guarantee, text stream objects that were modified use fresh FlateDecode at the default level. For unmodified objects, the `.pdfo` file is linked verbatim (bypassing re-encoding entirely).

---

## 9. AI Modification Workflow (Future)

The export format is designed to support an AI-guided modification cycle:

```
export(pdf) → pdfx_dir
    ↓
AI agent reads pdfx_dir/objects/*.pdfjson files
    ↓
Agent modifies .pdfjson files (e.g. changes font, edits text in content stream)
    ↓
compile(pdfx_dir) → modified.pdf
```

**What AI can safely modify in `.pdfjson` files:**
- Dictionary values (font names, metadata strings, color values, etc.)
- Content stream operators (text, graphics commands)
- ToUnicode / Encoding maps
- Trailer fields (/Author, /Title, etc. in Info dict)

**What AI should NOT modify:**
- Object numbers and generation numbers (cross-references must stay consistent)
- `/Length` values in stream dictionaries (linker recomputes these)
- `/XRef` and `/Prev` offsets (linker recomputes these)
- `.pdfo` object files (pre-compiled blobs — treat like a compiled `.pdfo`, edit the `.pdfjson` source instead)

The manifest's `src_sha256` and `pdfs_sha256` fields allow the linker to detect modified `.pdfjson` and `.pdfs` files automatically, without requiring the user to track changes.

---

## 10. Implementation Scope for Stage 1

The following components are in scope for v1:

| Component | Location | Notes |
|---|---|---|
| **Exporter** | `backend/pdf/exporter.py` | Produces `.pdfjson` (JSON) + `.pdfs` + `.pdfo` per object — like `gcc -c` |
| **Linker** | `backend/pdf/linker.py` | Assembles objects into final PDF — two-pass for linearized, re-packs ObjStm |
| **Export API endpoint** | `backend/main.py` | `GET /api/export/{upload_id}` → directory on disk (zip served for download) |
| **Link API endpoint** | `backend/main.py` | `POST /api/link` → PDF download |
| **Format version** | `pdfx_version: "1.0"` | |

Out of scope for v1: linearization preservation, encryption round-trip, object stream re-packing, AI agent integration (that is stage 2).

---

## 11. Design Decisions (Resolved)

1. **Export format — directory**: The canonical on-disk format is always an unpacked directory tree. The API serves a zip download as a convenience wrapper for transfer, but the format spec is directory-based.

2. **Raw bytes for all objects + `.pdfs` split**: Every object — including text-stream objects — has a `.pdfo` verbatim bytes file. Decoded stream content is stored separately in a `.pdfs` file. This gives a clean three-layer separation:
   - `.pdfo` — binary-exact blob, never edited
   - `.pdfjson` — JSON dict of the object, AI-editable
   - `.pdfs` — decoded stream body, AI-editable
   Unmodified objects always link from `.pdfo`, bypassing re-encoding entirely and eliminating FlateDecode fidelity concerns.

3. **Object stream re-packing**: The linker re-packs `/ObjStm` groups with the original grouping (recorded in the manifest via `objstm_host` / `objstm_index`). Binary-exact output is guaranteed for PDF 1.5+ files that use compressed object streams.

4. **Source file format — JSON**: `.pdfjson` files use JSON. AI agents produce JSON reliably via structured output modes, JSON Schema can validate AI output before it reaches the linker, and parsing is unambiguous. PDF-specific types use simple string conventions (`"/Name"`, `"N G R"`) that LLMs handle well without special training.

5. **Binary-exact scope**: Encrypted PDFs are out of scope. Linearized PDFs produce valid de-linearized output (not binary-exact). Compressed object streams (ObjStm re-packing) are handled correctly for binary-exact output. The binary-exact guarantee applies to all non-encrypted, non-linearized PDFs with either table-xref or stream-xref.

6. **xref type detection**: xref type (`"table"` vs `"stream"`) is determined by inspecting the bytes at `startxref_val` in the original PDF data — if the bytes start with `xref`, it is a table-xref; otherwise it is a stream-xref object. This is more reliable than checking for the presence of ObjStm compressed entries (a PDF 1.5 stream-xref file may contain no ObjStm objects).

7. **`.pdfo` byte range includes gap bytes**: Each object's `.pdfo` file extends from the object's byte offset to the next object's offset (or the xref start for the last object), capturing any inter-object whitespace. For the xref stream object the boundary is the `startxref` keyword position. The manifest's `byte_length` reflects this extended range. This is essential for binary-exact roundtrip — objects written verbatim from `.pdfo` must land at their original offsets, so the gap bytes must be included.
