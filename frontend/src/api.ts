/* -----------------------------------------------------------------------
   Types that mirror the backend PdfNode / API responses
   ----------------------------------------------------------------------- */

export interface TreeNode {
  label: string
  detail: string
  obj_num: number
  gen_num: number
  is_image: boolean
  children: TreeNode[]
}

export interface UploadResponse {
  id: string
  version: string
  filename: string
  tree: TreeNode | null
}

export interface ObjectDetailResponse {
  detail: string
  is_image: boolean
  is_icc_profile: boolean
  is_content_stream: boolean
  is_palette: boolean
  image_filter: string | null
  obj_num: number
  gen_num: number
}

export interface IccTag {
  sig: string
  name: string
  type_sig: string
  offset: number
  size: number
  summary: string
}

export interface IccSegment {
  label: string
  offset: number
  size: number
  color: string
}

export interface IccData {
  description: string | null
  copyright: string | null
  manufacturer_desc: string | null
  device_model_desc: string | null
  viewing_conditions_desc: string | null
  technology: string | null
  technology_name: string | null
  luminance_y: number | null
  observer: string | null
  view_illuminant: string | null
  profile_class: string | null
  color_space: string
  pcs: string
  total_size: number
  white_point: [number, number, number] | null
  black_point: [number, number, number] | null
  primaries: {
    r_xyz: [number, number, number] | null
    g_xyz: [number, number, number] | null
    b_xyz: [number, number, number] | null
    r_display: [number, number, number] | null
    g_display: [number, number, number] | null
    b_display: [number, number, number] | null
  }
  trc: {
    r: number[] | null
    g: number[] | null
    b: number[] | null
  }
  trc_summary: string | null
  tags_directory: IccTag[]
  structure: IccSegment[]
}

/* -----------------------------------------------------------------------
   API helpers
   ----------------------------------------------------------------------- */

const BASE = '/api'

export async function uploadPdf(file: File): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? 'Upload failed')
  }
  return res.json()
}

export async function fetchObjectDetail(
  uploadId: string,
  num: number,
  gen: number,
): Promise<ObjectDetailResponse> {
  const res = await fetch(`${BASE}/object/${uploadId}/${num}/${gen}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? 'Request failed')
  }
  return res.json()
}

export function imageUrl(uploadId: string, num: number, gen: number): string {
  return `${BASE}/image/${uploadId}/${num}/${gen}`
}

export async function fetchIccProfile(
  uploadId: string,
  num: number,
  gen: number,
): Promise<IccData | null> {
  const res = await fetch(`${BASE}/icc/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

export interface JpegSegment {
  marker: string
  name: string
  desc: string
  offset: number
  size: number
  summary: string
  color: string
  is_scan: boolean
}

export interface JpegFrameInfo {
  type: string
  precision: number
  height: number
  width: number
  components: number
}

export interface JpegStructureSegment {
  label: string
  offset: number
  size: number
  color: string
  is_scan: boolean
}

export interface CcittParam {
  key: string
  value: string
  meaning: string
}

export interface CcittStructureSegment {
  label: string
  offset: number
  size: number
  color: string
}

export interface CcittData {
  k: number
  columns: number
  rows: number | null
  end_of_block: boolean
  end_of_line: boolean
  encoded_byte_align: boolean
  black_is_1: boolean
  damaged_rows_before_error: number
  compression_name: string
  compression_short: string
  standard: string
  params: CcittParam[]
  raw_size: number
  structure: CcittStructureSegment[]
}

export interface FlatParam {
  key: string
  value: string
  meaning: string
}

export interface FlatStructureSegment {
  label: string
  offset: number
  size: number
  color: string
}

export interface FlatData {
  predictor: number
  predictor_name: string
  columns: number | null
  colors: number
  bpc: number
  raw_size: number
  params: FlatParam[]
  structure: FlatStructureSegment[]
}

export interface ImageDetailData {
  width: number | null
  height: number | null
  bits_per_component: number | null
  color_space: string | null
  filter: string | null
  raw_size: number
  decoded_size: number | null
  jpeg: {
    segments: JpegSegment[]
    structure: JpegStructureSegment[]
    frame_info: JpegFrameInfo | null
  } | null
  ccitt: CcittData | null
  flat: FlatData | null
}

export async function fetchImageDetail(
  uploadId: string,
  num: number,
  gen: number,
): Promise<ImageDetailData | null> {
  const res = await fetch(`${BASE}/image_detail/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

export interface ContentStreamOp {
  op: string
  category: string
  color: string
  desc: string
  summary: string
}

export interface ContentStreamStructSeg {
  label: string
  color: string
  count: number
  size: number
}

export interface ContentStreamData {
  operations: ContentStreamOp[]
  total_ops: number
  truncated: boolean
  structure: ContentStreamStructSeg[]
  category_counts: Record<string, number>
}

export async function fetchContentStream(
  uploadId: string,
  num: number,
  gen: number,
): Promise<ContentStreamData | null> {
  const res = await fetch(`${BASE}/content_stream/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

export interface PaletteEntry {
  index: number
  r: number
  g: number
  b: number
  hex: string
  dark_bg: boolean
}

export interface PaletteData {
  entry_count: number
  channels: number
  raw_size: number
  entries: PaletteEntry[]
}

export async function fetchPaletteData(
  uploadId: string,
  num: number,
  gen: number,
): Promise<PaletteData | null> {
  const res = await fetch(`${BASE}/palette/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   Store API
   ----------------------------------------------------------------------- */

export interface StoreFile {
  filename: string
  size: number
}

export async function storePdf(file: File): Promise<{ filename: string; size: number }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/store`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? 'Store failed')
  }
  return res.json()
}

export async function listStore(): Promise<StoreFile[]> {
  const res = await fetch(`${BASE}/store`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? 'Failed to list store')
  }
  const data = await res.json()
  return data.files as StoreFile[]
}

export async function openFromStore(filename: string): Promise<UploadResponse> {
  const res = await fetch(`${BASE}/open_from_store/${encodeURIComponent(filename)}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? 'Open from store failed')
  }
  return res.json()
}
