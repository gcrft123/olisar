// ── The memory replay ───────────────────────────────────────────────────────────────
// A fullscreen walk through a server's history, featuring the form, opened from the
// Knowledge page. Four stages, with the form in the same place for the first three:
//
//   start     what it reads and what it costs, and Start (no settings)
//   remember  the run on the bot, step by step; it goes on if this closes
//   play      what it made, and Play
//   replay    the chapters (replay-scene.ts)
//
// This file is the React half: the overlay, the three sheets under the form, and the API.
// The scene does everything that moves. Like the update screen, the overlay takes the
// whole window and makes everything else inert while it's open.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from './api'
import { botName } from './botname'
import { createForm, type Form } from './form'
import { CloseX, Icon } from './icons'
import { restoreFocus, toast } from './overlays'
import {
  type Latest, type Preflight, type Progress, type Report, type ReplayState, type Run,
  about, fmt, local, monthYear, plural, share, usd,
} from './replay-data'
import { createScene, type IconKey, type Scene, type Stage } from './replay-scene'
import { Badge, Section, usePoll } from './ui'

// ── Opening it ──
// Any page can open the replay (the Knowledge page does); it's mounted once, beside the
// console's other overlays (main.tsx).
type Opening = { fresh: boolean }
let opener: ((o: Opening) => void) | null = null
/** Open the replay. `fresh` goes to the start sheet even when a finished one exists. */
export function openReplay(o: { fresh?: boolean } = {}) { opener?.({ fresh: !!o.fresh }) }

export function ReplayHost() {
  const [open, setOpen] = useState<Opening | null>(null)
  useEffect(() => { opener = setOpen; return () => { opener = null } }, [])
  return open ? <ReplayOverlay fresh={open.fresh} onClose={() => setOpen(null)} /> : null
}

const STEPS = ['Gathering', 'Counting', 'Reading', 'Writing']
const HALTED = new Set(['paused', 'failed', 'stopped'])

