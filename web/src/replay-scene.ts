// ── The memory replay's scene ─────────────────────────────────────────────────────────
// Everything in the replay that isn't React, ported from the prototype
// (design/memory-replay/src/replay.js, which is the behavioral spec). One clock runs it all:
// the form's frame hook steps the camera (where the form sits), the memories (spheres of the
// form's dust with their words laid over them), the dust thrown across the window, and
// whatever the current beat is animating. Every beat is a state the page eases toward, so
// going back is the same motion run the other way.
//
// replay.tsx renders the overlay's skeleton and the three sheets under the form (start,
// remembering, Play); this finds its pieces by class inside it, the way brain.ts does on the
// final server screen. Everything is in layout px, the page's own units under the
// interface-size zoom.
//
// What a member, Discord or a model wrote is set as text, never as markup. Where a line has
// to be markup (bold, mono), what goes into it is escaped, and names sit in <bdi>.

import { animatedAvatar } from './activity'
import { apiUrl } from './api'
import { NSAT, SHAPE, layoutRect, zoomOf, type Form, type Satellite } from './form'
import {
  type Gap, type Msg, type Pick, type Progress, type Report,
  bdi, cap, clip, clock, mediaWord, dateRange, dayName, dayShort, discordLink, esc, fmt, isGap, local, longDate, monthYear, ordinal,
  partOfDay, plural, shortDate, span, withNames, word,
} from './replay-data'

export type Stage = 'start' | 'remember' | 'play' | 'replay'
export type IconKey = 'play' | 'pause' | 'restart' | 'back' | 'close'

export type SceneOpts = {
  root: HTMLElement
  form: Form | null
  /** A fresh copy of one of the console's icons (icons.tsx), for controls built here. */
  icon: (name: IconKey, size?: number) => Element
  /** Ask the host to change stage (Escape, Done, Watch again from the start). */
  go: (stage: Stage) => void
  /** Save the answer to the last question; resolves false if it couldn't be saved. */
  answer: (text: string) => Promise<boolean>
}

type Spec = {
  kind: 'member' | 'left' | 'faces' | 'quote' | 'term' | 'count' | 'name' | 'answer' | 'blur'
  who?: string | null; text?: string; term?: string; long?: boolean; big?: string; foot?: string; more?: string
  faces?: (string | null)[]; label?: string
  open?: () => Node[]
  onClick?: () => void
}
type Want = { id: string; spec: Spec; x: number; y: number; r: number; bright?: number; delay?: number }
type Cam = { cx: number; cy: number; r: number; x0: number; x1: number; fy: number; yb?: number }
type FormSet = { shape: number; v?: number; energy?: number; mood?: { dim?: number; loose?: number; tremor?: number }; density?: number; feed?: number; spark?: number; pulse?: number; regather?: number }
type Narr = { title?: string | Node; titleKey?: string; body?: string | Node; bodyKey?: string; extra?: Node; extraKey?: string; foot?: string | Node; footKey?: string }
type Beat = {
  id: string; ch: number; auto: number
  cam: () => Cam
  form: FormSet
  narr: () => Narr
  spheres?: () => Want[]
  hint?: string | (() => string)
  enter?: () => void
  leave?: () => void
  frame?: (dt: number) => void
}
type Quote = HTMLElement & { play?: (onLand?: (k: number) => void) => () => void }

// ── Helpers ──
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x))
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const smooth = (t: number) => t * t * (3 - 2 * t)
const sstep = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t) }
const hashStr = (s: string) => { let x = 2166136261; for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619) } return x >>> 0 }

type Props = Record<string, unknown> & { class?: string; html?: string; style?: Partial<CSSStyleDeclaration> }
function h(tag: string, props?: Props | null, ...kids: unknown[]): HTMLElement {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue
    if (k === 'class') el.className = String(v)
    else if (k === 'html') el.innerHTML = String(v)
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v as EventListener)
    else if (k === 'style') Object.assign(el.style, v)
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  for (const c of (kids as unknown[]).flat(Infinity)) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c))
  return el
}

// The dust's two inks, as form.ts draws them: --text-2 and --accent.
const INK = '170, 170, 180', ACCENT = '91, 156, 246'

