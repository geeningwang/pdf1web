import type { HintStreamData } from '../api'

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

interface Props {
  data: HintStreamData
}

export default function HintStreamPane({ data }: Props) {
  const lp = data.lin_params

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
          <div className="hs-scroll-table-wrap">
            <table className="hs-table hs-page-table">
              <thead>
                <tr>
                  <th className="hs-th hs-th-num">Page</th>
                  <th className="hs-th hs-th-num">Objects</th>
                  <th className="hs-th hs-th-num">Section length</th>
                  <th className="hs-th hs-th-num">Shared refs</th>
                </tr>
              </thead>
              <tbody>
                {data.pages.map((p, i) => (
                  <tr key={i} className={i % 2 === 0 ? 'hs-row-even' : 'hs-row-odd'}>
                    <td className="hs-td-num">{i}</td>
                    <td className="hs-td-num">{p.nobjects}</td>
                    <td className="hs-td-num">{fmtBytes(p.page_length)}</td>
                    <td className="hs-td-num">{p.nshared}</td>
                  </tr>
                ))}
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
            </tbody>
          </table>
        </div>
      )}

      {/* Shared groups */}
      {data.shared_groups && data.shared_groups.length > 0 && (
        <div className="hs-section">
          <div className="hs-section-label">Shared object groups ({data.shared_groups.length} groups)</div>
          <div className="hs-scroll-table-wrap">
            <table className="hs-table hs-page-table">
              <thead>
                <tr>
                  <th className="hs-th hs-th-num">Group #</th>
                  <th className="hs-th hs-th-num">Objects</th>
                  <th className="hs-th hs-th-num">Group length</th>
                </tr>
              </thead>
              <tbody>
                {data.shared_groups.map((g, i) => (
                  <tr key={i} className={i % 2 === 0 ? 'hs-row-even' : 'hs-row-odd'}>
                    <td className="hs-td-num">{i}</td>
                    <td className="hs-td-num">{g.nobjects}</td>
                    <td className="hs-td-num">{fmtBytes(g.group_length)}</td>
                  </tr>
                ))}
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

