import type { CidSetData } from '../api'

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length >> 1)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  }
  return out
}

// Same adaptive layout as CidToGidPane
function computeLayout(totalSlots: number) {
  let row = 16
  while (row < 256 && row * row < totalSlots) row *= 2
  const cell = Math.max(2, Math.min(16, Math.floor(512 / row)))
  return { row, cell }
}

interface Props {
  data: CidSetData
}

export default function CidSetPane({ data }: Props) {
  const bytes = hexToBytes(data.coverage_hex)
  const totalSlots = data.total_slots
  const { row: ROW, cell: CELL } = computeLayout(totalSlots)
  const rows = Math.ceil(totalSlots / ROW)
  const width  = ROW * CELL
  const height = rows * CELL

  // Tail padding: bits beyond the real slot count (last partial byte)
  const tailStart = totalSlots % ROW
  const hasTail = tailStart !== 0
  const tailY = (rows - 1) * CELL
  const tailX = tailStart * CELL
  const tailW = (ROW - tailStart) * CELL

  // Collect ABSENT cells (bit = 0) within valid range — highlight the gaps
  const absentRects: { x: number; y: number }[] = []
  for (let cid = 0; cid < totalSlots; cid++) {
    if (!(bytes[cid >> 3] & (0x80 >> (cid & 7)))) {
      absentRects.push({ x: (cid % ROW) * CELL, y: Math.floor(cid / ROW) * CELL })
    }
  }

  const density = totalSlots > 0
    ? ((data.present_count / totalSlots) * 100).toFixed(2)
    : '0'

  // Absent CIDs list (for sparse absence)
  const absentCids: number[] = []
  for (let cid = 0; cid < totalSlots; cid++) {
    if (!(bytes[cid >> 3] & (0x80 >> (cid & 7)))) {
      absentCids.push(cid)
    }
  }

  return (
    <div className="css-pane">
      <div className="css-header">
        <div className="css-title">CIDSet</div>
        <div className="css-meta">
          <span className="css-meta-item"><b>Total slots:</b> {totalSlots.toLocaleString()}</span>
          <span className="css-meta-item"><b>Present:</b> {data.present_count.toLocaleString()}</span>
          <span className="css-meta-item"><b>Absent:</b> {absentCids.length.toLocaleString()}</span>
          <span className="css-meta-item"><b>Highest CID:</b> {data.last_cid.toLocaleString()}</span>
          <span className="css-meta-item"><b>Coverage:</b> {density}%</span>
        </div>
      </div>

      {/* Heatmap: background = present (dense), highlighted cells = absent (gaps) */}
      <div className="css-heatmap-wrap">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          className="css-heatmap-svg"
          style={{ display: 'block' }}
        >
          {/* background = "present" color */}
          <rect width={width} height={height} fill="var(--accent-dim, #1e3a5f)" />
          {/* absent CID cells (gaps) */}
          {absentRects.map((r, i) => (
            <rect key={i} x={r.x} y={r.y} width={CELL} height={CELL} fill="var(--css-absent, #f87171)" />
          ))}
          {/* tail void — padding slots beyond last real CID */}
          {hasTail && (
            <rect x={tailX} y={tailY} width={tailW} height={CELL} fill="var(--ctg-void, #111)" />
          )}
        </svg>
        <div className="css-heatmap-legend">
          <span className="css-legend-dot css-dot-present" /> present
          <span className="css-legend-dot css-dot-absent" style={{ marginLeft: 12 }} /> absent
          {hasTail && <span className="css-legend-dot css-dot-void" style={{ marginLeft: 12 }} />}
          {hasTail && <span style={{ marginLeft: 2 }}>unused ({ROW - tailStart} padding slots)</span>}
        </div>
      </div>

      {/* Absent CIDs table — only show if count is manageable */}
      {absentCids.length === 0 && (
        <div className="css-all-present">All CID slots are marked as present.</div>
      )}
      {absentCids.length > 0 && absentCids.length <= 2000 && (
        <div className="css-absent-section">
          <div className="css-absent-title">Absent CIDs ({absentCids.length})</div>
          <div className="css-absent-chips">
            {absentCids.map(cid => (
              <span key={cid} className="css-absent-chip">
                {cid} <span className="css-chip-hex">(0x{cid.toString(16).toUpperCase().padStart(4, '0')})</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {absentCids.length > 2000 && (
        <div className="css-absent-section">
          <div className="css-absent-title">Absent CIDs ({absentCids.length.toLocaleString()} — too many to list)</div>
        </div>
      )}
    </div>
  )
}
