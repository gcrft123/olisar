// The console's API, canned: every response the dev fixture gives, with no backend, database
// or OAuth behind it. Two hosts drive it. `vite.config.ts` mounts it as dev-server middleware
// (`USAGE_MOCK=1 npm run dev`), and a demo build (`VITE_DEMO`, see src/demo.ts) answers
// fetch() from it in the browser, so the console can be published as static files.
//
// Both hand `handle` a Node-style request: `method`, `headers`, and `on('data' | 'end')` for
// the body.

export type MockEnv = {
  /** SETUP_MOCK: '' | '1' | 'second' | 'intents' — open on the setup wizard; 'server' — on the
   *  server panel of a server that has been up a few hours, which goes to the final screen. */
  setup?: string
  /** FRESH_MOCK: '' | '1' | 'refused' | 'refused-console' — a console just after setup. */
  fresh?: string
  /** MOCK_ROLE: '' | 'admin' — sign in as a Manage Server admin, not the operator. */
  role?: string
  /** BOT_MOCK: '' | 'updating' | 'starting' | 'limited' | 'offline' | 'refused' — the bot's state
   *  in the sidebar's drawer. Online by default. */
  bot?: string
  /** USAGE_STATE: '' | 'fresh' | 'afternoon' | 'resting' | 'low' | 'out' | 'stale' — which of
   *  the Usage page's states it opens on. Afternoon by default. */
  usage?: string
}

export function configureMock(env: MockEnv): void {
  SETUP = env.setup || ''
  FRESH = env.fresh || ''
  MOCK_ROLE = env.role || ''
  BOT = env.bot || ''
  USAGE = env.usage || ''
  // A fresh install starts with every channel off and no keys saved.
  channels = MOCK_CHANNELS.map((c) => ({ ...c, mode: FRESH ? 'off' : c.mode }))
  keys = Object.fromEntries(Object.entries(MOCK_KEYS).map(([k, v]) => [k, { ...v, dashboard: FRESH ? false : v.dashboard }]))
}

// What a write changes, read back by the next GET: channel modes and saved keys, the two
// things the Get started list watches. Every other write is accepted and forgotten.
let channels: any[] = []
let keys: Record<string, { dashboard: boolean; env: boolean; value: string }> = {}

function readBody(req: any, then: (b: any) => void): void {
  let raw = ''
  req.on('data', (c: any) => { raw += c })
  req.on('end', () => {
    let b: any = {}
    try { b = JSON.parse(raw || '{}') } catch { /* an unreadable body changes nothing */ }
    then(b)
  })
}

export type MockSend = (obj: unknown, status?: number) => void

// ── Why it covers everything ─────────────────────────────────────────────────
// It used to cover four endpoints, and everything else fell through to Vite's static
// handler and came back as HTML — so most pages sat on a spinner forever and Command
// replies took the error boundary. Anything a reviewer can't reach is a page nobody
// reviews. Payloads mirror the real serializers field for field, deliberately: a fixture
// that returns a convenient shape hides exactly the drift it should expose.

// ── Usage ────────────────────────────────────────────────────────────────────
// The six states in design/usage-page/, picked by USAGE_STATE. Each starts at its own Pacific
// time and runs on from there: the live payload's `ts` carries the scene's clock, which the
// page adopts as the server's, and a reply arrives every four seconds on the first model that
// can take it, so the numbers move the way they would on a live bot.
const USAGE_CHAIN: [string, number][] = [
  ['gemini-3.5-flash', 250], ['gemini-flash-latest', 250], ['gemini-3-flash-preview', 250],
  ['gemini-2.5-flash', 250], ['gemini-3.1-flash-lite', 1000], ['gemini-flash-lite-latest', 1000],
  ['gemini-2.5-flash-lite', 1000],
]
type UsageScene = {
  time: string
  used: number[]
  embed: number
  web: number
  out?: Record<number, string>
  resting?: Record<number, number>
  peak: { v: number; cap: number; model: string; at: string }
  ranOutToday?: string
  stale?: boolean
}
const USAGE_SCENES: Record<string, UsageScene> = {
  fresh: {
    time: '00:42', used: [38, 0, 0, 0, 21, 0, 3], embed: 14, web: 2,
    peak: { v: 4, cap: 10, model: 'gemini-3.5-flash', at: '00:31' },
  },
  afternoon: {
    time: '15:48', used: [250, 231, 131, 12, 468, 0, 22], embed: 388, web: 45,
    out: { 0: '11:52', 1: '14:14' },
    peak: { v: 9, cap: 10, model: 'gemini-3.5-flash', at: '11:40' },
  },
  resting: {
    time: '12:20', used: [212, 9, 0, 0, 301, 0, 10], embed: 290, web: 31, resting: { 0: 48 },
    peak: { v: 10, cap: 10, model: 'gemini-3.5-flash', at: '12:19' },
  },
  low: {
    time: '19:05', used: [250, 250, 250, 250, 1000, 862, 610], embed: 802, web: 311,
    out: { 0: '10:31', 1: '12:58', 2: '14:40', 3: '15:22', 4: '17:48' },
    peak: { v: 15, cap: 15, model: 'gemini-3.1-flash-lite', at: '17:41' },
  },
  out: {
    time: '21:50', used: [250, 250, 250, 250, 1000, 1000, 1000], embed: 941, web: 402,
    out: { 0: '10:31', 1: '12:58', 2: '14:40', 3: '15:22', 4: '17:48', 5: '20:06', 6: '21:31' },
    peak: { v: 15, cap: 15, model: 'gemini-2.5-flash-lite', at: '21:12' },
    ranOutToday: '21:31',
  },
}
USAGE_SCENES.stale = { ...USAGE_SCENES.afternoon, stale: true }
const USAGE_SPLIT_TODAY: Record<string, number> = {
  conversation: 452, summary: 196, persona: 141, glossary: 88, vision: 74,
  proactivity: 58, grounding: 45, catchup: 21, extension: 16, canary: 14, status: 9,
}
const USAGE_SPLIT_7: Record<string, number> = {
  conversation: 9820, summary: 4310, persona: 3050, glossary: 1920, vision: 1480,
  proactivity: 1310, grounding: 1020, catchup: 480, extension: 350, canary: 98, status: 64,
}
const USAGE_SPLIT_30: Record<string, number> = {
  conversation: 41200, summary: 17650, persona: 13020, glossary: 7480, vision: 6310,
  proactivity: 5920, grounding: 4390, catchup: 2210, extension: 1640, canary: 420, status: 270,
}
// The 13 days before today.
const USAGE_PAST = [2610, 2980, 3120, 2840, 3390, 3710, 2950, 3060, 3240, 4000, 3380, 3150, 3290]

const PT = 'America/Los_Angeles'
function ptParts(ms: number) {
  const p = new Intl.DateTimeFormat('en-US', {
    timeZone: PT, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric',
  }).formatToParts(new Date(ms))
  const g = (t: string) => Number(p.find((x) => x.type === t)!.value)
  return { y: g('year'), mo: g('month') - 1, d: g('day'), h: g('hour'), mi: g('minute'), s: g('second') }
}
/** A Pacific wall-clock time as epoch ms. */
function ptWall(y: number, mo: number, d: number, h: number, mi: number) {
  const guess = Date.UTC(y, mo, d, h, mi)
  const p = ptParts(guess)
  return guess - (Date.UTC(p.y, p.mo, p.d, p.h, p.mi, p.s) - guess)
}
const USAGE_T0 = Date.now()
const USAGE_TODAY = ptParts(USAGE_T0)
const ptAt = (hhmm: string, dayOffset = 0) => {
  const [h, m] = hhmm.split(':').map(Number)
  return ptWall(USAGE_TODAY.y, USAGE_TODAY.mo, USAGE_TODAY.d + dayOffset, h, m)
}
const ptDay = (dayOffset = 0) => {
  const p = ptParts(ptAt('12:00', dayOffset))
  return `${p.y}-${String(p.mo + 1).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`
}

let usageLivePolls = 0
function usageScene() {
  const sc = USAGE_SCENES[USAGE] || USAGE_SCENES.afternoon
  const elapsed = Date.now() - USAGE_T0
  const t = ptAt(sc.time) + elapsed
  // Replies since the page opened, each on the first model that could take it.
  const used = sc.used.slice()
  const outAt: (number | null)[] = USAGE_CHAIN.map((_, i) => (sc.out?.[i] ? ptAt(sc.out[i]) : null))
  const restLeft = (i: number) => Math.max(0, (sc.resting?.[i] ?? 0) - elapsed / 1000)
  const replies = sc.stale ? 0 : Math.floor(elapsed / 4000)
  for (let k = 1; k <= replies; k++) {
    const at = ptAt(sc.time) + k * 4000
    const i = USAGE_CHAIN.findIndex(([, limit], j) => !outAt[j] && used[j] < limit && (sc.resting?.[j] ?? 0) * 1000 <= k * 4000)
    if (i < 0) break
    used[i] += 1
    if (used[i] >= USAGE_CHAIN[i][1]) outAt[i] = at
  }
  return { sc, t, used, outAt, restLeft, replies }
}

