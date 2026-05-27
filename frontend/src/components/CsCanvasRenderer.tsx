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
import { useRef, useEffect, useState, useImperativeHandle, forwardRef } from 'react'
import type { ContentStreamData, CsOperand } from '../api'
import { imageUrl } from '../api'
import * as opentype from '../lib/opentype-compat'

export interface CsCanvasHandle {
  savePng: () => void
}

interface Props {
  data: ContentStreamData
  uploadId: string
  maxOps?: number
  /** 0 = visible only  1 = visible + invisible  2 = invisible only */
  invisibleMode?: 0 | 1 | 2
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
/** Extract raw byte values from a PDF string token (literal or hex). */
function rawTokenToBytes(rawToken: string): number[] {
  if (rawToken.startsWith('(') && rawToken.endsWith(')')) {
    return unescapePdfLiteralToBytes(rawToken.slice(1, -1))
  }
  if (rawToken.startsWith('<') && rawToken.endsWith('>')) {
    const hex = rawToken.slice(1, -1).replace(/\s/g, '')
    const bytes: number[] = []
    for (let i = 0; i + 1 < hex.length; i += 2) bytes.push(parseInt(hex.substr(i, 2), 16))
    return bytes
  }
  return []
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
  // Strip embedded-subset prefix e.g. "AAAAAE+TimesNewRomanPSMT" → "TimesNewRomanPSMT"
  const name = baseFontName.replace(/^[A-Z]{6}\+/, '')

  const cssFamily =
    // 1. Exact match against PDF standard font names
    PDF_FONT_MAP[name] ??
    // 2. Monospace families
    (/courier|monospac/i.test(name)
      ? '"Courier New", Courier, monospace'
    // 3. Serif families — match common embedded font name patterns:
    //    TimesNewRoman*, Times*, Roman, Palatino, Garamond, Georgia,
    //    Bookman, Baskerville, Cambria, Caslon, Minion, Bodoni, Charter,
    //    Constantia, Didot, Bembo, Plantin, Sabon, Utopia, Warnock
    : /times|newroman|palatin|garamond|georgia|bookman|baskervill|cambria|caslon|minion|bodoni|charter|constantia|didot|bembo|plantin|sabon|utopia|warnock/i.test(name) && !/sans/i.test(name)
      ? '"Times New Roman", Times, serif'
    // 4. Explicit "Serif" in name (but not "Sans-Serif")
    : /serif/i.test(name) && !/sans/i.test(name)
      ? '"Times New Roman", Times, serif'
    // 5. Known sans-serif families
    : /helvetica|arial|verdana|tahoma|trebuchet|calibri|myriad|futura|gilsans|gill.sans|optima|franklin|gothic|frutiger|univers|avenir/i.test(name)
      ? 'Arial, Helvetica, sans-serif'
    // 6. Default fallback
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

// ── Font metric measurement ─────────────────────────────────────────────────

/** Shared off-screen canvas for character-advance measurement (no transforms, identity CTM). */
const _mcCanvas = document.createElement('canvas')
const _mcCtx    = _mcCanvas.getContext('2d')!

/**
 * Return the advance width of `charStr` in em units for the given CSS font family
 * at the given font size.  Results are cached so repeated calls are O(1).
 */
const _mcCache = new Map<string, number>()
function measureEmWidth(charStr: string, cssFamily: string, fontSize: number): number {
  const key = `${fontSize}|${cssFamily}|${charStr}`
  const cached = _mcCache.get(key)
  if (cached !== undefined) return cached
  _mcCtx.font = `${fontSize}px ${cssFamily}`
  const em = _mcCtx.measureText(charStr).width / fontSize
  _mcCache.set(key, em)
  return em
}

// ── Invisible-text overlay compositor ─────────────────────────────────────

/**
 * Pixel-composite the invisible-text buffer onto the main canvas.
 * For each pixel that was painted by Tr=3 text, compute a high-contrast
 * colour based on the underlying colour-buffer pixel (perceptual luminance):
 *   light background → deep blue,  dark background → bright cyan.
 */
function applyInvisibleOverlay(
  ctx: CanvasRenderingContext2D,
  invisibleCanvas: HTMLCanvasElement,
  w: number,
  h: number,
  whiteBackground = false,
): void {
  const colorData = ctx.getImageData(0, 0, w, h)
  const invData   = invisibleCanvas.getContext('2d')!.getImageData(0, 0, w, h)
  const cd = colorData.data
  const id = invData.data
  for (let i = 0; i < id.length; i += 4) {
    if (whiteBackground) {
      // Mode 2 – invisible only: blank non-invisible pixels to white, paint invisible in blue
      if (id[i + 3] >= 128) {
        cd[i] = 0; cd[i + 1] = 0; cd[i + 2] = 200; cd[i + 3] = 255
      } else {
        cd[i] = 255; cd[i + 1] = 255; cd[i + 2] = 255; cd[i + 3] = 255
      }
    } else {
      if (id[i + 3] < 128) continue                            // no invisible text here
      const r = cd[i], g = cd[i + 1], b = cd[i + 2]
      const L = 0.2126 * r + 0.7152 * g + 0.0722 * b          // perceptual luminance 0–255
      const t = L / 255                                         // 0 = dark bg, 1 = light bg
      // light bg (t→1) → deep blue;  dark bg (t→0) → bright cyan
      cd[i]     = Math.round((1 - t) * 50)
      cd[i + 1] = Math.round((1 - t) * 200)
      cd[i + 2] = Math.round(200 + t * 55)
      cd[i + 3] = 255
    }
  }
  ctx.putImageData(colorData, 0, 0)
}

// ── main render function ────────────────────────────────────────────────────

function renderOps(
  ctx: CanvasRenderingContext2D,
  invisibleCtx: CanvasRenderingContext2D | null,
  data: ContentStreamData,
  loadedImages: Map<string, HTMLImageElement | HTMLCanvasElement>,
  loadedFontFamilies: Map<string, string>,
  loadedOtFonts: Map<string, opentype.Font>,
  cidToGidMaps: Map<string, Uint16Array | null>,
  maxOps?: number,
): void {
  const ops = maxOps !== undefined ? data.operations.slice(0, maxOps) : data.operations
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

  // Show a raw PDF string token using the current text state.
  // For Type0 fonts with an embedded OpenType font: renders by GlyphID via opentype.js.
  // Fallback: decodes to Unicode and uses ctx.fillText.
  function showText(rawToken: string): void {
    if (!rawToken) return
    const [a, b, c, d, e, f] = ts.tm
    const fontEntry = curFont()
    const hs = ts.hScale / 100

    const otFont = loadedOtFonts.get(ts.fontName)
    const useOT  = otFont !== undefined && fontEntry?.subtype === 'Type0'

    if (useOT && otFont) {
      // ── OpenType.js path (CID fonts with embedded binary) ───────────────────
      const bytes = rawTokenToBytes(rawToken)
      if (bytes.length < 2) return
      const cidMap = cidToGidMaps.get(ts.fontName)  // null = Identity
      const isInvisible = ts.renderMode === 3
      const doFill   = isInvisible ? (invisibleCtx !== null) : (ts.renderMode === 0 || ts.renderMode === 2 || ts.renderMode === 4 || ts.renderMode === 6)
      const doStroke = isInvisible ? false : (ts.renderMode === 1 || ts.renderMode === 2 || ts.renderMode === 5 || ts.renderMode === 6)
      if (!doFill && !doStroke) {
        // invisible text, no invisible buffer — just advance
        const bytes2 = rawTokenToBytes(rawToken)
        for (let i = 0; i + 1 < bytes2.length; i += 2) {
          const cid2 = (bytes2[i] << 8) | bytes2[i + 1]
          const gid2 = (cidMap === null || cidMap === undefined) ? cid2 : (cid2 < cidMap.length ? cidMap[cid2] : 0)
          let glyph2: opentype.Glyph | undefined
          try { glyph2 = otFont.glyphs.get(gid2) } catch { /* skip */ }
          const advW2 = (glyph2?.advanceWidth ?? otFont.unitsPerEm) / otFont.unitsPerEm
          ts.tm = matMul([1, 0, 0, 1, (advW2 * ts.fontSize + ts.charSpacing) * hs, 0], ts.tm)
        }
        return
      }
      // Redirect invisible text to the invisible buffer; sync its CTM from ctx
      const targetCtx = (isInvisible && invisibleCtx) ? invisibleCtx : ctx
      if (isInvisible && invisibleCtx) invisibleCtx.setTransform(ctx.getTransform())

      targetCtx.save()
      targetCtx.transform(a, b, c, d, e, f)
      targetCtx.transform(ts.fontSize * hs, 0, 0, -ts.fontSize, 0, ts.rise)
      targetCtx.fillStyle   = isInvisible ? 'black' : gs.fillColor
      targetCtx.strokeStyle = isInvisible ? 'black' : gs.strokeColor

      let xPos = 0
      let advTotal = 0

      for (let i = 0; i + 1 < bytes.length; i += 2) {
        const cid = (bytes[i] << 8) | bytes[i + 1]
        const gid = (cidMap === null || cidMap === undefined)
          ? cid
          : (cid < cidMap.length ? cidMap[cid] : 0)

        let glyph: opentype.Glyph | undefined
        try { glyph = otFont.glyphs.get(gid) } catch { /* skip invalid GID */ }

        if ((doFill || doStroke) && glyph) {
          const otPath = glyph.getPath(xPos, 0, 1)
          targetCtx.beginPath()
          for (const cmd of otPath.commands) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const c = cmd as any
            if      (cmd.type === 'M') targetCtx.moveTo(c.x, c.y)
            else if (cmd.type === 'L') targetCtx.lineTo(c.x, c.y)
            else if (cmd.type === 'C') targetCtx.bezierCurveTo(c.x1, c.y1, c.x2, c.y2, c.x, c.y)
            else if (cmd.type === 'Q') targetCtx.quadraticCurveTo(c.x1, c.y1, c.x, c.y)
            else if (cmd.type === 'Z') targetCtx.closePath()
          }
          if (doFill)   targetCtx.fill('nonzero')
          if (doStroke) targetCtx.stroke()
        }

        // Advance: advW in em units; charSpacing in text space → glyph space
        const advW_em = (glyph?.advanceWidth ?? otFont.unitsPerEm) / otFont.unitsPerEm
        xPos     += advW_em + (ts.fontSize > 0 ? ts.charSpacing / ts.fontSize : 0)
        advTotal += (advW_em * ts.fontSize + ts.charSpacing) * hs
      }

      targetCtx.restore()
      targetCtx.beginPath()  // clear stale glyph subpaths so later path-paint ops don't re-stroke them
      ts.tm = matMul([1, 0, 0, 1, advTotal, 0], ts.tm)

    } else {
      // ── Fallback: render byte-by-byte using PDF Widths ──────────────────────
      // IMPORTANT: Widths[] is indexed by the ORIGINAL PDF byte code, NOT by the
      // Unicode codepoint of the decoded character.  Iterating over the decoded
      // text string and using ch.charCodeAt(0) gives wrong indices for any font
      // that uses a non-identity encoding (ligatures, custom encoding, etc.).
      // We therefore iterate over the raw bytes in parallel.
      const bytes = rawTokenToBytes(rawToken)
      if (!bytes.length) return

      const { cssFamily, bold, italic } = fontNameToStyle(fontEntry?.base_font)
      const family = loadedFontFamilies.get(ts.fontName) ?? cssFamily

      const widths    = fontEntry?.widths ?? null
      const firstChar = fontEntry?.first_char ?? 0
      const cmap      = fontEntry?.cmap ?? null
      const isInvisible = ts.renderMode === 3
      const doFill   = isInvisible ? (invisibleCtx !== null) : (ts.renderMode === 0 || ts.renderMode === 2 || ts.renderMode === 4 || ts.renderMode === 6)
      const doStroke = isInvisible ? false : (ts.renderMode === 1 || ts.renderMode === 2 || ts.renderMode === 5 || ts.renderMode === 6)
      // Redirect invisible text to the invisible buffer; sync its CTM from ctx
      const targetCtx = (isInvisible && invisibleCtx) ? invisibleCtx : ctx
      if (isInvisible && invisibleCtx) invisibleCtx.setTransform(ctx.getTransform())

      targetCtx.save()
      targetCtx.transform(a, b, c, d, e, f)
      targetCtx.transform(ts.fontSize * hs, 0, 0, -ts.fontSize, 0, ts.rise)
      const fontStyle = loadedFontFamilies.has(ts.fontName)
        ? `1px ${family}`
        : `${italic ? 'italic' : 'normal'} ${bold ? 'bold' : 'normal'} 1px ${family}`
      targetCtx.font = fontStyle
      targetCtx.fillStyle   = isInvisible ? 'black' : gs.fillColor
      targetCtx.strokeStyle = isInvisible ? 'black' : gs.strokeColor

      // xPos in glyph-space canvas units (1 unit = em = fontSize text-space units * hs)
      // adv  in text-space units for ts.tm update
      let xPos = 0
      let adv  = 0

      for (const b of bytes) {
        // Resolve the Unicode glyph string for this PDF byte code
        const charStr = cmap?.[b] !== undefined
          ? cmap[b]
          : (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '')

        if (doFill || doStroke) {
          if (charStr) {
            if (doFill)   targetCtx.fillText(charStr, xPos, 0)
            if (doStroke) targetCtx.strokeText(charStr, xPos, 0)
          }
        }

        // Width lookup: use the PDF byte code b (not Unicode charCode) as the index.
        // If the font has no Widths array, fall back to actual browser font metrics so
        // that intra-word character positions are accurate (0.55 is far off for narrow
        // glyphs like 'i', 'l', 't' whose real advance is ≈ 0.28 em).
        const w0 = (widths && b >= firstChar && b < firstChar + widths.length)
          ? widths[b - firstChar] / 1000
          : (charStr && ts.fontSize > 0
              ? measureEmWidth(charStr, family, ts.fontSize)
              : (b === 0x20 ? 0.25 : 0.55))

        const isSpace = b === 0x20
        const stepX = w0
          + (ts.fontSize > 0 ? ts.charSpacing / ts.fontSize : 0)
          + (isSpace && ts.fontSize > 0 ? ts.wordSpacing / ts.fontSize : 0)
        xPos += stepX
        adv  += stepX * ts.fontSize * hs
      }

      targetCtx.restore()
      targetCtx.beginPath()  // clear stale glyph subpaths so later path-paint ops don't re-stroke them
      ts.tm = matMul([1, 0, 0, 1, adv, 0], ts.tm)
    }
  }

  // Colour helpers that also set on ctx immediately
  function setFill(css: string)   { gs = { ...gs, fillColor: css };   ctx.fillStyle   = css }
  function setStroke(css: string) { gs = { ...gs, strokeColor: css }; ctx.strokeStyle = css }

  syncGS()
  ctx.beginPath()

  for (const op of ops) {
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
            ctx.transform(1, 0, 0, -1, 0, 1)
            ctx.drawImage(img instanceof HTMLCanvasElement ? img : img, 0, 0, 1, 1)
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
        if (ops.length >= 1) showText(ops[ops.length - 1].value)
        break
      case "'":
        ts.tlm = matMul([1, 0, 0, 1, 0, -ts.leading], ts.tlm)
        ts.tm  = [...ts.tlm]
        if (ops.length >= 1) showText(ops[ops.length - 1].value)
        break
      case '"':
        if (ops.length >= 3) {
          ts.wordSpacing = asNum(ops[0]); ts.charSpacing = asNum(ops[1])
          ts.tlm = matMul([1, 0, 0, 1, 0, -ts.leading], ts.tlm)
          ts.tm  = [...ts.tlm]
          showText(ops[2].value)
        }
        break
      case 'TJ':
        if (ops.length >= 1 && ops[0].type === 'array') {
          for (const item of parseTJArray(ops[0].value)) {
            if (item.kind === 'str') {
              showText(item.raw)
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

/** Composite a color image + a grayscale SMask onto an RGBA offscreen canvas. */
async function compositeWithSMask(
  colorImg: HTMLImageElement,
  smaskImg: HTMLImageElement,
): Promise<HTMLCanvasElement> {
  const w = colorImg.naturalWidth  || colorImg.width
  const h = colorImg.naturalHeight || colorImg.height
  const off = document.createElement('canvas')
  off.width = w; off.height = h
  const ctx = off.getContext('2d')!
  ctx.drawImage(colorImg, 0, 0)
  const imageData = ctx.getImageData(0, 0, w, h)

  // Draw SMask into a separate canvas and read its luma values
  const mOff = document.createElement('canvas')
  mOff.width = w; mOff.height = h
  const mCtx = mOff.getContext('2d')!
  mCtx.drawImage(smaskImg, 0, 0, w, h)
  const maskData = mCtx.getImageData(0, 0, w, h)

  // Apply alpha: the SMask red channel is the alpha (grayscale → all channels same)
  const d = imageData.data
  const m = maskData.data
  for (let i = 0; i < d.length; i += 4) {
    d[i + 3] = m[i]  // red channel of grayscale mask → alpha
  }
  ctx.putImageData(imageData, 0, 0)
  return off
}

const CsCanvasRenderer = forwardRef<CsCanvasHandle, Props>(({ data, uploadId, maxOps, invisibleMode = 0 }, ref) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef   = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let cancelled = false

    const mb = data.media_box
    if (!mb || mb.length < 4) {
      setStatus('error')
      setErrorMsg('No MediaBox — cannot determine page dimensions')
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return

    // Use CropBox if present — it defines the visible region viewers display.
    // Its [x0,y0,x1,y1] may be a sub-rect of MediaBox (e.g. landscape crop of portrait page).
    const cb = data.crop_box
    const [x0, y0, x1, y1] = (cb && cb.length >= 4) ? cb : mb
    const pdfW = x1 - x0
    const pdfH = y1 - y0
    if (pdfW <= 0 || pdfH <= 0) {
      setStatus('error')
      setErrorMsg(`Invalid MediaBox [${mb.join(', ')}]`)
      return
    }

    const containerW = (wrapRef.current?.parentElement ?? wrapRef.current)?.clientWidth ?? 800
    const dpr = window.devicePixelRatio || 1
    // 1 PDF point = 1/72 inch; browser 100% zoom = 96 CSS px/inch → scale = 96/72
    const CSS_PX_PER_PT = 96 / 72
    const cssW = Math.min(pdfW * CSS_PX_PER_PT, containerW)
    const scale = cssW / pdfW

    // Canvas backing store is scaled by DPR for crispness; CSS size matches natural size
    canvas.width  = Math.round(cssW * dpr)
    canvas.height = Math.round(pdfH * scale * dpr)
    canvas.style.width  = `${cssW}px`
    canvas.style.height = `${Math.round(pdfH * scale)}px`

    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = 'white'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    ctx.setTransform(scale * dpr, 0, 0, -scale * dpr, -x0 * scale * dpr, y1 * scale * dpr)

    setStatus('loading')

    const xobjectRes = data.resources?.xobject ?? {}
    const fontRes    = data.resources?.font    ?? {}

    // ── 1. Load embedded fonts ───────────────────────────────────────────
    const loadedFontFamilies = new Map<string, string>()    // non-CID FontFace fallback
    const loadedOtFonts      = new Map<string, opentype.Font>()  // CID fonts via opentype.js
    const cidToGidMaps       = new Map<string, Uint16Array | null>()  // CIDToGIDMap per font
    const fontPromises = Object.entries(fontRes)
      .filter(([, f]) => f.font_file_num !== null)
      .map(async ([name, f]) => {
        try {
          const r = await fetch(`/api/ttf_raw/${uploadId}/${f.font_file_num}/${f.font_file_gen}`)
          if (!r.ok) throw new Error(`HTTP ${r.status}`)
          const buf = await r.arrayBuffer()

          if (f.subtype === 'Type0') {
            // ── OpenType.js path for CID/Type0 fonts ──────────────────────
            const font = opentype.parse(buf)
            loadedOtFonts.set(name, font)
            // CIDToGIDMap: null entry means Identity (CID = GID directly)
            if (f.cid_to_gid_identity || f.cid_to_gid_num === null) {
              cidToGidMaps.set(name, null)
            } else {
              const r2 = await fetch(`/api/raw_stream/${uploadId}/${f.cid_to_gid_num}/${f.cid_to_gid_gen}`)
              if (r2.ok) {
                const raw = new Uint8Array(await r2.arrayBuffer())
                const n   = Math.floor(raw.length / 2)
                const arr = new Uint16Array(n)
                for (let i = 0; i < n; i++) arr[i] = (raw[2 * i] << 8) | raw[2 * i + 1]
                cidToGidMaps.set(name, arr)
              } else {
                cidToGidMaps.set(name, null)   // fallback to Identity
              }
            }
            console.debug(`[CsCanvas] OT font loaded: ${name} (${f.base_font})`)
          } else {
            // ── FontFace fallback for non-CID embedded fonts ───────────────
            const family = `PdfFont_${uploadId.slice(0, 8)}_${name}`
            const face = new FontFace(family, buf)
            await face.load()
            document.fonts.add(face)
            loadedFontFamilies.set(name, family)
          }
        } catch (err) {
          console.warn(`[CsCanvas] font load failed for ${name}:`, err)
        }
      })

    // ── 2. Load images (with SMask compositing) ──────────────────────────
    const loadedImages = new Map<string, HTMLImageElement | HTMLCanvasElement>()

    function loadImg(url: string): Promise<HTMLImageElement> {
      return new Promise(resolve => {
        const img = new Image()
        img.onload  = () => resolve(img)
        img.onerror = () => resolve(img)   // resolve anyway; draw will silently fail
        img.src = url
      })
    }

    const imagePromises = Object.entries(xobjectRes)
      .filter(([, v]) => v.subtype === 'Image')
      .map(async ([name, res]) => {
        const colorImg = await loadImg(imageUrl(uploadId, res.num, res.gen))
        if (res.smask_num !== null && res.smask_gen !== null) {
          try {
            const maskImg  = await loadImg(imageUrl(uploadId, res.smask_num, res.smask_gen))
            const composed = await compositeWithSMask(colorImg, maskImg)
            loadedImages.set(name, composed)
            return
          } catch {
            // fall through to plain image
          }
        }
        loadedImages.set(name, colorImg)
      })

    // Invisible-text offscreen buffer — always rendered; composited on demand
    const invisibleOffscreen = document.createElement('canvas')
    invisibleOffscreen.width  = canvas.width
    invisibleOffscreen.height = canvas.height
    const invCtx = invisibleOffscreen.getContext('2d')!
    // Note: invCtx starts transparent; the PDF CTM is synced per-glyph via ctx.getTransform()

    Promise.all([...fontPromises, ...imagePromises]).then(() => {
      if (cancelled) return
      try {
        renderOps(ctx, invCtx, data, loadedImages, loadedFontFamilies, loadedOtFonts, cidToGidMaps, maxOps)
        if (invisibleMode === 1) applyInvisibleOverlay(ctx, invisibleOffscreen, canvas.width, canvas.height, false)
        else if (invisibleMode === 2) applyInvisibleOverlay(ctx, invisibleOffscreen, canvas.width, canvas.height, true)
        setStatus('done')
      } catch (err) {
        setStatus('error')
        setErrorMsg(String(err))
      }
    })

    return () => { cancelled = true }
  }, [data, uploadId, maxOps, invisibleMode])

  useImperativeHandle(ref, () => ({
    savePng() {
      const canvas = canvasRef.current
      if (!canvas) return
      const link = document.createElement('a')
      link.download = 'page-render.png'
      link.href = canvas.toDataURL('image/png')
      link.click()
    }
  }))

  return (
    <div className="cs-render-outer">
      <div ref={wrapRef} className="cs-render-wrap">
        {status === 'loading' && <div className="cs-render-overlay">Rendering…</div>}
        {status === 'error'   && <div className="cs-render-error">{errorMsg}</div>}
        <canvas ref={canvasRef} className="cs-render-canvas" />
      </div>
    </div>
  )
})

export default CsCanvasRenderer
