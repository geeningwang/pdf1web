import { useState } from 'react'
import type { CidToGidData } from '../api'

// Parse the coverage hex bitmap to a Uint8Array
function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length >> 1)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  }
  return out
}

// Choose ROW and CELL adaptively so the heatmap is always legible.
// ROW: smallest power-of-2 >= sqrt(totalCids), clamped to [16, 256]
// CELL: fills ~512px wide, clamped to [2, 16]
function computeLayout(totalCids: number) {
  let row = 16
  while (row < 256 && row * row < totalCids) row *= 2
  const cell = Math.max(2, Math.min(16, Math.floor(512 / row)))
  return { row, cell }
}

interface HeatmapProps {
  coverageHex: string
  totalCids: number
}

function CoverageHeatmap({ coverageHex, totalCids }: HeatmapProps) {
  const bytes = hexToBytes(coverageHex)
  const { row: ROW, cell: CELL } = computeLayout(totalCids)
  const rows = Math.ceil(totalCids / ROW)
  const width  = ROW * CELL
  const height = rows * CELL

  // Tail padding: slots from totalCids up to rows*ROW are phantom (not real CIDs)
  const tailStart = totalCids % ROW  // position within last row where real CIDs end (0 = no tail)
  const hasTail = tailStart !== 0
  const tailY = (rows - 1) * CELL
  const tailX = tailStart * CELL
  const tailW = (ROW - tailStart) * CELL

  // Build list of mapped cells for SVG rects
  const rects: { x: number; y: number }[] = []
  for (let cid = 0; cid < totalCids; cid++) {
    if (bytes[cid >> 3] & (0x80 >> (cid & 7))) {
      rects.push({ x: (cid % ROW) * CELL, y: Math.floor(cid / ROW) * CELL })
    }
  }

  return (
    <div className="ctg-heatmap-wrap">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="ctg-heatmap-svg"
        style={{ display: 'block' }}
      >
        {/* background for valid CID area */}
        <rect width={width} height={height} fill="var(--bg3, #1a1a1a)" />
        {/* tail void — unused padding slots at end of last row */}
        {hasTail && (
          <rect x={tailX} y={tailY} width={tailW} height={CELL} fill="var(--ctg-void, #111)" />
        )}
        {/* mapped cells */}
        {rects.map((r, i) => (
          <rect key={i} x={r.x} y={r.y} width={CELL} height={CELL} fill="var(--accent, #58a6ff)" />
        ))}
      </svg>
      <div className="ctg-heatmap-legend">
        <span className="ctg-legend-dot ctg-dot-mapped" /> mapped
        <span className="ctg-legend-dot ctg-dot-empty" style={{ marginLeft: 12 }} /> unmapped
        {hasTail && <span className="ctg-legend-dot ctg-dot-void" style={{ marginLeft: 12 }} />}
        {hasTail && <span style={{ marginLeft: 2 }}>unused ({ROW - tailStart} padding slots)</span>}
      </div>
    </div>
  )
}

const PAGE = 200

interface Props {
  data: CidToGidData
}

export default function CidToGidPane({ data }: Props) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)

  const filtered = search
    ? data.entries.filter(e =>
        String(e.cid).includes(search) || String(e.gid).includes(search)
      )
    : data.entries

  const totalPages = Math.ceil(filtered.length / PAGE)
  const pageEntries = filtered.slice(page * PAGE, (page + 1) * PAGE)

  const density = data.total_cids > 0
    ? ((data.mapped_count / data.total_cids) * 100).toFixed(2)
    : '0'

  return (
    <div className="ctg-pane">
      <div className="ctg-header">
        <div className="ctg-title">CIDToGIDMap</div>
        <div className="ctg-meta">
          <span className="ctg-meta-item"><b>Total CID slots:</b> {data.total_cids.toLocaleString()}</span>
          <span className="ctg-meta-item"><b>Mapped:</b> {data.mapped_count.toLocaleString()}</span>
          <span className="ctg-meta-item"><b>Density:</b> {density}%</span>
        </div>
      </div>

      <CoverageHeatmap coverageHex={data.coverage_hex} totalCids={data.total_cids} />

      <div className="ctg-table-section">
        <div className="ctg-toolbar">
          <input
            className="ctg-search"
            placeholder="filter by CID or GID…"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(0) }}
          />
          {totalPages > 1 && (
            <span className="ctg-page-info">
              page {page + 1} / {totalPages}
              <button className="ctg-page-btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹</button>
              <button className="ctg-page-btn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>›</button>
            </span>
          )}
        </div>
        <table className="ctg-table">
          <thead>
            <tr>
              <th>#</th>
              <th>CID (hex)</th>
              <th>CID (dec)</th>
              <th>GID (hex)</th>
              <th>GID (dec)</th>
            </tr>
          </thead>
          <tbody>
            {pageEntries.map((e, i) => (
              <tr key={e.cid}>
                <td className="ctg-mono ctg-dim">{page * PAGE + i + 1}</td>
                <td className="ctg-mono">0x{e.cid.toString(16).toUpperCase().padStart(4, '0')}</td>
                <td className="ctg-mono">{e.cid}</td>
                <td className="ctg-mono">0x{e.gid.toString(16).toUpperCase().padStart(4, '0')}</td>
                <td className="ctg-mono">{e.gid}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="ctg-empty">No entries match the filter.</div>
        )}
      </div>
    </div>
  )
}
