// The memory replay, canned (api/routers/replay.py). Hollow Pines is the prototype's example
// server (design/memory-replay/), here as the report the bot would make of it, so every
// beat can be seen without a backend. REPLAY_MOCK picks where it starts:
//
//   ''          no replay yet; Start runs one in about 16 seconds
//   'done'      a finished replay to play
//   'indexing'  the search index is still reading history: Start anyway
//   'quota'     not enough of today's allowance: Start is off
//   'budget'    a billed key whose budget this would pass: Start is off
//   'short'     too little history: Start is off
//   'busy'      another admin's run: Watch it
//   'paused'    a run paused on the day's allowance
//   'failed'    a run that failed partway: Try again
//   'stopped'   a run whose bot went offline: Try again
//   'stress'    'done', with the worst names and messages a server sends

type Send = (obj: unknown, status?: number) => void

let MODE = ''
let started = 0          // when the mock run started (ms), 0 for none
let retried = false
let answer: string | null = null
let finished = false

export function configureReplayMock(mode: string | undefined): void {
  MODE = mode || ''
  finished = MODE === 'done' || MODE === 'stress'
  started = ['busy', 'paused', 'failed', 'stopped'].includes(MODE) ? Date.now() - 8000 : 0
  retried = false
  answer = null
}

const ME = '1089250623490359378'
const RUN_SECONDS = 16

const ROSTER: Record<string, string> = {
  '101': 'juniper', '102': 'okapi', '103': 'bram', '104': 'tess', '106': 'nightjar', '107': 'pocket', '108': 'kestrel', '109': 'vel', '110': 'milo',
}
const STRESS: Record<string, string> = {
  '101': 'juniper_the_extremely_online_01', '102': 'أوكابي', '103': '布拉姆🔥', '104': 'tess 🌙✨ (she/her) · book club', '106': 'נייטג׳אר',
}

function people(stress: boolean) {
  const out: Record<string, { name: string; avatar: string | null }> = {}
  for (const [id, name] of Object.entries(ROSTER)) {
    out[id] = { name: (stress && STRESS[id]) || name, avatar: `https://cdn.discordapp.com/embed/avatars/${Number(id) % 6}.png` }
  }
  return out
}

// Hollow Pines runs on Eastern time: 9:12 p.m. there is 2:12 the next morning in UTC.
const at = (y: number, mo: number, d: number, h: number, mi = 0) => new Date(Date.UTC(y, mo - 1, d, h + 5, mi)).toISOString()
let seq = 1
const msg = (who: string | null, when: string, text: string, chn = 'general') =>
  ({ id: String(1100000000000000000n + BigInt(seq++)), ch: '1100000000000000001', chn, at: when, who, text })

