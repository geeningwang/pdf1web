import type { FontDescriptorData } from '../api'

function fmt(v: number | null, unit = ''): string {
  if (v == null) return '—'
  return v.toFixed(3).replace(/\.?0+$/, '') + (unit ? ' ' + unit : '')
}

interface EmDiagramProps {
  ascent: number | null
  descent: number | null
  capHeight: number | null
  xHeight: number | null
}

function EmDiagram({ ascent, descent, capHeight, xHeight }: EmDiagramProps) {
  // Normalize to a 1000-unit em square for display
  const asc = ascent ?? 800
  const desc = descent ?? -200
  const cap = capHeight ?? null
  const xh = xHeight ?? null

  const totalH = asc - desc   // e.g. 1000
  const svgH = 220
  const svgW = 180
  const leftPad = 60
  const rightPad = 20
  const boxW = svgW - leftPad - rightPad

  function yOf(unit: number): number {
    return svgH - ((unit - desc) / totalH) * svgH
  }

  const baseline = yOf(0)
  const ascentY  = yOf(asc)
  const descentY = yOf(desc)
  const capY     = cap != null ? yOf(cap) : null
  const xhY      = xh  != null ? yOf(xh)  : null

  const lines: { y: number; label: string; color: string; dash?: string }[] = [
    { y: ascentY,  label: `Ascent ${fmt(ascent)}`,   color: '#6ea8fe' },
    { y: baseline, label: 'Baseline 0',               color: '#6c757d' },
    { y: descentY, label: `Descent ${fmt(descent)}`,  color: '#f4a261' },
  ]
  if (capY != null) lines.push({ y: capY, label: `CapHeight ${fmt(cap)}`, color: '#57cc99', dash: '4 2' })
  if (xhY != null) lines.push({ y: xhY,  label: `xHeight ${fmt(xh)}`,    color: '#a78bfa', dash: '4 2' })

  lines.sort((a, b) => a.y - b.y)

  const glyphCenterX = leftPad + boxW / 2

  return (
    <svg className="fd-em-diagram" viewBox={`0 0 ${svgW} ${svgH}`} width={svgW} height={svgH}>
      {/* Em box */}
      <rect x={leftPad} y={ascentY} width={boxW} height={descentY - ascentY}
            fill="rgba(255,255,255,0.04)" stroke="#444" strokeWidth="1" />

      {/* Horizontal metric lines */}
      {lines.map(l => (
        <g key={l.label}>
          <line
            x1={leftPad} y1={l.y} x2={leftPad + boxW} y2={l.y}
            stroke={l.color} strokeWidth="1" strokeDasharray={l.dash ?? ''} />
          <text
            x={leftPad - 4} y={l.y + 4}
            textAnchor="end" fill={l.color} fontSize="9" fontFamily="monospace">
            {l.label}
          </text>
        </g>
      ))}

      {/* Vertical center guide */}
      <line x1={glyphCenterX} y1={ascentY} x2={glyphCenterX} y2={descentY}
            stroke="#555" strokeWidth="0.5" strokeDasharray="2 3" />
    </svg>
  )
}

interface Props {
  data: FontDescriptorData
  onJumpToObj?: (num: number, gen: number) => void
}

const METRIC_ROWS: { key: keyof FontDescriptorData; label: string; unit?: string }[] = [
  { key: 'ascent',       label: 'Ascent',        unit: 'units' },
  { key: 'descent',      label: 'Descent',        unit: 'units' },
  { key: 'cap_height',   label: 'CapHeight',      unit: 'units' },
  { key: 'x_height',     label: 'XHeight',        unit: 'units' },
  { key: 'italic_angle', label: 'ItalicAngle',    unit: '°' },
  { key: 'stem_v',       label: 'StemV',          unit: 'units' },
  { key: 'stem_h',       label: 'StemH',          unit: 'units' },
  { key: 'font_weight',  label: 'FontWeight' },
  { key: 'missing_width',label: 'MissingWidth',   unit: 'units' },
]

export default function FontDescriptorPane({ data, onJumpToObj }: Props) {
  return (
    <div className="fd-pane">
      <div className="font-pane-name-title">{data.font_name ?? '(unnamed)'}</div>
      <div className="fd-title">FontDescriptor</div>

      <div className="fd-body">
        {/* Em-square diagram */}
        <div className="fd-diagram-col">
          <div className="fd-section-label">Em-square metrics</div>
          <EmDiagram
            ascent={data.ascent}
            descent={data.descent}
            capHeight={data.cap_height}
            xHeight={data.x_height}
          />
          {data.bbox && (
            <div className="fd-bbox">
              FontBBox [{data.bbox.join(', ')}]
            </div>
          )}
        </div>

        {/* Metrics table + flags */}
        <div className="fd-info-col">
          <div className="fd-section-label">Numeric metrics</div>
          <table className="fd-metrics-table">
            <tbody>
              {METRIC_ROWS.map(row => {
                const v = data[row.key] as number | null
                return (
                  <tr key={row.key}>
                    <td className="fd-metric-key">{row.label}</td>
                    <td className="fd-metric-val">{fmt(v, row.unit)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="fd-section-label" style={{ marginTop: 12 }}>
            Flags <span className="fd-flags-raw">0x{data.flags_raw.toString(16).toUpperCase().padStart(8, '0')}</span>
          </div>
          {data.flags.length === 0
            ? <div className="fd-no-flags">No flags set</div>
            : (
              <div className="fd-flags-list">
                {data.flags.map(f => (
                  <div key={f.bit} className="fd-flag-chip" title={f.desc}>
                    <span className="fd-flag-bit">bit {f.bit}</span>
                    <span className="fd-flag-name">{f.name}</span>
                  </div>
                ))}
              </div>
            )
          }

          {(data.font_file2_num != null || data.cidset_num != null) && (
            <div style={{ marginTop: 10 }}>
              {data.font_file2_num != null && (
                <button
                  className="fd-jump-btn"
                  onClick={() => onJumpToObj?.(data.font_file2_num!, 0)}
                >
                  → FontFile2 obj {data.font_file2_num}
                </button>
              )}
              {data.cidset_num != null && (
                <button
                  className="fd-jump-btn"
                  onClick={() => onJumpToObj?.(data.cidset_num!, 0)}
                >
                  → CIDSet obj {data.cidset_num}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