function mockLive() {
  const { sc, t, used, outAt, restLeft, replies } = usageScene()
  const reset = ptAt('00:00', 1)
  const chain = USAGE_CHAIN.map(([model, limit], i) => {
    const resting = !outAt[i] && restLeft(i) > 0
    return {
      model, requests: used[i], tokens: used[i] * 1420, limit, limit_from_google: false,
      state: outAt[i] ? 'spent' : resting ? 'resting' : 'ok',
      back_in: resting ? Math.ceil(restLeft(i)) : null,
      spent_at: outAt[i] ? new Date(outAt[i]!).toISOString() : null,
    }
  })
  return {
    ts: new Date(t).toISOString(),
    exhausted: BOT === 'limited' || chain.every((m) => m.state !== 'ok'),
    day: ptDay(),
    day_start: new Date(ptAt('00:00')).toISOString(),
    reset_at: new Date(reset).toISOString(),
    chain,
    memory_search: { model: 'gemini-embedding-001', requests: sc.embed + Math.floor(replies * 0.4), limit: 1000, spent: false },
    web_search: { requests: sc.web, limit: 500, spent: false },
  }
}

function mockSummary() {
  const { sc, used } = usageScene()
  const total = used.reduce((a, b) => a + b, 0)
  const base = Object.values(USAGE_SPLIT_TODAY).reduce((a, b) => a + b, 0)
  // Today's split scales with the scene; the rounding drift lands on replies so it sums.
  const today = Object.fromEntries(Object.entries(USAGE_SPLIT_TODAY).map(([k, v]) => [k, Math.round((v * total) / base)]))
  today.conversation += total - Object.values(today).reduce((a, b) => a + b, 0)
  const values = [...USAGE_PAST, total]
  return {
    day: ptDay(),
    features: { today, 7: USAGE_SPLIT_7, 30: USAGE_SPLIT_30 },
    yesterday: { requests: Math.round(total * 0.918), tokens: Math.round(total * 1420 * 1.04) },
    busiest_minute: { model: sc.peak.model, requests: sc.peak.v, limit: sc.peak.cap, at: new Date(ptAt(sc.peak.at)).toISOString() },
    last_ran_out: new Date(sc.ranOutToday ? ptAt(sc.ranOutToday) : ptAt('21:12', -4)).toISOString(),
    days: values.map((v, k) => {
      const offset = k - (values.length - 1)
      const ranOut = offset === 0 ? sc.ranOutToday : v >= 4000 ? '21:12' : null
      return { day: ptDay(offset), requests: v, ran_out_at: ranOut ? new Date(ptAt(ranOut, offset)).toISOString() : null }
    }),
  }
}

// ── Fixture data ─────────────────────────────────────────────────────────────
// Chosen to exercise the states a screenshot of happy-path data never reaches: a channel
// with no category, an uncoloured role, a member with no avatar and no impression, a
// knowledge source that failed, an empty glossary subject, a 40-character server name.

const MOCK_PERSONA = {
  name: 'Olisar',
  system_prompt:
    'A dry, unflappable ship\'s AI who has seen it all and keeps replies short. Knows Red Nebula ' +
    'Industries inside out and treats its members like the crew.',
  tone_notes: 'casual, lowercase, no emoji, never more than three sentences unless asked',
  desired_bio: 'Ship\'s AI for Red Nebula Industries. Ask me anything.',
  server_type: '',
  slang_density: 2,
  // The console builds its picker from whatever the API offers, so the fixture has to
  // carry the roster too or the Persona page renders a picker with one option.
  server_types: ['anime', 'art', 'finance', 'gaming', 'music', 'social', 'study', 'tech'],
  bot_avatar: '',
}

const MOCK_CONFIG = {
  name_triggers: ['olisar', 'oli'],
  reply_in_dms: true,
  default_model: 'gemini-flash-latest',
  grounding_enabled: true,
  grounding_daily_cap: 50,
  summary_token_threshold: 6000,
  glossary_mine_token_threshold: 12000,
  user_persona_msg_threshold: 40,
  context_message_limit: 12,
  presence_tools_enabled: false,
  silent_acks_enabled: true,
  name_requires_address: true,
  see_other_bots: false,
  blocked_mentions: ['everyone', 'here'],
  allowed_role_ids: ['1321947496179568690'],
  blocked_role_ids: ['1321947496179568694'],
  pin_actions: ['self_edit'],
}

const MOCK_PROACTIVITY = {
  enabled: true, level: 'low',
  channel_cooldown_sec: 600, user_cooldown_sec: 900, global_cooldown_sec: 240,
  confidence_threshold: 0.8, max_per_hour: 4, quiet_hours: { start: 23, end: 7 }, allowed_channels: [],
  reaction_enabled: true, reaction_threshold: 0.6, reaction_cooldown_sec: 300, reaction_max_per_hour: 8,
}

const MOCK_MODELS = [
  { name: 'gemini-flash-latest', label: 'Flash (latest)' },
  { name: 'gemini-3.5-flash', label: 'Flash 3.5' },
  { name: 'gemini-2.5-flash', label: 'Flash 2.5' },
  { name: 'gemini-2.0-flash', label: 'Flash 2.0' },
  { name: 'gemini-flash-lite-latest', label: 'Flash-Lite (latest)' },
  { name: 'gemini-2.0-flash-lite', label: 'Flash-Lite 2.0' },
]

// Mirrors olisar/messages.py — every key carries `placeholders`, including the empty
// array. The real serializer does `PLACEHOLDERS.get(key, [])`; a fixture that omitted the
// key would let a page ship that crashes on the real thing.
function mockMessages() {
  const M: [string, string, string[]][] = [
    ['ping', 'pong — {latency} ms', ['latency']],
    ['watch', "I'll read and remember this channel now.", []],
    ['unwatch', "I'll leave this channel alone.", []],
    ['channel_status', "This channel's mode is **{mode}**.", ['mode']],
    ['learn_url', "queued **{url}** — i'll read it shortly.", ['url']],
    ['learn_site', 'queued crawl of **{url}** (depth {depth}, up to {max_pages} pages).', ['url', 'depth', 'max_pages']],
    ['learn_doc', "queued **{filename}** — i'll read it shortly.", ['filename']],
    ['forget_me', 'done — deleted {messages} messages and {facts} remembered facts, and cleared your profile.', ['messages', 'facts']],
    ['forget_me_optout', "i'll stop recording your messages from now on.", []],
    ['dm_indexing', 'DM saving & indexing is now **{state}**.', ['state']],
    ['proactive', 'proactive chiming is now **{state}** (level: **{level}**).', ['state', 'level']],
    ['rate_limit', "i'm a bit rate-limited right now — give me a minute and try again?", []],
    ['blank_fallback', '…my mind just went blank there. mind rephrasing?', []],
    ['access_denied', "sorry — you don't have access to me here.", []],
    ['tool_pin_prompt', 'I need a PIN before I can run **{tool}**. See Settings > Security in the console or ask an admin if you don\'t have access.', ['tool', 'seconds']],
    ['privacy', '**How I handle your data**\n…', []],
  ]
  const out: Record<string, unknown> = {}
  // One override, so the page renders both the "using the default" and "overridden" states.
  for (const [key, def, ph] of M) {
    out[key] = { default: def, custom: key === 'blank_fallback' ? '…lost my train of thought, say that again?' : null, placeholders: ph }
  }
  return out
}

const MOCK_CHANNELS = [
  { channel_id: '1', name: 'welcome', category: 'INFORMATION', mode: 'resource', kind: 'text', indexed: true },
  { channel_id: '2', name: 'rules', category: 'INFORMATION', mode: 'resource', kind: 'text', indexed: true },
  { channel_id: '3', name: 'announcements', category: 'INFORMATION', mode: 'feed', kind: 'text', indexed: true },
  { channel_id: '4', name: 'general', category: 'THE MESS HALL', mode: 'both', kind: 'text', indexed: true },
  { channel_id: '5', name: 'off-topic', category: 'THE MESS HALL', mode: 'both', kind: 'text', indexed: true },
  { channel_id: '6', name: 'screenshots-and-clips', category: 'THE MESS HALL', mode: 'memory', kind: 'text', indexed: true },
  { channel_id: '7', name: 'help-and-questions', category: 'THE MESS HALL', mode: 'respond', kind: 'forum', indexed: true },
  { channel_id: '8', name: 'org-ops', category: 'OPERATIONS', mode: 'memory', kind: 'text', indexed: false },
  { channel_id: '9', name: 'mod-only', category: 'OPERATIONS', mode: 'off', kind: 'text', indexed: false },
  // No category — Discord's uncategorised channels sit at the top level.
  { channel_id: '10', name: 'lobby', category: '', mode: 'both', kind: 'text', indexed: true },
]

