// ── The memory replay's data ────────────────────────────────────────────────────────
// What /api/replay answers (api/routers/replay.py), and the formatting the start sheet,
// remembering, Play and the replay itself share. The report is built on the bot
// (olisar/replay/); times in it are UTC, and `tz` is the server's own hour offset, so a
// night there reads as a night here whatever the viewer's clock says.

export type Msg = {
  id: string; ch: string; chn: string; at: string
  who: string | null          // a key into `people`, "bot" for Olisar, null for Someone
  text: string
  trigger?: string
}
export type Gap = { gap: string }
export type PersonRef = { name: string; avatar: string | null }

export type Milestone =
  | { kind: 'nth'; n: number; at: string; who: string | null; weeks_before?: number }
  | { kind: 'burst'; day: string; at: string; count: number; line: string | null }

export type Night = {
  day: string; channel: string; channel_id: string; count: number; in_channel: number; night: boolean
  rank?: number               // 0 for the busiest day; more when that one wasn't safe to show
  from: string; to: string; faces: string[]
  line: string | null; sphere: string | null; excerpt: (Msg | Gap)[]
}

export type Pick = {
  kind: string; title: string; meaning: string; why: string
  origin: Msg; intro: string; spread: string
  people: number; count: number; unit: string; channels: number
  spots: { where: string; said?: string | null }[]
}

export type Report = {
  v: number; run: number; answer: string | null
  guild: { id: string; name: string; icon: string }
  bot: { name: string; avatar: string | null }
  tz: number | null
  created: string
  range: { from: string; to: string }
  totals: { messages: number; channels: number; people: number; authors: number; years: number }
  people: Record<string, PersonRef>
  first: { channel: string; messages: Msg[] } | null
  founders: { who: string | null; at: string; count: number | null; msg: Msg | null; gone?: boolean }[]
  arrivals: { start: string; end: string; points: [number, number][]; now: number; milestones: Milestone[] }
  night: Night | null
  arrive: { at: string; reply: Msg[] } | null
  names: { at: string; from: string; to: string; by: string | null; via: 'chat' | 'console' }[]
  pick: Pick | null
  moments: { found: number }
  last_answer: string | null
}

export type Progress = {
  phase?: number              // 0 gathering, 1 counting, 2 reading, 3 writing, 4 ready
  read?: number; total?: number; where?: string | null
  people?: number; channels?: number; years?: number
  moments?: number; chapters?: number
  found?: { faces: string[]; year?: string }[]
}

export type Run = {
  id: number
  status: 'queued' | 'running' | 'paused' | 'failed' | 'stopped' | 'done'
  step: string
  progress: Progress
  started_by: { id: string; name: string | null }
  mine: boolean
  started_at: string | null
  updated_at: string | null
  paused_until: string | null
  pause_reason: 'quota' | 'budget' | null
  error: string | null
  eta: number | null
}

export type Latest = { id: number; finished_at: string; chapters: number; messages: number | null; from: string | null; answer: string | null }

export type Preflight = {
  state: 'ready' | 'indexing' | 'quota' | 'budget' | 'short'
  messages: number
  channels: number
  first: string | null
  indexing: { percent: number; channels: number; unread: number; before: string | null; minutes: number } | null
  cost: { tier: 'free' | 'paid'; minutes: number; requests: number; share?: number; resets_at?: string; usd?: number; budget?: number }
}

export type ReplayState = {
  server: { id: string; name: string }
  bot: { name: string }
  run: Run | null
  latest: Latest | null
  preflight: Preflight
}

// ── Formatting ──

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US')
export const plural = (n: number, one: string, many = one + 's') => `${fmt(n)} ${Math.round(n) === 1 ? one : many}`
export const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
export const word = (n: number) => (n >= 0 && n < WORDS.length ? WORDS[n] : fmt(n))
export const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100
  return fmt(n) + (s[(v - 20) % 10] || s[v] || s[0])
}

/** A moment in the server's own time: its offset when the report knows it, the viewer's
 *  otherwise. */
export function local(iso: string, tz: number | null) {
  const d = new Date(iso)
  if (tz == null) {
    return { y: d.getFullYear(), mo: d.getMonth(), d: d.getDate(), h: d.getHours(), mi: d.getMinutes(), wd: d.getDay() }
  }
  const t = new Date(d.getTime() + tz * 3600_000)
  return { y: t.getUTCFullYear(), mo: t.getUTCMonth(), d: t.getUTCDate(), h: t.getUTCHours(), mi: t.getUTCMinutes(), wd: t.getUTCDay() }
}

