import React, { useEffect, useMemo, useState } from 'react'
import type { FontPaneData } from '../api'

// ---------------------------------------------------------------------------
// Font family resolution (mirrors CsCanvasRenderer fontNameToStyle)
// ---------------------------------------------------------------------------
interface FontStyle {
  family: string
  weight: string
  style: string
}

function fontStyleForPdf(baseFontName: string | null): FontStyle {
  if (!baseFontName) return { family: 'serif', weight: 'normal', style: 'normal' }
  const s = baseFontName.toLowerCase().replace(/[,_\-\s]+/g, '')
  const bold   = s.includes('bold')
  const italic = s.includes('italic') || s.includes('oblique')
  let family: string
  if      (s.includes('timesnewroman') || s.includes('times'))   family = '"Times New Roman", Times, serif'
  else if (s.includes('helvetica') || s.includes('arial'))       family = 'Helvetica, Arial, sans-serif'
  else if (s.includes('courier'))                                 family = '"Courier New", Courier, monospace'
  else if (s.includes('symbol'))                                  family = 'Symbol, serif'
  else if (s.includes('dingbat') || s.includes('zapf'))          family = '"Zapf Dingbats", "Wingdings", serif'
  else if (s.includes('palatino'))                                family = 'Palatino, "Palatino Linotype", serif'
  else if (s.includes('garamond'))                                family = 'Garamond, "EB Garamond", serif'
  else if (s.includes('georgia'))                                 family = 'Georgia, serif'
  else if (s.includes('verdana'))                                 family = 'Verdana, sans-serif'
  else if (s.includes('trebuchet'))                               family = '"Trebuchet MS", sans-serif'
  else if (s.includes('futura'))                                  family = 'Futura, "Century Gothic", sans-serif'
  else                                                            family = 'serif'
  return { family, weight: bold ? 'bold' : 'normal', style: italic ? 'italic' : 'normal' }
}

// ---------------------------------------------------------------------------
// Width bar chart
// ---------------------------------------------------------------------------
interface WidthsChartProps {
  firstChar: number
  widths: number[]
  cmap: Record<string, string>
  fontStyle: FontStyle
}

