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
  obj_num: number
  gen_num: number
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
