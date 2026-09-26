// What the bot has been doing, for the final server screen's memories (see brain.ts). Read
// from the VM over SSH by /api/server/activity, which builds it from the bot's own tables: its
// replies and how each was called, members who joined and the roster sync, impressions,
// saved memories and glossary facts, the custom status it set, knowledge sources, reminders
// that fired, and images it drew. Never a DM. The health check comes from the status poll.

export type Person = { name: string; avatar: string }

type At = { id: string; at: number }
export type ActivityItem = At & (
  | { kind: 'reply'; who: Person; where: string; trigger: string; ask: string; text: string }
  | { kind: 'member'; who: Person; roles: string[] }
  | { kind: 'people'; count: number; faces: Person[] }
  | { kind: 'impression'; who: Person; text: string; messages: number }
  | { kind: 'remembered'; who: Person; type: string; text: string; said: string; where: string }
  | { kind: 'glossary'; subject: string; text: string; where: string }
  | { kind: 'status'; text: string; how: string }
  | { kind: 'learned'; title: string; url: string; count: number; who: Person | null; how: string }
  | { kind: 'reminder'; who: Person; text: string; where: string }
  | { kind: 'image'; who: Person; text: string; where: string }
  | { kind: 'health'; checks: HealthChecks | null }
)
export type ActivityKind = ActivityItem['kind']

/** What the in-container /api/health said: null where it hasn't run its self-check. */
export type HealthChecks = { vec: boolean | null; sandbox: boolean | null; model: string | null }

/** The /api/server/activity answer (see api/routers/server.py). */
export type ActivityPayload = {
  ok?: boolean
  supported?: boolean
  error?: string
  items?: any[]
  members?: { count: number; at: string | null; faces: Person[] } | null
  health?: HealthChecks | null
}

const ms = (iso: unknown) => {
  const t = typeof iso === 'string' ? Date.parse(iso) : NaN
  return Number.isFinite(t) ? t : 0
}
const person = (p: any): Person => ({ name: String(p?.name || 'Someone'), avatar: String(p?.avatar || '') })
const str = (v: unknown) => (typeof v === 'string' ? v : '')

// One item off the wire, or null for a kind this build doesn't draw. The backend owns the
// shapes; this only fills what a missing field would otherwise break.
function normalize(r: any): ActivityItem | null {
  if (!r || typeof r.id !== 'string' || typeof r.kind !== 'string') return null
  const base = { id: r.id, at: ms(r.at) }
  switch (r.kind) {
    case 'reply': return { ...base, kind: 'reply', who: person(r.who), where: str(r.where), trigger: str(r.trigger), ask: str(r.ask), text: str(r.text) }
    case 'member': return { ...base, kind: 'member', who: person(r.who), roles: Array.isArray(r.roles) ? r.roles.map(String) : [] }
    case 'impression': return { ...base, kind: 'impression', who: person(r.who), text: str(r.text), messages: Number(r.messages) || 0 }
    case 'remembered': return { ...base, kind: 'remembered', who: person(r.who), type: str(r.type), text: str(r.text), said: str(r.said), where: str(r.where) }
    case 'glossary': return { ...base, kind: 'glossary', subject: str(r.subject), text: str(r.text), where: str(r.where) }
    case 'status': return { ...base, kind: 'status', text: str(r.text), how: str(r.how) }
    case 'learned': return { ...base, kind: 'learned', title: str(r.title), url: str(r.url), count: Number(r.count) || 0, who: r.who ? person(r.who) : null, how: str(r.how) }
    case 'reminder': return { ...base, kind: 'reminder', who: person(r.who), text: str(r.text), where: str(r.where) }
    case 'image': return { ...base, kind: 'image', who: person(r.who), text: str(r.text), where: str(r.where) }
    default: return null
  }
}

export type Activity = {
  start(): void
  stop(): void
  items(): ActivityItem[]
  health(): ActivityItem & { kind: 'health' }
  /** The status poll saw a healthcheck: `at` is when it ran (ms). */
  beat(at: number): void
  subscribe(fn: (type: 'add' | 'beat', item?: ActivityItem) => void): () => void
}

