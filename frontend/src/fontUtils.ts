// Shared font detection utilities used by SimpleFontPane and Type0FontPane

export interface FontStyle {
  family: string
  weight: string
  style: string
}

export interface ResolvedFont {
  family: string       // the matched font name
  isFallback: boolean  // true if not the first choice in the CSS stack
  boldSynthesized: boolean
  italicSynthesized: boolean
}

export function fontStyleForPdf(baseFontName: string | null): FontStyle {
  if (!baseFontName) return { family: 'serif', weight: 'normal', style: 'normal' }
  // Strip PDF subset prefix (e.g. "AIFFRD+STKaiti" → "STKaiti")
  const stripped = baseFontName.replace(/^[A-Z]{6}\+/, '')
  const s = stripped.toLowerCase().replace(/[,_\-\s]+/g, '')
  const bold   = s.includes('bold')
  const italic = s.includes('italic') || s.includes('oblique')

  let family: string
  // --- Well-known Latin/Western fonts (no benefit from putting raw name first) ---
  if      (s.includes('timesnewroman') || s.includes('times'))
    family = '"Times New Roman", Times, serif'
  else if (s.includes('helvetica') || s.includes('arial'))
    family = 'Helvetica, Arial, sans-serif'
  else if (s.includes('courier'))
    family = '"Courier New", Courier, monospace'
  else if (s.includes('symbol'))
    family = 'Symbol, serif'
  else if (s.includes('dingbat') || s.includes('zapf'))
    family = '"Zapf Dingbats", "Wingdings", serif'
  else if (s.includes('palatino'))
    family = 'Palatino, "Palatino Linotype", serif'
  else if (s.includes('garamond'))
    family = 'Garamond, "EB Garamond", serif'
  else if (s.includes('georgia'))
    family = 'Georgia, serif'
  else if (s.includes('verdana'))
    family = 'Verdana, sans-serif'
  else if (s.includes('trebuchet'))
    family = '"Trebuchet MS", sans-serif'
  else if (s.includes('futura'))
    family = 'Futura, "Century Gothic", sans-serif'
  else {
    // For all other fonts (including CJK): put the actual font name first and
    // let the OS resolve it directly. Add a broad fallback chain based on a
    // simple sans/serif hint from the name — no manual CJK mapping table.
    const isSans = /hei|gothic|sans|meiryo|yahei|pingfang|noto.*sans|source.*sans/i.test(stripped)
    const isKai  = /kaiti|kai_gb|fzkai|\bkai\b/i.test(stripped)
    const cjkFallback = isSans
      ? '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", SimHei, sans-serif'
      : isKai
        // Kaiti (regular script): prefer kaiti-style fonts on all platforms
        ? 'STKaiti, "KaiTi_GB2312", "AR PL UKai CN", "Noto Serif CJK SC", "Source Han Serif SC", STSong, SimSun, serif'
        : '"Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", STSong, SimSun, serif'
    family = `"${stripped}", ${cjkFallback}`
  }

  return { family, weight: bold ? 'bold' : 'normal', style: italic ? 'italic' : 'normal' }
}

const GENERICS = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui'])

// CJK marker — if the fallback chain contains these, we're dealing with a CJK font stack
const CJK_FALLBACK_RE = /STKaiti|Songti|Noto.*CJK|Source Han|SimSun|SimHei|YaHei|PingFang|Mincho|Gothic.*CJK/i

/**
 * Canvas-only font detection: compare `"family", fallback` vs `fallback` alone.
 * Used only for bold/italic variant checks after the primary font is confirmed.
 */
export function isFontInstalled(family: string, style = 'normal', weight = 'normal', testChar = 'A'): boolean {
  const fc = document.createElement('canvas').getContext('2d')!
  const isLikelyMono = /courier|mono|console|typewriter/i.test(family)
  const fallback = isLikelyMono ? 'serif' : 'monospace'
  fc.font = `${style} ${weight} 72px ${fallback}`
  const baseW = fc.measureText(testChar).width
  fc.font = `${style} ${weight} 72px "${family}", ${fallback}`
  return fc.measureText(testChar).width !== baseW
}

