import React from 'react'
import type { ImageDetailData, JpegStructureSegment, CcittStructureSegment, FlatStructureSegment } from '../api'

// ------------------------------------------------------------------ helpers

function fmtBytes(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MiB (${n.toLocaleString()} B)`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB (${n.toLocaleString()} B)`
  return `${n.toLocaleString()} B`
}

function hex(n: number): string {
  return '0x' + n.toString(16).toUpperCase().padStart(4, '0')
}

// ------------------------------------------------------------------ structure bar

const JPEG_LEGEND = [
  { color: 'soi',     label: 'SOI/EOI' },
  { color: 'app',     label: 'APP segments' },
  { color: 'quant',   label: 'Quantization (DQT)' },
  { color: 'frame',   label: 'Frame header (SOF)' },
  { color: 'huffman', label: 'Huffman tables (DHT)' },
  { color: 'sos',     label: 'Scan header (SOS)' },
  { color: 'scan',    label: 'Compressed scan data' },
  { color: 'misc',    label: 'Other' },
]

function JpegStructBar({ segs, total }: { segs: JpegStructureSegment[]; total: number }) {
  return (
    <div>
      <div className="img-struct-bar">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`img-struct-seg img-seg-${s.color}`}
            style={{ flex: Math.max(s.size, 2) }}
            title={`${s.label}\n${hex(s.offset)} – ${hex(s.offset + s.size - 1)}  (${s.size} B)`}
          />
        ))}
      </div>
      <div className="img-struct-legend">
        {JPEG_LEGEND.filter(l => segs.some(s => s.color === l.color || (l.color === 'soi' && (s.color === 'soi' || s.color === 'eoi')))).map(l => (
          <span key={l.color} className="img-legend-item">
            <span className={`img-legend-dot img-seg-${l.color}`} />
            {l.label}
          </span>
        ))}
        <span className="img-legend-item img-legend-total">{total.toLocaleString()} B total</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ CCITT structure bar

const CCITT_LEGEND = [
  { color: 'ccitt', label: 'Compressed fax data' },
]

function CcittStructBar({ segs, total }: { segs: CcittStructureSegment[]; total: number }) {
  return (
    <div>
      <div className="img-struct-bar">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`img-struct-seg img-seg-${s.color}`}
            style={{ flex: Math.max(s.size, 2) }}
            title={`${s.label}\n(${s.size} B)`}
          />
        ))}
      </div>
      <div className="img-struct-legend">
        {CCITT_LEGEND.map(l => (
          <span key={l.color} className="img-legend-item">
            <span className={`img-legend-dot img-seg-${l.color}`} />
            {l.label}
          </span>
        ))}
        <span className="img-legend-item img-legend-total">{total.toLocaleString()} B compressed</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ FLAT structure bar

const FLAT_LEGEND = [
  { color: 'flat', label: 'Compressed pixel data (Deflate)' },
]

