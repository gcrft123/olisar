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

async function req(path: string, opts: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (currentGuild) headers['X-Guild-Id'] = currentGuild
  const res = await fetch(BASE + path, {
    credentials: 'include',
    ...opts,
    headers,
  })
  if (res.status === 401) { onUnauthorized?.(); throw new Unauthorized('not authenticated') }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    let msg = body || res.statusText
    let detail: any = null
    try {
      const j = JSON.parse(body)
      if (j?.detail !== undefined && j?.detail !== null) {
        detail = j.detail
        msg = typeof j.detail === 'string' ? j.detail : (j.detail?.message || JSON.stringify(j.detail))
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

  me: () => req('/api/me'),
  guilds: () => req('/api/guilds'),
  models: () => req('/api/models'),

  getPersona: () => req('/api/persona'),
  putPersona: (b: any) => req('/api/persona', { method: 'PUT', body: JSON.stringify(b) }),

  getConfig: () => req('/api/config'),
  putConfig: (b: any) => req('/api/config', { method: 'PUT', body: JSON.stringify(b) }),

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
  marketplacePublish: (key: string) =>
    req('/api/marketplace/publish', { method: 'POST', body: JSON.stringify({ key }) }),
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

  // Message search index (re)build + per-channel progress.
  reindex: () => req('/api/knowledge/reindex', { method: 'POST' }),
  clearIndex: () => req('/api/knowledge/reindex/clear', { method: 'POST' }),
  reindexStatus: () => req('/api/knowledge/reindex/status'),

  getFacts: () => req('/api/facts'),
  addFact: (b: any) => req('/api/facts', { method: 'POST', body: JSON.stringify(b) }),
  deleteFact: (id: number) => req(`/api/facts/${id}`, { method: 'DELETE' }),

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
  sendFeedback: (b: { category: string; message: string; email?: string; logs?: string; attachments?: { name: string; type: string; content_b64: string }[] }) =>
    req('/api/settings/feedback', { method: 'POST', body: JSON.stringify(b) }),
  getUpdates: () => req('/api/settings/updates'),
  getRemote: () => req('/api/settings/remote'),
  getDesktop: () => req('/api/settings/desktop'),
  putDesktop: (b: { show_in_menu_bar: boolean }) =>
    req('/api/settings/desktop', { method: 'PUT', body: JSON.stringify(b) }),
}