/** Polls `load` while started and the tab is visible. The first answer seeds the list
 *  quietly; anything new after that is announced, so it buds off the form as it arrives. */
export function createActivity(load: () => Promise<ActivityPayload>, everyMs = 20000): Activity {
  const subs = new Set<(type: 'add' | 'beat', item?: ActivityItem) => void>()
  const emit = (type: 'add' | 'beat', item?: ActivityItem) => subs.forEach((fn) => fn(type, item))
  let items: ActivityItem[] = []
  const seen = new Set<string>()
  let seeded = false, timer: ReturnType<typeof setTimeout> | undefined, running = false, inflight = false
  const health: ActivityItem & { kind: 'health' } = { id: 'health', kind: 'health', at: 0, checks: null }

  async function pull() {
    if (inflight) return
    inflight = true
    try {
      const r = await load()
      if (!r?.ok) return
      if (r.health) health.checks = r.health
      const next = (r.items || []).map(normalize).filter((x): x is ActivityItem => !!x)
      if (r.members?.count) {
        // One memory for the roster, whenever it last synced, so an open one stays open.
        next.push({ id: 'people', kind: 'people', at: ms(r.members.at), count: r.members.count, faces: (r.members.faces || []).map(person) })
      }
      next.sort((a, b) => b.at - a.at)
      const fresh = next.filter((m) => !seen.has(m.id))
      for (const m of next) seen.add(m.id)
      items = next.slice(0, 24)
      if (!seeded) { seeded = true; if (next.length) emit('add'); return }
      // Oldest first, so each lands in turn.
      for (const m of fresh.reverse()) emit('add', m)
    } catch {
      // A missed read is retried on the next tick; the memories on screen stay.
    } finally {
      inflight = false
    }
  }
  const schedule = () => {
    clearTimeout(timer)
    if (!running || document.hidden) return
    timer = setTimeout(() => { void pull().finally(schedule) }, everyMs)
  }
  const onVisible = () => { if (!document.hidden && running) void pull().finally(schedule) }

  return {
    start() {
      if (running) return
      running = true
      document.addEventListener('visibilitychange', onVisible)
      void pull().finally(schedule)
    },
    stop() {
      running = false
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    },
    items: () => items,
    health: () => health,
    beat(at) {
      if (!at || at === health.at) return
      const first = !health.at
      health.at = at
      if (!first) emit('beat', health)
    },
    subscribe(fn) { subs.add(fn); return () => { subs.delete(fn) } },
  }
}

// ── Pfps ────────────────────────────────────────────────────────────────────────
// Someone with no avatar of their own gets Discord's default: its mascot on one of its five
// colours, picked from the name so it's the same wherever they turn up.
const CLYDE = 'M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z'
// Discord's own default-avatar colours: a picture of Discord, like the --dc- tokens.
const DEFAULTS = ['#5865f2', '#757e8a', '#3ba55c', '#faa61a', '#ed4245', '#eb459f']
const hashStr = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) } return h >>> 0 }
const drawn = new Map<string, string>()

export function defaultAvatar(name: string): string {
  const hit = drawn.get(name)
  if (hit) return hit
  const c = document.createElement('canvas'); c.width = c.height = 64
  const g = c.getContext('2d')
  if (!g) return ''
  g.fillStyle = DEFAULTS[hashStr(name) % DEFAULTS.length]; g.fillRect(0, 0, 64, 64)
  g.save(); g.translate(14, 18.3); g.scale(36 / 127.14, 36 / 127.14); g.fillStyle = '#fff'; g.fill(new Path2D(CLYDE)); g.restore()
  const url = c.toDataURL('image/png')
  drawn.set(name, url)
  return url
}

export const avatarOf = (p: Person | null | undefined) => (p?.avatar ? p.avatar : defaultAvatar(p?.name || ''))