const MOCK_ROLES = [
  { role_id: '1321947496179568691', name: 'Fleet Admiral', color: '#f2728a', position: 9 },
  { role_id: '1321947496179568690', name: 'Member', color: '#5b9cf6', position: 6 },
  { role_id: '1321947496179568692', name: 'Veteran', color: '#43cf8e', position: 5 },
  // Uncoloured — Discord returns "" and the chip must fall back rather than invent a hue.
  { role_id: '1321947496179568693', name: 'Recruit', color: '', position: 3 },
  { role_id: '1321947496179568694', name: 'Muted', color: '#7f7f8a', position: 1 },
]

const MOCK_PROFILES = [
  {
    user_id: '101', display_name: 'DadBodNerd', avatar: '',
    roles: [{ id: '1321947496179568691', name: 'Fleet Admiral' }, { id: '1321947496179568690', name: 'Member' }],
    impression: 'Runs the Friday movie nights and most of the org ops. Dry sense of humour, answers questions before they finish being asked, and would rather be given the short version.',
    messages_since_persona: 12, first_seen: '2025-11-02T18:20:00Z', last_seen: '2026-08-07T21:04:00Z',
    memories: [
      { kind: 'fact', content: 'Flies a Carrack named "Long Way Round".', created_at: '2026-02-11T10:00:00Z' },
      { kind: 'preference', content: 'Prefers voice over text for anything longer than a paragraph.', created_at: '2026-03-01T10:00:00Z' },
      { kind: 'event', content: 'Organised the Pyro expedition on 12 July.', created_at: '2026-07-12T10:00:00Z' },
    ],
  },
  {
    user_id: '102', display_name: 'quietmoon', avatar: '',
    roles: [{ id: '1321947496179568692', name: 'Veteran' }],
    impression: '', messages_since_persona: 3,
    first_seen: '2026-01-14T09:00:00Z', last_seen: '2026-08-05T12:00:00Z',
    memories: [{ kind: 'fact', content: 'Timezone is JST.', created_at: '2026-06-02T10:00:00Z' }],
  },
  {
    // Long name, many roles, nothing learned — the "+N" chip and the empty impression.
    user_id: '103', display_name: 'a_very_long_discord_display_name_indeed', avatar: '',
    roles: [
      { id: '1321947496179568690', name: 'Member' }, { id: '1321947496179568692', name: 'Veteran' },
      { id: '1321947496179568693', name: 'Recruit' }, { id: '1321947496179568694', name: 'Muted' },
    ],
    impression: '', messages_since_persona: 0,
    first_seen: '2026-07-30T09:00:00Z', last_seen: '2026-08-01T12:00:00Z', memories: [],
  },
]

// Relative to server start, so the "checked 2 hours ago / next read in 5 days" line renders
// with real spans instead of dates from whenever this fixture was last edited.
const hoursOut = (h: number) => new Date(Date.now() + h * 3600_000).toISOString()

// Every state the sources list can be in, including the ones that are easy to get wrong: a
// source on an interval the console's own ladder doesn't offer (id 4), and an uploaded doc,
// which can't be scheduled at all and must render without the control rather than with a
// disabled one (id 5).
const MOCK_KNOWLEDGE = [
  { id: 1, type: 'url', uri: 'https://robertsspaceindustries.com/comm-link', title: 'RSI Comm-Link', status: 'ready', chunks: 184, error: null,
    refresh_hours: 24, next_refresh_at: hoursOut(9), last_checked_at: hoursOut(-15), last_ingested_at: hoursOut(-15), can_refresh: true },
  { id: 2, type: 'website', uri: 'https://docs.example.org/handbook', title: 'Org handbook', status: 'chunking', chunks: 26, error: null,
    refresh_hours: 168, next_refresh_at: hoursOut(121), last_checked_at: hoursOut(-47), last_ingested_at: hoursOut(-47), can_refresh: true },
  { id: 3, type: 'url', uri: 'https://unreachable.example/404', title: '', status: 'error', chunks: 0, error: 'fetch failed — 404 Not Found',
    refresh_hours: 6, next_refresh_at: hoursOut(-1), last_checked_at: hoursOut(-7), last_ingested_at: null, can_refresh: true },
  { id: 4, type: 'website', uri: 'https://status.example.net/', title: 'Status feed', status: 'ready', chunks: 12, error: null,
    refresh_hours: 5, next_refresh_at: hoursOut(3), last_checked_at: hoursOut(-2), last_ingested_at: hoursOut(-2), can_refresh: true },
  { id: 5, type: 'doc', uri: '/data/kb_uploads/charter.pdf', title: 'charter.pdf', status: 'ready', chunks: 41, error: null,
    refresh_hours: 0, next_refresh_at: null, last_checked_at: hoursOut(-620), last_ingested_at: hoursOut(-620), can_refresh: false },
  // Past the list's four-row cap, so its scroll and edge fades render.
  { id: 6, type: 'url', uri: 'https://starcitizen.tools/Mining', title: 'Mining — Star Citizen Wiki', status: 'ready', chunks: 67, error: null,
    refresh_hours: 168, next_refresh_at: hoursOut(90), last_checked_at: hoursOut(-78), last_ingested_at: hoursOut(-78), can_refresh: true },
  { id: 7, type: 'website', uri: 'https://uexcorp.space/', title: 'UEX trade data', status: 'crawling', chunks: 0, error: null,
    refresh_hours: 12, next_refresh_at: hoursOut(12), last_checked_at: null, last_ingested_at: null, can_refresh: true },
  { id: 8, type: 'doc', uri: '/data/kb_uploads/fleet-doctrine.md', title: 'fleet-doctrine.md', status: 'ready', chunks: 18, error: null,
    refresh_hours: 0, next_refresh_at: null, last_checked_at: hoursOut(-300), last_ingested_at: hoursOut(-300), can_refresh: false },
  { id: 9, type: 'url', uri: 'https://robertsspaceindustries.com/spectrum/community/SC/forum/1/thread/patch-notes', title: 'Patch notes thread', status: 'pending', chunks: 0, error: null,
    refresh_hours: 24, next_refresh_at: hoursOut(24), last_checked_at: null, last_ingested_at: null, can_refresh: true },
]

const MOCK_FACTS = [
  { id: 1, subject: 'MN', fact: 'Movie Night, the Friday watch-party in #general.', mentions: 14, updated_at: '2026-08-01T10:00:00Z' },
  { id: 2, subject: 'The Council', fact: "The server's moderator team.", mentions: 6, updated_at: '2026-07-21T10:00:00Z' },
  { id: 3, subject: '', fact: 'Long-haul runs leave from Port Olisar at 20:00 UTC on Saturdays.', mentions: 1, updated_at: '2026-06-02T10:00:00Z' },
  { id: 4, subject: 'RNI', fact: 'Red Nebula Industries, the org this server belongs to.', mentions: 41, updated_at: '2026-08-06T10:00:00Z' },
  { id: 5, subject: 'Hauler', fact: 'Anyone flying cargo for the org on a scheduled run.', mentions: 9, updated_at: '2026-08-02T10:00:00Z' },
  { id: 6, subject: 'Vex', fact: 'Vex is the org quartermaster and runs #quartermaster.', mentions: 17, updated_at: '2026-07-30T10:00:00Z' },
  { id: 7, subject: 'The Rock', fact: 'Daymar, where the org does most of its mining.', mentions: 5, updated_at: '2026-07-28T10:00:00Z' },
  { id: 8, subject: '', fact: 'Org ops are announced 48 hours ahead in #event-planning.', mentions: 3, updated_at: '2026-07-19T10:00:00Z' },
  { id: 9, subject: 'Blue ticket', fact: 'A recruit who has passed the flight check but not the interview.', mentions: 4, updated_at: '2026-07-12T10:00:00Z' },
  { id: 10, subject: 'Salvage Sunday', fact: 'The weekly salvage op, Sundays at 18:00 UTC.', mentions: 8, updated_at: '2026-07-08T10:00:00Z' },
  { id: 11, subject: 'Hull C', fact: 'The org owns two, and they are booked through #fleet-ops.', mentions: 2, updated_at: '2026-07-01T10:00:00Z' },
  { id: 12, subject: 'Kestrel', fact: "Kestrel is the org's head of recruitment.", mentions: 11, updated_at: '2026-06-24T10:00:00Z' },
  { id: 13, subject: '', fact: 'New members get a 30-day probation role before full access.', mentions: 1, updated_at: '2026-06-15T10:00:00Z' },
  { id: 14, subject: 'Grim HEX run', fact: 'The monthly outlaw-space supply run, and it always needs escorts.', mentions: 6, updated_at: '2026-06-09T10:00:00Z' },
]

