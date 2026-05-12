import React, { useRef, useEffect } from 'react'
import type { IccData, IccSegment } from '../api'

// ------------------------------------------------------------------ helpers

type RGB = [number, number, number]

function rgbStr(c: RGB | null, fallback = '#888888'): string {
  if (!c) return fallback
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

function addRgb(a: RGB, b: RGB): RGB {
  return [Math.min(255, a[0] + b[0]), Math.min(255, a[1] + b[1]), Math.min(255, a[2] + b[2])]
}

function fmt(v: number, d = 4): string {
  return v.toFixed(d)
}

function hex(n: number): string {
  return '0x' + n.toString(16).toUpperCase().padStart(4, '0')
}

// ------------------------------------------------------------------ TRC canvas

function drawTrc(
  canvas: HTMLCanvasElement,
  r: number[] | null,
  g: number[] | null,
  b: number[] | null,
) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const W = canvas.width
  const H = canvas.height

  ctx.fillStyle = '#12121f'
  ctx.fillRect(0, 0, W, H)

  // Grid
  ctx.strokeStyle = '#2a2a4a'
  ctx.lineWidth = 1
  for (let i = 0; i <= 4; i++) {
    const x = Math.round((i / 4) * W) + 0.5
    const y = Math.round((i / 4) * H) + 0.5
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
  }

  // Linear reference diagonal
  ctx.strokeStyle = '#3a3a5a'
  ctx.lineWidth = 1
  ctx.setLineDash([4, 4])
  ctx.beginPath(); ctx.moveTo(0, H); ctx.lineTo(W, 0); ctx.stroke()
  ctx.setLineDash([])

  const allSame =
    r && g && b &&
    r.length === g.length && r.length === b.length &&
    r.every((v, i) => Math.abs(v - g![i]) < 0.001 && Math.abs(v - b![i]) < 0.001)

  const drawCurve = (pts: number[] | null, color: string) => {
    if (!pts || pts.length < 2) return
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.beginPath()
    pts.forEach((v, i) => {
      const x = (i / (pts.length - 1)) * W
      const y = H - v * H
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
    })
    ctx.stroke()
  }

  if (allSame) {
    drawCurve(r, '#9999dd')
  } else {
    drawCurve(r, 'rgba(255,90,90,0.95)')
    drawCurve(g, 'rgba(80,220,80,0.95)')
    drawCurve(b, 'rgba(90,150,255,0.95)')
  }

  ctx.fillStyle = '#666688'
  ctx.font = '9px monospace'
  ctx.fillText('0', 2, H - 2)
  ctx.fillText('1', W - 8, 10)
}

// ------------------------------------------------------------------ structure bar

const STRUCT_LEGEND = [
  { color: 'hdr',    label: 'Header' },
  { color: 'tagdir', label: 'Tag Dir' },
  { color: 'desc',   label: 'Descriptive' },
  { color: 'xyz',    label: 'Colorimetry' },
  { color: 'trc',    label: 'Tone Curves' },
  { color: 'tech',   label: 'Technical' },
]

function StructBar({ segs, total }: { segs: IccSegment[]; total: number }) {
  return (
    <div>
      <div className="icc-struct-bar">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`icc-struct-seg icc-seg-${s.color}`}
            style={{ flex: s.size }}
            title={`${s.label}\n${hex(s.offset)} – ${hex(s.offset + s.size - 1)}  (${s.size} B)`}
          />
        ))}
      </div>
      <div className="icc-struct-legend">
        {STRUCT_LEGEND.map(l => (
          <span key={l.color} className="icc-legend-item">
            <span className={`icc-legend-dot icc-seg-${l.color}`} />
            {l.label}
          </span>
        ))}
        <span className="icc-legend-item icc-legend-total">{total} B total</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ component

interface Props {
  icc: IccData
}

