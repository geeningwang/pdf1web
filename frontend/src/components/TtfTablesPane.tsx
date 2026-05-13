import type { TtfTablesData, TtfTable } from '../api'

function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MiB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${n.toLocaleString()} B`
}

// Color map for well-known table tags
const TAG_COLORS: Record<string, string> = {
  'head': '#6ea8fe',
  'hhea': '#6ea8fe',
  'OS/2': '#6ea8fe',
  'name': '#6ea8fe',
  'post': '#6ea8fe',
  'cmap': '#57cc99',
  'glyf': '#f4a261',
  'loca': '#f4a261',
  'hmtx': '#a78bfa',
  'maxp': '#a78bfa',
  'kern': '#ffc857',
  'GSUB': '#e07a5f',
  'GPOS': '#e07a5f',
  'GDEF': '#e07a5f',
  'CFF ': '#f0a500',
  'DSIG': '#888',
  'cvt ': '#9bb5ce',
  'fpgm': '#9bb5ce',
  'prep': '#9bb5ce',
}

function tagColor(tag: string): string {
  return TAG_COLORS[tag] ?? '#6c757d'
}

interface StructBarProps {
  tables: TtfTable[]
  totalSize: number
}

function StructBar({ tables, totalSize }: StructBarProps) {
  // Show a proportional bar of table offsets + lengths
  const sorted = [...tables].sort((a, b) => a.offset - b.offset)
  return (
    <div className="ttf-struct-wrap">
      <div className="ttf-struct-bar">
        {sorted.map(t => (
          <div
            key={t.tag}
            className="ttf-struct-seg"
            style={{
              flex: Math.max(t.length, 1),
              backgroundColor: tagColor(t.tag),
            }}
            title={`${t.tag}\nOffset: 0x${t.offset.toString(16).toUpperCase()} | Length: ${fmtBytes(t.length)}\n${t.desc}`}
          />
        ))}
      </div>
      <div className="ttf-struct-legend">
        {sorted.map(t => (
          <span key={t.tag} className="ttf-legend-item">
            <span className="ttf-legend-dot" style={{ background: tagColor(t.tag) }} />
            {t.tag.trim()}
          </span>
        ))}
        <span className="ttf-legend-total">{fmtBytes(totalSize)} total</span>
      </div>
    </div>
  )
}

interface Props {
  data: TtfTablesData
}

export default function TtfTablesPane({ data }: Props) {
  return (
    <div className="ttf-pane">
      <div className="ttf-header">
        <div className="ttf-title">TrueType / OpenType Font</div>
        <div className="ttf-meta">
          <span className="ttf-meta-item"><b>Format:</b> {data.sfVersion}</span>
          <span className="ttf-meta-item"><b>Tables:</b> {data.num_tables}</span>
          <span className="ttf-meta-item"><b>Size:</b> {fmtBytes(data.total_size)}</span>
        </div>
      </div>

      <StructBar tables={data.tables} totalSize={data.total_size} />

      <div className="ttf-table-wrap">
        <table className="ttf-table">
          <thead>
            <tr>
              <th>Tag</th>
              <th>Offset</th>
              <th>Length</th>
              <th>Checksum</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.tables.map(t => (
              <tr key={t.tag}>
                <td>
                  <span className="ttf-tag-pill" style={{ borderColor: tagColor(t.tag) }}>
                    {t.tag.trim()}
                  </span>
                </td>
                <td className="ttf-mono">0x{t.offset.toString(16).toUpperCase().padStart(6, '0')}</td>
                <td className="ttf-mono">{fmtBytes(t.length)}</td>
                <td className="ttf-mono ttf-checksum">{t.checksum}</td>
                <td className="ttf-desc">{t.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
