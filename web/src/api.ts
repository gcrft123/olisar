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
    try {
      const j = JSON.parse(body)
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* not JSON — use the raw body */ }
    throw new Error(msg)
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

  getKnowledge: () => req('/api/knowledge'),
  addSource: (b: any) => req('/api/knowledge', { method: 'POST', body: JSON.stringify(b) }),
  deleteSource: (id: number) => req(`/api/knowledge/${id}`, { method: 'DELETE' }),

  getFacts: () => req('/api/facts'),
  addFact: (b: any) => req('/api/facts', { method: 'POST', body: JSON.stringify(b) }),
  deleteFact: (id: number) => req(`/api/facts/${id}`, { method: 'DELETE' }),

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
  enableTunnel: (b: { auth_key: string; hostname: string }) =>
    req('/api/tunnel/enable', { method: 'POST', body: JSON.stringify(b) }),
  // Remote-access status (loopback-readable): { available, running, helper, hostname, public_url }.
  tunnelStatus: () => req('/api/tunnel/status'),
}