/** A number that climbs toward its target rather than jumping, for the remembering line. */
function useClimb(target: number): number {
  const [shown, setShown] = useState(target)
  const value = useRef(target)
  useEffect(() => {
    let raf = 0, last = 0
    const step = (now: number) => {
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60
      last = now
      const next = value.current + (target - value.current) * (1 - Math.exp(-dt * 2.4))
      value.current = Math.abs(target - next) < 0.5 ? target : next
      setShown(value.current)
      if (value.current !== target) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target])
  return shown
}

function ReplayOverlay({ fresh, onClose }: { fresh: boolean; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const palette = useRef<HTMLDivElement>(null)
  const [scene, setScene] = useState<Scene | null>(null)
  const [noForm, setNoForm] = useState(false)
  const [state, setState] = useState<ReplayState | null>(null)
  const [failed, setFailed] = useState('')
  const [stage, setStage] = useState<Stage | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)   // the run that just finished, for the pulse
  const server = state?.server.name || report?.guild.name || ''

  // Everything else goes inert, and the console's own shortcuts are held back while this is
  // open (they listen on the document). Put back as it was on the way out.
  useLayoutEffect(() => {
    const own = root.current
    if (!own) return
    const prior = new Map<HTMLElement, boolean>()
    const hold = (n: Node) => {
      // The toasts stay usable: a failure toast has to be dismissible from in here.
      if (n === own || !(n instanceof HTMLElement) || prior.has(n) || n.matches('.toast-viewport') || n.querySelector('.toast-viewport')) return
      prior.set(n, n.inert)
      n.inert = true
    }
    Array.from(document.body.children).forEach(hold)
    const mo = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(hold)))
    mo.observe(document.body, { childList: true })
    const was = document.activeElement as HTMLElement | null
    return () => { mo.disconnect(); prior.forEach((v, n) => { n.inert = v }); restoreFocus(was) }
  }, [])

  // The form and the scene. Without WebGL2 the memories get hairline rings instead.
  useEffect(() => {
    const el = root.current, c = canvas.current
    if (!el || !c) return
    let form: Form | null = createForm(c)
    if (!form) setNoForm(true)
    const icon = (name: IconKey, size = 16) => {
      const svg = palette.current?.querySelector(`[data-ic="${name}"] svg`)?.cloneNode(true) as SVGElement | undefined
      const out = svg ?? document.createElementNS('http://www.w3.org/2000/svg', 'svg')
      out.setAttribute('width', String(size)); out.setAttribute('height', String(size)); out.setAttribute('aria-hidden', 'true')
      return out
    }
    const s = createScene({
      root: el, form, icon,
      go: (next) => setStage(next),
      answer: async (text) => {
        try { await api.answerReplay(text); return true }
        catch (e: any) { toast(e?.message || 'Couldn’t save that', 'danger'); return false }
      },
    })
    form?.start()
    setScene(s)
    return () => { s.destroy(); form?.destroy(); form = null; setScene(null) }
  }, [])

  // What to open on: a run in progress (ours, or one that stopped) is remembering; someone
  // else's running is the start sheet's "Watch it"; a finished replay is Play.
  const load = async () => {
    try {
      const st: ReplayState = await api.replay()
      setState(st)
      setRun(st.run)
      // A new replay over one that failed goes to the start sheet; Start replaces it.
      if (st.run && !(fresh && st.run.status === 'failed') && (st.run.mine || HALTED.has(st.run.status))) setStage('remember')
      else if (!st.run && st.latest && !fresh) await toPlay(false)
      else setStage('start')
    } catch (e: any) {
      setFailed(e?.message || 'Couldn’t reach the bot.')
    }
  }
  useEffect(() => { void load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const toPlay = async (justMade: boolean) => {
    const r: Report = await api.replayReport()
    setReport(r)
    setReady(justMade)
    setStage('play')
  }

  // Remembering: poll the run, quickly while it works and slowly while it waits.
  const halted = !!run && HALTED.has(run.status)
  const poll = usePoll(async () => {
    const st: { run: Run | null; latest: Latest | null } = await api.replayStatus()
    setRun(st.run)
    if (!st.run && st.latest) await toPlay(true)
  }, halted ? 15000 : 1500, stage === 'remember')

  const [summary, setSummary] = useState<{ chapters: number; minutes: number } | null>(null)
  useEffect(() => { if (scene && report) { scene.load(report); setSummary(scene.summary()) } }, [scene, report])
  useEffect(() => { if (scene && stage) scene.stage(stage, { ready }) }, [scene, stage])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (scene && stage === 'remember') scene.remember(run?.progress || {}, halted) }, [scene, stage, run, halted])

  // Keys. Escape leaves the overlay from the three sheets; in the replay it goes back to Play
  // (the scene's). Tab stays inside.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      e.stopPropagation()
      if (e.key === 'Escape' && stage !== 'replay') { e.preventDefault(); onClose(); return }
      if (e.key !== 'Tab' || !root.current) return
      const items = Array.from(root.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([type=hidden])'))
        .filter((el) => el.tabIndex >= 0 && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [stage, onClose])

  // Focus the sheet's action once it's there, so Enter starts or plays.
  useEffect(() => {
    if (!stage || stage === 'replay') return
    const t = window.setTimeout(() => root.current?.querySelector<HTMLElement>(`.rp-${stage} .primary:not([disabled]), .rp-${stage} h2`)?.focus({ preventScroll: true }), 420)
    return () => clearTimeout(t)
  }, [stage, state, run])

  const start = async (anyway: boolean) => {
    setBusy(true)
    try {
      const r: { run: Run } = await api.startReplay(anyway)
      setRun(r.run)
      setStage('remember')
    } catch (e: any) {
      toast(e?.message || 'Couldn’t start the replay', 'danger')
      void load()
    } finally { setBusy(false) }
  }
  const retry = async () => {
    setBusy(true)
    try { const r: { run: Run } = await api.retryReplay(); setRun(r.run) }
    catch (e: any) { toast(e?.message || 'Couldn’t resume the replay', 'danger') }
    finally { setBusy(false) }
  }
  const close = () => (stage === 'replay' ? setStage('play') : onClose())

  return createPortal(
    <div ref={root} className={'rp' + (noForm ? ' no-form' : '')} role="dialog" aria-modal="true" aria-label={`Replay of ${server}`}>
      <canvas ref={canvas} className="rp-form" aria-hidden="true" />
      <canvas className="rp-fx" aria-hidden="true" />
      <div className="rp-orb-css" aria-hidden="true" />
      <div className="rp-face" aria-hidden="true"><img src={report?.bot.avatar || '/logo.png'} alt="" /></div>
      <div className="rp-spark" aria-hidden="true" />
      <div className="rp-catch" aria-hidden="true" />

      <header className="rp-hud">
        <div className="rp-hud-name"><bdi className="rp-hud-server">{server}</bdi></div>
        <div className="rp-hud-actions">
          <button type="button" className="ghost icon-btn rp-auto" aria-label="Autoplay" data-tip="Autoplay" aria-pressed="false" />
          <button type="button" className="ghost icon-btn" aria-label={stage === 'replay' ? 'Close the replay' : 'Close'} data-tip="Close" onClick={(e) => { e.stopPropagation(); close() }}>
            <CloseX size={16} />
          </button>
        </div>
      </header>

      <div className="rp-narr" aria-live="polite">
        <div className="rp-n-title" />
        <div className="rp-n-body" />
        <div className="rp-n-extra" />
        <div className="rp-n-foot" />
      </div>
      <ul className="rp-mems" aria-label="Memories" />
      <div className="rp-open" role="dialog" aria-label="Memory">
        <button type="button" className="rp-back ghost icon-btn" aria-label="Back to the replay" data-tip="Back"><Icon.arrowLeft size={18} /></button>
        <div className="rp-open-in" />
      </div>
      <div className="rp-center">
        <p className="rp-center-line" />
        <p className="rp-center-sub" hidden />
        <form hidden>
          <input type="text" aria-label="What should I remember from this year?" autoComplete="off" maxLength={120} dir="auto" />
          <button type="submit">Save</button>
        </form>
        <div className="rp-center-acts" hidden>
          <button type="button" className="rp-again">Watch again</button>
          <button type="button" className="primary rp-done">Done</button>
        </div>
      </div>
      <p className="rp-hint" />
      <nav className="rp-timeline" aria-label="Chapters" />

      {failed ? (
        <section className="rp-under on" aria-label="Replay">
          <h2 tabIndex={-1}>Couldn’t open the replay</h2>
          <p className="rp-note">{failed}</p>
        </section>
      ) : null}
      <StartSheet on={stage === 'start'} state={state} run={run} busy={busy} start={start} watch={() => setStage('remember')} />
      <RememberSheet on={stage === 'remember'} server={server} run={run} busy={busy} retry={retry} stale={poll.stale} />
      <PlaySheet on={stage === 'play'} report={report} summary={summary} play={() => setStage('replay')} />

      {/* The icons the scene builds controls with, so they come from icons.tsx too. */}
      <div ref={palette} hidden aria-hidden="true">
        <span data-ic="play"><Icon.play size={16} /></span>
        <span data-ic="pause"><Icon.pause size={16} /></span>
        <span data-ic="restart"><Icon.restart size={16} /></span>
        <span data-ic="back"><Icon.arrowLeft size={16} /></span>
        <span data-ic="close"><CloseX size={16} /></span>
      </div>
    </div>,
    document.body,
  )
}