function report(stress: boolean) {
  seq = 1
  const created = at(2021, 3, 14, 21, 0)
  const first = [
    msg('101', at(2021, 3, 14, 21, 12), 'ok it exists. who’s in'),
    msg('102', at(2021, 3, 14, 21, 14), stress
      ? 'me. what is it 🤔 also has anyone seen https://example.com/an/extremely/long/link/that/goes/on/and/on?ref=hollowpines because i think it explains everything and nothing at the same time and i have been staring at it for an hour'
      : 'me. what is it'),
    msg('101', at(2021, 3, 14, 21, 14), 'no idea yet'),
  ]
  // Everyone still here by the day they joined: slow, a crowd on July 9, 2022, the 1,000th
  // two weeks before Olisar, 1,143 now.
  const start = Date.parse(created), DAY = 86400_000
  const end = Date.parse(at(2026, 10, 11, 12))
  const days = Math.round((end - start) / DAY)
  const burst = Math.round((Date.parse(at(2022, 7, 9, 12)) - start) / DAY)
  const thousand = Math.round((Date.parse(at(2025, 8, 5, 12)) - start) / DAY)
  const curve = (d: number) => 5 + 780 * Math.pow(d / thousand, 1.35) + (d >= burst ? 212 : 0)
  const scale = (1000 - 5) / (curve(thousand) - 5)
  const points: [number, number][] = []
  for (let d = 0; d <= days; d += 3) {
    const c = d <= thousand ? 5 + (curve(d) - 5) * scale : 1000 + 143 * ((d - thousand) / (days - thousand))
    points.push([d, Math.round(c)])
  }
  points.push([days, 1143])
  const night = [
    msg('103', at(2023, 2, 3, 23, 42), 'power’s out. whole street. phone at 31%'),
    msg('104', at(2023, 2, 3, 23, 43), stress ? 'هل لديك شموع؟' : 'do you have candles'),
    msg('103', at(2023, 2, 3, 23, 45), stress ? '我只有一根蜡烛，闻起来像“海洋微风”' : 'i have one candle and it smells like “ocean breeze”'),
    msg('102', at(2023, 2, 3, 23, 45), 'that’s a crime but it counts'),
    { gap: '2 hours and 611 messages later' },
    msg('106', at(2023, 2, 4, 2, 10), 'still up?'),
    msg('103', at(2023, 2, 4, 2, 11), '8%. the candle is winning'),
    msg('107', at(2023, 2, 4, 2, 13), 'ordering you a power bank. check your porch tuesday'),
    { gap: '4 hours later' },
    msg('103', at(2023, 2, 4, 6, 3), 'lights are back. ocean breeze lives on'),
  ]
  const spots = [
    ['#games · May 2022', 'most goose thing ever'], ['#general · Jun 2022', 'absolute goose'], ['#memes · Aug 2022', null],
    ['#late-night · Jan 2023', 'goose energy tonight'], ['#music · Mar 2023', null], ['#general · Feb 2024', 'that’s so goose'],
    ['#game-night · Nov 2024', null], ['#art · Apr 2025', 'goose of the week'], ['#general · Sep 2026', 'goose.'], ['#off-topic · Dec 2023', null],
  ].map(([where, said]) => ({ where, said }))
  return {
    v: 1, run: 7, answer,
    guild: { id: '1321947496179568680', name: stress ? 'The Hollow Pines Cozy Games, Book Club & Late Night Hangout 🌲' : 'Hollow Pines', icon: '' },
    bot: { name: 'Olisar', avatar: '/logo.png' },
    tz: -5,
    created,
    range: { from: created, to: new Date(end).toISOString() },
    totals: { messages: 412880, channels: 18, people: 1143, authors: 906, years: 5 },
    people: people(stress),
    first: { channel: 'general', messages: first },
    founders: [
      { who: '101', at: first[0].at, count: 41233, msg: first[0] },
      { who: '102', at: first[1].at, count: 28907, msg: first[1] },
      { who: '103', at: at(2021, 3, 15, 19, 40), count: 33410, msg: msg('103', at(2021, 3, 15, 19, 40), 'juniper sent me. i was promised snacks') },
      { who: '104', at: at(2021, 3, 17, 18, 2), count: 19882, msg: msg('104', at(2021, 3, 17, 18, 2), 'hi!! is this the book club or the other thing') },
      { who: null, at: at(2021, 3, 19, 20, 0), count: null, msg: null, gone: true },
    ],
    arrivals: {
      start: created, end: new Date(end).toISOString(), points, now: 1143,
      milestones: [
        { kind: 'nth', n: 100, at: at(2021, 10, 2, 18), who: '106' },
        { kind: 'burst', day: '2022-07-09', at: at(2022, 7, 9, 0), count: 212, line: 'Someone posted Hollow Pines on r/cozygames.' },
        { kind: 'nth', n: 1000, at: at(2025, 8, 5, 18), who: '108', weeks_before: 2 },
      ],
    },
    night: {
      day: '2023-02-03', channel: 'general', channel_id: '1100000000000000001', count: 1912, in_channel: 1880, night: true,
      from: at(2023, 2, 3, 21), to: at(2023, 2, 4, 6, 3), faces: ['103', '104', '102', '106', '107'],
      line: 'Most of them were about a candle.', sphere: `${people(stress)['103'].name}’s power went out`, excerpt: night,
    },
    pick: {
      kind: 'word', title: 'goose', meaning: 'a win nobody saw coming', why: 'Most of the server uses it, and it started with one message in #games.',
      origin: msg('102', at(2022, 5, 8, 22, 15), 'that was the most goose thing that’s ever happened', 'games'),
      intro: `Here, goose means a win nobody saw coming. ${people(stress)['102'].name} said it first.`,
      spread: '{people} of you have said it since. {count} times, in 17 channels.',
      people: 212, count: 1204, unit: 'times', channels: 17, spots,
    },
    arrive: {
      at: at(2025, 8, 19, 16, 20),
      reply: [
        { ...msg('101', at(2025, 8, 19, 16, 31), 'olisar what’s the wifi password at the cabin') },
        { ...msg('bot', at(2025, 8, 19, 16, 31), 'I don’t have that, and I’d be a little worried if I did.'), trigger: 'ask' },
      ],
    },
    names: [
      { at: at(2025, 10, 3, 14), from: 'Olisar', to: 'Pinecone', by: '101', via: 'chat' },
      { at: at(2025, 10, 7, 14), from: 'Pinecone', to: 'Olisar', by: '101', via: 'chat' },
    ],
    moments: { found: 31 },
    last_answer: null,
  }
}

const FACES = ['103', '104', '102', '101', '106', '107', '108', '110'].map((id) => `https://cdn.discordapp.com/embed/avatars/${Number(id) % 6}.png`)
const WHERE = ['#general · 2021', '#games · 2021', '#general · 2022', '#memes · 2022', '#music · 2022', '#late-night · 2023', '#general · 2023', '#art · 2024', '#game-night · 2024', '#general · 2025']

