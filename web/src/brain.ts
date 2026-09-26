// ── The final server screen ─────────────────────────────────────────────────────────
// Once the server runs healthy and Discord lists the console's redirect, the stats screen
// folds away: the form moves to the middle of the window, the panel's title, status and
// controls shrink into its top-left corner, and memories of what the bot has been doing bud
// off the form and settle on a ring around it. Anything else brings the stats screen back the
// same way.
//
// This is the part that isn't React: the controller that runs the change between the two
// screens and the memories' motion, writing styles and the form's framing directly every
// frame. It finds its pieces by class inside the onboarding shell (onboarding.tsx, server.tsx).

import type { Activity, ActivityItem, ActivityKind } from './activity'
import { zoomOf, type Form, type Framing, type Satellite, type View } from './form'

const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x))
const seg = (p: number, a: number, b: number) => clamp((p - a) / (b - a), 0, 1)
const smooth = (t: number) => t * t * (3 - 2 * t)
const inOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const approach = (x: number, to: number, step: number) => (x < to ? Math.min(to, x + step) : Math.max(to, x - step))
const TAU = Math.PI * 2
const wrap = (a: number) => ((a % TAU) + TAU) % TAU
const turnTo = (a: number, b: number) => wrap(b - a + Math.PI) - Math.PI   // shortest signed angle from a to b

type Rect = { x: number; y: number; w: number; h: number }
function rectL(el: Element): Rect {
  const r = el.getBoundingClientRect(), z = zoomOf()
  return { x: r.left / z, y: r.top / z, w: r.width / z, h: r.height / z }
}

/** Each kind's size on screen: the diameter of its sphere, in layout px, at a 900px window. */
export const SIZE: Record<ActivityKind, number> = {
  reply: 184, impression: 172, remembered: 160, reminder: 160, image: 160, glossary: 152, learned: 164,
  status: 152, member: 140, people: 156, health: 148,
}

type Body = {
  id: string; kind: ActivityKind; th: number; slot: number; seed: number; ph: number
  x: number; y: number; vx: number; vy: number; hx: number; hy: number; ax: number; ay: number
  r: number; rT: number; act: number; actT: number; releaseAt: number; released: boolean
  peek: number; peekT: number; pinned: boolean; ripple: number; text: number; node: HTMLElement | null
  rank: number; ov: number; ovT: number; isOpen: boolean; returning: boolean; leaving: boolean
  thinking: boolean; absorbAt: number; u: number
}

type Pair = { key: string; hud: HTMLElement; stats: HTMLElement; A: Rect; B: Rect; win: [number, number] }

export type Brain = ReturnType<typeof createBrain>

