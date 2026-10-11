// ── Usage ───────────────────────────────────────────────────────────────────
// Laid out in the order someone opening it needs answers: how much is left today across the
// fallback chain, which model is replying, where the requests go, then everything else. The
// design, and what the page before it got wrong, is in design/usage-page/.
//
// Every figure is a "left" figure, so every meter fills with what's left rather than what's
// used — a departure from DESIGN.md's meter recipe, made because a fill that meant the
// opposite of the number beside it read wrong.
//
// A key with billing on has no daily allowance to count down, so the page shows money
// instead: the month's spend against the budget, cost per model, and spend per day. A free
// key that has run out is told what the day would have cost with billing on.
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
  cost: number
}

/** What today and the month cost, or would have on a free key. `budget` is null unless
 *  the key has billing on and the operator set one. */
export type Billing = {
  today: number
  month: number
  projected: number
  budget: number | null
  action: 'stop' | 'cheapest'
  state: 'none' | 'ok' | 'warn' | 'over'
}

type Live = {
  ts: string
  tier: 'free' | 'paid' | null
  exhausted: boolean
  day: string
  day_start: string
  reset_at: string
  chain: ChainModel[]
  memory_search: { model: string; requests: number; limit: number; spent: boolean }
  web_search: { requests: number; limit: number; period: 'day' | 'month'; spent: boolean }
  billing: Billing
}

type Range = 'today' | '7' | '30'

type Summary = {
  day: string
  features: Record<Range, Record<string, number>>
  yesterday: { requests: number; tokens: number } | null
  busiest_minute: { model: string; requests: number; limit: number; at: string | null } | null
  last_ran_out: string | null
  days: { day: string; requests: number; ran_out_at: string | null; cost: number }[]
}

// When the reading arrived, and how far the server's clock is from this one, so countdowns
// run between polls and "in 8h 12m" doesn't inherit a wrong clock on this machine.
type Received = Live & { receivedAt: number; skew: number }

// ── Formatting ──────────────────────────────────────────────────────────────
// Times show in the viewer's own zone; only the day boundary is Pacific, because it's
// Google's.
const PACIFIC = 'America/Los_Angeles'
const n = (v: number) => Math.round(v).toLocaleString()
/** Dollars to the cent, whole dollars from $100 (or for a round sum like a budget), and
 *  "<$0.01" for a fraction of a cent. */
export function usd(v: number) {
  if (v <= 0) return '$0'
  if (v < 0.005) return '<$0.01'
  return '$' + (v >= 100 || Number.isInteger(v) ? Math.round(v).toLocaleString() : v.toFixed(2))
}
/** A projection: cents would be false precision past a few dollars. */
const usdAbout = (v: number) => (v >= 10 ? usd(Math.round(v)) : usd(v))
// Where a key's project turns billing on: "Set up billing" beside it.
export const AI_STUDIO_KEYS = 'https://aistudio.google.com/apikey'
// Where replies go once a budget is spent, cheapest first (olisar.gemini.spend.BUDGET_CHAIN).
const BUDGET_CHAIN = ['gemini-2.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite']
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
  // Past a billed key's budget, replies go to the cheapest model alone, or nowhere.
  const budgetOut = live.tier === 'paid' && live.billing?.state === 'over'
  // The first model that can take a request. With every one resting for a moment, the one
  // back soonest is who replies next.
  const cur = budgetOut
    ? (live.billing.action === 'cheapest'
      ? BUDGET_CHAIN.map((name) => chain.find((m) => m.model === name && m.state !== 'spent')).find(Boolean) ?? null
      : null)
    : chain.find((m) => m.state === 'ok')
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
  const paid = live.tier === 'paid'
  return { t, clientNow, chain, cur, allSpent, capTotal, usedTotal, tokens, left, reset, toReset, runsOutAt, paid, billing: live.billing }
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

/** `onGo` opens another page; given only to the operator, who can set a budget. */
export function Usage({ onGo }: { onGo?: (tab: string) => void } = {}) {
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
      <Now R={R} live={live.data} stale={stale} onGo={onGo} />
      <Chain R={R} stale={stale} />
      <Features data={summary.data} range={range} setRange={setRange} />
      <Stats R={R} data={summary.data} day={live.data.day} />
    </div>
  )
}

