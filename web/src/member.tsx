// Member portal — what Olisar knows about *you*, for members who aren't admins.
//
// Every route behind this page answers for the caller and nobody else, and nothing here
// renders message content the caller didn't write. That keeps the surface clear of the
// channel-visibility problem entirely: the server-wide search index spans channels a member
// may not be able to see in Discord, so anything showing other people's messages would have
// to re-derive Discord's permission model and get it right every time.
//
// Layout departs from the console's nav rail + stacked cards on purpose. This is five
// sections in one scroll, read more than operated, so it uses a label rail and hairline
// seams with no panels at all — see the .mp-* block in index.css.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, setGuild as apiSetGuild, setMemberCsrf } from './api'
import { CloseX, Icon } from './icons'
import { confirmDialog, promptDialog, toast } from './overlays'
import { SettingsModal, clearPendingReport, pendingReport } from './settings'
import { DonutChart, Segmented, Spinner, Toggle, U_SERIES, uReq, type DonutItem } from './ui'

type Server = { id: string; name: string; icon: string }
type Session = { user_id: string; username: string; avatar: string; csrf: string; servers: Server[] }
type Counts = { messages: number; indexed: number; facts: number; reminders: number }
type Breakdown = { label: string; value: number }
type Overview = {
  counts: Counts
  first_seen: string | null
  last_seen: string | null
  settings: { memory_opt_out: boolean; search_opt_out: boolean; dm_opt_out: boolean; pause_until: string | null }
  persona_visible: boolean
  persona: string
  persona_updated_at: string | null
  breakdowns?: Record<string, Breakdown[]>
  /** Hour-of-day histogram, indexed 0-23, in UTC. Rotated client-side — see localTimeOfDay. */
  hours_utc?: number[]
}
type Fact = {
  id: number; kind: string; content: string; salience: number
  event_date: string | null; created_at: string; source_link: string | null
}
type Reminder = {
  id: number; content: string; scheduled_at: string; target: string; source: string; created_at: string
}

const MEMBER_GUILD_KEY = 'olisar_member_guild'

