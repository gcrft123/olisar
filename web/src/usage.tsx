// ── Usage ───────────────────────────────────────────────────────────────────
// Laid out in the order someone opening it needs answers: how much is left today across the
// fallback chain, which model is replying, where the requests go, then everything else. The
// design, and what the page before it got wrong, is in design/usage-page/.
//
// Every figure is a "left" figure, so every meter fills with what's left rather than what's
// used — a departure from DESIGN.md's meter recipe, made because a fill that meant the
// opposite of the number beside it read wrong.
import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { botName } from './botname'
import { Icon } from './icons'
import { Loading, PageHead } from './pages'
import { Badge, Section, Segmented, useAsync, usePoll } from './ui'

type ChainModel = {
  model: string
  requests: number
  tokens: number
  limit: number
  limit_from_google: boolean
  state: 'ok' | 'resting' | 'spent'
  back_in: number | null
  spent_at: string | null
}

type Live = {
  ts: string
  exhausted: boolean
  day: string
  day_start: string
  reset_at: string
  chain: ChainModel[]
  memory_search: { model: string; requests: number; limit: number; spent: boolean }
  web_search: { requests: number; limit: number; spent: boolean }
}

type Range = 'today' | '7' | '30'

type Summary = {
  day: string
  features: Record<Range, Record<string, number>>
  yesterday: { requests: number; tokens: number } | null
  busiest_minute: { model: string; requests: number; limit: number; at: string | null } | null
  last_ran_out: string | null
  days: { day: string; requests: number; ran_out_at: string | null }[]
}

// When the reading arrived, and how far the server's clock is from this one, so countdowns
// run between polls and "in 8h 12m" doesn't inherit a wrong clock on this machine.
type Received = Live & { receivedAt: number; skew: number }

// ── Formatting ──────────────────────────────────────────────────────────────
// Times show in the viewer's own zone; only the day boundary is Pacific, because it's
// Google's.
const PACIFIC = 'America/Los_Angeles'
const n = (v: number) => Math.round(v).toLocaleString()
const compact = (v: number) => (v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e4 ? Math.round(v / 1e3) + 'k' : n(v))
function clock(ms: number) {
  const s = new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return s === '12:00 AM' ? 'midnight' : s
}
function span(ms: number) {
  const m = Math.max(0, Math.round(ms / 60000))
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}
function mmss(ms: number) {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
function change(now: number, then: number) {
  const p = Math.round(((now - then) / then) * 100)
  return (p > 0 ? '+' : p < 0 ? '−' : '±') + Math.abs(p) + '%'
}
/** The date Google was counting at `ms`, as YYYY-MM-DD. */
function pacificDay(ms: number) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: PACIFIC }).format(new Date(ms))
}
const daysBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000)

/** "gemini-3.1-flash-lite" → "Gemini 3.1 Flash-Lite", "gemini-flash-latest" → "Gemini Flash
 *  (latest)". A model it doesn't recognise keeps its id. */
function modelName(id: string) {
  const m = id.match(/^gemini-(?:(\d[\d.]*)-)?(flash-lite|flash|pro)(?:-(latest|preview))?$/)
  if (!m) return id
  const [, version, family, tag] = m
  const fam = family === 'flash-lite' ? 'Flash-Lite' : family[0].toUpperCase() + family.slice(1)
  return `Gemini ${version ? version + ' ' : ''}${fam}${tag === 'latest' ? ' (latest)' : ''}`
}

// ── Reading ─────────────────────────────────────────────────────────────────
type Row = ChainModel & { i: number; backAt: number | null; left: number }