// ── What's left, and what's replying ────────────────────────────────────────
function Now({ R, live, stale, onGo }: { R: Reading; live: Received; stale: boolean; onGo?: (tab: string) => void }) {
  const memory = live.memory_search
  const web = live.web_search
  const b = R.billing
  return (
    <div className={'u-now' + (stale ? ' stale' : '')}>
      {R.paid ? (
        <div className="u-now-left">
          <div className="u-top"><h2 className="u-eyebrow">Spent this month</h2></div>
          <div className="u-hero">{usd(b.month)}</div>
          <div className="u-hero-sub">
            {b.budget != null
              ? <>of your <b>{usd(b.budget)}</b> budget · on pace for {usdAbout(b.projected)}</>
              : <>On pace for <b>{usdAbout(b.projected)}</b> by the end of the month</>}
          </div>
        </div>
      ) : (
        <div className="u-now-left">
          <div className="u-top"><h2 className="u-eyebrow">Left today</h2></div>
          <div className={'u-hero' + (R.left === 0 ? ' zero' : '')}>{n(R.left)}</div>
          <div className="u-hero-sub">
            of <b>{n(R.capTotal)}</b> requests · resets at {clock(R.reset)}, in {span(R.toReset)}
          </div>
        </div>
      )}
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
        {R.paid ? <PaidPace R={R} onGo={onGo} /> : <Pace R={R} />}
        <div className="u-side-limits">
          {R.paid
            ? <SideCount label="Memory search" count={memory.requests} />
            : <SideMeter label="Memory search" used={memory.requests} limit={memory.limit} spent={memory.spent} />}
          <SideMeter
            label={web.period === 'month' ? 'Free web searches' : 'Web search'}
            used={web.requests} limit={web.limit} spent={web.spent}
          />
        </div>
      </div>
    </div>
  )
}

