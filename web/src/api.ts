// Typed-ish fetch wrapper. Cookies (the session) are sent with credentials.
// In dev VITE_API_BASE points at the FastAPI origin; in prod it's same-origin.

const BASE: string = (import.meta as any).env?.VITE_API_BASE ?? ''

export class Unauthorized extends Error {}

// Called whenever any request comes back 401 — e.g. the session was revoked mid-use
// because the account lost Manage Server. App.tsx registers this to drop to the login
// screen immediately, instead of leaving a stale (and now powerless) page on screen.
let onUnauthorized: (() => void) | null = null
export function setOnUnauthorized(cb: () => void): void {
  onUnauthorized = cb
}

// The server the dashboard is currently configuring. Sent as a header on every
// request so per-server endpoints scope to it; account/global routes ignore it.
let currentGuild: string | null = null
export function setGuild(id: string | null): void {
  currentGuild = id
}

// Member-portal CSRF token, handed over once by GET /api/member/session and echoed on every
// mutating member call. The console's own session has never needed one — SameSite=Lax blocks
// cross-site POSTs — but the portal is the first surface exposing mutating routes to every
// member of every server over a public tunnel URL, and its mutations delete data.
let memberCsrf: string | null = null
export function setMemberCsrf(token: string | null): void {
  memberCsrf = token
}

