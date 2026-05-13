/**
 * CsCanvasRenderer — partial front-end PDF content stream renderer.
 *
 * Supported:
 *   - All path construction & painting (m l c v y h re S s f B b n)
 *   - Clipping (W W*)
 *   - Stroke/fill colors: rg RG g G k K sc SC scn SCN
 *   - Graphics state: q Q cm w J j M d
 *   - Text (approximate, using sans-serif fallback): BT ET Tf Td TD Tm T* Tj TJ ' "
 *   - XObject images: Do
 *
 * Not supported (silently ignored):
 *   - ICCBased / Lab / Indexed colour spaces
 *   - Soft masks, transparency groups, shadings
 *   - Type 3 / CID fonts (text is approximate)
 *   - Form XObjects (treated as no-op)
 */
import React, { useRef, useEffect, useState } from 'react'
import type { ContentStreamData, CsOperand } from '../api'
import { imageUrl } from '../api'

interface Props {
  data: ContentStreamData
  uploadId: string
}

// ── numeric helpers ─────────────────────────────────────────────────────────

function asNum(op: CsOperand): number {
  return parseFloat(op.value)
}

function grayToCss(g: number): string {
  const v = Math.round(Math.max(0, Math.min(1, g)) * 255)
  return `rgb(${v},${v},${v})`
}

