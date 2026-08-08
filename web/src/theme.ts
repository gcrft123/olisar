// Interface size — a per-browser preference applied to the dashboard's CSS variables.
// Kept client-side (localStorage) so it applies instantly with no flash and needs no
// backend round-trip.
//
// The accent used to be switchable here too. It isn't any more: colour carries state in
// this console (--ok / --warn / --danger / --info), three of the eight presets were
// byte-identical to semantic tokens, and picking Red turned every focus ring, link and
// "on" toggle into the danger colour — an enabled extension read as an error. --accent is
// now a fixed token in index.css.

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