const KIND_LABEL: Record<string, string> = {
  fact: 'Fact', preference: 'Preference', event: 'Event',
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function fmtWhen(iso: string): { day: string; time: string } {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return { day: '', time: '' }
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  const tomorrow = new Date(today.getTime() + 86400e3)
  const day = sameDay ? 'Today' : d.toDateString() === tomorrow.toDateString() ? 'Tomorrow'
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  // 24-hour, matching every other clock in the console.
  const time = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
  return { day, time }
}

// The server ships the hour histogram in UTC because it doesn't know the member's timezone.
// The browser does, so the rotation happens here: "you talk mostly in the evening" is a claim
// about *their* evening, and bucketing server-side would be wrong by up to half a day.
const TIME_BUCKETS: { label: string; from: number }[] = [
  { label: 'Night (0–6)', from: 0 },
  { label: 'Morning (6–12)', from: 6 },
  { label: 'Afternoon (12–18)', from: 12 },
  { label: 'Evening (18–24)', from: 18 },
]

function localTimeOfDay(hoursUtc: number[] | undefined): Breakdown[] {
  if (!hoursUtc || hoursUtc.length !== 24 || !hoursUtc.some((n) => n > 0)) return []
  // getTimezoneOffset is minutes *behind* UTC, so negate it. Rounded to the nearest hour:
  // the half-hour zones exist, and a 6-hour bucket doesn't care about 30 minutes.
  const shift = Math.round(-new Date().getTimezoneOffset() / 60)
  const local = new Array(24).fill(0)
  hoursUtc.forEach((n, h) => { local[(((h + shift) % 24) + 24) % 24] += n })
  // Chronological, not sorted by size — the hue is assigned by position.
  return TIME_BUCKETS.map((b, i) => {
    const to = i + 1 < TIME_BUCKETS.length ? TIME_BUCKETS[i + 1].from : 24
    return { label: b.label, value: local.slice(b.from, to).reduce((s, n) => s + n, 0) }
  })
}

function fmtAgo(iso: string | null): string {
  if (!iso) return ''
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400e3)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days} days ago`
  return fmtDate(iso)
}

// ── Statistic chip ───────────────────────────────────────────────────────────
// The chip wears the hue of its own largest slice, so hovering connects the figure to the
// arc that dominates it. Categories arrive from the server in a stable order (channel
// order, enum order, chronological) rather than sorted by size: the hue is assigned by
// position, so sorting would put the biggest slice at us0 every time and make every chip
// on the page blue — a colour encoding nothing but "first".
function StatChip(
  { value, items, unit, caption, note, centerLabel }:
  { value: string; items: Breakdown[]; unit: string; caption: string; note?: string; centerLabel?: string },
) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const popId = useId()

  const total = useMemo(() => items.reduce((s, x) => s + x.value, 0), [items])
  // Mirrors DonutChart's own folding so the chip can't claim a hue the ring doesn't show.
  const dominantCls = useMemo(() => {
    const priced = items.filter((it) => it.value > 0).map((it) => ({ ...it, frac: it.value / (total || 1) }))
    const small = priced.filter((it) => it.frac < 0.05)
    const base = small.length >= 2
      ? [...priced.filter((it) => it.frac >= 0.05), { label: 'Other', value: small.reduce((s, x) => s + x.value, 0), frac: 0 }]
      : priced
    if (!base.length) return U_SERIES[0]
    let bestIdx = 0
    base.forEach((s, i) => { if (s.value > base[bestIdx].value) bestIdx = i })
    return base[bestIdx].label === 'Other' ? 'us-mut' : U_SERIES[bestIdx % U_SERIES.length]
  }, [items, total])

  // Clamp into the viewport rather than mirror-flipping: the chip sits mid-sentence, so
  // flipping the anchor just moves the overflow to the other side.
  //
  // The viewport width is sampled while nothing is open. An absolutely positioned popover
  // that overflows widens what innerWidth *and* documentElement.clientWidth report, so
  // measuring from inside the placement pass is circular — it reads a viewport wide enough
  // to hold the overflow it was called to fix, and corrects by almost nothing.
  const place = useCallback(() => {
    const pop = popRef.current
    if (!pop) return
    pop.style.transform = ''
    const box = pop.getBoundingClientRect()
    const vw = viewportWidth()
    const margin = 10
    let dx = 0
    if (box.right > vw - margin) dx = (vw - margin) - box.right
    if (box.left + dx < margin) dx = margin - box.left
    if (dx) pop.style.transform = `translateX(${Math.round(dx)}px)`
  }, [])

  useEffect(() => { if (open) place() }, [open, place])

  if (!items.length) return <b className="mono">{value}</b>

  return (
    <span className="mp-chipwrap" ref={wrapRef}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className={'mp-chip ' + dominantCls}
        aria-expanded={open}
        aria-describedby={open ? popId : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => { e.preventDefault(); setOpen((o) => !o) }}
        onKeyDown={(e) => { if (e.key === 'Escape' && open) { setOpen(false) } }}
      >
        {value}
      </button>
      {open && (
        <div className="mp-pop" id={popId} ref={popRef} role="group" aria-label={caption}>
          <div className="mp-pop-in">
            <div className="mp-pop-head">
              <b>{caption}</b>
              {note ? <span>{note}</span> : null}
            </div>
            <DonutChart
              items={items as DonutItem[]}
              total={total}
              unit={unit}
              size={158}
              centerLabel={centerLabel}
              caption={caption}
            />
          </div>
        </div>
      )}
    </span>
  )
}

// Sampled on load and on resize, never while a popover is open — see place().
let _vw = typeof document !== 'undefined' ? document.documentElement.clientWidth : 0
function viewportWidth(): number { return _vw }
if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => {
    if (!document.querySelector('.mp-pop')) _vw = document.documentElement.clientWidth
  })
}

// ── Server switcher ──────────────────────────────────────────────────────────
function ServerSwitcher(
  { servers, current, onPick }: { servers: Server[]; current: Server; onPick: (s: Server) => void },
) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  // A switcher with one option is a control that can't do anything.
  if (servers.length < 2) {
    return <span className="mp-who"><span className="nm">{current.name}</span></span>
  }
  return (
    <div className="switcher" ref={ref}>
      <button className="btn" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((o) => !o)}>
        {current.name}
        <Icon.chevron size={14} />
      </button>
      {open && (
        <div className="menu" role="radiogroup" aria-label="Choose a server">
          {servers.map((s) => (
            <button
              key={s.id} role="radio" aria-checked={s.id === current.id}
              onClick={() => { onPick(s); setOpen(false) }}
            >
              {s.name}
              {s.id === current.id ? <Icon.check size={15} className="tick" /> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── The portal ───────────────────────────────────────────────────────────────
export function MemberPortal({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [server, setServer] = useState<Server | null>(() => {
    const saved = localStorage.getItem(MEMBER_GUILD_KEY)
    return session.servers.find((s) => s.id === saved) ?? session.servers[0] ?? null
  })
  const [overview, setOverview] = useState<Overview | null>(null)
  const [facts, setFacts] = useState<Fact[] | null>(null)
  const [reminders, setReminders] = useState<Reminder[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // The console's half of this lives in App; a member never reaches that branch, so the
  // portal opens the same pane itself. One "Report this" link, whichever way you sign in.
  const [report, setReport] = useState('')

  useEffect(() => { setMemberCsrf(session.csrf) }, [session.csrf])

  useEffect(() => {
    const token = pendingReport()
    if (!token) return
    setReport(token)
    setSettingsOpen(true)
  }, [])

  const load = useCallback(async () => {
    if (!server) return
    apiSetGuild(server.id)
    setError(null)
    setOverview(null); setFacts(null); setReminders(null)
    try {
      const [o, f, r] = await Promise.all([api.memberOverview(), api.memberFacts(), api.memberReminders()])
      setOverview(o); setFacts(f.facts); setReminders(r.reminders)
    } catch (e: any) {
      setError(e?.message || 'Could not load your data.')
    }
  }, [server])

  useEffect(() => { load() }, [load])

  if (!server) {
    return (
      <div className="login">
        <div className="box wide">
          <div className="mark warn"><Icon.access size={26} weight="Bold" /></div>
          <h1>Nothing to show</h1>
          <p>None of the servers you share with Olisar have opened this page.</p>
          <div className="login-actions">
            <button className="ghost" onClick={onSignOut}><Icon.logout size={16} /> Log out</button>
          </div>
        </div>
      </div>
    )
  }

  const patch = async (body: Parameters<typeof api.memberSettings>[0], optimistic: Partial<Overview['settings']>) => {
    if (!overview) return
    const prev = overview.settings
    setOverview({ ...overview, settings: { ...prev, ...optimistic } })
    try {
      await api.memberSettings(body)
    } catch (e: any) {
      setOverview({ ...overview, settings: prev })
      toast(e?.message || 'Could not save that.', 'danger')
    }
  }

  const removeFact = async (f: Fact) => {
    try {
      await api.memberDeleteFact(f.id)
      setFacts((cur) => (cur || []).filter((x) => x.id !== f.id))
      setOverview((o) => (o ? { ...o, counts: { ...o.counts, facts: Math.max(0, o.counts.facts - 1) } } : o))
    } catch (e: any) { toast(e?.message || 'Could not delete that.', 'danger') }
  }

  const cancelReminder = async (r: Reminder) => {
    try {
      await api.memberCancelReminder(r.id)
      setReminders((cur) => (cur || []).filter((x) => x.id !== r.id))
    } catch (e: any) { toast(e?.message || 'Could not cancel that.', 'danger') }
  }

  const download = async () => {
    setBusy(true)
    try {
      const blob = await api.memberExport()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `olisar-my-data-${server.id}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e: any) { toast(e?.message || 'Could not build the download.', 'danger') } finally { setBusy(false) }
  }

  const erase = async () => {
    // The phrase proves intent; naming the server in the title is what makes it prove the
    // *right* intent, since the switcher is elsewhere on screen.
    const ok = await confirmDialog({
      title: `Erase everything Olisar knows about you in ${server.name}`,
      message: (
        <>
          {uReq(overview?.counts.messages ?? 0)} messages, {uReq(overview?.counts.indexed ?? 0)} index entries,{' '}
          {uReq(overview?.counts.facts ?? 0)} remembered things and {uReq(reminders?.length ?? 0)} reminders.
          Your Discord messages stay; Olisar's copy doesn't.
        </>
      ),
      confirmLabel: 'Erase everything',
      requirePhrase: { phrase: server.name },
      tone: 'danger',
    })
    if (ok !== true) return
    setBusy(true)
    try {
      await api.memberForget(false)
      toast('Erased.', 'success')
      await load()
    } catch (e: any) { toast(e?.message || 'Could not erase.', 'danger') } finally { setBusy(false) }
  }

  const correct = async () => {
    const text = await promptCorrection()
    if (!text) return
    try {
      await api.memberCorrection(text)
      toast('Noted — Olisar will take that into account.', 'success')
    } catch (e: any) { toast(e?.message || 'Could not save that.', 'danger') }
  }

  const b = overview?.breakdowns || {}
  const timeOfDay = useMemo(() => localTimeOfDay(overview?.hours_utc), [overview?.hours_utc])
  // The headline figure is the evening share, so the chip and the ring's centre agree.
  const eveningShare = useMemo(() => {
    const total = timeOfDay.reduce((t, x) => t + x.value, 0)
    if (!total) return null
    const evening = timeOfDay.find((x) => x.label.startsWith('Evening'))?.value ?? 0
    return Math.round((evening / total) * 100)
  }, [timeOfDay])
  const c = overview?.counts
  const s = overview?.settings
  const pauseHours = s?.pause_until && new Date(s.pause_until) > new Date()
    ? (new Date(s.pause_until).getTime() - Date.now() > 36 * 3600e3 ? 168 : 24)
    : 0

  return (
    <div className="mp">
      <header className="mp-top">
        <div className="brand"><img src="/logo.png" alt="" width={26} height={26} /> Olisar</div>
        <div className="spacer" />
        <ServerSwitcher
          servers={session.servers}
          current={server}
          onPick={(s2) => { localStorage.setItem(MEMBER_GUILD_KEY, s2.id); setServer(s2) }}
        />
        <span className="mp-who">
          <span className="nm">{session.username}</span>
          {session.avatar
            ? <img className="avatar" src={session.avatar} alt="" width={24} height={24} />
            : <span className="avatar">{(session.username[0] || '?').toUpperCase()}</span>}
        </span>
        {/* Size and Feedback only: every other pane in that modal configures the server or
            the install, which is not a member's to change. */}
        <button className="ghost icon-btn" aria-label="Settings" data-tip="Settings"
          onClick={() => setSettingsOpen(true)}>
          <Icon.settings size={16} />
        </button>
        <button className="ghost icon-btn" aria-label="Log out" data-tip="Log out" onClick={onSignOut}>
          <Icon.logout size={16} />
        </button>
      </header>

      <section className="mp-open">
        <h1>What Olisar has of yours</h1>
        {error ? <p className="mp-lead">{error}</p> : !overview ? <Spinner label="Loading your data…" /> : (
          <p className="mp-lead">
            In {server.name} it has kept{' '}
            <StatChip value={uReq(c!.messages)} items={b.messages || []} unit="messages"
              caption="Where your stored messages came from" note="Only channels an admin set to remember." />
            {' '}of your messages and made{' '}
            <StatChip value={uReq(c!.indexed)} items={b.indexed || []} unit="indexed"
              caption="Where your searchable messages came from"
              note="The index reaches every channel, including ones Olisar never speaks in." />
            {' '}findable by anyone who asks. From those it has written down{' '}
            <StatChip value={uReq(c!.facts)} items={b.facts || []} unit="things"
              caption="What kind of thing it wrote down" />
            {' '}things about you.
            {/* A second sentence, assembled from whichever of the two figures exist — a
                fresh member has neither, and the copy must not leave a dangling clause. */}
            {b.days?.length ? (
              <> You've turned up on{' '}
                <StatChip value={uReq(b.days.reduce((t, x) => t + x.value, 0))} items={b.days} unit="days"
                  caption="Days you turned up, by month" note="A day counts once, however much you said." />
                {' '}days{eveningShare === null ? '.' : ','}
              </>
            ) : null}
            {eveningShare !== null ? (
              <>{b.days?.length ? ' and ' : ' '}
                <StatChip value={`${eveningShare}%`} items={timeOfDay} unit="of msgs"
                  centerLabel={`${eveningShare}%`}
                  caption="When you talk" note="Your local time, from the timestamps on your messages." />
                {' '}of what you say lands after six in the evening.
              </>
            ) : null}
          </p>
        )}
      </section>

      {overview && (
        <>
          <div className="mp-seam" />
          <section className="mp-sec" aria-labelledby="mp-imp">
            <div className="rail"><h2 id="mp-imp">How it sees you</h2></div>
            <div className="body">
              {!overview.persona_visible
                ? <p className="mp-quiet">{server.name}'s admins keep impressions private.</p>
                : !overview.persona
                  ? <p className="mp-quiet">Not enough messages yet.</p>
                  : (
                    <>
                      <p className="mp-impression">{overview.persona}</p>
                      <div className="mp-impression-foot">
                        <span className="when mono">Updated {fmtAgo(overview.persona_updated_at)}</span>
                        <button className="btn" onClick={correct}>That's not right</button>
                      </div>
                    </>
                  )}
            </div>
          </section>

          <div className="mp-seam flow" />
          <section className="mp-sec" aria-labelledby="mp-facts">
            <div className="rail"><h2 id="mp-facts">What it remembers</h2></div>
            <div className="body">
              {facts === null ? <Spinner /> : facts.length === 0
                ? <p className="mp-quiet">Nothing remembered here.</p>
                : (
                  <div className="mp-list">
                    {facts.map((f) => (
                      <div className="mp-item" key={f.id}>
                        <div className="body">
                          <div className="what">{f.content}</div>
                          <div className="meta">
                            <span className={'kind' + (f.kind === 'event' ? ' ev' : '')}>
                              {KIND_LABEL[f.kind] || f.kind}
                            </span>
                            {f.source_link
                              ? <a className="mp-src" href={f.source_link} target="_blank" rel="noreferrer">Source</a>
                              : <span>Source message pruned</span>}
                            <span className="mono">{fmtDate(f.created_at)}</span>
                          </div>
                        </div>
                        <div className="acts">
                          <button className="danger icon-btn" data-tip="Delete"
                            aria-label={`Delete: ${f.content}`} onClick={() => removeFact(f)}>
                            <Icon.trash size={15} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
            </div>
          </section>

          <div className="mp-seam" />
          <section className="mp-sec" aria-labelledby="mp-ctl">
            <div className="rail"><h2 id="mp-ctl">What it may keep</h2></div>
            <div className="body">
              {/* Labels are permissions, not opt-outs. The database stores memory_opt_out;
                  a control you switch off to stop opting out is a trap. */}
              <div className="mp-field">
                <div className="mp-copy"><span className="mp-lbl" id="mp-l-mem">Remember me here</span></div>
                <Toggle value={!s!.memory_opt_out} ariaLabel="Remember me here"
                  onChange={(v) => patch({ memory_opt_out: !v }, { memory_opt_out: !v })} />
              </div>

              <div className="mp-field">
                <div className="mp-copy"><span className="mp-lbl" id="mp-l-search">Let anyone search my messages</span></div>
                <Toggle value={!s!.search_opt_out} ariaLabel="Let anyone search my messages"
                  onChange={(v) => patch({ search_opt_out: !v }, { search_opt_out: !v })} />
              </div>

              <div className="mp-field">
                <div className="mp-copy">
                  <span className="mp-lbl" id="mp-l-dm">Save our direct messages</span>
                  <div className="mp-desc">Every server, not just this one.</div>
                </div>
                <Toggle value={!s!.dm_opt_out} ariaLabel="Save our direct messages"
                  onChange={(v) => patch({ dm_opt_out: !v }, { dm_opt_out: !v })} />
              </div>

              <div className="mp-field">
                <div className="mp-copy"><span className="mp-lbl" id="mp-l-pause">Pause everything</span></div>
                <Segmented
                  className="useg"
                  value={pauseHours}
                  ariaLabel="Pause everything"
                  onChange={(v) => patch({ pause_hours: v }, {
                    pause_until: v ? new Date(Date.now() + v * 3600e3).toISOString() : null,
                  })}
                  options={[{ value: 0, label: 'Off' }, { value: 24, label: '24 hours' }, { value: 168, label: '7 days' }]}
                />
              </div>

              {pauseHours > 0 && (
                <div className="callout warning mp-pause">
                  <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                  <div className="callout-body">
                    Paused until <b className="mono">{fmtWhen(s!.pause_until!).day}, {fmtWhen(s!.pause_until!).time}</b>
                  </div>
                  <button className="btn ghost" onClick={() => patch({ pause_hours: 0 }, { pause_until: null })}>
                    Resume now
                  </button>
                </div>
              )}
            </div>
          </section>

          <div className="mp-seam flow" />
          <section className="mp-sec" aria-labelledby="mp-rem">
            <div className="rail"><h2 id="mp-rem">Waiting on</h2></div>
            <div className="body">
              {reminders === null ? <Spinner /> : reminders.length === 0
                ? <p className="mp-quiet">Nothing scheduled.</p>
                : (
                  <div className="mp-list">
                    {reminders.map((r) => {
                      const w = fmtWhen(r.scheduled_at)
                      return (
                        <div className="mp-item" key={r.id}>
                          <div className="mp-when">{w.day}<br />{w.time}</div>
                          <div className="body">
                            <div className="what">{r.content}</div>
                            <div className="meta">
                              {/* event_fact is the one people don't remember agreeing to. */}
                              <span className={'kind' + (r.source === 'event_fact' ? ' ev' : '')}>
                                {r.source === 'event_fact' ? 'Olisar noticed' : 'You asked'}
                              </span>
                              <span>{r.target === 'dm' ? 'Sent by DM' : 'Posted in the channel'}</span>
                            </div>
                          </div>
                          <div className="acts">
                            <button className="ghost icon-btn" data-tip="Cancel"
                              aria-label={`Cancel reminder: ${r.content}`} onClick={() => cancelReminder(r)}>
                              <CloseX size={15} />
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
            </div>
          </section>

          <div className="mp-seam" />
          <section className="mp-sec" aria-labelledby="mp-dz">
            <div className="rail"><h2 id="mp-dz">Export or erase</h2></div>
            <div className="body">
              <div className="mp-dz">
                <div className="mp-copy"><b>Download everything</b></div>
                <button className="btn" disabled={busy} onClick={download}>
                  <Icon.download size={15} /> Download
                </button>
              </div>
              <div className="mp-dz">
                <div className="mp-copy"><b>Erase everything in {server.name}</b></div>
                <button className="btn danger" disabled={busy} onClick={erase}>Erase</button>
              </div>
            </div>
          </section>
        </>
      )}

      {settingsOpen && (
        <SettingsModal
          sections={['size', 'feedback']}
          initialSection={report ? 'feedback' : undefined}
          report={report}
          onClose={() => {
            setSettingsOpen(false)
            if (report) { clearPendingReport(); setReport('') }
          }}
        />
      )}
    </div>
  )
}

// The console's own prompt dialog — window.prompt is forbidden by the design lint and
// no styling can reach it.
async function promptCorrection(): Promise<string | null> {
  const text = await promptDialog({
    title: 'What has it got wrong?',
    confirmLabel: 'Send',
    prompt: { placeholder: "I don't actually fly a Cutlass any more.", multiline: true },
  })
  return text && text.trim() ? text.trim() : null
}
