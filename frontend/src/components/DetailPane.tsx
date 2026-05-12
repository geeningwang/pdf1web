import React, { useEffect, useState } from 'react'
import type { TreeNode, IccData, ImageDetailData, ContentStreamData, PaletteData } from '../api'
import { fetchObjectDetail, fetchIccProfile, fetchImageDetail, fetchContentStream, fetchPaletteData, imageUrl } from '../api'
import IccPane from './IccPane'
import ImagePane from './ImagePane'
import ContentStreamPane from './ContentStreamPane'
import PalettePane from './PalettePane'

interface Props {
  node: TreeNode | null
  uploadId: string | null
  onJumpToObj: (num: number) => void
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

const DetailPane: React.FC<Props> = ({ node, uploadId, onJumpToObj }) => {
  const [detail, setDetail] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  const [isImageResolved, setIsImageResolved] = useState(false)
  const [iccData, setIccData] = useState<IccData | null>(null)
  const [imageDetail, setImageDetail] = useState<ImageDetailData | null>(null)
  const [contentStreamData, setContentStreamData] = useState<ContentStreamData | null>(null)
  const [paletteData, setPaletteData] = useState<PaletteData | null>(null)

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
    setImageError(null)
    setIccData(null)
    setImageDetail(null)
    setContentStreamData(null)
    setPaletteData(null)
    setError(null)

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
        setLoading(false)
        if (resp.is_image && !isImageNode) {
          setIsImageResolved(true)
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
        <span className="detail-node-label">{node.label}</span>
      </div>

      <div className="detail-body">
        {canShowImage && (
          <div className="detail-image-section">
            {imageError
              ? <div className="detail-error">{imageError}</div>
              : (
                <img
                  src={imageUrl(uploadId!, node.obj_num, node.gen_num)}
                  alt="XObject image"
                  className="detail-image"
                  onError={() =>
                    setImageError('Image could not be rendered (unsupported pixel format or filter)')
                  }
                />
              )
            }
          </div>
        )}

        {canShowImage && imageDetail && (
          <div className="detail-image-meta-section">
            <ImagePane data={imageDetail} />
          </div>
        )}

        {!canShowImage && iccData && (
          <div className="detail-icc-section">
            <IccPane icc={iccData} />
          </div>
        )}

        {!canShowImage && !iccData && contentStreamData && (
          <div className="detail-cs-section">
            <ContentStreamPane data={contentStreamData} />
          </div>
        )}

        {!canShowImage && !iccData && !contentStreamData && paletteData && (
          <div className="detail-palette-section">
            <PalettePane data={paletteData} />
          </div>
        )}

        <div className="detail-text-section">
          {loading && <div className="detail-loading">Loading…</div>}
          {error && <div className="detail-error">{error}</div>}
          {!loading && !error && <pre className="detail-text">{renderDetail(detail, onJumpToObj)}</pre>}
        </div>
      </div>
    </div>
  )
}

export default DetailPane
