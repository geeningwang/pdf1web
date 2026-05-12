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