function progress(secs: number) {
  const k = (a: number, b: number) => Math.max(0, Math.min(1, (secs - a) / (b - a)))
  const phase = secs < 4.2 ? 0 : secs < 6.4 ? 1 : secs < 11 ? 2 : secs < RUN_SECONDS - 1 ? 3 : 4
  const moments = Math.round(31 * k(6.6, 10.6))
  return {
    phase, read: Math.round(412880 * k(0, 4)), total: 412880, where: WHERE[Math.floor(secs * 2.5) % WHERE.length],
    people: 1143, channels: 18, years: 5, moments, chapters: 6,
    found: Array.from({ length: Math.min(8, Math.floor(moments / 4)) }, (_, i) => ({ faces: [FACES[i], FACES[(i + 3) % 8]].slice(0, 1 + (i % 2)), year: String(2021 + (i % 5)) })),
  }
}

function run(now: number) {
  if (!started) return null
  const secs = (now - started) / 1000
  const halted = (MODE === 'paused' || MODE === 'failed' || MODE === 'stopped') && !retried
  if (!halted && secs >= RUN_SECONDS) { started = 0; finished = true; return null }
  const p = progress(halted ? 8.2 : secs)
  return {
    id: 7,
    status: halted ? MODE : secs < 0.5 ? 'queued' : 'running',
    step: ['gather', 'count', 'moments', 'write', 'check'][Math.min(4, p.phase)],
    progress: p,
    started_by: MODE === 'busy' ? { id: '1089266822827737191', name: 'okapi' } : { id: ME, name: 'gcrft123' },
    mine: MODE !== 'busy',
    started_at: new Date(started).toISOString(),
    updated_at: new Date(now).toISOString(),
    paused_until: MODE === 'paused' ? new Date(now + 6 * 3600_000).toISOString() : null,
    pause_reason: MODE === 'paused' ? 'quota' : null,
    error: MODE === 'failed' ? 'TimeoutError: the request timed out' : null,
    eta: Math.max(0, Math.round(RUN_SECONDS - secs)) * 60,
  }
}

function latest() {
  return finished ? { id: 7, finished_at: new Date().toISOString(), chapters: 6, messages: 412880, from: report(false).created, answer } : null
}

function preflight() {
  const base = {
    messages: 412880, channels: 18, first: report(false).created, indexing: null as unknown,
    cost: { tier: 'free', minutes: 25, requests: 51, share: 0.012, resets_at: new Date(Date.now() + 6 * 3600_000).toISOString() } as Record<string, unknown>,
  }
  if (MODE === 'indexing') return { ...base, state: 'indexing', indexing: { percent: 41, channels: 6, unread: 2, before: '2023-01-04T00:00:00Z', minutes: 190 } }
  if (MODE === 'quota') return { ...base, state: 'quota' }
  if (MODE === 'budget') return { ...base, state: 'budget', cost: { tier: 'paid', minutes: 9, requests: 51, usd: 0.71, budget: 5 } }
  if (MODE === 'short') return { ...base, state: 'short', messages: 86, channels: 3 }
  return { ...base, state: 'ready' }
}

/** Answers /api/replay*; false for anything else. */
export function replayMock(req: any, url: string, send: Send): boolean {
  if (!url.startsWith('/api/replay')) return false
  const now = Date.now()
  const method = req.method || 'GET'
  const server = { id: '1321947496179568680', name: MODE === 'stress' ? 'The Hollow Pines Cozy Games, Book Club & Late Night Hangout 🌲' : 'Hollow Pines' }
  if (url.startsWith('/api/replay/start') && method === 'POST') {
    if (run(now)) { send({ detail: 'A replay of this server is already running.' }, 409); return true }
    MODE = ''
    started = now
    send({ run: run(now) })
    return true
  }
  if (url.startsWith('/api/replay/retry') && method === 'POST') {
    retried = true
    started = now - 8000
    send({ run: run(now) })
    return true
  }
  if (url.startsWith('/api/replay/answer') && method === 'POST') {
    let raw = ''
    req.on('data', (c: any) => { raw += c })
    req.on('end', () => {
      try { answer = String(JSON.parse(raw || '{}').text || '').replace(/\s+/g, ' ').trim().slice(0, 120) } catch { /* keep it */ }
      send({ ok: true, answer })
    })
    return true
  }
  if (url.startsWith('/api/replay/report')) {
    if (!finished) { send({ detail: 'There’s no finished replay of this server.' }, 404); return true }
    send(report(MODE === 'stress'))
    return true
  }
  if (url.startsWith('/api/replay/status')) { send({ run: run(now), latest: latest() }); return true }
  send({ server, bot: { name: 'Olisar' }, run: run(now), latest: latest(), preflight: preflight() })
  return true
}