// Thirty rows, shaped like /api/knowledge/reindex/status: a finished backfill for most of the
// server, two channels mid-backfill and two still queued (so the progress bar and every chip
// state render), and the aggregate DM row the endpoint appends once there's DM activity.
const MOCK_REINDEX = (() => {
  const names = [
    'general', 'announcements', 'rules', 'welcome', 'introductions', 'fleet-ops', 'trade-routes',
    'mining', 'salvage', 'bounty-board', 'medical', 'ship-showcase', 'screenshots', 'lfg',
    'event-planning', 'patch-notes', 'org-news', 'recruitment', 'diplomacy', 'lore',
    'off-topic', 'memes', 'music', 'tech-support', 'feedback', 'voice-text', 'hangar',
    'quartermaster', 'training',
  ]
  const status = (i: number) => (i === 7 || i === 13 ? 'indexing' : i === 26 || i === 28 ? 'queued' : 'done')
  const channels: any[] = names.map((name, i) => ({
    channel_id: String(9001 + i), name, kind: name === 'lore' ? 'forum' : 'text', status: status(i),
    indexed: status(i) === 'queued' ? 0 : Math.round(18_400 / (1 + i * 0.6)) + (i * 137) % 400,
  }))
  channels.push({ channel_id: 'dm', name: 'Direct messages', kind: 'dm', status: 'done', indexed: 2_214 })
  const count = (s: string) => channels.filter((c) => c.status === s).length
  const indexing = count('indexing'), queued = count('queued')
  return {
    total: channels.length, done: count('done'), indexing, queued, running: indexing + queued > 0,
    indexed_messages: channels.reduce((n, c) => n + c.indexed, 0), channels,
  }
})()

// Mirrors the admin router's /api/extensions entry (NOT extensions.py's authoring
// summary — different shape). `editable` is `kind == "user"` there, which is what drives
// the "Custom" badge: mirroring it loosely made every built-in claim to be the operator's
// own code, and first-party vs imported is exactly what governs host secrets and hooks.
const MOCK_EXTENSIONS = [
  { key: 'dice', name: 'Dice roller', description: 'Roll dice on request.', category: 'Games', enabled: true, default_enabled: true, kind: 'builtin', editable: false, user_modified: false, has_code: true, origin: 'builtin', publisher: null, signed_by: null, signature_verified: null, tools: ['roll_dice'], commands: [], permissions: [], requested_permissions: [], behavior: false, settings_schema: null },
  { key: 'calculator', name: 'Calculator', description: 'Exact arithmetic instead of guessing at numbers.', category: 'Utilities', enabled: true, default_enabled: true, kind: 'builtin', editable: false, user_modified: false, has_code: true, origin: 'builtin', publisher: null, signed_by: null, signature_verified: null, tools: ['calculate'], commands: [], permissions: [], requested_permissions: [], behavior: false, settings_schema: null },
  { key: 'concise', name: 'Concise mode', description: 'Keeps replies short and to the point.', category: 'Behavior', enabled: false, default_enabled: false, kind: 'builtin', editable: false, user_modified: false, has_code: true, origin: 'builtin', publisher: null, signed_by: null, signature_verified: null, tools: [], commands: [], permissions: [], requested_permissions: [], behavior: true, settings_schema: null },
  { key: 'welcome', name: 'Welcome', description: 'Greets new members as they join.', category: 'Community', enabled: false, default_enabled: false, kind: 'builtin', editable: false, user_modified: false, has_code: true, origin: 'builtin', publisher: null, signed_by: null, signature_verified: null, tools: [], commands: [], permissions: ['model.generate', 'discord.send'], requested_permissions: ['model.generate', 'discord.send'], behavior: false, settings_schema: { fields: [{ key: 'channel_id', type: 'channel', label: 'Welcome channel' }, { key: 'prompt', type: 'textarea', label: 'Welcome prompt' }] } },
  { key: 'star_citizen', name: 'Star Citizen', description: 'Trade, ship and location data for SC communities.', category: 'Games', enabled: true, default_enabled: false, kind: 'builtin', editable: false, user_modified: true, has_code: true, origin: 'builtin', publisher: null, signed_by: null, signature_verified: null, tools: ['commodity', 'trade_routes', 'ship_lookup', 'location', 'jump_points'], commands: ['citizen'], permissions: ['fetch', 'kb.write', 'secret:uex_api_key'], requested_permissions: ['fetch', 'kb.write', 'secret:uex_api_key'], behavior: true, settings_schema: null },
  // A marketplace install: fewer granted than requested — the state the consent screen creates.
  { key: 'poll', name: 'Polls', description: 'Persistent poll buttons that survive a restart.', category: 'Utilities', enabled: false, default_enabled: false, kind: 'user', editable: true, user_modified: false, has_code: true, origin: 'marketplace', publisher: 'm-studio', signed_by: 'a3f1 9c22 dd07', signature_verified: true, tools: [], commands: ['poll'], permissions: ['kv', 'discord.reply'], requested_permissions: ['kv', 'discord.reply', 'discord.components', 'fetch'], behavior: false, settings_schema: null },
]

// Marketplace search results, shaped like the registry proxy's `results` rows: one verified
// publisher, one unverified, one of this install's own (so Yank renders), one with no
// description or permissions at all.
const MOCK_MARKET = [
  { id: 'm-studio/poll', namespace: 'm-studio', name: 'poll', version: '1.4.0', category: 'Utilities', publisher: 'm-studio', publisher_verified: true, description: 'Persistent poll buttons that survive a restart.', permissions: ['kv', 'discord.reply', 'discord.components'] },
  { id: 'lorekeeper/quotes', namespace: 'lorekeeper', name: 'quotes', version: '0.9.2', category: 'Community', publisher: 'lorekeeper', publisher_verified: false, description: 'Save a message as a quote with a reaction, and pull a random one back up with /quote.', permissions: ['kv', 'discord.reply'] },
  { id: 'rednebula/fleet-roster', namespace: 'rednebula', name: 'fleet-roster', version: '2.0.0', category: 'Games', publisher: 'rednebula', publisher_verified: false, description: 'Who flies what: a shared ship roster Olisar can answer questions from.', permissions: ['kv', 'kb.write', 'fetch'] },
  { id: 'tinytools/coinflip', namespace: 'tinytools', name: 'coinflip', version: '1.0.0', category: 'Games', publisher: 'tinytools', publisher_verified: false, description: '', permissions: [] },
]

// The tool PIN behind Settings → Security. Stateful on purpose: "no PIN yet", "PIN set"
// and the removal confirm are three different renderings of one pane, and a fixture that
// always answers "set" leaves two of them unreviewable. It starts unset so Access shows its
// no-PIN warning. `gated_tools` is empty, which is what every shipped configuration reports.
const MOCK_UPDATES = { current: '2.0.beta-1', channel: 'beta', latest: 'v2.0.beta-1', available: false }

const MOCK_PIN: { is_set: boolean; timeout_sec: number; updated_at: string | null; gated_tools: string[] } = {
  is_set: false, timeout_sec: 120, updated_at: null, gated_tools: [],
}

const MOCK_KEYS = {
  gemini_api_key: { dashboard: true, env: false, value: '' },
  cloudflare_account_id: { dashboard: true, env: false, value: '' },
  cloudflare_api_token: { dashboard: true, env: false, value: '' },
  uex_api_key: { dashboard: false, env: false, value: '' },
}

const MOCK_AUDIT = {
  install_wide: true,
  entries: [
    { id: 5, ts: '2026-08-07T20:14:00Z', actor: 'gcrft123', action: 'clear_memory', label: 'Cleared memory', destructive: true, target_type: 'guild', target_id: '1321947496179568680', after: { counts: { messages: 12481, facts: 340, profiles: 96, knowledge: 4 } } },
    { id: 4, ts: '2026-08-07T18:02:00Z', actor: 'gcrft123', action: 'update_persona', label: 'Updated the persona', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: null },
    { id: 3, ts: '2026-08-06T11:40:00Z', actor: 'intmorg', action: 'set_channel_indexing', label: "Changed a channel's indexing", destructive: true, target_type: 'channel', target_id: '9', after: { indexed: false } },
    { id: 2, ts: '2026-08-05T09:15:00Z', actor: 'gcrft123', action: 'toggle_extension', label: 'Toggled an extension', destructive: false, target_type: 'extension', target_id: 'star_citizen', after: { enabled: true } },
    { id: 1, ts: '2026-08-04T16:30:00Z', actor: 'gcrft123', action: 'update_config', label: 'Changed behavior settings', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: null },
    // Older history, past the list's ten-row cap. Labels and the destructive flag come from
    // api/routers/audit.py's ACTION_LABELS and DESTRUCTIVE.
    { id: 0, ts: '2026-08-03T21:05:00Z', actor: 'intmorg', action: 'add_kb_source', label: 'Added a knowledge source', destructive: false, target_type: 'kb_source', target_id: '9', after: { uri: 'https://robertsspaceindustries.com/spectrum/community/SC/forum/1/thread/patch-notes', type: 'url', refresh_hours: 24 } },
    { id: -1, ts: '2026-08-03T12:48:00Z', actor: 'gcrft123', action: 'mine_glossary', label: 'Mined the glossary', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: { counts: { facts: 7 } } },
    { id: -2, ts: '2026-08-02T19:22:00Z', actor: 'gcrft123', action: 'delete_guild_fact', label: 'Deleted a glossary fact', destructive: true, target_type: 'guild_fact', target_id: '15', after: null },
    { id: -3, ts: '2026-08-02T08:10:00Z', actor: 'gcrft123', action: 'set_channel_mode', label: "Changed a channel's mode", destructive: false, target_type: 'channel', target_id: '4', after: { mode: 'both' } },
    { id: -4, ts: '2026-08-01T17:33:00Z', actor: 'intmorg', action: 'update_command_messages', label: 'Edited command replies', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: null },
    { id: -5, ts: '2026-07-31T22:01:00Z', actor: 'gcrft123', action: 'set_kb_refresh', label: "Changed a source's refresh schedule", destructive: false, target_type: 'kb_source', target_id: '4', after: { refresh_hours: 5 } },
    { id: -6, ts: '2026-07-30T14:26:00Z', actor: 'gcrft123', action: 'reindex_search', label: 'Started a re-index', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: null },
    { id: -7, ts: '2026-07-29T09:52:00Z', actor: 'gcrft123', action: 'delete_kb_source', label: 'Removed a knowledge source', destructive: true, target_type: 'kb_source', target_id: '12', after: null },
    { id: -8, ts: '2026-07-28T20:15:00Z', actor: 'gcrft123', action: 'update_proactivity', label: 'Changed proactivity', destructive: false, target_type: 'guild', target_id: '1321947496179568680', after: null },
    { id: -9, ts: '2026-07-27T11:40:00Z', actor: 'gcrft123', action: 'set_pin_actions', label: 'Changed what needs the PIN', destructive: false, target_type: 'guild_config', target_id: '1321947496179568680', after: null },
    { id: -10, ts: '2026-07-26T16:03:00Z', actor: 'gcrft123', action: 'update_keys', label: 'Updated API keys', destructive: false, target_type: 'app_secret', target_id: '1', after: null },
  ],
}