/** "March 14, 2021" */
export const longDate = (iso: string, tz: number | null) => { const l = local(iso, tz); return `${MONTHS[l.mo]} ${l.d}, ${l.y}` }
/** "Mar 14, 2021" */
export const shortDate = (iso: string, tz: number | null) => { const l = local(iso, tz); return `${MONTHS[l.mo].slice(0, 3)} ${l.d}, ${l.y}` }
/** "Mar 2021" */
export const monthYear = (iso: string, tz: number | null) => { const l = local(iso, tz); return `${MONTHS[l.mo].slice(0, 3)} ${l.y}` }
/** "9:12 p.m." */
export function clock(iso: string, tz: number | null): string {
  const l = local(iso, tz)
  const h = l.h % 12 || 12
  return `${h}:${String(l.mi).padStart(2, '0')} ${l.h < 12 ? 'a.m.' : 'p.m.'}`
}
/** "a Sunday night" */
export function partOfDay(iso: string, tz: number | null): string {
  const l = local(iso, tz)
  const part = l.h >= 5 && l.h < 12 ? 'morning' : l.h < 17 && l.h >= 12 ? 'afternoon' : l.h < 21 && l.h >= 17 ? 'evening' : 'night'
  return `a ${WEEKDAYS[l.wd]} ${part}`
}
/** "March 14 to 19, 2021", "March 14 to April 2, 2021", "March 14, 2021 to January 3, 2022" */
export function dateRange(a: string, b: string, tz: number | null): string {
  const x = local(a, tz), y = local(b, tz)
  if (x.y === y.y && x.mo === y.mo) return x.d === y.d ? longDate(a, tz) : `${MONTHS[x.mo]} ${x.d} to ${y.d}, ${x.y}`
  if (x.y === y.y) return `${MONTHS[x.mo]} ${x.d} to ${MONTHS[y.mo]} ${y.d}, ${x.y}`
  return `${longDate(a, tz)} to ${longDate(b, tz)}`
}

/** A calendar day ("2023-02-03") as "Feb 3, 2023". */
export function dayShort(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return `${MONTHS[m - 1].slice(0, 3)} ${d}, ${y}`
}

/** A calendar day ("2023-02-03") as "February 3, 2023". */
export function dayName(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return `${MONTHS[m - 1]} ${d}, ${y}`
}

/** "four days", "three weeks", "two months" */
export function span(ms: number): string {
  const days = Math.max(1, Math.round(ms / 86400_000))
  if (days < 14) return `${word(days)} ${days === 1 ? 'day' : 'days'}`
  if (days < 60) { const w = Math.round(days / 7); return `${word(w)} weeks` }
  if (days < 730) { const m = Math.round(days / 30.4); return `${word(m)} months` }
  const y = Math.round(days / 365.25)
  return `${word(y)} years`
}

/** About how long, for a person to read: "about 25 minutes", "about 2 hours". */
export function about(minutes: number): string {
  if (minutes < 1.5) return 'about a minute'
  if (minutes < 90) return `about ${plural(Math.round(minutes), 'minute')}`
  return `about ${plural(Math.round(minutes / 60), 'hour')}`
}

/** A share of the day's allowance as a person would say it: "a third", "about 4%". */
export function share(x: number): string {
  const named: [number, string][] = [[1 / 2, 'half'], [1 / 3, 'a third'], [1 / 4, 'a quarter'], [1 / 5, 'a fifth'], [1 / 10, 'a tenth']]
  for (const [v, name] of named) if (Math.abs(x - v) / v < 0.12) return name
  if (x < 0.01) return 'under 1%'
  return `about ${Math.round(x * 100)}%`
}

export function usd(x: number): string {
  if (x < 0.01) return 'under a cent'
  return `$${x.toFixed(2)}`
}

/** Text set as HTML: everything a member, Discord or a model wrote goes through this. */
export const esc = (t: unknown) =>
  String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

/** A name, isolated so a right-to-left one can't reorder the English around it. */
export const bdi = (name: string) => `<bdi>${esc(name)}</bdi>`

/** A model-written line, escaped, with the names it mentions isolated. */
export function withNames(text: string, names: string[]): string {
  let out = esc(text)
  const sorted = [...new Set(names.filter(Boolean))].sort((a, b) => b.length - a.length)
  for (const n of sorted) {
    const e = esc(n).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    out = out.replace(new RegExp(`(^|[^\\p{L}\\p{N}_])(${e})(?=$|[^\\p{L}\\p{N}_])`, 'gu'), (_, pre, name) => `${pre}<bdi>${name}</bdi>`)
  }
  return out
}

/** A message's link in Discord. */
export const discordLink = (guild: string, m: Msg) => `https://discord.com/channels/${guild}/${m.ch}/${m.id}`

export const QUOTE_MAX = 220
/** A quote over 220 characters, cut at a word. */
export const clip = (t: string) => (t.length > QUOTE_MAX ? t.slice(0, QUOTE_MAX - 8).replace(/\s+\S*$/, '') + '…' : t)

export const isGap = (m: Msg | Gap): m is Gap => 'gap' in m
