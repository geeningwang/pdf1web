import React, { useMemo, useState } from 'react'
import type { XRefData, XRefEntry } from '../api'

// ---------------------------------------------------------------------------
// File-layout strip — marks object positions proportionally across file size
// ---------------------------------------------------------------------------
interface LayoutStripProps {
  entries: XRefEntry[]
  fileSize: number
  onHover: (entry: XRefEntry | null) => void
  hoveredNum: number | null
}

function LayoutStrip({ entries, fileSize, onHover, hoveredNum }: LayoutStripProps) {
  const inUse = useMemo(
    () => entries.filter(e => e.etype === 'in_use').sort((a, b) => a.offset - b.offset),
    [entries],
  )
  if (fileSize <= 0 || inUse.length === 0) return null

  return (
    <div className="xrp-layout-wrap" title="File layout — each tick is an in-use object at its byte offset">
      <div className="xrp-layout-label">file layout</div>
      <div className="xrp-layout-bar">
        {inUse.map(e => {
          const pct = (e.offset / fileSize) * 100
          const isHov = hoveredNum === e.obj_num
          return (
            <div
              key={e.obj_num}
              className={`xrp-layout-tick${isHov ? ' xrp-layout-tick--hov' : ''}`}
              style={{ left: `${pct}%` }}
              onMouseEnter={() => onHover(e)}
              onMouseLeave={() => onHover(null)}
            />
          )
        })}
      </div>
      <div className="xrp-layout-ends">
        <span>0</span>
        <span>{fileSize.toLocaleString()} B</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Type badge
// ---------------------------------------------------------------------------
function EtypeBadge({ etype }: { etype: XRefEntry['etype'] }) {
  const cls = {
    in_use: 'xrp-badge--inuse',
    free: 'xrp-badge--free',
    compressed: 'xrp-badge--compressed',
  }[etype]
  const label = { in_use: 'in-use', free: 'free', compressed: 'compressed' }[etype]
  return <span className={`xrp-badge ${cls}`}>{label}</span>
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
type Filter = 'all' | 'in_use' | 'free' | 'compressed'
type SortCol = 'obj_num' | 'offset' | 'gen'
type SortDir = 'asc' | 'desc'

interface Props {
  data: XRefData
  onJumpToObj: (num: number) => void
}

const XRefPane: React.FC<Props> = ({ data, onJumpToObj }) => {
  const [filter, setFilter] = useState<Filter>('all')
  const [sortCol, setSortCol] = useState<SortCol>('obj_num')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [hoveredNum, setHoveredNum] = useState<number | null>(null)

  const filtered = useMemo(() => {
    const rows = filter === 'all' ? data.entries : data.entries.filter(e => e.etype === filter)
    return [...rows].sort((a, b) => {
      let av: number, bv: number
      if (sortCol === 'obj_num') { av = a.obj_num; bv = b.obj_num }
      else if (sortCol === 'gen') { av = a.gen; bv = b.gen }
      else { av = a.offset; bv = b.offset }
      return sortDir === 'asc' ? av - bv : bv - av
    })
  }, [data.entries, filter, sortCol, sortDir])

  function toggleSort(col: SortCol) {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortCol(col)
      setSortDir('asc')
    }
  }

  function SortArrow({ col }: { col: SortCol }) {
    if (sortCol !== col) return <span className="xrp-sort-arrow xrp-sort-arrow--inactive">⇅</span>
    return <span className="xrp-sort-arrow">{sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  const fileSizeLabel = data.file_size > 0
    ? data.file_size >= 1024 * 1024
      ? `${(data.file_size / (1024 * 1024)).toFixed(1)} MB`
      : data.file_size >= 1024
        ? `${(data.file_size / 1024).toFixed(1)} KB`
        : `${data.file_size} B`
    : null

  return (
    <div className="xrp-root">
      {/* Stats row */}
      <div className="xrp-stats-row">
        <span className="xrp-stat"><span className="xrp-stat-num">{data.total}</span> total</span>
        <span className="xrp-sep">·</span>
        <span className="xrp-stat xrp-stat--inuse"><span className="xrp-stat-num">{data.in_use}</span> in-use</span>
        <span className="xrp-sep">·</span>
        <span className="xrp-stat xrp-stat--free"><span className="xrp-stat-num">{data.free}</span> free</span>
        {data.compressed > 0 && <>
          <span className="xrp-sep">·</span>
          <span className="xrp-stat xrp-stat--compressed"><span className="xrp-stat-num">{data.compressed}</span> compressed</span>
        </>}
        {fileSizeLabel && <>
          <span className="xrp-sep">·</span>
          <span className="xrp-stat xrp-stat--size">{fileSizeLabel}</span>
        </>}
      </div>

      {/* File layout strip */}
      <LayoutStrip
        entries={data.entries}
        fileSize={data.file_size}
        onHover={e => setHoveredNum(e ? e.obj_num : null)}
        hoveredNum={hoveredNum}
      />

      {/* Filter buttons */}
      <div className="xrp-filter-row">
        {(['all', 'in_use', 'free', 'compressed'] as Filter[]).map(f => {
          const label = f === 'all' ? 'All' : f === 'in_use' ? 'In-use' : f === 'free' ? 'Free' : 'Compressed'
          if (f === 'compressed' && data.compressed === 0) return null
          if (f === 'free' && data.free === 0) return null
          return (
            <button
              key={f}
              className={`xrp-filter-btn${filter === f ? ' xrp-filter-btn--active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {label}
            </button>
          )
        })}
        <span className="xrp-filter-count">{filtered.length} {filtered.length === 1 ? 'entry' : 'entries'}</span>
      </div>

      {/* Table */}
      <div className="xrp-table-wrap">
        <table className="xrp-table">
          <thead>
            <tr>
              <th className="xrp-th xrp-th--obj" onClick={() => toggleSort('obj_num')}>
                Obj <SortArrow col="obj_num" />
              </th>
              <th className="xrp-th xrp-th--type">Type</th>
              <th className="xrp-th xrp-th--gen" onClick={() => toggleSort('gen')}>
                Gen <SortArrow col="gen" />
              </th>
              <th className="xrp-th xrp-th--offset" onClick={() => toggleSort('offset')}>
                Offset / Location <SortArrow col="offset" />
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(e => {
              const isHov = hoveredNum === e.obj_num
              const canJump = e.etype !== 'free'
              return (
                <tr
                  key={e.obj_num}
                  className={`xrp-row xrp-row--${e.etype}${isHov ? ' xrp-row--hov' : ''}${canJump ? ' xrp-row--clickable' : ''}`}
                  onMouseEnter={() => setHoveredNum(e.obj_num)}
                  onMouseLeave={() => setHoveredNum(null)}
                  onClick={canJump ? () => onJumpToObj(e.obj_num) : undefined}
                  title={canJump ? `Jump to object ${e.obj_num}` : undefined}
                >
                  <td className="xrp-td xrp-td--obj">{e.obj_num}</td>
                  <td className="xrp-td xrp-td--type"><EtypeBadge etype={e.etype} /></td>
                  <td className="xrp-td xrp-td--gen">{e.gen}</td>
                  <td className="xrp-td xrp-td--offset">
                    {e.etype === 'in_use' && (
                      <span title={`${e.offset} bytes from start of file`}>
                        <span className="xrp-offset-hex">0x{e.offset.toString(16).toUpperCase().padStart(6, '0')}</span>
                        <span className="xrp-offset-dec"> ({e.offset.toLocaleString()})</span>
                      </span>
                    )}
                    {e.etype === 'free' && (
                      <span className="xrp-offset-free">—</span>
                    )}
                    {e.etype === 'compressed' && (
                      <span className="xrp-offset-compressed">
                        ObjStm {e.stm_num}
                        {e.stm_index != null && <span className="xrp-offset-dec">, index {e.stm_index}</span>}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default XRefPane
