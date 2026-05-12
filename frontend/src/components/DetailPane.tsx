import React, { useEffect, useState } from 'react'
import type { TreeNode } from '../api'
import { fetchObjectDetail, imageUrl } from '../api'

interface Props {
  node: TreeNode | null
  uploadId: string | null
}

const DetailPane: React.FC<Props> = ({ node, uploadId }) => {
  const [detail, setDetail] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // true = showing image, false = showing text detail
  const [showImage, setShowImage] = useState(false)
  // is_image can be updated after a lazy fetch (e.g. XRef Table entries)
  const [isImageResolved, setIsImageResolved] = useState(false)

  useEffect(() => {
    if (!node) {
      setDetail('')
      setError(null)
      setShowImage(false)
      setIsImageResolved(false)
      return
    }

    const isImageNode = node.is_image && node.obj_num >= 0 && uploadId !== null
    setIsImageResolved(isImageNode)

    // Auto-switch to image view when an image node is selected (mirrors Win32 behaviour)
    setShowImage(isImageNode)
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
        // Update is_image from server response (e.g. XRef Table entries where
        // tree-build time doesn't know the object type yet)
        if (resp.is_image && !isImageNode) {
          setIsImageResolved(true)
          setShowImage(true)
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
        {canShowImage && (
          <button
            className="btn-image-toggle"
            onClick={() => setShowImage(v => !v)}
          >
            {showImage ? '📄 Show Detail' : '🖼 View Image'}
          </button>
        )}
      </div>

      {loading && !showImage && <div className="detail-loading">Loading…</div>}
      {error && <div className="detail-error">{error}</div>}

      {!error && (
        <>
          {showImage && canShowImage ? (
            <div className="detail-image-wrap">
              <img
                src={imageUrl(uploadId!, node.obj_num, node.gen_num)}
                alt="XObject image"
                className="detail-image"
                onError={() => {
                  setError('Image could not be rendered (unsupported pixel format or filter)')
                  setShowImage(false)
                }}
              />
            </div>
          ) : (
            !loading && <pre className="detail-text">{detail}</pre>
          )}
        </>
      )}
    </div>
  )
}

export default DetailPane