// ── Dev-only: first-run setup ──────────────────────────────────────────────────────────────
// `SETUP_MOCK=1 USAGE_MOCK=1 npm run dev` opens on the setup wizard instead of the console, and
// answers every call the wizard makes after a delay close to the real one. Nothing persists: a
// reload starts setup over, and finishing lands in the mock console (or, after a server deploy,
// the server control panel). `SETUP_MOCK=second` sets up a second bot instead of the first, so
// the deploy step offers the server another bot already runs on.
//
// A value starting with "bad" takes that step's failure path: a token (Discord rejects it), a
// client secret or Gemini key (not accepted), a Tailscale key (Funnel refuses; on the deploy
// step, Tailscale refuses it and the bot deploys with no console address, which the control
// panel then offers to fix with a new key), a VM address (the deploy fails with its install
// log, or the connect can't reach it). Connecting to a VM whose address ends in .9 finds two
// installs and asks which bot this is.
//
// A token containing "intents" belongs to an app whose intents setup can't switch on, so the
// Bot step waits for the operator to; they come on 8 seconds in. The redirect URLs register
// themselves 5 seconds after that wait, and the bot joins a server 4 seconds after those.
let SETUP = ''
// `FRESH_MOCK=1` is a console just after setup: every channel off and no Gemini key, so the
// Get started list and the Channels warning show. `FRESH_MOCK=refused` is a bot Discord
// refused (Message Content Intent off), which lands on "No servers yet" until Turn on and
// reconnect; `FRESH_MOCK=refused-console` is the same bot with its servers still listed, so
// the sidebar's bot card shows it.
let FRESH = ''
let MOCK_ROLE = ''
// `BOT_MOCK` holds the bot in one state, so the sidebar's drawer can be seen in each. `updating`
// is a VM part-way through moving onto a release, the one state a bot on this machine can't
// get into.
let BOT = ''
// `USAGE_STATE` opens the Usage page on one of its states (see USAGE_SCENES).
let USAGE = ''
const MOCK_OK = { available: true, running: true, ready: true, can_power: true }
function mockBot() {
  switch (BOT) {
    case 'updating': return { ...MOCK_OK, updating: { to: '2.0' } }
    case 'starting': return { ...MOCK_OK, ready: false }
    case 'offline': return { ...MOCK_OK, running: false, ready: false }
    case 'refused': return { ...MOCK_OK, running: false, ready: false, error: { kind: 'token' } }
    default: return MOCK_OK   // 'limited' is online, with every model parked (see mockLive)
  }
}
const FRESH_STATE = { reconnected: false }
// `consoleErr`: the server bot deployed with a key Tailscale refused, until a new one is given.
const SETUP_STATE = { done: '' as '' | 'local' | 'server', unread: false, consoleErr: '', bootAt: 0 }
const MOCK_KEY_REFUSED = 'Tailscale rejected the auth key: it has expired, was revoked, or was already used. Use a new one.'
// When each thing the wizard waits on was first polled for, so it can "happen" a few seconds
// later as if the operator had done it: the intents coming on, the redirect URLs being added,
// then the bot joining a server. Each wait starts once the one before it is over.
const SETUP_WAIT: Record<string, number> = {}
const waited = (what: string, ms: number) => {
  SETUP_WAIT[what] ??= Date.now()
  return Date.now() - SETUP_WAIT[what] >= ms
}
// A made-up application, so the invite link in a published demo can't add a real bot.
const MOCK_APP_ID = '1100000000000000001'
// The address remote access last came up at, so the redirect the wizard asks for is the one
// that gets "registered", whatever device name was typed.
const SETUP_TUNNEL = { url: 'https://olisar.tail4f2a.ts.net' }
// The device name remote access runs under, on this machine and on the server. A rename moves
// the address, and Discord "lists" the new sign-in address 8 seconds later. A name starting
// with "bad" fails as Tailscale would, and "taken" comes back as taken-1, the name Tailscale
// gives a device when another in the tailnet already has the one it asked for.
const RENAMED = { local: 'olisar', server: 'olisar' }
const tsUrl = (node: string) => `https://${node}.tail4f2a.ts.net`
function mockRename(where: 'local' | 'server', raw: unknown): { ok: true; url: string; note: string } | { ok: false; error: string } {
  const name = String(raw ?? '').trim().toLowerCase()
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(name)) {
    return { ok: false, error: 'Use letters, numbers and hyphens, starting and ending with a letter or number.' }
  }
  if (name.startsWith('bad')) return { ok: false, error: 'Couldn’t rename it: timed out bringing up the tunnel' }
  const got = name === 'taken' ? 'taken-1' : name
  RENAMED[where] = got
  SETUP_WAIT[`renamed-${where}`] = Date.now()
  const note = got === name ? '' : `Another device in your tailnet is already called ${name}, so Tailscale named this one ${got}.`
  return { ok: true, url: tsUrl(got), note }
}
const redirectListed = (where: 'local' | 'server') => !SETUP_WAIT[`renamed-${where}`] || waited(`renamed-${where}`, 8000)
function mockSetupApp(token: string, polled: boolean, origin = '') {
  const intentsOn = !token.includes('intents') || (polled && waited('intents', 8000))
  const redirectsIn = polled && intentsOn && waited('redirects', 5000)
  return {
    app: {
      id: MOCK_APP_ID, username: 'Olisar', avatar: '', bot_public: true, code_grant: false,
      intents_missing: intentsOn ? [] : ['message_content', 'members'],
      redirect_uris: redirectsIn
        ? [`${origin}/auth/callback`, `${SETUP_TUNNEL.url}/auth/callback`]
        : [],
      invite_url: `https://discord.com/oauth2/authorize?client_id=${MOCK_APP_ID}&scope=bot+applications.commands&permissions=274878024768`,
    },
    joined: redirectsIn && waited('invite', 4000),
  }
}
const MOCK_PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHq7mZ0x3cN8vWkq2d1p5sQyR4tLb9uFjE6aGhYcTzUo olisar-app'
const MOCK_INSTALL_LOG = [
  '==> Checking the VM', 'Ubuntu 22.04.4 LTS (aarch64), 23 GB free',
  '==> Installing Docker', 'docker 27.3.1 installed',
  '==> Writing the config to ~/olisar/.env',
  '==> Pulling ghcr.io/gcrft123/olisar:2.0.0-beta.1',
  'Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: temporary failure in name resolution',
].join('\n')