function FlatStructBar({ segs, total }: { segs: FlatStructureSegment[]; total: number }) {
  return (
    <div>
      <div className="img-struct-bar">
        {segs.map((s, i) => (
          <div
            key={i}
            className={`img-struct-seg img-seg-${s.color}`}
            style={{ flex: Math.max(s.size, 2) }}
            title={`${s.label}\n(${s.size} B)`}
          />
        ))}
      </div>
      <div className="img-struct-legend">
        {FLAT_LEGEND.map(l => (
          <span key={l.color} className="img-legend-item">
            <span className={`img-legend-dot img-seg-${l.color}`} />
            {l.label}
          </span>
        ))}
        <span className="img-legend-item img-legend-total">{total.toLocaleString()} B compressed</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ component

interface Props {
  data: ImageDetailData
  imageSrc?: string
  isThumb?: boolean
  imageError?: string | null
  onImageError?: (msg: string) => void
}

const ImagePane: React.FC<Props> = ({ data, imageSrc, isThumb, imageError, onImageError }) => {
  const { jpeg } = data
  const fi = jpeg?.frame_info

  // Build a one-line header summary
  const dims = data.width != null && data.height != null
    ? `${data.width}×${data.height} px`
    : null
  const bpcLabel = data.bits_per_component != null ? `${data.bits_per_component}-bit` : null
  const colorLabel = fi
    ? (['Grayscale', null, 'YCbCr', 'CMYK'][fi.components - 1] ?? `${fi.components} ch`)
    : (data.color_space ?? null)

  const headerParts = [dims, bpcLabel, colorLabel, data.filter].filter(Boolean)

  // Compression ratio
  const ratio = data.raw_size > 0 && data.decoded_size != null
    ? (data.decoded_size / data.raw_size).toFixed(1)
    : null

  const metaRows: Array<[string, string | null]> = [
    ['Width',        data.width  != null ? `${data.width} px` : null],
    ['Height',       data.height != null ? `${data.height} px` : null],
    ['Bits/channel', bpcLabel],
    ['Color space',  data.color_space],
    ['Filter',       data.filter],
    ['Raw (compressed)', fmtBytes(data.raw_size)],
    ['Decoded (pixels)', data.decoded_size != null ? fmtBytes(data.decoded_size) : null],
    ['Compression',  ratio != null ? `${ratio} : 1` : null],
  ]

  return (
    <div className="img-pane">
      <div className="img-fixed-top">
        {/* Header */}
        <div className="img-header">
          <span className="img-title">Image Properties</span>
          {headerParts.length > 0 && (
            <span className="img-subtitle"> · {headerParts.join('  ·  ')}</span>
          )}
        </div>

        {/* JPEG structure bar */}
        {jpeg && jpeg.structure.length > 0 && (
          <JpegStructBar segs={jpeg.structure} total={data.raw_size} />
        )}

        {/* CCITT structure bar */}
        {data.ccitt && data.ccitt.structure.length > 0 && (
          <CcittStructBar segs={data.ccitt.structure} total={data.ccitt.raw_size} />
        )}

        {/* FlateDecode structure bar */}
        {data.flat && data.flat.structure.length > 0 && (
          <FlatStructBar segs={data.flat.structure} total={data.flat.raw_size} />
        )}
      </div>

      <div className="img-scrollable-body">
        {/* Image preview */}
        {imageSrc && (
          <div className="img-preview-section">
            {isThumb && <div className="detail-thumb-label">Page Thumbnail</div>}
            {imageError
              ? <div className="detail-error">{imageError}</div>
              : <img
                  src={imageSrc}
                  alt={isThumb ? 'Page thumbnail' : 'XObject image'}
                  className="detail-image"
                  onError={() => onImageError?.('Image could not be rendered (unsupported pixel format or filter)')}
                />
            }
          </div>
        )}

        {/* Metadata table */}
        <div className="img-section-label">Properties</div>
      <div className="img-meta-table">
        {metaRows.filter(([, v]) => v != null).map(([k, v]) => (
          <div key={k} className="img-meta-row">
            <span className="img-meta-key">{k}</span>
            <span className="img-meta-val">{v}</span>
          </div>
        ))}
      </div>

      {/* JPEG segment table */}
      {jpeg && jpeg.segments.length > 0 && (
        <>
          <div className="img-section-label">JPEG Segments ({jpeg.segments.length})</div>
          <div className="img-segs-wrap">
            <table className="img-segs-table">
              <thead>
                <tr>
                  <th>Marker</th>
                  <th>Name</th>
                  <th>Offset</th>
                  <th>Size</th>
                  <th>Description / Summary</th>
                </tr>
              </thead>
              <tbody>
                {jpeg.segments.map((s, i) => (
                  <tr key={i} className={`img-segs-row img-seg-row-${s.color}`}>
                    <td className="img-td-marker">{s.marker || '—'}</td>
                    <td className="img-td-name">{s.name}</td>
                    <td className="img-td-addr">{hex(s.offset)}</td>
                    <td className="img-td-addr">{s.size}</td>
                    <td className="img-td-summary">
                      {s.summary ? `${s.desc} — ${s.summary}` : s.desc}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* CCITT parameter table */}
      {data.ccitt && (
        <>
          <div className="img-section-label">
            CCITTFaxDecode Parameters
            <span className="img-ccitt-standard"> · {data.ccitt.compression_name} ({data.ccitt.standard})</span>
          </div>
          <div className="img-segs-wrap">
            <table className="img-segs-table">
              <thead>
                <tr>
                  <th>DecodeParm</th>
                  <th>Value</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                {data.ccitt.params.map((p, i) => (
                  <tr key={i} className="img-segs-row img-seg-row-ccitt">
                    <td className="img-td-name">{p.key}</td>
                    <td className="img-td-addr">{p.value}</td>
                    <td className="img-td-summary">{p.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* FlateDecode parameter table */}
      {data.flat && (
        <>
          <div className="img-section-label">
            FlateDecode Parameters
            <span className="img-ccitt-standard"> · {data.flat.predictor_name}</span>
          </div>
          <div className="img-segs-wrap">
            <table className="img-segs-table">
              <thead>
                <tr>
                  <th>DecodeParm</th>
                  <th>Value</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                {data.flat.params.map((p, i) => (
                  <tr key={i} className="img-segs-row img-seg-row-flat">
                    <td className="img-td-name">{p.key}</td>
                    <td className="img-td-addr">{p.value}</td>
                    <td className="img-td-summary">{p.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      </div>
    </div>
  )
}

export default ImagePane
