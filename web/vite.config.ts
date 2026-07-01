import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// ── Dev-only fixture ─────────────────────────────────────────────────────────
// `USAGE_MOCK=1 npm run dev` serves canned API responses for the shell + the Usage
// page, so the console renders fully populated with NO backend / OAuth. Off by
// default (normal dev proxies to :8000, prod is unaffected) — this only activates
// when the env var is set.
const MOCK = !!process.env.USAGE_MOCK

function mockSummary(days: number) {
  days = Math.max(1, Math.min(days, 30))
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
  const by_model = roster
    .map((m) => {
      const reqW = daily.reduce((s, d) => s + (d.by_model[m.model] || 0), 0)
      return {
        model: m.model, cap: m.cap, role: m.role, requests: reqW, tokens: m.tpr ? reqW * m.tpr : 0,
        requests_today: last.by_model[m.model] || 0, peak_rpm_today: m.peak || 0,
      }
    })
    .sort((a, b) => b.requests - a.requests)
  const shares: [string, number][] = [
    ['conversation', 0.34], ['embed', 0.26], ['summary', 0.14], ['persona', 0.09],
    ['glossary', 0.06], ['vision', 0.05], ['grounding', 0.03], ['proactivity', 0.03],
  ]
  return {
    window_days: days,
    today: { requests: last.requests, tokens: last.tokens, grounding: 38 },
    peak: { rpm: { value: 8, cap: 10, model: 'gemini-flash-latest' }, tpm: last.peak_tpm, tpm_limit: 1000000 },
    daily,
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
        if (url.startsWith('/api/me')) return send({ id: '1089250623490359378', username: 'gcrft123', granted_via: 'allowlist' })
        if (url.startsWith('/api/guilds')) return send([
          { id: '1321947496179568680', name: 'Red Nebula Industries', icon: '' },
          { id: '1089266822827737190', name: 'Test Server', icon: '' },
        ])
        if (url.startsWith('/api/dev/status')) return send({ is_developer: false })
        if (url.startsWith('/api/dev/standing')) return send({ banned: false, warning: null })
        if (url.startsWith('/api/tunnel/status')) return send({ available: false, running: false, helper: false, headless: false, hostname: '', public_url: '' })
        if (url.startsWith('/api/usage/live')) return send(mockLive())
        if (url.startsWith('/api/usage/summary')) {
          const m = url.match(/days=(\d+)/)
          return send(mockSummary(m ? Number(m[1]) : 7))
        }
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