// What the server's bot has been doing (the final screen's memories), in the shape
// /api/server/activity answers with. A few are there from the start; a new one turns up every
// ten to twenty seconds while the screen is open.
const PEOPLE = ['DadBodNerd', 'quietmoon', 'Kestrel', 'vex.io', 'marisol', 'NightOwl_88', 'Tobi', 'hollowpoint', 'a_very_long_discord_display_name_indeed', 'ferrocene', 'Juniper', 'lumen_dust']
const who = (name: string) => ({ name, avatar: '' })
const ACTIVITY_POOLS: Record<string, any[]> = {
  reply: [
    { trigger: 'ask', who: who('DadBodNerd'), where: '#general', ask: 'when’s the Pyro run again, and do I need to bring anything?', text: 'Pyro run is Friday at 20:00 UTC. Meet at Checkmate and bring medpens. Fuel’s on the org this time.' },
    { trigger: 'name', who: who('quietmoon'), where: '#off-topic', ask: 'olisar can you ping the whole org about tonight?', text: 'It’s 3:40 in the morning in JST, so maybe hold that ping until later. I can post it at 18:00 your time instead.' },
    { trigger: 'mention', who: who('Kestrel'), where: '#help-and-questions', ask: 'which has the better med bay, the Carrack or the Cutlass Red?', text: 'The Carrack’s med bay can respawn you. The Cutlass Red’s can’t, but it gets to a downed crewmate faster.' },
    { trigger: 'reply', who: who('vex.io'), where: '#general', ask: 'doesn’t that change if we’re carrying cargo though?', text: 'Fair point. The quantum fuel math changes once you’re hauling cargo. Budget about a fifth more for a full hold.' },
    { trigger: 'proactive', who: who('marisol'), where: '#help-and-questions', ask: 'anyone know how to fix a stuck hangar elevator?', text: 'Someone asked this last week, and the answer’s pinned in this channel: leave the hangar, wait a minute, then call the elevator again.' },
    { trigger: 'catchup', who: who('NightOwl_88'), where: '#general', ask: '/catchup hours: 24', text: 'Since yesterday: the op moved to Friday, Tobi’s ship is back, and ICA is recruiting. DadBodNerd is running movie night again.' },
    { trigger: 'ask', who: who('hollowpoint'), where: '#lobby', ask: 'is mining worth it for a solo player?', text: 'Rent a Prospector first. Two runs will tell you whether mining is for you, and you won’t be out the price of a ship.' },
    { trigger: 'name', who: who('a_very_long_discord_display_name_indeed'), where: '#off-topic', ask: 'olisar do you remember which hull I wanted?', text: 'I remember. You wanted the Hull C, not the Hull A.' },
  ],
  member: [{ who: who('ferrocene'), roles: ['Recruit'] }, { who: who('Juniper'), roles: ['Recruit'] }, { who: who('Kestrel'), roles: ['Recruit', 'Member'] }],
  impression: [
    { who: who('quietmoon'), messages: 15, text: 'Quiet until it matters, and knows the Pyro jump routes cold. Usually around late evenings JST. Prefers short answers with a source, and flies a Constellation with two regulars from the org.' },
    { who: who('DadBodNerd'), messages: 22, text: 'Runs the Friday movie nights and most of the org ops. Dry sense of humour, answers questions before they finish being asked, and would rather be given the short version.' },
  ],
  remembered: [
    { who: who('DadBodNerd'), where: '#general', type: 'fact', text: 'Flies a Carrack named Long Way Round.', said: 'the Carrack’s name is Long Way Round btw, don’t let vex rename it again' },
    { who: who('Kestrel'), where: '#general', type: 'preference', text: 'Prefers voice over text for anything long.', said: 'honestly just hop in voice if it’s longer than a paragraph' },
  ],
  glossary: [
    { subject: 'ICA', text: 'ICA is short for Ironclad Assault.', where: '#general' },
    { subject: 'Checkmate', text: 'Checkmate is where the org meets up in Pyro.', where: '#general' },
  ],
  status: [{ text: 'watching the stars', how: 'Set when it started' }, { text: 'counting quantum fuel', how: 'Set during a conversation in #general' }],
  learned: [
    { title: 'Comm-Link', url: 'https://robertsspaceindustries.com/comm-link', count: 214, who: who('DadBodNerd'), how: 'site' },
    { title: '', url: 'https://starcitizen.tools/Pyro', count: 96, who: who('NightOwl_88'), how: 'page' },
  ],
  reminder: [{ who: who('DadBodNerd'), where: '#general', text: 'Fuel the Carrack before the op.' }],
  image: [{ who: who('hollowpoint'), where: '#off-topic', text: 'a Carrack over microTech at dusk' }],
}
const ACTIVITY = { seeded: 0, items: [] as any[], next: 0, seq: 0, used: {} as Record<string, number> }
function mockActivity() {
  const now = Date.now()
  const iso = (ago: number) => new Date(now - ago * 1000).toISOString()
  const make = (kind: string, data: any, ago = 0) => ({ id: `${kind}:${++ACTIVITY.seq}`, kind, at: iso(ago), ...data })
  if (!ACTIVITY.seeded) {
    const P = ACTIVITY_POOLS
    ACTIVITY.items = [
      make('reply', P.reply[0], 40), make('impression', P.impression[0], 190), make('reply', P.reply[1], 320),
      make('member', P.member[2], 480), make('remembered', P.remembered[0], 730), make('status', P.status[0], 1140),
      make('learned', P.learned[0], 2400), make('glossary', P.glossary[0], 3000),
    ]
    ACTIVITY.used = { reply: 1, impression: 0, member: 2, remembered: 0, status: 0, learned: 0, glossary: 0 }
    ACTIVITY.seeded = now
    ACTIVITY.next = now + 12000
  } else if (now >= ACTIVITY.next) {
    const kinds = ['reply', 'reply', 'reply', 'reply', 'impression', 'remembered', 'member', 'glossary', 'status', 'learned', 'reminder', 'image']
    const kind = kinds[Math.floor(Math.random() * kinds.length)]
    const pool = ACTIVITY_POOLS[kind]
    const i = ACTIVITY.used[kind] = ((ACTIVITY.used[kind] ?? -1) + 1) % pool.length
    ACTIVITY.items.unshift(make(kind, pool[i]))
    ACTIVITY.items.length = Math.min(ACTIVITY.items.length, 24)
    ACTIVITY.next = now + 10000 + Math.random() * 10000
  }
  return {
    ok: true, supported: true, items: ACTIVITY.items,
    members: { count: 176, at: new Date((SETUP_STATE.bootAt || now) + 20000).toISOString(), faces: PEOPLE.slice(0, 11).map(who) },
    health: { vec: true, sandbox: true, model: 'ok' },
  }
}

