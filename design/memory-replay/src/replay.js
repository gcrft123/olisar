// ── Memory replay (prototype) ─────────────────────────────────────────────────────
// The page that isn't the engine: a start sheet, the remembering screen, Play, and the
// replay itself, for an example server called Hollow Pines. One clock runs everything: the
// engine's frame hook steps the camera (where the form sits), the memories (their spheres and
// the words set in them), the dust thrown across the window, and whatever the current beat is
// animating. Every beat is a state the page eases toward, so going back is the same motion run
// the other way.

;(function () {
'use strict'
const { createForm, SHAPE, NSAT } = window.Engine
const ASSETS = window.ASSETS || {}

// ── Helpers ────────────────────────────────────────────────────────────────────
const clamp = (x, a, b) => Math.min(b, Math.max(a, x))
const lerp = (a, b, t) => a + (b - a) * t
const smooth = (t) => t * t * (3 - 2 * t)
const $ = (s, r = document) => r.querySelector(s)
const fmt = (n) => Math.round(n).toLocaleString('en-US')
const plural = (n, one, many = one + 's') => `${fmt(n)} ${Math.round(n) === 1 ? one : many}`
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
const q = new URLSearchParams(location.search)
// Anything a member or Discord wrote is set as text, never as markup. Where a line has to be
// markup (bold, mono), what's dropped into it goes through esc(), and names are isolated in
// <bdi> so a right-to-left name can't reorder the English around it.
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
// The server's name: up to 100 characters, in any script. ?server= tries another.
const SERVER = (q.get('server') || '').trim().slice(0, 100) || (q.has('stress') ? 'The Hollow Pines Cozy Games, Book Club & Late Night Hangout 🌲' : 'Hollow Pines')
const SRV = `<bdi>${esc(SERVER)}</bdi>`

function h(tag, props, ...kids) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue
    if (k === 'class') el.className = v
    else if (k === 'html') el.innerHTML = v
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v)
    else if (k === 'style') Object.assign(el.style, v)
    else el.setAttribute(k, v === true ? '' : v)
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c.nodeType ? c : String(c))
  return el
}

// Solar-style linear icons, drawn here so the page needs nothing else.
const ICON = {
  play: '<path d="M8 6.5v11c0 .8.9 1.3 1.6.9l8.6-5.5c.6-.4.6-1.3 0-1.7L9.6 5.6C8.9 5.2 8 5.7 8 6.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
  pause: '<path d="M8.5 6v12M15.5 6v12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  close: '<path d="m7 7 10 10M17 7 7 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  back: '<path d="m14.5 6-6 6 6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  again: '<path d="M5 12a7 7 0 1 0 2.05-4.95M5 5v3.5h3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  x: '<path d="m8.5 8.5 7 7M15.5 8.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  undo: '<path d="M9 8 5 12l4 4M5.5 12H15a4 4 0 0 1 0 8h-2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
}
const icon = (name, size = 16) => {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  s.setAttribute('width', size); s.setAttribute('height', size); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none')
  s.setAttribute('aria-hidden', 'true'); s.innerHTML = ICON[name]
  return s
}

// ── Pfps ────────────────────────────────────────────────────────────────────────
// The prototype's stand-ins for the avatars the roster sync keeps: Discord's default avatars
// for some, small seeded pictures of planets for the rest.
const CLYDE = 'M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z'
const DEFAULTS = ['#5865f2', '#3ba55c', '#faa61a', '#ed4245', '#eb459f']
const SKIES = [['#123039', '#050d11'], ['#35211a', '#110907'], ['#1b2818', '#080d07'], ['#1d2531', '#090c12'], ['#382a14', '#120c05']]
const PLANETS = [['#e2c29c', '#6b4a2e'], ['#a9cbd6', '#2d4c57'], ['#c3cca4', '#4b5634'], ['#d7aea6', '#5c3431'], ['#d9d0b4', '#57503a']]
const hashStr = (s) => { let x = 2166136261; for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619) } return x >>> 0 }
function rng(seed) {
  let t = seed
  return () => { t = (t + 0x6d2b79f5) | 0; let x = Math.imul(t ^ (t >>> 15), 1 | t); x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296 }
}
const PEOPLE = { juniper: 'art', okapi: 'default', bram: 'art', tess: 'default', nightjar: 'art', pocket: 'default', kestrel: 'art', wren: 'art', vel: 'art', milo: 'default' }
const AV = new Map()
function avatarFor(name) {
  if (AV.has(name)) return AV.get(name)
  const c = document.createElement('canvas'); c.width = c.height = 64
  const g = c.getContext('2d'), R = rng(hashStr(name + '·hp'))
  if (name === '·gone') {
    g.fillStyle = '#26262a'; g.fillRect(0, 0, 64, 64)
    g.fillStyle = '#3a3a40'; g.beginPath(); g.arc(32, 25, 10, 0, Math.PI * 2); g.fill()
    g.beginPath(); g.ellipse(32, 56, 19, 14, 0, Math.PI, 0); g.fill()
  } else if (PEOPLE[name] === 'default') {
    g.fillStyle = DEFAULTS[hashStr(name) % DEFAULTS.length]; g.fillRect(0, 0, 64, 64)
    g.save(); g.translate(14, 18.3); g.scale(36 / 127.14, 36 / 127.14); g.fillStyle = '#fff'; g.fill(new Path2D(CLYDE)); g.restore()
  } else {
    const [a, b] = SKIES[Math.floor(R() * SKIES.length)]
    const sky = g.createLinearGradient(0, 0, 0, 64); sky.addColorStop(0, a); sky.addColorStop(1, b)
    g.fillStyle = sky; g.fillRect(0, 0, 64, 64)
    for (let i = 0; i < 10; i++) { g.fillStyle = `rgba(255,255,255,${0.25 + 0.55 * R()})`; g.fillRect(R() * 64, R() * 64, 1, 1) }
    const px = 18 + R() * 28, py = 22 + R() * 26, pr = 11 + R() * 13
    const [lit, dark] = PLANETS[Math.floor(R() * PLANETS.length)]
    const pl = g.createRadialGradient(px - pr * 0.45, py - pr * 0.45, pr * 0.1, px, py, pr)
    pl.addColorStop(0, lit); pl.addColorStop(1, dark)
    g.fillStyle = pl; g.beginPath(); g.arc(px, py, pr, 0, Math.PI * 2); g.fill()
    if (R() > 0.45) {
      g.strokeStyle = 'rgba(255,255,255,0.32)'; g.lineWidth = 1.4
      g.beginPath(); g.ellipse(px, py, pr * 1.75, pr * 0.42, -0.35, 0, Math.PI * 2); g.stroke()
    }
  }
  const url = c.toDataURL('image/png')
  AV.set(name, url)
  return url
}

// Which of Olisar's picks plays: its choice, or a runner-up with &pick=. Anyone who has left the
// server shows as "Someone" with a blank face.
const settings = { pick: 0 }
const gone = (n) => n === 'wren'
const DISPLAY = {}
const nm = (n) => (n === 'Olisar' ? 'Olisar' : gone(n) ? 'Someone' : DISPLAY[n] || n)
const who = (n) => `<bdi>${esc(nm(n))}</bdi>`
const av = (n) => (n === 'Olisar' ? ASSETS.bot : avatarFor(gone(n) ? '·gone' : n))
const img = (src, size, cls = 'mem-av') => h('img', { class: cls, src, alt: '', width: size, height: size, style: { width: size + 'px', height: size + 'px' } })

// ── The example server ──────────────────────────────────────────────────────────
const CHAPTERS = [
  { title: 'Before I was here' },
  { title: 'Everyone else' },
  { title: 'The longest night' },
  { get title() { return cap(pick().title) } },
  { title: 'Then I showed up' },
  { title: 'Now' },
]
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1)

// ── Layers ─────────────────────────────────────────────────────────────────────
const body = document.body
const canvas = $('#form'), fxCanvas = $('#fx')
const form = createForm(canvas, { particles: 60000 })
if (!form) body.classList.add('no-form')
const orbCss = $('.orb-css')
let W = innerWidth, H = innerHeight
const wide = () => W > 900

