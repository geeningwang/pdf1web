import React, { useEffect, useState } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import type { TreeNode, IccData, ImageDetailData, ContentStreamData, PaletteData, ToUnicodeData, FontDescriptorData, TtfTablesData, BackRefsData, CidToGidData, CidSetData, FontPaneData, XRefData, HintStreamData } from '../api'
import { fetchObjectDetail, fetchIccProfile, fetchImageDetail, fetchContentStream, fetchPaletteData, fetchToUnicode, fetchFontDescriptor, fetchTtfTables, fetchBackRefs, fetchCidToGid, fetchCidSet, fetchFontPane, fetchXRef, fetchHintStream, imageUrl } from '../api'
import IccPane from './IccPane'
import ImagePane from './ImagePane'
import ContentStreamPane from './ContentStreamPane'
import PalettePane from './PalettePane'
import ToUnicodePane from './ToUnicodePane'
import FontDescriptorPane from './FontDescriptorPane'
import TtfTablesPane from './TtfTablesPane'
import CidToGidPane from './CidToGidPane'
import CidSetPane from './CidSetPane'
import SimpleFontPane from './SimpleFontPane'
import XRefPane from './XRefPane'
import HintStreamPane from './HintStreamPane'

interface Props {
  node: TreeNode | null
  chain: TreeNode[]
  uploadId: string | null
  onJumpToObj: (num: number) => void
  onSelect: (node: TreeNode) => void
}

/** Parse detail text and turn every "N G R" reference into a clickable span. */
function renderDetail(text: string, onJump: (num: number) => void): React.ReactNode[] {
  const refRe = /\b(\d+)\s+(\d+)\s+R\b/g
  const result: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = refRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index))
    }
    const num = parseInt(match[1], 10)
    result.push(
      <span
        key={match.index}
        className="ref-link"
        title={`Jump to object ${num}`}
        onClick={() => onJump(num)}
      >
        {match[0]}
      </span>
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) result.push(text.slice(lastIndex))
  return result
}

