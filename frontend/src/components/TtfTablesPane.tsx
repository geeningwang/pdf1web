import { useEffect, useState } from 'react'
import * as opentype from 'opentype.js'
import type { TtfTablesData, TtfTable } from '../api'

function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MiB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${n.toLocaleString()} B`
}

// Color map for well-known table tags
const TAG_COLORS: Record<string, string> = {
  'head': '#6ea8fe',
  'hhea': '#6ea8fe',
  'OS/2': '#6ea8fe',
  'name': '#6ea8fe',
  'post': '#6ea8fe',
  'cmap': '#57cc99',
  'glyf': '#f4a261',
  'loca': '#f4a261',
  'hmtx': '#a78bfa',
  'maxp': '#a78bfa',
  'kern': '#ffc857',
  'GSUB': '#e07a5f',
  'GPOS': '#e07a5f',
  'GDEF': '#e07a5f',
  'CFF ': '#f0a500',
  'DSIG': '#888',
  'cvt ': '#9bb5ce',
  'fpgm': '#9bb5ce',
  'prep': '#9bb5ce',
}

function tagColor(tag: string): string {
  return TAG_COLORS[tag] ?? '#6c757d'
}

interface StructBarProps {
  tables: TtfTable[]
  totalSize: number
}

function StructBar({ tables, totalSize }: StructBarProps) {
  // Show a proportional bar of table offsets + lengths
  const sorted = [...tables].sort((a, b) => a.offset - b.offset)
  return (
    <div className="ttf-struct-wrap">
      <div className="ttf-struct-bar">
        {sorted.map(t => (
          <div
            key={t.tag}
            className="ttf-struct-seg"
            style={{
              flex: Math.max(t.length, 1),
              backgroundColor: tagColor(t.tag),
            }}
            title={`${t.tag}\nOffset: 0x${t.offset.toString(16).toUpperCase()} | Length: ${fmtBytes(t.length)}\n${t.desc}`}
          />
        ))}
      </div>
      <div className="ttf-struct-legend">
        {sorted.map(t => (
          <span key={t.tag} className="ttf-legend-item">
            <span className="ttf-legend-dot" style={{ background: tagColor(t.tag) }} />
            {t.tag.trim()}
          </span>
        ))}
        <span className="ttf-legend-total">{fmtBytes(totalSize)} total</span>
      </div>
    </div>
  )
}

interface Props {
  data: TtfTablesData
}

// --- Glyph grid (exported so DetailPane can place it at top) ---

const CELL = 64   // cell size in px
const PAD  = 6    // padding inside the cell

interface GlyphCell {
  index: number
  name: string
  svgPath: string
  viewBox: string
  isEmpty: boolean
}

function buildGlyphCells(font: opentype.Font, limit: number): GlyphCell[] {
  const cells: GlyphCell[] = []
  const count = Math.min(font.glyphs.length, limit)
  for (let i = 0; i < count; i++) {
    const glyph = font.glyphs.get(i)
    const upm = font.unitsPerEm
    const ascender = font.ascender
    // canvas coords: origin at top-left, baseline at ascender fraction
    const scale = (CELL - PAD * 2) / upm
    const ox = PAD
    const oy = PAD + ascender * scale
    const path = glyph.getPath(ox, oy, (CELL - PAD * 2))
    const svgPath = path.toSVG(2)
    // extract just the d= attribute value
    const dMatch = svgPath.match(/d="([^"]*)"/)
    cells.push({
      index: i,
      name: glyph.name || `gid${i}`,
      svgPath: dMatch ? dMatch[1] : '',
      viewBox: `0 0 ${CELL} ${CELL}`,
      isEmpty: !dMatch || dMatch[1].trim() === '',
    })
  }
  return cells
}

export function GlyphGrid({ uploadId, num, gen }: { uploadId: string; num: number; gen: number }) {
  const [cells, setCells] = useState<GlyphCell[] | null>(null)
  const [total, setTotal] = useState(0)
  const [err, setErr] = useState<string | null>(null)
  const [showEmpty, setShowEmpty] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    setCells(null); setErr(null)
    fetch(`/api/ttf_raw/${uploadId}/${num}/${gen}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.arrayBuffer() })
      .then(buf => {
        const font = opentype.parse(buf)
        setTotal(font.glyphs.length)
        setCells(buildGlyphCells(font, font.glyphs.length))
      })
      .catch(e => setErr(String(e)))
  }, [uploadId, num, gen])

  if (err) return <div className="ttf-glyph-err">Failed to load font: {err}</div>
  if (!cells) return <div className="ttf-glyph-loading">Loading glyphs…</div>

  const filtered = cells.filter(c => {
    if (!showEmpty && c.isEmpty) return false
    if (search) return c.name.toLowerCase().includes(search.toLowerCase()) || String(c.index).includes(search)
    return true
  })

  return (
    <div className="ttf-glyph-section">
      <div className="ttf-glyph-toolbar">
        <span className="ttf-glyph-count">{total} glyphs total · {filtered.length} shown</span>
        <label className="ttf-glyph-toggle">
          <input type="checkbox" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)} />
          {' '}show empty
        </label>
        <input
          className="ttf-glyph-search"
          placeholder="filter by name or index…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>
      <div className="ttf-glyph-grid">
        {filtered.map(c => (
          <div key={c.index} className={`ttf-glyph-cell${c.isEmpty ? ' ttf-glyph-empty' : ''}`} title={`#${c.index} ${c.name}`}>
            <svg viewBox={c.viewBox} width={CELL} height={CELL}>
              {c.svgPath && <path d={c.svgPath} fill="currentColor" />}
            </svg>
            <span className="ttf-glyph-label">{c.index}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TtfTablesPane({ data }: Props) {
  return (
    <div className="ttf-pane">
      <div className="ttf-header">
        <div className="ttf-title">TrueType / OpenType Font</div>
        <div className="ttf-meta">
          <span className="ttf-meta-item"><b>Format:</b> {data.sfVersion}</span>
          <span className="ttf-meta-item"><b>Tables:</b> {data.num_tables}</span>
          <span className="ttf-meta-item"><b>Size:</b> {fmtBytes(data.total_size)}</span>
        </div>
      </div>

      <StructBar tables={data.tables} totalSize={data.total_size} />

      <div className="ttf-table-wrap">
        <table className="ttf-table">
          <thead>
            <tr>
              <th>Tag</th>
              <th>Offset</th>
              <th>Length</th>
              <th>Checksum</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.tables.map(t => (
              <tr key={t.tag}>
                <td>
                  <span className="ttf-tag-pill" style={{ borderColor: tagColor(t.tag) }}>
                    {t.tag.trim()}
                  </span>
                </td>
                <td className="ttf-mono">0x{t.offset.toString(16).toUpperCase().padStart(6, '0')}</td>
                <td className="ttf-mono">{fmtBytes(t.length)}</td>
                <td className="ttf-mono ttf-checksum">{t.checksum}</td>
                <td className="ttf-desc">{t.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

    </div>
  )
}