// ── The start sheet ──
// No settings: it says what the replay reads and what that costs, and Start says what it
// would do when it can't start cleanly.
function StartSheet({ on, state, run, busy, start, watch }: {
  on: boolean; state: ReplayState | null; run: Run | null; busy: boolean
  start: (anyway: boolean) => void; watch: () => void
}) {
  const p = state?.preflight
  const server = state?.server.name || ''
  const others = run && !run.mine && !HALTED.has(run.status) ? run : null
  const copy = p ? sheetCopy(p, others) : null
  return (
    <section className={'rp-under rp-start' + (on ? ' on' : '')} aria-label="Start a replay">
      <h2 tabIndex={-1}>Replay <bdi>{server}</bdi></h2>
      <p className="rp-meta">{copy?.meta}</p>
      <div className="rp-actions">
        <button type="button" className="primary" disabled={!copy || copy.off || busy} aria-describedby={copy?.notes.length ? 'rp-start-why' : undefined}
          onClick={() => (others ? watch() : start(p?.state === 'indexing'))}>
          {copy?.go || 'Start'}
        </button>
      </div>
      {copy?.notes.map((n, i) => <p key={i} className="rp-note" id={i ? undefined : 'rp-start-why'}>{n}</p>)}
    </section>
  )
}

const READS = 'Channels that aren’t indexed on the Channels tab, and anyone who opted out of search, are left out.'