function WidthsChart({ firstChar, widths, cmap, fontStyle }: WidthsChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const maxW = Math.max(...widths, 1)
  const BAR_H = 48
  const LABEL_H = 16
  const TOTAL_H = BAR_H + LABEL_H + 4

  return (
    <div className="sfp-widths-chart-wrap">
      <div className="sfp-widths-scroll">
        <svg
          className="sfp-widths-svg"
          width={widths.length * 14}
          height={TOTAL_H}
          viewBox={`0 0 ${widths.length * 14} ${TOTAL_H}`}
        >
          {widths.map((w, i) => {
            const code = firstChar + i
            const ch = cmap[String(code)] ?? ''
            const barH = Math.round((w / maxW) * BAR_H)
            const isHov = hover === i
            return (
              <g key={i}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <rect
                  x={i * 14 + 1} y={BAR_H - barH}
                  width={12} height={barH}
                  fill={isHov ? '#6ea8fe' : '#4c7dd4'}
                  rx={1}
                />
                <text
                  x={i * 14 + 7} y={TOTAL_H - 2}
                  textAnchor="middle"
                  fontSize="8"
                  fill={isHov ? '#ccc' : '#888'}
                  fontFamily={fontStyle.family}
                  fontWeight={fontStyle.weight}
                  fontStyle={fontStyle.style}
                >
                  {ch || '·'}
                </text>
                {isHov && (
                  <title>{`U+${code.toString(16).toUpperCase().padStart(2, '0')} "${ch}"  →  ${w} / 1000 em`}</title>
                )}
              </g>
            )
          })}
          {/* Zero baseline */}
          <line x1={0} y1={BAR_H} x2={widths.length * 14} y2={BAR_H}
                stroke="#444" strokeWidth="0.5" />
        </svg>
      </div>
      {hover !== null && (
        <div className="sfp-widths-tooltip">
          Code {firstChar + hover} (0x{(firstChar + hover).toString(16).toUpperCase().padStart(2, '0')})
          {' · '}"{cmap[String(firstChar + hover)] ?? ''}"
          {' · '}{widths[hover]} / 1000 em
          {' · '}{((widths[hover] / 1000) * 100).toFixed(1)}%
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Character grid
// ---------------------------------------------------------------------------
interface CharGridProps {
  cmap: Record<string, string>
  firstChar: number | null
  lastChar: number | null
  widths: number[] | null
  fontStyle: FontStyle
}

function CharGrid({ cmap, firstChar, lastChar, widths, fontStyle }: CharGridProps) {
  const lo = Math.floor((firstChar ?? 0x20) / 16) * 16
  const hi = Math.ceil(((lastChar ?? 0xFF) + 1) / 16) * 16
  const rows: number[] = []
  for (let r = lo; r < hi; r += 16) rows.push(r)

  const hasWidths = widths != null && firstChar != null
  const maxW = hasWidths ? Math.max(...widths!, 1) : 1000

  const colHexes = '0123456789ABCDEF'.split('')

  return (
    <div className="sfp-char-grid">
      {/* Column headers */}
      <div className="sfp-grid-corner" />
      {colHexes.map(h => (
        <div key={h} className="sfp-col-head">{h}</div>
      ))}

      {rows.map(rowStart => {
        const rowLabel = rowStart.toString(16).toUpperCase().padStart(2, '0').slice(0, -1) + 'x'
        return (
          <React.Fragment key={rowStart}>
            <div className="sfp-row-head">{rowLabel}</div>
            {colHexes.map((_, col) => {
              const code = rowStart + col
              const ch = cmap[String(code)]
              const isDefined = ch !== undefined && ch !== ''
              const isPrint = isDefined && code >= 0x20

              let widthFrac = 0
              if (hasWidths && firstChar != null && code >= firstChar && code <= (lastChar ?? Infinity)) {
                const wi = code - firstChar!
                if (wi >= 0 && wi < widths!.length) {
                  widthFrac = widths![wi] / maxW
                }
              }

              return (
                <div
                  key={col}
                  className={`sfp-cell${isDefined ? ' sfp-cell--defined' : ' sfp-cell--unmapped'}${!isPrint ? ' sfp-cell--ctrl' : ''}`}
                  title={isDefined
                    ? `0x${code.toString(16).toUpperCase().padStart(2, '0')} (${code}) → U+${ch.codePointAt(0)?.toString(16).toUpperCase().padStart(4, '0')} "${ch}"`
                    : `0x${code.toString(16).toUpperCase().padStart(2, '0')} (${code}) — unmapped`}
                >
                  <span className="sfp-cell-code">
                    {code.toString(16).toUpperCase().padStart(2, '0')}
                  </span>
                  <span
                    className="sfp-cell-glyph"
                    style={{
                      fontFamily: fontStyle.family,
                      fontWeight: fontStyle.weight,
                      fontStyle: fontStyle.style,
                    }}
                  >
                    {isPrint ? ch : (isDefined ? '·' : '')}
                  </span>
                  {hasWidths && widthFrac > 0 && (
                    <span
                      className="sfp-cell-wbar"
                      style={{ width: `${widthFrac * 100}%` }}
                    />
                  )}
                </div>
              )
            })}
          </React.Fragment>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
interface Props {
  data: FontPaneData
  onJumpToObj?: (num: number, gen: number) => void
}

interface ResolvedFont {
  family: string       // the matched font name
  isFallback: boolean  // true if not the first choice in the CSS stack
  boldSynthesized: boolean
  italicSynthesized: boolean
}

const GENERICS = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui'])

async function detectFont(fontStyle: FontStyle): Promise<ResolvedFont> {
  await document.fonts.ready

  const families = fontStyle.family
    .split(',')
    .map(f => f.trim().replace(/^"|"$|^'|'$/g, ''))

  for (let i = 0; i < families.length; i++) {
    const family = families[i]
    if (GENERICS.has(family.toLowerCase())) {
      return { family: `${family} (generic)`, isFallback: i > 0, boldSynthesized: false, italicSynthesized: false }
    }
    if (document.fonts.check(`1px "${family}"`)) {
      // Check if the specific bold/italic variant exists or will be synthesized
      const boldAvail   = fontStyle.weight === 'bold'   ? document.fonts.check(`bold 1px "${family}"`)   : true
      const italicAvail = fontStyle.style  === 'italic' ? document.fonts.check(`italic 1px "${family}"`) : true
      return {
        family,
        isFallback: i > 0,
        boldSynthesized: !boldAvail,
        italicSynthesized: !italicAvail,
      }
    }
  }
  return { family: families[families.length - 1], isFallback: true, boldSynthesized: false, italicSynthesized: false }
}

const SimpleFontPane: React.FC<Props> = ({ data, onJumpToObj }) => {
  const fontStyle = useMemo(() => fontStyleForPdf(data.base_font), [data.base_font])
  const [resolvedFont, setResolvedFont] = useState<ResolvedFont | null>(null)

  useEffect(() => {
    let cancelled = false
    detectFont(fontStyle).then(r => { if (!cancelled) setResolvedFont(r) })
    return () => { cancelled = true }
  }, [fontStyle])

  const sampleText = "The quick brown fox jumps over the lazy dog. 0123456789"

  return (
    <div className="sfp-root">
      {/* Header */}
      <div className="sfp-header">
        <span
          className="sfp-sample-preview"
          style={{
            fontFamily: fontStyle.family,
            fontWeight: fontStyle.weight,
            fontStyle: fontStyle.style,
          }}
        >
          {sampleText}
        </span>
        <div className="sfp-meta-row">
          <span className="sfp-font-name">{data.base_font ?? '(unnamed)'}</span>
          {data.subtype && (
            <span className="sfp-badge sfp-badge--subtype">{data.subtype}</span>
          )}
          {data.encoding && (
            <span className="sfp-badge sfp-badge--encoding">{data.encoding}</span>
          )}
          <span className={`sfp-badge ${data.is_embedded ? 'sfp-badge--embedded' : 'sfp-badge--notembedded'}`}>
            {data.is_embedded ? 'Embedded' : 'Not Embedded'}
          </span>
          {data.first_char != null && data.last_char != null && (
            <span className="sfp-badge sfp-badge--range">
              U+{data.first_char.toString(16).toUpperCase().padStart(2,'0')}
              –
              U+{data.last_char.toString(16).toUpperCase().padStart(2,'0')}
              &nbsp;({data.last_char - data.first_char + 1} chars)
            </span>
          )}
          {data.font_descriptor_num != null && onJumpToObj && (
            <button
              className="sfp-jump-btn"
              onClick={() => onJumpToObj(data.font_descriptor_num!, 0)}
            >
              FontDescriptor #{data.font_descriptor_num}
            </button>
          )}
          {data.to_unicode_num != null && onJumpToObj && (
            <button
              className="sfp-jump-btn"
              onClick={() => onJumpToObj(data.to_unicode_num!, 0)}
            >
              ToUnicode #{data.to_unicode_num}
            </button>
          )}
        </div>

        {/* Resolved font row */}
        <div className="sfp-resolved-row">
          <span className="sfp-resolved-label">rendered as</span>
          {resolvedFont == null ? (
            <span className="sfp-resolved-detecting">detecting…</span>
          ) : (
            <>
              <span className={`sfp-resolved-family${resolvedFont.isFallback ? ' sfp-resolved-family--fallback' : ''}`}>
                {resolvedFont.family}
              </span>
              {resolvedFont.isFallback && (
                <span className="sfp-resolved-note">
                  (fallback — "{data.base_font}" not found on this system)
                </span>
              )}
              {resolvedFont.boldSynthesized && (
                <span className="sfp-resolved-note sfp-resolved-note--warn">bold synthesized</span>
              )}
              {resolvedFont.italicSynthesized && (
                <span className="sfp-resolved-note sfp-resolved-note--warn">italic synthesized</span>
              )}
            </>
          )}
        </div>
      </div>

      {/* Widths chart */}
      {data.widths && data.first_char != null && (
        <div className="sfp-section">
          <div className="sfp-section-title">
            Character Widths
            <span className="sfp-section-subtitle">
              &nbsp;·&nbsp;{data.widths.length} entries from code {data.first_char} to {data.last_char}
              &nbsp;·&nbsp;units: 1/1000 em
            </span>
          </div>
          <WidthsChart
            firstChar={data.first_char}
            widths={data.widths}
            cmap={data.cmap}
            fontStyle={fontStyle}
          />
        </div>
      )}

      {/* Character grid */}
      <div className="sfp-section">
        <div className="sfp-section-title">
          Character Map
          <span className="sfp-section-subtitle">
            &nbsp;·&nbsp;{data.encoding ?? 'default encoding'}
            &nbsp;·&nbsp;{Object.keys(data.cmap).length} mapped code points
            {data.widths == null && <>&nbsp;·&nbsp;<span className="sfp-note">no /Widths — system font metrics used</span></>}
          </span>
        </div>
        <CharGrid
          cmap={data.cmap}
          firstChar={data.first_char}
          lastChar={data.last_char}
          widths={data.widths}
          fontStyle={fontStyle}
        />
      </div>
    </div>
  )
}

export default SimpleFontPane