function reading(live: Received, clientNow: number) {
  const t = clientNow + live.skew
  const chain: Row[] = live.chain.map((m, i) => {
    const backAt = m.back_in != null ? live.receivedAt + m.back_in * 1000 : null
    // A countdown that ran out between polls is back already; the next poll will agree.
    const state = m.state === 'resting' && backAt != null && backAt <= clientNow ? 'ok' : m.state
    return { ...m, i, backAt, state, left: m.state === 'spent' ? 0 : Math.max(0, m.limit - m.requests) }
  })
  const allSpent = chain.length > 0 && chain.every((m) => m.state === 'spent')
  // The first model that can take a request. With every one resting for a moment, the one
  // back soonest is who replies next.
  const cur = chain.find((m) => m.state === 'ok')
    ?? chain.filter((m) => m.state === 'resting').sort((a, b) => (a.backAt ?? 0) - (b.backAt ?? 0))[0]
    ?? null
  const capTotal = chain.reduce((s, m) => s + m.limit, 0)
  const usedTotal = chain.reduce((s, m) => s + m.requests, 0)
  const tokens = chain.reduce((s, m) => s + m.tokens, 0)
  const left = chain.reduce((s, m) => s + m.left, 0)
  const reset = Date.parse(live.reset_at)
  // Today's pace: everything counted since Google's day began, spread over the hours so far.
  const hours = Math.max(0.25, (t - Date.parse(live.day_start)) / 3.6e6)
  const perHour = usedTotal / hours
  const toReset = reset - t
  const runsOutAt = !allSpent && perHour > 0 && perHour * (toReset / 3.6e6) > left
    ? t + (left / perHour) * 3.6e6
    : null
  return { t, clientNow, chain, cur, allSpent, capTotal, usedTotal, tokens, left, reset, toReset, runsOutAt }
}
type Reading = ReturnType<typeof reading>

function useNow(everyMs: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(id)
  }, [everyMs])
  return now
}

async function loadLive(): Promise<Received> {
  const data: Live = await api.getUsageLive()
  const receivedAt = Date.now()
  return { ...data, receivedAt, skew: Date.parse(data.ts) - receivedAt }
}

export function Usage() {
  const live = useAsync<Received>(loadLive, [])
  // No .catch on either poll: usePoll needs the rejection to know the backend is gone.
  const livePoll = usePoll(() => live.refresh(), 4000)
  const summary = useAsync<Summary>(() => api.getUsage(), [])
  usePoll(() => summary.refresh(), 60000)
  const [range, setRange] = useState<Range>('today')
  // Countdowns (a model resting, the reset) move every second without waiting for a poll.
  const clientNow = useNow(1000)

  if (!live.data || !summary.data) {
    const gate = {
      loading: live.loading || summary.loading,
      error: live.error || summary.error,
      reload: () => { live.reload(); summary.reload() },
    }
    return <Loading of={gate} what="usage" />
  }
  const R = reading(live.data, clientNow)
  const stale = livePoll.stale

  return (
    <div className="usage">
      <PageHead icon="usage" title="Usage" doc="usage" />
      {stale && (
        <div className="callout warning">
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">Can’t reach the bot. These are the last figures it reported, not the current ones.</div>
        </div>
      )}
      <Now R={R} live={live.data} stale={stale} />
      <Chain R={R} stale={stale} />
      <Features data={summary.data} range={range} setRange={setRange} />
      <Stats R={R} data={summary.data} day={live.data.day} />
    </div>
  )
}

// ── What's left, and what's replying ────────────────────────────────────────
function Now({ R, live, stale }: { R: Reading; live: Received; stale: boolean }) {
  const memory = live.memory_search
  const web = live.web_search
  return (
    <div className={'u-now' + (stale ? ' stale' : '')}>
      <div className="u-now-left">
        <div className="u-top"><h2 className="u-eyebrow">Left today</h2></div>
        <div className={'u-hero' + (R.left === 0 ? ' zero' : '')}>{n(R.left)}</div>
        <div className="u-hero-sub">
          of <b>{n(R.capTotal)}</b> requests · resets at {clock(R.reset)}, in {span(R.toReset)}
        </div>
      </div>
      <div className="u-now-right">
        <div className="u-top">
          <h2 className="u-eyebrow">Replying with</h2>
          {/* Amber once the poll stops landing: a green "Live" over numbers that have
              stopped moving is the most misleading thing this page could show. */}
          {stale
            ? <Badge tone="warning" icon="danger-circle">Not responding</Badge>
            : <Badge tone="success" icon="soundwave-circle">Live</Badge>}
        </div>
        {R.cur && !R.allSpent ? (
          <>
            <div className="u-cur">{modelName(R.cur.model)}</div>
            <div className="u-cur-id">{R.cur.model}</div>
          </>
        ) : <div className="u-cur none">No model left</div>}
      </div>
      <div className="u-now-foot">
        <Pace R={R} />
        <div className="u-side-limits">
          <SideMeter label="Memory search" used={memory.requests} limit={memory.limit} spent={memory.spent} />
          <SideMeter label="Web search" used={web.requests} limit={web.limit} spent={web.spent} />
        </div>
      </div>
    </div>
  )
}

