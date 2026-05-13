import React, { useState } from 'react'
import type { ToUnicodeData, CMapEntry } from '../api'

const PAGE_SIZE = 100

function isRenderable(char: string): boolean {
  if (!char || char.length === 0) return false
  const cp = char.codePointAt(0) ?? 0
  if (cp < 0x20) return false
  if (cp >= 0xD800 && cp <= 0xDFFF) return false
  if (cp === 0xFFFD || cp === 0xFFFF) return false
  return true
}

interface Props {
  data: ToUnicodeData
  onJumpToObj?: (num: number, gen: number) => void
}

export default function ToUnicodePane({ data }: Props) {
  const [page, setPage] = useState(0)
  const [search, setSearch] = useState('')

  const filtered: CMapEntry[] = search.trim()
    ? data.mappings.filter(m =>
        m.src_hex.includes(search.toUpperCase()) ||
        m.dst_hex.includes(search.toUpperCase()) ||
        m.char === search ||
        m.code_point.toString() === search,
      )
    : data.mappings

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const rows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function handleSearch(e: React.ChangeEvent<HTMLInputElement>) {
    setSearch(e.target.value)
    setPage(0)
  }

  return (
    <div className="cmap-pane">
      <div className="cmap-header">
        <div className="cmap-title">ToUnicode CMap</div>
        <div className="cmap-meta">
          {data.cmap_name && <span className="cmap-meta-item"><b>Name:</b> {data.cmap_name}</span>}
          {data.registry && data.ordering && (
            <span className="cmap-meta-item"><b>CIDSystem:</b> {data.registry}-{data.ordering}</span>
          )}
          <span className="cmap-meta-item"><b>Total mappings:</b> {data.total_mappings.toLocaleString()}</span>
          {data.total_mappings > 2000 && (
            <span className="cmap-meta-item cmap-truncated">(showing first 2 000)</span>
          )}
        </div>
      </div>

      <div className="cmap-search-row">
        <input
          className="cmap-search"
          placeholder="Filter by CID hex, Unicode hex, or character…"
          value={search}
          onChange={handleSearch}
        />
        {search && (
          <span className="cmap-search-count">{filtered.length} match{filtered.length !== 1 ? 'es' : ''}</span>
        )}
      </div>

      <div className="cmap-table-wrap">
        <table className="cmap-table">
          <thead>
            <tr>
              <th>CID (hex)</th>
              <th>CID (dec)</th>
              <th>Unicode (hex)</th>
              <th>Code point</th>
              <th>Glyph</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(m => (
              <tr key={m.src_int}>
                <td className="cmap-mono">{m.src_hex}</td>
                <td className="cmap-mono">{m.src_int}</td>
                <td className="cmap-mono">{m.dst_hex}</td>
                <td className="cmap-mono">U+{m.code_point.toString(16).toUpperCase().padStart(4, '0')}</td>
                <td className="cmap-glyph">{isRenderable(m.char) ? m.char : <span className="cmap-nochar">—</span>}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={5} className="cmap-empty">No mappings found</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="cmap-pagination">
          <button
            className="cmap-page-btn"
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
          >‹ Prev</button>
          <span className="cmap-page-info">Page {page + 1} / {totalPages}</span>
          <button
            className="cmap-page-btn"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => p + 1)}
          >Next ›</button>
        </div>
      )}
    </div>
  )
}