export function createBrain({ root, activity, open: startOpen }: { root: HTMLElement; activity: Activity; open: boolean }) {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
  const $ = <T extends HTMLElement = HTMLElement>(sel: string) => root.querySelector<T>(sel)
  const $$ = <T extends HTMLElement = HTMLElement>(sel: string) => root.querySelectorAll<T>(sel)
  const subs = new Set<() => void>()
  const notify = () => subs.forEach((fn) => fn())
  let form: Form | null = null, detachHook: (() => void) | null = null
  let p = startOpen ? 1 : 0, target = p
  let open = false                        // memories are out
  let view = { w: innerWidth / zoomOf(), h: innerHeight / zoomOf() }
  let slot: Rect | null = null, hudRect: Rect | null = null
  let pairs: Pair[] = []
  const bodies = new Map<string, Body>()
  const slots: (string | null)[] = new Array(12).fill(null)  // the form's sphere for each body, kept stable
  let order: string[] = []
  let lastStep = 0, raf = 0, applied = -1, time = 0, last = 0, dead = false
  // One memory can be opened: it comes to the middle, large, with its context, while the form
  // and the rest draw back. `openness` is how far that has got (0 to 1).
  let openId: string | null = null, shownId: string | null = null, openness = 0

  function measure() {
    view = { w: innerWidth / zoomOf(), h: innerHeight / zoomOf() }
    const s = $('.onb-stage')
    slot = s && s.getClientRects().length ? rectL(s) : null
    const hud = $('.brain-hud')
    hudRect = hud && hud.getClientRects().length ? rectL(hud) : null
    $('.mems')?.style.setProperty('--sv', scaleV().toFixed(3))
    applied = -1
  }
  const onResize = () => { measure(); if (target !== p) measurePairs(); kick() }
  window.addEventListener('resize', onResize)

  // ── Framing ──
  function stageFraming(s: Rect, v: View): Framing {
    const x = s.x - v.x, y = s.y - v.y
    return { cx: x + s.w - 0.2 * s.h, cy: y + s.h / 2, r: 0.45 * s.h, x0: x, x1: x + 0.3 * s.w, fy: 0.1 * s.h }
  }
  function brainFraming(v: View): Framing {
    const r0 = Math.min(0.23 * v.h, 0.3 * v.w)
    const cy = Math.max(v.h / 2, hudRect ? hudRect.y + hudRect.h + 0.74 * r0 + 48 : 0)
    // An opened memory takes the middle; the form draws back behind it.
    return { cx: v.w / 2, cy: Math.min(cy, v.h - 0.74 * r0 - 24), r: r0 * (1 - 0.45 * openness), x0: -2, x1: -1, fy: 0.02 * v.h }
  }
  // The form's resting radius on the final screen, whatever is open.
  const coreR = () => 0.74 * Math.min(0.23 * view.h, 0.3 * view.w)
  function framingFor(v: View): Framing {
    const Fb = brainFraming(v)
    if (!slot || slot.w < 2) return Fb
    const Fs = stageFraming(slot, v)
    if (reduce.matches) return p < 0.5 ? Fs : Fb
    const e = inOut(seg(p, 0.1, 0.85))
    const F = {} as Framing
    for (const k of Object.keys(Fb) as (keyof Framing)[]) F[k] = lerp(Fs[k], Fb[k], e)
    return F
  }
  const orbFrame = () => framingFor({ x: 0, y: 0, w: view.w, h: view.h })

  // ── The shared pieces: title, badge, the two buttons, and the logo ──
  // The corner's copies start exactly over the stats screen's, take over from them in a few
  // frames, and travel; the stats screen's own never move (the wizard's half clips).
  const WIN: Record<string, [number, number]> = { logo: [0, 0.45], title: [0.1, 0.68], badge: [0.1, 0.68], console: [0.14, 0.72], power: [0.14, 0.72] }
  const statsFor = (key: string) => $(`.onb-pane [data-morph="${key}"]`) || $(`.onb-rail [data-morph="${key}"]`)
  function measurePairs() {
    const rail = $('.onb-rail')
    const saved = rail ? rail.style.transform : ''
    if (rail) rail.style.transform = ''
    pairs = []
    for (const hud of $$('.brain-hud [data-morph]')) {
      const key = hud.getAttribute('data-morph') || ''
      const stats = statsFor(key)
      if (!stats || !stats.getClientRects().length) continue
      const t = hud.style.transform
      hud.style.transform = ''
      pairs.push({ key, hud, stats, A: rectL(stats), B: rectL(hud), win: WIN[key] || [0.1, 0.7] })
      hud.style.transform = t
    }
    if (rail) rail.style.transform = saved
    applied = -1
  }

  function applyTransition() {
    if (applied === p) return
    applied = p
    const rm = reduce.matches
    root.classList.toggle('brain-on', p > 0 || target > 0)
    root.classList.toggle('brain-full', p >= 1)
    root.style.setProperty('--brain', p.toFixed(3))
    // The stats screen's details: gone in the first third.
    const out = rm ? smooth(seg(p, 0.15, 0.6)) : inOut(seg(p, 0, 0.3))
    for (const el of $$('.onb-pane [data-fade]')) {
      el.style.opacity = p > 0 ? String(1 - out) : ''
      el.style.transform = p > 0 && !rm ? `translateX(${(-12 * out).toFixed(2)}px)` : ''
    }
    const rail = $('.onb-rail')
    if (rail) {
      const r = rm ? smooth(seg(p, 0.15, 0.6)) : inOut(seg(p, 0.05, 0.45))
      rail.style.transform = p > 0 && !rm ? `translateX(${(-100 * r).toFixed(2)}%)` : ''
      rail.style.opacity = p > 0 && rm ? String(1 - r) : ''
    }
    if (p > 0 && p < 1) {
      const saved = rail ? rail.style.transform : ''
      if (rail) rail.style.transform = ''
      for (const q of pairs) {
        if (!q.stats.isConnected) {
          const el = statsFor(q.key)
          if (el) q.stats = el
        }
        if (q.stats.isConnected && q.stats.getClientRects().length) q.A = rectL(q.stats)
      }
      if (rail) rail.style.transform = saved
    }
    for (const q of pairs) {
      if (!q.hud.isConnected || !q.stats.isConnected) continue
      const [a, b] = q.win
      // Text scales by its height (the two sizes share proportions); the buttons don't quite,
      // so they meet halfway (the mean of the two ratios, centred) and hand over in a few
      // frames: a longer cross-fade shows two sizes of the same words.
      const btn = q.key === 'console' || q.key === 'power'
      const swap = rm ? smooth(seg(p, 0.2, 0.8)) : seg(p, a, a + 0.03)
      const e = rm ? 1 : inOut(seg(p, a + 0.02, b))
      q.stats.style.opacity = p > 0 ? String(1 - swap) : ''
      const s0 = btn ? Math.sqrt((q.A.w / Math.max(1, q.B.w)) * (q.A.h / Math.max(1, q.B.h))) : q.A.h / Math.max(1, q.B.h)
      // Centre on the stats piece at the start (origin top-left), then travel home.
      const tx = q.A.x + q.A.w / 2 - (q.B.x + (q.B.w * s0) / 2), ty = q.A.y + q.A.h / 2 - (q.B.y + (q.B.h * s0) / 2)
      const s1 = s0 + (1 - s0) * e
      q.hud.style.transform = e < 1 ? `translate(${(tx * (1 - e)).toFixed(2)}px, ${(ty * (1 - e)).toFixed(2)}px) scale(${s1.toFixed(4)})` : ''
      q.hud.style.opacity = String(swap)
    }
    const extra = rm ? smooth(seg(p, 0.4, 0.9)) : smooth(seg(p, 0.55, 0.85))
    for (const el of $$('.brain-hud [data-extra]')) el.style.opacity = String(extra)
    // Where there's no form on the stats screen (narrow, or reduced motion), the form's layer
    // fades out and in rather than the form travelling.
    const layer = $('.orb-layer')
    if (layer) {
      const narrow = !slot || slot.w < 2
      layer.style.opacity = narrow ? String(smooth(seg(p, 0.25, 0.75))) : rm ? String(Math.abs(2 * p - 1)) : ''
    }
    const pane = $('.onb-pane'), hud = $('.brain-hud'), mems = $('.mems')
    const statsGone = p >= 1 && target === 1, brainGone = p <= 0 && target === 0
    if (pane) pane.style.visibility = statsGone ? 'hidden' : ''
    if (rail) rail.style.visibility = statsGone ? 'hidden' : ''
    if (hud) hud.style.visibility = brainGone ? 'hidden' : 'visible'
    if (mems) mems.style.visibility = brainGone ? 'hidden' : 'visible'
  }

  // Keyboard focus follows the change: to the same control on the other side, or to the other
  // side's heading when that control can't take it (Stop turns into Working…).
  function moveFocus(toBrain: boolean, a: Element | null) {
    const from = toBrain ? a?.closest?.('.onb-pane, .onb-rail') : a?.closest?.('.brain-hud, .mems')
    if (!from || !a) return
    const key = a.closest('[data-morph]')?.getAttribute('data-morph')
    const side = toBrain ? '.brain-hud' : '.onb-pane'
    let el = key ? ($<HTMLButtonElement>(`${side} [data-morph="${key}"]`) || (!toBrain ? $<HTMLButtonElement>(`.onb-rail [data-morph="${key}"]`) : null)) : null
    if (!el || el.disabled || el.tagName === 'IMG') el = $(`${side} h1`) as HTMLButtonElement | null
    el?.focus({ preventScroll: true })
  }
  let lastBlur: { el: Element; at: number } | null = null
  const onFocusOut = (e: FocusEvent) => { lastBlur = { el: e.target as Element, at: performance.now() } }
  document.addEventListener('focusout', onFocusOut)
  function setInert(toBrain: boolean) {
    const set = (sel: string, v: boolean) => { const el = $(sel); if (el) el.inert = v }
    set('.onb-pane', toBrain); set('.onb-rail', toBrain); set('.brain-hud', !toBrain); set('.mems', !toBrain)
  }

  // ── Memories ──
  const capacity = () => clamp(Math.floor((view.w * view.h) / 118000), 3, 8)
  const scaleV = () => clamp(Math.min(view.h / 818, view.w / 700), 0.72, 1.15)
  function visibleIds() {
    const list = activity.items().slice(0, capacity()).map((m) => m.id)
    return new Set(['health', ...list])
  }
  const itemFor = (id: string): ActivityItem | undefined => (id === 'health' ? activity.health() : activity.items().find((m) => m.id === id))
  // Where memories rest: all on one ring round the form, a wide ellipse, centred on it whatever
  // their sizes, with the same straight-line gap between neighbours' edges. Only the stretch of
  // ring where a memory would overlap the corner cluster is left out, and with nothing in the
  // way the ring closes on itself. They keep their order round it; a new one goes in the widest
  // gap.
  const ellipse = () => [clamp((1.05 * view.w) / view.h, 0.9, 1.9), clamp((0.85 * view.h) / view.w, 1, 1.6)]
  const restR = (b: { kind: ActivityKind }) => ((SIZE[b.kind] || 150) / 2) * scaleV()
  const openR = () => Math.min(0.29 * view.h, (view.w < 560 ? 0.47 : 0.44) * view.w, 280)
  // How far out the ring sits along the ellipse's short axis: past the form (or an opened
  // memory) by the largest memory's size.
  function ringD() {
    const shrink = lerp(1, 0.55, openness)
    return lerp(coreR(), openR(), openness) + (SIZE.reply / 2) * scaleV() * shrink + lerp(30, 40, openness)
  }
  type Arc = { key: string; theta: (u: number) => number; frac: (t: number) => number; sx: number; sy: number; d: number; closed: boolean }
  let arcIn = '', arcCache: Arc | null = null
  function arc(F: Framing): Arc {
    const [sx, sy] = ellipse(), d = ringD(), hud = hudRect
    const inKey = `${sx.toFixed(3)}|${sy.toFixed(3)}|${d.toFixed(1)}|${F.cx.toFixed(1)}|${F.cy.toFixed(1)}|${hud ? `${hud.x | 0},${hud.y | 0},${hud.w | 0},${hud.h | 0}` : ''}`
    if (inKey === arcIn && arcCache) return arcCache
    arcIn = inKey
    // Where round the ring a full-size memory would touch the corner cluster.
    let lo: number | null = null, cut = 0
    if (hud) {
      const K = 360, pad = 18, r = (SIZE.reply / 2) * scaleV() * lerp(1, 0.55, openness)
      const hit: boolean[] = []
      for (let i = 0; i < K; i++) {
        const t = ((i + 0.5) * TAU) / K
        const x = F.cx + d * sx * Math.cos(t), y = F.cy + d * sy * Math.sin(t)
        const ex = x - clamp(x, hud.x - pad, hud.x + hud.w + pad), ey = y - clamp(y, hud.y - pad, hud.y + hud.h + pad)
        hit.push(Math.hypot(ex, ey) < r)
      }
      const free = hit.indexOf(false)
      if (free >= 0 && hit.includes(true)) {
        let run = 0, runStart = 0, best = 0, bestStart = 0
        for (let k = 1; k <= K; k++) {
          const i = (free + k) % K
          if (hit[i]) { if (!run) runStart = i; run++; if (run > best) { best = run; bestStart = runStart } } else run = 0
        }
        lo = (bestStart * TAU) / K; cut = (best * TAU) / K
      }
    }
    const closed = lo == null
    // A closed ring's seam is on the corner's side, so the order reads round from there.
    const seam = hud ? Math.atan2(hud.y + hud.h / 2 - F.cy, hud.x + hud.w / 2 - F.cx) : -2.5
    const start = closed ? seam : (lo as number) + cut, span = TAU - cut
    const K = 240, th = new Float32Array(K + 1), acc = new Float32Array(K + 1)
    let total = 0
    for (let i = 0; i <= K; i++) {
      th[i] = start + (span * i) / K
      if (i) { const t = th[i] - span / K / 2; total += Math.hypot(sx * Math.sin(t), sy * Math.cos(t)) * (span / K) }
      acc[i] = total
    }
    const theta = (u: number) => {
      const want = clamp(u, 0, 1) * total
      let a = 1, b = K
      while (a < b) { const mid = (a + b) >> 1; if (acc[mid] < want) a = mid + 1; else b = mid }
      return th[a - 1] + (th[a] - th[a - 1]) * ((want - acc[a - 1]) / Math.max(1e-6, acc[a] - acc[a - 1]))
    }
    // Where along the ring (0 to 1) an angle falls; inside the left-out stretch, its nearer end.
    const frac = (t: number) => {
      let rel = wrap(t - start)
      if (!closed && rel > span) rel = rel - span < (TAU - span) / 2 ? span : 0
      const f = (rel / span) * K, i = Math.min(K - 1, Math.floor(f))
      return (acc[i] + (acc[i + 1] - acc[i]) * (f - i)) / total
    }
    arcCache = { key: `${inKey}|${start.toFixed(3)}|${span.toFixed(3)}`, theta, frac, sx, sy, d, closed }
    return arcCache
  }
  function ringPoint(F: Framing, th: number): [number, number] {
    const A = arc(F)
    return [F.cx + A.d * A.sx * Math.cos(th), F.cy + A.d * A.sy * Math.sin(th)]
  }
  // The ring's spacing: the straight-line gap between neighbours' edges is the same all the way
  // round (plus half of it at either end of an open ring). The gap is found by bisection, each
  // next centre by bisection along the ellipse. Cached until the ring or its sizes change.
  let placeKey = '', placed: number[] = []
  function chain(A: Arc, radii: number[]) {
    const key = `${A.key}|${radii.map((r) => r.toFixed(1)).join(',')}`
    if (key === placeKey) return placed
    const n = radii.length, d = A.d
    const pt = (u: number) => { const t = A.theta(Math.min(u, 1)); return [d * A.sx * Math.cos(t), d * A.sy * Math.sin(t)] }
    // The next centre at a straight-line distance from this one. The distance only grows over
    // less than half a ring, so the search stays within that (on a closed ring the far end comes
    // back round to the start).
    const next = (u0: number, dist: number) => {
      const [x0, y0] = pt(u0), cap = Math.min(1, u0 + 0.45), [xc, yc] = pt(cap)
      if (Math.hypot(xc - x0, yc - y0) < dist) return 2
      let lo = u0, hi = cap
      for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2, [x, y] = pt(mid); if (Math.hypot(x - x0, y - y0) < dist) lo = mid; else hi = mid }
      return hi
    }
    const run = (gap: number): [number[], number] => {
      const us: number[] = []
      let u = A.closed ? 0 : next(0, radii[0] + gap / 2)
      us.push(u)
      for (let i = 1; i < n && u <= 1; i++) { u = next(u, radii[i - 1] + radii[i] + gap); us.push(u) }
      const tail = u > 1 ? 2 : next(u, A.closed ? radii[n - 1] + radii[0] + gap : radii[n - 1] + gap / 2)
      return [us, tail]
    }
    let us: number[]
    if (!n) us = []
    else if (n === 1 || run(0)[1] > 1) {
      // One memory, or no room for any gap: spread them evenly instead.
      us = radii.map((_, i) => (A.closed ? i / n : (i + 0.5) / n))
    } else {
      let lo = 0, hi = 600
      for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2; if (run(mid)[1] > 1) hi = mid; else lo = mid }
      us = run(lo)[0]
    }
    placeKey = key; placed = us
    return us
  }
  // Target angles for the ring's memories, in their order. An open ring spaces them between its
  // ends. A closed one can sit anywhere round, so it's laid from the first memory, turned to sit
  // as near as it can to where they all are, and laid again from there (turning a laid ring
  // would spoil its gaps, since the ellipse bends differently further round).
  function placeRing(A: Arc, radii: number[], cur: number[]): number[] {
    if (!A.closed) return chain(A, radii).map((u) => A.theta(u))
    const n = radii.length
    if (n < 2) return cur.slice()
    const d = A.d
    const pt = (t: number) => [d * A.sx * Math.cos(t), d * A.sy * Math.sin(t)]
    const next = (t0: number, dist: number, limit: number) => {
      const [x0, y0] = pt(t0), cap = Math.min(limit, t0 + 0.9 * Math.PI), [xc, yc] = pt(cap)
      if (Math.hypot(xc - x0, yc - y0) < dist) return Infinity
      let lo = t0, hi = cap
      for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2, [x, y] = pt(mid); if (Math.hypot(x - x0, y - y0) < dist) lo = mid; else hi = mid }
      return hi
    }
    const run = (t0: number, gap: number): [number[], number] => {
      const ts = [t0]
      let t = t0
      for (let i = 1; i < n; i++) { t = next(t, radii[i - 1] + radii[i] + gap, t0 + TAU); if (!isFinite(t)) return [ts, Infinity]; ts.push(t) }
      return [ts, next(t, radii[n - 1] + radii[0] + gap, t0 + TAU)]
    }
    const lay = (t0: number) => {
      if (!(run(t0, 0)[1] <= t0 + TAU)) return radii.map((_, i) => t0 + (TAU * i) / n)
      let lo = 0, hi = 600
      for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2; if (run(t0, mid)[1] <= t0 + TAU) lo = mid; else hi = mid }
      return run(t0, lo)[0]
    }
    const first = lay(cur[0])
    let c = 0, sn = 0
    first.forEach((t, i) => { const a = turnTo(t, cur[i]); c += Math.cos(a); sn += Math.sin(a) })
    return lay(cur[0] + Math.atan2(sn, c))
  }
  function pickTheta(F: Framing) {
    const A = arc(F)
    const us = [...bodies.values()].filter((b) => !b.leaving).map((b) => A.frac(b.th)).sort((a, c) => a - c)
    if (!us.length) return A.theta(0.5)
    let best = 0.5, widest = -1
    if (A.closed) {
      for (let i = 0; i < us.length; i++) {
        const a = us[i], b = i + 1 < us.length ? us[i + 1] : us[0] + 1
        if (b - a > widest) { widest = b - a; best = ((a + b) / 2) % 1 }
      }
      return A.theta(best)
    }
    const pts = [0, ...us, 1]
    for (let i = 0; i < pts.length - 1; i++) {
      const gp = (pts[i + 1] - pts[i]) * (i === 0 || i === pts.length - 2 ? 2 : 1)
      if (gp > widest) { widest = gp; best = i === 0 ? pts[1] / 2 : i === pts.length - 2 ? (pts[i] + 1) / 2 : (pts[i] + pts[i + 1]) / 2 }
    }
    return A.theta(best)
  }
  function spawn(id: string, delay: number): Body | undefined {
    const F = orbFrame(), item = itemFor(id)
    if (!item) return
    const th = pickTheta(F)
    const R = 0.74 * F.r
    const free = slots.indexOf(null)
    if (free < 0) return
    const [hx, hy] = ringPoint(F, th)
    const b: Body = {
      id, kind: item.kind, th, slot: free, seed: Math.random(), ph: Math.random() * 6.28,
      x: F.cx + R * 0.82 * Math.cos(th), y: F.cy + R * 0.82 * Math.sin(th), vx: 0, vy: 0, hx, hy, ax: 0, ay: 0,
      r: 12, rT: 12, act: 0, actT: 0, releaseAt: performance.now() + delay, released: false,
      peek: 0, peekT: 0, pinned: false, ripple: 0, text: 0, node: null, rank: 0, ov: 0, ovT: 0, isOpen: false,
      returning: false, leaving: false, thinking: false, absorbAt: 0, u: 0,
    }
    slots[free] = id
    bodies.set(id, b)
    return b
  }
  function reorder() {
    const at = (id: string) => (id === 'health' ? -Infinity : itemFor(id)?.at ?? 0)
    order = [...bodies.keys()].sort((a, c) => at(c) - at(a))
    notify()
  }
  // Spawn what's newly visible. `releaseGap` staggers a batch; a single new reply is thought
  // about first: the form livens for a moment before the memory buds.
  function syncBodies(releaseGap: number, thinking: boolean) {
    if (!open) return
    const vis = visibleIds()
    let changed = false, i = 0
    for (const id of vis) {
      if (bodies.has(id)) { i++; continue }
      const item = itemFor(id)
      const reply = thinking && item?.kind === 'reply'
      const b = spawn(id, reply ? 1000 : releaseGap * i)
      if (b && reply) { b.thinking = true; form?.energy(0.55) }
      i++; changed = true
    }
    if (changed) reorder()
  }
  function openMemories(intro: boolean) {
    if (open) return
    open = true
    activity.start()
    // Newest first, one after another.
    const vis = [...visibleIds()]
    vis.forEach((id, i) => { if (!bodies.has(id)) spawn(id, (intro ? 1400 : 0) + i * (intro ? 220 : 90)) })
    reorder()
  }
  function closeMemories() {
    if (!open) return
    open = false
    activity.stop()
    shut(true)
    const now = performance.now()
    let i = 0
    for (const b of [...bodies.values()].sort((a, c) => a.rank - c.rank)) { b.actT = 0; b.absorbAt = now + 40 * i++ }
  }
  const unsubscribe = activity.subscribe((type, item) => {
    if (type === 'add' && open) syncBodies(item ? 0 : 220, !!item)
    if (type === 'beat') { const b = bodies.get('health'); if (b) b.ripple = 1 }
    notify()
  })

  // ── Opening one ──
  function openOne(id: string) {
    const b = bodies.get(id)
    if (!b || !open || b.act < 0.5) return
    if (openId && openId !== id) { const a = bodies.get(openId); if (a) { a.ovT = 0; a.isOpen = false } }
    openId = id; shownId = id
    b.ovT = 1; b.isOpen = true; b.pinned = false; b.peekT = 0
    const mems = $('.mems'); if (mems) mems.inert = true
    form?.mood({ dim: 0.2 })
    notify(); kick()
  }
  // quiet: the whole screen is leaving, so don't touch the form's mood or move focus.
  function shut(quiet: boolean) {
    if (!openId) return
    const id = openId, b = bodies.get(id)
    if (b) { b.ovT = 0; b.isOpen = false; b.returning = true; b.peekT = 0; b.pinned = false }
    openId = null
    const mems = $('.mems'); if (mems && !quiet) mems.inert = false
    if (!quiet) {
      form?.mood({})
      $(`.mems [data-mem="${CSS.escape(id)}"] .mem-btn`)?.focus({ preventScroll: true })
    }
    notify(); kick()
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && openId && !document.querySelector('[aria-modal="true"]')) { e.preventDefault(); shut(false) }
  }
  document.addEventListener('keydown', onKey)

  function stepBodies(dt: number, now: number) {
    if (!bodies.size) { openness = 0; return }
    const rm = reduce.matches
    // How far open: the one opened (or closing) memory's own progress.
    for (const b of bodies.values()) b.ov = approach(b.ov, b.ovT, dt / (b.ovT > b.ov ? (rm ? 0.35 : 0.7) : (rm ? 0.3 : 0.5)))
    const shown = shownId ? bodies.get(shownId) : undefined
    openness = shown ? smooth(shown.ov) : 0
    if (shown && shown.ov === 0 && shown.ovT === 0 && shownId !== openId) { shownId = null; notify() }
    const F = orbFrame()
    const vis = open ? visibleIds() : new Set<string>()
    if (openId) vis.add(openId)          // an opened memory stays, however old
    const ranks = [...vis].filter((id) => id !== 'health')
    const o = openness, shrink = lerp(1, 0.55, o), Ro = openR()
    let removed = false
    time += dt
    for (const b of bodies.values()) {
      const visible = vis.has(b.id)
      b.leaving = !visible
      const ri = ranks.indexOf(b.id)
      if (b.id === 'health') b.rank = 0
      else if (ri >= 0) b.rank = ri
      if (!visible) b.actT = 0
      else if (now >= b.releaseAt) {
        if (!b.released) {
          b.released = true
          if (rm) { b.x = b.hx; b.y = b.hy; b.r = b.rT || b.r }
          if (b.thinking) { form?.energy(0); form?.pulse(0.55) } else form?.pulse(0.3)
        }
        b.actT = 1
      }
      const waitAbsorb = b.actT === 0 && b.absorbAt && now < b.absorbAt
      if (!waitAbsorb) b.act = approach(b.act, b.actT, dt / (b.actT > b.act ? (rm ? 0.6 : 1.4) : (rm ? 0.4 : 0.8)))
      if (b.act <= 0 && b.actT === 0 && (!visible || !open)) {
        bodies.delete(b.id); slots[b.slot] = null; removed = true
        if (shownId === b.id) shownId = null
        continue
      }
      // Landed after being opened: hover or keyboard focus may peek it again now.
      if (b.returning && b.ov === 0 && Math.hypot(b.x - b.hx, b.y - b.hy) < 8) {
        b.returning = false
        const btn = b.node?.querySelector('.mem-btn')
        if (btn && (btn.matches(':hover') || btn.matches(':focus-visible'))) { b.peekT = 1; b.pinned = true; b.vx = b.vy = 0 }
      }
      b.peek = approach(b.peek, b.peekT, dt / 0.25)
      b.ripple *= Math.exp(-dt * 2.2)
      b.rT = lerp(restR(b) * shrink * (1 + 0.18 * b.peek), Ro, b.ov)
      b.ax = 0; b.ay = 0
    }
    if (removed) reorder()

    // The ring: every memory centred on it, the same gap between neighbours' edges. An opened
    // memory keeps its place on it, so closing it sends it back where it came from.
    const A = arc(F)
    const ring = [...bodies.values()].filter((b) => !b.leaving)
    for (const b of ring) b.u = A.frac(b.th)
    ring.sort((a, c) => a.u - c.u)
    const ts = placeRing(A, ring.map((b) => restR(b) * shrink), ring.map((b) => b.th))
    ring.forEach((b, i) => {
      if (b.pinned || ts[i] == null) return
      b.th += turnTo(b.th, ts[i]) * (1 - Math.exp(-dt * (rm ? 6 : 1.8)))
    })
    const m = (b: Body) => b.r + 18
    for (const b of bodies.values()) {
      if (b.leaving) continue
      const [rx, ry] = ringPoint(F, b.th)
      // An opened memory's home is the middle; on the way back it's its place on the ring.
      const hx = lerp(clamp(rx, m(b), Math.max(m(b), view.w - m(b))), F.cx, b.ov)
      const hy = lerp(clamp(ry, m(b), Math.max(m(b), view.h - m(b))), F.cy, b.ov)
      const wob = rm || b.pinned || b.ov > 0 ? 0 : 1
      b.hx = hx + wob * 2.5 * Math.sin(0.7 * time + b.ph)
      b.hy = hy + wob * 2.5 * Math.cos(0.53 * time + 1.7 * b.ph)
    }

    const list = [...bodies.values()]
    const K_HOME = 9, DAMP = 5.4, K_REP = 70, GAP = 16, VMAX = 900
    const push = (b: Body, nx: number, ny: number, over: number) => { b.ax += K_REP * over * nx; b.ay += K_REP * over * ny }
    const heavy = (b: Body) => b.pinned || b.isOpen || b.ov > 0.02
    for (const b of list) {
      if (!b.released) continue
      const k = b.ovT > 0 || b.ov > 0 ? 4 : 1       // opening and closing travel quicker
      b.ax += K_HOME * k * (b.hx - b.x) - DAMP * Math.sqrt(k) * b.vx
      b.ay += K_HOME * k * (b.hy - b.y) - DAMP * Math.sqrt(k) * b.vy
    }
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const a = list[i], c = list[j]
      if (a.act < 0.05 || c.act < 0.05) continue
      const dx = c.x - a.x, dy = c.y - a.y, dd = Math.hypot(dx, dy) || 1
      const over = a.r + c.r + GAP - dd
      if (over <= 0) continue
      const wa = heavy(a) ? 0 : heavy(c) ? 2 : 1
      push(a, -dx / dd, -dy / dd, over * wa); push(c, dx / dd, dy / dd, over * (2 - wa))
    }
    const core = lerp(0.74 * F.r, Ro, o)
    for (const b of list) {
      if (!b.released) { b.r = lerp(b.r, b.rT * 0.35, 1 - Math.exp(-8 * dt)); continue }
      if (!b.isOpen && b.ov < 0.02) {
        const dx = b.x - F.cx, dy = b.y - F.cy, dd = Math.hypot(dx, dy) || 1
        push(b, dx / dd, dy / dd, Math.max(0, core + b.r + 22 - dd) * (b.act > 0.6 ? 1 : 0.3))
        if (hudRect) {
          const k = hudRect
          const ex = b.x - Math.max(k.x, Math.min(b.x, k.x + k.w)), ey = b.y - Math.max(k.y, Math.min(b.y, k.y + k.h))
          const e = Math.hypot(ex, ey) || 1
          push(b, ex / e, ey / e, Math.max(0, b.r + 18 - e))
        }
        b.ax += K_REP * (Math.max(0, 16 + b.r - b.x) - Math.max(0, b.x + b.r + 16 - view.w))
        b.ay += K_REP * (Math.max(0, 16 + b.r - b.y) - Math.max(0, b.y + b.r + 16 - view.h))
      }
      b.r = lerp(b.r, b.rT, 1 - Math.exp(-(b.ov > 0 || b.ovT > 0 ? 9 : 6) * dt))
      if (b.pinned) { b.vx = b.vy = 0; continue }
      b.vx += b.ax * dt; b.vy += b.ay * dt
      const v = Math.hypot(b.vx, b.vy)
      if (v > VMAX) { b.vx *= VMAX / v; b.vy *= VMAX / v }
      b.x += b.vx * dt; b.y += b.vy * dt
    }
  }

  function writeBodies() {
    const sats: (Satellite | null)[] = new Array(12).fill(null)
    const box = 280, o = openness
    for (const b of bodies.values()) {
      if (!b.node || !b.node.isConnected) b.node = $(`.mems [data-mem="${CSS.escape(b.id)}"]`)
      const n = b.node
      const base = (b.id === 'health' ? 0.85 : clamp(1 - 0.07 * b.rank, 0.5, 1)) * (1 + 0.35 * b.peek)
      // The opened one brightens and gives its words to the open view; the rest dim and hush.
      const bright = b.isOpen || b.ov > 0 ? lerp(base, 1.5, b.ov) : base * lerp(1, 0.35, o)
      b.text = smooth(seg(b.act, 0.72, 1)) * lerp(clamp(1 - 0.04 * b.rank, 0.72, 1), 1, b.peek)
        * (b.ov > 0 ? 1 - smooth(seg(b.ov, 0, 0.3)) : 1 - o)
      if (n) {
        n.style.transform = `translate3d(${(b.x - box / 2).toFixed(2)}px, ${(b.y - box / 2).toFixed(2)}px, 0)`
        n.style.setProperty('--r', `${b.r.toFixed(1)}px`)
        n.style.opacity = b.text.toFixed(3)
        n.style.visibility = b.act > 0.02 ? 'visible' : 'hidden'
        if ((b.peekT > 0) !== n.hasAttribute('data-peek')) n.toggleAttribute('data-peek', b.peekT > 0)
        if (b.isOpen !== n.hasAttribute('data-open')) n.toggleAttribute('data-open', b.isOpen)
      }
      sats[b.slot] = { x: b.x, y: b.y, r: b.r, act: b.act, bright, peek: b.peek, ripple: b.ripple, seed: b.seed }
    }
    form?.satellites(sats)
    // The open view sits on the opened memory's sphere and fades in once it has arrived.
    const openEl = shownId ? $('.mem-open') : null, ob = shownId ? bodies.get(shownId) : undefined
    if (openEl && ob) {
      const Ro = openR()
      openEl.style.transform = `translate(${(ob.x - Ro).toFixed(2)}px, ${(ob.y - Ro).toFixed(2)}px)`
      openEl.style.setProperty('--ro', `${Ro.toFixed(1)}px`)
      // Only once the sphere has arrived, so the words don't slide in with it.
      const near = 1 - clamp(Math.hypot(ob.x - ob.hx, ob.y - ob.hy) / 70, 0, 1)
      openEl.style.opacity = (smooth(seg(ob.ov, ob.ovT > 0 ? 0.55 : 0.7, 1)) * (ob.ovT > 0 ? smooth(near) : 1)).toFixed(3)
    }
  }

  // The bot's own face, faint, filling most of the form, and its name across it: only on the
  // final screen, arriving as the form settles in the middle and leaving first. An opened
  // memory covers it, so it steps back then too.
  let faceKey = ''
  function writeFace() {
    const face = $('.orb-face'), name = $('.orb-name')
    if (!face && !name) return
    const F = orbFrame()
    const d = 2 * 0.74 * F.r * 0.9
    const op = (reduce.matches ? smooth(seg(p, 0.5, 0.9)) : smooth(seg(p, 0.55, 0.95))) * (1 - openness)
    if (face) {
      face.style.transform = `translate(${(F.cx - d / 2).toFixed(2)}px, ${(F.cy - d / 2).toFixed(2)}px) scale(${(d / 400).toFixed(4)})`
      face.style.opacity = op.toFixed(3)
    }
    if (name) {
      name.style.transform = `translate(${F.cx.toFixed(2)}px, ${F.cy.toFixed(2)}px) translate(-50%, -50%)`
      name.style.opacity = op.toFixed(3)
      const w = `${Math.round(d * 0.8)}px`
      if (w !== faceKey) { faceKey = w; name.style.maxWidth = w }
    }
  }

  // ── The clock ──
  // One clock, p, runs from 0 (the stats screen) to 1 (the final screen) and back, and every
  // piece of the change reads its own stretch of it, so turning round halfway is just the clock
  // running the other way.
  let waiters: ((p: number) => void)[] = []
  function step(dt: number, now: number) {
    if (dead || now - lastStep < 4) return
    lastStep = now
    const rm = reduce.matches
    if (p !== target) {
      const dur = rm ? 0.45 : target > p ? 1.7 : 1.3
      p = approach(p, target, dt / dur)
      if (p === target && waiters.length) { const w = waiters; waiters = []; w.forEach((fn) => fn(p)) }
    }
    if (target > 0 && p >= 0.6) openMemories(false)
    applyTransition()
    stepBodies(Math.min(dt, 1 / 30), now)
    writeBodies()
    writeFace()
  }
  // The form's frame steps this while it's drawing; otherwise (no WebGL, or its layer withheld)
  // a frame loop of its own does, for as long as there's something to move.
  function loop(now: number) {
    raf = 0
    if (dead) return
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60
    last = now
    if (form && form.running()) { last = 0; return }
    step(dt, now)
    if (p !== target || bodies.size > 0) raf = requestAnimationFrame(loop)
    else last = 0
  }
  function kick() { if (!raf && !dead) raf = requestAnimationFrame(loop) }

  measure()
  return {
    attach(f: Form | null) {
      detachHook?.()
      detachHook = null
      form = f
      if (f) {
        f.framing((v) => framingFor(v))
        detachHook = f.onFrame((dt, now) => step(dt, now))
      }
      measure()
      document.fonts?.ready.then(() => { if (!dead) { measure(); kick() } })
      if (startOpen) { setInert(true); applied = -1; applyTransition(); openMemories(true) }
      kick()
    },
    destroy() {
      dead = true
      if (raf) cancelAnimationFrame(raf)
      detachHook?.()
      unsubscribe()
      activity.stop()
      window.removeEventListener('resize', onResize)
      document.removeEventListener('focusout', onFocusOut)
      document.removeEventListener('keydown', onKey)
    },
    setTarget(t: number) {
      if (t === target) return
      let focused: Element | null = document.activeElement
      if ((!focused || focused === document.body) && lastBlur && performance.now() - lastBlur.at < 1500) focused = lastBlur.el
      measure()
      measurePairs()
      target = t
      setInert(t === 1)
      applied = -1
      applyTransition()
      moveFocus(t === 1, focused)
      if (t === 1) { form?.requalify(); if (reduce.matches || !slot) form?.regather(0.35) }
      else closeMemories()
      if (t === 0 && (reduce.matches || !slot)) form?.regather(0.35)
      kick()
    },
    /** What the memories list renders: the ones with a body, newest first. */
    list: () => order.map((id) => itemFor(id)).filter((m): m is ActivityItem => !!m),
    subscribe(fn: () => void) { subs.add(fn); return () => { subs.delete(fn) } },
    peek(id: string, on: boolean) {
      const b = bodies.get(id)
      if (!b || openId || b.returning || b.ovT > 0) return
      b.peekT = on ? 1 : 0; b.pinned = !!on
      if (on) b.vx = b.vy = 0
    },
    open(id: string) { openOne(id) },
    close() { shut(false) },
    /** The memory the open view shows (still set while it closes), and whether it's open. */
    shown: () => (shownId ? { item: itemFor(shownId), open: shownId === openId } : null),
    remeasure() { measure(); measurePairs(); kick() },
    /** Runs fn once the change in progress lands (at once if nothing's moving). */
    whenSettled(fn: (p: number) => void) {
      if (p === target) { fn(p); return () => {} }
      waiters.push(fn)
      return () => { waiters = waiters.filter((w) => w !== fn) }
    },
    get target() { return target },
  }
}