const IccPane: React.FC<Props> = ({ icc }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (canvasRef.current) {
      drawTrc(canvasRef.current, icc.trc.r, icc.trc.g, icc.trc.b)
    }
  }, [icc])

  const { primaries } = icc
  const rD = (primaries.r_display ?? [220, 50, 50]) as RGB
  const gD = (primaries.g_display ?? [50, 200, 50]) as RGB
  const bD = (primaries.b_display ?? [50, 100, 255]) as RGB

  const swatches: Array<{ label: string; color: RGB; title?: string }> = [
    { label: 'Black',   color: [0, 0, 0] },
    { label: 'Red',     color: rD,              title: `R (${rD[0]},${rD[1]},${rD[2]})` },
    { label: 'Green',   color: gD,              title: `G (${gD[0]},${gD[1]},${gD[2]})` },
    { label: 'Blue',    color: bD,              title: `B (${bD[0]},${bD[1]},${bD[2]})` },
    { label: 'Yellow',  color: addRgb(rD, gD) },
    { label: 'Magenta', color: addRgb(rD, bD) },
    { label: 'Cyan',    color: addRgb(gD, bD) },
    { label: 'White',   color: [255, 255, 255] },
  ]

  // Metadata rows to display
  const meta: Array<[string, string | null | undefined]> = [
    ['Class',        icc.profile_class],
    ['Copyright',    icc.copyright],
    ['Manufacturer', icc.manufacturer_desc],
    ['Device',       icc.device_model_desc],
    ['Technology',   icc.technology && icc.technology_name
                       ? `${icc.technology}  (${icc.technology_name})`
                       : (icc.technology_name ?? icc.technology)],
    ['Luminance',    icc.luminance_y != null ? `${icc.luminance_y} cd/m²` : null],
    ['Observer',     icc.observer],
    ['Illuminant',   icc.view_illuminant],
    ['View. cond.',  icc.viewing_conditions_desc],
  ]

  return (
    <div className="icc-pane">
      {/* Header */}
      <div className="icc-header">
        <span className="icc-name">{icc.description ?? 'ICC Profile'}</span>
        <span className="icc-meta"> · {icc.color_space} → {icc.pcs}</span>
      </div>

      {/* Structure map */}
      {icc.structure.length > 0 && (
        <StructBar segs={icc.structure} total={icc.total_size} />
      )}

      {/* Primaries / swatches */}
      <div className="icc-section-label">Primaries</div>
      <div className="icc-swatches">
        {swatches.map(s => (
          <div key={s.label} className="icc-swatch-wrap" title={s.title ?? s.label}>
            <div className="icc-swatch" style={{ background: rgbStr(s.color) }} />
            <div className="icc-swatch-label">{s.label}</div>
          </div>
        ))}
      </div>

      {primaries.r_xyz && primaries.g_xyz && primaries.b_xyz && (
        <div className="icc-xyz-table">
          {([['R', primaries.r_xyz], ['G', primaries.g_xyz], ['B', primaries.b_xyz]] as const).map(
            ([ch, xyz]) => (
              <div key={ch} className="icc-xyz-row">
                <span className="icc-xyz-ch">{ch}</span>
                <span>X={fmt((xyz as number[])[0])}</span>
                <span>Y={fmt((xyz as number[])[1])}</span>
                <span>Z={fmt((xyz as number[])[2])}</span>
              </div>
            )
          )}
          {icc.white_point && (
            <div className="icc-xyz-row">
              <span className="icc-xyz-ch" style={{ color: '#aaa' }}>W</span>
              <span>X={fmt(icc.white_point[0])}</span>
              <span>Y={fmt(icc.white_point[1])}</span>
              <span>Z={fmt(icc.white_point[2])}</span>
            </div>
          )}
          {icc.black_point && (
            <div className="icc-xyz-row">
              <span className="icc-xyz-ch" style={{ color: '#666' }}>K</span>
              <span>X={fmt(icc.black_point[0])}</span>
              <span>Y={fmt(icc.black_point[1])}</span>
              <span>Z={fmt(icc.black_point[2])}</span>
            </div>
          )}
        </div>
      )}

      {/* TRC */}
      <div className="icc-section-label">Tone Response Curve</div>
      <canvas ref={canvasRef} className="icc-trc-canvas" width={300} height={130} />
      {icc.trc_summary && <div className="icc-info">{icc.trc_summary}</div>}

      {/* Metadata */}
      <div className="icc-section-label">Metadata</div>
      <div className="icc-meta-table">
        {meta.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
          <div key={k} className="icc-meta-row">
            <span className="icc-meta-key">{k}</span>
            <span className="icc-meta-val">{v}</span>
          </div>
        ))}
      </div>

      {/* Tag directory */}
      <div className="icc-section-label">Tag Directory ({icc.tags_directory.length} entries)</div>
      <div className="icc-tags-wrap">
        <table className="icc-tags-table">
          <thead>
            <tr>
              <th>Tag</th>
              <th>Name</th>
              <th>Type</th>
              <th>Offset</th>
              <th>Size</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {icc.tags_directory.map((t, i) => (
              <tr key={i} className={`icc-tags-row icc-seg-row-${
                t.sig === 'rXYZ' || t.sig === 'gXYZ' || t.sig === 'bXYZ' || t.sig === 'wtpt' || t.sig === 'bkpt' || t.sig === 'lumi' ? 'xyz'
                : t.sig === 'rTRC' || t.sig === 'gTRC' || t.sig === 'bTRC' || t.sig === 'kTRC' ? 'trc'
                : t.sig === 'desc' || t.sig === 'cprt' || t.sig === 'dmnd' || t.sig === 'dmdd' || t.sig === 'vued' ? 'desc'
                : t.sig === 'tech' || t.sig === 'meas' || t.sig === 'view' ? 'tech'
                : 'other'
              }`}>
                <td className="icc-td-sig">{t.sig}</td>
                <td>{t.name}</td>
                <td className="icc-td-type">{t.type_sig.trim()}</td>
                <td className="icc-td-addr">{hex(t.offset)}</td>
                <td className="icc-td-addr">{t.size}</td>
                <td className="icc-td-summary">{t.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default IccPane
