import React, { useEffect, useMemo, useState } from 'react'
import type { Type0FontPaneData } from '../api'
import { fontStyleForPdf, detectFont, type ResolvedFont } from '../fontUtils'

// ---------------------------------------------------------------------------
// CMap character gallery — shows only mapped entries as readable cards
// ---------------------------------------------------------------------------
interface CmapGalleryProps {
  cmap: Record<string, string>
  fontFamily: string
  fontWeight: string
  fontStyle: string
}

function CmapGallery({ cmap, fontFamily, fontWeight, fontStyle }: CmapGalleryProps) {
  const entries = useMemo(() => {
    return Object.entries(cmap)
      .map(([k, v]) => ({ code: parseInt(k, 10), char: v }))
      .filter(e => !isNaN(e.code) && e.char !== '')
      .sort((a, b) => a.code - b.code)
  }, [cmap])

  if (entries.length === 0) {
    return <div className="t0fp-cmap-empty">No ToUnicode mapping available</div>
  }

  return (
    <div className="t0fp-gallery">
      {entries.map(({ code, char }) => {
        const cp = char.codePointAt(0)!
        const isPrint = cp >= 0x20
        const cidHex = code.toString(16).toUpperCase().padStart(4, '0')
        const uniHex = cp.toString(16).toUpperCase().padStart(4, '0')
        return (
          <div
            key={code}
            className="t0fp-glyph-card"
            title={`CID 0x${cidHex} → U+${uniHex}`}
          >
            <div className="t0fp-glyph-char" style={{ fontFamily, fontWeight, fontStyle }}>{isPrint ? char : '·'}</div>
            <div className="t0fp-glyph-uni">U+{uniHex}</div>
            <div className="t0fp-glyph-cid">{cidHex}</div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
interface Props {
  data: Type0FontPaneData
  onJumpToObj?: (num: number, gen: number) => void
}

const Type0FontPane: React.FC<Props> = ({ data, onJumpToObj }) => {
  const cmapCount = Object.keys(data.cmap).length
  const cidSystemLabel = [data.cid_registry, data.cid_ordering, data.cid_supplement != null ? String(data.cid_supplement) : null]
    .filter(Boolean).join('-')

  const fontStyle = useMemo(() => fontStyleForPdf(data.cid_base_font ?? data.base_font), [data.cid_base_font, data.base_font])
  const [resolvedFont, setResolvedFont] = useState<ResolvedFont | null>(null)
  useEffect(() => { setResolvedFont(detectFont(fontStyle)) }, [fontStyle])

  return (
    <div className="sfp-root">
      <div className="font-pane-name-title" style={{ fontFamily: fontStyle.family }}>{data.base_font ?? '(unnamed)'}</div>
      {resolvedFont && (
        <div className="font-pane-rendered-as" style={{ fontFamily: fontStyle.family }}>rendered as {resolvedFont.family}</div>
      )}
      {/* Header */}
      <div className="sfp-header">
        <div className="sfp-meta-row">
          <span className="sfp-badge sfp-badge--subtype">Type0</span>
          {data.cid_subtype && (
            <span className="sfp-badge sfp-badge--cidsubtype">{data.cid_subtype}</span>
          )}
          {data.encoding && (
            <span className="sfp-badge sfp-badge--encoding">{data.encoding}</span>
          )}
          <span className={`sfp-badge ${data.is_embedded ? 'sfp-badge--embedded' : 'sfp-badge--notembedded'}`}>
            {data.is_embedded ? 'Embedded' : 'Not Embedded'}
          </span>
          {data.descendant_num != null && onJumpToObj && (
            <button className="sfp-jump-btn" onClick={() => onJumpToObj(data.descendant_num!, 0)}>
              CIDFont #{data.descendant_num}
            </button>
          )}
          {data.font_descriptor_num != null && onJumpToObj && (
            <button className="sfp-jump-btn" onClick={() => onJumpToObj(data.font_descriptor_num!, 0)}>
              FontDescriptor #{data.font_descriptor_num}
            </button>
          )}
          {data.to_unicode_num != null && onJumpToObj && (
            <button className="sfp-jump-btn" onClick={() => onJumpToObj(data.to_unicode_num!, 0)}>
              ToUnicode #{data.to_unicode_num}
            </button>
          )}
        </div>

        {/* CIDSystemInfo */}
        {cidSystemLabel && (
          <div className="t0fp-cidsys-row">
            <span className="t0fp-cidsys-label">CIDSystemInfo</span>
            <span className="t0fp-cidsys-value">{cidSystemLabel}</span>
            {data.cid_registry && <span className="t0fp-cidsys-part"><span className="t0fp-cidsys-key">Registry</span> {data.cid_registry}</span>}
            {data.cid_ordering && <span className="t0fp-cidsys-part"><span className="t0fp-cidsys-key">Ordering</span> {data.cid_ordering}</span>}
            {data.cid_supplement != null && <span className="t0fp-cidsys-part"><span className="t0fp-cidsys-key">Supplement</span> {data.cid_supplement}</span>}
          </div>
        )}

        {/* Default width */}
        <div className="t0fp-dw-row">
          <span className="t0fp-dw-label">Default width</span>
          <span className="t0fp-dw-value">{data.default_width} / 1000 em</span>
        </div>
      </div>

      {/* CMap gallery */}
      <div className="sfp-section">
        <div className="sfp-section-title">
          ToUnicode Map
          <span className="sfp-section-subtitle">
            &nbsp;·&nbsp;{cmapCount} mapped code points
            {data.to_unicode_num == null && <>&nbsp;·&nbsp;<span className="sfp-note">no /ToUnicode stream</span></>}
          </span>
        </div>
        <CmapGallery cmap={data.cmap} fontFamily={fontStyle.family} fontWeight={fontStyle.weight} fontStyle={fontStyle.style} />
      </div>
    </div>
  )
}

export default Type0FontPane
