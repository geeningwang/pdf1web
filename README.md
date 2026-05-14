# pdf1web

A web-based PDF structure analyzer. Upload a PDF and explore its internal object tree, page content streams, embedded images, fonts, ICC profiles, and more — all rendered in the browser.

## Features

- **Object tree** — browse every PDF indirect object (dictionaries, streams, arrays, references) in a collapsible tree
- **Back-references** — see every object that points to the selected object
- **Page rendering** — canvas-based renderer that executes PDF content stream operators, with correct font and image support
  - CID/Type0 fonts rendered via [OpenType.js](https://opentype.js.org/) by GlyphID, bypassing Unicode cmap limitations
  - SMask (soft-mask) transparency compositing for images
  - Natural page size at 96 dpi (96/72 pt→px)
- **Image viewer** — decode and display embedded images (JPEG, CCITT, FlateDecode/PNG); structural breakdown of JPEG markers, CCITT parameters, and Flate predictor settings
- **ICC profile viewer** — parse embedded ICC color profiles; show white/black point, primaries, TRC curves, tag directory, and binary structure map
- **Content stream inspector** — list and categorize all PDF operators with operands, counts, and structure segments
- **Font tools**
  - ToUnicode CMap viewer — mappings from character codes to Unicode code points
  - FontDescriptor viewer — font flags, metrics (ascent, descent, cap-height, etc.)
  - TrueType table directory — all `sfnt` table tags, offsets, lengths, and descriptions
  - CIDToGIDMap viewer — CID → Glyph ID mapping table with coverage bitmap
  - CIDSet viewer — which CIDs are present in the embedded font subset
- **Color palette viewer** — indexed-color palette entries with hex values and swatches
- **PDF store** — save PDFs on the server for quick re-opening across sessions

## Architecture

```
pdf1web/
├── backend/          FastAPI application (Python 3.12)
│   ├── main.py       All API endpoints + session/store management
│   ├── pdf/          Pure-Python PDF parsing library
│   │   ├── reader.py       Raw byte reader
│   │   ├── tokenizer.py    PDF tokenizer
│   │   ├── parser.py       PDF object parser
│   │   ├── objects.py      In-memory PDF value model (PdfObject)
│   │   ├── xref.py         Cross-reference table / stream parser
│   │   ├── document.py     PdfDocument — loads, resolves, builds tree
│   │   ├── filters.py      Stream filter decoders (Flate, LZW, ASCII…)
│   │   ├── content_stream.py  Content stream operator parser
│   │   ├── icc.py          ICC profile binary parser
│   │   ├── jpeg.py         JPEG segment structure parser
│   │   ├── ccitt.py        CCITTFaxDecode parameter parser
│   │   └── flat.py         FlateDecode image metadata parser
│   └── requirements.txt
└── frontend/         React + TypeScript + Vite application
    ├── src/
    │   ├── App.tsx           Root component — layout, state, file handling
    │   ├── api.ts            Typed fetch wrappers for all API endpoints
    │   ├── main.tsx          React entry point
    │   ├── index.css         Global styles
    │   └── components/
    │       ├── Toolbar.tsx           Top bar: upload, store, filename display
    │       ├── TreePane.tsx          Virtualized object tree with keyboard nav
    │       ├── DetailPane.tsx        Right panel — dispatches to sub-panes
    │       ├── ImagePane.tsx         Image display + structural breakdown
    │       ├── IccPane.tsx           ICC profile viewer
    │       ├── ContentStreamPane.tsx Content stream ops + canvas renderer toggle
    │       ├── CsCanvasRenderer.tsx  Canvas renderer (OpenType.js, SMask)
    │       ├── PalettePane.tsx       Indexed color palette
    │       ├── ToUnicodePane.tsx     ToUnicode CMap table
    │       ├── FontDescriptorPane.tsx Font metrics and flags
    │       ├── TtfTablesPane.tsx     TrueType table directory + glyph grid
    │       ├── CidToGidPane.tsx      CID→GID mapping table
    │       └── CidSetPane.tsx        CIDSet bitmap viewer
    └── package.json
```

## Prerequisites

- Python 3.12+
- Node.js 18+

## Running Locally

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (development)

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Open `http://localhost:5173`.

### Frontend (production build)

```bash
cd frontend
npm run build
```

The built files are output to `frontend/dist/`. The backend serves them automatically if the `dist/` folder is present (FastAPI `StaticFiles` mount).

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a PDF; returns object tree JSON |
| `POST` | `/api/store` | Save a PDF to the server store |
| `GET`  | `/api/store` | List PDFs in the server store |
| `POST` | `/api/open_from_store/{filename}` | Open a stored PDF |
| `GET`  | `/api/object/{id}/{num}/{gen}` | Object detail + capability flags |
| `GET`  | `/api/backrefs/{id}/{num}` | Objects that reference this object |
| `GET`  | `/api/content_stream/{id}/{num}/{gen}` | Parsed content stream ops + resources |
| `GET`  | `/api/image/{id}/{num}/{gen}` | Decoded image (PNG or JPEG response) |
| `GET`  | `/api/image_detail/{id}/{num}/{gen}` | Image metadata + structural breakdown |
| `GET`  | `/api/icc/{id}/{num}/{gen}` | Parsed ICC profile data |
| `GET`  | `/api/palette/{id}/{num}/{gen}` | Indexed color palette entries |
| `GET`  | `/api/tounicode/{id}/{num}/{gen}` | ToUnicode CMap mappings |
| `GET`  | `/api/fontdescriptor/{id}/{num}/{gen}` | Font descriptor metrics |
| `GET`  | `/api/ttf_tables/{id}/{num}/{gen}` | TrueType table directory |
| `GET`  | `/api/ttf_raw/{id}/{num}/{gen}` | Raw decoded TrueType/OTF bytes |
| `GET`  | `/api/raw_stream/{id}/{num}/{gen}` | Raw decoded stream bytes |
| `GET`  | `/api/cid_to_gid/{id}/{num}/{gen}` | CIDToGIDMap table |
| `GET`  | `/api/cid_set/{id}/{num}/{gen}` | CIDSet bitmap |
| `GET`  | `/api/page_render/{id}/{num}/{gen}` | Server-side page render (PNG) |

## Session Model

Uploaded PDFs are held in memory (up to 20 sessions; oldest is evicted). Each upload also persists the raw PDF bytes and an `analysis.log` under `backend/uploads/<upload-id>/`. The store (`backend/store/`) is permanent across restarts.

## License

See [LICENSE](LICENSE).
