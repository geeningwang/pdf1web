import React, { useRef, useEffect } from 'react'
import type { IccData } from '../api'

// ------------------------------------------------------------------ helpers

type RGB = [number, number, number]

function rgbStr(c: RGB | null, fallback = '#888888'): string {
  if (!c) return fallback
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

function addRgb(a: RGB, b: RGB): RGB {
  return [Math.min(255, a[0] + b[0]), Math.min(255, a[1] + b[1]), Math.min(255, a[2] + b[2])]
}

function fmt(v: number): string {
  return v.toFixed(4)
}

// ------------------------------------------------------------------ canvas draw

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

  // Background
  ctx.fillStyle = '#12121f'
  ctx.fillRect(0, 0, W, H)

  // Grid (4×4 cells)
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

  // Axis labels
  ctx.fillStyle = '#666688'
  ctx.font = '9px monospace'
  ctx.fillText('0', 2, H - 2)
  ctx.fillText('1', W - 8, 10)
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

  const swatches: Array<{ label: string; color: RGB; sub?: string }> = [
    { label: 'Black', color: [0, 0, 0] },
    { label: 'Red',   color: rD, sub: `(${rD[0]},${rD[1]},${rD[2]})` },
    { label: 'Green', color: gD, sub: `(${gD[0]},${gD[1]},${gD[2]})` },
    { label: 'Blue',  color: bD, sub: `(${bD[0]},${bD[1]},${bD[2]})` },
    { label: 'Yellow',  color: addRgb(rD, gD) },
    { label: 'Magenta', color: addRgb(rD, bD) },
    { label: 'Cyan',    color: addRgb(gD, bD) },
    { label: 'White',   color: [255, 255, 255] },
  ]

  return (
    <div className="icc-pane">
      <div className="icc-header">
        <span className="icc-name">{icc.description ?? 'ICC Profile'}</span>
        <span className="icc-meta"> · {icc.color_space} → {icc.pcs}</span>
      </div>

      <div className="icc-section-label">Primaries</div>
      <div className="icc-swatches">
        {swatches.map(s => (
          <div key={s.label} className="icc-swatch-wrap" title={s.sub ?? s.label}>
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
        </div>
      )}

      <div className="icc-section-label">Tone Response Curve</div>
      <canvas ref={canvasRef} className="icc-trc-canvas" width={300} height={150} />

      {icc.white_point && (
        <div className="icc-info">
          White point: X={fmt(icc.white_point[0])} Y={fmt(icc.white_point[1])} Z={fmt(icc.white_point[2])}
        </div>
      )}
    </div>
  )
}

export default IccPane