function setupMock(req: any, url: string, send: (obj: unknown, status?: number) => void): boolean {
  const later = (ms: number, fn: () => void) => { setTimeout(fn, ms); return true }
  const body = (fn: (b: any) => void) => {
    let raw = ''
    req.on('data', (c: any) => { raw += c })
    req.on('end', () => { let b: any = {}; try { b = JSON.parse(raw || '{}') } catch { /* empty */ } fn(b) })
    return true
  }
  const bad = (v: unknown) => typeof v === 'string' && v.trim().toLowerCase().startsWith('bad')
  const finish = (as: 'local' | 'server') => {
    SETUP_STATE.done = as; SETUP_STATE.unread = true
    if (as === 'server') { SETUP_STATE.bootAt = Date.now(); ACTIVITY.seeded = 0 }
  }
  // `SETUP_MOCK=server`: an install that finished long ago, on a server up for a few hours.
  if (SETUP === 'server' && !SETUP_STATE.done) {
    SETUP_STATE.done = 'server'
    SETUP_STATE.bootAt = Date.now() - (3 * 3600 + 720) * 1000
    SETUP_WAIT.signin = Date.now() - 60000
  }

  if (url.startsWith('/api/setup/status')) {
    // The read straight after finishing sees the finished install, which is what routes the
    // wizard into the console. Any read after that is a reload, and starts setup over.
    const done = SETUP_STATE.unread || SETUP === 'server' ? SETUP_STATE.done : ''
    SETUP_STATE.unread = false
    if (!done) {
      SETUP_STATE.done = ''
      for (const k of Object.keys(SETUP_WAIT)) delete SETUP_WAIT[k]
    }
    return send({
      configured: !!done, local_url: 'http://localhost:8723', redirect_uri: 'http://localhost:8723/auth/callback',
      tunnel_enabled: false, hosting_mode: done === 'server' ? 'server' : 'local', ...(done ? {} : { prefill: {} }),
    }), true
  }
  // A local setup lands on sign-in, as a real install does: nobody is signed in yet.
  if (SETUP_STATE.done === 'local' && (url === '/api/me' || url.startsWith('/api/member/session'))) {
    return send({ detail: 'not signed in' }, 401), true
  }
  if (url.startsWith('/api/bots/share-server')) return body(() => later(1400, () => send({
    ok: true, host: '203.0.113.9', user: 'ubuntu', admin_allowlist: 'gcrft123',
  })))
  if (/^\/api\/bots\/[^/]+\/pubkey/.test(url)) return later(500, () => send({ public_key: MOCK_PUBKEY }))
  if (url.startsWith('/api/bots')) {
    const first = SETUP !== 'second'
    const configured = !!SETUP_STATE.done
    const me = { id: first ? 'default' : 'e5f6a7b8', name: first ? 'Olisar' : 'Staging bot', created: true, state: 'ready',
      configured, hosting_mode: SETUP_STATE.done === 'server' ? 'server' : 'local', server_host: SETUP_STATE.done === 'server' ? '203.0.113.9' : '',
      bot: { running: configured, ready: configured, id: '', name: '', avatar: '' } }
    const others = first ? [] : [
      { id: 'default', name: 'Red Nebula bot', created: true, state: 'ready', configured: true, hosting_mode: 'local', server_host: '',
        bot: { running: true, ready: true, id: '1', name: 'Red Nebula', avatar: '' } },
      { id: 'a1b2c3d4', name: 'Support bot', created: true, state: 'ready', configured: true, hosting_mode: 'server', server_host: '203.0.113.9',
        bot: { running: false, ready: false, id: '', name: '', avatar: '' } },
    ]
    if (url.startsWith('/api/bots/active')) return send({ ...me, active_id: me.id }), true
    return send({ active_id: me.id, default_id: 'default', profiles: first ? [me] : [others[0], others[1], me] }), true
  }
  if (url.startsWith('/api/setup/bot')) return body((b) => later(800, () => bad(b.token)
    ? send({ detail: 'Discord rejected that bot token' }, 400)
    : send(mockSetupApp(String(b.token), false).app)))
  if (url.startsWith('/api/setup/discord-status')) return body((b) => later(300, () => {
    const { app, joined } = mockSetupApp(String(b.token), true, req.headers?.origin || `http://${req.headers?.host}`)
    send({ ...app, guilds: joined ? [{ id: '1321947496179568680', name: 'Red Nebula Industries', icon: '' }] : [] })
  }))
  if (url.startsWith('/api/setup/secret')) return body((b) => later(500, () => send({ ok: !bad(b.client_secret) })))
  if (url.startsWith('/api/setup/gemini')) return body((b) => later(500, () => send({ ok: !bad(b.key) })))
  if (url.startsWith('/api/setup/keys')) return body(() => later(400, () => send({ ok: true })))
  if (url.startsWith('/api/setup/save')) return body(() => later(900, () => { finish('local'); send({ ok: true, redirect_uri: 'http://localhost:8723/auth/callback' }) }))
  if (url.startsWith('/api/tunnel/enable')) return body((b) => later(2200, () => bad(b.auth_key)
    ? send({ detail: 'Funnel isn’t turned on for this tailnet. Turn it on at https://login.tailscale.com/f/funnel?node=olisar, then press Enable again.' }, 400)
    : (SETUP_TUNNEL.url = `https://${(b.hostname || 'olisar').trim()}.tail4f2a.ts.net`,
      send({ ok: true, public_url: SETUP_TUNNEL.url, redirect_uri: `${SETUP_TUNNEL.url}/auth/callback` }))))
  if (url.startsWith('/api/server/pubkey')) return later(600, () => send({ public_key: MOCK_PUBKEY }))
  if (url.startsWith('/api/server/deploy')) return body((b) => later(4500, () => {
    if (bad(b.host)) return send({ ok: false, error: 'The install stopped: the VM couldn’t download the Olisar image.', log: MOCK_INSTALL_LOG })
    finish('server')
    SETUP_STATE.consoleErr = bad(/^TAILSCALE_AUTH=(.*)$/m.exec(String(b.env))?.[1]) ? MOCK_KEY_REFUSED : ''
    send({ ok: true, console_error: SETUP_STATE.consoleErr })
  }))
  if (url.startsWith('/api/server/connect')) return body((b) => later(1800, () => {
    if (bad(b.host)) return send({ ok: false, error: `Couldn't reach the VM: connection to ${b.host}:22 timed out` })
    if (String(b.host).trim().endsWith('.9') && !b.app_dir) {
      return send({ ok: false, choose: [{ dir: 'olisar', name: 'Support bot' }, { dir: 'olisar-e5f6a7b8', name: 'Staging bot' }] })
    }
    finish('server'); send({ ok: true })
  }))
  // The control panel a server deploy lands on. The container reads as starting for its first
  // few seconds, then healthy; Docker's healthcheck runs every 30 seconds.
  if (url.startsWith('/api/server/status')) {
    const boot = SETUP_STATE.bootAt || Date.now()
    const up = Date.now() - boot
    const lastCheck = up < 3000 ? 0 : boot + 3000 + Math.floor((up - 3000) / 30000) * 30000
    return send({
      configured: true, host: '203.0.113.9', auto_updating: false, reachable: true, running: true, state: 'running',
      health: up < 3000 ? 'starting' : 'healthy', version: '2.0.0-beta.1', revision: '', digest: '', logs: '',
      started_at: new Date(boot).toISOString(), health_at: lastCheck ? new Date(lastCheck).toISOString() : '',
      url: SETUP_STATE.consoleErr ? '' : tsUrl(RENAMED.server), console_error: SETUP_STATE.consoleErr,
    }), true
  }
  if (url.startsWith('/api/server/activity')) return later(500, () => send(mockActivity()))
  if (url.startsWith('/api/server/tunnel-key')) return body((b) => later(3000, () => {
    if (bad(b.key)) return send({ ok: false, error: MOCK_KEY_REFUSED })
    SETUP_STATE.consoleErr = ''
    send({ ok: true, url: tsUrl(RENAMED.server) })
  }))
  if (url.startsWith('/api/server/tunnel-node')) return body((b) => later(4000, () => send(mockRename('server', b.node))))
  // The console's sign-in address turns up registered 6 seconds after the panel first asks.
  // SETUP_MOCK=intents also has the server bot's intents off until Turn on and restart.
  if (url.startsWith('/api/server/discord')) return later(400, () => send({
    ok: true, app_id: MOCK_APP_ID, redirect: `${tsUrl(RENAMED.server)}/auth/callback`, added: waited('signin', 6000) && redirectListed('server'),
    bot_name: 'Olisar', bot_avatar: '',
    intents_missing: SETUP === 'intents' && !SETUP_WAIT.intentsFixed ? ['message_content'] : [],
  }))
  if (url.startsWith('/api/server/reconnect')) return later(1500, () => { SETUP_WAIT.intentsFixed = 1; send({ ok: true, running: true, intents_missing: [] }) })
  if (url.startsWith('/api/server/last-update')) return send({}), true
  if (url.startsWith('/api/server/logs')) return send({ ok: true, logs: 'olisar  | Logged in as Olisar#0412\nolisar  | Ready in 1 server' }), true
  if (url.startsWith('/api/server/power')) return body((b) => later(1200, () => send({ ok: true, running: b.action === 'up' })))
  return false
}