const DetailPane: React.FC<Props> = ({ node, chain, uploadId, onJumpToObj, onSelect }) => {
  const [detail, setDetail] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  const [isImageResolved, setIsImageResolved] = useState(false)
  const [isThumb, setIsThumb] = useState(false)
  const [iccData, setIccData] = useState<IccData | null>(null)
  const [imageDetail, setImageDetail] = useState<ImageDetailData | null>(null)
  const [contentStreamData, setContentStreamData] = useState<ContentStreamData | null>(null)
  const [paletteData, setPaletteData] = useState<PaletteData | null>(null)
  const [toUnicodeData, setToUnicodeData] = useState<ToUnicodeData | null>(null)
  const [fontDescData, setFontDescData] = useState<FontDescriptorData | null>(null)
  const [ttfData, setTtfData] = useState<TtfTablesData | null>(null)
  const [cidToGidData, setCidToGidData] = useState<CidToGidData | null>(null)
  const [cidSetData, setCidSetData] = useState<CidSetData | null>(null)
  const [fontData, setFontData] = useState<FontPaneData | null>(null)
  const [xrefData, setXrefData] = useState<XRefData | null>(null)
  const [hintStreamData, setHintStreamData] = useState<HintStreamData | null>(null)
  const [backRefs, setBackRefs] = useState<BackRefsData | null>(null)
  const [typeLabel, setTypeLabel] = useState<string | null>(null)

  useEffect(() => {
    if (!node) {
      setDetail('')
      setError(null)
      setImageError(null)
      setIsImageResolved(false)
      return
    }

    const isImageNode = node.is_image && node.obj_num >= 0 && uploadId !== null
    setIsImageResolved(isImageNode)
    setIsThumb(false)
    setImageError(null)
    setIccData(null)
    setImageDetail(null)
    setContentStreamData(null)
    setPaletteData(null)
    setToUnicodeData(null)
    setFontDescData(null)
    setTtfData(null)
    setCidToGidData(null)
    setCidSetData(null)
    setFontData(null)
    setXrefData(null)
    setHintStreamData(null)
    setBackRefs(null)
    setTypeLabel(null)
    setError(null)

    // XRef Table node — fetch structured data (node has pre-filled detail text)
    if (node.label === 'XRef Table' && uploadId) {
      fetchXRef(uploadId).then(d => setXrefData(d))
    }

    // If the node already has detail text, use it directly
    if (node.detail) {
      setDetail(node.detail)
      return
    }

    // Otherwise fetch detail lazily from the backend
    if (!uploadId || node.obj_num < 0) {
      setDetail('(no detail available)')
      return
    }

    setLoading(true)
    fetchObjectDetail(uploadId, node.obj_num, node.gen_num)
      .then(resp => {
        setDetail(resp.detail)
        setTypeLabel(resp.type_label ?? null)
        setLoading(false)
        if (resp.is_image && !isImageNode) {
          setIsImageResolved(true)
        }
        if (resp.is_thumb) {
          setIsThumb(true)
        }
        if (resp.is_icc_profile) {
          fetchIccProfile(uploadId, node.obj_num, node.gen_num)
            .then(icc => setIccData(icc))
        }
        if (resp.is_image) {
          fetchImageDetail(uploadId, node.obj_num, node.gen_num)
            .then(d => setImageDetail(d))
        }
        if (resp.is_content_stream) {
          fetchContentStream(uploadId, node.obj_num, node.gen_num)
            .then(d => setContentStreamData(d))
        }
        if (resp.is_palette) {
          fetchPaletteData(uploadId, node.obj_num, node.gen_num)
            .then(d => setPaletteData(d))
        }
        if (resp.is_tounicode) {
          fetchToUnicode(uploadId, node.obj_num, node.gen_num)
            .then(d => setToUnicodeData(d))
        }
        if (resp.is_font_descriptor) {
          fetchFontDescriptor(uploadId, node.obj_num, node.gen_num)
            .then(d => setFontDescData(d))
        }
        if (resp.is_ttf) {
          fetchTtfTables(uploadId, node.obj_num, node.gen_num)
            .then(d => setTtfData(d))
        }
        if (resp.is_cid_to_gid_map) {
          fetchCidToGid(uploadId, node.obj_num, node.gen_num)
            .then(d => setCidToGidData(d))
        }
        if (resp.is_cid_set) {
          fetchCidSet(uploadId, node.obj_num, node.gen_num)
            .then(d => setCidSetData(d))
        }
        if (resp.is_font) {
          fetchFontPane(uploadId, node.obj_num, node.gen_num)
            .then(d => setFontData(d))
        }
        if (resp.is_hint_stream) {
          fetchHintStream(uploadId, node.obj_num, node.gen_num)
            .then(d => setHintStreamData(d))
        }
        // Always fetch back-references for any real object
        fetchBackRefs(uploadId, node.obj_num)
          .then(d => setBackRefs(d))
      })
      .catch(err => {
        setError(String(err))
        setLoading(false)
      })
  }, [node, uploadId])

  if (!node) {
    return (
      <div className="detail-pane detail-empty">
        <p>Select a node in the tree to view its details.</p>
      </div>
    )
  }

  const canShowImage = isImageResolved && node.obj_num >= 0 && uploadId !== null

  return (
    <div className="detail-pane">
      <div className="detail-header">
        {chain.length > 1 && (
          <div className="detail-breadcrumb">
            {chain.slice(0, -1).map((ancestor, i) => (
              <span key={i} className="detail-breadcrumb-item">
                <span
                  className="detail-breadcrumb-link"
                  title={ancestor.label}
                  onClick={() => onSelect(ancestor)}
                >
                  {ancestor.label}
                </span>
                <span className="detail-breadcrumb-sep">›</span>
              </span>
            ))}
          </div>
        )}
        <div className="detail-label-row">
          <span className="detail-node-label">{node.label}</span>
          {typeLabel && <span className="detail-type-badge">{typeLabel}</span>}
        </div>
        {backRefs && backRefs.refs.length > 0 && (
          <div className="detail-backrefs">
            <span className="detail-backrefs-label">referenced by</span>
            {backRefs.refs.map((r, i) => (
              <span key={i} className="detail-backref-item">
                {i > 0 && <span className="detail-backref-sep">,</span>}
                {r.from_num < 0 ? (
                  <span
                    className="detail-backref-link"
                    title={`Jump to ${r.type_name}`}
                    onClick={() => onJumpToObj(r.from_num)}
                  >
                    {r.type_name}
                    {r.key_path && <span className="detail-backref-via"> via {r.key_path}</span>}
                  </span>
                ) : (
                  <span
                    className="detail-backref-link"
                    title={`obj ${r.from_num} ${r.from_gen} R (${r.type_name}) via ${r.key_path}`}
                    onClick={() => onJumpToObj(r.from_num)}
                  >
                    obj {r.from_num}
                    <span className="detail-backref-via"> via {r.key_path}</span>
                  </span>
                )}
              </span>
            ))}
          </div>
        )}
      </div>

      <PanelGroup direction="vertical" autoSaveId="detail-vsplit" className="detail-body">
        <Panel defaultSize={55} minSize={10} className="detail-viz-panel">
          <div className="detail-viz-scroll">


            {canShowImage && imageDetail && (
              <div className="detail-image-meta-section">
                <ImagePane
                  data={imageDetail}
                  imageSrc={imageUrl(uploadId!, node.obj_num, node.gen_num)}
                  isThumb={isThumb}
                  imageError={imageError}
                  onImageError={msg => setImageError(msg)}
                />
              </div>
            )}

            {canShowImage && !imageDetail && (
              <div className="detail-image-section">
                {isThumb && (
                  <div className="detail-thumb-label">Page Thumbnail</div>
                )}
                {imageError
                  ? <div className="detail-error">{imageError}</div>
                  : (
                    <img
                      src={imageUrl(uploadId!, node.obj_num, node.gen_num)}
                      alt={isThumb ? 'Page thumbnail' : 'XObject image'}
                      className="detail-image"
                      onError={() =>
                        setImageError('Image could not be rendered (unsupported pixel format or filter)')
                      }
                    />
                  )
                }
              </div>
            )}

            {!canShowImage && iccData && (
              <div className="detail-icc-section">
                <IccPane icc={iccData} />
              </div>
            )}

            {!canShowImage && !iccData && contentStreamData && (
              <div className="detail-cs-section">
                <ContentStreamPane data={contentStreamData} uploadId={uploadId ?? undefined} />
              </div>
            )}

            {!canShowImage && !iccData && !contentStreamData && paletteData && (
              <div className="detail-palette-section">
                <PalettePane data={paletteData} />
              </div>
            )}

            {!canShowImage && toUnicodeData && (
              <div className="detail-cmap-section">
                <ToUnicodePane data={toUnicodeData} onJumpToObj={onJumpToObj} />
              </div>
            )}

            {!canShowImage && fontDescData && (
              <div className="detail-fd-section">
                <FontDescriptorPane data={fontDescData} onJumpToObj={onJumpToObj} />
              </div>
            )}

            {!canShowImage && ttfData && (
              <div className="detail-ttf-section">
                <TtfTablesPane
                  data={ttfData}
                  uploadId={uploadId ?? undefined}
                  num={node?.obj_num}
                  gen={node?.gen_num}
                />
              </div>
            )}

            {!canShowImage && cidToGidData && (
              <div className="detail-ctg-section">
                <CidToGidPane data={cidToGidData} />
              </div>
            )}

            {!canShowImage && cidSetData && (
              <div className="detail-css-section">
                <CidSetPane data={cidSetData} />
              </div>
            )}

            {!canShowImage && fontData && (
              <div className="detail-font-section">
                <SimpleFontPane data={fontData} onJumpToObj={(num, _gen) => onJumpToObj(num)} />
              </div>
            )}

            {hintStreamData && (
              <div className="detail-hs-section">
                <HintStreamPane data={hintStreamData} onJumpToObj={onJumpToObj} />
              </div>
            )}

            {xrefData && (
              <div className="detail-xref-section">
                <XRefPane data={xrefData} onJumpToObj={onJumpToObj} />
              </div>
            )}
          </div>
        </Panel>

        <PanelResizeHandle className="detail-resize-handle" />

        <Panel defaultSize={45} minSize={10} className="detail-dump-panel">
          <div className="detail-text-section">
            {loading && <div className="detail-loading">Loading…</div>}
            {error && <div className="detail-error">{error}</div>}
            {!loading && !error && <pre className="detail-text">{renderDetail(detail, onJumpToObj)}</pre>}
          </div>
        </Panel>
      </PanelGroup>
    </div>
  )
}

export default DetailPane
