import React, { useRef, useState } from 'react'
import type { StoreFile, UploadResponse } from '../api'
import { listStore, openFromStore, storePdf } from '../api'

interface Props {
  filename: string | null
  version: string | null
  loading: boolean
  onFile: (file: File) => void
  onOpen: (resp: UploadResponse) => void
}

const Toolbar: React.FC<Props> = ({ filename, version, loading, onFile, onOpen }) => {
  const inputRef = useRef<HTMLInputElement>(null)
  const storeInputRef = useRef<HTMLInputElement>(null)

  const [storeOpen, setStoreOpen] = useState(false)
  const [storeFiles, setStoreFiles] = useState<StoreFile[]>([])
  const [storeLoading, setStoreLoading] = useState(false)
  const [storeError, setStoreError] = useState<string | null>(null)

  const handleClick = () => inputRef.current?.click()

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) {
      onFile(f)
      e.target.value = ''
    }
  }

  const handleStoreClick = () => storeInputRef.current?.click()

  const handleStoreChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    try {
      await storePdf(f)
    } catch (err) {
      alert(`Store failed: ${err}`)
    }
  }

  const handleOpenFromStore = async () => {
    setStoreError(null)
    setStoreLoading(true)
    setStoreOpen(true)
    try {
      const files = await listStore()
      setStoreFiles(files)
    } catch (err) {
      setStoreError(String(err))
    } finally {
      setStoreLoading(false)
    }
  }

  const handlePickStoreFile = async (f: StoreFile) => {
    setStoreOpen(false)
    try {
      const resp = await openFromStore(f.filename)
      onOpen(resp)
    } catch (err) {
      alert(`Open from store failed: ${err}`)
    }
  }

  return (
    <>
      <header className="toolbar">
        <div className="toolbar-left">
          <span className="toolbar-title">PDF Structure Analyzer</span>
        </div>
        <div className="toolbar-center">
          {filename && (
            <span className="toolbar-file">
              📄 {filename}
              {version && <span className="toolbar-version"> — PDF {version}</span>}
            </span>
          )}
        </div>
        <div className="toolbar-right">
          {loading && <span className="toolbar-spinner">Parsing…</span>}
          <button className="btn-open" onClick={handleClick} disabled={loading}>
            Open PDF
          </button>
          <button className="btn-store" onClick={handleStoreClick} disabled={loading}>
            Store
          </button>
          <button className="btn-store-open" onClick={handleOpenFromStore} disabled={loading}>
            Open from Store
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,application/pdf"
            style={{ display: 'none' }}
            onChange={handleChange}
          />
          <input
            ref={storeInputRef}
            type="file"
            accept=".pdf,application/pdf"
            style={{ display: 'none' }}
            onChange={handleStoreChange}
          />
        </div>
      </header>

      {storeOpen && (
        <div className="store-modal-overlay" onClick={() => setStoreOpen(false)}>
          <div className="store-modal" onClick={e => e.stopPropagation()}>
            <div className="store-modal-header">
              <span>Open from Store</span>
              <button className="btn-dismiss" onClick={() => setStoreOpen(false)}>✕</button>
            </div>
            <div className="store-modal-body">
              {storeLoading && <div className="store-modal-hint">Loading…</div>}
              {storeError && <div className="store-modal-error">{storeError}</div>}
              {!storeLoading && !storeError && storeFiles.length === 0 && (
                <div className="store-modal-hint">Store is empty. Use "Store" to add PDFs.</div>
              )}
              {!storeLoading && storeFiles.map(f => (
                <button
                  key={f.filename}
                  className="store-file-row"
                  onClick={() => handlePickStoreFile(f)}
                >
                  <span className="store-file-name">📄 {f.filename}</span>
                  <span className="store-file-size">{(f.size / 1024).toFixed(1)} KB</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default Toolbar
