// Accent color and interface size — per-browser preferences applied to the dashboard's CSS
// variables. Kept client-side (localStorage) so they apply instantly with no flash and need
// no backend round-trip.

const KEY = 'olisar_accent'
export const DEFAULT_ACCENT = '#5b9cf6'

// ── Interface size ───────────────────────────────────────────────────────────
// The console is sized in px throughout — 34px controls, a 244px rail, chart geometry — so
// there is no single font-size to turn up. `zoom` on :root scales the whole coordinate
// system, which is the same thing the browser's own Cmd +/− does, and it needs no refactor.
const SCALE_KEY = 'olisar_ui_scale'
export const DEFAULT_SCALE = 1.1
export const SCALES: { label: string; value: number }[] = [
  { label: '100%', value: 1 },
  { label: '110%', value: 1.1 },
  { label: '125%', value: 1.25 },
]

export function getScale(): number {
  try {
    const v = parseFloat(localStorage.getItem(SCALE_KEY) || '')
    return Number.isFinite(v) && v >= 0.75 && v <= 2 ? v : DEFAULT_SCALE
  } catch {
    return DEFAULT_SCALE
  }
}

export function applyScale(value?: number): number {
  const v = value ?? getScale()
  document.documentElement.style.setProperty('--ui-scale', String(v))
  return v
}

export function setScale(value: number): number {
  const v = applyScale(value)
  try { localStorage.setItem(SCALE_KEY, String(v)) } catch { /* private mode — session only */ }
  return v
}

/**
 * The live scale, for the few places that mix DOM geometry with CSS lengths.
 *
 * `getBoundingClientRect()` reports **viewport** pixels (already multiplied by the zoom),
 * while a CSS `left`/`top` you then set is interpreted in the **zoomed** coordinate space —
 * so feeding one straight into the other displaces the result by exactly this factor.
 * `offsetWidth`, `offsetLeft` and `ResizeObserver`'s contentRect are already element-local
 * and need no correction.
 */
export function uiScale(): number {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale'))
  return Number.isFinite(v) && v > 0 ? v : 1
}

export const ACCENTS: { name: string; value: string }[] = [
  { name: 'Violet', value: '#8a8af2' },
  { name: 'Iris', value: '#7c6cf0' },
  { name: 'Blue', value: '#5b9cf6' },
  { name: 'Teal', value: '#2dd4bf' },
  { name: 'Green', value: '#43cf8e' },
  { name: 'Amber', value: '#e0a458' },
  { name: 'Rose', value: '#f2728a' },
  { name: 'Red', value: '#ff6369' },
]

export function getAccent(): string {
  try {
    return localStorage.getItem(KEY) || DEFAULT_ACCENT
  } catch {
    return DEFAULT_ACCENT
  }
}

function rgbOf(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function softFrom(hex: string, alpha = 0.16): string {
  const rgb = rgbOf(hex)
  if (!rgb) return `rgba(138, 138, 242, ${alpha})`
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`
}

// WCAG relative luminance, and contrast against the app ground (--bg, #020203).
const BG_LUM = 0.000972  // luminance of #020203
function luminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4) }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
const contrastOnBg = (rgb: [number, number, number]) => (luminance(rgb) + 0.05) / (BG_LUM + 0.05)

/**
 * The accent is a free-form colour picker, but it isn't decoration: it carries links, the
 * focus ring, the toggle "on" track, meters and chart series. A dark pick made all of those
 * vanish into the near-black ground with no warning and no way back but Reset. Lift the
 * colour toward white — hue and saturation intact — until it clears 4.5:1 on --bg.
 * Returns the colour actually applied, which the picker echoes back.
 */
export function usableAccent(hex: string): string {
  const rgb = rgbOf(hex)
  if (!rgb) return DEFAULT_ACCENT
  if (contrastOnBg(rgb) >= 4.5) return hex.startsWith('#') ? hex.toLowerCase() : '#' + hex.toLowerCase()
  // Binary search the blend toward white; contrast rises monotonically with it.
  let lo = 0, hi = 1
  for (let i = 0; i < 18; i++) {
    const t = (lo + hi) / 2
    const mixed = rgb.map((c) => Math.round(c + (255 - c) * t)) as [number, number, number]
    if (contrastOnBg(mixed) >= 4.5) hi = t; else lo = t
  }
  const out = rgb.map((c) => Math.round(c + (255 - c) * hi))
  return '#' + out.map((c) => c.toString(16).padStart(2, '0')).join('')
}

export function applyAccent(hex?: string): string {
  const c = usableAccent(hex || getAccent())
  const root = document.documentElement
  root.style.setProperty('--accent', c)
  root.style.setProperty('--accent-soft', softFrom(c))
  // The ambient background blobs are tinted with the accent too.
  root.style.setProperty('--glow-a', softFrom(c, 0.18))
  root.style.setProperty('--glow-b', softFrom(c, 0.10))
  return c
}

/** Stores and applies an accent. Returns the colour actually in use — which differs from
 *  `hex` when the pick was too dark to read against the ground. */
export function setAccent(hex: string): string {
  const c = applyAccent(hex)
  try { localStorage.setItem(KEY, c) } catch { /* private mode — apply for the session */ }
  return c
}