/** Answer one request from the fixture, or call `next` for anything it doesn't cover. */
export function handle(req: any, url: string, send: MockSend, next: () => void): void {
  if (SETUP && setupMock(req, url, send)) return
  if (url.startsWith('/api/setup/status')) return send({ configured: true })
  // Exact-match: `/api/me` as a prefix also swallows `/api/messages`.
  // `MOCK_ROLE=admin` signs in as a Manage Server admin rather than the operator, who
  // doesn't get the API keys.
  if (url === '/api/me' || url.startsWith('/api/me?')) {
    return MOCK_ROLE === 'admin'
      ? send({ id: '1089266822827737191', username: 'intmorg', granted_via: 'manage_guild', bot_name: 'Olisar' })
      : send({ id: '1089250623490359378', username: 'gcrft123', granted_via: 'allowlist', bot_name: 'Olisar' })
  }
  if (MOCK_ROLE === 'admin' && url.startsWith('/api/keys')) return send({ detail: "only the bot's operator can do that" }, 403)
  if (url.startsWith('/api/guilds')) return send(FRESH === 'refused' && !FRESH_STATE.reconnected ? [] : [
    { id: '1321947496179568680', name: 'Red Nebula Industries', icon: '' },
    { id: '1089266822827737190', name: 'Test Server', icon: '' },
  ])
  if (url.startsWith('/api/invite')) return send({ url: `https://discord.com/oauth2/authorize?client_id=${MOCK_APP_ID}&scope=bot+applications.commands&permissions=274878024768`, available: true })
  if (url.startsWith('/api/dev/status')) return send({ is_developer: false })
  if (url.startsWith('/api/dev/standing')) return send({ banned: false, warning: null })
  // Remote access is on, under whatever name it was last renamed to.
  if (url.startsWith('/api/tunnel/status')) {
    const at = tsUrl(RENAMED.local)
    return send({ available: true, running: true, helper: true, headless: false, hostname: at.slice(8), public_url: at })
  }
  if (url.startsWith('/api/tunnel/rename')) {
    return readBody(req, (b) => setTimeout(() => {
      const r = mockRename('local', b.hostname)
      if (!r.ok) return send({ detail: r.error }, 400)
      send({ ok: true, public_url: r.url, redirect_uri: `${r.url}/auth/callback`, note: r.note })
    }, 2500))
  }
  if (url.startsWith('/api/tunnel/discord')) {
    return send({ ok: true, app_id: MOCK_APP_ID, redirect: `${tsUrl(RENAMED.local)}/auth/callback`, added: redirectListed('local') })
  }
  if (url.startsWith('/api/bots')) {
    // Shaped like the desktop gateway's answer: one running bot, one on a server, one
    // not set up yet — every state the switcher and Settings ▸ Bots draw.
    const bots = [
      { id: 'default', name: 'Red Nebula bot', created: true, state: 'ready', configured: true, hosting_mode: 'local', server_host: '',
        bot: { running: true, ready: true, id: '1', name: 'Red Nebula', avatar: '' } },
      { id: 'a1b2c3d4', name: 'Support bot', created: true, state: 'ready', configured: true, hosting_mode: 'server', server_host: '203.0.113.9',
        bot: { running: false, ready: false, id: '', name: '', avatar: '' } },
      { id: 'e5f6a7b8', name: 'Staging bot', created: true, state: 'ready', configured: false, hosting_mode: 'local', server_host: '',
        bot: { running: false, ready: false, id: '', name: '', avatar: '' } },
    ]
    if (url.startsWith('/api/bots/active')) return send({ ...bots[0], active_id: 'default' })
    return send({ active_id: 'default', default_id: 'default', profiles: bots })
  }
  // Operator power card: online + ready so USAGE_MOCK can also show the rate-limit
  // amber state (driven by mockLive().exhausted) without a running Discord gateway.
  if (url.startsWith('/api/bot/reconnect')) {
    FRESH_STATE.reconnected = true
    return send({ available: true, running: true, ready: false, can_power: true, error: null, intents_missing: [], app_id: MOCK_APP_ID })
  }
  if (url.startsWith('/api/bot/status') || url.startsWith('/api/bot/power')) {
    if (FRESH.startsWith('refused') && !FRESH_STATE.reconnected) {
      return send({ available: true, running: false, ready: false, can_power: true,
        error: { kind: 'intents', missing: ['message_content'], app_id: MOCK_APP_ID } })
    }
    return send(mockBot())
  }
  // Running a beta, so switching to Stable shows the "you'll stay on it" line.
  if (url.startsWith('/api/settings/updates/channel')) {
    let raw = ''
    req.on('data', (c: any) => { raw += c })
    req.on('end', () => {
      try { MOCK_UPDATES.channel = JSON.parse(raw || '{}').channel || MOCK_UPDATES.channel } catch { /* keep it */ }
      send({ channel: MOCK_UPDATES.channel })
    })
    return
  }
  if (url.startsWith('/api/settings/updates')) return send(MOCK_UPDATES)
  if (url.startsWith('/api/settings/desktop')) return send({ show_in_menu_bar: true })
  // A parked blank reply, reached by the "Report this" button Olisar puts on one.
  // Open http://localhost:5173/?report=expired for the other half of this — the
  // link that has aged out, which is what most late clicks will hit.
  if (url.startsWith('/api/settings/report/')) {
    if (url.endsWith('/expired')) {
      return send({ detail: 'That report link isn’t valid any more.' }, 404)
    }
    return send({
      prompt: 'what was that link someone posted about the fleet week schedule',
      trigger: 'mention', when: new Date(Date.now() - 3600_000).toISOString(),
      server: 'Red Nebula Industries', channel: 'general', has_logs: true,
    })
  }
  if (url.startsWith('/api/settings/pin')) {
    const method = req.method || 'GET'
    if (method === 'DELETE') {
      MOCK_PIN.is_set = false
      MOCK_PIN.updated_at = null
      return send({ ok: true })
    }
    if (method === 'PUT') {
      let raw = ''
      req.on('data', (c: any) => { raw += c })
      req.on('end', () => {
        try {
          const body = JSON.parse(raw || '{}')
          if (body.pin) { MOCK_PIN.is_set = true; MOCK_PIN.updated_at = new Date().toISOString() }
          if (body.timeout_sec) MOCK_PIN.timeout_sec = Number(body.timeout_sec)
        } catch { /* a malformed body just changes nothing */ }
        send({ ok: true })
      })
      return
    }
    return send(MOCK_PIN)
  }
  if (url.startsWith('/api/usage/live')) {
    // Not responding: the first reading lands, and then the bot stops answering.
    if (USAGE === 'stale' && ++usageLivePolls > 2) return send({ detail: 'Bad gateway' }, 502)
    return send(mockLive())
  }
  if (url.startsWith('/api/usage/summary')) return send(mockSummary())

  // ── Config pages ────────────────────────────────────────────────────
  // Writes are accepted and discarded: the fixture exists to render states, not to
  // persist them. Every payload mirrors the real serializer's shape exactly — a
  // fixture that returns a *convenient* shape hides the drift it should expose.
  // PATCH belongs here too. Without it a PATCH fell past this block into the GET
  // matchers, where /api/knowledge/1/schedule matched the /api/knowledge prefix and
  // came back 200 with the whole sources array — a write that "succeeded" by being
  // answered as a read, which is exactly the drift this fixture is supposed to expose.
  // A value starting with "bad" fails its check, and a blank one checks the saved key. With no
  // account ID typed or saved, the token can't find its own, like the Workers AI template's.
  if (url.startsWith('/api/keys/check/')) {
    readBody(req, (b) => setTimeout(() => {
      const bad = (v: string) => String(v || '').trim().toLowerCase().startsWith('bad')
      const saved = (f: string) => !!keys[f]?.dashboard
      if (url.includes('gemini')) {
        if (!b.key && !saved('gemini_api_key')) return send({ set: false, ok: false })
        return send({ set: true, ok: !bad(b.key) })
      }
      if (!b.token && !saved('cloudflare_api_token')) return send({ set: false, ok: false, problem: '' })
      if (bad(b.token)) return send({ set: true, ok: false, problem: 'token' })
      if (!b.account_id && !saved('cloudflare_account_id')) return send({ set: true, ok: false, account_id: '', problem: 'account' })
      send(bad(b.account_id) ? { set: true, ok: false, problem: 'account' } : { set: true, ok: true, problem: '' })
    }, 600))
    return
  }
  if (url.startsWith('/api/channels') && req.method === 'PUT') {
    return readBody(req, (b) => {
      const c = channels.find((x) => String(x.channel_id) === String(b.channel_id))
      if (c && b.mode) c.mode = b.mode
      if (c && typeof b.indexed === 'boolean') c.indexed = b.indexed
      send({ ok: true })
    })
  }
  if (url === '/api/keys' && req.method === 'PUT') {
    return readBody(req, (b) => {
      for (const [k, v] of Object.entries(b)) if (keys[k] && String(v || '').trim()) keys[k].dashboard = true
      send({ ok: true })
    })
  }
  if (url.startsWith('/api/keys/') && req.method === 'DELETE') {
    const k = url.slice('/api/keys/'.length)
    if (keys[k]) keys[k].dashboard = false
    return send({ ok: true })
  }
  if (['PUT', 'POST', 'PATCH', 'DELETE'].includes(req.method || '')) {
    if (url.startsWith('/api/')) return send({ ok: true })
  }
  // The marketplace browse view. Without these it could only ever render its error
  // state in mock mode, so the list itself went unreviewed.
  if (url.startsWith('/api/marketplace/search')) return send({ results: MOCK_MARKET })
  if (url.startsWith('/api/marketplace/publisher')) return send({ registered: true, handle: 'rednebula', verified: false })
  if (url.startsWith('/api/marketplace/published') || url.startsWith('/api/marketplace/installed')) return send({})
  if (url.startsWith('/api/persona')) return send(MOCK_PERSONA)
  if (url.startsWith('/api/config')) return send(MOCK_CONFIG)
  if (url.startsWith('/api/proactivity')) return send(MOCK_PROACTIVITY)
  if (url.startsWith('/api/models')) return send(MOCK_MODELS)
  if (url.startsWith('/api/messages')) return send(mockMessages())
  if (url.startsWith('/api/channels')) return send(channels)
  if (url.startsWith('/api/roles')) return send(MOCK_ROLES)
  if (url.startsWith('/api/profiles')) return send(MOCK_PROFILES)
  if (url.startsWith('/api/knowledge/reindex/status')) return send(MOCK_REINDEX)
  if (url.startsWith('/api/knowledge')) return send(MOCK_KNOWLEDGE)
  if (url.startsWith('/api/facts')) return send(MOCK_FACTS)
  if (url.startsWith('/api/extensions')) return send(MOCK_EXTENSIONS)
  if (url.startsWith('/api/keys')) return send(keys)
  if (url.startsWith('/api/audit')) return send(MOCK_AUDIT)
  if (url.startsWith('/api/stats')) return send({ today: { requests: 4120, grounding: 38 }, by_model: {} })
  if (url.startsWith('/api/settings/remote')) {
    const at = tsUrl(RENAMED.local)
    return send({
      status: {
        available: true, running: true, helper: true, headless: false, hostname: at.slice(8), public_url: at,
        // An admin who isn't the operator signs in over the funnel, not at the operator's machine.
        local: MOCK_ROLE !== 'admin',
      },
      logs: [],
      users: [
        { username: 'gcrft123', granted_via: 'allowlist', is_allowlisted: true, last_login: new Date(Date.now() - 3600000).toISOString(), guild_count: 2 },
        { username: 'intmorg', granted_via: 'manage_guild', is_allowlisted: false, last_login: new Date(Date.now() - 86400000 * 2).toISOString(), guild_count: 1 },
      ],
    })
  }
  if (url.startsWith('/api/settings/logs')) return send({ lines: ['[fixture] no live log in mock mode'] })
  next()
}