// `timeoutMs` is opt-in — most calls have none (deploy/reindex legitimately run for minutes),
// but SSH-backed reads (server status/pubkey/logs) pass a short timeout so a wedged/unreachable
// backend surfaces as an error instead of hanging the UI forever.
//
// `signal` is opt-in too: a call the operator can abort (marketplace publish) passes one, and we
// link it to the timeout controller so either source can cancel the same fetch. The abort reason
// is what tells them apart afterwards — a timeout is the backend's fault and gets a message; a
// cancel is the operator's own doing and must not be reported as a failure.
async function req(path: string, opts: RequestInit & { timeoutMs?: number } = {}): Promise<any> {
  const { timeoutMs, signal: callerSignal, ...init } = opts
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (currentGuild) headers['X-Guild-Id'] = currentGuild
  // Sent on every member call rather than only the mutating ones: the server ignores it on
  // safe methods, and a per-call opt-in is a thing a future route can forget.
  if (memberCsrf && path.startsWith('/api/member')) headers['X-CSRF-Token'] = memberCsrf
  const ctrl = timeoutMs || callerSignal ? new AbortController() : null
  const timer = timeoutMs && ctrl ? setTimeout(() => ctrl.abort('timeout'), timeoutMs) : null
  const onCallerAbort = () => ctrl?.abort(callerSignal?.reason ?? 'cancelled')
  if (ctrl && callerSignal) {
    if (callerSignal.aborted) onCallerAbort()
    else callerSignal.addEventListener('abort', onCallerAbort, { once: true })
  }
  let res: Response
  try {
    res = await fetch(BASE + path, {
      credentials: 'include',
      ...init,
      ...(ctrl ? { signal: ctrl.signal } : {}),
      headers,
    })
  } catch (e: any) {
    if (ctrl?.signal.aborted) {
      if (ctrl.signal.reason === 'timeout') throw new Error('timed out — the backend didn’t respond')
      // The operator cancelled. Rethrow the AbortError untouched so the caller can tell this
      // apart from a real failure and stay quiet about it.
      throw e
    }
    // fetch() rejects with a bare TypeError for DNS, refused connections, offline and CORS.
    // That surfaced to the operator as the browser's own "Failed to fetch", and because it
    // carries a message the friendlier fallbacks downstream were dead code.
    if (e instanceof TypeError) throw new Error('couldn’t reach the backend — is it running?')
    throw e
  } finally {
    if (timer) clearTimeout(timer)
    callerSignal?.removeEventListener('abort', onCallerAbort)
  }
  if (res.status === 401) { onUnauthorized?.(); throw new Unauthorized('not authenticated') }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    let msg = body || res.statusText
    let detail: any = null
    try {
      const j = JSON.parse(body)
      if (j?.detail !== undefined && j?.detail !== null) {
        detail = j.detail
        msg = typeof j.detail === 'string'
          ? j.detail
          // FastAPI's 422 detail is an array of {loc, msg, type}. JSON.stringify put the raw
          // objects on screen; read the field name and the reason out of the first one.
          : Array.isArray(j.detail)
            ? j.detail.map((d: any) => {
                const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : null
                return field ? `${field}: ${d?.msg || 'is not valid'}` : (d?.msg || 'is not valid')
              }).join('; ')
            : (j.detail?.message || JSON.stringify(j.detail))
      }
    } catch { /* not JSON — use the raw body */ }
    const err = new Error(msg) as Error & { detail?: any }
    err.detail = detail  // structured payloads (e.g. a risk-blocked publish) ride here
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  loginUrl: () => BASE + '/auth/login',
  logout: () => req('/auth/logout', { method: 'POST' }),
  // Desktop sign-in: the app opens OAuth in the system browser and polls this loopback-only
  // endpoint to claim the session the browser created (keyed by the app's nonce).
  desktopClaim: (nonce: string) =>
    req('/auth/desktop/claim', { method: 'POST', body: JSON.stringify({ nonce }) }),

  me: () => req('/api/me'),
  guilds: () => req('/api/guilds'),
  models: () => req('/api/models'),

  getPersona: () => req('/api/persona'),
  putPersona: (b: any) => req('/api/persona', { method: 'PUT', body: JSON.stringify(b) }),

  getConfig: () => req('/api/config'),
  putConfig: (b: any) => req('/api/config', { method: 'PUT', body: JSON.stringify(b) }),

  // Wipe everything Olisar has learned about the current server (keeps persona/settings).
  clearMemory: () => req('/api/clear-memory', { method: 'POST' }),

  getMessages: () => req('/api/messages'),
  putMessages: (b: any) => req('/api/messages', { method: 'PUT', body: JSON.stringify(b) }),

  getProactivity: () => req('/api/proactivity'),
  putProactivity: (b: any) => req('/api/proactivity', { method: 'PUT', body: JSON.stringify(b) }),

  getChannels: () => req('/api/channels'),
  putChannel: (b: any) => req('/api/channels', { method: 'PUT', body: JSON.stringify(b) }),

  getRoles: () => req('/api/roles'),

  getExtensions: () => req('/api/extensions'),
  putExtension: (b: any) => req('/api/extensions', { method: 'PUT', body: JSON.stringify(b) }),
  getExtensionSettings: (key: string) => req(`/api/extensions/${key}/settings`),
  putExtensionSettings: (key: string, b: any) =>
    req(`/api/extensions/${key}/settings`, { method: 'PUT', body: JSON.stringify(b) }),

  // Extension authoring (operator-only). The SDK editor posts source + compiled JS.
  listAuthoring: () => req('/api/extensions/authoring'),
  getAuthoring: (key: string) => req(`/api/extensions/authoring/${encodeURIComponent(key)}`),
  createAuthoring: (b: any) => req('/api/extensions/authoring', { method: 'POST', body: JSON.stringify(b) }),
  updateAuthoring: (key: string, b: any) =>
    req(`/api/extensions/authoring/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify(b) }),
  deleteAuthoring: (key: string) =>
    req(`/api/extensions/authoring/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  validateAuthoring: (b: any) =>
    req('/api/extensions/authoring/validate', { method: 'POST', body: JSON.stringify(b) }),
  authoringTypes: () => req('/api/extensions/authoring/sdk-types'),

  // .olx export/import. Export returns the bundle JSON (the UI saves it as a file);
  // import is a two-step preview → confirm so the operator approves the capabilities.
  exportAuthoring: (key: string) => req(`/api/extensions/authoring/${encodeURIComponent(key)}/export`),
  importPreview: (bundle: any) =>
    req('/api/extensions/authoring/import/preview', { method: 'POST', body: JSON.stringify({ bundle }) }),
  importAuthoring: (bundle: any, granted: string[]) =>
    req('/api/extensions/authoring/import', { method: 'POST', body: JSON.stringify({ bundle, granted_permissions: granted }) }),

  // Marketplace (operator-only) — the bot proxies these to the registry. Install reuses
  // the import consent flow; the bot fetches the .olx and re-verifies it locally.
  marketplaceSearch: (q = '', category = '') =>
    req(`/api/marketplace/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`),
  marketplaceDetail: (ns: string, name: string) =>
    req(`/api/marketplace/ext/${encodeURIComponent(ns)}/${encodeURIComponent(name)}`),
  marketplaceInstallPreview: (ref: { namespace: string; name: string; version: string }) =>
    req('/api/marketplace/install/preview', { method: 'POST', body: JSON.stringify(ref) }),
  marketplaceInstall: (b: { namespace: string; name: string; version: string; granted_permissions: string[] }) =>
    req('/api/marketplace/install', { method: 'POST', body: JSON.stringify(b) }),
  marketplacePublisher: () => req('/api/marketplace/publisher'),
  marketplaceRegister: (handle: string) =>
    req('/api/marketplace/register', { method: 'POST', body: JSON.stringify({ handle }) }),
  // Takes a signal: the server-side risk review can run for a minute and the console offers
  // a Stop, which has to actually reach the backend before it ships anything.
  marketplacePublish: (key: string, opts?: { signal?: AbortSignal }) =>
    req('/api/marketplace/publish', {
      method: 'POST', body: JSON.stringify({ key }), signal: opts?.signal,
    }),
  marketplaceReview: (key: string) =>
    req('/api/marketplace/review', { method: 'POST', body: JSON.stringify({ key }) }),
  marketplacePublished: () => req('/api/marketplace/published'),
  marketplaceYank: (name: string, version?: string) =>
    req('/api/marketplace/yank', { method: 'POST', body: JSON.stringify({ name, version }) }),
  marketplaceVerifyStartUrl: () => BASE + '/api/marketplace/verify/start',
  marketplaceInstalled: () => req('/api/marketplace/installed'),
  marketplaceUpdatePreview: (key: string) =>
    req('/api/marketplace/update/preview', { method: 'POST', body: JSON.stringify({ key }) }),
  marketplaceUpdate: (key: string, granted: string[]) =>
    req('/api/marketplace/update', { method: 'POST', body: JSON.stringify({ key, granted_permissions: granted }) }),
  // Publish-block risk threshold (operator-tunable).
  marketplacePolicy: () => req('/api/marketplace/policy'),
  setMarketplacePolicy: (risk_threshold: number) =>
    req('/api/marketplace/policy', { method: 'PUT', body: JSON.stringify({ risk_threshold }) }),
  // Abuse report against a marketplace extension (→ email to the platform owner + dev console).
  marketplaceReport: (b: {
    namespace: string; name: string; version?: string | null; description: string;
    logs?: string; attachments?: { name: string; type: string; content_b64: string }[]
  }) => req('/api/marketplace/report', { method: 'POST', body: JSON.stringify(b) }),

  // Developer console (platform owner) — proxied to the registry behind the publisher token.
  devStatus: () => req('/api/dev/status'),
  devExtensions: () => req('/api/dev/extensions'),
  devReports: () => req('/api/dev/reports'),
  devBlocked: () => req('/api/dev/blocked'),
  devClearReports: () => req('/api/dev/reports/clear', { method: 'POST' }),
  devClearBlocked: () => req('/api/dev/blocked/clear', { method: 'POST' }),
  devSource: (namespace: string, name: string, version = '') =>
    req(`/api/dev/source?namespace=${encodeURIComponent(namespace)}&name=${encodeURIComponent(name)}&version=${encodeURIComponent(version)}`),
  devYank: (namespace: string, name: string, version?: string | null) =>
    req('/api/dev/yank', { method: 'POST', body: JSON.stringify({ namespace, name, version }) }),
  devModerationList: () => req('/api/dev/moderation'),
  devModeration: (discord_id: string, status: 'warn' | 'ban' | 'clear', message = '') =>
    req('/api/dev/moderation', { method: 'POST', body: JSON.stringify({ discord_id, status, message }) }),
  devStanding: () => req('/api/dev/standing'),
  devStandingAck: () => req('/api/dev/standing/ack', { method: 'POST' }),

  getKnowledge: () => req('/api/knowledge'),
  addSource: (b: any) => req('/api/knowledge', { method: 'POST', body: JSON.stringify(b) }),
  deleteSource: (id: number) => req(`/api/knowledge/${id}`, { method: 'DELETE' }),
  setSourceSchedule: (id: number, refresh_hours: number) =>
    req(`/api/knowledge/${id}/schedule`, { method: 'PATCH', body: JSON.stringify({ refresh_hours }) }),
  refreshSource: (id: number) => req(`/api/knowledge/${id}/refresh`, { method: 'POST' }),

  // Message search index (re)build + per-channel progress.
  reindex: () => req('/api/knowledge/reindex', { method: 'POST' }),
  clearIndex: () => req('/api/knowledge/reindex/clear', { method: 'POST' }),
  reindexStatus: () => req('/api/knowledge/reindex/status'),

  getUsage: (days: number) => req(`/api/usage/summary?days=${days}`),
  getUsageLive: () => req('/api/usage/live'),
  getAudit: (limit = 100) => req(`/api/audit?limit=${limit}`),

  getFacts: () => req('/api/facts'),
  addFact: (b: any) => req('/api/facts', { method: 'POST', body: JSON.stringify(b) }),
  deleteFact: (id: number) => req(`/api/facts/${id}`, { method: 'DELETE' }),
  mineFacts: () => req('/api/facts/mine', { method: 'POST' }),
  deepMineFacts: () => req('/api/facts/mine-index', { method: 'POST' }),

  // Enclosed test chat: persona + KB + tools, no memory. Send the full transcript.
  sandboxChat: (messages: { role: string; content: string }[]) =>
    req('/api/sandbox/chat', { method: 'POST', body: JSON.stringify({ messages }) }),

  getProfiles: () => req('/api/profiles'),
  buildImpression: (userId: string) => req(`/api/profiles/${userId}/impression`, { method: 'POST' }),
  getStats: () => req('/api/stats'),

  getKeys: () => req('/api/keys'),
  putKeys: (b: any) => req('/api/keys', { method: 'PUT', body: JSON.stringify(b) }),
  clearKey: (field: string) => req(`/api/keys/${field}`, { method: 'DELETE' }),

  // First-run setup (loopback-only, pre-OAuth).
  setupStatus: () => req('/api/setup/status'),
  validateSetupToken: (token: string) =>
    req('/api/setup/validate-token', { method: 'POST', body: JSON.stringify({ token }) }),
  saveSetupKeys: (b: any) => req('/api/setup/keys', { method: 'POST', body: JSON.stringify(b) }),
  saveSetup: (b: any) => req('/api/setup/save', { method: 'POST', body: JSON.stringify(b) }),
  // Server hosting: the app drives the operator's cloud VM over SSH (no local bot). The
  // SSH-backed reads carry a timeout so a wedged/unreachable backend surfaces as an error
  // instead of hanging (deploy has none — it legitimately runs for minutes).
  serverPubkey: () => req('/api/server/pubkey', { timeoutMs: 12000 }),
  serverDeploy: (b: { host: string; user?: string; env: string }) =>
    req('/api/server/deploy', { method: 'POST', body: JSON.stringify(b) }),
  serverConnect: (b: { host: string; user?: string }) =>
    req('/api/server/connect', { method: 'POST', body: JSON.stringify(b), timeoutMs: 30000 }),
  serverPower: (action: 'up' | 'stop') =>
    // Boots the pinned digest — no pull, so this is quick now.
    req('/api/server/power', { method: 'POST', body: JSON.stringify({ action }), timeoutMs: 120000 }),
  // Runs the VM's update script: resolve the newest release, pin it, apply it health-gated,
  // roll back on failure. Explicit — the panel no longer fires this just by opening.
  serverUpdate: () =>
    req('/api/server/update', { method: 'POST', timeoutMs: 1260000 }),
  // What the VM's update timer last did, including while the app was closed.
  serverLastUpdate: () => req('/api/server/last-update', { timeoutMs: 40000 }),
  // SSH connect (≤20s) + one remote docker probe (≤45s). Leave headroom over the
  // backend budget so a slow link doesn't false-flag the panel as Unreachable.
  serverStatus: () => req('/api/server/status', { timeoutMs: 75000 }),
  serverLogs: (which: 'bot' | 'funnel', tail = 200) =>
    req(`/api/server/logs?which=${which}&tail=${tail}`, { timeoutMs: 40000 }),

  // Bot profiles (loopback-only): each "bot" is an independent profile with its own token,
  // config, and database. One local bot is active at a time; switching stops the current
  // one and starts the selected profile's bot. Callers reload the app after switch/create,
  // since auth, guilds, the X-Guild-Id header, and the active database all change.
  botList: () => req('/api/bots'),
  activeBot: () => req('/api/bots/active'),
  createBot: (name: string) => req('/api/bots', { method: 'POST', body: JSON.stringify({ name }) }),
  switchBot: (id: string) => req('/api/bots/switch', { method: 'POST', body: JSON.stringify({ id }) }),
  renameBot: (id: string, name: string) => req('/api/bots/rename', { method: 'POST', body: JSON.stringify({ id, name }) }),
  setDefaultBot: (id: string) => req('/api/bots/default', { method: 'POST', body: JSON.stringify({ id }) }),
  // Reset a bot's deployment config (Discord creds, server, API keys) — keeps its learned
  // data + SSH key. Returns { ok, active, hosting_mode } so the caller can route.
  resetBot: (id: string) => req(`/api/bots/${encodeURIComponent(id)}/reset`, { method: 'POST' }),
  // Move a bot between hosts (local ↔ cloud VM), carrying its data + keeping the old copy as a
  // backup. Long-running (SSH deploy + data transfer), so no client timeout. Only the active bot.
  moveBot: (id: string, b: { target: 'local' | 'server'; host?: string; user?: string }) =>
    req(`/api/bots/${encodeURIComponent(id)}/move`, { method: 'POST', body: JSON.stringify(b) }),
  deleteBot: (id: string) => req(`/api/bots/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  enableTunnel: (b: { auth_key?: string; hostname?: string } = {}) =>
    req('/api/tunnel/enable', { method: 'POST', body: JSON.stringify(b) }),
  disableTunnel: () => req('/api/tunnel/disable', { method: 'POST' }),
  // Remote-access status (loopback-readable): { available, running, helper, hostname, public_url }.
  tunnelStatus: () => req('/api/tunnel/status'),

  // Bot power (operator only): { available, running, ready, can_power }.
  botStatus: () => req('/api/bot/status'),
  botPower: (on: boolean) => req('/api/bot/power', { method: 'POST', body: JSON.stringify({ on }) }),

  // Settings popup.
  getLogs: (lines = 500) => req(`/api/settings/logs?lines=${lines}`),
  sendFeedback: (b: { category: string; message: string; email?: string; include_logs?: boolean; attachments?: { name: string; type: string; content_b64: string }[] }) =>
    req('/api/settings/feedback', { method: 'POST', body: JSON.stringify(b) }),
  getUpdates: () => req('/api/settings/updates'),
  getRemote: () => req('/api/settings/remote'),
  getDesktop: () => req('/api/settings/desktop'),
  putDesktop: (b: { show_in_menu_bar: boolean }) =>
    req('/api/settings/desktop', { method: 'PUT', body: JSON.stringify(b) }),

  // ── Member portal ──────────────────────────────────────────────────────────
  // Every route answers for the signed-in member and nobody else. Guild scope rides on the
  // usual X-Guild-Id header; mutations additionally carry X-CSRF-Token (see setMemberCsrf).
  memberSession: () => req('/api/member/session'),
  memberOverview: () => req('/api/member/overview'),
  memberFacts: () => req('/api/member/facts'),
  memberDeleteFact: (id: number) => req(`/api/member/facts/${id}`, { method: 'DELETE' }),
  memberCorrection: (content: string) =>
    req('/api/member/facts/correction', { method: 'POST', body: JSON.stringify({ content }) }),
  memberReminders: () => req('/api/member/reminders'),
  memberCancelReminder: (id: number) => req(`/api/member/reminders/${id}`, { method: 'DELETE' }),
  memberSettings: (b: {
    memory_opt_out?: boolean; dm_opt_out?: boolean; search_opt_out?: boolean; pause_hours?: number
  }) => req('/api/member/settings', { method: 'PATCH', body: JSON.stringify(b) }),
  memberForget: (stopRemembering: boolean) =>
    req('/api/member/forget', { method: 'POST', body: JSON.stringify({ stop_remembering: stopRemembering }) }),
  // Fetched rather than linked: the route is scoped by the X-Guild-Id header, and an
  // <a href> can't send one. Returns the blob for the caller to save.
  memberExport: async (): Promise<Blob> => {
    const res = await fetch(BASE + '/api/member/export', {
      credentials: 'include',
      headers: {
        ...(currentGuild ? { 'X-Guild-Id': currentGuild } : {}),
        ...(memberCsrf ? { 'X-CSRF-Token': memberCsrf } : {}),
      },
    })
    if (res.status === 401) { onUnauthorized?.(); throw new Unauthorized('not authenticated') }
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || res.statusText)
    return res.blob()
  },
}