function Pace({ R }: { R: Reading }) {
  if (R.allSpent) {
    // The one moment billing is worth raising: the bot has stopped, and the day's own
    // usage says what keeping it going would have cost.
    return (
      <div className="u-pace danger">
        <Icon.warn size={16} weight="Bold" />
        <span>
          {botName()} can’t reply until the limits reset at {clock(R.reset)}. With billing on, today would cost about {usd(R.billing.today)}.{' '}
          <a href={AI_STUDIO_KEYS} target="_blank" rel="noreferrer">Turn on billing</a>
        </span>
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

/** The pace line on a key with billing on: where the month is heading, against the budget. */
function PaidPace({ R, onGo }: { R: Reading; onGo?: (tab: string) => void }) {
  const b = R.billing
  const setBudget = onGo && <button className="linklike" onClick={() => onGo('keys')}>{b.budget != null ? 'Change the budget' : 'Set a budget'}</button>
  if (b.state === 'over') {
    return b.action === 'stop' ? (
      <div className="u-pace danger">
        <Icon.warn size={16} weight="Bold" />
        <span>The month’s budget is spent. {botName()} won’t reply until the month ends. {setBudget}</span>
      </div>
    ) : (
      <div className="u-pace warn">
        <Icon.warn size={16} weight="Bold" />
        <span>The month’s budget is spent. Until the month ends, {botName()} replies with its cheapest models, with no web search or Gemini images. {setBudget}</span>
      </div>
    )
  }
  if (b.budget != null && (b.state === 'warn' || b.projected > b.budget)) {
    return (
      <div className="u-pace warn">
        <Icon.warn size={16} weight="Bold" />
        <span>
          {b.projected > b.budget
            ? <>At this pace, the month comes to about {usdAbout(b.projected)}, over the budget.</>
            : <>{Math.floor((b.month / b.budget) * 100)}% of the month’s budget is spent.</>}{' '}
          {setBudget}
        </span>
      </div>
    )
  }
  return (
    <div className="u-pace ok">
      <Icon.check size={16} weight="Bold" />
      <span>{b.budget != null ? 'Within the month’s budget.' : 'No monthly budget.'} {setBudget}</span>
    </div>
  )
}

/** A count with no limit to measure it against. */
function SideCount({ label, count }: { label: string; count: number }) {
  return (
    <div className="u-side">
      <span>{label}</span>
      <span className="v">{n(count)} <s>today</s></span>
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
            <th scope="col" className="c-today">{R.paid ? 'Today' : 'Left today'}</th>
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
                  {R.paid ? (
                    <div className="today">
                      <span className="v wide"><span className="left-v">{usd(m.cost)}</span><s> · {n(m.requests)} req</s></span>
                    </div>
                  ) : (
                    <div className="today">
                      <Meter used={m.requests} limit={m.limit} out={out} lost={out && m.requests < m.limit} />
                      <span className="v"><span className="left-v">{n(m.left)}</span><s> / {n(m.limit)}</s></span>
                    </div>
                  )}
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
  review: 'Extension reviews', canary: 'Health checks', status: 'Status', image: 'Making images', other: 'Other',
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
    R.paid
      ? { k: 'Spent today', big: usd(R.billing.today), cap: data.days.length > 1 ? `${usd(data.days[data.days.length - 2].cost)} yesterday` : '' }
      : { k: 'Last ran out', big: out.big, cap: out.cap },
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
const axisUsd = (v: number) => '$' + (v >= 1000 ? axisNum(v) : v >= 1 ? String(+v.toFixed(1)) : v.toFixed(2))
/** Gridline values for a scale up to `max`: a round step, two or three lines. Steps go no
 *  finer than `floor`: whole requests, or cents. */
function gridlines(max: number, floor = 1) {
  const raw = max / 2.5
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, floor)))
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? 10 * mag
  const out: number[] = []
  for (let v = step; v < max; v += step) out.push(v)
  return out
}

function Days({ R, data }: { R: Reading; data: Summary }) {
  const [box, W] = useBoxWidth(900)
  const [hover, setHover] = useState(-1)
  // Today's bar reads the live count, so it agrees with the figures above it.
  // On a billed key the bars are dollars and there's no daily limit to draw.
  const paid = R.paid
  const days = data.days.map((d, k) => (k === data.days.length - 1
    ? { ...d, requests: R.usedTotal, cost: R.billing.today, ranOut: R.allSpent || !!d.ran_out_at }
    : { ...d, ranOut: !!d.ran_out_at }))
    .map((d) => ({ ...d, value: paid ? d.cost : d.requests }))
  const last = days.length - 1
  const limit = paid ? 0 : R.capTotal
  const fmt = paid ? usd : n
  const axis = paid ? axisUsd : axisNum
  const H = 150, x0 = 38, x1 = W - 4, y0 = H - 22, y1 = 14
  const yMax = Math.max(limit * 1.08, ...days.map((d) => d.value), paid ? 0.05 : 1)
  const yAt = (v: number) => y0 - (v / yMax) * (y0 - y1)
  const slot = (x1 - x0) / Math.max(1, days.length)
  const bw = Math.min(24, slot * 0.56)
  const stride = Math.max(1, Math.ceil(56 / slot))
  const dateOf = (day: string, opts: Intl.DateTimeFormatOptions) =>
    new Date(day + 'T12:00:00Z').toLocaleDateString([], { ...opts, timeZone: 'UTC' })
  const label = (k: number) => (k === last ? 'Today' : dateOf(days[k].day, { month: 'short', day: 'numeric' }))
  const ticks = gridlines(yMax, paid ? 0.01 : 1).filter((v) => paid || Math.abs(yAt(v) - yAt(limit)) > 12)
  const tip = hover >= 0 ? days[hover] : null
  const what = paid ? 'Spend' : 'Requests'

  return (
    <div className="u-daily">
      <div className="u-days-head"><h3>{what} per day</h3><span>last {days.length} days</span></div>
      <div className="u-days" ref={box}>
        <svg
          className="u-days-chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={paid
            ? `Spend per day over the last ${days.length} days. Today so far ${usd(R.billing.today)}.`
            : `Requests per day over the last ${days.length} days, against the daily limit of ${n(limit)}. Today so far ${n(R.usedTotal)}.`}
          onPointerLeave={() => setHover(-1)}
        >
          {ticks.map((v) => (
            <g key={v}>
              <line className="grid" x1={x0} x2={x1} y1={yAt(v)} y2={yAt(v)} />
              <text className="axis" x={x0 - 8} y={yAt(v) + 4} textAnchor="end">{axis(v)}</text>
            </g>
          ))}
          <line className="grid" x1={x0} x2={x1} y1={y0} y2={y0} />
          <text className="axis" x={x0 - 8} y={y0 + 4} textAnchor="end">{paid ? '$0' : '0'}</text>
          {!paid && (
            <>
              <line className="limit" x1={x0} x2={x1} y1={yAt(limit)} y2={yAt(limit)} strokeDasharray="5 4" />
              <text className="limit-txt" x={x0 - 8} y={yAt(limit) + 4} textAnchor="end">{axisNum(limit)}</text>
            </>
          )}
          {days.map((d, k) => {
            const cx = x0 + slot * (k + 0.5)
            const top = yAt(d.value), h = Math.max(0, y0 - top)
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
                {today && <text className="tag" x={Math.min(cx, x1 - 20)} y={top - 7} textAnchor="middle">{fmt(d.value)}</text>}
                {showLabel && (
                  <text className={'axis' + (today ? ' on' : '')} x={today ? Math.min(cx, x1 - 18) : cx} y={y0 + 16} textAnchor="middle">{label(k)}</text>
                )}
              </g>
            )
          })}
        </svg>
        {tip && (
          <div className="u-tip" style={{ left: Math.min(Math.max(x0 + slot * (hover + 0.5), 70), W - 70), top: yAt(tip.value) - 8 }} aria-hidden>
            <b>{fmt(tip.value)}{hover === last ? ' so far' : ''}</b>
            <span>{hover === last ? 'Today' : dateOf(tip.day, { weekday: 'long', month: 'short', day: 'numeric' })}{tip.ranOut ? ' · ran out' : ''}</span>
          </div>
        )}
      </div>
      {/* Every chart ships its numbers twice; the class goes on a wrapping div, never the
          table, or the table sizes itself to its content and pushes the page sideways. */}
      <div className="visually-hidden">
        <table>
          <caption>{what} per day, last {days.length} days.{paid ? '' : ` Daily limit ${n(limit)}.`}</caption>
          <thead><tr><th scope="col">Day</th><th scope="col">{what}</th></tr></thead>
          <tbody>
            {days.map((d, k) => (
              <tr key={d.day}><th scope="row">{label(k)}</th><td>{fmt(d.value)}{d.ranOut ? ' (ran out)' : ''}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
