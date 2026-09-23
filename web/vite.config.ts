import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// ── Dev-only fixture ─────────────────────────────────────────────────────────
// `USAGE_MOCK=1 npm run dev` serves canned API responses for the whole console — every
// page renders fully populated with NO backend, no database and no OAuth. Off by default
// (normal dev proxies to :8000, prod is unaffected); this only activates on the env var.
//
// It used to cover four endpoints, and everything else fell through to Vite's static
// handler and came back as HTML — so most pages sat on a spinner forever and Command
// replies took the error boundary. Anything a reviewer can't reach is a page nobody
// reviews. Payloads mirror the real serializers field for field, deliberately: a fixture
// that returns a convenient shape hides exactly the drift it should expose.
const MOCK = !!process.env.USAGE_MOCK

function mockSummary(days: number) {
  // days=0 is all-time; the real endpoint derives the window from the earliest recorded
  // day and buckets past ~10 weeks. 400 days here so "Forever" exercises the bucketing.
  const allTime = days === 0
  days = allTime ? 400 : Math.max(1, Math.min(days, 30))
  // Full fallback roster: the top few have usage; the rest are idle chain models.
  const roster: any[] = [
    { model: 'gemini-flash-latest', cap: 10, role: 'chat', base: 520, growth: 780, tpr: 1400, peak: 8 },
    { model: 'gemini-flash-lite-latest', cap: 15, role: 'chat', base: 360, growth: 620, tpr: 900, peak: 6 },
    { model: 'gemini-embedding-001', cap: 100, role: 'embed', base: 400, growth: 520, tpr: 120, peak: 12 },
    { model: 'gemini-2.0-flash', cap: 15, role: 'chat', base: 80, growth: 200, tpr: 1600, peak: 3 },
    { model: 'gemini-3.5-flash', cap: 10, role: 'chat' },
    { model: 'gemini-3-flash-preview', cap: 10, role: 'chat' },
    { model: 'gemini-2.5-flash', cap: 10, role: 'chat' },
    { model: 'gemini-3.1-flash-lite', cap: 15, role: 'chat' },
    { model: 'gemini-2.5-flash-lite', cap: 15, role: 'chat' },
    { model: 'gemini-2.0-flash-lite', cap: 30, role: 'chat' },
  ]
  const active = roster.filter((m) => m.base)
  const daily: any[] = []
  for (let i = 0; i < days; i++) {
    const d = new Date()
    d.setUTCHours(0, 0, 0, 0)
    d.setUTCDate(d.getUTCDate() - (days - 1 - i))
    const frac = days > 1 ? i / (days - 1) : 1
    const by_model: Record<string, number> = {}
    let requests = 0, tokens = 0
    for (const m of active) {
      const v = Math.max(0, Math.round(m.base + m.growth * frac + Math.sin(i * 1.3 + m.cap) * 24))
      by_model[m.model] = v
      requests += v
      tokens += v * m.tpr
    }
    daily.push({
      day: d.toISOString().slice(0, 10),
      requests, tokens,
      peak_tpm: Math.round(140000 + 300000 * frac + Math.sin(i) * 35000),
      by_model,
    })
  }
  const last = daily[daily.length - 1]
  const total = daily.reduce((s, d) => s + d.requests, 0)
  const step = days <= 70 ? 1 : days <= 730 ? 7 : 30
  const series = step === 1 ? daily : daily.reduce((acc: any[], d, i) => {
    if (i % step === 0) acc.push({ ...d, by_model: { ...d.by_model } })
    else {
      const b = acc[acc.length - 1]
      b.requests += d.requests; b.tokens += d.tokens
      b.peak_tpm = Math.max(b.peak_tpm, d.peak_tpm)
      for (const k of Object.keys(d.by_model)) b.by_model[k] = (b.by_model[k] || 0) + d.by_model[k]
    }
    return acc
  }, [])
  const by_model = roster
    .map((m) => {
      const reqW = daily.reduce((s, d) => s + (d.by_model[m.model] || 0), 0)
      return {
        model: m.model, cap: m.cap, role: m.role, requests: reqW, tokens: m.tpr ? reqW * m.tpr : 0,
        requests_today: last.by_model[m.model] || 0,
        tokens_today: (last.by_model[m.model] || 0) * (m.tpr || 0),
        peak_rpm_today: m.peak || 0,
      }
    })
    .sort((a, b) => b.requests - a.requests)
  const shares: [string, number][] = [
    ['conversation', 0.34], ['embed', 0.26], ['summary', 0.14], ['persona', 0.09],
    ['glossary', 0.06], ['vision', 0.05], ['grounding', 0.03], ['proactivity', 0.03], ['canary', 0.01],
  ]
  return {
    window_days: days,
    all_time: allTime,
    start: daily[0].day,
    bucket_days: step,
    today: { requests: last.requests, tokens: last.tokens, grounding: 38 },
    peak: { rpm: { value: 8, cap: 10, model: 'gemini-flash-latest' }, tpm: last.peak_tpm, tpm_limit: 1000000 },
    daily: series,
    by_model,
    by_source: shares.map(([source, f]) => ({ source, requests: Math.round(total * f) })).sort((a, b) => b.requests - a.requests),
  }
}

