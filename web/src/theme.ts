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

// How much of the zoom `getBoundingClientRect()` has *already* applied — measured, not
// assumed, because engines disagree.
//
// `zoom` was standardised in Chromium 128: from there on a rect comes back in viewport px
// (zoom included), which is what `uiScale()` above documents. Older engines report the rect
// in the same space as the CSS length you feed it to. The desktop app is Electron 31 —
// Chromium 126 — so the console and the app sit on opposite sides of that change, and a
// single hard-coded correction cannot be right in both: dividing by the scale on the old
// engine pushed every tooltip left by ~9% of its distance from the origin, which is why a
// toolbar button near the right edge showed its tooltip ~160px off to the left.
//
// So ask the engine. A fixed 100px probe measures 110 where rects are zoomed and 100 where
// they aren't, and callers divide by whatever comes back — correct on both, and on whatever
// Electron ships next. Cached per scale value, so this costs one layout per scale change.
let rectProbe: { scale: number; k: number } | null = null

export function rectScale(): number {
  const scale = uiScale()
  if (rectProbe && rectProbe.scale === scale) return rectProbe.k
  let k = scale
  try {
    const probe = document.createElement('div')
    probe.style.cssText =
      'position:fixed;left:0;top:0;width:100px;height:0;visibility:hidden;pointer-events:none'
    document.body.appendChild(probe)
    const w = probe.getBoundingClientRect().width
    probe.remove()
    if (w > 0) k = w / 100
  } catch {
    /* no document to probe — fall back to the configured scale */
  }
  rectProbe = { scale, k }
  return k
}

/** A rect measurement converted to viewport px, for comparing against `window.innerWidth`
 *  / `innerHeight` (always viewport px) or against a CSS length multiplied by `uiScale()`.
 *  Identity where the engine already zooms rects; scales up where it doesn't. */
export function rectToViewport(px: number): number {
  return px * (uiScale() / rectScale())
}
