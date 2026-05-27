import { useState, useRef, useMemo } from 'react'
import type { HintStreamData, PageHintEntry } from '../api'

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function fmtOffset(n: number | null): string {
  if (n == null) return '—'
  return `0x${n.toString(16).toUpperCase()} (${n.toLocaleString()})`
}

interface StructBarProps {
  pageSize: number
  sharedSize: number
  totalDecoded: number
}

function StructBar({ pageSize, sharedSize, totalDecoded }: StructBarProps) {
  const total = totalDecoded || 1
  const pagePct = (pageSize / total) * 100
  const sharedPct = (sharedSize / total) * 100

  return (
    <div className="hs-structbar-wrap">
      <div className="hs-structbar">
        <div
          className="hs-structbar-seg hs-seg-page"
          style={{ width: `${pagePct}%` }}
          title={`Page Hints Table: ${fmtBytes(pageSize)}`}
        />
        <div
          className="hs-structbar-seg hs-seg-shared"
          style={{ width: `${sharedPct}%` }}
          title={`Shared Objects Hints Table: ${fmtBytes(sharedSize)}`}
        />
      </div>
      <div className="hs-legend">
        <span className="hs-legend-dot hs-dot-page" />
        <span className="hs-legend-label">Page Hints ({fmtBytes(pageSize)})</span>
        <span className="hs-legend-dot hs-dot-shared" style={{ marginLeft: 16 }} />
        <span className="hs-legend-label">Shared Objects Hints ({fmtBytes(sharedSize)})</span>
      </div>
    </div>
  )
}

const LIN_PARAM_ROWS: {
  key: keyof HintStreamData['lin_params']
  label: string
  pdfKey: string
  desc: string
  fmt?: (v: number) => string
}[] = [
  { key: 'num_pages',          label: 'Page count',            pdfKey: '/N', desc: 'Total number of pages in the document' },
  { key: 'file_length',        label: 'File length',           pdfKey: '/L', desc: 'Total length of the PDF file in bytes', fmt: fmtBytes },
  { key: 'first_page_obj',     label: 'First page object',     pdfKey: '/O', desc: "Object number of the first page's page object" },
  { key: 'end_of_first_page',  label: 'End of first page',     pdfKey: '/E', desc: 'Byte offset of the end of the first page section', fmt: fmtOffset },
  { key: 'main_xref_offset',   label: 'Main xref offset',      pdfKey: '/T', desc: 'Byte offset of the main cross-reference table', fmt: fmtOffset },
  { key: 'hint_offset',        label: 'Hint stream offset',    pdfKey: '/H[0]', desc: 'Byte offset of this hint stream in the file', fmt: fmtOffset },
  { key: 'hint_length',        label: 'Hint stream length',    pdfKey: '/H[1]', desc: 'Byte length of the hint stream in the file', fmt: fmtBytes },
]

// ---------------------------------------------------------------------------
// Page layout bar — proportional blocks per page section
// ---------------------------------------------------------------------------
const PAGE_PALETTE = [
  '#3b82f6', '#2e9e6b', '#c97a1a', '#7c52b2',
  '#b03e3e', '#1a8fa0', '#8a7c2e', '#5c7a3e',
  '#c94f8a', '#4a8080', '#6d7c2e', '#8a4a2e',
]

interface PageLayoutBarProps {
  pages: PageHintEntry[]
  fileLength: number
}

function PageLayoutBar({ pages, fileLength }: PageLayoutBarProps) {
  const [hovered, setHovered] = useState<number | null>(null)

  const segments = useMemo(() => {
    if (!fileLength || pages.length === 0) return []
    const sorted = pages
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => p.section_offset != null && p.page_length > 0)
      .sort((a, b) => a.p.section_offset! - b.p.section_offset!)
    if (sorted.length === 0) return []

    type Seg = { kind: 'page' | 'gap'; pageIdx?: number; start: number; end: number }
    const segs: Seg[] = []
    let prev = 0
    for (const { p, i } of sorted) {
      const start = p.section_offset!
      const end = start + p.page_length
      if (start > prev) segs.push({ kind: 'gap', start: prev, end: start })
      segs.push({ kind: 'page', pageIdx: i, start, end })
      prev = end
    }
    if (fileLength > prev) segs.push({ kind: 'gap', start: prev, end: fileLength })
    return segs
  }, [pages, fileLength])

  if (segments.length === 0) return null

  const hex = (n: number) => '0x' + n.toString(16).toUpperCase().padStart(6, '0')

  return (
    <div className="hs-layout-wrap">
      <div className="hs-layout-bar">
        {segments.map((seg, i) => {
          const size = seg.end - seg.start
          if (seg.kind === 'gap') {
            return (
              <div
                key={`gap-${i}`}
                className="hs-layout-seg hs-layout-seg--gap"
                style={{ flex: Math.max(size, 1) }}
                title={`Preamble / shared / xref: ${hex(seg.start)}–${hex(seg.end - 1)} (${size.toLocaleString()} B)`}
              />
            )
          }
          const pageIdx = seg.pageIdx!
          const color = PAGE_PALETTE[pageIdx % PAGE_PALETTE.length]
          const isHov = hovered === pageIdx
          return (
            <div
              key={`page-${pageIdx}`}
              className={`hs-layout-seg hs-layout-seg--page${isHov ? ' hs-layout-seg--hov' : ''}`}
              style={{ flex: Math.max(size, 1), background: color }}
              onMouseEnter={() => setHovered(pageIdx)}
              onMouseLeave={() => setHovered(null)}
              title={`Page ${pageIdx}: ${hex(seg.start)}–${hex(seg.end - 1)} (${size.toLocaleString()} B)`}
            />
          )
        })}
      </div>
      <div className="hs-layout-ends">
        <span>0</span>
        <span>{fileLength.toLocaleString()} B</span>
      </div>
    </div>
  )
}

