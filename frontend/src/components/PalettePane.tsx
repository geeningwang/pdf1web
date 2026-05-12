import React from 'react'
import type { PaletteData } from '../api'

interface Props {
  data: PaletteData
}

function fmtBytes(n: number): string {
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB (${n} B)`
  return `${n} B`
}

const PalettePane: React.FC<Props> = ({ data }) => {
  return (
    <div className="palette-pane">
      <div className="palette-header">
        <span className="palette-title">Indexed Color Palette</span>
        <span className="palette-subtitle">
          {' '}· {data.entry_count} entr{data.entry_count === 1 ? 'y' : 'ies'}
          {'  ·  '}RGB  {'  ·  '}{fmtBytes(data.raw_size)}
        </span>
      </div>

      {/* Color swatches */}
      <div className="palette-swatches">
        {data.entries.map(e => (
          <div
            key={e.index}
            className="palette-swatch"
            style={{ background: e.hex }}
            title={`Index ${e.index}: ${e.hex.toUpperCase()}  rgb(${e.r}, ${e.g}, ${e.b})`}
          >
            <span
              className="palette-swatch-label"
              style={{ color: e.dark_bg ? '#fff' : '#000' }}
            >
              {e.index}
            </span>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="palette-table-wrap">
        <table className="palette-table">
          <thead>
            <tr>
              <th>Index</th>
              <th>Swatch</th>
              <th>Hex</th>
              <th>R</th>
              <th>G</th>
              <th>B</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map(e => (
              <tr key={e.index}>
                <td className="palette-td-index">{e.index}</td>
                <td>
                  <div
                    className="palette-table-swatch"
                    style={{ background: e.hex }}
                    title={e.hex}
                  />
                </td>
                <td className="palette-td-hex">{e.hex.toUpperCase()}</td>
                <td className="palette-td-channel">{e.r}</td>
                <td className="palette-td-channel">{e.g}</td>
                <td className="palette-td-channel">{e.b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default PalettePane
