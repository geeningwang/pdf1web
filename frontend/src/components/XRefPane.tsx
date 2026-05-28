import React, { useMemo, useState } from 'react'
import type { XRefData, XRefEntry } from '../api'

// ---------------------------------------------------------------------------
// File-layout strip — proportional blocks showing allocation per object
// ---------------------------------------------------------------------------
interface LayoutStripProps {
  entries: XRefEntry[]
  fileSize: number
  onHover: (entry: XRefEntry | null) => void
  hoveredNum: number | null
}

interface LayoutSeg {
  kind: 'obj' | 'gap'
  entry?: XRefEntry
  size: number
  label: string
}

const LAYOUT_PALETTE = [
  '#4c7dd4', '#2e9e6b', '#c97a1a', '#7c52b2',
  '#b03e3e', '#1a8fa0', '#8a7c2e', '#5c7a3e',
  '#c94f8a', '#4a8080',
]

function LayoutStrip({ entries, fileSize, onHover, hoveredNum }: LayoutStripProps) {
  const segments = useMemo<LayoutSeg[]>(() => {
    const inUse = entries
      .filter(e => e.etype === 'in_use')
      .sort((a, b) => a.offset - b.offset)
    if (inUse.length === 0 || fileSize <= 0) return []

    const segs: LayoutSeg[] = []

    // PDF header / preamble before first object
    if (inUse[0].offset > 0) {
      segs.push({
        kind: 'gap',
        size: inUse[0].offset,
        label: `Header · ${inUse[0].offset} B`,
      })
    }

    for (let i = 0; i < inUse.length; i++) {
      const e = inUse[i]
      const nextOffset = i + 1 < inUse.length ? inUse[i + 1].offset : fileSize
      const size = nextOffset - e.offset
      const hex = (n: number) => '0x' + n.toString(16).toUpperCase().padStart(6, '0')
      segs.push({
        kind: 'obj',
        entry: e,
        size,
        label: `Obj ${e.obj_num} gen ${e.gen} · ${hex(e.offset)}–${hex(nextOffset - 1)} · ${size.toLocaleString()} B`,
      })
    }

    return segs
  }, [entries, fileSize])

  if (segments.length === 0) return null

  let colorIdx = 0

  return (
    <div className="xrp-layout-wrap">
      <div className="xrp-layout-label">file layout — allocation per object</div>
      <div className="xrp-layout-bar">
        {segments.map((seg, i) => {
          if (seg.kind === 'gap') {
            return (
              <div
                key={`gap-${i}`}
                className="xrp-layout-seg xrp-layout-seg--gap"
                style={{ flex: Math.max(seg.size, 1) }}
                title={seg.label}
              />
            )
          }
          const color = LAYOUT_PALETTE[colorIdx % LAYOUT_PALETTE.length]
          const isHov = hoveredNum === seg.entry!.obj_num
          colorIdx++
          return (
            <div
              key={`obj-${seg.entry!.obj_num}`}
              className={`xrp-layout-seg xrp-layout-seg--obj${isHov ? ' xrp-layout-seg--hov' : ''}`}
              style={{ flex: Math.max(seg.size, 1), background: color }}
              onMouseEnter={() => onHover(seg.entry!)}
              onMouseLeave={() => onHover(null)}
              title={seg.label}
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
type SortCol = 'obj_num' | 'offset' | 'gen' | 'size_bytes' | 'kind'
type SortDir = 'asc' | 'desc'

function fmtSize(bytes: number | undefined): string {
  if (bytes == null || bytes <= 0) return '—'
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes.toLocaleString()} B`
}

function KindCell({ e }: { e: XRefEntry }) {
  if (!e.kind || e.etype === 'free') return <span className="xrp-kind-none">—</span>
  // Derive CSS key from the base label (before any parenthetical), lowercased, spaces→hyphens
  const cssKey = e.kind.replace(/\s*\(.*$/, '').trim().toLowerCase().replace(/\s+/g, '-')
  return (
    <span className={`xrp-kind-label xrp-kind--${cssKey}`}>{e.kind}</span>
  )
}

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
      if (sortCol === 'kind') {
        const ak = a.kind ?? ''
        const bk = b.kind ?? ''
        const cmp = ak.localeCompare(bk)
        return sortDir === 'asc' ? cmp : -cmp
      }
      let av: number, bv: number
      if (sortCol === 'obj_num') { av = a.obj_num; bv = b.obj_num }
      else if (sortCol === 'gen') { av = a.gen; bv = b.gen }
      else if (sortCol === 'size_bytes') { av = a.size_bytes ?? 0; bv = b.size_bytes ?? 0 }
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
              <th className="xrp-th xrp-th--type">XRef</th>
              <th className="xrp-th xrp-th--kind" onClick={() => toggleSort('kind')}>
                Object Type <SortArrow col="kind" />
              </th>
              <th className="xrp-th xrp-th--gen" onClick={() => toggleSort('gen')}>
                Gen <SortArrow col="gen" />
              </th>
              <th className="xrp-th xrp-th--offset" onClick={() => toggleSort('offset')}>
                Offset / Location <SortArrow col="offset" />
              </th>
              <th className="xrp-th xrp-th--size" onClick={() => toggleSort('size_bytes')}>
                Size <SortArrow col="size_bytes" />
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(e => {
              const isHov = hoveredNum === e.obj_num
              const canJump = true
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
                  <td className="xrp-td xrp-td--kind"><KindCell e={e} /></td>
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
                  <td className="xrp-td xrp-td--size" title={e.size_bytes ? `${e.size_bytes.toLocaleString()} bytes` : undefined}>{fmtSize(e.size_bytes)}</td>
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