export function detectFont(fontStyle: FontStyle): ResolvedFont {
  const families = fontStyle.family
    .split(',')
    .map(f => f.trim().replace(/^"|"$|^'|'$/g, ''))

  const isCJK = CJK_FALLBACK_RE.test(fontStyle.family)
  const fc = document.createElement('canvas').getContext('2d')!

  // Quote a family name for use in CSS font shorthand.
  // Generic keywords (serif, sans-serif, etc.) must NOT be quoted.
  // All other names MUST be quoted — multi-word names like "Noto Serif CJK SC"
  // contain "Serif" which is a CSS keyword and silently break font assignment
  // when unquoted.
  const q = (f: string) => GENERICS.has(f.toLowerCase()) ? f : `"${f}"`

  /**
   * Metric used to detect whether `family` actually changes rendering vs `tail`.
   *
   * For Latin stacks: advance width of 'A' — reliable since different fonts
   * have distinct glyph widths.
   *
   * For CJK stacks: advance width of '\u4e2d' (中) is ALWAYS 1em (fullwidth)
   * regardless of font, so advance width comparison always returns equal.
   * Instead, compare the sum of ink-bounding-box dimensions, which differ
   * between CJK typefaces (e.g. Songti SC serif strokes vs PingFang SC sans
   * strokes). If font A is not installed and the glyph falls through to font B,
   * both stacks measure the same ink bounds — so uninstalled fonts are skipped.
   */
  const metricOf = (fontStr: string): number => {
    fc.font = fontStr
    if (isCJK) {
      const m = fc.measureText('\u4e2d')
      // Use ink-bounding-box dimensions + font-level design metrics.
      // Advance width is ALWAYS 1em for fullwidth CJK — useless for comparison.
      // Ink bounds and font bounds differ between typefaces (e.g. Songti SC
      // serif vs PingFang SC sans), so an uninstalled font falls through to the
      // same typeface in the tail → identical metric → correctly skipped.
      return (m.actualBoundingBoxLeft ?? 0)
           + (m.actualBoundingBoxRight ?? 0)
           + (m.actualBoundingBoxAscent ?? 0)
           + (m.actualBoundingBoxDescent ?? 0)
           + (m.fontBoundingBoxAscent ?? 0)
           + (m.fontBoundingBoxDescent ?? 0)
    }
    return fc.measureText('A').width
  }

  for (let i = 0; i < families.length; i++) {
    const family = families[i]
    if (GENERICS.has(family.toLowerCase())) {
      return { family: `${family} (generic)`, isFallback: i > 0, boldSynthesized: false, italicSynthesized: false }
    }

    // Compare "family + rest-of-stack" vs "rest-of-stack" alone.
    // If they differ, this family changes the rendering — it is installed and
    // its glyphs are distinct from whatever the tail resolves to.
    const tail = families.slice(i + 1).map(q).join(', ') || 'monospace'
    const mTail = metricOf(`normal normal 72px ${tail}`)
    const mWith = metricOf(`normal normal 72px ${q(family)}, ${tail}`)

    if (mWith !== mTail) {
      const testChar = isCJK ? '\u4e2d' : 'A'
      const boldAvail   = fontStyle.weight === 'bold'   ? isFontInstalled(family, 'normal', 'bold',   testChar) : true
      const italicAvail = fontStyle.style  === 'italic' ? isFontInstalled(family, 'italic', 'normal', testChar) : true
      return { family, isFallback: i > 0, boldSynthesized: !boldAvail, italicSynthesized: !italicAvail }
    }
  }

  return { family: families[families.length - 1] + ' (generic)', isFallback: true, boldSynthesized: false, italicSynthesized: false }
}