// ── The camera: where the form sits ─────────────────────────────────────────────
const remTop = () => H * 0.38 + Math.min(H * 0.23, W * 0.22) * 0.74 + Math.max(56, H * 0.09)
const CAM = {
  remember: () => ({ cx: W * 0.5, cy: H * 0.38, r: Math.min(H * 0.23, W * 0.22), x0: 0, x1: 0, fy: H * 0.1, yb: remTop() - 18 }),
  stage: (k = 1) => wide()
    ? { cx: W * 0.64, cy: H * 0.47, r: H * 0.27 * k, x0: 0, x1: 0, fy: H * 0.06 }
    : { cx: W * 0.5, cy: H * 0.28, r: H * 0.17 * k, x0: 0, x1: 0, fy: H * 0.04 },
  corner: (k = 1) => wide()
    ? { cx: W * 0.86, cy: H * 0.24, r: H * 0.15 * k, x0: 0, x1: 0, fy: H * 0.04 }
    : { cx: W * 0.82, cy: H * 0.12, r: H * 0.08 * k, x0: 0, x1: 0, fy: 0 },
  end: () => ({ cx: W * 0.5, cy: H * 0.48, r: Math.min(H * 0.21, W * 0.2), x0: 0, x1: 0, fy: H * 0.04 }),
}
let camFn = CAM.remember
let camMod = 1
const cam = { ...CAM.remember() }
const camV = { cx: 0, cy: 0, r: 0 }
form?.framing(() => cam)
const sparkEl = $('.spark')
let spark = 0
function stepSpark(dt) {
  spark += (sparkT - spark) * (1 - Math.exp(-dt * 2.2))
  const d = 26 + 70 * spark
  sparkEl.style.opacity = (spark * 0.9).toFixed(3)
  sparkEl.style.transform = `translate(${cam.cx - d / 2}px, ${cam.cy - d / 2}px)`
  sparkEl.style.width = sparkEl.style.height = d + 'px'
}
function stepCam(dt) {
  let t = camFn()
  if (openId) { const sp = openSpot(); t = { ...t, cx: sp.x, cy: sp.y, r: t.r * 0.4 } }
  const w0 = reduce.matches ? 1.6 : 2.6
  for (const k of ['cx', 'cy', 'r']) {
    const goal = k === 'r' ? t.r * camMod : t[k]
    camV[k] += (-2 * w0 * camV[k] - w0 * w0 * (cam[k] - goal)) * dt
    cam[k] += camV[k] * dt
  }
  const e = 1 - Math.exp(-dt * 3)
  cam.x0 += (t.x0 - cam.x0) * e; cam.x1 += (t.x1 - cam.x1) * e; cam.fy += (t.fy - cam.fy) * e
  cam.yb = lerp(cam.yb || H, t.yb || H, e)
  if (!t.yb && Math.abs(cam.yb - H) < 2) cam.yb = 0
  if (orbCss) { const d = cam.r * 1.48 * formScale; Object.assign(orbCss.style, { left: cam.cx + 'px', top: cam.cy + 'px', width: d + 'px', height: d + 'px' }) }
}