export function createScene(o: SceneOpts) {
  const { root, form } = o
  const $ = <T extends HTMLElement = HTMLElement>(s: string) => root.querySelector<T>(s)!
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
  const coarse = window.matchMedia('(pointer: coarse)')
  let report: Report | null = null
  let tz: number | null = null
  let W = innerWidth / zoomOf(), H = innerHeight / zoomOf()
  const wide = () => W > 900
  let dead = false
  // Every listener on the overlay's own elements and the window goes when the scene does
  // (StrictMode mounts it twice, and the React-owned nodes outlive the first).
  const life = new AbortController()
  const { signal } = life

  // ── Names and faces ──
  // A person the report can name, Olisar ("bot"), or Someone (null) with a blank face.
  const blank = (() => {
    const c = document.createElement('canvas'); c.width = c.height = 64
    const g = c.getContext('2d')!
    const css = getComputedStyle(root)
    g.fillStyle = css.getPropertyValue('--border').trim() || 'gray'; g.fillRect(0, 0, 64, 64)
    g.fillStyle = css.getPropertyValue('--border-strong').trim() || 'gray'
    g.beginPath(); g.arc(32, 25, 10, 0, Math.PI * 2); g.fill()
    g.beginPath(); g.ellipse(32, 56, 19, 14, 0, Math.PI, 0); g.fill()
    return c.toDataURL('image/png')
  })()
  const BOT_FACE = '/logo.png'
  const nm = (who: string | null | undefined) =>
    who === 'bot' ? (report?.bot.name || 'Olisar') : who && report?.people[who] ? report.people[who].name : 'Someone'
  const av = (who: string | null | undefined) =>
    who === 'bot' ? (report?.bot.avatar || BOT_FACE) : who && report?.people[who]?.avatar ? report.people[who].avatar! : blank
  const whoHtml = (who: string | null | undefined) => bdi(nm(who))
  const img = (src: string, size: number, cls = 'rp-av') => {
    const el = h('img', { class: cls, src, alt: '', width: size, height: size, style: { width: size + 'px', height: size + 'px' } }) as HTMLImageElement
    el.addEventListener('error', () => { delete el.dataset.moving; if (el.src !== blank) el.src = blank }, { once: true })
    return el
  }
  // A face in a memory: an animated avatar holds its first frame there until play() runs it,
  // while the memory is hovered.
  const face = (src: string, size: number, cls = 'rp-av') => {
    const anim = animatedAvatar(src)
    const el = img(anim ? anim.still : src, size, cls)
    if (anim) { el.dataset.still = anim.still; el.dataset.moving = anim.moving }
    return el
  }
  const play = (root: Element, on: boolean) => {
    for (const el of root.querySelectorAll<HTMLImageElement>('img[data-moving]')) el.src = (on ? el.dataset.moving : el.dataset.still)!
  }
  const SRV = () => bdi(report?.guild.name || '')
  const names = () => Object.values(report?.people || {}).map((p) => p.name).concat(report?.bot.name || [])

  // ── Layers ──
  const fxCanvas = $<HTMLCanvasElement>('.rp-fx')
  const orbCss = $('.rp-orb-css')
  const sparkEl = $('.rp-spark')
  const memsEl = $('.rp-mems')
  const narrEl = $('.rp-narr')
  const centerEl = $('.rp-center')
  const faceEl = $('.rp-face')
  const hintEl = $('.rp-hint')
  const timelineEl = $('.rp-timeline')
  const autoBtn = $('.rp-auto')

  // ── The camera: where the form sits ──
  const remTop = () => H * 0.38 + Math.min(H * 0.23, W * 0.22) * 0.74 + Math.max(56, H * 0.09)
  const CAM = {
    remember: (): Cam => ({ cx: W * 0.5, cy: H * 0.38, r: Math.min(H * 0.23, W * 0.22), x0: 0, x1: 0, fy: H * 0.1, yb: remTop() - 18 }),
    stage: (k = 1): Cam => wide()
      ? { cx: W * 0.64, cy: H * 0.47, r: H * 0.27 * k, x0: 0, x1: 0, fy: H * 0.06 }
      : { cx: W * 0.5, cy: H * 0.28, r: H * 0.17 * k, x0: 0, x1: 0, fy: H * 0.04 },
    corner: (k = 1): Cam => wide()
      ? { cx: W * 0.86, cy: H * 0.24, r: H * 0.15 * k, x0: 0, x1: 0, fy: H * 0.04 }
      : { cx: W * 0.82, cy: H * 0.12, r: H * 0.08 * k, x0: 0, x1: 0, fy: 0 },
    end: (): Cam => ({ cx: W * 0.5, cy: H * 0.48, r: Math.min(H * 0.21, W * 0.2), x0: 0, x1: 0, fy: H * 0.04 }),
  }
  let camFn: () => Cam = CAM.remember
  const cam: Cam = { ...CAM.remember() }
  const camV = { cx: 0, cy: 0, r: 0 }
  form?.framing(() => cam)
  let spark = 0, sparkT = 0
  function stepSpark(dt: number) {
    spark += (sparkT - spark) * (1 - Math.exp(-dt * 2.2))
    const d = 26 + 70 * spark
    sparkEl.style.opacity = (spark * 0.9).toFixed(3)
    sparkEl.style.transform = `translate(${cam.cx - d / 2}px, ${cam.cy - d / 2}px)`
    sparkEl.style.width = sparkEl.style.height = d + 'px'
  }
  function stepCam(dt: number) {
    let t = camFn()
    if (openId) { const sp = openSpot(); t = { ...t, cx: sp.x, cy: sp.y, r: t.r * 0.4 } }
    const w0 = reduce.matches ? 1.6 : 2.6
    for (const k of ['cx', 'cy', 'r'] as const) {
      camV[k] += (-2 * w0 * camV[k] - w0 * w0 * (cam[k] - t[k])) * dt
      cam[k] += camV[k] * dt
    }
    const e = 1 - Math.exp(-dt * 3)
    cam.x0 += (t.x0 - cam.x0) * e; cam.x1 += (t.x1 - cam.x1) * e; cam.fy += (t.fy - cam.fy) * e
    cam.yb = lerp(cam.yb || H, t.yb || H, e)
    if (!t.yb && Math.abs(cam.yb - H) < 2) cam.yb = 0
    const d = cam.r * 1.48 * formScale
    orbCss.style.transform = `translate(${cam.cx - d / 2}px, ${cam.cy - d / 2}px)`
    orbCss.style.width = orbCss.style.height = d + 'px'
  }

  // The form's settings, by beat. formScale is the whole body's size, for anything placed
  // on it (the question at the end, the ring without WebGL2).
  let formScale = 1
  // At a tenth of its size the same surface warp tears the body apart, so it calms down.
  const calm = (shape: number, v: number | undefined, e: number) => (shape === SHAPE.whole && (v || 1) < 1 ? Math.min(e, (v || 1) * 1.2 - 1) : e)
  function applyForm(f: FormSet) {
    sparkT = f.spark || 0
    formScale = f.shape === SHAPE.whole ? (f.v || 1) : 1
    if (!form) return
    form.set(f.shape, f.v)
    form.energy(calm(f.shape, f.v, f.energy ?? 0))
    form.mood(f.mood || {})
    form.density(f.density ?? 1)
    form.feed(f.feed ?? 0.0022)
    if (f.pulse) form.pulse(f.pulse)
    if (f.regather) form.regather(f.regather)
  }

  // ── Memories ──
  // Each is a sphere of the form's dust, drawn by the engine, with its words in a box laid
  // over it. A memory buds out of the form when it's wanted and its dust flows back when it
  // isn't; the slot it draws in stays its own meanwhile.
  type Sphere = {
    id: string; spec: Spec; key: string; node: HTMLElement; btn: HTMLButtonElement; mask: HTMLElement; inner: HTMLElement; cap: HTMLElement
    slot: number; seed: number; x: number; y: number; r: number; vx: number; vy: number; vr: number
    tx: number; ty: number; tr: number; base: number; act: number; tact: number; bright: number; tbright: number
    peek: number; peekT: number; ripple: number; at: number; placed: boolean; box: number
  }
  const sats: (Satellite | null)[] = new Array(NSAT).fill(null)
  form?.satellites(sats)
  const spheres = new Map<string, Sphere>()
  let openId: string | null = null

  function sphereWords(s: Spec): (Node | null)[] {
    const foot = h('div', { class: 'rp-mem-foot' }, s.foot || '')
    switch (s.kind) {
      case 'member': return [h('div', { class: 'rp-mem-who' }, face(av(s.who), 34), h('bdi', {}, nm(s.who))), foot]
      case 'left': return [h('div', { class: 'rp-mem-who' }, img(blank, 34), h('span', {}, 'Someone')), foot]
      case 'faces': return [h('div', { class: 'rp-mem-faces' }, (s.faces || []).map((n) => face(av(n), 22))), h('div', { class: 'rp-mem-text' }, s.text || ''), foot]
      case 'quote': return [h('div', { class: 'rp-mem-who' }, face(av(s.who), 18), h('bdi', {}, nm(s.who))), h('div', { class: 'rp-mem-text', dir: 'auto' }, s.text || ''), foot]
      case 'term': return [h('div', { class: 'rp-mem-term' + (s.long ? ' long' : ''), dir: 'auto' }, s.term || ''), s.text ? h('div', { class: 'rp-mem-text' }, s.text) : null, foot]
      case 'count': return [h('div', { class: 'rp-mem-faces' }, (s.faces || []).map((n) => face(av(n), 20))), h('div', { class: 'rp-mem-big num' }, s.big || ''), foot]
      case 'name': return [h('div', { class: 'rp-mem-was', dir: 'auto' }, s.text || ''), foot]
      case 'answer': return [h('div', { class: 'rp-mem-text', dir: 'auto' }, `“${s.text}”`), foot]
      case 'blur': return [h('div', { class: 'rp-mem-faces' }, (s.faces || []).map((src) => face(src || blank, 18))), h('div', { class: 'rp-mem-bar wide' }), h('div', { class: 'rp-mem-bar' }), foot]
    }
  }
  const live = (s: Spec) => !!(s.open || s.onClick)
  const specKey = (s: Spec) => [s.kind, s.who, s.text, s.term, s.long, s.big, s.foot, s.more, s.faces?.join(), !!s.open, !!s.onClick].join('|')

  function makeSphere(id: string, spec: Spec): Sphere {
    const inner = h('div', { class: 'rp-mem-in' }, sphereWords(spec).filter(Boolean))
    const mask = h('div', { class: 'rp-mem-mask' }, inner)
    const capEl = h('div', { class: 'rp-mem-cap', 'aria-hidden': 'true' }, spec.more || '')
    const btn = h('button', { class: 'rp-mem-btn', type: 'button', 'aria-label': spec.label || id, tabindex: live(spec) ? 0 : -1 }) as HTMLButtonElement
    const node = h('li', { class: `rp-mem rp-mem-${spec.kind}${live(spec) ? '' : ' inert'}` }, btn, mask, capEl)
    memsEl.append(node)
    const S: Sphere = {
      id, spec, key: specKey(spec), node, btn, mask, inner, cap: capEl, slot: -1, seed: (hashStr(id) % 1000) / 1000,
      x: 0, y: 0, r: 0, vx: 0, vy: 0, vr: 0, tx: 0, ty: 0, tr: 0, base: 0,
      act: 0, tact: 1, bright: 1, tbright: 1, peek: 0, peekT: 0, ripple: 0, at: 0, placed: false, box: 140,
    }
    const peek = (on: boolean) => {
      play(inner, on)
      const can = live(S.spec)
      if (can) S.peekT = on ? 1 : 0
      node.toggleAttribute('data-peek', on && can)
      if (on && can) S.ripple = Math.max(S.ripple, 0.6)
    }
    btn.addEventListener('pointerenter', () => peek(true))
    btn.addEventListener('pointerleave', () => peek(false))
    btn.addEventListener('focus', () => peek(true))
    btn.addEventListener('blur', () => peek(false))
    btn.addEventListener('click', (e) => {
      e.stopPropagation()
      if (S.spec.open) openSphere(id)
      else S.spec.onClick?.()
    })
    return S
  }
  // A memory asked for again with different words (the pick's "Open" becoming "Again") keeps
  // its sphere and changes only what's set in it.
  function respec(S: Sphere, spec: Spec) {
    const k = specKey(spec)
    S.spec = spec
    if (k === S.key) return
    S.key = k
    S.inner.replaceChildren(...(sphereWords(spec).filter(Boolean) as Node[]))
    play(S.inner, S.btn.matches(':hover, :focus'))
    S.cap.textContent = spec.more || ''
    S.node.className = `rp-mem rp-mem-${spec.kind}${live(spec) ? '' : ' inert'}`
    S.btn.tabIndex = live(spec) ? 0 : -1
    S.btn.setAttribute('aria-label', spec.label || S.id)
  }
  // Ask for exactly these memories, where they should sit now. Anything not asked for goes
  // back into the form.
  function setSpheres(list: Want[], { stagger = 0.16 } = {}) {
    const want = new Set<string>()
    let i = 0
    const now = performance.now() / 1000
    for (const m of list) {
      want.add(m.id)
      let S = spheres.get(m.id)
      if (!S) {
        S = makeSphere(m.id, m.spec)
        spheres.set(m.id, S)
        S.at = now + (m.delay ?? i * stagger)
        i++
      } else {
        if (S.tact === 0) S.at = now
        respec(S, m.spec)
      }
      S.tx = m.x; S.ty = m.y; S.tr = m.r; S.base = m.r; S.tbright = m.bright ?? 1; S.tact = 1
      if (!S.placed) { S.x = m.x; S.y = m.y; S.r = m.r; S.placed = true }
      const B = Math.ceil(Math.max(140, m.r * 1.15 + 14))
      S.box = B
      Object.assign(S.node.style, { width: B * 2 + 'px', height: B * 2 + 'px' })
      S.node.style.setProperty('--d', m.r * 2 + 'px')
    }
    for (const S of spheres.values()) if (!want.has(S.id)) { S.tact = 0; S.peekT = 0; S.node.removeAttribute('data-peek') }
  }

  let openFast = 0
  function stepSpheres(dt: number) {
    const now = performance.now() / 1000
    // Opening or closing a memory moves at about twice the speed of everything else.
    openFast = openId ? 1 : Math.max(0, openFast - dt)
    const w0 = (reduce.matches ? 2.4 : 4.2) * (openFast > 0 ? 1.9 : 1)
    for (const S of [...spheres.values()]) {
      if (S.slot < 0 && S.tact > 0) { const f = sats.indexOf(null); if (f >= 0) { S.slot = f; sats[f] = { x: 0, y: 0, r: 0, act: 0 } } }
      const on = S.tact > 0 && now >= S.at
      const rate = reduce.matches ? 0.6 : 0.95
      S.act = on ? Math.min(1, S.act + dt * rate) : S.tact === 0 ? Math.max(0, S.act - dt * rate * 1.25) : S.act
      if (S.tact === 0 && S.act <= 0) {
        S.node.remove(); if (S.slot >= 0) sats[S.slot] = null; spheres.delete(S.id)
        continue
      }
      const open = openId === S.id
      const others = !!openId && !open
      let gx = open ? openSpot().x : S.tx, gy = open ? openSpot().y : S.ty
      if (others) {
        const sp = openSpot(), dx = S.tx - sp.x, dy = S.ty - sp.y, d = Math.hypot(dx, dy) || 1
        const need = sp.r + S.tr * 0.72 + 28
        if (d < need) { gx = sp.x + (dx / d) * need; gy = sp.y + (dy / d) * need }
      }
      const gr = open ? openSpot().r : others ? S.tr * 0.72 : S.tr
      S.vx += (-2 * w0 * S.vx - w0 * w0 * (S.x - gx)) * dt; S.x += S.vx * dt
      S.vy += (-2 * w0 * S.vy - w0 * w0 * (S.y - gy)) * dt; S.y += S.vy * dt
      S.vr += (-2 * w0 * S.vr - w0 * w0 * (S.r - gr)) * dt; S.r += S.vr * dt
      S.peek += (S.peekT - S.peek) * (1 - Math.exp(-dt * 8))
      S.ripple *= Math.exp(-dt * 2.2)
      const bt = others ? 0.35 : S.tbright
      S.bright += (bt - S.bright) * (1 - Math.exp(-dt * 4))
      const r = Math.max(1, S.r * (1 + 0.07 * S.peek))
      if (S.slot >= 0) {
        const sat = sats[S.slot]!
        sat.x = S.x; sat.y = S.y; sat.r = r; sat.act = S.act; sat.bright = S.bright * (1 + 0.35 * S.peek); sat.peek = S.peek; sat.ripple = S.ripple; sat.seed = S.seed
      }
      S.node.style.transform = `translate(${S.x - S.box}px, ${S.y - S.box}px)`
      S.node.style.setProperty('--r', r + 'px')
      S.node.style.setProperty('--sv', clamp(r / Math.max(1, S.base), 0.6, 1.15).toFixed(3))
      S.node.style.visibility = S.act > 0.05 ? 'visible' : 'hidden'
      const words = open ? 0 : smooth(clamp((S.act - 0.55) / 0.45, 0, 1)) * (others ? 0.4 : 1)
      S.mask.style.opacity = words.toFixed(3)
    }
  }

  // ── Opening a memory ──
  // Its sphere comes to the middle, large, with the form drawn back behind it and the rest
  // stepped out to the edges, like the final server screen.
  const openEl = $('.rp-open')
  const openIn = $('.rp-open-in')
  const openBack = $<HTMLButtonElement>('.rp-back')
  let openReturn: HTMLElement | null = null, openShown = 0, openHover = false
  openIn.addEventListener('pointerenter', () => { openHover = true; play(openIn, true) }, { signal })
  openIn.addEventListener('pointerleave', () => { openHover = false; play(openIn, false) }, { signal })
  const openSpot = () => wide()
    ? { x: W * 0.5, y: H * 0.47, r: Math.min(H * 0.37, W * 0.27, 330) }
    : { x: W * 0.5, y: H * 0.45, r: Math.min(W * 0.46, H * 0.36) }
  function openSphere(id: string) {
    const S = spheres.get(id)
    if (!S || !S.spec.open) return
    openId = id
    openReturn = S.btn
    S.node.setAttribute('data-open', '')
    openIn.replaceChildren(...S.spec.open())
    play(openIn, openHover)
    openIn.scrollTop = 0
    openEl.style.visibility = 'visible'
    openShown = 0
    form?.mood({ dim: 0.08 })
    narrEl.classList.add('dim')
    centerEl.classList.add('away')
    hint(null)
    form?.pulse(0.4)
  }
  function closeSphere(quiet = false) {
    if (!openId) return
    spheres.get(openId)?.node.removeAttribute('data-open')
    openId = null
    narrEl.classList.remove('dim')
    centerEl.classList.remove('away')
    const b = beat()
    if (b) applyForm(b.form)
    if (!quiet && openReturn && document.contains(openReturn)) openReturn.focus({ preventScroll: true })
    openReturn = null
  }
  openBack.addEventListener('click', (e) => { e.stopPropagation(); closeSphere() }, { signal })
  function stepOpen(dt: number) {
    const S = openId ? spheres.get(openId) : undefined
    if (S) {
      const sp = openSpot()
      const arrived = Math.hypot(S.x - sp.x, S.y - sp.y) < 18 && Math.abs(S.r - sp.r) < 22
      openShown = arrived ? Math.min(1, openShown + dt * 5) : openShown
      openEl.style.setProperty('--ro', S.r + 'px')
      openEl.style.transform = `translate(${S.x - S.r}px, ${S.y - S.r}px)`
      if (arrived && openShown > 0 && document.activeElement !== openBack && !openEl.contains(document.activeElement)) openBack.focus({ preventScroll: true })
    } else openShown = Math.max(0, openShown - dt * 5)
    openEl.style.opacity = smooth(openShown).toFixed(3)
    if (!S && openShown === 0) openEl.style.visibility = 'hidden'
  }

  // ── Dust thrown across the window ──
  // For what the form takes in (people arriving) or gives out (the pick spreading). Drawn on
  // a 2D layer over the form, in the form's ink. It never crosses the narration, the corner
  // or the chapters.
  const fx = fxCanvas.getContext('2d')!
  let fxK = 1
  type Grain = { x0: number; y0: number; x1: number; y1: number; mx: number; my: number; t: number; dur: number; size: number; acc: boolean; out?: boolean; alpha?: number; land?: (() => void) | null }
  type Mote = { x: number; y: number; size: number; a: number; w: number; ph: number; acc: boolean; born: number }
  const grains: Grain[] = []
  const motes: Mote[] = []
  function sizeFx() {
    fxK = zoomOf() * Math.min(devicePixelRatio || 1, 2)
    fxCanvas.width = Math.round(W * fxK); fxCanvas.height = Math.round(H * fxK)
  }
  function grain(g: { x0: number; y0: number; x1: number; y1: number; bend?: number; delay?: number; dur?: number; size?: number; acc?: boolean; out?: boolean; alpha?: number; land?: (() => void) | null }) {
    const slow = reduce.matches ? 2.6 : 1
    const dx = g.x1 - g.x0, dy = g.y1 - g.y0
    const bend = g.bend ?? (Math.random() - 0.5) * 0.5
    grains.push({
      ...g, t: -(g.delay || 0) * slow, dur: (g.dur || 1.4) * slow,
      mx: (g.x0 + g.x1) / 2 - dy * bend, my: (g.y0 + g.y1) / 2 + dx * bend,
      size: g.size || 0.7 + Math.random() * 0.9, acc: g.acc ?? Math.random() < 0.14,
    })
  }
  function stepFx(dt: number) {
    fx.setTransform(fxK, 0, 0, fxK, 0, 0)
    fx.clearRect(0, 0, W, H)
    if (!grains.length && !motes.length) return
    fx.globalCompositeOperation = 'lighter'
    const nr = stage === 'replay' && wide() ? Math.max(56, W * 0.07) + Math.min(440, W * 0.34) : -1e9
    const nt = stage === 'replay' && !wide() ? layoutRect(narrEl).y : 1e9
    const keep = (x: number, y: number) => sstep(58, 112, y) * (1 - sstep(H - 118, H - 66, y)) * sstep(nr + 10, nr + 90, x) * (1 - sstep(nt - 70, nt - 12, y))
    const now = performance.now() / 1000
    for (const m of motes) {
      const a = m.a * (0.55 + 0.45 * Math.sin(now * m.w + m.ph)) * Math.min(1, (now - m.born) * 1.5)
      fx.fillStyle = `rgba(${m.acc ? ACCENT : INK}, ${a})`
      fx.beginPath(); fx.arc(m.x, m.y, m.size, 0, Math.PI * 2); fx.fill()
    }
    for (let i = grains.length - 1; i >= 0; i--) {
      const g = grains[i]
      g.t += dt
      if (g.t < 0) continue
      const u = g.t / g.dur
      if (u >= 1) { grains.splice(i, 1); g.land?.(); continue }
      const ease = g.out ? 1 - Math.pow(1 - u, 2.2) : u * u * (1.6 - 0.6 * u)
      const a = Math.min(1, u * 5) * (1 - Math.pow(u, 5)) * (g.alpha ?? 0.85)
      for (let k = 0; k < 4; k++) {
        const e = clamp(ease - k * 0.022, 0, 1), q = 1 - e
        const x = q * q * g.x0 + 2 * q * e * g.mx + e * e * g.x1
        const y = q * q * g.y0 + 2 * q * e * g.my + e * e * g.y1
        const aa = a * [1, 0.45, 0.22, 0.1][k] * keep(x, y)
        if (aa < 0.004) continue
        fx.fillStyle = `rgba(${g.acc ? ACCENT : INK}, ${aa})`
        fx.beginPath(); fx.arc(x, y, g.size * (1 - k * 0.18), 0, Math.PI * 2); fx.fill()
      }
    }
  }

  // ── Narration ──
  // Four slots beside the form. A slot whose words don't change between beats stays put; one
  // that does fades out as the new words come in, so only what changed plays. Each slot eases
  // its height, and a quote that types in has its full height reserved from the start.
  const SLOTS = ['title', 'body', 'extra', 'foot'] as const
  const slotEl = Object.fromEntries(SLOTS.map((s) => [s, $(`.rp-n-${s}`)])) as Record<(typeof SLOTS)[number], HTMLElement>
  const slotKey: Record<string, unknown> = {}
  const fitSlot = (el: HTMLElement) => {
    const node = [...el.children].reverse().find((c) => !c.classList.contains('leave')) as HTMLElement | undefined
    el.style.height = (node ? node.offsetHeight : 0) + 'px'
  }
  const slotSize = new ResizeObserver((es) => { for (const e of es) { const p = (e.target as HTMLElement).parentElement; if (p) fitSlot(p) } })
  function narrate(n: Narr = {}) {
    let i = 0
    for (const s of SLOTS) {
      const v = n[s]
      const key = v == null ? null : (n as Record<string, unknown>)[s + 'Key'] ?? (typeof v === 'string' ? v : Math.random())
      if (slotKey[s] === key) continue
      slotKey[s] = key
      const el = slotEl[s]
      if (!el.style.height) { el.style.height = el.offsetHeight + 'px'; void el.offsetHeight }
      for (const c of [...el.children] as HTMLElement[]) {
        if (c.classList.contains('leave')) continue
        c.classList.add('leave'); slotSize.unobserve(c); window.setTimeout(() => c.remove(), 260)
      }
      let node: HTMLElement | null = null
      if (v != null) {
        node = typeof v === 'string' ? h('div', { html: v }) : (v as HTMLElement)
        node.classList.add('enter')
        el.append(node)
        slotSize.observe(node)
      }
      fitSlot(el)
      if (!node) continue
      const d = 140 + i * 110
      i++
      const entering = node
      window.setTimeout(() => requestAnimationFrame(() => entering.classList.remove('enter')), d)
    }
  }

  // Messages as the replay quotes them: in Olisar's palette (it's Olisar speaking), typed in
  // one after another. A screen reader hears each one whole, once.
  function quote(msgs: Msg[], head?: string): Quote {
    const rows = msgs.map((m) => {
      const text = clip(m.text)
      const typed = h('span', { 'aria-hidden': 'true' })
      const p = text ? h('p', { dir: 'auto' }, h('span', { class: 'visually-hidden' }, text), h('span', { class: 'ghost', 'aria-hidden': 'true' }, text, h('span', { class: 'caret' })), typed) : null
      const row = h('div', { class: 'rp-qmsg' + (m.who === 'bot' ? ' bot' : '') },
        img(av(m.who), 28, 'rp-qav'),
        h('div', {}, h('b', {}, h('bdi', {}, nm(m.who)), h('time', {}, timeLabel(m))), p, pictures(m)))
      row.style.opacity = '0'
      return { row, typed, text }
    })
    const node = h('div', { class: 'rp-quote' }, head ? h('div', { class: 'rp-qhead' }, head) : null, rows.map((r) => r.row)) as Quote
    node.play = (onLand) => {
      let k = 0, j = 0, alive = true
      const caret = h('span', { class: 'caret' })
      const next = () => {
        if (!alive || k >= rows.length) { caret.remove(); return }
        const r = rows[k]
        r.row.style.opacity = '1'
        if (reduce.matches) { r.typed.textContent = r.text; k++; onLand?.(k); later(700, next); return }
        j = 0
        const type = () => {
          if (!alive) return
          j = Math.min(r.text.length, j + 1 + (Math.random() < 0.3 ? 1 : 0))
          r.typed.textContent = r.text.slice(0, j); r.typed.append(caret)
          if (j < r.text.length) later(26 + Math.random() * 30, type)
          else { k++; onLand?.(k); later(520, next) }
        }
        later(260, type)
      }
      later(500, next)
      return () => { alive = false }
    }
    return node
  }
  const timeLabel = (m: Msg) => (m.trigger === 'ask' ? `/ask · ${clock(m.at, tz)}` : clock(m.at, tz))

  // ── Timers that belong to the beat on screen ──
  let timers: number[] = []
  function later(ms: number, fn: () => void) { const t = window.setTimeout(fn, ms); timers.push(t); return t }
  function clearTimers() { timers.forEach(clearTimeout); timers = [] }

  // ── Hint ──
  let hintT = 0
  function hint(html: string | null, secs = 7) {
    clearTimeout(hintT)
    if (!html) { hintEl.classList.remove('on'); return }
    hintEl.innerHTML = html
    hintEl.classList.add('on')
    hintT = window.setTimeout(() => hintEl.classList.remove('on'), secs * 1000)
  }

  // ── What the replay shows ──
  // The GIFs, images and stickers in a message. A GIF plays as the short video Tenor and
  // Klipy make of it, or shows its still under reduced motion; an upload comes through the
  // API, which knows where Discord keeps it today.
  function pictures(m: Msg): HTMLElement | null {
    if (!m.media?.length || !report) return null
    const guild = report.guild.id
    const items = m.media.map((x, n) => {
      const size = { width: x.w || undefined, height: x.h || undefined }
      const label = x.kind === 'gif' ? 'GIF' : x.kind === 'sticker' ? `Sticker${x.name ? `: ${x.name}` : ''}` : x.kind === 'video' ? 'Video' : 'Image'
      const upload = x.i != null ? apiUrl(`/api/replay/media/${encodeURIComponent(m.id)}/${x.i}?g=${encodeURIComponent(guild)}`) : ''
      const src = x.kind === 'image' || x.kind === 'video' ? upload : x.src || ''
      const moving = (x.kind === 'gif' && /\.(mp4|webm)(\?|$)/i.test(src)) || x.kind === 'video'
      let el: HTMLElement
      if (moving && !(x.kind === 'gif' && reduce.matches && x.poster)) {
        el = h('video', { src, poster: x.poster || undefined, muted: true, loop: x.kind === 'gif', autoplay: x.kind === 'gif', playsinline: true, controls: x.kind === 'video', 'aria-label': label, ...size })
        ;(el as HTMLVideoElement).muted = true
      } else {
        el = h('img', { src: x.kind === 'gif' && reduce.matches ? x.poster || src : src, alt: label, loading: 'lazy', ...size })
      }
      el.className = `rp-pic rp-pic-${x.kind}`
      el.addEventListener('error', () => el.remove(), { once: true })
      el.dataset.n = String(n)
      return el
    })
    return h('div', { class: 'rp-pics' }, items)
  }
  const msgRows = (msgs: (Msg | Gap)[]) => h('div', { class: 'rp-mo-chat' }, msgs.map((m) => isGap(m)
    ? h('div', { class: 'rp-mo-gap' }, m.gap)
    : h('div', { class: 'rp-mo-msg' + (m.who === 'bot' ? ' bot' : '') }, face(av(m.who), 24),
        h('div', {}, h('b', {}, h('bdi', {}, nm(m.who)), h('time', {}, timeLabel(m))), m.text ? h('p', { dir: 'auto' }, m.text) : null, pictures(m)))))
  const link = (m: Msg | undefined) => m && report
    ? h('a', { class: 'rp-mo-link', href: discordLink(report.guild.id, m), target: '_blank', rel: 'noopener noreferrer', onclick: (e: Event) => e.stopPropagation() }, 'Open in Discord')
    : null
  const head = (t: string) => h('div', { class: 'rp-mo-head' }, t)
  const meta = (t: string) => h('div', { class: 'rp-mo-meta' }, t)

  // The opening chapter: the first five, as they arrived.
  const founderSpec = (f: Report['founders'][number]): Spec => {
    const day = shortDate(f.at, tz).replace(/, \d{4}$/, '')
    if (!f.who) return { kind: 'left', foot: day, label: 'Someone' }
    const said = f.msg
    return {
      kind: 'member', who: f.who, foot: day, more: said ? `“${clip(said.text).slice(0, 60)}”` : '', label: `${nm(f.who)}, ${day}`,
      open: () => [
        head(said ? `#${said.chn} · ${longDate(said.at, tz)}` : longDate(f.at, tz)),
        face(av(f.who), 56, 'rp-av rp-mo-av'),
        h('div', { class: 'rp-mo-name' }, h('bdi', {}, nm(f.who))),
        said ? msgRows([said]) : null,
        f.count ? meta(`First words. Still here, ${plural(f.count, 'message')} later.`) : null,
      ].filter(Boolean) as Node[],
    }
  }
  const fiveSpec = (): Spec => {
    const R = report!
    const shown = R.founders.filter((f) => f.who && f.msg)
    const gone = R.founders.length - R.founders.filter((f) => f.who).length
    return {
      kind: 'faces', faces: R.founders.map((f) => f.who), text: `The first ${word(R.founders.length)}`,
      foot: monthYear(R.founders[0]?.at || R.created, tz), more: 'Open', label: `The first ${word(R.founders.length)}`,
      open: () => [
        head(`${shown[0]?.msg ? `#${shown[0].msg.chn} · ` : ''}${dateRange(R.founders[0].at, R.founders[R.founders.length - 1].at, tz)}`),
        msgRows(shown.map((f) => f.msg!)),
        gone ? meta(gone === 1 ? 'And one more, who has since left.' : `And ${word(gone)} more, who have since left.`) : null,
      ].filter(Boolean) as Node[],
    }
  }
  const firstSpec = (): Spec => {
    const F = report!.first!
    const m = F.messages[0]
    return {
      kind: 'quote', who: m.who, text: clip(m.text) || mediaWord(m), foot: shortDate(m.at, tz), more: 'Open', label: 'The first message',
      open: () => [head(`#${F.channel} · ${longDate(m.at, tz)}`), msgRows(F.messages), link(m)].filter(Boolean) as Node[],
    }
  }

  // Arrivals: the count, a scrubber through the years, and dust falling into the form for
  // everyone who came.
  type Arrivals = { title: HTMLElement; extra: HTMLElement; run: () => void; holding: () => boolean; frame: (dt: number) => void }
  let arrivals: Arrivals | null = null
  const DAY = 86400_000
  function arrivalsModel() {
    const A = report!.arrivals
    const t0 = Date.parse(A.start), tEnd = Math.max(1, (Date.parse(A.end) - t0) / DAY)
    const pts = A.points.length ? A.points : [[0, 0], [tEnd, A.now]] as [number, number][]
    const countAt = (t: number) => {
      if (t <= pts[0][0]) return pts[0][0] > 0 ? 0 : pts[0][1]
      for (let i = 1; i < pts.length; i++) {
        if (t <= pts[i][0]) {
          const [a, ca] = pts[i - 1], [b, cb] = pts[i]
          return ca + (cb - ca) * ((t - a) / Math.max(1e-6, b - a))
        }
      }
      return pts[pts.length - 1][1]
    }
    const iso = (t: number) => new Date(t0 + t * DAY).toISOString()
    const miles = A.milestones.map((m) => ({ m, t: (Date.parse(m.at) - t0) / DAY }))
    return { t0, tEnd, countAt, iso, miles, total: Math.max(1, A.now) }
  }
  function mileHtml(m: Report['arrivals']['milestones'][number]): string {
    if (m.kind === 'nth') {
      const who = m.who ? `<b>${whoHtml(m.who)}</b>` : 'Someone'
      return m.weeks_before ? `${who} was the ${ordinal(m.n)}, ${span(m.weeks_before * 7 * DAY)} before I got here.` : `${who} was the ${ordinal(m.n)}.`
    }
    const line = m.line ? withNames(m.line, names()) + ' ' : ''
    return `${line}<b>${plural(m.count, 'person', 'people')}</b> ${m.line ? 'came that day.' : 'came on one day.'}`
  }
  function milePlain(m: Report['arrivals']['milestones'][number]): string {
    if (m.kind === 'nth') return `${nm(m.who)} was the ${ordinal(m.n)}.`
    return `${plural(m.count, 'person', 'people')} came on one day.`
  }
  const mileWhen = (m: Report['arrivals']['milestones'][number]) => (m.kind === 'burst' ? dayShort(m.day) : monthYear(m.at, tz))

  function makeArrivals(): Arrivals {
    const M = arrivalsModel()
    let p = 0, playing = false, lastCount = M.countAt(0), debt = 0, scrubbing = false
    const num = h('span', { class: 'num' }, fmt(lastCount))
    const when = h('span', { class: 'sub mono' }, monthYear(M.iso(0), tz))
    const unit = h('span', {}, ' people')
    const title = h('div', {}, h('span', { 'aria-hidden': 'true' }, num, unit, when),
      h('span', { class: 'visually-hidden' }, `From ${plural(M.countAt(0), 'person', 'people')} in ${monthYear(M.iso(0), tz)} to ${fmt(report!.arrivals.now)} now`))
    const input = h('input', { type: 'range', min: 0, max: 1000, value: 0, step: 1, 'aria-label': 'Scrub through the years' }) as HTMLInputElement
    const fill = h('div', { class: 'rp-track-fill' })
    const knob = h('div', { class: 'rp-track-knob' })
    const marks = M.miles.map(({ t }) => h('div', { class: 'rp-track-mark', style: { left: clamp(t / M.tEnd, 0, 1) * 100 + '%' } }))
    const btn = h('button', { type: 'button', class: 'ghost icon-btn sm', 'aria-label': 'Pause', 'data-tip': 'Pause', 'data-tip-side': 'up' }, o.icon('pause', 14))
    const startYear = local(M.iso(0), tz).y, endYear = local(M.iso(M.tEnd), tz).y
    const years: number[] = []
    for (let y = startYear + 1; y <= endYear; y++) years.push(y)
    const every = years.length > 8 ? 2 : 1
    const ticks = h('div', { class: 'rp-ticks', 'aria-hidden': 'true' }, years.filter((_, i) => i % every === 0).map((y) => {
      const t = (Date.UTC(y, 0, 1) - M.t0) / DAY
      return h('span', { style: { left: clamp(t / M.tEnd, 0, 1) * 100 + '%' } }, String(y))
    }))
    const miles = M.miles.map(({ m }) => h('div', { class: 'rp-mile', hidden: true }, h('time', {}, mileWhen(m)), h('span', { html: mileHtml(m) })))
    const extra = h('div', { class: 'rp-live-ctl' },
      h('div', { class: 'rp-scrub' }, btn, h('div', { class: 'rp-track' }, input, h('div', { class: 'rp-track-line' }, fill), marks, knob)),
      ticks, h('div', { class: 'rp-miles' }, miles))
    const setIcon = () => {
      btn.replaceChildren(o.icon(playing ? 'pause' : p >= 1 ? 'restart' : 'play', 14))
      const l = playing ? 'Pause' : p >= 1 ? 'Play again' : 'Play'
      btn.setAttribute('aria-label', l); btn.setAttribute('data-tip', l)
    }
    btn.addEventListener('click', (e) => {
      e.stopPropagation()
      if (playing) playing = false
      else { if (p >= 1) { p = 0; lastCount = M.countAt(0) } playing = true }
      setIcon()
    })
    const endScrub = () => { scrubbing = false; for (const t of ['pointerup', 'pointercancel', 'blur']) removeEventListener(t, endScrub) }
    input.addEventListener('pointerdown', (e) => {
      e.stopPropagation(); scrubbing = true; playing = false; setIcon()
      for (const t of ['pointerup', 'pointercancel', 'blur']) addEventListener(t, endScrub)
    })
    input.addEventListener('click', (e) => e.stopPropagation())
    input.addEventListener('input', () => { p = Number(input.value) / 1000; playing = false; setIcon() })
    input.addEventListener('keydown', (e) => e.stopPropagation())
    const DUR = 11
    let lastValueText = ''
    return {
      title, extra,
      run() { later(900, () => { playing = true; setIcon() }) },
      holding: () => scrubbing || playing,
      frame(dt) {
        if (playing) { p = Math.min(1, p + dt / (DUR * (reduce.matches ? 1.6 : 1))); if (p >= 1) { playing = false; setIcon() } }
        const t = p * M.tEnd
        const c = M.countAt(t)
        num.textContent = fmt(c)
        unit.textContent = Math.round(c) === 1 ? ' person' : ' people'
        when.textContent = monthYear(M.iso(t), tz)
        input.value = String(Math.round(p * 1000))
        // Said when it changes by a month, not every frame.
        const vt = `${monthYear(M.iso(t), tz)}, ${plural(c, 'person', 'people')}`
        if (vt !== lastValueText) { lastValueText = vt; input.setAttribute('aria-valuetext', vt) }
        fill.style.transform = `scaleX(${p})`
        knob.style.left = p * 100 + '%'
        M.miles.forEach(({ t: mt }, i) => {
          const hit = t >= mt - 0.5
          if (hit && miles[i].hidden) { miles[i].hidden = false; form?.pulse(0.9) }
          else if (!hit) miles[i].hidden = true
        })
        // Dust for everyone who joined since the last frame, a little at a time.
        const step = Math.max(1, M.total / 1000)
        if (c > lastCount) debt += (c - lastCount) / step
        lastCount = c
        const n = Math.min(Math.floor(debt), reduce.matches ? 6 : 26)
        debt -= n
        const fr = cam.r * 0.74 * formScale
        for (let k = 0; k < (reduce.matches ? Math.ceil(n / 2) : n); k++) {
          const a = Math.random() * Math.PI * 2, R = Math.hypot(W, H) * (0.45 + Math.random() * 0.2)
          const b = Math.random() * Math.PI * 2
          grain({ x0: cam.cx + Math.cos(a) * R, y0: cam.cy + Math.sin(a) * R, x1: cam.cx + Math.cos(b) * fr * 0.85, y1: cam.cy + Math.sin(b) * fr * 0.85, dur: 1.5 + Math.random() * 0.9, delay: Math.random() * 0.15, bend: (Math.random() - 0.5) * 0.7 })
        }
        // The body grows with the people in it.
        const v = 0.3 + 0.7 * Math.sqrt(c / M.total)
        formScale = v
        form?.set(SHAPE.whole, v)
        form?.energy(calm(SHAPE.whole, v, 0.1))
      },
    }
  }
  const peopleSpec = (): Spec => {
    const R = report!
    const faces = R.arrivals.milestones.filter((m) => m.kind === 'nth').map((m) => (m as { who: string | null }).who).filter(Boolean) as string[]
    return {
      kind: 'count', faces: faces.slice(0, 3), big: fmt(R.arrivals.now), foot: R.arrivals.now === 1 ? 'person' : 'people', more: 'Open', label: plural(R.arrivals.now, 'person', 'people'),
      open: () => [
        head(`Since ${longDate(R.created, tz)}`),
        h('div', { class: 'rp-mo-term num' }, fmt(R.arrivals.now)),
        h('div', { class: 'rp-mo-text' }, R.arrivals.milestones.map((m) => h('div', { class: 'rp-mo-meta rp-mo-line' }, `${mileWhen(m)}: ${milePlain(m)}`))),
      ],
    }
  }

  // The busiest day.
  const nightTitle = () => (report!.night!.night ? 'The longest night' : 'The busiest day')
  const nightSpec = (): Spec => {
    const N = report!.night!
    const first = N.excerpt.find((m) => !isGap(m)) as Msg | undefined
    return {
      kind: 'faces', faces: N.faces, text: N.sphere || nightTitle(), foot: plural(N.count, 'message'), more: 'Open',
      label: `${nightTitle()}, ${dayName(N.day)}`,
      open: () => [
        head(`#${N.channel} · ${dayName(N.day)}`),
        N.excerpt.length ? msgRows(N.excerpt) : meta(`${plural(N.count, 'message')} that day, ${fmt(N.in_channel)} of them in #${N.channel}.`),
        link(first),
      ].filter(Boolean) as Node[],
    }
  }

  // Olisar's pick: the one thing that most belongs to this server (olisar/replay/pick.py).
  const pickTitle = () => { const P = report!.pick!; return P.kind === 'word' || P.kind === 'joke' ? `“${P.title}”` : cap(P.title) }
  const KIND: Record<string, string> = { word: 'A word', joke: 'A running joke', ritual: 'A ritual', made: 'Something you made', place: 'A place', game: 'A game', emoji: 'An emoji', other: 'My pick' }
  const pickSpec = (again: boolean, compact = false): Spec => {
    const P = report!.pick!
    return {
      kind: 'term', term: P.title, long: P.title.length > 10, text: compact ? undefined : P.meaning, foot: plural(P.count, P.unit.replace(/s$/, ''), P.unit),
      more: again ? 'Again' : 'Open', label: `${P.title}, my pick`,
      ...(again ? { onClick: () => spreadPick() } : {
        open: () => [
          head(KIND[P.kind] || KIND.other),
          h('div', { class: 'rp-mo-term', dir: 'auto' }, P.title),
          h('div', { class: 'rp-mo-text' }, cap(P.meaning) + '.'),
          msgRows([P.origin]),
          P.why ? meta(P.why) : null,
          link(P.origin),
        ].filter(Boolean) as Node[],
      }),
    }
  }
  function pickSpreadBody(P: Pick, count: HTMLElement): HTMLElement {
    // The writer's line, with {people} and {count} filled from the stats.
    const parts = P.spread.split(/(\{people\}|\{count\})/)
    const out = h('div')
    for (const part of parts) {
      if (part === '{people}') out.append(h('b', {}, fmt(P.people)))
      else if (part === '{count}') out.append(count)
      else if (part) out.append(h('span', { html: withNames(part, names()) }))
    }
    return out
  }

  // The pick spreading: where its dust lands, as fractions of the window. In a narrow window
  // the spots stack in two staggered columns between the sphere and the narration.
  const SPOTS = [[0.48, 0.17], [0.68, 0.12], [0.87, 0.52], [0.46, 0.8], [0.77, 0.82], [0.92, 0.7], [0.62, 0.78], [0.53, 0.31], [0.79, 0.64], [0.47, 0.6]]
  let spreadEl: HTMLElement | null = null, spreadCount: HTMLElement | null = null
  function spreadPick() {
    const S = spheres.get('pick'), P = report?.pick
    if (!S || !P) return
    if (!spreadEl) { spreadEl = h('div', { class: 'rp-spread', 'aria-hidden': 'true' }); root.append(spreadEl) }
    spreadEl.replaceChildren()
    motes.length = 0
    form?.pulse(0.5)
    S.ripple = 1
    let landed = 0
    let at = SPOTS.slice(0, P.spots.length)
    if (!wide()) {
      const y0 = S.y + S.r + 52, y1 = layoutRect(narrEl).y - 26   // clear of the sphere's caption
      const fit = clamp(Math.floor((y1 - y0) / 18) + 1, 2, P.spots.length)
      at = Array.from({ length: fit }, (_, i) => [i % 2 ? 0.72 : 0.28, (y0 + (y1 - y0) * (fit > 1 ? i / (fit - 1) : 0)) / H])
    }
    at.forEach(([fx0, fy0], i) => {
      const x = fx0 * W, y = fy0 * H, what = P.spots[i]
      const spot = h('div', { class: 'rp-spot', style: { left: x + 'px', top: y + 'px' } }, what.said ? h('q', { dir: 'auto' }, what.said) : null, h('span', {}, what.where))
      spreadEl!.append(spot)
      const n = 7
      for (let k = 0; k < n; k++) {
        grain({
          x0: S.x + (Math.random() - 0.5) * S.r * 0.6, y0: S.y + (Math.random() - 0.5) * S.r * 0.6,
          x1: x + (Math.random() - 0.5) * 18, y1: y + (Math.random() - 0.5) * 10, out: true,
          delay: 0.15 + i * 0.32 + k * 0.03, dur: 1.1 + Math.random() * 0.4, bend: (Math.random() - 0.5) * 0.6, acc: k === 0,
          land: k === n - 1 ? () => { spot.classList.add('on'); landed++; if (spreadCount) spreadCount.textContent = fmt(Math.round(P.count * landed / at.length)); dustAt(x, y + 16) } : null,
        })
      }
    })
    // And a haze of the rest, landing everywhere else.
    for (let k = 0; k < 160; k++) {
      const a = Math.random() * Math.PI * 2, d = 0.25 + Math.random() * 0.6
      grain({ x0: S.x, y0: S.y, x1: S.x + Math.cos(a) * d * W * 0.5, y1: S.y + Math.sin(a) * d * H * 0.55, out: true, delay: 0.3 + Math.random() * 3.2, dur: 1.3 + Math.random(), alpha: 0.45 })
    }
  }
  function dustAt(x: number, y: number) {
    const born = performance.now() / 1000
    for (let k = 0; k < 9; k++) motes.push({ x: x + (Math.random() - 0.5) * 46, y: y + (Math.random() - 0.3) * 14, size: 0.6 + Math.random() * 0.8, a: 0.25 + Math.random() * 0.35, w: 0.8 + Math.random() * 1.6, ph: Math.random() * 6.28, acc: Math.random() < 0.2, born })
  }
  function clearSpread() { spreadEl?.replaceChildren(); grains.length = 0; motes.length = 0 }
  const pickSphere = (again: boolean): Want => wide()
    ? { id: 'pick', spec: pickSpec(again), x: W * 0.62, y: H * 0.5, r: 98 }
    : { id: 'pick', spec: pickSpec(again), x: W * 0.5, y: H * 0.3, r: 80 }

  // Olisar's arrival, and its renames.
  const replySpec = (): Spec => {
    const A = report!.arrive!
    const mine = A.reply[A.reply.length - 1]
    return {
      kind: 'quote', who: 'bot', text: clip(mine.text) || mediaWord(mine), foot: shortDate(mine.at, tz), more: 'Open', label: 'My first reply',
      open: () => [head(`${mine.trigger === 'ask' ? '/ask · ' : ''}#${mine.chn} · ${longDate(mine.at, tz)}`), msgRows(A.reply), link(mine)].filter(Boolean) as Node[],
    }
  }
  // The rename to tell: the last one that was taken back, or else the last one.
  function renameStory() {
    const N = report!.names
    if (!N.length) return null
    for (let i = N.length - 1; i > 0; i--) {
      const back = N[i], to = N[i - 1]
      if (back.from === to.to && back.to === to.from) return { to, back }
    }
    return { to: N[N.length - 1], back: null as Report['names'][number] | null }
  }
  const pineSpec = (): Spec => {
    const st = renameStory()!
    const lasted = st.back ? span(Date.parse(st.back.at) - Date.parse(st.to.at)) : `Since ${monthYear(st.to.at, tz)}`
    const by = (n: Report['names'][number]) => `${n.by ? nm(n.by) : 'Someone'}${n.via === 'chat' ? ', through chat' : ', in the console'}`
    // The same person the same way both times reads as one change and its undoing.
    const changed = (to: Report['names'][number], back: Report['names'][number] | null) =>
      !back ? `Changed by ${by(to)}.`
        : back.by === to.by && back.via === to.via ? `Changed by ${by(to)}, and back again.`
        : `Changed by ${by(to)}. Changed back by ${by(back)}.`
    return {
      kind: 'name', text: st.to.to, foot: lasted, more: 'Open', label: st.back ? `The ${lasted} I was ${st.to.to}` : `When I became ${st.to.to}`,
      open: () => [
        head(st.back ? `${shortDate(st.to.at, tz)} to ${shortDate(st.back.at, tz)}` : longDate(st.to.at, tz)),
        h('div', { class: 'rp-mo-term', dir: 'auto' }, st.to.to),
        meta(`${changed(st.to, st.back)} The Activity log kept the old name.`),
      ],
    }
  }

  // ── The beats ──
  // Where each memory sits, relative to where the form is.
  const around = (c: { cx: number; cy: number }, n: number, a0: number, a1: number, rx: number, ry: number, r: number, ids: string[], specs: Spec[]): Want[] =>
    ids.map((id, i) => {
      const a = ((a0 + (a1 - a0) * (n > 1 ? i / (n - 1) : 0.5)) * Math.PI) / 180
      return { id, spec: specs[i], x: c.cx + Math.cos(a) * rx, y: c.cy + Math.sin(a) * ry, r }
    })
  let stopQuote: (() => void) | null = null
  let chapters: string[] = []
  let beats: Beat[] = []

  function buildBeats(): Beat[] {
    const R = report!
    const out: Beat[] = []
    const T: string[] = []
    const ch = (title: string) => { T.push(title); return T.length - 1 }
    const created = R.created
    const startTitle = longDate(created, tz)

    const c0 = ch('Before I was here')
    out.push({
      id: 'cold', ch: c0, auto: 6, cam: () => CAM.stage(),
      form: { shape: SHAPE.whole, v: 0.05, energy: -0.6, mood: { dim: 0.8 }, density: 0.03, feed: 0.0006, spark: 0.6 },
      narr: () => ({ title: startTitle, body: `${SRV()} started on ${partOfDay(created, tz)}.` }),
      hint: () => (coarse.matches ? 'Tap anywhere to continue' : 'Click anywhere or press <kbd>→</kbd> to continue'),
    })
    if (R.first?.messages.length) {
      const F = R.first
      out.push({
        id: 'first', ch: c0, auto: 9, cam: () => CAM.stage(),
        form: { shape: SHAPE.whole, v: 0.085, energy: -0.3, mood: { dim: 0.9 }, density: 0.06, feed: 0.001, spark: 0.55 },
        narr: () => {
          const q = quote(F.messages)
          later(0, () => {
            stopQuote = q.play!((k) => {
              const v = 0.085 + 0.035 * k
              form?.pulse(0.7); form?.set(SHAPE.whole, v); form?.energy(calm(SHAPE.whole, v, -0.3)); form?.density(0.06 + 0.045 * k)
              sparkT = 0.55 - 0.12 * k
            })
          })
          return { title: longDate(F.messages[0].at, tz), body: `The first messages in ${SRV()}.`, extra: q, extraKey: 'first-day', foot: `<span class="mono">#${esc(F.channel)}</span>`, footKey: 'first-channel' }
        },
      })
    }
    if (R.founders.length >= 2) {
      const F = R.founders
      const n = F.length
      const firstAt = Date.parse(F[0].at), lastAt = Date.parse(F[n - 1].at)
      const within = lastAt - firstAt
      const lead = within <= 7 * DAY ? `${cap(word(n))} people the first week.` : within <= 31 * DAY ? `${cap(word(n))} people the first month.` : `The first ${word(n)} came over ${span(within)}.`
      const here = F.filter((f) => f.who || !f.gone).length
      const still = here === n ? (n === 2 ? 'Both are still here.' : 'All of them are still here.') : here === 0 ? 'None of them are still here.' : `${cap(word(here))} of them ${here === 1 ? 'is' : 'are'} still here.`
      out.push({
        id: 'five', ch: c0, auto: 8, cam: () => (wide() ? CAM.stage() : { ...CAM.stage(), cy: H * 0.36 }),
        form: { shape: SHAPE.whole, v: 0.3, energy: 0, mood: { dim: 1 }, density: 1, pulse: 0.6 },
        narr: () => ({ title: startTitle, body: `${lead} ${still}` }),
        spheres: () => {
          const ids = F.map((_, i) => 'f-' + i), specs = F.map(founderSpec)
          return wide()
            ? around(CAM.stage(), n, -150, 70, H * 0.34, H * 0.31, 66, ids, specs)
            : around({ cx: W * 0.5, cy: H * 0.36 }, n, -126, 162, Math.min(W * 0.33, 130), H * 0.165, 46, ids, specs)
        },
        hint: 'Open one to see their first words',
      })
    }
    if (R.arrivals.now > 1 && R.arrivals.points.length > 1) {
      out.push({
        id: 'arrivals', ch: ch('Everyone else'), auto: 5, cam: () => CAM.stage(),
        form: { shape: SHAPE.whole, v: 0.3, energy: 0.1, mood: { dim: 1 } },
        narr: () => {
          arrivals = makeArrivals()
          return { title: arrivals.title, titleKey: 'arrivals', body: 'Then people kept arriving.', extra: arrivals.extra, extraKey: 'arrivals' }
        },
        enter: () => arrivals?.run(),
        frame: (dt) => arrivals?.frame(dt),
        leave: () => { arrivals = null; grains.length = 0 },
      })
    }
    if (R.night) {
      const N = R.night
      const most = N.in_channel >= 0.8 * N.count
      // The busiest day, or one of them when the busiest wasn't safe to show.
      const which = N.rank ? `one of the busiest days ${SRV()} has had` : `the most ${SRV()} has sent in one day`
      const body = most
        ? `<b>${plural(N.count, 'message')}</b> in <span class="mono">#${esc(N.channel)}</span>, ${which}.`
        : `<b>${plural(N.count, 'message')}</b>, ${which}. Most of them were in <span class="mono">#${esc(N.channel)}</span>.`
      out.push({
        id: 'night', ch: ch(nightTitle()), auto: 9, cam: () => CAM.corner(),
        form: { shape: SHAPE.whole, v: 1, energy: 0.35, mood: { dim: 0.85 } },
        narr: () => ({ title: dayName(N.day), body: body + (N.line ? ' ' + withNames(N.line, names()) : '') }),
        spheres: () => [wide() ? { id: 'night', spec: nightSpec(), x: W * 0.6, y: H * 0.56, r: 118 } : { id: 'night', spec: nightSpec(), x: W * 0.5, y: H * 0.32, r: 96 }],
        hint: N.night ? 'Open it to read that night' : 'Open it to read that day',
      })
    }
    if (R.pick?.origin) {
      const P = R.pick
      const c3 = ch(cap(P.title))
      out.push({
        id: 'pick', ch: c3, auto: 8, cam: () => CAM.corner(0.85),
        form: { shape: SHAPE.whole, v: 1, energy: 0.2, mood: { dim: 0.7 } },
        narr: () => ({ title: esc(pickTitle()), titleKey: 'pick-title', body: withNames(P.intro, names()), extra: quote([P.origin], `#${P.origin.chn} · ${longDate(P.origin.at, tz)}`), extraKey: 'pick-origin' }),
        enter: () => { const q = slotEl.extra.lastElementChild as Quote | null; if (q?.play) stopQuote = q.play() },
        spheres: () => [pickSphere(false)],
      })
      out.push({
        id: 'spread', ch: c3, auto: 9, cam: () => CAM.corner(0.85),
        form: { shape: SHAPE.whole, v: 1, energy: 0.5, mood: { dim: 0.7 } },
        narr: () => {
          spreadCount = h('span', { class: 'num' }, '0')
          return { title: esc(pickTitle()), titleKey: 'pick-title', body: pickSpreadBody(P, spreadCount), bodyKey: 'spread' }
        },
        enter: () => later(300, spreadPick),
        leave: clearSpread,
        spheres: () => [pickSphere(true)],
      })
    }
    const c4 = ch('Then I showed up')
    if (R.arrive) {
      const A = R.arrive
      out.push({
        id: 'arrive', ch: c4, auto: 10, cam: () => CAM.stage(1.06),
        form: { shape: SHAPE.bot, energy: 0.3, mood: { dim: 1 }, pulse: 1, regather: 0.4 },
        narr: () => {
          const q = A.reply.length ? quote(A.reply) : null
          if (q) later(900, () => { stopQuote = q.play!((k) => form?.pulse(k === A.reply.length ? 1 : 0.4)) })
          return { title: longDate(A.at, tz), body: 'Here’s where I joined.', extra: q || undefined, extraKey: 'first-reply', foot: q ? 'My first reply' : undefined }
        },
      })
    }
    const story = renameStory()
    if (story) {
      const { to, back } = story
      out.push({
        id: 'renamed', ch: c4, auto: 8, cam: () => CAM.stage(1.06),
        form: { shape: SHAPE.bot, energy: 0.2, mood: { dim: 1 } },
        narr: () => {
          const a = h('span', { dir: 'auto' }, to.from), i1 = h('i', { hidden: true }, '→'), b = h('span', { hidden: true, dir: 'auto' }, to.to)
          const i2 = h('i', { hidden: true }, '→'), c = h('span', { hidden: true, dir: 'auto' }, back ? back.to : '')
          const chain = h('div', { class: 'rp-names' }, a, i1, b, ...(back ? [i2, c] : []))
          later(900, () => { i1.hidden = false; b.hidden = false; a.classList.add('was'); form?.reject() })
          if (back) later(2600, () => { i2.hidden = false; c.hidden = false; b.classList.add('was'); form?.pulse(0.8) })
          const body = back
            ? `You renamed me ${bdi(to.to)}. It lasted ${span(Date.parse(back.at) - Date.parse(to.at))}.`
            : `You renamed me ${bdi(to.to)}.`
          return { title: longDate(to.at, tz), body, extra: chain, extraKey: 'renamed' }
        },
      })
    }
    // Nothing else in Olisar's chapter: it's dropped, and with it its title.
    if (!out.some((b) => b.ch === c4)) T.pop()
    const c5 = ch('Now')
    out.push({
      id: 'now', ch: c5, auto: 0, cam: () => CAM.end(),
      form: { shape: SHAPE.whole, v: 1, energy: 0, mood: { dim: 0.9 } },
      narr: () => ({}),
      spheres: () => ring(),
      enter: () => center.start(),
      leave: () => center.stop(),
    })
    chapters = T
    // Chapter numbers in order, whichever were dropped.
    const order = [...new Set(out.map((b) => b.ch))]
    for (const b of out) b.ch = order.indexOf(b.ch)
    chapters = order.map((i) => T[i])
    return out
  }

  // The end: what the replay showed, back on one ring round the form, the way the final
  // server screen keeps its memories.
  let answer: string | null = null
  function ring(): Want[] {
    const R = report!
    const list: { id: string; spec: Spec }[] = []
    if (R.founders.length >= 2) list.push({ id: 'end-five', spec: fiveSpec() })
    if (R.first?.messages.length) list.push({ id: 'end-first', spec: firstSpec() })
    if (R.arrivals.now > 1) list.push({ id: 'end-people', spec: peopleSpec() })
    if (R.night) list.push({ id: 'end-night', spec: nightSpec() })
    if (R.pick?.origin) list.push({ id: 'end-pick', spec: pickSpec(false, true) })
    if (R.arrive?.reply.length) list.push({ id: 'end-reply', spec: replySpec() })
    if (renameStory()) list.push({ id: 'end-renamed', spec: pineSpec() })
    if (answer) {
      const text = answer
      list.push({ id: 'end-answer', spec: { kind: 'answer', text, foot: 'Just now', label: 'Your answer', more: 'Open', open: () => [head('Saved for next year’s replay'), h('div', { class: 'rp-mo-term rp-mo-answer', dir: 'auto' }, `“${text}”`)] } })
    }
    const c = CAM.end()
    const n = list.length
    if (!wide()) {
      const top = Math.ceil(n / 2), box = 72
      const r = Math.min(46, (W - 24 - (top - 1) * 8) / (top * 2))
      const y1 = clamp((112 + c.cy - box) / 2, 112 + r, c.cy - box - r)
      const y2 = clamp((c.cy + box + H - 70) / 2, c.cy + box + r, H - 70 - r)
      const row = (k: number, count: number, y: number, dir: number) => {
        const mid = (count - 1) / 2, bow = mid ? 1 - ((k - mid) / mid) ** 2 : 1
        return { x: count > 1 ? 12 + r + (W - 24 - 2 * r) * k / (count - 1) : W / 2, y: y + dir * bow * Math.min(22, r * 0.5) }
      }
      return list.map((m, i) => ({ ...m, r, ...(i < top ? row(i, top, y1, -1) : row(i - top, n - top, y2, 1)) }))
    }
    const rx = Math.min(W * 0.37, 600), ry = H * 0.33, r = clamp(H * 0.085, 60, 86)
    return list.map((m, i) => {
      const a = (-100 + (360 / n) * i) * Math.PI / 180
      return { ...m, x: c.cx + Math.cos(a) * rx, y: c.cy + Math.sin(a) * ry, r }
    })
  }

  // The question at the end, set inside the form like the final screen's name.
  const center = (() => {
    const line = $('.rp-center-line'), sub = $('.rp-center-sub'), fm = $<HTMLFormElement>('.rp-center form'), input = $<HTMLInputElement>('.rp-center input'), acts = $('.rp-center-acts')
    const save = $<HTMLButtonElement>('.rp-center form button')
    let on = false
    fm.addEventListener('submit', async (e) => {
      e.preventDefault()
      const v = input.value.replace(/\s+/g, ' ').trim()
      if (!v) { input.focus(); return }
      save.disabled = true
      const ok = await o.answer(v).catch(() => false)
      save.disabled = false
      if (!ok || dead) return
      answer = v
      input.value = ''
      setSpheres(ring())
      form?.pulse(1)
      swap('Saved. See you next year.', 'It’ll be in next year’s replay.', 'done')
    }, { signal })
    function swap(l: string, s: string, mode: 'count' | 'ask' | 'done') {
      centerEl.classList.remove('on')
      later(320, () => {
        line.textContent = l
        sub.textContent = s || ''
        sub.hidden = !s
        fm.hidden = mode !== 'ask'
        acts.hidden = mode !== 'done'
        centerEl.classList.add('on')
        if (mode === 'ask' && !coarse.matches) input.focus({ preventScroll: true })
        if (mode === 'done' && keyed) $<HTMLButtonElement>('.rp-done').focus({ preventScroll: true })
      })
    }
    return {
      start() {
        on = true
        faceEl.classList.add('on')
        input.placeholder = `Something from ${new Date().getFullYear()}`
        if (answer) { swap('Saved. See you next year.', '', 'done'); return }
        swap(`${fmt(report!.totals.people)} of you now.`, '', 'count')
        later(2900, () => swap('What should I remember from this year?', '', 'ask'))
      },
      stop() { on = false; centerEl.classList.remove('on'); faceEl.classList.remove('on') },
      frame() {
        if (!on) return
        centerEl.style.transform = `translate(${cam.cx}px, ${cam.cy}px) translate(-50%, -50%)`
        const d = cam.r * 1.48
        faceEl.style.transform = `translate(${cam.cx - d / 2}px, ${cam.cy - d / 2}px)`
        faceEl.style.width = faceEl.style.height = d + 'px'
      },
    }
  })()
  $('.rp-again').addEventListener('click', (e) => { e.stopPropagation(); show(0) }, { signal })
  $('.rp-done').addEventListener('click', (e) => { e.stopPropagation(); o.go('play') }, { signal })
  centerEl.addEventListener('click', (e) => e.stopPropagation(), { signal })

  // ── Moving between beats ──
  let cur = -1, autoplay = false, autoT = 0, seenHint = false
  const beat = () => beats[cur] as Beat | undefined
  function buildTimeline() {
    timelineEl.replaceChildren(...chapters.map((title, i) => {
      const btn = h('button', { type: 'button', class: 'rp-tl', 'data-ch': i, 'aria-label': `Chapter ${i + 1}: ${title}` },
        h('div', { class: 'rp-tl-line' }, h('div', { class: 'rp-tl-fill' })), h('div', { class: 'rp-tl-name' }, title))
      btn.addEventListener('click', (e) => { e.stopPropagation(); show(beats.findIndex((b) => b.ch === i)) })
      return btn
    }))
  }
  function updateTimeline() {
    const b = beat()
    if (!b) return
    for (const el of [...timelineEl.children] as HTMLElement[]) {
      const ch = Number(el.dataset.ch)
      const inCh = beats.filter((x) => x.ch === ch)
      const idx = inCh.indexOf(b)
      const f = ch < b.ch ? 1 : ch > b.ch ? 0 : (idx + 1) / inCh.length
      ;(el.querySelector('.rp-tl-fill') as HTMLElement).style.transform = `scaleX(${f})`
      el.classList.toggle('now', ch === b.ch)
      if (ch === b.ch) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current')
    }
  }
  function show(i: number) {
    if (i < 0 || i >= beats.length) return
    closeSphere(true)
    const prev = beat()
    stopQuote?.(); stopQuote = null
    clearTimers()
    prev?.leave?.()
    cur = i
    const b = beat()!
    camFn = b.cam
    applyForm(b.form)
    setSpheres(b.spheres ? b.spheres() : [])
    narrate(b.narr())
    b.enter?.()
    updateTimeline()
    if (b.hint && !(b.id === 'cold' && seenHint)) hint(typeof b.hint === 'function' ? b.hint() : b.hint)
    else hint(null)
    if (b.id === 'cold') seenHint = true
    autoT = 0
  }
  const next = () => { if (cur < beats.length - 1) show(cur + 1) }
  const prev = () => { if (cur > 0) show(cur - 1) }

  function setAuto(v: boolean) {
    autoplay = v; autoT = 0
    autoBtn.replaceChildren(o.icon(v ? 'pause' : 'play', 16))
    const l = v ? 'Stop autoplay' : 'Autoplay'
    autoBtn.setAttribute('aria-label', l); autoBtn.setAttribute('data-tip', l); autoBtn.setAttribute('aria-pressed', String(v))
  }
  autoBtn.addEventListener('click', (e) => { e.stopPropagation(); setAuto(!autoplay) }, { signal })
  setAuto(false)

  // ── Stages ──
  let stage: Stage | null = null
  let keyed = false
  const onKeyFlag = () => { keyed = true }
  const onPointerFlag = () => { keyed = false }
  addEventListener('keydown', onKeyFlag, { capture: true, signal })
  addEventListener('pointerdown', onPointerFlag, { capture: true, signal })

  // Remembering: the form follows the steps (streams drawn in while it gathers, the server's
  // orbit while it counts, the whole body while it reads, keys while it writes), and each
  // moment it keeps buds off as a sphere, out of focus so nothing is spoiled.
  let remPhase = -1, remHalt = false
  function remember(p: Progress, halted: boolean) {
    if (stage !== 'remember') return
    const phase = p.phase ?? 0
    if (halted !== remHalt) {
      remHalt = halted
      remPhase = -1
      if (halted) { applyForm({ shape: SHAPE.whole, v: 0.86, energy: -0.5, mood: { dim: 0.55 } }); return }
      applyForm({ shape: SHAPE.whole, v: 0.9, energy: 0.2, mood: { dim: 1 }, pulse: 0.6 })
    }
    if (halted) return
    if (phase !== remPhase) {
      const was = remPhase
      remPhase = phase
      if (phase <= 0) applyForm({ shape: SHAPE.remember, v: 1, energy: 0.6, mood: { dim: 1 }, feed: 0.007, regather: was < 0 ? 0.5 : undefined })
      else if (phase === 1) { applyForm({ shape: SHAPE.server, v: 0.15, energy: 0.3, mood: { dim: 1 }, feed: 0.003 }); later(500, () => form?.set(SHAPE.server, 1)) }
      else if (phase === 2) applyForm({ shape: SHAPE.whole, v: 0.9, energy: 0.2, mood: { dim: 1 } })
      else if (phase === 3) applyForm({ shape: SHAPE.keys, v: 1, energy: 0.3, mood: { dim: 1 } })
      else form?.pulse(1)
    }
    const found = phase === 2 ? (p.found || []).slice(0, 8) : []
    const c = CAM.remember()
    const want = found.map((f, i): Want => {
      const a = (-200 + i * (220 / 7)) * Math.PI / 180
      const r = wide() ? 50 : 42
      return { id: 'b' + i, spec: { kind: 'blur', faces: f.faces.slice(0, 3), foot: f.year || '' }, x: clamp(c.cx + Math.cos(a) * Math.min(W * 0.36, 520), r + 16, W - r - 16), y: c.cy + Math.sin(a) * H * 0.3, r, delay: 0 }
    })
    if (want.length > spheres.size) form?.pulse(0.6)
    setSpheres(want)
  }

  function go(next: Stage, opts: { ready?: boolean; beat?: string } = {}) {
    if (stage === 'replay') { closeSphere(true); stopQuote?.(); clearTimers(); beat()?.leave?.(); cur = -1; narrate({}); setSpheres([]); hint(null); clearSpread() }
    if (stage === 'remember') { remPhase = -1; remHalt = false; setSpheres([]) }
    stage = next
    root.classList.toggle('rp-replay', next === 'replay')
    if (next === 'start') {
      camFn = CAM.remember
      applyForm({ shape: SHAPE.whole, v: 0.92, energy: 0, mood: { dim: 0.9 } })
      setSpheres([])
    } else if (next === 'remember') {
      camFn = CAM.remember
    } else if (next === 'play') {
      camFn = CAM.remember
      applyForm({ shape: SHAPE.whole, v: 0.92, energy: 0, mood: { dim: 0.8 }, pulse: opts.ready ? 1 : 0 })
      setSpheres([])
    } else if (next === 'replay' && report) {
      beats = buildBeats()
      buildTimeline()
      show(opts.beat ? Math.max(0, beats.findIndex((b) => b.id === opts.beat)) : 0)
    }
  }

  // ── Input ──
  const catchEl = $('.rp-catch')
  catchEl.addEventListener('click', () => {
    if (stage !== 'replay') return
    if (openId) { closeSphere(); return }
    next()
  }, { signal })
  function onKey(e: KeyboardEvent) {
    const target = e.target as HTMLElement | null
    const typing = target?.closest?.('input, textarea') && !(target as HTMLInputElement).matches('[type=range]')
    if (e.key === 'Escape') {
      if (openId) { e.preventDefault(); closeSphere(); return }
      if (stage === 'replay' && !typing) { e.preventDefault(); o.go('play') }
      return
    }
    if (stage !== 'replay' || typing) return
    const onControl = target?.closest?.('button, a, input') && !target.classList.contains('rp-catch')
    if (e.key === 'ArrowRight' || ((e.key === ' ' || e.key === 'Enter') && !onControl)) {
      e.preventDefault()
      if (openId) closeSphere()
      else next()
    } else if (e.key === 'ArrowLeft' && !target?.matches('[type=range]')) { e.preventDefault(); if (openId) closeSphere(); else prev() }
  }
  addEventListener('keydown', onKey, { capture: true, signal })

  // ── The clock ──
  function frame(dt: number) {
    stepCam(dt)
    stepSpark(dt)
    const b = beat()
    if (stage === 'replay' && b) {
      b.frame?.(dt)
      if (autoplay && b.auto && !openId && !(arrivals && b.id === 'arrivals' && arrivals.holding())) {
        autoT += dt
        if (autoT > b.auto) next()
      }
    }
    stepSpheres(dt)
    stepOpen(dt)
    center.frame()
    stepFx(dt)
  }
  let stopFrames: () => void
  if (form) stopFrames = form.onFrame(frame)
  else {
    let last = 0, raf = 0
    const loop = (now: number) => { const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60; last = now; frame(dt); raf = requestAnimationFrame(loop) }
    raf = requestAnimationFrame(loop)
    stopFrames = () => cancelAnimationFrame(raf)
  }

  const placeUnder = () => { root.style.setProperty('--under-top', remTop() + 'px'); root.classList.toggle('narrow', !wide()) }
  function onResize() {
    W = innerWidth / zoomOf(); H = innerHeight / zoomOf()
    sizeFx(); placeUnder()
    const b = stage === 'replay' ? beat() : undefined
    if (b?.spheres) setSpheres(b.spheres())
  }
  addEventListener('resize', onResize, { signal })
  sizeFx()
  placeUnder()
  Object.assign(cam, camFn())

  return {
    /** Move to a stage (the host owns which one; see replay.tsx). */
    stage(next: Stage, opts?: { ready?: boolean; beat?: string }) { if (next !== stage || opts?.beat) go(next, opts) },
    /** What the remembering screen shows the form: the run's progress, and whether it stopped. */
    remember,
    /** The finished replay to play. */
    load(r: Report) { report = r; tz = r.tz; answer = r.answer },
    /** How many chapters the loaded replay has, and about how many minutes it plays, for
     *  Play's line. */
    summary() {
      if (!report) return { chapters: 0, minutes: 0 }
      const b = buildBeats()
      return { chapters: chapters.length, minutes: Math.max(1, Math.round(b.reduce((m, x) => m + (x.auto || 20), 0) / 60 + 1)) }
    },
    setAuto,
    destroy() {
      dead = true
      clearTimers(); stopQuote?.()
      stopFrames()
      slotSize.disconnect()
      life.abort()
      spreadEl?.remove()
      form?.satellites([])
    },
  }
}

export type Scene = ReturnType<typeof createScene>
