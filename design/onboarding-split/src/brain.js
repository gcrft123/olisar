// ── The final screen ──────────────────────────────────────────────────────────────
// Once the server runs healthy and Discord lists the console's redirect, the stats screen
// folds away: the form moves to the middle of the window, the panel's title, status and
// controls shrink into its top-left corner, and memories of what Olisar has been doing bud
// off the form and drift around it. Anything else brings the stats screen back the same way.
//
// This file is the part that isn't Preact: the mock feed of what the bot did, the pfps it
// draws, and the controller that runs the change between the two screens and the memories'
// motion, writing styles and the engine's framing directly every frame.
;(function () {
  'use strict'
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
  const zoomOf = () => parseFloat(getComputedStyle(document.documentElement).zoom) || 1
  function rectL(el) {
    const r = el.getBoundingClientRect(), z = zoomOf()
    return { x: r.left / z, y: r.top / z, w: r.width / z, h: r.height / z }
  }
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x))
  const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1)
  const smooth = (t) => t * t * (3 - 2 * t)
  const inOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
  const lerp = (a, b, t) => a + (b - a) * t
  const approach = (x, to, step) => (x < to ? Math.min(to, x + step) : Math.max(to, x - step))

  // ── Pfps ────────────────────────────────────────────────────────────────────────
  // Discord's default avatars for some, and small seeded pictures of planets for the rest,
  // drawn once per name. Stand-ins for the CDN avatars the roster sync keeps.
  const CLYDE = 'M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z'
  const DEFAULTS = ['#5865f2', '#757e8a', '#3ba55c', '#faa61a', '#ed4245', '#eb459f']
  const SKIES = [['#123039', '#050d11'], ['#35211a', '#110907'], ['#1b2818', '#080d07'], ['#1d2531', '#090c12'], ['#382a14', '#120c05']]
  const PLANETS = [['#e2c29c', '#6b4a2e'], ['#a9cbd6', '#2d4c57'], ['#c3cca4', '#4b5634'], ['#d7aea6', '#5c3431'], ['#d9d0b4', '#57503a']]
  const hashStr = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) } return h >>> 0 }
  function rng(seed) {
    let t = seed
    return () => { t = (t + 0x6d2b79f5) | 0; let x = Math.imul(t ^ (t >>> 15), 1 | t); x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296 }
  }
  const AV = new Map()
  function avatarFor(name) {
    if (AV.has(name)) return AV.get(name)
    const who = PEOPLE[name] || 'art'
    const c = document.createElement('canvas'); c.width = c.height = 64
    const g = c.getContext('2d'), R = rng(hashStr(name))
    if (who === 'default') {
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

  // A stand-in for a picture the bot drew: a planet at dusk with a ship across it, seeded by
  // the prompt.
  const PIC = new Map()
  function pictureFor(prompt) {
    if (PIC.has(prompt)) return PIC.get(prompt)
    const W = 480, H = 300, c = document.createElement('canvas'); c.width = W; c.height = H
    const g = c.getContext('2d'), R = rng(hashStr(prompt))
    const sky = g.createLinearGradient(0, 0, 0, H)
    sky.addColorStop(0, '#0b1224'); sky.addColorStop(0.55, '#2a2440'); sky.addColorStop(1, '#8a5a3c')
    g.fillStyle = sky; g.fillRect(0, 0, W, H)
    for (let i = 0; i < 60; i++) { g.fillStyle = `rgba(255,255,255,${0.2 + 0.6 * R()})`; g.fillRect(R() * W, R() * H * 0.6, 1, 1) }
    const px = W * (0.55 + 0.25 * R()), py = H * 1.05, pr = H * 0.62
    const pl = g.createRadialGradient(px - pr * 0.3, py - pr * 0.55, pr * 0.1, px, py, pr)
    pl.addColorStop(0, '#e9f1f6'); pl.addColorStop(0.6, '#9fb6c8'); pl.addColorStop(1, '#3a4b5c')
    g.fillStyle = pl; g.beginPath(); g.arc(px, py, pr, 0, Math.PI * 2); g.fill()
    g.strokeStyle = 'rgba(255, 196, 140, 0.55)'; g.lineWidth = 2; g.beginPath(); g.arc(px, py, pr, Math.PI * 1.1, Math.PI * 1.75); g.stroke()
    // The ship: a long hull with a raised bridge, in silhouette.
    const sx = W * (0.18 + 0.2 * R()), sy = H * (0.28 + 0.12 * R()), L = W * 0.3
    g.fillStyle = '#05070d'
    g.beginPath(); g.moveTo(sx, sy); g.lineTo(sx + L, sy - L * 0.06); g.lineTo(sx + L * 1.08, sy + L * 0.02); g.lineTo(sx + L * 0.9, sy + L * 0.1)
    g.lineTo(sx + L * 0.2, sy + L * 0.12); g.closePath(); g.fill()
    g.fillRect(sx + L * 0.55, sy - L * 0.12, L * 0.18, L * 0.1)
    g.fillStyle = 'rgba(255, 200, 150, 0.8)'; g.fillRect(sx + L * 0.98, sy - L * 0.02, 3, 2)
    const url = c.toDataURL('image/jpeg', 0.86)
    PIC.set(prompt, url)
    return url
  }

  // ── What the bot did (mock) ─────────────────────────────────────────────────────
  // There's no activity feed in the backend yet; this has the fields one would need. The
  // kinds and their names follow the bot: reply triggers (bot/triggers.py, /ask and /catchup,
  // proactive chime-ins), member joins and the roster sync, impressions (persona_summary),
  // saved memories and glossary facts, the custom status, knowledge sources, reminders,
  // images, and the Docker healthcheck every 30 seconds. No DMs.
  const PEOPLE = {
    DadBodNerd: 'art', quietmoon: 'art', Kestrel: 'default', 'vex.io': 'art', marisol: 'default',
    NightOwl_88: 'art', Tobi: 'default', hollowpoint: 'art', a_very_long_discord_display_name_indeed: 'default',
    ferrocene: 'art', Juniper: 'default', lumen_dust: 'art',
  }
  // Each memory carries what an opened one shows: the message a reply answered, the whole
  // impression, where a fact was said, who asked for a reminder or a picture.
  const POOLS = {
    reply: [
      { trigger: '/ask', who: 'DadBodNerd', where: '#general', ask: 'when’s the Pyro run again, and do I need to bring anything?', text: 'Pyro run is Friday at 20:00 UTC. Meet at Checkmate and bring medpens. Fuel’s on the org this time.' },
      { trigger: 'Name trigger', who: 'quietmoon', where: '#off-topic', ask: 'olisar can you ping the whole org about tonight?', text: 'It’s 3:40 in the morning in JST, so maybe hold that ping until later. I can post it at 18:00 your time instead.' },
      { trigger: 'Mention', who: 'Kestrel', where: '#help-and-questions', ask: 'which has the better med bay, the Carrack or the Cutlass Red?', text: 'The Carrack’s med bay can respawn you. The Cutlass Red’s can’t, but it gets to a downed crewmate faster.' },
      { trigger: 'Reply', who: 'vex.io', where: '#general', ask: 'doesn’t that change if we’re carrying cargo though?', text: 'Fair point. The quantum fuel math changes once you’re hauling cargo. Budget about a fifth more for a full hold.' },
      { trigger: 'Chimed in', who: 'marisol', where: '#help-and-questions', ask: 'anyone know how to fix a stuck hangar elevator?', text: 'Someone asked this last week, and the answer’s pinned in this channel: leave the hangar, wait a minute, then call the elevator again.' },
      { trigger: '/catchup', who: 'NightOwl_88', where: '#general', ask: '/catchup hours: 24', text: 'Since yesterday: the op moved to Friday, Tobi’s ship is back, and ICA is recruiting. DadBodNerd is running movie night again.' },
      { trigger: '/ask', who: 'hollowpoint', where: '#lobby', ask: 'is mining worth it for a solo player?', text: 'Rent a Prospector first. Two runs will tell you whether mining is for you, and you won’t be out the price of a ship.' },
      { trigger: 'Mention', who: 'Tobi', where: '#general', ask: 'I’m back! did anyone take my spot in the fleet?', text: 'Welcome back. The fleet list still has you on the Cutlass.' },
      { trigger: 'Name trigger', who: 'a_very_long_discord_display_name_indeed', where: '#off-topic', ask: 'olisar do you remember which hull I wanted?', text: 'I remember. You wanted the Hull C, not the Hull A.' },
      { trigger: 'Reply', who: 'DadBodNerd', where: '#general', ask: 'can you close the movie poll at 6?', text: 'Done. The movie night poll closes at 18:00.' },
      { trigger: '/ask', who: 'lumen_dust', where: '#help-and-questions', ask: 'why can’t my crew see my beacon?', text: 'Your beacon only shows in the system you’re in. Jump first, then set it.' },
    ],
    member: [
      { who: 'ferrocene', roles: ['Recruit'] }, { who: 'Juniper', roles: ['Recruit'] },
      { who: 'lumen_dust', roles: ['Recruit'] }, { who: 'Kestrel', roles: ['Recruit', 'Member'] },
    ],
    impression: [
      { who: 'quietmoon', messages: 15, text: 'Quiet until it matters, and knows the Pyro jump routes cold.', full: 'Quiet until it matters, and knows the Pyro jump routes cold. Usually around late evenings JST. Prefers short answers with a source, and flies a Constellation with two regulars from the org.' },
      { who: 'DadBodNerd', messages: 22, text: 'Runs the Friday movie nights and most of the org ops.', full: 'Runs the Friday movie nights and most of the org ops. Dry sense of humour, answers questions before they finish being asked, and would rather be given the short version.' },
      { who: 'vex.io', messages: 15, text: 'Asks precise questions about cargo and trusts numbers over opinions.', full: 'Asks precise questions about cargo and trusts numbers over opinions. Runs trade routes between Stanton and Pyro and keeps a spreadsheet of margins.' },
      { who: 'marisol', messages: 17, text: 'Helps new members first, in short and friendly bursts.', full: 'Helps new members first, in short and friendly bursts. Knows the hangar and elevator bugs well, and points people to the pinned answers.' },
    ],
    remembered: [
      { who: 'DadBodNerd', where: '#general', type: 'Fact', text: 'Flies a Carrack named Long Way Round.', said: 'the Carrack’s name is Long Way Round btw, don’t let vex rename it again' },
      { who: 'quietmoon', where: '#off-topic', type: 'Fact', text: 'Their timezone is JST.', said: 'I’m on JST so your evenings are my mornings' },
      { who: 'Kestrel', where: '#general', type: 'Preference', text: 'Prefers voice over text for anything long.', said: 'honestly just hop in voice if it’s longer than a paragraph' },
      { who: 'NightOwl_88', where: '#general', type: 'Event', text: 'Leading the Pyro expedition on Friday.', said: 'I’m leading the Pyro expedition Friday, sign up in #org-ops' },
    ],
    glossary: [
      { where: '#general', who: 'Tobi', text: 'ICA is short for Ironclad Assault.', said: 'ICA = Ironclad Assault, the merc crew we’re allying with' },
      { where: '#general', who: 'DadBodNerd', text: 'Checkmate is where the org meets up in Pyro.', said: 'meet at Checkmate, it’s the station by the Pyro gateway' },
      { where: '#off-topic', who: 'marisol', text: 'The Hangar is the org’s voice channel for ops.', said: 'voice for ops is The Hangar, not general voice' },
    ],
    status: [
      { text: 'watching the stars', how: 'Set when it started' },
      { text: 'thinking it over', how: 'Set during a conversation in #off-topic' },
      { text: 'counting quantum fuel', how: 'Set during a conversation in #general' },
    ],
    learned: [
      { text: 'robertsspaceindustries.com', url: 'https://robertsspaceindustries.com/comm-link', count: 214, who: 'DadBodNerd', how: '/olisar learn-site' },
      { text: 'Org handbook', url: 'org-handbook.pdf', count: 38, who: 'marisol', how: '/olisar learn-doc' },
      { text: 'starcitizen.tools/Pyro', url: 'https://starcitizen.tools/Pyro', count: 96, who: 'NightOwl_88', how: '/olisar learn-url' },
    ],
    reminder: [
      { who: 'DadBodNerd', where: '#general', text: 'Fuel the Carrack before the op.', ask: 'remind me to fuel the Carrack an hour before the op' },
      { who: 'marisol', where: '#off-topic', text: 'Movie night starts in 30 minutes.', ask: 'remind everyone 30 minutes before movie night' },
    ],
    image: [
      { who: 'hollowpoint', where: '#off-topic', text: 'a Carrack over microTech at dusk', ask: 'can you draw a Carrack over microTech at dusk?' },
      { who: 'vex.io', where: '#general', text: 'the org logo as a hull decal', ask: 'what would the org logo look like as a hull decal?' },
    ],
  }
  const WEIGHTS = { reply: 50, impression: 9, remembered: 9, member: 8, glossary: 5, status: 4, learned: 5, reminder: 5, image: 5 }

  function createActivity() {
    const subs = new Set()
    const emit = (type, item) => subs.forEach((fn) => fn(type, item))
    let items = [], seq = 0, timer = 0, beat = 0, seeded = false
    const used = {}
    const health = { id: 'health', kind: 'health', at: Date.now() - 12000 }
    const make = (kind, data, ago = 0) => ({ id: `m${++seq}`, kind, at: Date.now() - ago * 1000, ...data })
    function next(kind, data) {
      if (!kind) {
        let r = Math.random() * Object.values(WEIGHTS).reduce((a, b) => a + b, 0)
        kind = Object.keys(WEIGHTS).find((k) => (r -= WEIGHTS[k]) < 0) || 'reply'
      }
      if (!data) {
        const pool = POOLS[kind], i = used[kind] = ((used[kind] ?? Math.floor(Math.random() * pool.length)) + 1) % pool.length
        data = pool[i]
      }
      return make(kind, data)
    }
    function push(item) {
      items.unshift(item)
      if (items.length > 24) items.length = 24
      emit('add', item)
    }
    function seed() {
      const P = POOLS
      items = [
        make('reply', P.reply[0], 40), make('impression', P.impression[0], 190), make('reply', P.reply[1], 320),
        make('member', P.member[3], 480), make('remembered', P.remembered[0], 730), make('status', P.status[0], 1140),
        make('people', { count: 176, faces: ['marisol', 'hollowpoint', 'Tobi'], more: ['DadBodNerd', 'quietmoon', 'vex.io', 'NightOwl_88', 'Kestrel', 'lumen_dust', 'Juniper', 'ferrocene'] }, 1500),
      ]
      used.reply = 1; used.impression = 0; used.member = 3; used.remembered = 0; used.status = 0
    }
    // `?quiet` holds the feed to the seeded memories (for measuring the layout).
    const quiet = new URLSearchParams(location.search).has('quiet')
    const schedule = () => { if (!quiet) timer = setTimeout(() => { push(next()); schedule() }, 6000 + Math.random() * 6000) }
    return {
      start() {
        if (!seeded) { seed(); seeded = true }
        if (!timer) schedule()
        if (!beat) beat = setInterval(() => { health.at = Date.now(); emit('beat', health) }, 30000)
      },
      stop() { clearTimeout(timer); timer = 0; clearInterval(beat); beat = 0 },
      items: () => items,
      health: () => health,
      add(kind) { push(next(kind)) },
      beat() { health.at = Date.now(); emit('beat', health) },
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn) },
    }
  }

  // Each kind's size on screen: the diameter of its sphere, in layout px, at a 900px window.
  const SIZE = { reply: 184, impression: 172, remembered: 160, reminder: 160, image: 160, glossary: 152, learned: 164, status: 152, member: 140, people: 156, health: 148 }

  // ── The controller ──────────────────────────────────────────────────────────────
  // One clock, p, runs from 0 (the stats screen) to 1 (the final screen) and back, and every
  // piece of the change reads its own stretch of it, so turning round halfway is just the
  // clock running the other way.
  function createBrain({ activity, open: startOpen }) {
    const $ = (sel) => document.querySelector(sel)
    const $$ = (sel) => document.querySelectorAll(sel)
    const subs = new Set()
    const notify = () => subs.forEach((fn) => fn())
    let form = null, detachHook = null
    let p = startOpen ? 1 : 0, target = p, frozen = null
    let open = false                        // memories are out
    let view = { w: innerWidth / zoomOf(), h: innerHeight / zoomOf() }
    let slot = null, hudRect = null, stripRect = null
    let pairs = []
    const bodies = new Map()
    const slots = new Array(12).fill(null)  // the engine's sphere for each body, kept stable
    let order = []
    let lastStep = 0, raf = 0, applied = -1, time = 0
    // One memory can be opened: it comes to the middle, large, with its context, while the form
    // and the rest draw back. `openness` is how far that has got (0 to 1).
    let openId = null, shownId = null, openness = 0
    let debug = new URLSearchParams(location.search).get('debug') === 'frame'

    const Z = () => zoomOf()
    function measure() {
      view = { w: innerWidth / Z(), h: innerHeight / Z() }
      const s = $('.onb-stage')
      slot = s && s.getClientRects().length ? rectL(s) : null
      const box = (q) => { const el = $(q); return el && el.getClientRects().length ? rectL(el) : null }
      hudRect = box('.brain-hud'); stripRect = box('.state-strip')
      $('.mems')?.style.setProperty('--sv', scaleV().toFixed(3))
      applied = -1
    }
    window.addEventListener('resize', () => { measure(); if (target !== p) measurePairs(); kick() })

    // ── Framing ──
    function stageFraming(s, v) {
      const x = s.x - v.x, y = s.y - v.y
      return { cx: x + s.w - 0.2 * s.h, cy: y + s.h / 2, r: 0.45 * s.h, x0: x, x1: x + 0.3 * s.w, fy: 0.1 * s.h }
    }
    function brainFraming(v) {
      const r0 = Math.min(0.23 * v.h, 0.3 * v.w)
      const hud = hudRect
      const cy = Math.max(v.h / 2, hud ? hud.y + hud.h + 0.74 * r0 + 48 : 0)
      // An opened memory takes the middle; the form draws back behind it.
      return { cx: v.w / 2, cy: Math.min(cy, v.h - 0.74 * r0 - 24), r: r0 * (1 - 0.45 * openness), x0: -2, x1: -1, fy: 0.02 * v.h }
    }
    // The form's resting radius on the final screen, whatever is open.
    const coreR = () => 0.74 * Math.min(0.23 * view.h, 0.3 * view.w)
    function framingFor(v) {
      const Fb = brainFraming(v)
      if (!slot || slot.w < 2) return Fb
      const Fs = stageFraming(slot, v)
      if (reduce.matches) return p < 0.5 ? Fs : Fb
      const e = inOut(seg(p, 0.1, 0.85))
      const F = {}
      for (const k in Fb) F[k] = lerp(Fs[k], Fb[k], e)
      return F
    }
    const orbFrame = () => framingFor({ x: 0, y: 0, w: view.w, h: view.h })

    // ── The shared pieces: title, badge, the two buttons, and the logo ──
    // The corner's copies start exactly over the stats screen's, take over from them in a
    // few frames, and travel; the stats screen's own never move (the wizard's half clips).
    const WIN = { logo: [0, 0.45], title: [0.1, 0.68], badge: [0.1, 0.68], console: [0.14, 0.72], power: [0.14, 0.72] }
    function measurePairs() {
      const rail = $('.onb-rail')
      const saved = rail ? rail.style.transform : ''
      if (rail) rail.style.transform = ''
      pairs = []
      for (const hud of $$('.brain-hud [data-morph]')) {
        const key = hud.getAttribute('data-morph')
        const stats = $(`.onb-pane [data-morph="${key}"]`) || $(`.onb-rail [data-morph="${key}"]`)
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
      const root = $('#onb')
      root?.classList.toggle('brain-on', p > 0 || target > 0)
      root?.classList.toggle('brain-full', p >= 1)
      root?.style.setProperty('--brain', p.toFixed(3))
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
      const moving = p > 0 && p < 1
      if (moving) {
        const rail = $('.onb-rail'), saved = rail ? rail.style.transform : ''
        if (rail) rail.style.transform = ''
        for (const q of pairs) {
          if (!q.stats.isConnected) {
            const el = $(`.onb-pane [data-morph="${q.key}"]`) || $(`.onb-rail [data-morph="${q.key}"]`)
            if (el) q.stats = el
          }
          if (q.stats.isConnected && q.stats.getClientRects().length) q.A = rectL(q.stats)
        }
        if (rail) rail.style.transform = saved
      }
      for (const q of pairs) {
        if (!q.hud.isConnected || !q.stats.isConnected) continue
        const [a, b] = q.win
        // Text scales by its height (the two sizes share proportions); the buttons don't
        // quite, so they meet halfway (the mean of the two ratios, centred) and hand over in
        // a few frames: a longer cross-fade shows two sizes of the same words.
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
      // Where there's no form on the stats screen (narrow, or reduced motion), the form's
      // layer fades out and in rather than the form travelling.
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

    // Keyboard focus follows the change: to the same control on the other side, or to the
    // other side's heading when that control can't take it (Stop turns into Working…).
    function moveFocus(toBrain, a) {
      const from = toBrain ? a?.closest?.('.onb-pane, .onb-rail') : a?.closest?.('.brain-hud, .mems')
      if (!from) return
      const key = a.closest('[data-morph]')?.getAttribute('data-morph')
      const side = toBrain ? '.brain-hud' : '.onb-pane'
      let el = key && ($(`${side} [data-morph="${key}"]`) || (!toBrain && $(`.onb-rail [data-morph="${key}"]`)))
      if (!el || el.disabled || el.tagName === 'IMG') el = $(`${side} h1`)
      el?.focus({ preventScroll: true })
    }
    let lastBlur = null
    document.addEventListener('focusout', (e) => { lastBlur = { el: e.target, at: performance.now() } })
    function setInert(toBrain) {
      const set = (sel, v) => { const el = $(sel); if (el) el.inert = v }
      set('.onb-pane', toBrain); set('.onb-rail', toBrain); set('.brain-hud', !toBrain); set('.mems', !toBrain)
    }

    // ── Memories ──
    const capacity = () => clamp(Math.floor((view.w * view.h) / 118000), 3, 8)
    const scaleV = () => clamp(Math.min(view.h / 818, view.w / 700), 0.72, 1.15)
    function visibleIds() {
      const list = activity.items().slice(0, capacity()).map((m) => m.id)
      return new Set(['health', ...list])
    }
    const itemFor = (id) => (id === 'health' ? activity.health() : activity.items().find((m) => m.id === id))
    // Where memories rest: all on one ring round the form, a wide ellipse, centred on it
    // whatever their sizes, with the same straight-line gap between neighbours' edges. Only the
    // stretch of ring where a memory would overlap the corner cluster is left out, and with
    // nothing in the way the ring closes on itself. They keep their order round it; a new one
    // goes in the widest gap.
    function ellipse() {
      return [clamp((1.05 * view.w) / view.h, 0.9, 1.9), clamp((0.85 * view.h) / view.w, 1, 1.6)]
    }
    const restR = (b) => ((SIZE[b.kind] || 150) / 2) * scaleV()
    const openR = () => Math.min(0.29 * view.h, (view.w < 560 ? 0.47 : 0.44) * view.w, 280)
    const TAU = Math.PI * 2
    const wrap = (a) => ((a % TAU) + TAU) % TAU
    const turnTo = (a, b) => wrap(b - a + Math.PI) - Math.PI   // shortest signed angle from a to b
    // How far out the ring sits along the ellipse's short axis: past the form (or an opened
    // memory) by the largest memory's size.
    function ringD() {
      const shrink = lerp(1, 0.55, openness)
      return lerp(coreR(), openR(), openness) + (SIZE.reply / 2) * scaleV() * shrink + lerp(30, 40, openness)
    }
    let arcIn = '', arcCache = null
    function arc(F) {
      const [sx, sy] = ellipse(), d = ringD(), hud = hudRect
      const inKey = `${sx.toFixed(3)}|${sy.toFixed(3)}|${d.toFixed(1)}|${F.cx.toFixed(1)}|${F.cy.toFixed(1)}|${hud ? `${hud.x | 0},${hud.y | 0},${hud.w | 0},${hud.h | 0}` : ''}`
      if (inKey === arcIn && arcCache) return arcCache
      arcIn = inKey
      // Where round the ring a full-size memory would touch the corner cluster.
      let lo = null, cut = 0
      if (hud) {
        const K = 360, pad = 18, r = (SIZE.reply / 2) * scaleV() * lerp(1, 0.55, openness)
        const hit = []
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
      const start = closed ? seam : lo + cut, span = TAU - cut
      const K = 240, th = new Float32Array(K + 1), acc = new Float32Array(K + 1)
      let total = 0
      for (let i = 0; i <= K; i++) {
        th[i] = start + (span * i) / K
        if (i) { const t = th[i] - span / K / 2; total += Math.hypot(sx * Math.sin(t), sy * Math.cos(t)) * (span / K) }
        acc[i] = total
      }
      const theta = (u) => {
        const want = clamp(u, 0, 1) * total
        let a = 1, b = K
        while (a < b) { const mid = (a + b) >> 1; if (acc[mid] < want) a = mid + 1; else b = mid }
        return th[a - 1] + (th[a] - th[a - 1]) * ((want - acc[a - 1]) / Math.max(1e-6, acc[a] - acc[a - 1]))
      }
      // Where along the ring (0 to 1) an angle falls; inside the left-out stretch, its nearer end.
      const frac = (t) => {
        let rel = wrap(t - start)
        if (!closed && rel > span) rel = rel - span < (TAU - span) / 2 ? span : 0
        const f = (rel / span) * K, i = Math.min(K - 1, Math.floor(f))
        return (acc[i] + (acc[i + 1] - acc[i]) * (f - i)) / total
      }
      arcCache = { key: `${inKey}|${start.toFixed(3)}|${span.toFixed(3)}`, theta, frac, sx, sy, d, closed }
      return arcCache
    }
    function ringPoint(F, th) {
      const A = arc(F)
      return [F.cx + A.d * A.sx * Math.cos(th), F.cy + A.d * A.sy * Math.sin(th)]
    }
    // The ring's spacing: the straight-line gap between neighbours' edges is the same all the way
    // round (plus half of it at either end of an open ring). The gap is found by bisection, each
    // next centre by bisection along the ellipse. Cached until the ring or its sizes change.
    let placeKey = '', placed = []
    function chain(A, radii) {
      const key = `${A.key}|${radii.map((r) => r.toFixed(1)).join(',')}`
      if (key === placeKey) return placed
      const n = radii.length, d = A.d
      const pt = (u) => { const t = A.theta(Math.min(u, 1)); return [d * A.sx * Math.cos(t), d * A.sy * Math.sin(t)] }
      // The next centre at a straight-line distance from this one. The distance only grows over
      // less than half a ring, so the search stays within that (on a closed ring the far end
      // comes back round to the start).
      const next = (u0, dist) => {
        const [x0, y0] = pt(u0), cap = Math.min(1, u0 + 0.45), [xc, yc] = pt(cap)
        if (Math.hypot(xc - x0, yc - y0) < dist) return 2
        let lo = u0, hi = cap
        for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2, [x, y] = pt(mid); if (Math.hypot(x - x0, y - y0) < dist) lo = mid; else hi = mid }
        return hi
      }
      const run = (g) => {
        const us = []
        let u = A.closed ? 0 : next(0, radii[0] + g / 2)
        us.push(u)
        for (let i = 1; i < n && u <= 1; i++) { u = next(u, radii[i - 1] + radii[i] + g); us.push(u) }
        const tail = u > 1 ? 2 : next(u, A.closed ? radii[n - 1] + radii[0] + g : radii[n - 1] + g / 2)
        return [us, tail]
      }
      let us
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
    // ends. A closed one can sit anywhere round, so it's laid from the first memory, turned to
    // sit as near as it can to where they all are, and laid again from there (turning a laid
    // ring would spoil its gaps, since the ellipse bends differently further round).
    function placeRing(A, radii, cur) {
      if (!A.closed) return chain(A, radii).map((u) => A.theta(u))
      const n = radii.length
      if (n < 2) return cur.slice()
      const d = A.d
      const pt = (t) => [d * A.sx * Math.cos(t), d * A.sy * Math.sin(t)]
      const next = (t0, dist, limit) => {
        const [x0, y0] = pt(t0), cap = Math.min(limit, t0 + 0.9 * Math.PI), [xc, yc] = pt(cap)
        if (Math.hypot(xc - x0, yc - y0) < dist) return Infinity
        let lo = t0, hi = cap
        for (let k = 0; k < 22; k++) { const mid = (lo + hi) / 2, [x, y] = pt(mid); if (Math.hypot(x - x0, y - y0) < dist) lo = mid; else hi = mid }
        return hi
      }
      const run = (t0, g) => {
        const ts = [t0]
        let t = t0
        for (let i = 1; i < n; i++) { t = next(t, radii[i - 1] + radii[i] + g, t0 + TAU); if (!isFinite(t)) return [ts, Infinity]; ts.push(t) }
        return [ts, next(t, radii[n - 1] + radii[0] + g, t0 + TAU)]
      }
      const lay = (t0) => {
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
    function pickTheta(F) {
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
        const g = (pts[i + 1] - pts[i]) * (i === 0 || i === pts.length - 2 ? 2 : 1)
        if (g > widest) { widest = g; best = i === 0 ? pts[1] / 2 : i === pts.length - 2 ? (pts[i] + 1) / 2 : (pts[i] + pts[i + 1]) / 2 }
      }
      return A.theta(best)
    }
    function spawn(id, delay) {
      const F = orbFrame(), item = itemFor(id)
      if (!item) return
      const th = pickTheta(F)
      const R = 0.74 * F.r
      const slot = slots.indexOf(null)
      if (slot < 0) return
      const [hx, hy] = ringPoint(F, th)
      const b = {
        id, kind: item.kind, th, slot, seed: Math.random(), ph: Math.random() * 6.28,
        x: F.cx + R * 0.82 * Math.cos(th), y: F.cy + R * 0.82 * Math.sin(th), vx: 0, vy: 0, hx, hy,
        r: 12, act: 0, actT: 0, releaseAt: performance.now() + delay, released: false,
        peek: 0, peekT: 0, pinned: false, ripple: 0, text: 0, node: null, rank: 0, ov: 0, ovT: 0, isOpen: false,
      }
      slots[slot] = id
      bodies.set(id, b)
      return b
    }
    function reorder() {
      const at = (id) => (id === 'health' ? -Infinity : itemFor(id)?.at ?? 0)
      order = [...bodies.keys()].sort((a, c) => at(c) - at(a))
      notify()
    }
    function syncBodies(now, releaseGap = 90, thinking = true) {
      if (!open) return
      const vis = visibleIds()
      let changed = false, i = 0
      for (const id of vis) {
        if (bodies.has(id)) { i++; continue }
        const item = itemFor(id)
        const reply = thinking && item?.kind === 'reply' && releaseGap === 0
        const b = spawn(id, reply ? 1000 : releaseGap * i)
        if (b && reply) { b.thinking = true; form?.energy(0.55) }
        i++; changed = true
      }
      if (changed) reorder()
    }
    function openMemories(intro) {
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
    activity.subscribe((type, item) => {
      if (type === 'add' && open) syncBodies(performance.now(), 0)
      if (type === 'beat') { const b = bodies.get('health'); if (b) b.ripple = 1 }
    })

    // ── Opening one ──
    function openOne(id) {
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
    function shut(quiet) {
      if (!openId) return
      const id = openId, b = bodies.get(id)
      if (b) { b.ovT = 0; b.isOpen = false; b.returning = true; b.peekT = 0; b.pinned = false }
      openId = null
      const mems = $('.mems'); if (mems && !quiet) mems.inert = false
      if (!quiet) {
        form?.mood({})
        $(`.mems [data-mem="${id}"] .mem-btn`)?.focus({ preventScroll: true })
      }
      notify(); kick()
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && openId && !document.querySelector('.modal-backdrop')) { e.preventDefault(); shut(false) }
    })

    function stepBodies(dt, now) {
      if (!bodies.size) { openness = 0; return }
      const rm = reduce.matches
      // How far open: the one opened (or closing) memory's own progress.
      for (const b of bodies.values()) b.ov = approach(b.ov, b.ovT, dt / (b.ovT > b.ov ? (rm ? 0.35 : 0.7) : (rm ? 0.3 : 0.5)))
      const shown = shownId && bodies.get(shownId)
      openness = shown ? smooth(shown.ov) : 0
      if (shown && shown.ov === 0 && shown.ovT === 0 && shownId !== openId) { shownId = null; notify() }
      const F = orbFrame()
      const vis = open ? visibleIds() : new Set()
      if (openId) vis.add(openId)          // an opened memory stays, however old
      const ranks = [...vis].filter((id) => id !== 'health')
      const sv = scaleV(), o = openness, shrink = lerp(1, 0.55, o), Ro = openR()
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
        const opened = b.ov
        b.rT = lerp(restR(b) * shrink * (1 + 0.18 * b.peek), Ro, opened)
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
      const m = (b) => b.r + 18
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
      const push = (b, nx, ny, over) => { b.ax += K_REP * over * nx; b.ay += K_REP * over * ny }
      const heavy = (b) => b.pinned || b.isOpen || b.ov > 0.02
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
          // The Preview strip is the prototype's own; while a memory is open, the rest may pass
          // under it.
          for (const k of [hudRect, o < 0.3 ? stripRect : null]) {
            if (!k) continue
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
      const sats = new Array(12).fill(null)
      const box = 280, o = openness
      for (const b of bodies.values()) {
        if (!b.node || !b.node.isConnected) b.node = $(`.mems [data-mem="${b.id}"]`)
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
      const openEl = shownId && $('.mem-open'), ob = shownId && bodies.get(shownId)
      if (openEl && ob) {
        const Ro = openR()
        openEl.style.transform = `translate(${(ob.x - Ro).toFixed(2)}px, ${(ob.y - Ro).toFixed(2)}px)`
        openEl.style.setProperty('--ro', `${Ro.toFixed(1)}px`)
        // Only once the sphere has arrived, so the words don't slide in with it.
        const near = 1 - clamp(Math.hypot(ob.x - ob.hx, ob.y - ob.hy) / 70, 0, 1)
        openEl.style.opacity = (smooth(seg(ob.ov, ob.ovT > 0 ? 0.55 : 0.7, 1)) * (ob.ovT > 0 ? smooth(near) : 1)).toFixed(3)
      }
      if (debug) drawDebug()
    }

    // The bot's own face, faint, filling most of the form, and its name across it: only on
    // the final screen, arriving as the form settles in the middle and leaving first. An
    // opened memory covers it, so it steps back then too.
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

    let dbg = null
    function drawDebug() {
      if (!dbg) { dbg = document.createElement('div'); dbg.className = 'dbg'; document.body.append(dbg) }
      const F = orbFrame()
      const rings = [{ x: F.cx, y: F.cy, r: 0.74 * F.r }, ...[...bodies.values()].map((b) => ({ x: b.x, y: b.y, r: b.r }))]
      while (dbg.children.length < rings.length) dbg.append(document.createElement('i'))
      ;[...dbg.children].forEach((el, i) => {
        const g = rings[i]
        el.style.display = g ? '' : 'none'
        if (g) Object.assign(el.style, { left: `${g.x - g.r}px`, top: `${g.y - g.r}px`, width: `${2 * g.r}px`, height: `${2 * g.r}px` })
      })
    }

    // ── The clock ──
    let waiters = []
    function step(dt, now) {
      if (now - lastStep < 4) return
      lastStep = now
      const rm = reduce.matches
      if (frozen == null && p !== target) {
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
    // The engine's frame steps this when it's drawing; otherwise (no WebGL, or its layer
    // withheld) a frame loop of its own does, for as long as there's something to move.
    function loop(now) {
      raf = 0
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60
      last = now
      if (form && form.running()) { last = 0; return }
      step(dt, now)
      if (p !== target || bodies.size > 0) raf = requestAnimationFrame(loop)
      else last = 0
    }
    let last = 0
    function kick() { if (!raf) raf = requestAnimationFrame(loop) }

    measure()
    const api = {
      attach(f) {
        form = f
        if (f) {
          f.framing((v) => framingFor(v))
          detachHook = f.onFrame((dt, now) => step(dt, now))
        }
        measure()
        document.fonts?.ready.then(() => { measure(); kick() })
        if (startOpen) { setInert(true); applyTransition(); openMemories(true) }
        kick()
      },
      setTarget(t) {
        if (t === target) return
        let focused = document.activeElement
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
      // Tests and the Preview strip.
      seek(v) { frozen = v; p = v; applied = -1; kick() },
      release() { frozen = null; kick() },
      add(kind) { activity.add(kind) },
      beat() { activity.beat() },
      state: () => ({ p, target, open, view, openId, openness, bodies: [...bodies.values()].map((b) => ({ id: b.id, kind: b.kind, x: b.x, y: b.y, r: b.r, act: b.act, rank: b.rank, peek: b.peek, ov: b.ov })), frame: orbFrame() }),
      // What the memories list renders: the ones with a body, newest first.
      list: () => order.map((id) => itemFor(id)).filter(Boolean),
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn) },
      peek(id, on) {
        const b = bodies.get(id)
        if (!b || openId || b.returning || b.ovT > 0) return
        b.peekT = on ? 1 : 0; b.pinned = !!on
        if (on) b.vx = b.vy = 0
      },
      open(id) { openOne(id) },
      close() { shut(false) },
      // The memory the open view shows (still set while it closes), and whether it's open.
      shown: () => (shownId ? { item: itemFor(shownId), open: shownId === openId } : null),
      remeasure() { measure(); measurePairs(); kick() },
      // Runs fn once the change in progress lands (at once if nothing's moving).
      whenSettled(fn) {
        if (p === target) { fn(p); return () => {} }
        waiters.push(fn)
        return () => { waiters = waiters.filter((w) => w !== fn) }
      },
      get target() { return target },
      get p() { return p },
    }
    return api
  }

  window.Brain = { createActivity, createBrain, avatarFor, pictureFor, SIZE }
})()