function sheetCopy(p: Preflight, others: Run | null): { meta: React.ReactNode; go: string; off: boolean; notes: string[] } {
  const reads = <><span className="num">{fmt(p.messages)}</span> messages in <span className="num">{fmt(p.channels)}</span> indexed {p.channels === 1 ? 'channel' : 'channels'}</>
  const cost = p.cost.tier === 'paid'
    ? `${cap1(about(p.cost.minutes))} and ${p.cost.usd != null ? `about ${usd(p.cost.usd)}` : 'a little'} of Gemini usage.`
    : `${cap1(about(p.cost.minutes))} and ${share(p.cost.share || 0)} of today’s Gemini allowance.`
  if (others) {
    const ago = others.started_at ? about((Date.now() - Date.parse(others.started_at)) / 60000).replace(/^about /, '') : ''
    return {
      meta: <><bdi>{others.started_by.name || 'Another admin'}</bdi> started a replay {ago ? `${ago} ago` : 'just now'}</>,
      go: 'Watch it', off: false,
      notes: [`One replay runs at a time.${others.eta ? ` This one is ready in ${about(others.eta / 60)}.` : ''}`],
    }
  }
  if (p.state === 'short') {
    return { meta: reads, go: 'Start', off: true, notes: ['That isn’t enough for a replay yet. Try again after a month or so of conversation.'] }
  }
  if (p.state === 'quota') {
    const hours = p.cost.resets_at ? Math.max(1, Math.round((Date.parse(p.cost.resets_at) - Date.now()) / 3600_000)) : null
    return { meta: reads, go: 'Start', off: true, notes: [`There isn’t enough of today’s Gemini allowance left for this. It resets at midnight Pacific${hours ? `, in about ${plural(hours, 'hour')}` : ''}.`] }
  }
  if (p.state === 'budget') {
    return { meta: reads, go: 'Start', off: true, notes: [`That would take this month past your ${usd(p.cost.budget || 0)} Gemini budget.`] }
  }
  if (p.state === 'indexing' && p.indexing) {
    const ix = p.indexing
    const parts: string[] = []
    if (ix.before) parts.push(`what came before ${monthYear(ix.before, null)}`)
    if (ix.unread) parts.push(`${plural(ix.unread, 'channel')} it hasn’t reached yet`)
    const missing = parts.length ? `A replay now would leave out ${parts.join(', and ')}.` : 'A replay now would leave some of it out.'
    const left = ix.minutes > 5 ? ` Indexing finishes in ${about(ix.minutes)}.` : ''
    return {
      meta: <>The search index has read <span className="num">{ix.percent}%</span> of the history so far</>,
      go: 'Start anyway', off: false, notes: [missing + left, READS],
    }
  }
  return { meta: reads, go: 'Start', off: false, notes: [cost, READS] }
}
const cap1 = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)

// ── Remembering ──
// The real job runs for minutes on a big server and goes on if this closes. Four steps under
// the form; what each moment it keeps looks like stays out of focus, so nothing is spoiled.
function RememberSheet({ on, server, run, busy, retry, stale }: {
  on: boolean; server: string; run: Run | null; busy: boolean; retry: () => void; stale: boolean
}) {
  const p: Progress = run?.progress || {}
  const phase = Math.min(4, p.phase ?? 0)
  const halted = !!run && HALTED.has(run.status)
  const read = useClimb(p.read || 0)
  const people = useClimb(phase >= 1 ? p.people || 0 : 0)
  const channels = useClimb(phase >= 1 ? p.channels || 0 : 0)
  const moments = useClimb(p.moments || 0)
  const found = <><span className="num">{fmt(moments)}</span> {Math.round(moments) === 1 ? 'moment' : 'moments'} found</>
  let line: React.ReactNode = null
  let note = ''
  if (halted && run) {
    line = <>{run.status === 'paused' ? 'Paused' : 'Stopped'} · {found}</>
    if (run.status === 'paused') {
      const until = run.paused_until ? Math.max(1, Math.round((Date.parse(run.paused_until) - Date.now()) / 3600_000)) : null
      note = run.pause_reason === 'budget'
        ? 'This month’s Gemini budget ran out. It carries on by itself on the first of the month. Nothing read so far is lost.'
        : `Today’s Gemini allowance ran out. It carries on by itself after midnight Pacific${until ? `, in about ${plural(until, 'hour')}` : ''}. Nothing read so far is lost.`
    } else if (run.status === 'stopped') {
      note = `${botName()} went offline partway through. Nothing read so far is lost, and trying again carries on from here.`
    } else {
      note = run.error?.startsWith('There’s nothing') || run.error?.startsWith("There's nothing")
        ? run.error
        : `It stopped partway through${run.error ? ` (${run.error})` : ''}. Nothing read so far is lost, and trying again carries on from here.`
    }
  } else if (phase === 0) {
    line = <><span className="num">{fmt(read)}</span> messages read {p.where ? <span className="rp-where">{p.where}</span> : null}</>
  } else if (phase === 1) {
    line = <><span className="num">{fmt(people)}</span> people · <span className="num">{fmt(channels)}</span> channels · <span className="num">{fmt(p.years || 1)}</span> {(p.years || 1) === 1 ? 'year' : 'years'}</>
  } else if (phase === 2) {
    line = <>Found <span className="num">{fmt(moments)}</span> {Math.round(moments) === 1 ? 'moment' : 'moments'} {p.where ? <span className="rp-where">{p.where}</span> : null}</>
  } else if (phase === 3) {
    line = p.chapters ? <>Writing <span className="num">{p.chapters}</span> chapters</> : <>Writing</>
  } else {
    line = <>Ready</>
  }
  const status = halted ? `${run?.status === 'paused' ? 'Paused' : 'Stopped'}. ${note}` : STEPS[phase] || 'Ready'
  return (
    <div className={'rp-under rp-remember' + (on ? ' on' : '')}>
      <h2 tabIndex={-1}>Remembering <bdi>{server}</bdi></h2>
      <ol className="rp-steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i < phase ? 'done' : i === phase ? 'now' + (halted ? ' halt' : '') : ''} aria-current={i === phase ? 'step' : undefined}>{s}</li>
        ))}
      </ol>
      <p className="rp-live">{line}</p>
      {stale && !halted ? <p className="rp-note rp-rem-note">Can’t reach the bot. This is the last progress it reported.</p> : null}
      {note ? <p className="rp-note rp-rem-note">{note}</p> : null}
      {halted && run?.status !== 'paused' ? (
        <div className="rp-actions"><button type="button" className="primary" disabled={busy} onClick={retry}>Try again</button></div>
      ) : null}
      <p className="visually-hidden" role="status">{status}</p>
    </div>
  )
}