interface Props {
  data: HintStreamData
  onJumpToObj: (num: number) => void
}

export default function HintStreamPane({ data, onJumpToObj }: Props) {
  const lp = data.lin_params
  const [groupFilter, setGroupFilter] = useState<string>('')
  const sharedSectionRef = useRef<HTMLDivElement>(null)
  const filterInputRef = useRef<HTMLInputElement>(null)

  const filteredIds = useMemo<Set<number> | null>(() => {
    const t = groupFilter.trim()
    if (!t) return null
    const ids = t.split(/[\s,]+/).map(Number).filter(n => Number.isInteger(n) && n >= 0)
    return ids.length > 0 ? new Set(ids) : null
  }, [groupFilter])

  const handlePageRowClick = (sharedIds: number[]) => {
    if (sharedIds.length === 0) return
    setGroupFilter(sharedIds.join(', '))
    setTimeout(() => {
      sharedSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      filterInputRef.current?.focus()
    }, 0)
  }

  return (
    <div className="hs-pane">
      <div className="hs-title">Linearization Hint Stream</div>

      {/* Stream size overview */}
      <div className="hs-section">
        <div className="hs-section-label">Stream size</div>
        <table className="hs-table">
          <tbody>
            <tr>
              <td className="hs-key">Compressed (raw)</td>
              <td className="hs-val">{fmtBytes(data.raw_size)}</td>
            </tr>
            <tr>
              <td className="hs-key">Decompressed</td>
              <td className="hs-val">{fmtBytes(data.decoded_size)}</td>
            </tr>
            <tr>
              <td className="hs-key">Compression ratio</td>
              <td className="hs-val">
                {data.decoded_size > 0
                  ? `${((1 - data.raw_size / data.decoded_size) * 100).toFixed(1)}%`
                  : '—'}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Structure bar */}
      {data.shared_offset != null && (
        <div className="hs-section">
          <div className="hs-section-label">Hint table layout</div>
          <StructBar
            pageSize={data.page_hints_size}
            sharedSize={data.shared_hints_size}
            totalDecoded={data.decoded_size}
          />
          <table className="hs-table" style={{ marginTop: 6 }}>
            <tbody>
              <tr>
                <td className="hs-key">Page hints table</td>
                <td className="hs-val">
                  bytes 0 – {data.shared_offset - 1} ({fmtBytes(data.page_hints_size)})
                </td>
              </tr>
              <tr>
                <td className="hs-key">Shared objects hints</td>
                <td className="hs-val">
                  bytes {data.shared_offset} – {data.decoded_size - 1} ({fmtBytes(data.shared_hints_size)})
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Page hints header */}
      {data.page_header && (
        <div className="hs-section">
          <div className="hs-section-label">Page hints table header</div>
          <table className="hs-table">
            <tbody>
              <tr><td className="hs-key">Min objects/page</td><td className="hs-val">{data.page_header.min_nobjects}</td></tr>
              <tr><td className="hs-key">First page offset</td><td className="hs-val">{fmtOffset(data.page_header.first_page_offset)}</td></tr>
              <tr><td className="hs-key">Min page length</td><td className="hs-val">{fmtBytes(data.page_header.min_page_length)}</td></tr>
              <tr><td className="hs-key">Bits for Δ page length</td><td className="hs-val">{data.page_header.nbits_delta_page_length}</td></tr>
              <tr><td className="hs-key">Bits for Δ obj count</td><td className="hs-val">{data.page_header.nbits_delta_nobjects}</td></tr>
              <tr><td className="hs-key" title="Acrobat always writes 0 (impl. note 126): not used for seeking">Min content stream offset</td><td className="hs-val">{data.page_header.min_co_offset === 0 ? '0 (not provided)' : fmtBytes(data.page_header.min_co_offset)}</td></tr>
              <tr><td className="hs-key" title="Acrobat always writes 0 (impl. note 126)">Bits for Δ content offset</td><td className="hs-val">{data.page_header.nbits_delta_co_offset}</td></tr>
              <tr><td className="hs-key" title="Acrobat always writes 0 here (impl. note 127); per-page content_length = delta_page_length, not the stream /Length">Min content stream length</td><td className="hs-val">{data.page_header.min_co_length === 0 ? '0 (not provided)' : fmtBytes(data.page_header.min_co_length)}</td></tr>
              <tr><td className="hs-key" title="Acrobat sets this equal to 'Bits for Δ page length' (impl. note 127)">Bits for Δ content length</td><td className="hs-val">{data.page_header.nbits_delta_co_length}</td></tr>
              <tr><td className="hs-key">Bits for shared ref count</td><td className="hs-val">{data.page_header.nbits_nshared}</td></tr>
              <tr><td className="hs-key">Bits for shared identifier</td><td className="hs-val">{data.page_header.nbits_shared_id}</td></tr>
              <tr><td className="hs-key">Fraction (num bits / denom)</td><td className="hs-val">{data.page_header.nbits_shared_num} bits / {data.page_header.shared_denom}</td></tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Per-page data */}
      {data.pages && data.pages.length > 0 && (
        <div className="hs-section">
          <div className="hs-section-label">Per-page data ({data.pages.length} pages)</div>
          {data.lin_params?.file_length && (
            <PageLayoutBar pages={data.pages} fileLength={data.lin_params.file_length} />
          )}
          <div className="hs-scroll-table-wrap">
            <table className="hs-table hs-page-table">
              <thead>
                <tr>
                  <th className="hs-th hs-th-num">Page</th>
                  <th className="hs-th hs-th-num">Section offset</th>
                  <th className="hs-th hs-th-num">Objects</th>
                  <th className="hs-th hs-th-num">Section length</th>
                  <th className="hs-th hs-th-num" title="Deduced from xref offsets: InUse objects whose file offset falls in this page's section range, minus shared group objects">Deduced objects</th>
                  <th className="hs-th hs-th-num" title="Acrobat always writes 0 (impl. note 126): not meaningful">Content offset</th>
                  <th className="hs-th hs-th-num" title="Acrobat: equals page_length − min_page_length, not the stream /Length (impl. note 127). Acrobat ignores this when reading.">Content length</th>
                  <th className="hs-th hs-th-num">Shared group IDs</th>
                </tr>
              </thead>
              <tbody>
                {data.pages.map((p, i) => {
                  const clickable = i !== 0 && p.nshared > 0
                  return (
                    <tr
                      key={i}
                      className={i % 2 === 0 ? 'hs-row-even' : 'hs-row-odd'}
                      style={clickable ? { cursor: 'pointer' } : undefined}
                      title={clickable ? 'Click to filter Shared groups table by these IDs' : undefined}
                      onClick={clickable ? () => handlePageRowClick(p.shared_ids) : undefined}
                    >
                      <td className="hs-td-num">{i}</td>
                      <td className="hs-td-num">{p.section_offset != null ? `@${p.section_offset}` : '—'}</td>
                      <td className="hs-td-num">{p.nobjects}</td>
                      <td className="hs-td-num">{fmtBytes(p.page_length)}</td>
                      <td className="hs-td hs-td-deduced">
                        {(() => {
                          const objs = p.deduced_objects ?? []
                          const count = objs.length
                          const mismatch = count !== p.nobjects
                          return (
                            <div className="hs-deduced-list">
                              {mismatch && (
                                <span
                                  className="hs-deduced-mismatch"
                                  title={`Count mismatch: deduced ${count} but hint table says ${p.nobjects}`}
                                >
                                  [{count}/{p.nobjects}]
                                </span>
                              )}
                              {count > 0
                                ? objs.map(o => (
                                  <button
                                    key={o.num}
                                    className="hs-obj-link"
                                    onClick={e => { e.stopPropagation(); onJumpToObj(o.num) }}
                                  >
                                    obj {o.num} <span className="hs-obj-type">[{o.obj_type}]</span>
                                  </button>
                                ))
                                : '\u2014'
                              }
                            </div>
                          )
                        })()}
                      </td>
                      <td className="hs-td-num"><span style={{ textDecoration: 'line-through' }}>{p.content_offset}</span></td>
                      <td className="hs-td-num"><span style={{ textDecoration: 'line-through' }}>{p.content_length}</span></td>
                      <td className="hs-td-num" style={{ fontFamily: 'monospace' }}>
                        {p.nshared === 0
                          ? '\u2014'
                          : i === 0
                            ? <span title="PDF spec requires page 0 to have no shared refs; pdlin/Acrobat fill garbage here">
                                {p.shared_ids.join(', ')} <span className="hs-badge-warn">spec violation</span>
                              </span>
                            : <span>{p.shared_ids.join(', ')} <span className="hs-page-click-hint">{'→ filter'}</span></span>
                        }
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Shared objects hint table header */}
      {data.shared_header && (
        <div className="hs-section">
          <div className="hs-section-label">Shared objects hint table header</div>
          <table className="hs-table">
            <tbody>
              <tr><td className="hs-key">Shared refs from first page</td><td className="hs-val">{data.shared_header.nshared_first_page}</td></tr>
              <tr><td className="hs-key">Total shared object groups</td><td className="hs-val">{data.shared_header.nshared_total}</td></tr>
              <tr><td className="hs-key">First non-first-page shared obj</td><td className="hs-val">obj {data.shared_header.first_shared_obj}</td></tr>
              <tr><td className="hs-key">First shared obj file offset</td><td className="hs-val">{fmtOffset(data.shared_header.first_shared_offset)}</td></tr>
              <tr><td className="hs-key">Min group length</td><td className="hs-val">{fmtBytes(data.shared_header.min_group_length)}</td></tr>
              <tr><td className="hs-key">Bits for Δ group length</td><td className="hs-val">{data.shared_header.nbits_delta_group_length}</td></tr>
              <tr><td className="hs-key">Bits for obj count per group</td><td className="hs-val">{data.shared_header.nbits_nobjects}</td></tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Shared groups */}
      {data.shared_groups && data.shared_groups.length > 0 && (
        <div className="hs-section" ref={sharedSectionRef}>
          <div className="hs-section-label">
            Shared object groups ({data.shared_groups.length} groups)
          </div>
          {/* Filter input */}
          <div className="hs-filter-row">
            <input
              ref={filterInputRef}
              className="hs-filter-input"
              placeholder="Filter by group IDs, e.g. 0, 5, 12"
              value={groupFilter}
              onChange={e => setGroupFilter(e.target.value)}
            />
            {groupFilter && (
              <button className="hs-filter-clear" onClick={() => setGroupFilter('')}>{'×'}</button>
            )}
            {filteredIds && (
              <span className="hs-filter-count">
                {data.shared_groups.filter((_, i) => filteredIds.has(i)).length} / {data.shared_groups.length} shown
              </span>
            )}
          </div>
          <div className="hs-scroll-table-wrap">
            <table className="hs-table hs-page-table">
              <thead>
                <tr>
                  <th className="hs-th hs-th-num">Group #</th>
                  <th className="hs-th">PDF Object</th>
                  <th className="hs-th hs-th-num">Objects</th>
                  <th className="hs-th hs-th-num">Length</th>
                  <th className="hs-th hs-th-num">Section</th>
                </tr>
              </thead>
              <tbody>
                {data.shared_groups
                  .map((g, i) => ({ g, i }))
                  .filter(({ i }) => !filteredIds || filteredIds.has(i))
                  .map(({ g, i }) => (
                    <tr key={i} className={i % 2 === 0 ? 'hs-row-even' : 'hs-row-odd'}>
                      <td className="hs-td-num">{i}</td>
                      <td className="hs-td">
                        <button
                          className="hs-obj-link"
                          onClick={() => onJumpToObj(g.first_obj)}
                        >
                          obj {g.first_obj}
                        </button>
                        {g.obj_type && <span className="hs-obj-type"> [{g.obj_type}]</span>}
                      </td>
                      <td className="hs-td-num">{g.nobjects}</td>
                      <td className="hs-td-num">{fmtBytes(g.group_length)}</td>
                      <td className="hs-td-num">
                        <span className={g.section === 'first_page' ? 'hs-badge-fp' : 'hs-badge-rest'}>
                          {g.section === 'first_page' ? 'First page' : 'Rest of file'}
                        </span>
                      </td>
                    </tr>
                  ))
                }
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Linearization parameters */}
      {lp && lp.obj_num != null && (
        <div className="hs-section">
          <div className="hs-section-label">
            Linearization parameters
            <span className="hs-lin-obj">from obj {lp.obj_num}</span>
          </div>
          <table className="hs-table">
            <thead>
              <tr>
                <th className="hs-th">Parameter</th>
                <th className="hs-th">PDF key</th>
                <th className="hs-th">Value</th>
                <th className="hs-th">Description</th>
              </tr>
            </thead>
            <tbody>
              {LIN_PARAM_ROWS.map(row => {
                const raw = lp[row.key] as number | null
                const display = raw == null
                  ? '—'
                  : row.fmt ? row.fmt(raw) : String(raw)
                return (
                  <tr key={row.key}>
                    <td className="hs-key">{row.label}</td>
                    <td className="hs-pdfkey">{row.pdfKey}</td>
                    <td className="hs-val">{display}</td>
                    <td className="hs-desc">{row.desc}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