function rgbToCss(r: number, g: number, b: number): string {
  return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`
}

function cmykToCss(c: number, m: number, y: number, k: number): string {
  const r = (1 - c) * (1 - k)
  const g = (1 - m) * (1 - k)
  const b = (1 - y) * (1 - k)
  return rgbToCss(r, g, b)
}

// ── PDF string decode (font-aware) ─────────────────────────────────────────

type FontResEntry = NonNullable<ContentStreamData['resources']>['font'][string]
type FontRes = FontResEntry | undefined

/** Unescape a PDF literal string body (between the outer parens) to raw bytes. */
function unescapePdfLiteralToBytes(s: string): number[] {
  const bytes: number[] = []
  let i = 0
  while (i < s.length) {
    if (s[i] !== '\\') { bytes.push(s.charCodeAt(i++) & 0xff); continue }
    const next = s[i + 1]; i += 2
    if      (next === 'n')  bytes.push(0x0a)
    else if (next === 'r')  bytes.push(0x0d)
    else if (next === 't')  bytes.push(0x09)
    else if (next === 'b')  bytes.push(0x08)
    else if (next === 'f')  bytes.push(0x0c)
    else if (next === '(')  bytes.push(0x28)
    else if (next === ')')  bytes.push(0x29)
    else if (next === '\\') bytes.push(0x5c)
    else if (next >= '0' && next <= '7') {
      let oct = next
      while (i < s.length && oct.length < 3 && s[i] >= '0' && s[i] <= '7') oct += s[i++]
      bytes.push(parseInt(oct, 8) & 0xff)
    }
    // unknown escape: skip
  }
  return bytes
}

/**
 * Decode a raw PDF string token (literal or hex) for the given font.
 * Uses the font's ToUnicode CMap for glyph-code→Unicode lookup.
 * Falls back to printable-ASCII for unmapped 1-byte codes.
 */
function decodePdfStringWithFont(raw: string, font: FontRes): string {
  const isType0 = font?.subtype === 'Type0'
  const cmap    = font?.cmap ?? null

  if (raw.startsWith('(') && raw.endsWith(')')) {
    const bytes = unescapePdfLiteralToBytes(raw.slice(1, -1))
    let result = ''
    if (isType0) {
      for (let i = 0; i + 1 < bytes.length; i += 2) {
        const code = (bytes[i] << 8) | bytes[i + 1]
        result += cmap?.[code] ?? '\u00b7'
      }
    } else {
      for (const b of bytes) {
        result += cmap?.[b] ?? (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '\u00b7')
      }
    }
    return result
  }

  if (raw.startsWith('<') && raw.endsWith('>')) {
    const hex       = raw.slice(1, -1).replace(/\s/g, '')
    const codeWidth = isType0 ? 4 : 2
    let result = ''
    for (let i = 0; i < hex.length; i += codeWidth) {
      const code = parseInt(hex.substr(i, codeWidth), 16)
      if (cmap && cmap[code] !== undefined) {
        result += cmap[code]
      } else {
        const byte = parseInt(hex.substr(i, 2), 16)
        result += byte >= 0x20 && byte < 0x7f ? String.fromCharCode(byte) : '\u00b7'
      }
    }
    return result
  }

  return raw
}

// ── TJ array parser ─────────────────────────────────────────────────────────

/** Items in a TJ array; strings kept as raw PDF tokens for font-aware decode. */
type TJItem = { kind: 'str'; raw: string } | { kind: 'num'; value: number }

function parseTJArray(raw: string): TJItem[] {
  const content = raw.startsWith('[') ? raw.slice(1, -1) : raw
  const items: TJItem[] = []
  let i = 0
  while (i < content.length) {
    const ch = content[i]
    if (/\s/.test(ch)) { i++; continue }
    if (ch === '(') {
      let depth = 1; let j = i + 1
      while (j < content.length && depth > 0) {
        if (content[j] === '\\') { j += 2; continue }
        if (content[j] === '(') depth++
        else if (content[j] === ')') depth--
        j++
      }
      items.push({ kind: 'str', raw: content.slice(i, j) })
      i = j
    } else if (ch === '<') {
      let j = content.indexOf('>', i + 1)
      j = j >= 0 ? j + 1 : content.length
      items.push({ kind: 'str', raw: content.slice(i, j) })
      i = j
    } else {
      let j = i
      while (j < content.length && !/[\s[\]()<>]/.test(content[j])) j++
      const v = parseFloat(content.slice(i, j))
      if (!isNaN(v)) items.push({ kind: 'num', value: v })
      i = j > i ? j : j + 1
    }
  }
  return items
}

// ── PDF standard font → CSS mapping ───────────────────────────────────────

const PDF_FONT_MAP: Record<string, string> = {
  'Times-Roman':           '"Times New Roman", Times, serif',
  'Times-Bold':            '"Times New Roman", Times, serif',
  'Times-Italic':          '"Times New Roman", Times, serif',
  'Times-BoldItalic':      '"Times New Roman", Times, serif',
  'Helvetica':             'Arial, Helvetica, sans-serif',
  'Helvetica-Bold':        'Arial, Helvetica, sans-serif',
  'Helvetica-Oblique':     'Arial, Helvetica, sans-serif',
  'Helvetica-BoldOblique': 'Arial, Helvetica, sans-serif',
  'Courier':               '"Courier New", Courier, monospace',
  'Courier-Bold':          '"Courier New", Courier, monospace',
  'Courier-Oblique':       '"Courier New", Courier, monospace',
  'Courier-BoldOblique':   '"Courier New", Courier, monospace',
  'Symbol':                'Symbol, serif',
  'ZapfDingbats':          '"Zapf Dingbats", serif',
}

interface FontStyle { cssFamily: string; bold: boolean; italic: boolean }

function fontNameToStyle(baseFontName: string | null | undefined): FontStyle {
  if (!baseFontName) return { cssFamily: 'sans-serif', bold: false, italic: false }
  // Strip embedded-subset prefix e.g. "ABCDEF+FontName" → "FontName"
  const name = baseFontName.replace(/^[A-Z]{6}\+/, '')
  const cssFamily =
    PDF_FONT_MAP[name] ??
    (/[Cc]ourier|[Mm]ono/.test(name)
      ? '"Courier New", Courier, monospace'
      : /[Ss]erif/.test(name) && !/[Ss]ans/.test(name)
        ? '"Times New Roman", Times, serif'
        : 'Arial, Helvetica, sans-serif')
  return { cssFamily, bold: /Bold/i.test(name), italic: /Italic|Oblique/i.test(name) }
}

// ── Matrix helpers ──────────────────────────────────────────────────────────

/**
 * Concatenate two PDF transformation matrices.
 * PDF convention: [a b c d e f] = [a b 0 / c d 0 / e f 1] in row-major.
 * matMul(m1, m2) = apply m1 first, then m2.
 */
function matMul(m1: number[], m2: number[]): number[] {
  const [a1, b1, c1, d1, e1, f1] = m1
  const [a2, b2, c2, d2, e2, f2] = m2
  return [
    a1 * a2 + b1 * c2,
    a1 * b2 + b1 * d2,
    c1 * a2 + d1 * c2,
    c1 * b2 + d1 * d2,
    e1 * a2 + f1 * c2 + e2,
    e1 * b2 + f1 * d2 + f2,
  ]
}

const IDENTITY: number[] = [1, 0, 0, 1, 0, 0]

// ── Graphics / text state ───────────────────────────────────────────────────

interface GState {
  fillColor: string
  strokeColor: string
  lineWidth: number
  lineCap: CanvasLineCap
  lineJoin: CanvasLineJoin
  miterLimit: number
  dashArray: number[]
  dashPhase: number
}

interface TState {
  fontSize: number
  fontName: string
  charSpacing: number
  wordSpacing: number
  hScale: number      // 100 = normal
  leading: number
  renderMode: number
  rise: number
  tm: number[]        // text matrix (in PDF user space)
  tlm: number[]       // text line matrix
}

const DEFAULT_GS: GState = {
  fillColor: 'black',
  strokeColor: 'black',
  lineWidth: 1,
  lineCap: 'butt',
  lineJoin: 'miter',
  miterLimit: 10,
  dashArray: [],
  dashPhase: 0,
}

const DEFAULT_TS: TState = {
  fontSize: 12,
  fontName: '',
  charSpacing: 0,
  wordSpacing: 0,
  hScale: 100,
  leading: 0,
  renderMode: 0,
  rise: 0,
  tm: [...IDENTITY],
  tlm: [...IDENTITY],
}

// ── dash array parser ───────────────────────────────────────────────────────

function parseDashArray(raw: string): number[] {
  return (raw.match(/[-\d.]+/g) ?? []).map(Number).filter(v => !isNaN(v) && v >= 0)
}

// ── main render function ────────────────────────────────────────────────────

function renderOps(
  ctx: CanvasRenderingContext2D,
  data: ContentStreamData,
  loadedImages: Map<string, HTMLImageElement>,
): void {
  const fontResources = data.resources?.font ?? {}
  const curFont = (): FontRes => fontResources[ts.fontName]
  let gs: GState = { ...DEFAULT_GS }
  const gsStack: GState[] = []
  let ts: TState = { ...DEFAULT_TS, tm: [...IDENTITY], tlm: [...IDENTITY] }
  let curX = 0, curY = 0, startX = 0, startY = 0

  // Apply the current gs to the ctx
  function syncGS(): void {
    ctx.fillStyle   = gs.fillColor
    ctx.strokeStyle = gs.strokeColor
    ctx.lineWidth   = gs.lineWidth
    ctx.lineCap     = gs.lineCap
    ctx.lineJoin    = gs.lineJoin
    ctx.miterLimit  = gs.miterLimit
    ctx.setLineDash(gs.dashArray)
    ctx.lineDashOffset = gs.dashPhase
  }

  // Show a string in the current text state
  function showText(text: string): void {
    if (!text) return
    const [a, b, c, d, e, f] = ts.tm
    const fontRes   = fontResources[ts.fontName]
    const { cssFamily, bold, italic } = fontNameToStyle(fontRes?.base_font)
    const hs = ts.hScale / 100
    ctx.save()
    ctx.transform(a, b, c, d, e, f)
    // Scale by font size; negate Y to undo the coordinate-system Y-flip.
    ctx.transform(ts.fontSize * hs, 0, 0, -ts.fontSize, 0, ts.rise)
    ctx.font = `${italic ? 'italic' : 'normal'} ${bold ? 'bold' : 'normal'} 1px ${cssFamily}`
    if (ts.renderMode !== 3) {  // 3 = invisible
      const fill   = ts.renderMode === 0 || ts.renderMode === 2 || ts.renderMode === 4 || ts.renderMode === 6
      const stroke = ts.renderMode === 1 || ts.renderMode === 2 || ts.renderMode === 5 || ts.renderMode === 6
      if (fill)   { ctx.fillStyle   = gs.fillColor;   ctx.fillText(text, 0, 0) }
      if (stroke) { ctx.strokeStyle = gs.strokeColor; ctx.strokeText(text, 0, 0) }
    }
    ctx.restore()

    // Advance text matrix using actual /Widths; fall back to rough estimate
    const widths    = fontRes?.widths ?? null
    const firstChar = fontRes?.first_char ?? 0
    let adv = 0
    for (const ch of text) {
      const code = ch.charCodeAt(0)
      const w0 = (widths && code >= firstChar && code < firstChar + widths.length)
        ? widths[code - firstChar] / 1000
        : ch === ' ' ? 0.25 : 0.55
      adv += (w0 * ts.fontSize + ts.charSpacing + (ch === ' ' ? ts.wordSpacing : 0)) * hs
    }
    ts.tm = matMul([1, 0, 0, 1, adv, 0], ts.tm)
  }

  // Colour helpers that also set on ctx immediately
  function setFill(css: string)   { gs = { ...gs, fillColor: css };   ctx.fillStyle   = css }
  function setStroke(css: string) { gs = { ...gs, strokeColor: css }; ctx.strokeStyle = css }

  syncGS()
  ctx.beginPath()

  for (const op of data.operations) {
    const ops = op.operands

    switch (op.op) {

      // ── Graphics state ──────────────────────────────────────────────────
      case 'q':
        gsStack.push({ ...gs })
        ctx.save()
        break
      case 'Q':
        if (gsStack.length > 0) { gs = gsStack.pop()!; syncGS() }
        ctx.restore()
        break
      case 'cm':
        if (ops.length >= 6) ctx.transform(...ops.map(asNum) as [number,number,number,number,number,number])
        break
      case 'w':
        if (ops.length >= 1) { gs.lineWidth = asNum(ops[0]); ctx.lineWidth = gs.lineWidth }
        break
      case 'J': {
        const caps: CanvasLineCap[] = ['butt', 'round', 'square']
        if (ops.length >= 1) { gs.lineCap = caps[Math.min(2, Math.max(0, asNum(ops[0])))]; ctx.lineCap = gs.lineCap }
        break
      }
      case 'j': {
        const joins: CanvasLineJoin[] = ['miter', 'round', 'bevel']
        if (ops.length >= 1) { gs.lineJoin = joins[Math.min(2, Math.max(0, asNum(ops[0])))]; ctx.lineJoin = gs.lineJoin }
        break
      }
      case 'M':
        if (ops.length >= 1) { gs.miterLimit = asNum(ops[0]); ctx.miterLimit = gs.miterLimit }
        break
      case 'd':
        if (ops.length >= 2) {
          gs.dashArray = parseDashArray(ops[0].value)
          gs.dashPhase = asNum(ops[1])
          ctx.setLineDash(gs.dashArray)
          ctx.lineDashOffset = gs.dashPhase
        }
        break

      // ── Colour ──────────────────────────────────────────────────────────
      case 'g':  if (ops.length >= 1) setFill(grayToCss(asNum(ops[0]))); break
      case 'G':  if (ops.length >= 1) setStroke(grayToCss(asNum(ops[0]))); break
      case 'rg': if (ops.length >= 3) setFill(rgbToCss(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]))); break
      case 'RG': if (ops.length >= 3) setStroke(rgbToCss(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]))); break
      case 'k':  if (ops.length >= 4) setFill(cmykToCss(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]), asNum(ops[3]))); break
      case 'K':  if (ops.length >= 4) setStroke(cmykToCss(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]), asNum(ops[3]))); break
      case 'sc':
      case 'scn': {
        const nums = ops.filter(o => o.type === 'num').map(asNum)
        if (nums.length === 1) setFill(grayToCss(nums[0]))
        else if (nums.length >= 3) setFill(rgbToCss(nums[0], nums[1], nums[2]))
        break
      }
      case 'SC':
      case 'SCN': {
        const nums = ops.filter(o => o.type === 'num').map(asNum)
        if (nums.length === 1) setStroke(grayToCss(nums[0]))
        else if (nums.length >= 3) setStroke(rgbToCss(nums[0], nums[1], nums[2]))
        break
      }

      // ── Path construction ────────────────────────────────────────────────
      case 'm':
        if (ops.length >= 2) {
          curX = asNum(ops[0]); curY = asNum(ops[1])
          startX = curX; startY = curY
          ctx.moveTo(curX, curY)
        }
        break
      case 'l':
        if (ops.length >= 2) {
          curX = asNum(ops[0]); curY = asNum(ops[1])
          ctx.lineTo(curX, curY)
        }
        break
      case 'c':
        if (ops.length >= 6) {
          ctx.bezierCurveTo(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]), asNum(ops[3]), asNum(ops[4]), asNum(ops[5]))
          curX = asNum(ops[4]); curY = asNum(ops[5])
        }
        break
      case 'v':
        if (ops.length >= 4) {
          ctx.bezierCurveTo(curX, curY, asNum(ops[0]), asNum(ops[1]), asNum(ops[2]), asNum(ops[3]))
          curX = asNum(ops[2]); curY = asNum(ops[3])
        }
        break
      case 'y':
        if (ops.length >= 4) {
          ctx.bezierCurveTo(asNum(ops[0]), asNum(ops[1]), asNum(ops[2]), asNum(ops[3]), asNum(ops[2]), asNum(ops[3]))
          curX = asNum(ops[2]); curY = asNum(ops[3])
        }
        break
      case 'h':
        ctx.closePath(); curX = startX; curY = startY; break
      case 're':
        if (ops.length >= 4) {
          const [rx, ry, rw, rh] = ops.map(asNum)
          ctx.rect(rx, ry, rw, rh)
          curX = rx; curY = ry; startX = rx; startY = ry
        }
        break

      // ── Path painting ────────────────────────────────────────────────────
      case 'S':  ctx.stroke();  ctx.beginPath(); break
      case 's':  ctx.closePath(); ctx.stroke();  ctx.beginPath(); break
      case 'f':
      case 'F':  ctx.fill('nonzero');  ctx.beginPath(); break
      case 'f*': ctx.fill('evenodd'); ctx.beginPath(); break
      case 'B':  ctx.fill('nonzero');  ctx.stroke(); ctx.beginPath(); break
      case 'B*': ctx.fill('evenodd'); ctx.stroke(); ctx.beginPath(); break
      case 'b':  ctx.closePath(); ctx.fill('nonzero'); ctx.stroke(); ctx.beginPath(); break
      case 'b*': ctx.closePath(); ctx.fill('evenodd'); ctx.stroke(); ctx.beginPath(); break
      case 'n':  ctx.beginPath(); break

      // ── Clipping ─────────────────────────────────────────────────────────
      case 'W':  ctx.clip('nonzero'); break
      case 'W*': ctx.clip('evenodd'); break

      // ── XObject ──────────────────────────────────────────────────────────
      case 'Do': {
        if (ops.length >= 1) {
          const name = ops[0].value.startsWith('/') ? ops[0].value.slice(1) : ops[0].value
          const img = loadedImages.get(name)
          if (img) {
            ctx.save()
            // PDF images fill a 1×1 unit square; flip vertically to undo outer Y-flip
            ctx.transform(1, 0, 0, -1, 0, 1)
            ctx.drawImage(img, 0, 0, 1, 1)
            ctx.restore()
          }
        }
        break
      }

      // ── Text state ───────────────────────────────────────────────────────
      case 'BT': ts.tm = [...IDENTITY]; ts.tlm = [...IDENTITY]; break
      case 'ET': break
      case 'Tf':
        if (ops.length >= 2) {
          ts.fontName = ops[0].value.replace(/^\//, '')
          ts.fontSize = asNum(ops[1])
        }
        break
      case 'Tc': if (ops.length >= 1) ts.charSpacing  = asNum(ops[0]); break
      case 'Tw': if (ops.length >= 1) ts.wordSpacing  = asNum(ops[0]); break
      case 'Tz': if (ops.length >= 1) ts.hScale       = asNum(ops[0]); break
      case 'TL': if (ops.length >= 1) ts.leading      = asNum(ops[0]); break
      case 'Tr': if (ops.length >= 1) ts.renderMode   = asNum(ops[0]); break
      case 'Ts': if (ops.length >= 1) ts.rise         = asNum(ops[0]); break

      // ── Text positioning ─────────────────────────────────────────────────
      case 'Td':
        if (ops.length >= 2) {
          ts.tlm = matMul([1, 0, 0, 1, asNum(ops[0]), asNum(ops[1])], ts.tlm)
          ts.tm  = [...ts.tlm]
        }
        break
      case 'TD':
        if (ops.length >= 2) {
          ts.leading = -asNum(ops[1])
          ts.tlm = matMul([1, 0, 0, 1, asNum(ops[0]), asNum(ops[1])], ts.tlm)
          ts.tm  = [...ts.tlm]
        }
        break
      case 'Tm':
        if (ops.length >= 6) { ts.tm = ops.map(asNum); ts.tlm = [...ts.tm] }
        break
      case 'T*':
        ts.tlm = matMul([1, 0, 0, 1, 0, -ts.leading], ts.tlm)
        ts.tm  = [...ts.tlm]
        break

      // ── Text show ────────────────────────────────────────────────────────
      case 'Tj':
        if (ops.length >= 1) showText(decodePdfStringWithFont(ops[ops.length - 1].value, curFont()))
        break
      case "'":
        ts.tlm = matMul([1, 0, 0, 1, 0, -ts.leading], ts.tlm)
        ts.tm  = [...ts.tlm]
        if (ops.length >= 1) showText(decodePdfStringWithFont(ops[ops.length - 1].value, curFont()))
        break
      case '"':
        if (ops.length >= 3) {
          ts.wordSpacing = asNum(ops[0]); ts.charSpacing = asNum(ops[1])
          ts.tlm = matMul([1, 0, 0, 1, 0, -ts.leading], ts.tlm)
          ts.tm  = [...ts.tlm]
          showText(decodePdfStringWithFont(ops[2].value, curFont()))
        }
        break
      case 'TJ':
        if (ops.length >= 1 && ops[0].type === 'array') {
          const f = curFont()
          for (const item of parseTJArray(ops[0].value)) {
            if (item.kind === 'str') {
              showText(decodePdfStringWithFont(item.raw, f))
            } else {
              // Displacement in 1/1000 text-unit; negative = move right
              const dx = -(item.value / 1000) * ts.fontSize * (ts.hScale / 100)
              ts.tm = matMul([1, 0, 0, 1, dx, 0], ts.tm)
            }
          }
        }
        break
    }
  }
}

// ── Component ───────────────────────────────────────────────────────────────

const CsCanvasRenderer: React.FC<Props> = ({ data, uploadId }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef   = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    const mb = data.media_box
    if (!mb || mb.length < 4) {
      setStatus('error')
      setErrorMsg('No MediaBox — cannot determine page dimensions')
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return

    const [x0, y0, x1, y1] = mb
    const pdfW = x1 - x0
    const pdfH = y1 - y0
    if (pdfW <= 0 || pdfH <= 0) {
      setStatus('error')
      setErrorMsg(`Invalid MediaBox [${mb.join(', ')}]`)
      return
    }

    const containerW = wrapRef.current?.clientWidth ?? 800
    const scale = containerW / pdfW
    canvas.width  = containerW
    canvas.height = Math.round(pdfH * scale)

    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = 'white'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    // Initial transform: PDF user space → canvas pixels.
    // PDF origin is bottom-left, Y up. Canvas origin is top-left, Y down.
    ctx.setTransform(scale, 0, 0, -scale, -x0 * scale, y1 * scale)

    setStatus('loading')

    // Pre-load all image XObjects
    const xobjectRes = data.resources?.xobject ?? {}
    const loadedImages = new Map<string, HTMLImageElement>()
    const promises = Object.entries(xobjectRes)
      .filter(([, v]) => v.subtype === 'Image')
      .map(([name, res]) =>
        new Promise<void>(resolve => {
          const img = new Image()
          img.onload  = () => { loadedImages.set(name, img); resolve() }
          img.onerror = () => resolve()
          img.src = imageUrl(uploadId, res.num, res.gen)
        })
      )

    Promise.all(promises).then(() => {
      try {
        renderOps(ctx, data, loadedImages)
        setStatus('done')
      } catch (err) {
        setStatus('error')
        setErrorMsg(String(err))
      }
    })
  }, [data, uploadId])

  return (
    <div ref={wrapRef} className="cs-render-wrap">
      {status === 'loading' && <div className="cs-render-overlay">Rendering…</div>}
      {status === 'error'   && <div className="cs-render-error">{errorMsg}</div>}
      <canvas ref={canvasRef} className="cs-render-canvas" />
    </div>
  )
}

export default CsCanvasRenderer
