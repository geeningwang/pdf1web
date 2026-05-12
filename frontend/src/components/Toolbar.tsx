import React, { useRef } from 'react'

interface Props {
  filename: string | null
  version: string | null
  loading: boolean
  onFile: (file: File) => void
}

const Toolbar: React.FC<Props> = ({ filename, version, loading, onFile }) => {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleClick = () => inputRef.current?.click()

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) {
      onFile(f)
      // Reset so the same file can be re-selected
      e.target.value = ''
    }
  }

  return (
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
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          style={{ display: 'none' }}
          onChange={handleChange}
        />
      </div>
    </header>
  )
}

export default Toolbar
