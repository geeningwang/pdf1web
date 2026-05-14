/**
 * opentype-compat.ts
 *
 * Runtime wrapper around opentype.js that adds cmap format 6
 * ("Trimmed Table Mapping") support.
 *
 * opentype.js only handles cmap formats 0, 4, 12, 13 and 14.
 * Format 6 is common in older Mac/Type1-descendant fonts.
 * When encountered, we rebuild the cmap table in a copy of the font
 * buffer, replacing every format-6 subtable with an equivalent
 * format-4 subtable, then hand the fixed buffer to opentype.parse.
 *
 * This module re-exports everything from opentype.js so callers can
 * use it as a drop-in replacement:
 *   import * as opentype from '../lib/opentype-compat'
 */

import * as opentype from 'opentype.js'

// ── Format-4 builder ──────────────────────────────────────────────────────────
// Builds a minimal format-4 subtable that encodes the same character→glyph
// mapping as a format-6 subtable with a single contiguous range.
//
// Format-4 layout for segCount=2 (one real segment + sentinel):
//   offset  size  field
//    0       2    format = 4
//    2       2    length = 32 + 2*entryCount
//    4       2    language = 0
//    6       2    segCountX2 = 4
//    8       2    searchRange = 4
//   10       2    entrySelector = 1
//   12       2    rangeShift = 0
//   14       2    endCount[0] = firstCode + entryCount - 1
//   16       2    endCount[1] = 0xFFFF  (sentinel)
//   18       2    reservedPad = 0
//   20       2    startCount[0] = firstCode
//   22       2    startCount[1] = 0xFFFF  (sentinel)
//   24       2    idDelta[0] = 0
//   26       2    idDelta[1] = 1         (0xFFFF + 1 = 0, wraps to .notdef)
//   28       2    idRangeOffset[0] = 4   (→ glyphIdArray[0])
//   30       2    idRangeOffset[1] = 0   (sentinel)
//   32      2*N   glyphIdArray[N]
//
// Derivation of idRangeOffset[0]=4:
//   opentype.js resolves: index = s - segCount + idRangeOffset/2 + (c - startCount)
//   For s=0, segCount=2, c=firstCode → index = 0 - 2 + 2 + 0 = 0  ✓
function buildFormat4(firstCode: number, glyphIds: number[]): ArrayBuffer {
  const entryCount = glyphIds.length
  const len = 32 + 2 * entryCount
  const buf = new ArrayBuffer(len)
  const d = new DataView(buf)
  d.setUint16(0,  4,                            false) // format
  d.setUint16(2,  len,                          false) // length
  d.setUint16(4,  0,                            false) // language
  d.setUint16(6,  4,                            false) // segCountX2
  d.setUint16(8,  4,                            false) // searchRange
  d.setUint16(10, 1,                            false) // entrySelector
  d.setUint16(12, 0,                            false) // rangeShift
  d.setUint16(14, firstCode + entryCount - 1,   false) // endCount[0]
  d.setUint16(16, 0xFFFF,                       false) // endCount[1] sentinel
  d.setUint16(18, 0,                            false) // reservedPad
  d.setUint16(20, firstCode,                    false) // startCount[0]
  d.setUint16(22, 0xFFFF,                       false) // startCount[1] sentinel
  d.setUint16(24, 0,                            false) // idDelta[0]
  d.setUint16(26, 1,                            false) // idDelta[1] sentinel
  d.setUint16(28, 4,                            false) // idRangeOffset[0]
  d.setUint16(30, 0,                            false) // idRangeOffset[1] sentinel
  for (let i = 0; i < entryCount; i++) {
    d.setUint16(32 + i * 2, glyphIds[i], false)
  }
  return buf
}

// ── Subtable length helper ────────────────────────────────────────────────────
function subtableLength(dv: DataView, absOffset: number): number {
  const fmt = dv.getUint16(absOffset, false)
  if (fmt === 0)  return 262
  if (fmt === 4 || fmt === 6) return dv.getUint16(absOffset + 2, false)
  if (fmt === 12 || fmt === 13 || fmt === 14) return dv.getUint32(absOffset + 4, false)
  // Fallback: try reading a UShort length field
  return dv.getUint16(absOffset + 2, false)
}

