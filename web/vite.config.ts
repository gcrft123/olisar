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
    ['glossary', 0.06], ['vision', 0.05], ['grounding', 0.03], ['proactivity', 0.03],
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
  blocked_mentions: ['everyone', 'here'],
  allowed_role_ids: ['1321947496179568690'],
  blocked_role_ids: ['1321947496179568694'],
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

const MOCK_KNOWLEDGE = [
  { id: 1, type: 'url', uri: 'https://robertsspaceindustries.com/comm-link', title: 'RSI Comm-Link', status: 'ready', chunks: 184, error: null },
  { id: 2, type: 'website', uri: 'https://docs.example.org/handbook', title: 'Org handbook', status: 'ingesting', chunks: 26, error: null },
  { id: 3, type: 'url', uri: 'https://unreachable.example/404', title: '', status: 'error', chunks: 0, error: 'fetch failed — 404 Not Found' },
]

const MOCK_FACTS = [
  { id: 1, subject: 'MN', fact: 'Movie Night, the Friday watch-party in #general.', mentions: 14, updated_at: '2026-08-01T10:00:00Z' },
  { id: 2, subject: 'The Council', fact: "The server's moderator team.", mentions: 6, updated_at: '2026-07-21T10:00:00Z' },
  { id: 3, subject: '', fact: 'Long-haul runs leave from Port Olisar at 20:00 UTC on Saturdays.', mentions: 1, updated_at: '2026-06-02T10:00:00Z' },
]

const MOCK_REINDEX = { running: false, indexed_messages: 128_431, channels: [] }

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
  ],
}

function mockPlugin(): Plugin {
  return {
    name: 'olisar-usage-mock',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url || ''
        const send = (obj: unknown) => {
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(obj))
        }
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
        if (url.startsWith('/api/bots')) return send({
          active_id: 'default',
          default_id: 'default',
          profiles: [
            { id: 'default', name: 'Red Nebula bot', created: true },
            { id: 'a1b2c3d4', name: 'Support bot', created: true },
            { id: 'e5f6a7b8', name: 'Staging bot', created: false },
          ],
        })
        if (url.startsWith('/api/settings/updates')) return send({ current: '1.0.5', available: false })
        if (url.startsWith('/api/settings/desktop')) return send({ show_in_menu_bar: true })
        if (url.startsWith('/api/usage/live')) return send(mockLive())
        if (url.startsWith('/api/usage/summary')) {
          const m = url.match(/days=(\d+)/)
          return send(mockSummary(m ? Number(m[1]) : 7))
        }

        // ── Config pages ────────────────────────────────────────────────────
        // Writes are accepted and discarded: the fixture exists to render states, not to
        // persist them. Every payload mirrors the real serializer's shape exactly — a
        // fixture that returns a *convenient* shape hides the drift it should expose.
        if (req.method === 'PUT' || req.method === 'POST' || req.method === 'DELETE') {
          if (url.startsWith('/api/')) return send({ ok: true })
        }
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
