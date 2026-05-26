/* -----------------------------------------------------------------------
   Types that mirror the backend PdfNode / API responses
   ----------------------------------------------------------------------- */

export interface TreeNode {
  label: string
  detail: string
  obj_num: number
  gen_num: number
  is_image: boolean
  type_label: string
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
  type_label: string
  is_image: boolean
  is_thumb: boolean
  is_icc_profile: boolean
  is_content_stream: boolean
  is_palette: boolean
  is_tounicode: boolean
  is_font_descriptor: boolean
  is_font: boolean
  is_ttf: boolean
  is_cid_to_gid_map: boolean
  is_cid_set: boolean
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

export interface CsOperand {
  type: 'num' | 'name' | 'string' | 'array' | 'dict_val'
  value: string
}

export interface ContentStreamOp {
  op: string
  category: string
  color: string
  desc: string
  summary: string
  operands: CsOperand[]
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
  resources?: {
    xobject: Record<string, {
      num: number
      gen: number
      subtype: string
      smask_num: number | null
      smask_gen: number | null
    }>
    font: Record<string, {
      num: number
      gen: number
      base_font: string | null
      subtype: string | null
      first_char: number
      last_char: number
      widths: number[] | null
      cmap: Record<number, string> | null
      font_file_num: number | null
      font_file_gen: number | null
      cid_to_gid_identity: boolean
      cid_to_gid_num: number | null
      cid_to_gid_gen: number | null
    }>
  }
  media_box?: [number, number, number, number] | null
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

/* -----------------------------------------------------------------------
   ToUnicode CMap API
   ----------------------------------------------------------------------- */

export interface CMapEntry {
  src_hex: string
  src_int: number
  dst_hex: string
  code_point: number
  char: string
}

export interface ToUnicodeData {
  cmap_name: string | null
  cmap_type: number | null
  registry: string | null
  ordering: string | null
  total_mappings: number
  mappings: CMapEntry[]
}

export async function fetchToUnicode(
  uploadId: string,
  num: number,
  gen: number,
): Promise<ToUnicodeData | null> {
  const res = await fetch(`${BASE}/tounicode/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   FontDescriptor API
   ----------------------------------------------------------------------- */

export interface FontFlag {
  bit: number
  name: string
  desc: string
}

export interface FontDescriptorData {
  font_name: string | null
  flags_raw: number
  flags: FontFlag[]
  ascent: number | null
  descent: number | null
  cap_height: number | null
  x_height: number | null
  italic_angle: number | null
  stem_v: number | null
  stem_h: number | null
  font_weight: number | null
  bbox: number[] | null
  font_file2_num: number | null
  cidset_num: number | null
  missing_width: number | null
}

export async function fetchFontDescriptor(
  uploadId: string,
  num: number,
  gen: number,
): Promise<FontDescriptorData | null> {
  const res = await fetch(`${BASE}/fontdescriptor/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   TrueType table directory API
   ----------------------------------------------------------------------- */

export interface TtfTable {
  tag: string
  checksum: string
  offset: number
  length: number
  desc: string
}

export interface TtfTablesData {
  sfVersion: string
  num_tables: number
  total_size: number
  tables: TtfTable[]
}

export async function fetchTtfTables(
  uploadId: string,
  num: number,
  gen: number,
): Promise<TtfTablesData | null> {
  const res = await fetch(`${BASE}/ttf_tables/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   Back-references (cross-reference) API
   ----------------------------------------------------------------------- */

export interface BackRef {
  from_num: number
  from_gen: number
  key_path: string
  type_name: string
}

export interface BackRefsData {
  obj_num: number
  refs: BackRef[]
}

export async function fetchBackRefs(
  uploadId: string,
  num: number,
): Promise<BackRefsData | null> {
  const res = await fetch(`${BASE}/backrefs/${uploadId}/${num}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   CIDToGIDMap API
   ----------------------------------------------------------------------- */

export interface CidToGidEntry {
  cid: number
  gid: number
}

export interface CidToGidData {
  total_cids: number
  mapped_count: number
  entries: CidToGidEntry[]
  coverage_hex: string  // packed bitmap, 1 bit per CID
}

export async function fetchCidToGid(
  uploadId: string,
  num: number,
  gen: number,
): Promise<CidToGidData | null> {
  const res = await fetch(`${BASE}/cid_to_gid/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

export interface CidSetData {
  total_slots: number    // bytes * 8
  present_count: number  // number of set bits
  last_cid: number       // highest set CID
  coverage_hex: string   // raw bitmap bytes as hex (1 bit per CID, MSB first)
}

export async function fetchCidSet(
  uploadId: string,
  num: number,
  gen: number,
): Promise<CidSetData | null> {
  const res = await fetch(`${BASE}/cid_set/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   Simple Font (non-CID) character encoding API
   ----------------------------------------------------------------------- */

export interface FontPaneData {
  base_font: string | null
  subtype: string | null
  encoding: string | null
  first_char: number | null
  last_char: number | null
  widths: number[] | null
  is_embedded: boolean
  cmap: Record<string, string>  // byte index (as string) → unicode char
  font_descriptor_num: number | null
  to_unicode_num: number | null
}

export async function fetchFontPane(
  uploadId: string,
  num: number,
  gen: number,
): Promise<FontPaneData | null> {
  const res = await fetch(`${BASE}/font/${uploadId}/${num}/${gen}`)
  if (!res.ok) return null
  return res.json()
}

/* -----------------------------------------------------------------------
   XRef Table API
   ----------------------------------------------------------------------- */

export interface XRefEntry {
  obj_num: number
  etype: 'in_use' | 'free' | 'compressed'
  offset: number
  gen: number
  stm_num?: number    // compressed only: object stream object number
  stm_index?: number  // compressed only: index within object stream
}

export interface XRefData {
  total: number
  in_use: number
  free: number
  compressed: number
  file_size: number
  entries: XRefEntry[]
}

export async function fetchXRef(uploadId: string): Promise<XRefData | null> {
  const res = await fetch(`${BASE}/xref/${uploadId}`)
  if (!res.ok) return null
  return res.json()
}