// ── Main buffer fixer ─────────────────────────────────────────────────────────
function fixCmapFormat6(buf: ArrayBuffer): ArrayBuffer {
  const src = new Uint8Array(buf)
  const dv  = new DataView(buf)

  // Locate the 'cmap' entry in the font table directory
  const numTables = dv.getUint16(4, false)
  let cmapAbsOffset = -1
  let cmapOldLength = -1
  let cmapDirEntryOffset = -1

  for (let i = 0; i < numTables; i++) {
    const de = 12 + i * 16
    const tag = String.fromCharCode(src[de], src[de+1], src[de+2], src[de+3])
    if (tag === 'cmap') {
      cmapAbsOffset    = dv.getUint32(de + 8,  false)
      cmapOldLength    = dv.getUint32(de + 12, false)
      cmapDirEntryOffset = de
      break
    }
  }
  if (cmapAbsOffset < 0) return buf   // no cmap table – can't help

  const numCmapTables = dv.getUint16(cmapAbsOffset + 2, false)

  // Collect per-encoding-record info
  interface RecInfo {
    platformId: number
    encodingId: number
    oldRelOffset: number   // relative to cmapAbsOffset
    fmt6FirstCode?: number
    fmt6GlyphIds?: number[]
    format: number
    oldSubLength: number
  }
  const records: RecInfo[] = []
  let hasFmt6 = false

  for (let i = 0; i < numCmapTables; i++) {
    const recOff = cmapAbsOffset + 4 + i * 8
    const platformId  = dv.getUint16(recOff,     false)
    const encodingId  = dv.getUint16(recOff + 2, false)
    const relOff      = dv.getUint32(recOff + 4, false)
    const subAbsOff   = cmapAbsOffset + relOff
    const format      = dv.getUint16(subAbsOff,  false)
    const subLen      = subtableLength(dv, subAbsOff)

    const rec: RecInfo = { platformId, encodingId, oldRelOffset: relOff, format, oldSubLength: subLen }

    if (format === 6) {
      hasFmt6 = true
      const firstCode  = dv.getUint16(subAbsOff + 6, false)
      const entryCount = dv.getUint16(subAbsOff + 8, false)
      const glyphIds: number[] = []
      for (let j = 0; j < entryCount; j++) {
        glyphIds.push(dv.getUint16(subAbsOff + 10 + j * 2, false))
      }
      rec.fmt6FirstCode  = firstCode
      rec.fmt6GlyphIds   = glyphIds
    }

    records.push(rec)
  }

  if (!hasFmt6) return buf   // no format 6 – nothing to fix

  // Build replacement subtable blobs and compute new relative offsets
  const blobs: ArrayBuffer[] = []
  const newRelOffsets: number[] = []
  let curRelOff = 4 + numCmapTables * 8   // header(4) + encoding records

  for (const rec of records) {
    newRelOffsets.push(curRelOff)
    if (rec.format === 6 && rec.fmt6GlyphIds && rec.fmt6FirstCode !== undefined) {
      const f4 = buildFormat4(rec.fmt6FirstCode, rec.fmt6GlyphIds)
      blobs.push(f4)
      curRelOff += f4.byteLength
    } else {
      const subAbsOff = cmapAbsOffset + rec.oldRelOffset
      blobs.push(buf.slice(subAbsOff, subAbsOff + rec.oldSubLength))
      curRelOff += rec.oldSubLength
    }
  }

  const newCmapLen = curRelOff

  // Assemble new cmap table
  const newCmapBuf = new ArrayBuffer(newCmapLen)
  const newCmapDv  = new DataView(newCmapBuf)
  const newCmapU8  = new Uint8Array(newCmapBuf)

  newCmapDv.setUint16(0, 0,             false)  // version
  newCmapDv.setUint16(2, numCmapTables, false)  // numTables

  for (let i = 0; i < records.length; i++) {
    const rec    = records[i]
    const destOff = 4 + i * 8
    newCmapDv.setUint16(destOff,     rec.platformId, false)
    newCmapDv.setUint16(destOff + 2, rec.encodingId, false)
    newCmapDv.setUint32(destOff + 4, newRelOffsets[i], false)
  }

  let writeOff = 4 + numCmapTables * 8
  for (const blob of blobs) {
    newCmapU8.set(new Uint8Array(blob), writeOff)
    writeOff += blob.byteLength
  }

  // Build new font buffer, splicing in the new cmap table
  const delta       = newCmapLen - cmapOldLength
  const newFontSize = buf.byteLength + delta
  const newFontBuf  = new ArrayBuffer(newFontSize)
  const newFontU8   = new Uint8Array(newFontBuf)
  const newFontDv   = new DataView(newFontBuf)

  // Copy bytes before cmap table
  newFontU8.set(src.subarray(0, cmapAbsOffset))
  // Insert new cmap table
  newFontU8.set(newCmapU8, cmapAbsOffset)
  // Copy bytes after old cmap table
  newFontU8.set(src.subarray(cmapAbsOffset + cmapOldLength), cmapAbsOffset + newCmapLen)

  // Update cmap entry in table directory: new length
  newFontDv.setUint32(cmapDirEntryOffset + 12, newCmapLen, false)

  // Shift offsets for any table that was located after the old cmap table
  if (delta !== 0) {
    for (let i = 0; i < numTables; i++) {
      const de = 12 + i * 16
      const tableOff = newFontDv.getUint32(de + 8, false)
      if (tableOff > cmapAbsOffset) {
        newFontDv.setUint32(de + 8, tableOff + delta, false)
      }
    }
  }

  return newFontBuf
}

// ── Drop-in parse replacement ─────────────────────────────────────────────────
export function parse(buffer: ArrayBuffer): opentype.Font {
  try {
    return opentype.parse(buffer)
  } catch (e: unknown) {
    if (e instanceof Error && /cmap|format/i.test(e.message)) {
      return opentype.parse(fixCmapFormat6(buffer))
    }
    throw e
  }
}

// Re-export everything else from opentype.js unchanged
export * from 'opentype.js'