function Pace({ R }: { R: Reading }) {
  if (R.allSpent) {
    return (
      <div className="u-pace danger">
        <Icon.warn size={16} weight="Bold" />
        <span>{botName()} can’t reply until the limits reset at {clock(R.reset)}.</span>
      </div>
    )
  }
  if (R.runsOutAt) {
    return (
      <div className="u-pace warn">
        <Icon.warn size={16} weight="Bold" />
        <span>At today’s pace, this runs out around {clock(R.runsOutAt)}, {span(R.reset - R.runsOutAt)} before the reset.</span>
      </div>
    )
  }
  return (
    <div className="u-pace ok">
      <Icon.check size={16} weight="Bold" />
      <span>At today’s pace, this lasts until the reset.</span>
    </div>
  )
}

/** A meter that fills with what's left. `--used` clips the fill in from the left. */
function Meter({ used, limit, out, lost, className }: { used: number; limit: number; out?: boolean; lost?: boolean; className?: string }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100
  return (
    <span className={'u-mini' + (out ? ' out' : '') + (className ? ' ' + className : '')} style={{ '--used': `${pct}%` } as React.CSSProperties} aria-hidden>
      <i />
      {/* Quota Google says is gone though Olisar never counted it: something else on the
          same Google Cloud project used it. */}
      {lost && <b className="u-lost" />}
    </span>
  )
}

function SideMeter({ label, used, limit, spent }: { label: string; used: number; limit: number; spent: boolean }) {
  const left = spent ? 0 : Math.max(0, limit - used)
  return (
    <div className="u-side">
      <span>{label}</span>
      <Meter used={spent ? limit : used} limit={limit} />
      <span className="v">{n(left)} <s>left of {n(limit)}</s></span>
    </div>
  )
}

// ── Fallback chain ──────────────────────────────────────────────────────────
function Status({ m, R }: { m: Row; R: Reading }) {
  if (m.state === 'spent') {
    return (
      <>
        <Badge tone="danger" icon="minus-circle">Used up</Badge>
        {m.spent_at && <span className="when">at {clock(Date.parse(m.spent_at))}</span>}
      </>
    )
  }
  if (m.state === 'resting') {
    return <Badge tone="info" busy>Back in<span className="num">{mmss((m.backAt ?? 0) - R.clientNow)}</span></Badge>
  }
  if (R.cur && R.cur.i === m.i) return <Badge tone="success" icon="play-circle">Replying</Badge>
  return <Badge icon="menu-dots-circle">Standby</Badge>
}