// ── Play ──
// Where remembering ends: what it made, and Play. There's nothing to change: the replay
// plays as Olisar wrote it.
function PlaySheet({ on, report, summary, play }: {
  on: boolean; report: Report | null; summary: { chapters: number; minutes: number } | null; play: () => void
}) {
  const year = report ? local(report.created, report.tz).y : null
  return (
    <section className={'rp-under rp-play' + (on ? ' on' : '')} aria-label="Play the replay">
      <h2 tabIndex={-1}>{report ? <><bdi>{report.guild.name}</bdi>, {year} to now</> : null}</h2>
      <p className="rp-meta">
        {report && summary ? `${plural(summary.chapters, 'chapter')} · about ${plural(summary.minutes, 'minute')} · from ${plural(report.totals.messages, 'message')}` : null}
      </p>
      <div className="rp-actions">
        <button type="button" className="primary" disabled={!report} onClick={play}><Icon.play size={15} /> Play</button>
      </div>
    </section>
  )
}

// ── On the Knowledge page ──
// Where the replay opens from: it reads the search index, which is the section above it.
// A run going on (anyone's) shows here too, since it goes on with the console closed.
export function ReplaySection() {
  const [data, setData] = useState<{ run: Run | null; latest: Latest | null } | null>(null)
  const working = !!data?.run && !HALTED.has(data.run.status)
  const poll = usePoll(() => api.replayStatus().then(setData), working ? 5000 : 60000)
  const run = data?.run, latest = data?.latest
  let status: React.ReactNode
  if (!data) status = poll.stale ? <span className="rx-dim">Can’t reach the bot.</span> : <span className="rx-dim">Checking…</span>
  else if (run) {
    status = run.status === 'paused' ? <Badge tone="warning" icon="danger-circle">Paused</Badge>
      : run.status === 'failed' || run.status === 'stopped' ? <Badge tone="warning" icon="danger-circle">Stopped</Badge>
      : <Badge tone="info" busy>Remembering…</Badge>
  } else if (latest) {
    status = <>Made <b>{new Date(latest.finished_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}</b>
      <span className="rx-dim"> · {plural(latest.chapters, 'chapter')}</span></>
  } else status = <span className="rx-dim">Not made yet</span>
  return (
    <Section title="Replay" hint={`A walk through this server’s history, told by ${botName()}.`}>
      <div className="reindex-top">
        <div className="reindex-stat">{status}</div>
        <div className="reindex-actions">
          {run ? <>
                <button type="button" onClick={() => openReplay()}>{run.mine || HALTED.has(run.status) ? 'Open' : 'Watch'}</button>
                {run.status === 'failed' ? <button type="button" className="ghost" onClick={() => openReplay({ fresh: true })}>New replay</button> : null}
              </>
            : latest ? <>
                <button type="button" onClick={() => openReplay()}><Icon.play size={14} /> Play</button>
                <button type="button" className="ghost" onClick={() => openReplay({ fresh: true })}>New replay</button>
              </>
            : <button type="button" onClick={() => openReplay()}><Icon.replay size={15} /> Make a replay</button>}
        </div>
      </div>
    </Section>
  )
}