function mockLive() {
  const jitter = (n: number) => Math.max(0, Math.round(n + (Math.sin(Date.now() / 3000) * 2)))
  return {
    ts: new Date().toISOString(),
    exhausted: false,
    models: [
      { model: 'gemini-flash-latest', rpm: jitter(7), cap: 10, cooldown: false },
      { model: 'gemini-flash-lite-latest', rpm: jitter(4), cap: 15, cooldown: false },
      { model: 'gemini-embedding-001', rpm: jitter(9), cap: 100, cooldown: false },
    ],
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
    ['tool_pin_prompt', 'A PIN is required for Olisar to run **{tool}**. See Settings > Security in the console or ask an admin if you don\'t have access.', ['tool', 'seconds']],
    ['privacy', '**How Olisar handles your data**\n…', []],
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
// Tailscale key (Funnel refuses), a VM address (the deploy fails with its install log, or the
// connect can't reach it). Connecting to a VM whose address ends in .9 finds two installs and
// asks which bot this is.
const SETUP = process.env.SETUP_MOCK || ''
const SETUP_STATE = { done: '' as '' | 'local' | 'server', unread: false }
const MOCK_PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHq7mZ0x3cN8vWkq2d1p5sQyR4tLb9uFjE6aGhYcTzUo olisar-app'
const MOCK_INSTALL_LOG = [
  '==> Checking the VM', 'Ubuntu 22.04.4 LTS (aarch64), 23 GB free',
  '==> Installing Docker', 'docker 27.3.1 installed',
  '==> Writing the config to ~/olisar/.env',
  '==> Pulling ghcr.io/gcrft123/olisar:2.0.0-beta.1',
  'Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: temporary failure in name resolution',
].join('\n')

function setupMock(req: any, url: string, send: (obj: unknown, status?: number) => void): boolean {
  const later = (ms: number, fn: () => void) => { setTimeout(fn, ms); return true }
  const body = (fn: (b: any) => void) => {
    let raw = ''
    req.on('data', (c: any) => { raw += c })
    req.on('end', () => { let b: any = {}; try { b = JSON.parse(raw || '{}') } catch { /* empty */ } fn(b) })
    return true
  }
  const bad = (v: unknown) => typeof v === 'string' && v.trim().toLowerCase().startsWith('bad')
  const finish = (as: 'local' | 'server') => { SETUP_STATE.done = as; SETUP_STATE.unread = true }

  if (url.startsWith('/api/setup/status')) {
    // The read straight after finishing sees the finished install, which is what routes the
    // wizard into the console. Any read after that is a reload, and starts setup over.
    const done = SETUP_STATE.unread ? SETUP_STATE.done : ''
    SETUP_STATE.unread = false
    if (!done) SETUP_STATE.done = ''
    return send({
      configured: !!done, local_url: 'http://localhost:8723', redirect_uri: 'http://localhost:8723/auth/callback',
      tunnel_enabled: false, hosting_mode: done === 'server' ? 'server' : 'local', ...(done ? {} : { prefill: {} }),
    }), true
  }
  if (url.startsWith('/api/bots/share-server')) return body(() => later(1400, () => send({
    ok: true, host: '203.0.113.9', user: 'ubuntu', tailscale_auth: 'tskey-auth-kSh4r3dExample-1a2b3c', admin_allowlist: 'gcrft123',
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
  if (url.startsWith('/api/setup/validate-token')) return body((b) => later(800, () => bad(b.token)
    ? send({ detail: 'Discord rejected that bot token' }, 400)
    : send({ ok: true, id: '1537976722840887296', username: 'Olisar' })))
  if (url.startsWith('/api/setup/keys')) return body(() => later(400, () => send({ ok: true })))
  if (url.startsWith('/api/setup/save')) return body(() => later(900, () => { finish('local'); send({ ok: true, redirect_uri: 'http://localhost:8723/auth/callback' }) }))
  if (url.startsWith('/api/tunnel/enable')) return body((b) => later(2200, () => bad(b.auth_key)
    ? send({ detail: 'Funnel isn’t turned on for this tailnet. Turn it on at https://login.tailscale.com/f/funnel?node=olisar, then press Enable again.' }, 400)
    : send({ ok: true, public_url: `https://${(b.hostname || 'olisar').trim()}.tail4f2a.ts.net`, redirect_uri: `https://${(b.hostname || 'olisar').trim()}.tail4f2a.ts.net/auth/callback` })))
  if (url.startsWith('/api/server/pubkey')) return later(600, () => send({ public_key: MOCK_PUBKEY }))
  if (url.startsWith('/api/server/deploy')) return body((b) => later(4500, () => {
    if (bad(b.host)) return send({ ok: false, error: 'The install stopped: the VM couldn’t download the Olisar image.', log: MOCK_INSTALL_LOG })
    finish('server'); send({ ok: true })
  }))
  if (url.startsWith('/api/server/connect')) return body((b) => later(1800, () => {
    if (bad(b.host)) return send({ ok: false, error: `Couldn't reach the VM: connection to ${b.host}:22 timed out` })
    if (String(b.host).trim().endsWith('.9') && !b.app_dir) {
      return send({ ok: false, choose: [{ dir: 'olisar', name: 'Support bot' }, { dir: 'olisar-e5f6a7b8', name: 'Staging bot' }] })
    }
    finish('server'); send({ ok: true })
  }))
  // The control panel a server deploy lands on.
  if (url.startsWith('/api/server/status')) return send({
    configured: true, host: '203.0.113.9', auto_updating: false, reachable: true, running: true, state: 'running',
    health: 'healthy', version: '2.0.0-beta.1', revision: '', digest: '', url: 'https://olisar.tail4f2a.ts.net', logs: '',
  }), true
  if (url.startsWith('/api/server/last-update')) return send({}), true
  if (url.startsWith('/api/server/logs')) return send({ ok: true, logs: 'olisar  | Logged in as Olisar#0412\nolisar  | Ready in 1 server' }), true
  if (url.startsWith('/api/server/power')) return body((b) => later(1200, () => send({ ok: true, running: b.action === 'up' })))
  return false
}

function mockPlugin(): Plugin {
  return {
    name: 'olisar-usage-mock',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url || ''
        const send = (obj: unknown, status = 200) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(obj))
        }
        if (SETUP && setupMock(req, url, send)) return
        if (url.startsWith('/api/setup/status')) return send({ configured: true })
        // Exact-match: `/api/me` as a prefix also swallows `/api/messages`.
        if (url === '/api/me' || url.startsWith('/api/me?')) return send({ id: '1089250623490359378', username: 'gcrft123', granted_via: 'allowlist' })
        if (url.startsWith('/api/guilds')) return send([
          { id: '1321947496179568680', name: 'Red Nebula Industries', icon: '' },
          { id: '1089266822827737190', name: 'Test Server', icon: '' },
        ])
        if (url.startsWith('/api/dev/status')) return send({ is_developer: false })
        if (url.startsWith('/api/dev/standing')) return send({ banned: false, warning: null })
        if (url.startsWith('/api/tunnel/status')) return send({ available: false, running: false, helper: false, headless: false, hostname: '', public_url: '' })
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
        if (url.startsWith('/api/bot/status') || url.startsWith('/api/bot/power')) {
          return send({ available: true, running: true, ready: true, can_power: true })
        }
        // Running a beta, so switching to Stable shows the "you'll stay on it" line.
        if (url.startsWith('/api/settings/updates/channel')) {
          let raw = ''
          req.on('data', (c) => { raw += c })
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
            res.statusCode = 404
            return send({ detail: 'That report link isn’t valid any more.' })
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
            req.on('data', (c) => { raw += c })
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
        if (url.startsWith('/api/usage/live')) return send(mockLive())
        if (url.startsWith('/api/usage/summary')) {
          const m = url.match(/days=(\d+)/)
          return send(mockSummary(m ? Number(m[1]) : 7))
        }

        // ── Config pages ────────────────────────────────────────────────────
        // Writes are accepted and discarded: the fixture exists to render states, not to
        // persist them. Every payload mirrors the real serializer's shape exactly — a
        // fixture that returns a *convenient* shape hides the drift it should expose.
        // PATCH belongs here too. Without it a PATCH fell past this block into the GET
        // matchers, where /api/knowledge/1/schedule matched the /api/knowledge prefix and
        // came back 200 with the whole sources array — a write that "succeeded" by being
        // answered as a read, which is exactly the drift this fixture is supposed to expose.
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
        if (url.startsWith('/api/channels')) return send(MOCK_CHANNELS)
        if (url.startsWith('/api/roles')) return send(MOCK_ROLES)
        if (url.startsWith('/api/profiles')) return send(MOCK_PROFILES)
        if (url.startsWith('/api/knowledge/reindex/status')) return send(MOCK_REINDEX)
        if (url.startsWith('/api/knowledge')) return send(MOCK_KNOWLEDGE)
        if (url.startsWith('/api/facts')) return send(MOCK_FACTS)
        if (url.startsWith('/api/extensions')) return send(MOCK_EXTENSIONS)
        if (url.startsWith('/api/keys')) return send(MOCK_KEYS)
        if (url.startsWith('/api/audit')) return send(MOCK_AUDIT)
        if (url.startsWith('/api/stats')) return send({ today: { requests: 4120, grounding: 38 }, by_model: {} })
        if (url.startsWith('/api/settings/remote')) return send({ running: false, public_url: '', sessions: [] })
        if (url.startsWith('/api/settings/logs')) return send({ lines: ['[fixture] no live log in mock mode'] })
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), ...(MOCK ? [mockPlugin()] : [])],
  server: {
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    // In dev, proxy API + auth to the FastAPI server so the browser sees a
    // single origin (:5173). This keeps the OAuth cookie/redirect flow simple —
    // leave VITE_API_BASE empty so the app calls same-origin /api and /auth.
    // With USAGE_MOCK the mock plugin answers /api itself, so skip the proxy.
    proxy: MOCK ? undefined : {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