// The form's own settings, by beat. formScale is the whole body's size, for anything placed
// on it (the question at the end, the ring without WebGL2).
let formScale = 1
let sparkT = 0
const calm = (shape, v, e) => (shape === SHAPE.whole && (v || 1) < 1 ? Math.min(e, (v || 1) * 1.2 - 1) : e)
function applyForm(f) {
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

// ── Memories ───────────────────────────────────────────────────────────────────
// Each is a sphere of the form's dust, drawn by the engine, with its words in a box the page
// lays over it. A memory buds out of the form when it's wanted and its dust flows back when
// it isn't; the slot it draws in stays its own meanwhile.
const sats = new Array(NSAT).fill(null)
form?.satellites(sats)
const memsEl = $('.mems')
const spheres = new Map()
let openId = null

function sphereWords(spec) {
  switch (spec.kind) {
    case 'member': return [h('div', { class: 'mem-who' }, img(av(spec.who), 34), h('bdi', {}, nm(spec.who))), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'left': return [h('div', { class: 'mem-who' }, img(av('·gone'), 34), h('span', {}, 'Someone')), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'faces': return [h('div', { class: 'mem-faces' }, spec.faces.map((n) => img(av(n), 22))), h('div', { class: 'mem-text' }, spec.text), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'quote': return [h('div', { class: 'mem-who' }, img(av(spec.who), 18), h('bdi', {}, nm(spec.who))), h('div', { class: 'mem-text', dir: 'auto' }, spec.text), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'term': return [h('div', { class: 'mem-term' + (spec.long ? ' long' : ''), dir: 'auto' }, spec.term), spec.text ? h('div', { class: 'mem-text' }, spec.text) : null, h('div', { class: 'mem-foot' }, spec.foot)]
    case 'count': return [h('div', { class: 'mem-faces' }, spec.faces.map((n) => img(av(n), 20))), h('div', { class: 'mem-big num' }, spec.big), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'name': return [h('div', { class: 'mem-was' }, spec.text), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'answer': return [h('div', { class: 'mem-text', dir: 'auto' }, `“${spec.text}”`), h('div', { class: 'mem-foot' }, spec.foot)]
    case 'blur': return [h('div', { class: 'mem-faces' }, spec.faces.map((n) => img(av(n), 18))), h('div', { class: 'mem-bar', style: { width: '78%' } }), h('div', { class: 'mem-bar', style: { width: '52%' } }), h('div', { class: 'mem-foot' }, spec.foot)]
  }
  return []
}

function makeSphere(id, spec) {
  const inner = h('div', { class: 'mem-in' }, sphereWords(spec).filter(Boolean))
  const mask = h('div', { class: 'mem-mask' }, inner)
  const cap = h('div', { class: 'mem-cap', 'aria-hidden': 'true' }, spec.more || '')
  const btn = h('button', { class: 'mem-btn', type: 'button', 'aria-label': spec.label || id, tabindex: spec.open || spec.onClick ? 0 : -1 })
  const node = h('li', { class: `mem mem-${spec.kind}${spec.open || spec.onClick ? '' : ' inert'}` }, btn, mask, cap)
  memsEl.append(node)
  const S = {
    id, spec, node, btn, mask, inner, cap, slot: -1, seed: (hashStr(id) % 1000) / 1000,
    x: 0, y: 0, r: 0, vx: 0, vy: 0, vr: 0, tx: 0, ty: 0, tr: 0, base: 0,
    act: 0, tact: 1, bright: 1, tbright: 1, peek: 0, peekT: 0, ripple: 0, at: 0, placed: false,
  }
  const live = () => !!(S.spec.open || S.spec.onClick)
  const peek = (on) => { if (live()) S.peekT = on ? 1 : 0; node.toggleAttribute('data-peek', on && live()); if (on && live()) S.ripple = Math.max(S.ripple, 0.6) }
  btn.addEventListener('pointerenter', () => peek(true))
  btn.addEventListener('pointerleave', () => peek(false))
  btn.addEventListener('focus', () => peek(true))
  btn.addEventListener('blur', () => peek(false))
  btn.addEventListener('click', (e) => {
    e.stopPropagation()
    if (S.spec.open) openSphere(id)
    else if (S.spec.onClick) S.spec.onClick(S)
  })
  S.key = specKey(spec)
  return S
}

// A memory asked for again with different words (goose's "Open" becoming "Again") keeps its
// sphere and changes only what's set in it.
const specKey = (s) => [s.kind, s.who && nm(s.who), s.text, s.term, s.long, s.big, s.foot, s.more, s.faces && s.faces.map(nm).join(), !!s.open, !!s.onClick].join('|')
function respec(S, spec) {
  const k = specKey(spec)
  S.spec = spec
  if (k === S.key) return
  S.key = k
  const can = !!(spec.open || spec.onClick)
  S.inner.replaceChildren(...sphereWords(spec).filter(Boolean))
  S.cap.textContent = spec.more || ''
  S.node.className = `mem mem-${spec.kind}${can ? '' : ' inert'}`
  S.btn.tabIndex = can ? 0 : -1
  S.btn.setAttribute('aria-label', spec.label || S.id)
}

// Ask for exactly these memories, where they should sit now. Anything not asked for goes back
// into the form.
function setSpheres(list, { stagger = 0.16 } = {}) {
  const want = new Set()
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
function stepSpheres(dt) {
  const now = performance.now() / 1000
  // Opening or closing a memory moves at about twice the speed of everything else.
  openFast = openId ? 1 : Math.max(0, openFast - dt)
  const w0 = (reduce.matches ? 2.4 : 4.2) * (openFast > 0 ? 1.9 : 1)
  for (const S of [...spheres.values()]) {
    // A slot to draw in, from the first one free.
    if (S.slot < 0 && S.tact > 0) { const f = sats.indexOf(null); if (f >= 0) { S.slot = f; sats[f] = {} } }
    const live = S.tact > 0 && now >= S.at
    const rate = reduce.matches ? 0.6 : 0.95
    S.act = live ? Math.min(1, S.act + dt * rate) : S.tact === 0 ? Math.max(0, S.act - dt * rate * 1.25) : S.act
    if (S.tact === 0 && S.act <= 0) {
      S.node.remove(); if (S.slot >= 0) sats[S.slot] = null; spheres.delete(S.id)
      continue
    }
    const open = openId === S.id
    const others = openId && !open
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
      const o = sats[S.slot]
      o.x = S.x; o.y = S.y; o.r = r; o.act = S.act; o.bright = S.bright * (1 + 0.35 * S.peek); o.peek = S.peek; o.ripple = S.ripple; o.seed = S.seed
    }
    const B = S.box || 140
    S.node.style.transform = `translate(${S.x - B}px, ${S.y - B}px)`
    S.node.style.setProperty('--r', r + 'px')
    S.node.style.setProperty('--sv', clamp(r / Math.max(1, S.base), 0.6, 1.15).toFixed(3))
    S.node.style.visibility = S.act > 0.05 ? 'visible' : 'hidden'
    const words = open ? 0 : smooth(clamp((S.act - 0.55) / 0.45, 0, 1)) * (others ? 0.4 : 1)
    S.mask.style.opacity = words.toFixed(3)
  }
}

// ── Opening a memory ─────────────────────────────────────────────────────────────
const openEl = $('.mem-open')
const openIn = $('.mo-in', openEl)
const openBack = $('.mem-back', openEl)
openBack.append(icon('back', 18))
let openReturn = null, openShown = 0
const openSpot = () => wide()
  ? { x: W * 0.5, y: H * 0.47, r: Math.min(H * 0.37, W * 0.27, 330) }
  : { x: W * 0.5, y: H * 0.45, r: Math.min(W * 0.46, H * 0.36) }
function openSphere(id) {
  const S = spheres.get(id)
  if (!S || !S.spec.open) return
  openId = id
  openReturn = S.btn
  S.node.setAttribute('data-open', '')
  openIn.replaceChildren(...[S.spec.open()].flat())
  openIn.scrollTop = 0
  openEl.style.visibility = 'visible'
  openShown = 0
  form?.mood({ dim: 0.08 })
  narrEl.classList.add('dim')
  centerEl.classList.add('away')
  hint(null)
  form?.pulse(0.4)
}
function closeSphere(quiet) {
  if (!openId) return
  const S = spheres.get(openId)
  S?.node.removeAttribute('data-open')
  openId = null
  narrEl.classList.remove('dim')
  centerEl.classList.remove('away')
  if (beat()) applyForm(beat().form)
  if (!quiet && openReturn && document.contains(openReturn)) openReturn.focus({ preventScroll: true })
  openReturn = null
}
openBack.addEventListener('click', (e) => { e.stopPropagation(); closeSphere() })
function stepOpen(dt) {
  const S = openId && spheres.get(openId)
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

// ── Dust thrown across the window ───────────────────────────────────────────────
// For what the form takes in (people arriving) or gives out (a word spreading). Drawn on a 2D
// layer over the form, in the form's ink.
const fx = fxCanvas.getContext('2d')
let fxDpr = 1
const grains = []
function sizeFx() {
  fxDpr = Math.min(devicePixelRatio || 1, 2)
  fxCanvas.width = Math.round(W * fxDpr); fxCanvas.height = Math.round(H * fxDpr)
}
function grain(o) {
  const slow = reduce.matches ? 2.6 : 1
  const dx = o.x1 - o.x0, dy = o.y1 - o.y0
  const bend = o.bend ?? (Math.random() - 0.5) * 0.5
  grains.push({
    ...o, t: -(o.delay || 0) * slow, dur: (o.dur || 1.4) * slow,
    mx: (o.x0 + o.x1) / 2 - dy * bend, my: (o.y0 + o.y1) / 2 + dx * bend,
    size: o.size || 0.7 + Math.random() * 0.9, acc: o.acc ?? Math.random() < 0.14,
  })
}
const sstep = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t) }
const motes = []
function stepFx(dt) {
  fx.setTransform(fxDpr, 0, 0, fxDpr, 0, 0)
  fx.clearRect(0, 0, W, H)
  if (!grains.length && !motes.length) return
  fx.globalCompositeOperation = 'lighter'
  const nr = stage === 'replay' && wide() ? Math.max(56, W * 0.07) + Math.min(440, W * 0.34) : -1e9
  const nt = stage === 'replay' && !wide() ? narrEl.getBoundingClientRect().top : 1e9
  const keep = (x, y) => sstep(58, 112, y) * (1 - sstep(H - 118, H - 66, y)) * sstep(nr + 10, nr + 90, x) * (1 - sstep(nt - 70, nt - 12, y))
  const now = performance.now() / 1000
  for (const m of motes) {
    const a = m.a * (0.55 + 0.45 * Math.sin(now * m.w + m.ph)) * Math.min(1, (now - m.born) * 1.5)
    fx.fillStyle = m.acc ? `rgba(91,156,246,${a})` : `rgba(170,170,180,${a})`
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
      fx.fillStyle = g.acc ? `rgba(91,156,246,${aa})` : `rgba(170,170,180,${aa})`
      fx.beginPath(); fx.arc(x, y, g.size * (1 - k * 0.18), 0, Math.PI * 2); fx.fill()
    }
  }
}

// ── Narration ──────────────────────────────────────────────────────────────────
// Four slots beside the form. A slot whose words don't change between beats stays put; one
// that does fades out as the new words come in, so only what changed plays.
const narrEl = $('.narr')
const SLOTS = ['title', 'body', 'extra', 'foot']
const slotEl = Object.fromEntries(SLOTS.map((s) => [s, $(`.n-${s}`, narrEl)]))
const slotKey = {}
const fitSlot = (el) => {
  const node = [...el.children].reverse().find((c) => !c.classList.contains('leave'))
  el.style.height = (node ? node.offsetHeight : 0) + 'px'
}
const slotSize = new ResizeObserver((es) => { for (const e of es) if (e.target.parentElement) fitSlot(e.target.parentElement) })
function narrate(n = {}) {
  let i = 0
  for (const s of SLOTS) {
    const v = n[s]
    const key = v == null ? null : n[s + 'Key'] ?? (typeof v === 'string' ? v : Math.random())
    if (slotKey[s] === key) continue
    slotKey[s] = key
    const el = slotEl[s]
    if (!el.style.height) { el.style.height = el.offsetHeight + 'px'; el.offsetHeight }
    for (const c of [...el.children]) { if (c.classList.contains('leave')) continue; c.classList.add('leave'); slotSize.unobserve(c); setTimeout(() => c.remove(), 260) }
    let node = null
    if (v != null) {
      node = typeof v === 'string' ? h('div', { html: v }) : v
      node.classList.add('enter')
      el.append(node)
      slotSize.observe(node)
    }
    fitSlot(el)
    if (!node) continue
    const d = 140 + i * 110
    i++
    setTimeout(() => requestAnimationFrame(() => node.classList.remove('enter')), d)
  }
}

// Messages, as the replay quotes them: in Olisar's palette (it's Olisar speaking), typed in
// one after another.
const QUOTE_MAX = 220
const clip = (t) => (t.length > QUOTE_MAX ? t.slice(0, QUOTE_MAX - 8).replace(/\s+\S*$/, '') + '…' : t)
function quote(msgs, head) {
  const rows = msgs.map((m) => {
    const text = clip(m.text)
    const typed = h('span', { 'aria-hidden': 'true' })
    const p = h('p', { dir: 'auto' }, h('span', { class: 'visually-hidden' }, text), h('span', { class: 'ghost', 'aria-hidden': 'true' }, text, h('span', { class: 'caret' })), typed)
    const row = h('div', { class: 'qmsg' + (m.who === 'Olisar' ? ' bot' : '') },
      h('img', { src: av(m.who), alt: '' }),
      h('div', {}, h('b', {}, h('bdi', {}, nm(m.who)), m.time ? h('time', {}, m.time) : null), p))
    row.style.opacity = '0'
    row.style.transition = 'opacity .35s var(--ease-out)'
    return { row, typed, text }
  })
  const node = h('div', { class: 'quote' }, head ? h('div', { class: 'qhead' }, head) : null, rows.map((r) => r.row))
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

// ── Timers that belong to the beat on screen ────────────────────────────────────
let timers = []
function later(ms, fn) { const t = setTimeout(fn, ms); timers.push(t); return t }
function clearTimers() { timers.forEach(clearTimeout); timers = [] }

// ── Hint and toast ─────────────────────────────────────────────────────────────
const hintEl = $('.hint')
const coarse = matchMedia('(pointer: coarse)')
let hintT = 0
function hint(html, secs = 7) {
  clearTimeout(hintT)
  if (!html) { hintEl.classList.remove('on'); return }
  hintEl.innerHTML = html
  hintEl.classList.add('on')
  hintT = setTimeout(() => hintEl.classList.remove('on'), secs * 1000)
}
const toastEl = $('.toast')
let toastT = 0
function toast(text) {
  toastEl.textContent = text
  toastEl.classList.add('on')
  clearTimeout(toastT)
  toastT = setTimeout(() => toastEl.classList.remove('on'), 2600)
}
const discordLink = () => h('a', { class: 'mo-link', href: '#', onclick: (e) => { e.preventDefault(); e.stopPropagation(); toast('In the app, this opens the message in Discord.') } }, 'Open in Discord')

// ── What the replay shows ───────────────────────────────────────────────────────
// The memories, by what they are. Each one's `open` is what it shows opened.
const msgRows = (msgs) => h('div', { class: 'mo-chat' }, msgs.map((m) => m.gap
  ? h('div', { class: 'mo-gap' }, m.gap)
  : h('div', { class: 'mo-msg' + (m.who === 'Olisar' ? ' bot' : '') }, img(av(m.who), 24),
      h('div', {}, h('b', {}, h('bdi', {}, nm(m.who)), m.time ? h('time', {}, m.time) : null), h('p', { dir: 'auto' }, m.text)))))

const FOUNDERS = [
  { who: 'juniper', day: 'Mar 14', said: 'ok it exists. who’s in', time: '9:12 p.m.', count: 41233 },
  { who: 'okapi', day: 'Mar 14', said: 'me. what is it', time: '9:14 p.m.', count: 28907 },
  { who: 'bram', day: 'Mar 15', said: 'juniper sent me. i was promised snacks', time: '7:40 p.m.', count: 33410 },
  { who: 'tess', day: 'Mar 17', said: 'hi!! is this the book club or the other thing', time: '6:02 p.m.', count: 19882 },
  { who: 'wren', day: 'Mar 19', gone: true },
]
const founderSpec = (f) => f.gone || gone(f.who)
  ? { kind: 'left', foot: f.gone ? 'Left in 2022' : f.day, label: 'Someone' }
  : {
      kind: 'member', who: f.who, foot: f.day, more: `“${f.said}”`, label: `${f.who}, joined ${f.day}`,
      open: () => [
        h('div', { class: 'mo-head' }, `#general · March ${f.day.slice(4)}, 2021`),
        img(av(f.who), 56, 'mem-av mo-av'),
        h('div', { class: 'mo-name' }, h('bdi', {}, nm(f.who))),
        msgRows([{ who: f.who, time: f.time, text: f.said }]),
        h('div', { class: 'mo-meta' }, `First words. Still here, ${fmt(f.count)} messages later.`),
      ],
    }

const NIGHT = [
  { who: 'bram', time: '11:42 p.m.', text: 'power’s out. whole street. phone at 31%' },
  { who: 'tess', time: '11:43 p.m.', text: 'do you have candles' },
  { who: 'bram', time: '11:45 p.m.', text: 'i have one candle and it smells like “ocean breeze”' },
  { who: 'okapi', time: '11:45 p.m.', text: 'that’s a crime but it counts' },
  { gap: '2 hours and 611 messages later' },
  { who: 'nightjar', time: '2:10 a.m.', text: 'still up?' },
  { who: 'bram', time: '2:11 a.m.', text: '8%. the candle is winning' },
  { who: 'pocket', time: '2:13 a.m.', text: 'ordering you a power bank. check your porch tuesday' },
  { gap: '4 hours later' },
  { who: 'bram', time: '6:03 a.m.', text: 'lights are back. ocean breeze lives on' },
]
const nightSpec = () => ({
  kind: 'faces', faces: ['bram', 'tess', 'okapi', 'nightjar', 'pocket'], text: `${nm('bram')}’s power went out`,
  foot: '1,912 messages', more: 'Open', label: 'The longest night, February 3, 2023',
  open: () => [h('div', { class: 'mo-head' }, '#general · February 3 to 4, 2023'), msgRows(NIGHT), discordLink()],
})

// Olisar's pick: the one thing that most belongs to this server, chosen while remembering
// (see PICK.md). &pick= plays a runner-up instead; each needs only these fields to play.
const PICKS = [
  {
    title: 'goose', quote: true, kind: 'A word', meaning: 'A win nobody saw coming', when: '2022 to now',
    origin: { who: 'okapi', where: '#games', time: 'May 8, 2022', text: 'that was the most goose thing that’s ever happened' },
    intro: () => `Here, goose means a win nobody saw coming. ${who('okapi')} said it first.`,
    people: 212, count: 1204, unit: 'times', spread: (c) => [h('b', {}, '212 of you'), ' have said it since. ', c, ' times, in 17 channels.'],
    why: 'Used by 212 people in 17 channels for four years.',
    spots: [
      { where: '#games · May 2022', said: 'most goose thing ever' }, { where: '#general · Jun 2022', said: 'absolute goose' },
      { where: '#memes · Aug 2022' }, { where: '#late-night · Jan 2023', said: 'goose energy tonight' }, { where: '#music · Mar 2023' },
      { where: '#general · Feb 2024', said: 'that’s so goose' }, { where: '#game-night · Nov 2024' }, { where: '#art · Apr 2025', said: 'goose of the week' },
      { where: '#general · Sep 2026', said: 'goose.' }, { where: '#off-topic · Dec 2023' },
    ],
  },
  {
    title: 'Friday movie night', kind: 'A ritual', meaning: 'Every Friday at nine, since June 2021', when: '2021 to now',
    origin: { who: 'tess', where: '#movie-night', time: 'June 4, 2021', text: 'ok. friday, 9pm, we watch something terrible together. bring snacks' },
    intro: () => `Every Friday at nine since June 2021. ${who('tess')} started it, and it has started late ever since.`,
    people: 61, count: 198, unit: 'Fridays', spread: (c) => [h('b', {}, '61 of you'), ' have come at least once. ', c, ' Fridays so far.'],
    why: '198 Fridays in a row, with 61 people, started by one message.',
    spots: [
      { where: 'Fri Jun 4, 2021', said: 'starting in 5' }, { where: 'Fri Oct 29, 2021', said: 'it’s 9:20. starting in 5' },
      { where: 'Fri Dec 24, 2021', said: 'christmas eve and we’re still here' }, { where: 'Fri Apr 8, 2022', said: 'who picked this' },
      { where: 'Fri Jan 6, 2023' }, { where: 'Fri Jul 14, 2023', said: 'paddington 2 again. no complaints' }, { where: 'Fri Mar 1, 2024' },
      { where: 'Fri Nov 15, 2024', said: '40 minutes late, a record' }, { where: 'Fri Jun 6, 2025' }, { where: 'Fri Oct 2, 2026', said: 'starting in 5' },
    ],
  },
  {
    title: 'the lighthouse', kind: 'A build', meaning: 'A Minecraft build fourteen people kept adding to', when: '2022 to 2024',
    origin: { who: 'bram', where: '#minecraft', time: 'September 12, 2022', text: 'started a lighthouse on the north cliff. add whatever' },
    intro: () => `A Minecraft build on the north cliff. ${who('bram')} laid the first block and said to add whatever. You did.`,
    people: 14, count: 640, unit: 'messages', spread: (c) => [h('b', {}, '14 of you'), ' built on it for two years. ', c, ' messages about it.'],
    why: 'Fourteen builders over two years, from one message.',
    spots: [
      { where: '#minecraft · Sep 2022', said: 'add whatever' }, { where: '#minecraft · Nov 2022', said: 'it has a basement now' },
      { where: '#screenshots · Jan 2023' }, { where: '#general · Feb 2023', said: 'why does the lighthouse have a moat' },
      { where: '#minecraft · Apr 2023' }, { where: '#minecraft · Jun 2023', said: 'library on floor 3' }, { where: '#screenshots · Oct 2023' },
      { where: '#minecraft · Feb 2024', said: 'the light works now' }, { where: '#minecraft · Dec 2024', said: 'last block. it’s done' },
      { where: '#general · May 2024', said: 'someone put a cow on the roof' },
    ],
  },
]
const pick = () => PICKS[settings.pick]
const pickTitle = () => (pick().quote ? `“${pick().title}”` : cap(pick().title))
const pickSpec = (again, compact) => {
  const P = pick()
  return {
    kind: 'term', term: P.title, long: P.title.length > 10, text: compact ? null : P.meaning, foot: `${fmt(P.count)} ${P.unit}`, more: again ? 'Again' : 'Open', label: `${P.title}, my pick`,
    ...(again ? { onClick: () => spreadPick() } : {
      open: () => [
        h('div', { class: 'mo-head' }, P.kind),
        h('div', { class: 'mo-term' }, P.title),
        h('div', { class: 'mo-text' }, P.meaning + '.'),
        msgRows([{ who: P.origin.who, time: `${P.origin.where} · ${P.origin.time}`, text: P.origin.text }]),
        h('div', { class: 'mo-meta' }, P.why),
        discordLink(),
      ],
    }),
  }
}

const FIRST_REPLY = [
  { who: 'juniper', time: '/ask · 4:31 p.m.', text: 'olisar what’s the wifi password at the cabin' },
  { who: 'Olisar', time: '4:31 p.m.', text: 'I don’t have that, and I’d be a little worried if I did.' },
]
const replySpec = () => ({
  kind: 'quote', who: 'juniper', text: 'I don’t have that, and I’d be a little worried if I did.', foot: 'Aug 19, 2025', more: 'Open', label: 'My first reply',
  open: () => [h('div', { class: 'mo-head' }, '/ask · #general · August 19, 2025'), msgRows(FIRST_REPLY), discordLink()],
})

const PINECONE = [
  { who: 'juniper', time: 'Oct 3', text: 'olisar your name is pinecone now' },
  { who: 'Olisar', time: 'Oct 3', text: 'Done. I’m Pinecone.' },
  { gap: '4 days later' },
  { who: 'juniper', time: 'Oct 7', text: 'ok this was a mistake. change it back' },
  { who: 'Olisar', time: 'Oct 7', text: 'Back to Olisar. Thank you.' },
]
const pineSpec = () => ({
  kind: 'name', text: 'Pinecone', foot: '4 days', more: 'Open', label: 'The four days I was Pinecone',
  open: () => [h('div', { class: 'mo-head' }, '#general · October 3 to 7, 2025'), msgRows(PINECONE), h('div', { class: 'mo-meta' }, `Changed by ${nm('juniper')}, through chat. The activity log kept the old name.`)],
})

const fiveSpec = () => ({
  kind: 'faces', faces: ['juniper', 'okapi', 'bram', 'tess', 'wren'], text: 'The first five', foot: 'Mar 2021', more: 'Open', label: 'The first five',
  open: () => [h('div', { class: 'mo-head' }, '#general · March 14 to 19, 2021'), msgRows(FOUNDERS.filter((f) => !f.gone).map((f) => ({ who: f.who, time: f.day, text: f.said }))), h('div', { class: 'mo-meta' }, 'And one more, who has since left.')],
})
const firstSpec = () => ({
  kind: 'quote', who: 'juniper', text: 'ok it exists. who’s in', foot: 'Mar 14, 2021', more: 'Open', label: 'The first message',
  open: () => [h('div', { class: 'mo-head' }, '#general · March 14, 2021'), msgRows(FIRST_DAY), discordLink()],
})
const peopleSpec = () => ({
  kind: 'count', faces: ['nightjar', 'kestrel', 'pocket'], big: '1,143', foot: 'people', more: 'Open', label: '1,143 people',
  open: () => [
    h('div', { class: 'mo-head' }, 'Since March 14, 2021'),
    h('div', { class: 'mo-term num' }, '1,143'),
    h('div', { class: 'mo-text' }, MILES.map((m) => h('div', { class: 'mo-meta', style: { marginTop: '6px' } }, `${m.when}: ${m.plain()}`))),
  ],
})

const FIRST_DAY = [
  { who: 'juniper', time: '9:12 p.m.', text: 'ok it exists. who’s in' },
  { who: 'okapi', time: '9:14 p.m.', text: 'me. what is it' },
  { who: 'juniper', time: '9:14 p.m.', text: 'no idea yet' },
]

// ?stress swaps in the worst a real server sends: names at Discord's 32-character limit, in
// right-to-left and CJK scripts, emoji, and a message long enough to need cutting.
if (q.has('stress')) {
  Object.assign(DISPLAY, { juniper: 'juniper_the_extremely_online_01', okapi: 'أوكابي', bram: '布拉姆🔥', tess: 'tess 🌙✨ (she/her) · book club', nightjar: 'נייטג׳אר' })
  FIRST_DAY[1].text = 'me. what is it 🤔 also has anyone seen https://example.com/an/extremely/long/link/that/goes/on/and/on?ref=hollowpines because i think it explains everything and nothing at the same time and i have been staring at it for an hour'
  NIGHT[1].text = 'هل لديك شموع؟'
  NIGHT[2].text = '我只有一根蜡烛，闻起来像“海洋微风”'
}

// Arrivals. Months since March 14, 2021, against members who joined by then and are still
// here; the shoutout is the step on July 9, 2022.
const T_END = 52.7
const rawCount = (t) => 5 + 3889 * (Math.exp(t / 290) - 1) + 212 / (1 + Math.exp(-(t - 15.84) * 80))
const countAt = (t) => Math.round(clamp(rawCount(t) * (1000 / rawCount(T_END)), 5, 1000))
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const monthAt = (t) => { const m = 2 + Math.floor(t + 0.45); return `${MONTHS[m % 12]} ${2021 + Math.floor(m / 12)}` }
const MILES = [
  { when: 'Oct 2021', at: (c, t) => c >= 100, html: () => `<b>${who('nightjar')}</b> was the 100th.`, plain: () => `${nm('nightjar')} was the 100th.` },
  { when: 'Jul 9, 2022', at: (c, t) => t >= 15.84, html: () => `Someone posted ${SRV} on r/cozygames. <b>212 people</b> came that day.`, plain: () => '212 people came the day it was posted on r/cozygames.' },
  { when: 'Aug 2025', at: (c, t) => c >= 1000, html: () => `<b>${who('kestrel')}</b> was the 1,000th, two weeks before I got here.`, plain: () => `${nm('kestrel')} was the 1,000th.` },
]

// The pick spreading: where its dust lands, as fractions of the window. What was said there
// comes from the pick. In a narrow window they stack in two staggered columns between the
// sphere and the narration, as many as fit.
const SPOTS = [[0.48, 0.17], [0.68, 0.12], [0.87, 0.52], [0.46, 0.8], [0.77, 0.82], [0.92, 0.7], [0.62, 0.78], [0.53, 0.31], [0.79, 0.64], [0.47, 0.6]]
const SPOTS_NARROW = [[0.28], [0.72], [0.28], [0.72], [0.28], [0.72], [0.28], [0.72], [0.28], [0.72]]
let spreadEl = null, spreadCount = null
function spreadPick() {
  const S = spheres.get('goose'), P = pick()
  if (!S) return
  if (!spreadEl) { spreadEl = h('div', { class: 'spread', 'aria-hidden': 'true' }); body.append(spreadEl) }
  spreadEl.replaceChildren()
  motes.length = 0
  form?.pulse(0.5)
  S.ripple = 1
  let landed = 0
  let at = SPOTS
  if (!wide()) {
    const y0 = S.y + S.r + 34, y1 = narrEl.getBoundingClientRect().top - 26
    const fit = clamp(Math.floor((y1 - y0) / 18) + 1, 2, SPOTS_NARROW.length)
    at = SPOTS_NARROW.slice(0, fit).map(([fx0], i) => [fx0, (y0 + (y1 - y0) * (fit > 1 ? i / (fit - 1) : 0)) / H])
  }
  at.forEach(([fx0, fy0], i) => {
    const x = fx0 * W, y = fy0 * H, what = P.spots[i]
    const spot = h('div', { class: 'spot', style: { left: fx0 * 100 + '%', top: fy0 * 100 + '%' } }, what.said ? h('q', { dir: 'auto' }, what.said) : null, h('span', {}, what.where))
    spreadEl.append(spot)
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
function dustAt(x, y) {
  const born = performance.now() / 1000
  for (let k = 0; k < 9; k++) motes.push({ x: x + (Math.random() - 0.5) * 46, y: y + (Math.random() - 0.3) * 14, size: 0.6 + Math.random() * 0.8, a: 0.25 + Math.random() * 0.35, w: 0.8 + Math.random() * 1.6, ph: Math.random() * 6.28, acc: Math.random() < 0.2, born })
}
function clearSpread() { spreadEl?.replaceChildren(); grains.length = 0; motes.length = 0 }

// ── The beats ──────────────────────────────────────────────────────────────────
// Where each memory sits, relative to where the form is.
const around = (c, n, a0, a1, rx, ry, r, ids, specs) => ids.map((id, i) => {
  const a = ((a0 + (a1 - a0) * (n > 1 ? i / (n - 1) : 0.5)) * Math.PI) / 180
  return { id, spec: specs[i], x: c.cx + Math.cos(a) * rx, y: c.cy + Math.sin(a) * ry, r }
})

let arrivals = null
const BEATS = [
  {
    id: 'cold', ch: 0, auto: 6, cam: () => CAM.stage(),
    form: { shape: SHAPE.whole, v: 0.05, energy: -0.6, mood: { dim: 0.8 }, density: 0.03, feed: 0.0006, spark: 0.6 },
    narr: () => ({ title: 'March 14, 2021', body: `${SRV} started on a Sunday night.` }),
    hint: () => (coarse.matches ? 'Tap anywhere to continue' : 'Click anywhere or press <kbd>→</kbd> to continue'),
  },
  {
    id: 'first', ch: 0, auto: 9, cam: () => CAM.stage(),
    form: { shape: SHAPE.whole, v: 0.085, energy: -0.3, mood: { dim: 0.9 }, density: 0.06, feed: 0.001, spark: 0.55 },
    narr: () => {
      const q = quote(FIRST_DAY)
      later(0, () => { stopQuote = q.play((k) => { const v = 0.085 + 0.035 * k; form?.pulse(0.7); form?.set(SHAPE.whole, v); form?.energy(calm(SHAPE.whole, v, -0.3)); form?.density(0.06 + 0.045 * k); sparkT = 0.55 - 0.12 * k }) })
      return { title: 'March 14, 2021', body: `The first messages in ${SRV}.`, extra: q, extraKey: 'first-day', foot: '<span class="mono">#general</span>', footKey: 'general' }
    },
  },
  {
    id: 'five', ch: 0, auto: 8, cam: () => (wide() ? CAM.stage() : { ...CAM.stage(), cy: H * 0.36 }),
    form: { shape: SHAPE.whole, v: 0.3, energy: 0, mood: { dim: 1 }, density: 1, pulse: 0.6 },
    narr: () => ({ title: 'March 14, 2021', body: 'Five people the first week. Four of them are still here.' }),
    spheres: () => {
      const ids = FOUNDERS.map((f) => 'f-' + f.who), specs = FOUNDERS.map(founderSpec)
      return wide()
        ? around(CAM.stage(), 5, -150, 70, H * 0.34, H * 0.31, 66, ids, specs)
        : around({ cx: W * 0.5, cy: H * 0.36 }, 5, -126, 162, Math.min(W * 0.33, 130), H * 0.165, 46, ids, specs)
    },
    hint: 'Open one to see their first words',
  },
  {
    id: 'arrivals', ch: 1, auto: 5, cam: () => CAM.stage(),
    form: { shape: SHAPE.whole, v: 0.3, energy: 0.1, mood: { dim: 1 } },
    narr: () => {
      arrivals = makeArrivals()
      return { title: arrivals.title, titleKey: 'arrivals', body: 'Then people kept arriving.', extra: arrivals.extra, extraKey: 'arrivals' }
    },
    enter: () => arrivals.run(),
    frame: (dt) => arrivals?.frame(dt),
    leave: () => { arrivals = null; grains.length = 0 },
  },
  {
    id: 'night', ch: 2, auto: 9, cam: () => CAM.corner(),
    form: { shape: SHAPE.whole, v: 1, energy: 0.35, mood: { dim: 0.85 } },
    narr: () => ({ title: 'February 3, 2023', body: `<b>1,912 messages</b> in <span class="mono">#general</span>, the most ${SRV} has sent in one day. Most of them were about a candle.` }),
    spheres: () => [wide() ? { id: 'night', spec: nightSpec(), x: W * 0.6, y: H * 0.56, r: 118 } : { id: 'night', spec: nightSpec(), x: W * 0.5, y: H * 0.32, r: 96 }],
    hint: 'Open it to read that night',
  },
  {
    id: 'goose', ch: 3, auto: 8, cam: () => CAM.corner(0.85),
    form: { shape: SHAPE.whole, v: 1, energy: 0.2, mood: { dim: 0.7 } },
    narr: () => ({ title: pickTitle(), body: pick().intro(), extra: quote([{ who: pick().origin.who, time: pick().origin.time, text: pick().origin.text }], pick().origin.where), extraKey: 'pick-first-' + settings.pick }),
    enter: () => { const q = slotEl.extra.lastElementChild; if (q?.play) stopQuote = q.play() },
    spheres: () => [gooseSphere(false)],
  },
  {
    id: 'spread', ch: 3, auto: 9, cam: () => CAM.corner(0.85),
    form: { shape: SHAPE.whole, v: 1, energy: 0.5, mood: { dim: 0.7 } },
    narr: () => {
      spreadCount = h('span', { class: 'num' }, '0')
      return { title: pickTitle(), body: h('div', {}, pick().spread(spreadCount)), bodyKey: 'spread' }
    },
    enter: () => later(300, spreadPick),
    leave: clearSpread,
    spheres: () => [gooseSphere(true)],
  },
  {
    id: 'arrive', ch: 4, auto: 10, cam: () => CAM.stage(1.06),
    form: { shape: SHAPE.bot, energy: 0.3, mood: { dim: 1 }, pulse: 1, regather: 0.4 },
    narr: () => {
      const q = quote(FIRST_REPLY)
      later(900, () => { stopQuote = q.play((k) => form?.pulse(k === 2 ? 1 : 0.4)) })
      return { title: 'August 19, 2025', body: 'Here’s where I joined.', extra: q, extraKey: 'first-reply', foot: 'My first reply' }
    },
  },
  {
    id: 'pinecone', ch: 4, auto: 8, cam: () => CAM.stage(1.06),
    form: { shape: SHAPE.bot, energy: 0.2, mood: { dim: 1 } },
    narr: () => {
      const a = h('span', {}, 'Olisar'), i1 = h('i', { hidden: true }, '→'), b = h('span', { hidden: true }, 'Pinecone'), i2 = h('i', { hidden: true }, '→'), c = h('span', { hidden: true }, 'Olisar')
      const names = h('div', { class: 'names' }, a, i1, b, i2, c)
      later(900, () => { i1.hidden = false; b.hidden = false; a.classList.add('was'); form?.reject() })
      later(2600, () => { i2.hidden = false; c.hidden = false; b.classList.add('was'); form?.pulse(0.8) })
      return { title: 'October 3, 2025', body: 'You renamed me Pinecone. It lasted four days.', extra: names, extraKey: 'pinecone' }
    },
  },
  {
    id: 'now', ch: 5, auto: 0, cam: () => CAM.end(),
    form: { shape: SHAPE.whole, v: 1, energy: 0, mood: { dim: 0.9 } },
    narr: () => ({}),
    spheres: () => ring(),
    enter: () => center.start(),
    leave: () => center.stop(),
  },
]
let stopQuote = null
const gooseSphere = (again) => wide()
  ? { id: 'goose', spec: pickSpec(again), x: W * 0.62, y: H * 0.5, r: 98 }
  : { id: 'goose', spec: pickSpec(again), x: W * 0.5, y: H * 0.3, r: 80 }

// The end: what the replay showed, back on one ring round the form, the way the final server
// screen keeps its memories.
let answer = null
function ring() {
  const list = [
    { id: 'end-five', spec: fiveSpec() },
    { id: 'end-first', spec: firstSpec() },
    { id: 'end-people', spec: peopleSpec() },
    { id: 'end-night', spec: nightSpec() },
    { id: 'end-goose', spec: pickSpec(false, true) },
    { id: 'end-reply', spec: replySpec() },
    { id: 'end-pine', spec: pineSpec() },
  ]
  if (answer) list.push({ id: 'end-answer', spec: { kind: 'answer', text: answer, foot: 'Just now', label: 'Your answer', more: 'Open', open: () => [h('div', { class: 'mo-head' }, 'Saved for next year’s replay'), h('div', { class: 'mo-term', style: { fontSize: '22px' } }, `“${answer}”`)] } })
  const c = CAM.end()
  const n = list.length
  if (!wide()) {
    const top = Math.ceil(n / 2), box = 72
    const r = Math.min(46, (W - 24 - (top - 1) * 8) / (top * 2))
    const y1 = clamp((112 + c.cy - box) / 2, 112 + r, c.cy - box - r)
    const y2 = clamp((c.cy + box + H - 70) / 2, c.cy + box + r, H - 70 - r)
    const row = (k, count, y, dir) => {
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
const centerEl = $('.center')
const faceEl = $('.orb-face')
faceEl.append(h('img', { src: ASSETS.bot, alt: '' }))
const center = (() => {
  const line = $('.center-line', centerEl), sub = $('.center-sub', centerEl), fm = $('form', centerEl), input = $('input', centerEl), acts = $('.actions', centerEl)
  let on = false
  fm.addEventListener('submit', (e) => {
    e.preventDefault()
    const v = input.value.replace(/\s+/g, ' ').trim()
    if (!v) { input.focus(); return }
    answer = v
    input.value = ''
    setSpheres(ring())
    form?.pulse(1)
    swap('Saved. See you next year.', 'It’ll be in next year’s replay.', 'done')
  })
  function swap(l, s, mode) {
    centerEl.classList.remove('on')
    later(320, () => {
      line.textContent = l
      sub.textContent = s || ''
      sub.hidden = !s
      fm.hidden = mode !== 'ask'
      acts.hidden = mode !== 'done'
      centerEl.classList.add('on')
      if (mode === 'ask' && !coarse.matches) input.focus({ preventScroll: true })
    })
  }
  return {
    start() {
      on = true
      faceEl.classList.add('on')
      if (answer) { swap('Saved. See you next year.', '', 'done'); return }
      swap('1,143 of you now.', '', 'count')
      later(2900, () => swap('What should I remember from this year?', '', 'ask'))
    },
    stop() { on = false; centerEl.classList.remove('on'); faceEl.classList.remove('on') },
    frame() {
      if (!on) return
      centerEl.style.left = cam.cx + 'px'; centerEl.style.top = cam.cy + 'px'
      const d = cam.r * 1.48
      Object.assign(faceEl.style, { left: cam.cx - d / 2 + 'px', top: cam.cy - d / 2 + 'px', width: d + 'px', height: d + 'px' })
    },
  }
})()
$('.watch-again', centerEl).addEventListener('click', (e) => { e.stopPropagation(); show(0) })
$('.done', centerEl).addEventListener('click', (e) => { e.stopPropagation(); go('play') })
centerEl.addEventListener('click', (e) => e.stopPropagation())

// Arrivals: the count, a scrubber through the years, and dust falling into the form for
// everyone who came.
function makeArrivals() {
  let p = 0, playing = false, lastCount = 5, debt = 0, scrubbing = false
  const num = h('span', { class: 'num' }, '5')
  const when = h('span', { class: 'sub mono' }, 'Mar 2021')
  const title = h('div', {}, h('span', { 'aria-hidden': 'true' }, num, ' people', when), h('span', { class: 'visually-hidden' }, 'From 5 people in March 2021 to 1,143 now'))
  const input = h('input', { type: 'range', min: 0, max: 1000, value: 0, step: 1, 'aria-label': 'Scrub through the years', 'aria-valuetext': 'March 2021, 5 people' })
  const fill = h('div', { class: 'track-fill' })
  const knob = h('div', { class: 'track-knob' })
  const marks = MILES.map((m, i) => h('div', { class: 'track-mark', style: { left: (i === 0 ? 7 / T_END : i === 1 ? 15.84 / T_END : 1) * 100 + '%' } }))
  const btn = h('button', { type: 'button', class: 'icon sm ghost', 'aria-label': 'Pause', 'data-tip': 'Pause', 'data-tip-side': 'up' }, icon('pause', 14))
  const ticks = h('div', { class: 'ticks', 'aria-hidden': 'true' }, [2022, 2023, 2024, 2025].map((y) => h('span', { style: { left: `calc(${((y - 2021) * 12 - 2.45) / T_END * 100}%)` } }, String(y))))
  const miles = MILES.map((m) => h('div', { class: 'mile', hidden: true }, h('time', {}, m.when), h('span', { html: m.html() })))
  const extra = h('div', { class: 'live-ctl' },
    h('div', { class: 'scrub' }, btn, h('div', { class: 'track' }, input, h('div', { class: 'track-line' }, fill), marks, knob)),
    ticks, h('div', { class: 'miles' }, miles))
  const setIcon = () => {
    btn.replaceChildren(icon(playing ? 'pause' : p >= 1 ? 'again' : 'play', 14))
    const l = playing ? 'Pause' : p >= 1 ? 'Play again' : 'Play'
    btn.setAttribute('aria-label', l); btn.setAttribute('data-tip', l)
  }
  btn.addEventListener('click', (e) => {
    e.stopPropagation()
    if (playing) playing = false
    else { if (p >= 1) { p = 0; lastCount = 5 } playing = true }
    setIcon()
  })
  const endScrub = () => { scrubbing = false; for (const t of ['pointerup', 'pointercancel', 'blur']) removeEventListener(t, endScrub) }
  input.addEventListener('pointerdown', (e) => {
    e.stopPropagation(); scrubbing = true; playing = false; setIcon()
    for (const t of ['pointerup', 'pointercancel', 'blur']) addEventListener(t, endScrub)
  })
  input.addEventListener('click', (e) => e.stopPropagation())
  input.addEventListener('input', () => { p = input.value / 1000; playing = false; setIcon() })
  input.addEventListener('keydown', (e) => e.stopPropagation())
  const DUR = 11
  return {
    title, extra,
    run() { later(900, () => { playing = true; setIcon() }) },
    holding: () => scrubbing || playing,
    frame(dt) {
      if (playing) { p = Math.min(1, p + dt / (DUR * (reduce.matches ? 1.6 : 1))); if (p >= 1) { playing = false; setIcon() } }
      const t = p * T_END
      const c = countAt(t)
      num.textContent = fmt(c)
      when.textContent = monthAt(t)
      input.value = String(Math.round(p * 1000))
      input.setAttribute('aria-valuetext', `${monthAt(t)}, ${fmt(c)} people`)
      fill.style.transform = `scaleX(${p})`
      knob.style.left = p * 100 + '%'
      MILES.forEach((m, i) => {
        const hit = m.at(c, t)
        if (hit && miles[i].hidden) { miles[i].hidden = false; form?.pulse(0.9) }
        else if (!hit) miles[i].hidden = true
      })
      // Dust for everyone who joined since the last frame, a little at a time.
      if (c > lastCount) debt += c - lastCount
      lastCount = c
      const n = Math.min(debt, reduce.matches ? 6 : 26)
      debt -= n
      const fr = cam.r * 0.74 * formScale
      for (let k = 0; k < (reduce.matches ? Math.ceil(n / 2) : n); k++) {
        const a = Math.random() * Math.PI * 2, R = Math.hypot(W, H) * (0.45 + Math.random() * 0.2)
        const b = Math.random() * Math.PI * 2
        grain({ x0: cam.cx + Math.cos(a) * R, y0: cam.cy + Math.sin(a) * R, x1: cam.cx + Math.cos(b) * fr * 0.85, y1: cam.cy + Math.sin(b) * fr * 0.85, dur: 1.5 + Math.random() * 0.9, delay: Math.random() * 0.15, bend: (Math.random() - 0.5) * 0.7 })
      }
      // The body grows with the people in it.
      const v = 0.3 + 0.7 * Math.sqrt(c / 1000)
      formScale = v
      form?.set(SHAPE.whole, v)
      form?.energy(calm(SHAPE.whole, v, 0.1))
    },
  }
}

// ── The replay: which beats, and moving between them ─────────────────────────────
let beats = [], cur = -1, autoplay = false, autoT = 0
const beat = () => beats[cur]
const timelineEl = $('.timeline')
function buildBeats() {
  beats = BEATS
  const chs = [...new Set(beats.map((b) => b.ch))]
  timelineEl.replaceChildren(...chs.map((ch, i) => {
    const btn = h('button', { type: 'button', class: 'tl', 'data-ch': ch }, h('div', { class: 'tl-line' }, h('div', { class: 'tl-fill' })), h('div', { class: 'tl-name' }, CHAPTERS[ch].title))
    btn.setAttribute('aria-label', `Chapter ${i + 1}: ${CHAPTERS[ch].title}`)
    btn.addEventListener('click', (e) => { e.stopPropagation(); show(beats.findIndex((b) => b.ch === ch)) })
    return btn
  }))
}
function updateTimeline() {
  const b = beat()
  for (const el of timelineEl.children) {
    const ch = +el.dataset.ch
    const inCh = beats.filter((x) => x.ch === ch)
    const idx = inCh.indexOf(b)
    const f = ch < b.ch ? 1 : ch > b.ch ? 0 : (idx + 1) / inCh.length
    el.querySelector('.tl-fill').style.transform = `scaleX(${f})`
    el.classList.toggle('now', ch === b.ch)
    if (ch === b.ch) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current')
  }
}
function show(i) {
  if (i < 0 || i >= beats.length) return
  closeSphere(true)
  const prev = beat()
  stopQuote?.(); stopQuote = null
  clearTimers()
  prev?.leave?.()
  cur = i
  const b = beat()
  camFn = b.cam || (() => CAM.stage())
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
let seenHint = false
const next = () => { if (cur < beats.length - 1) show(cur + 1) }
const prev = () => { if (cur > 0) show(cur - 1) }

// ── The stages ─────────────────────────────────────────────────────────────────
let stage = null
let keyed = false
addEventListener('keydown', () => { keyed = true }, true)
addEventListener('pointerdown', () => { keyed = false }, true)
const startEl = $('.start'), playEl = $('.play'), rememberEl = $('.remember')
function go(next, opts = {}) {
  if (stage === 'replay') { closeSphere(true); stopQuote?.(); clearTimers(); beat()?.leave?.(); cur = -1; narrate({}); setSpheres([]); hint(null); clearSpread() }
  if (stage === 'remember') remembering.stop()
  stage = next
  body.classList.toggle('rp-replay', next === 'replay')
  startEl.classList.toggle('on', next === 'start')
  playEl.classList.toggle('on', next === 'play')
  rememberEl.classList.toggle('on', next === 'remember')
  demoSkip.hidden = next === 'replay'
  camMod = 1
  if (next === 'start') {
    camFn = CAM.remember
    applyForm({ shape: SHAPE.whole, v: 0.92, energy: 0, mood: { dim: 0.9 } })
    setSpheres([])
    if (opts.focus) later(400, () => $('.start-go').focus({ preventScroll: true }))
  } else if (next === 'remember') {
    camFn = CAM.remember
    remembering.start(opts.from)
  } else if (next === 'play') {
    camFn = CAM.remember
    applyForm({ shape: SHAPE.whole, v: 0.92, energy: 0, mood: { dim: 0.8 }, pulse: opts.ready ? 1 : 0 })
    setSpheres([])
    if (keyed) later(450, () => playGo.focus({ preventScroll: true }))
  } else if (next === 'replay') {
    buildBeats()
    show(opts.beat ? Math.max(0, beats.findIndex((b) => b.id === opts.beat)) : 0)
  }
}

// ── The start sheet ────────────────────────────────────────────────────────────
// Under the form, where remembering and Play sit, so the form doesn't move until the replay. It
// reads only what's in the search index, so the Channels tab's indexed setting decides which
// channels it sees.
//
// When a replay can't start cleanly, the sheet says why before anything runs, and Start says
// what it would do. Each is a check the real feature makes; ?state= shows one.
const STATE = q.get('state')
const START = {
  indexing: { meta: () => 'The search index has read 41% of the history so far', go: 'Start anyway', notes: ['A replay now would leave out most of 2021 and 2022. Indexing finishes in about 3 hours.', 'Channels that aren’t indexed are left out.'] },
  quota: { off: true, notes: ['There isn’t enough of today’s Gemini allowance left for this. It resets at midnight Pacific, in about 6 hours.'] },
  short: { meta: () => '86 messages in 3 indexed channels', off: true, notes: ['That isn’t enough for a replay yet. Try again after a month or so of conversation.'] },
  busy: { meta: () => [h('bdi', {}, nm('okapi')), ' started a replay 4 minutes ago'], go: 'Watch it', from: 6.6, notes: ['One replay runs at a time. This one is ready in about 20 minutes.'] },
}
const startGo = $('.start-go', startEl)
const startState = START[STATE]
$('h2', startEl).replaceChildren('Replay ', h('bdi', {}, SERVER))
if (startState) {
  if (startState.meta) $('.meta', startEl).replaceChildren(...[startState.meta()].flat())
  if (startState.go) startGo.textContent = startState.go
  startGo.disabled = !!startState.off
  for (const n of startEl.querySelectorAll('.note')) n.remove()
  startEl.append(...startState.notes.map((t, i) => h('p', { class: 'note', id: i ? null : 'start-why' }, t)))
  startGo.setAttribute('aria-describedby', 'start-why')
}
startGo.addEventListener('click', () => go('remember', { from: startState?.from || 0 }))

// ── Remembering ────────────────────────────────────────────────────────────────
// The real job runs for minutes on a big server and survives the console closing. Here it's
// fourteen seconds: reading, counting, finding moments (each a sphere, kept out of focus so
// nothing's spoiled), and writing the chapters.
const remembering = (() => {
  const steps = [...rememberEl.querySelectorAll('.steps li')]
  const live = $('.live', rememberEl)
  let t = 0, on = false, fired = new Set(), whereT = 0, whereI = 0
  const WHERE = ['#general · 2021', '#games · 2021', '#general · 2022', '#memes · 2022', '#music · 2022', '#late-night · 2023', '#general · 2023', '#art · 2024', '#game-night · 2024', '#general · 2025', '#off-topic · 2026']
  const BLUR = [['bram', 'tess'], ['okapi'], ['juniper', 'okapi', 'bram'], ['nightjar'], ['pocket', 'kestrel'], ['tess', 'vel'], ['milo'], ['juniper']]
  const BLUR_FOOT = ['2023', '2022', '2021', '2024', '2022', '2025', '2023', '2026']
  const N = { msgs: 412880, years: 5, moments: 31, chapters: 6 }
  const at = (s, fn) => { if (t >= s && !fired.has(s)) { fired.add(s); fn() } }
  const status = $('.rem-status', rememberEl)
  const setStep = (k) => {
    steps.forEach((li, i) => { li.className = i < k ? 'done' : i === k ? 'now' : ''; if (i === k) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current') })
    status.textContent = k < steps.length ? steps[k].textContent : 'Ready'
  }
  let liveText = ''
  const say = (html) => { if (html !== liveText) { liveText = html; live.innerHTML = html } }
  const blurSpheres = (k) => {
    const c = CAM.remember()
    const all = BLUR.map((faces, i) => {
      const a = (-200 + i * (220 / 7)) * Math.PI / 180
      const r = wide() ? 50 : 42
      return { id: 'b' + i, spec: { kind: 'blur', faces, foot: BLUR_FOOT[i] }, x: clamp(c.cx + Math.cos(a) * Math.min(W * 0.36, 520), r + 16, W - r - 16), y: c.cy + Math.sin(a) * H * 0.3, r, delay: 0 }
    })
    return all.slice(0, k)
  }
  // A run that stops partway: today's allowance running out pauses it until midnight Pacific,
  // and the bot going offline stops it until someone tries again. Either way what's been read
  // is kept, and the run carries on from where it stopped.
  const HALT = {
    paused: { at: 8.2, live: 'Paused', note: 'Today’s Gemini allowance ran out. It carries on by itself after midnight Pacific, in about 6 hours. Nothing read so far is lost.' },
    failed: { at: 8.2, live: 'Stopped', note: 'Olisar went offline partway through. Nothing read so far is lost, and trying again carries on from here.', retry: true },
  }
  const halt = HALT[STATE]
  const note = $('.rem-note', rememberEl), retryRow = $('.rem-retry', rememberEl).parentElement
  let halted = false, resumed = false
  const stopHere = () => {
    halted = true
    steps.forEach((li) => li.classList.toggle('halt', li.classList.contains('now')))
    const found = Math.round(N.moments * clamp((t - 6.6) / 3.8, 0, 1))
    say(`${halt.live} · <span class="num">${fmt(found)}</span> ${found === 1 ? 'moment' : 'moments'} found`)
    note.textContent = halt.note; note.hidden = false
    retryRow.hidden = !halt.retry
    status.textContent = `${halt.live}. ${halt.note}`
    applyForm({ shape: SHAPE.whole, v: 0.86, energy: -0.5, mood: { dim: 0.55 } })
    if (halt.retry && keyed) later(400, () => $('.rem-retry', rememberEl).focus({ preventScroll: true }))
  }
  $('.rem-retry', rememberEl).addEventListener('click', () => {
    halted = false; resumed = true
    note.hidden = true; retryRow.hidden = true
    steps.forEach((li) => li.classList.remove('halt'))
    status.textContent = steps[2].textContent
    applyForm({ shape: SHAPE.whole, v: 0.9, energy: 0.2, mood: { dim: 1 }, pulse: 0.6 })
  })
  let shown = 0
  return {
    start(from = 0) {
      t = from; on = true; fired = new Set(); shown = 0; liveText = ''; halted = false; resumed = false
      note.hidden = true; retryRow.hidden = true
      setStep(0)
      applyForm({ shape: SHAPE.remember, v: 1, energy: 0.6, mood: { dim: 1 }, feed: 0.007, regather: 0.5 })
      say('')
    },
    stop() { on = false; setSpheres([]) },
    frame(dt) {
      if (!on || halted) return
      if (halt && !resumed && t >= halt.at) { stopHere(); return }
      t += dt
      const n = N
      whereT += dt
      if (whereT > 0.26) { whereT = 0; whereI = (whereI + 1) % WHERE.length }
      at(4.2, () => { setStep(1); applyForm({ shape: SHAPE.server, v: 0.15, energy: 0.3, mood: { dim: 1 }, feed: 0.003 }); later(500, () => form?.set(SHAPE.server, 1)) })
      at(6.4, () => { setStep(2); applyForm({ shape: SHAPE.whole, v: 0.9, energy: 0.2, mood: { dim: 1 } }) })
      at(10.8, () => { setStep(3); applyForm({ shape: SHAPE.keys, v: 1, energy: 0.3, mood: { dim: 1 } }) })
      at(13.6, () => { setStep(4); form?.pulse(1) })
      at(14.4, () => go('play', { ready: true }))
      if (!on) return
      if (t < 4.2) {
        const k = smooth(clamp(t / 4, 0, 1))
        say(`<span class="num">${fmt(n.msgs * k)}</span> messages read <span class="where">${WHERE[whereI]}</span>`)
      } else if (t < 6.4) {
        const k = smooth(clamp((t - 4.2) / 1.6, 0, 1))
        say(`<span class="num">${fmt(1143 * k)}</span> people · <span class="num">${fmt(23 * k)}</span> channels · <span class="num">${Math.max(1, Math.round(n.years * k))}</span> ${n.years === 1 ? 'year' : 'years'}`)
      } else if (t < 10.8) {
        const k = clamp((t - 6.6) / 3.8, 0, 1)
        const want = Math.min(BLUR.length, Math.floor(k * (BLUR.length + 0.99)))
        if (want > shown) { shown = want; setSpheres(blurSpheres(shown)); form?.pulse(0.6) }
        const found = Math.max(0, Math.round(n.moments * k))
        say(`Found <span class="num">${found}</span> ${found === 1 ? 'moment' : 'moments'} <span class="where">${WHERE[(whereI + 3) % WHERE.length]}</span>`)
      } else {
        const k = clamp((t - 10.8) / 2.2, 0, 1)
        const keep = Math.round(BLUR.length * (1 - k))
        if (keep < shown) { shown = keep; setSpheres(blurSpheres(shown)) }
        say(t < 13.6 ? `Writing <span class="num">${n.chapters}</span> chapters` : 'Ready')
      }
    },
  }
})()

// ── Play ───────────────────────────────────────────────────────────────────────
// Where remembering ends: what it made, and Play.
document.title = `Replay · ${SERVER}`
$('.hud-name').firstChild.replaceWith(h('bdi', { class: 'hud-server' }, SERVER))
$('h2', rememberEl).replaceChildren('Remembering ', h('bdi', {}, SERVER))
$('h2', playEl).replaceChildren(h('bdi', {}, SERVER), ', 2021 to now')
const playGo = $('.play-go', playEl)
const playMins = Math.max(1, Math.round(BEATS.reduce((m, b) => m + (b.auto || 20), 0) / 60 + 1))
$('.meta', playEl).textContent = `${plural(CHAPTERS.length, 'chapter')} · about ${plural(playMins, 'minute')} · from ${plural(412880, 'message')}`
playGo.prepend(icon('play', 15))
playGo.addEventListener('click', () => go('replay'))

// ── Input ──────────────────────────────────────────────────────────────────────
$('.catch').addEventListener('click', () => {
  if (stage !== 'replay') return
  if (openId) { closeSphere(); return }
  next()
})
const ctlAuto = $('.ctl-auto'), ctlClose = $('.ctl-close')
ctlAuto.append(icon('play', 16)); ctlClose.append(icon('close', 16))
function setAuto(v) {
  autoplay = v; autoT = 0
  ctlAuto.replaceChildren(icon(v ? 'pause' : 'play', 16))
  const l = v ? 'Stop autoplay' : 'Autoplay'
  ctlAuto.setAttribute('aria-label', l); ctlAuto.setAttribute('data-tip', l); ctlAuto.setAttribute('aria-pressed', String(v))
}
ctlAuto.addEventListener('click', (e) => { e.stopPropagation(); setAuto(!autoplay) })
ctlClose.addEventListener('click', (e) => { e.stopPropagation(); go('play') })
window.addEventListener('keydown', (e) => {
  const typing = e.target.closest?.('input, textarea')
  if (e.key === 'Escape') {
    if (openId) { e.preventDefault(); closeSphere(); return }
    if (stage === 'replay' && !typing) { e.preventDefault(); go('play') }
    return
  }
  if (stage !== 'replay' || typing) return
  const onControl = e.target.closest?.('button, a, [role="slider"]') && !e.target.classList.contains('catch')
  if (e.key === 'ArrowRight' || ((e.key === ' ' || e.key === 'Enter') && !onControl)) {
    e.preventDefault()
    if (openId) closeSphere()
    else next()
  } else if (e.key === 'ArrowLeft') { e.preventDefault(); if (openId) closeSphere(); else prev() }
})

// Prototype only: start over, or skip to the replay.
const demoSkip = $('.demo-skip')
$('.demo-restart').addEventListener('click', (e) => { e.stopPropagation(); answer = null; go('start', { focus: true }) })
demoSkip.addEventListener('click', (e) => { e.stopPropagation(); go('replay') })

// ── The clock ──────────────────────────────────────────────────────────────────
function frame(dt) {
  stepCam(dt)
  stepSpark(dt)
  if (stage === 'remember') remembering.frame(dt)
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
if (form) form.onFrame(frame)
else { let last = 0; const loop = (now) => { const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60; last = now; frame(dt); requestAnimationFrame(loop) }; requestAnimationFrame(loop) }

const placeUnder = () => body.style.setProperty('--under-top', remTop() + 'px')
addEventListener('resize', () => {
  W = innerWidth; H = innerHeight; sizeFx(); placeUnder()
  const b = stage === 'replay' && beat()
  if (b?.spheres) setSpheres(b.spheres())
})
sizeFx()
placeUnder()

// ?at=start|remember|play|replay, and &beat= with a beat's id, open straight on it.
const at = q.get('at') === 'draft' ? 'play' : q.get('at')
if (q.has('pick')) settings.pick = clamp(+q.get('pick') || 0, 0, PICKS.length - 1)
go(['start', 'remember', 'play', 'replay'].includes(at) ? at : 'start', { beat: q.get('beat') })
Object.assign(cam, camFn())
if (q.has('auto')) setAuto(true)
form?.start()
})()