function Chain({ R, stale }: { R: Reading; stale: boolean }) {
  return (
    <Section stacked title="Fallback chain">
      <table className={'u-chain' + (stale ? ' stale' : '')}>
        <thead>
          <tr>
            <th scope="col" className="c-rank"><span className="visually-hidden">Position</span></th>
            <th scope="col">Model</th>
            <th scope="col" className="c-status">Status</th>
            <th scope="col" className="c-today">Left today</th>
          </tr>
        </thead>
        <tbody>
          {R.chain.map((m) => {
            const isCur = !R.allSpent && R.cur?.i === m.i
            const out = m.state === 'spent'
            return (
              <tr key={m.model} className={(isCur ? 'cur' : '') + (out ? ' gone' : '')}>
                <th scope="row" className="c-rank">{m.i + 1}</th>
                <td>
                  <span className="m-name">{modelName(m.model)}</span>
                  <span className="m-id">{m.model}</span>
                  <div className="m-status"><Status m={m} R={R} /></div>
                </td>
                <td className="c-status"><div className="st"><Status m={m} R={R} /></div></td>
                <td className="c-today">
                  <div className="today">
                    <Meter used={m.requests} limit={m.limit} out={out} lost={out && m.requests < m.limit} />
                    <span className="v"><span className="left-v">{n(m.left)}</span><s> / {n(m.limit)}</s></span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

// ── By feature ──────────────────────────────────────────────────────────────
// Only requests against the chain's daily limits: memory search has its own, and on a busy
// day it was a quarter of the old donut. Each feature keeps its hue whatever the range, in
// the order that clears the colorblind check for neighbours round the ring.
const FEATURES = [
  { key: 'conversation', label: 'Replies' },
  { key: 'summary', label: 'Summaries' },
  { key: 'persona', label: 'Impressions' },
  { key: 'glossary', label: 'Glossary' },
  { key: 'vision', label: 'Image' },
  { key: 'else', label: 'Everything else' },
]
const NAMED = new Set(FEATURES.map((f) => f.key))
// What "Everything else" is made of, keyed by the `source` each Gemini call is tagged with.
const OTHER_LABEL: Record<string, string> = {
  proactivity: 'Chiming in', grounding: 'Web search', catchup: 'Catch-up', extension: 'Extensions',
  review: 'Extension reviews', canary: 'Health checks', status: 'Status', other: 'Other',
}
const RANGES: { value: Range; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
]

function Features({ data, range, setRange }: { data: Summary; range: Range; setRange: (r: Range) => void }) {
  const [hl, setHl] = useState(-1)
  const src = data.features[range] || {}
  const rows = FEATURES.map((f, i) => {
    if (f.key !== 'else') return { ...f, i, value: src[f.key] || 0, parts: null }
    const parts = Object.entries(src)
      .filter(([k, v]) => !NAMED.has(k) && v > 0)
      .map(([k, v]) => ({ label: OTHER_LABEL[k] || k, value: v }))
      .sort((a, b) => b.value - a.value)
    return { ...f, i, value: parts.reduce((s, p) => s + p.value, 0), parts }
  })
  const total = rows.reduce((s, r) => s + r.value, 0)
  const max = Math.max(1, ...rows.map((r) => r.value))
  const pct = (v: number) => (total ? Math.round((v / total) * 100) : 0)
  const scope = range === 'today' ? 'today' : `last ${range} days`

  const size = 184, c = size / 2, r = 74, sw = 16, gap = 5
  const C = 2 * Math.PI * r
  const drawn = rows.filter((row) => row.value > 0)
  const drawable = C - (drawn.length > 1 ? drawn.length * gap : 0)
  let cursor = 0
  const arcs = drawn.map((row) => {
    const len = Math.max(sw * 0.2, (row.value / (total || 1)) * drawable)
    const arc = { row, start: cursor, len }
    cursor += len + (drawn.length > 1 ? gap : 0)
    return arc
  })
  const focus = hl >= 0 ? rows[hl] : null

  return (
    <Section stacked title="By feature" actions={<Segmented className="useg" ariaLabel="Range" value={range} onChange={setRange} options={RANGES} />}>
      <div className="u-feat">
        <svg
          className={'u-ring' + (hl >= 0 ? ' hover' : '')} width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
          aria-label={`Requests by feature, ${scope}: ` + rows.map((x) => `${x.label} ${pct(x.value)}%`).join(', ')}
          onPointerLeave={() => setHl(-1)}
        >
          <circle className="u-ring-track" cx={c} cy={c} r={r} strokeWidth={sw} />
          {arcs.map((a) => {
            const dash = Math.max(0.5, a.len)
            const on = a.row.i === hl
            return (
              <circle
                key={a.row.key} className={`u-ring-seg u-f${a.row.i}` + (on ? ' hl' : '')} cx={c} cy={c} r={r}
                strokeWidth={on ? sw + 4 : sw} strokeDasharray={`${dash} ${C - dash}`}
                transform={`rotate(${(a.start / C) * 360 - 90} ${c} ${c})`}
                onPointerEnter={() => setHl(a.row.i)}
              />
            )
          })}
          <text className="u-ring-total" x={c} y={c + 4} textAnchor="middle">{n(focus ? focus.value : total)}</text>
          <text className="u-ring-sub" x={c} y={c + 24} textAnchor="middle">{focus ? focus.label : 'Requests'}</text>
        </svg>
        <div className="u-legend" onPointerLeave={() => setHl(-1)}>
          {rows.map((row) => (
            <React.Fragment key={row.key}>
              <div className={`u-lg u-f${row.i}` + (row.i === hl ? ' hl' : '')} onPointerEnter={() => setHl(row.i)}>
                <span className="sw" />
                <span className="nm">{row.label}</span>
                <span className="bar" style={{ transform: `scaleX(${row.value / max})` }} />
                <span className="val">{n(row.value)}</span>
                <span className="pc">{pct(row.value)}%</span>
              </div>
              {row.parts && row.parts.length > 0 && (
                <div className="u-lg-parts" onPointerEnter={() => setHl(row.i)}>
                  {row.parts.map((p, k) => (
                    <React.Fragment key={p.label}>
                      {k > 0 && <span className="sep" aria-hidden>·</span>}
                      <span className="part">{p.label} <span className="mono">{n(p.value)}</span></span>
                    </React.Fragment>
                  ))}
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </Section>
  )
}

// ── Stats ───────────────────────────────────────────────────────────────────
function lastRanOut(iso: string | null, today: string) {
  if (!iso) return { big: 'Never', cap: '' }
  const ms = Date.parse(iso)
  const ago = daysBetween(pacificDay(ms), today)
  if (ago <= 0) return { big: 'Today', cap: `at ${clock(ms)}` }
  if (ago === 1) return { big: 'Yesterday', cap: `at ${clock(ms)}` }
  const date = ago < 7
    ? new Date(ms).toLocaleDateString([], { weekday: 'long' })
    : new Date(ms).toLocaleDateString([], { month: 'short', day: 'numeric' })
  return { big: `${ago} days ago`, cap: `${date} at ${clock(ms)}` }
}

function Stats({ R, data, day }: { R: Reading; data: Summary; day: string }) {
  const y = data.yesterday
  const busiest = data.busiest_minute
  const out = lastRanOut(data.last_ran_out, day)
  const tiles: { k: string; big: React.ReactNode; cap: string }[] = [
    {
      k: 'Requests today', big: n(R.usedTotal),
      cap: y && y.requests > 0 ? `${change(R.usedTotal, y.requests)} on this time yesterday` : 'So far today',
    },
    {
      k: 'Tokens today', big: compact(R.tokens),
      cap: y && y.tokens > 0 ? `${change(R.tokens, y.tokens)} on this time yesterday` : 'So far today',
    },
    busiest
      ? {
          k: 'Busiest minute', big: <>{busiest.requests}<s> / {busiest.limit}</s></>,
          cap: modelName(busiest.model) + (busiest.at ? ` at ${clock(Date.parse(busiest.at))}` : ''),
        }
      : { k: 'Busiest minute', big: '0', cap: 'No requests yet today' },
    { k: 'Last ran out', big: out.big, cap: out.cap },
  ]
  return (
    <Section stacked title="Stats">
      <div className="u-kpis">
        {tiles.map((t) => (
          <div className="u-kpi" key={t.k}>
            <h3 className="u-eyebrow">{t.k}</h3>
            <div className="u-big">{t.big}</div>
            {t.cap && <div className="u-cap">{t.cap}</div>}
          </div>
        ))}
      </div>
      <Days R={R} data={data} />
    </Section>
  )
}

// ── Requests per day ────────────────────────────────────────────────────────
function useBoxWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(fallback)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    // contentRect is element-local, so it needs no correction for the interface zoom.
    const ro = new ResizeObserver(([e]) => {
      const next = Math.round(e.contentRect.width)
      if (next > 0) setW(next)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

const axisNum = (v: number) => (v >= 1000 ? (v % 1000 === 0 ? v / 1000 : (v / 1000).toFixed(1)) + 'k' : String(v))
/** Gridline values for a scale up to `max`: a round step, two or three lines. */
function gridlines(max: number) {
  const raw = max / 2.5
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1)))
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? 10 * mag
  const out: number[] = []
  for (let v = step; v < max; v += step) out.push(v)
  return out
}

function Days({ R, data }: { R: Reading; data: Summary }) {
  const [box, W] = useBoxWidth(900)
  const [hover, setHover] = useState(-1)
  // Today's bar reads the live count, so it agrees with the figures above it.
  const days = data.days.map((d, k) => (k === data.days.length - 1
    ? { ...d, requests: R.usedTotal, ranOut: R.allSpent || !!d.ran_out_at }
    : { ...d, ranOut: !!d.ran_out_at }))
  const last = days.length - 1
  const limit = R.capTotal
  const H = 150, x0 = 38, x1 = W - 4, y0 = H - 22, y1 = 14
  const yMax = Math.max(limit * 1.08, ...days.map((d) => d.requests), 1)
  const yAt = (v: number) => y0 - (v / yMax) * (y0 - y1)
  const slot = (x1 - x0) / Math.max(1, days.length)
  const bw = Math.min(24, slot * 0.56)
  const stride = Math.max(1, Math.ceil(56 / slot))
  const dateOf = (day: string, opts: Intl.DateTimeFormatOptions) =>
    new Date(day + 'T12:00:00Z').toLocaleDateString([], { ...opts, timeZone: 'UTC' })
  const label = (k: number) => (k === last ? 'Today' : dateOf(days[k].day, { month: 'short', day: 'numeric' }))
  const ticks = gridlines(yMax).filter((v) => Math.abs(yAt(v) - yAt(limit)) > 12)
  const tip = hover >= 0 ? days[hover] : null

  return (
    <div className="u-daily">
      <div className="u-days-head"><h3>Requests per day</h3><span>last {days.length} days</span></div>
      <div className="u-days" ref={box}>
        <svg
          className="u-days-chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`Requests per day over the last ${days.length} days, against the daily limit of ${n(limit)}. Today so far ${n(R.usedTotal)}.`}
          onPointerLeave={() => setHover(-1)}
        >
          {ticks.map((v) => (
            <g key={v}>
              <line className="grid" x1={x0} x2={x1} y1={yAt(v)} y2={yAt(v)} />
              <text className="axis" x={x0 - 8} y={yAt(v) + 4} textAnchor="end">{axisNum(v)}</text>
            </g>
          ))}
          <line className="grid" x1={x0} x2={x1} y1={y0} y2={y0} />
          <text className="axis" x={x0 - 8} y={y0 + 4} textAnchor="end">0</text>
          <line className="limit" x1={x0} x2={x1} y1={yAt(limit)} y2={yAt(limit)} strokeDasharray="5 4" />
          <text className="limit-txt" x={x0 - 8} y={yAt(limit) + 4} textAnchor="end">{axisNum(limit)}</text>
          {days.map((d, k) => {
            const cx = x0 + slot * (k + 0.5)
            const top = yAt(d.requests), h = Math.max(0, y0 - top)
            const rr = Math.min(4, h)
            const today = k === last
            // A 4px rounded data end, square at the baseline.
            const path = h > 0
              ? `M${cx - bw / 2} ${y0} V${top + rr} Q${cx - bw / 2} ${top} ${cx - bw / 2 + rr} ${top} H${cx + bw / 2 - rr} Q${cx + bw / 2} ${top} ${cx + bw / 2} ${top + rr} V${y0} Z`
              : ''
            const fromEnd = last - k
            const showLabel = today || (fromEnd % stride === 0 && fromEnd >= Math.max(1, Math.ceil(44 / slot)))
            return (
              <g key={d.day} className={'col' + (k === hover ? ' hl' : '')} onPointerEnter={() => setHover(k)}>
                <rect className="hit" x={x0 + slot * k} y={y1} width={slot} height={y0 - y1 + 18} />
                <path className={'bar' + (today ? ' today' : d.ranOut ? ' ranout' : '')} d={path} />
                {today && <text className="tag" x={Math.min(cx, x1 - 20)} y={top - 7} textAnchor="middle">{n(d.requests)}</text>}
                {showLabel && (
                  <text className={'axis' + (today ? ' on' : '')} x={today ? Math.min(cx, x1 - 18) : cx} y={y0 + 16} textAnchor="middle">{label(k)}</text>
                )}
              </g>
            )
          })}
        </svg>
        {tip && (
          <div className="u-tip" style={{ left: Math.min(Math.max(x0 + slot * (hover + 0.5), 70), W - 70), top: yAt(tip.requests) - 8 }} aria-hidden>
            <b>{n(tip.requests)}{hover === last ? ' so far' : ''}</b>
            <span>{hover === last ? 'Today' : dateOf(tip.day, { weekday: 'long', month: 'short', day: 'numeric' })}{tip.ranOut ? ' · ran out' : ''}</span>
          </div>
        )}
      </div>
      {/* Every chart ships its numbers twice; the class goes on a wrapping div, never the
          table, or the table sizes itself to its content and pushes the page sideways. */}
      <div className="visually-hidden">
        <table>
          <caption>Requests per day, last {days.length} days. Daily limit {n(limit)}.</caption>
          <thead><tr><th scope="col">Day</th><th scope="col">Requests</th></tr></thead>
          <tbody>
            {days.map((d, k) => (
              <tr key={d.day}><th scope="row">{label(k)}</th><td>{n(d.requests)}{d.ranOut ? ' (ran out)' : ''}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
