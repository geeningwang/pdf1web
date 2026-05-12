import React from 'react'
import type { ContentStreamData, ContentStreamStructSeg } from '../api'

// ------------------------------------------------------------------ legend config

const CS_LEGEND = [
  { color: 'text-state', label: 'Text state' },
  { color: 'text-pos',   label: 'Text positioning' },
  { color: 'text-show',  label: 'Text show' },
  { color: 'gstate',     label: 'Graphics state' },
  { color: 'color',      label: 'Color' },
  { color: 'path',       label: 'Path' },
  { color: 'clip',       label: 'Clipping' },
  { color: 'xobject',    label: 'XObject' },
  { color: 'shading',    label: 'Shading' },
  { color: 'marked',     label: 'Marked content' },
  { color: 'inline',     label: 'Inline image' },
  { color: 'compat',     label: 'Compatibility' },
  { color: 'unknown',    label: 'Unknown' },
]

// ------------------------------------------------------------------ structure bar

function CsStructBar({ segs, total }: { segs: ContentStreamStructSeg[]; total: number }) {
  return (
    <div>
      <div className="cs-struct-bar">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`cs-struct-seg cs-seg-${s.color}`}
            style={{ flex: Math.max(s.size, 1) }}
            title={`${s.label}  —  ${s.count.toLocaleString()} operator${s.count !== 1 ? 's' : ''}`}
          />
        ))}
      </div>
      <div className="cs-struct-legend">
        {CS_LEGEND.filter(l => segs.some(s => s.color === l.color)).map(l => (
          <span key={l.color} className="cs-legend-item">
            <span className={`cs-legend-dot cs-seg-${l.color}`} />
            {l.label}
          </span>
        ))}
        <span className="cs-legend-item cs-legend-total">{total.toLocaleString()} ops total</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ category summary row

function CategorySummary({ counts }: { counts: Record<string, number> }) {
  const entries = CS_LEGEND.filter(l => {
    // map legend color back to category key
    const cat = l.color.replace(/-/g, '_').replace('text_show', 'text_show')
    return counts[cat] || counts[l.color.replace(/-/g, '_')] || counts[l.color]
  })
  if (entries.length === 0) return null
  return (
    <div className="cs-cat-summary">
      {CS_LEGEND
        .filter(l => {
          const key = l.color.replace(/-/g, '_')
          return (counts[key] ?? 0) > 0
        })
        .map(l => {
          const key = l.color.replace(/-/g, '_')
          return (
            <span key={l.color} className="cs-cat-chip">
              <span className={`cs-legend-dot cs-seg-${l.color}`} />
              {l.label}: <strong>{(counts[key] ?? 0).toLocaleString()}</strong>
            </span>
          )
        })}
    </div>
  )
}

// ------------------------------------------------------------------ component

const MAX_DISPLAY = 500  // rows shown in table; rest visible in text pane

interface Props {
  data: ContentStreamData
}

const ContentStreamPane: React.FC<Props> = ({ data }) => {
  const displayed = data.operations.slice(0, MAX_DISPLAY)
  const hiddenCount = data.total_ops - displayed.length

  return (
    <div className="cs-pane">
      {/* Header */}
      <div className="cs-header">
        <span className="cs-title">Content Stream</span>
        <span className="cs-subtitle">
          · {data.total_ops.toLocaleString()} operator{data.total_ops !== 1 ? 's' : ''}
          {data.truncated ? ` (parsed first ${data.operations.length.toLocaleString()})` : ''}
        </span>
      </div>

      {/* Structure bar */}
      {data.structure.length > 0 && (
        <CsStructBar segs={data.structure} total={data.total_ops} />
      )}

      {/* Category summary chips */}
      <CategorySummary counts={data.category_counts} />

      {/* Operator table */}
      <div className="cs-section-label">
        Operators
        {hiddenCount > 0 && (
          <span className="cs-table-note">
            {` (showing first ${MAX_DISPLAY.toLocaleString()} of ${data.total_ops.toLocaleString()} — full listing in text pane below)`}
          </span>
        )}
      </div>
      <div className="cs-ops-wrap">
        <table className="cs-ops-table">
          <thead>
            <tr>
              <th>Op</th>
              <th>Description</th>
              <th>Operands</th>
            </tr>
          </thead>
          <tbody>
            {displayed.map((op, i) => (
              <tr key={i} className={`cs-ops-row cs-row-${op.color}`}>
                <td className="cs-td-op">{op.op}</td>
                <td className="cs-td-desc">{op.desc}</td>
                <td className="cs-td-summary">{op.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default ContentStreamPane
