# pdf1web — Competitive Gap Analysis

Compared against: **iText RUPS**, **Apache PDFBox PDFDebugger**, **Didier Stevens' pdf-parser**, **Origami**.

## Structural / Navigation

- No page navigation — canvas renderer works per content-stream object, not as a navigable document; users can't move between pages.
- No document outline (Bookmarks tree) viewer.
- No full-text search across the object tree (e.g. "find all objects with `/Filter /DCTDecode`").

## Object-level Gaps

- No hex/raw bytes viewer for arbitrary objects — only streams with a known type get a viewer; plain dictionaries and integers have no hex dump.
- No XMP metadata viewer (`/Metadata` stream with XML).
- No annotation inspection (`/Annots` array).
- No form field / AcroForm inspection.
- No JavaScript / action chain inspection (security-relevant).
- No embedded files (`/EmbeddedFiles`) viewer.
- No digital signature inspection.

## PDF Health / Conformance

- No encrypted PDF support — upload fails on password-protected files.
- No PDF/A or PDF/X conformance validation.
- No linearization ("fast web view") analysis.
- No broken xref / repair detection reporting.

## Usability

- Sessions are ephemeral (max 20, in-memory) — no persistent history or named sessions.
- No zoom/pan on the canvas renderer.
- No way to export/download extracted resources (images, embedded fonts) from the UI.
- No multi-PDF comparison/diff.

## Competitor Summary

| Tool | Key Edge over pdf1web |
|------|-----------------------|
| iText RUPS | Object editing, hex view, encrypted PDFs |
| PDFBox PDFDebugger | Full page tree navigation, raw bytes |
| pdf-parser (Didier Stevens) | Deep JavaScript/action analysis, malware triage |
| Origami (Ruby) | Read + write, digital signature inspection |

## Highest-Priority Gaps

1. **No page navigation** — rules out multi-page document workflows entirely.
2. **No encrypted PDF support** — rules out a large class of real-world files.
3. **No hex viewer** for arbitrary objects — core feature of every competing tool.
4. **No JavaScript/action inspection** — important for security analysis use cases.
