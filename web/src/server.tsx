import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api } from './api'
import { avatarOf, createActivity, type ActivityItem, type Person } from './activity'
import { BotMenu, intentList, sharedServers, useBots } from './bots'
import { createBrain, type Brain } from './brain'
import { SHAPE, type Mood } from './form'
import { Icon } from './icons'
import { Pane, useArrived, useForm, useInert, useShell } from './onboarding'
import { RoleChip } from './pages'
import { toast, type Tone } from './overlays'
import { CopyText, DisclosureChev, Linkified, PubkeyBox, RedirectRow, usePubkey } from './setup'
import { FeedbackButton } from './settings'
import { reportBody } from './feedback'
import { Badge, Field, Select, Text, usePoll, type BadgeGlyph, type BadgeTone } from './ui'
import { displayVersion } from './version'

type Status = {
  configured?: boolean
  reachable?: boolean
  running?: boolean
  /** Docker's own healthcheck verdict: healthy | unhealthy | starting | '' (none). */
  health?: string
  /** When the container started, and when its healthcheck last ran (ISO; '' when unknown). */
  started_at?: string
  health_at?: string
  /** From the image's OCI labels, so it resolves even while the container is stopped. */
  version?: string
  digest?: string
  url?: string
  /** Why a running server's console has no address (Tailscale refused the key, most often). */
  console_error?: string
  host?: string
  /** The app is updating the VM (see remote.autoupdate). */
  auto_updating?: boolean
  error?: string
}

type UpdateResult = {
  ok?: boolean
  status?: string
  message?: string
  tag?: string
  updated?: boolean
  rolled_back?: boolean
  error?: string
  at?: string
}

/** What the VM's last update attempt should say. Tone drives how it's delivered: a success
 *  expires on its own, a rollback or failure is something the operator has to act on, so it
 *  sticks until dismissed. */
function noteFor(r: UpdateResult | null | undefined): { text: string; tone: Tone } | null {
  if (!r || !r.at) return null
  const tag = r.tag ? `v${displayVersion(r.tag)}` : 'the latest release'
  if (r.rolled_back) return { text: `${tag} failed its healthcheck and was rolled back. The server is on the previous version.`, tone: 'warning' }
  if (r.updated) return { text: `Server updated to ${tag}.`, tone: 'success' }
  if (r.ok === false) return { text: `Last update attempt failed: ${r.message || r.error || 'unknown error'}.`, tone: 'danger' }
  return null
}

// Each reading of the server has a label, a badge, and a form: the form is the server's state,
// quietly. Dim while it's checked, gathering as it starts, whole and calm while it runs, a gap
// sweeping up through it while it updates, a steady tremor while it's unhealthy or can't be
// reached, and drawn in, still and dim once it's stopped.
type Phase = 'checking' | 'starting' | 'running' | 'updating' | 'unhealthy' | 'unreachable' | 'stopping' | 'stopped'
type Orb = { shape: number; v?: number; energy: number; mood: Mood; regather?: boolean; pulse?: number }
const PHASES: Record<Phase, { label: string; chip: BadgeGlyph & { tone: BadgeTone }; orb: Orb }> = {
  checking: { label: 'Checking…', chip: { tone: 'info', busy: true }, orb: { shape: SHAPE.whole, v: 0.92, energy: 0.3, mood: { dim: 0.45 } } },
  starting: { label: 'Starting…', chip: { tone: 'info', busy: true }, orb: { shape: SHAPE.whole, v: 1, energy: 0.8, mood: { dim: 0.85 }, regather: true } },
  running: { label: 'Running', chip: { tone: 'success', icon: 'play-circle' }, orb: { shape: SHAPE.whole, v: 1, energy: 0, mood: {}, pulse: 0.8 } },
  updating: { label: 'Updating…', chip: { tone: 'info', busy: true }, orb: { shape: SHAPE.updating, energy: 0.5, mood: { dim: 0.95 } } },
  unhealthy: { label: 'Unhealthy', chip: { tone: 'danger', icon: 'danger-circle' }, orb: { shape: SHAPE.whole, v: 1, energy: 1.2, mood: { tremor: 0.3, dim: 0.9 } } },
  unreachable: { label: 'Unreachable', chip: { tone: 'danger', icon: 'close-circle' }, orb: { shape: SHAPE.whole, v: 1, energy: 1.2, mood: { tremor: 0.3, dim: 0.9 } } },
  stopping: { label: 'Stopping…', chip: { tone: 'info', busy: true }, orb: { shape: SHAPE.whole, v: 0.8, energy: 0.6, mood: { dim: 0.75 } } },
  stopped: { label: 'Stopped', chip: { tone: 'warning', icon: 'stop-circle' }, orb: { shape: SHAPE.whole, v: 0.7, energy: -0.8, mood: { dim: 0.5 } } },
}

function upFor(ms: number): string {
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'Under a minute'
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'}`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} hour${h === 1 ? '' : 's'}`
  const d = Math.floor(h / 24)
  return `${d} days`
}

/** Shown (loopback-gated, no Discord login) when the app is in server-hosting mode: the bot
 *  runs on the operator's cloud VM, and this is the local control panel that starts/stops it
 *  over SSH (`docker compose up -d` / `stop`) and links to its console. A reconnect flow
 *  re-adopts the VM after a reinstall / reset / IP change.
 *
 *  It has two screens in the first-run frame. The stats screen is the panel: status rows,
 *  whatever needs doing, and the buttons. Once the server runs healthy and Discord lists its
 *  console's sign-in address, that folds away into the final screen (brain.ts): the form in
 *  the middle of the window, the title, status and controls small in the corner, and memories
 *  of what the bot has been doing around the form. Anything that needs a look brings the
 *  stats screen back.
 *
 *  Opening the panel only *reads* status. It used to fire an image pull from a mount effect,
 *  which locked every button — including "Open console" — for minutes. Updates start in the
 *  backend the moment it notices this app is ahead of the VM (which is what a relaunch after a
 *  self-update looks like), and the panel reports one it finds in flight. There's no update
 *  button: the VM moves when the app does. */
export function ServerControlPanel() {
  const form = useForm()
  const shell = useShell()
  const bots = useBots()
  const arrived = useArrived()
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState<'' | 'up' | 'stop'>('')
  const [err, setErr] = useState('')
  // The last update attempt that needs attention (rolled back or failed). A toast announced
  // it and was dismissed; this keeps it on the panel, with a way to report it, until an update
  // succeeds.
  const [updateNote, setUpdateNote] = useState<{ text: string; tone: Tone } | null>(null)
  // A replacement Tailscale key, for a server whose console never got an address.
  const [tsKey, setTsKey] = useState('')
  const [savingKey, setSavingKey] = useState(false)

  // Reconnect sub-flow
  const [reconnect, setReconnect] = useState(false)
  const [rcHost, setRcHost] = useState('')
  const [rcUser, setRcUser] = useState('ubuntu')
  const [rcBusy, setRcBusy] = useState(false)
  const [rcErr, setRcErr] = useState('')
  const [showKey, setShowKey] = useState(false)  // the collapsible "add this key" fallback
  // A VM running several bots: which install is this one.
  const [rcInstalls, setRcInstalls] = useState<{ dir: string; name: string }[]>([])
  const [rcDir, setRcDir] = useState('')
  // The app's SSH key is only a fallback here (a VM the app set up already trusts it), so
  // fetch it lazily when the operator expands the disclosure — never blocks the panel.
  const pk = usePubkey(reconnect && showKey)

  // A deploy or a connect that lands here takes over the wizard's half, and the panel slides
  // in as the next screen. Otherwise only a switch to Reconnect and back plays, from the side
  // it went.
  const [moved, setMoved] = useState<{ back: boolean } | null>(arrived ? { back: false } : null)
  const enter = moved ? ' enter' + (moved.back ? ' back' : '') : ''
  // Switching screens swaps the buttons, so focus would drop to the page: land on the address
  // Reconnect asks for, and on the way back on the button that opened it.
  const switched = useRef(false)
  const screen = useRef<HTMLDivElement>(null)
  const reconnectBtn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!switched.current) return
    if (reconnect) screen.current?.querySelector<HTMLInputElement>('input')?.focus()
    else reconnectBtn.current?.focus()
  }, [reconnect])
  function showReconnect(on: boolean) {
    switched.current = true
    setMoved({ back: !on })
    setReconnect(on)
  }

  // What Discord says about the server's bot, which the VM can't: its console's sign-in
  // address has to be registered with the bot's Discord app (setup couldn't show it, since it
  // only exists once the server is up), and a bot whose intents are off is refused while the
  // container still reads as healthy. Read every 5 seconds until both are fine, then once a
  // minute, so the address being taken away brings the stats screen back.
  const [dc, setDc] = useState<{
    app_id: string; redirect: string; added: boolean; intents_missing: string[]; bot_name?: string; bot_avatar?: string
  } | null>(null)
  const dcFine = !!dc && dc.added && !dc.intents_missing.length
  usePoll(() => api.serverDiscord(st?.url || '').then((r: any) => { if (r?.ok) setDc(r) }), dcFine ? 60000 : 5000, !!st?.url)
  // Once seen missing, the redirect row stays to show its tick.
  const signinMissing = useRef(false)
  if (dc?.redirect && !dc.added) signinMissing.current = true
  const [fixing, setFixing] = useState(false)
  const [fixLeft, setFixLeft] = useState(false)  // Discord wouldn't let the app turn them on
  async function fixIntents() {
    setFixing(true)
    try {
      const r = await api.serverReconnect()
      if (r?.intents_missing?.length) setFixLeft(true)
      else if (r?.ok) { toast('Intents on. Restarting the bot.', 'success'); setDc((d) => (d ? { ...d, intents_missing: [] } : d)) }
      else toast(r?.error || 'Couldn’t reconnect the bot', 'danger')
    } catch (e: any) {
      toast(e?.message || 'Couldn’t reconnect the bot', 'danger')
    } finally {
      setFixing(false)
    }
  }

  // Whether the last reading had an automatic update in flight. Read by `refresh` below as
  // well as the effect that announces one landing, so it's declared before both.
  const wasAuto = useRef(false)

  async function refresh(): Promise<Status> {
    // Degrade gracefully: a failed status read (VM down, container restarting, timeout)
    // resolves to "Unreachable" with the real error — never a stuck "Checking…".
    let next: Status
    try {
      next = await api.serverStatus()
    } catch (e: any) {
      // `auto_updating` is carried over rather than dropped. The backend puts it on its own
      // failure answers precisely so a container being recreated isn't painted as a dead
      // server; a fetch that fails here is the same situation, and letting it read as false
      // would both flash "Unreachable" and fire the finished-update toast a poll early.
      next = {
        configured: true,
        reachable: false,
        auto_updating: wasAuto.current,
        error: e?.message || 'status check failed',
      }
    }
    setSt(next)
    return next
  }

  /** What the VM's last update attempt has to say, whatever started it, or null. */
  async function lastUpdateNote() {
    try {
      return noteFor(await api.serverLastUpdate())
    } catch {
      return null  // informational only
    }
  }

  useEffect(() => {
    // Holder so the unmount cleanup always sees the latest timer. Every 15 seconds, or every 4
    // while the container is starting, so the panel moves on soon after it comes up. Chained
    // rather than an interval: a probe over SSH can take longer than 4 seconds.
    const life = { cancelled: false, poll: undefined as ReturnType<typeof setTimeout> | undefined }
    const again = (last: Status) => {
      const soon = !!last.running && last.health === 'starting'
      life.poll = setTimeout(async () => { if (!life.cancelled) again(await refresh()) }, soon ? 4000 : 15000)
    }
    ;(async () => {
      const first = await refresh()
      if (life.cancelled) return
      // Surface what the last update did while nobody was watching. Only the outcomes that
      // need attention: a *successful* one already shows as the version below, so toasting
      // it too would announce the same news on every open, days later. Skipped entirely
      // while an update is running — that one reports itself when it lands.
      if (!first.auto_updating) {
        const note = await lastUpdateNote()
        // Said on the panel rather than toasted: it happened while nobody was watching, so
        // it isn't news arriving now, and the panel line can carry a Report link.
        if (!life.cancelled && note && note.tone !== 'success') setUpdateNote(note)
      }
      if (life.cancelled) return
      again(first)
    })()
    return () => {
      life.cancelled = true
      if (life.poll) clearTimeout(life.poll)
    }
  }, [])

  // An update the app started (a launch onto a newer build than the VM) finishes while the
  // panel is open. Nothing else would say how it went, so say it here — including the
  // success, since the operator is watching this one happen.
  useEffect(() => {
    const now = !!st?.auto_updating
    const finished = wasAuto.current && !now
    wasAuto.current = now
    if (!finished) return
    let alive = true
    lastUpdateNote().then((note) => {
      if (!alive || !note) return
      toast(note.text, note.tone)
      setUpdateNote(note.tone === 'success' ? null : note)
    })
    return () => { alive = false }
  }, [st?.auto_updating])

  async function power(action: 'up' | 'stop') {
    setErr(''); setBusy(action)
    try {
      const r = await api.serverPower(action)
      if (!r?.ok) setErr(r?.error || 'That didn’t work.')
    } catch (e: any) {
      setErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      await refresh()
      setBusy('')
    }
  }

  // Recreates the container on the new key and waits for its funnel, so this takes as long
  // as a boot does. The panel keeps polling meanwhile and reads that as "Starting…".
  async function replaceKey() {
    setSavingKey(true)
    try {
      const r = await api.serverTunnelKey(tsKey.trim())
      if (r?.ok) { setTsKey(''); toast('Tailscale connected.', 'success') }
      else toast(r?.error || 'Couldn’t use that key.', 'danger')
    } catch (e: any) {
      toast(`Couldn’t use that key: ${e?.message || 'request failed'}`, 'danger')
    } finally {
      setSavingKey(false)
      await refresh()
    }
  }

  function openReconnect() {
    setRcErr(''); setShowKey(false); setRcHost(st?.host || '')
    setRcInstalls([]); setRcDir('')
    showReconnect(true)
  }
  async function doReconnect() {
    setRcErr('')
    if (!rcHost.trim()) return setRcErr('Enter the VM’s public IP address.')
    setRcBusy(true)
    try {
      // Another bot here runs on that VM: have it let this bot's key in first.
      const via = sharedServers(bots.bots, bots.activeId).find((x) => x.host === rcHost.trim())
      if (via) await api.shareServer(via.from.id).catch(() => null)
      const r = await api.serverConnect({ host: rcHost.trim(), user: rcUser.trim() || 'ubuntu', app_dir: rcDir || undefined })
      if (r?.ok) { showReconnect(false); await refresh() }
      else if (r?.choose?.length) { setRcInstalls(r.choose); setRcDir(r.choose[0].dir) }
      else setRcErr(r?.error || 'Couldn’t connect to that VM.')
    } catch (e: any) {
      setRcErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setRcBusy(false)
    }
  }

  const loading = st === null
  const running = !!st?.running
  const reachable = st?.reachable !== false
  // Docker's healthcheck is authoritative: a crashlooping container under
  // `restart: unless-stopped` is "running", and reporting that as healthy was a lie.
  const unhealthy = running && st?.health === 'unhealthy'
  const starting = running && st?.health === 'starting'
  // The backend's launch-time update outranks every other reading: mid-update the container
  // is *meant* to be recreated, so "Stopped" or "Unreachable" would be alarming and wrong.
  const updating = !!st?.auto_updating
  const phase: Phase = updating ? 'updating'
    : busy === 'stop' ? 'stopping'
    : busy === 'up' ? 'starting'
    : loading ? 'checking'
    : !reachable ? 'unreachable'
    : !running ? 'stopped'
    : unhealthy ? 'unhealthy'
    : starting ? 'starting'
    : 'running'
  const { label: stateLabel, chip } = PHASES[phase]
  // The chip pops each time the reading changes ("Checking…" to "Running"), but not on first
  // paint, where nothing has changed yet.
  const chipSeen = useRef(stateLabel)
  const chipMoved = useRef(false)
  if (stateLabel !== chipSeen.current) { chipSeen.current = stateLabel; chipMoved.current = true }
  const actionsLocked = !!busy || updating || savingKey
  // Running and healthy, but with no address to open. Held while a new key is applied, or
  // the field would vanish under the operator the moment the container reads as starting.
  const consoleDown = savingKey || (!loading && !updating && reachable && running && !starting
    && !unhealthy && !st?.url && !!st?.console_error)
  const startedAt = st?.started_at ? Date.parse(st.started_at) : NaN
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 15000); return () => clearInterval(t) }, [])
  const uptime = phase === 'running' || phase === 'unhealthy'
    ? (Number.isFinite(startedAt) ? upFor(Date.now() - startedAt) : null)
    : phase === 'stopped' ? 'Not running' : phase === 'unreachable' ? 'Unknown' : '…'

  // Each reading's form, and back to calm when the panel goes.
  useEffect(() => {
    if (!form) return
    const o = PHASES[phase].orb
    form.set(o.shape, o.v)
    form.energy(o.energy)
    form.mood(o.mood)
    if (o.regather) form.regather()
    if (o.pulse) form.pulse(o.pulse)
  }, [phase, form])
  useEffect(() => () => { form?.mood({}); form?.energy(0) }, [form])

  // ── The final screen ──
  // Running, healthy, the console's redirect listed, and nothing else to look at. It waits a
  // moment before taking over when the redirect was on screen, so its Added is seen; anything
  // else takes it back at once.
  const wantBrain = !reconnect && phase === 'running' && !!st?.url && dcFine && !consoleDown
  const [view, setView] = useState<'stats' | 'brain'>('stats')
  useEffect(() => {
    if (!wantBrain) { setView('stats'); return }
    const t = setTimeout(() => setView('brain'), signinMissing.current ? 1200 : 0)
    return () => clearTimeout(t)
  }, [wantBrain])

  const [activity] = useState(() => createActivity(() => api.serverActivity()))
  const [brain, setBrain] = useState<Brain | null>(null)
  useEffect(() => {
    if (!shell.root) return
    const b = createBrain({ root: shell.root, activity, open: false })
    setBrain(b)
    return () => { b.destroy(); setBrain(null) }
  }, [shell.root, activity])
  useEffect(() => { brain?.attach(form) }, [brain, form])
  useEffect(() => {
    // The docs go with the rail they open from.
    if (view === 'brain') shell.closeDocs()
    brain?.setTarget(view === 'brain' ? 1 : 0)
  }, [brain, view])  // eslint-disable-line react-hooks/exhaustive-deps
  // Back on the stats screen, the form shows the server's state again, whatever an opened
  // memory left it at.
  useEffect(() => { if (view === 'stats') form?.mood(PHASES[phase].orb.mood) }, [view])  // eslint-disable-line react-hooks/exhaustive-deps
  // The heartbeat: the health memory ripples each time a check passes.
  useEffect(() => {
    if (phase !== 'running') return
    activity.beat((st?.health_at && Date.parse(st.health_at)) || Date.now())
  }, [st, phase, activity])

  // Whether a new build of the app is waiting, for the corner's settings button (the stats
  // screen is where the rest of the update story lives).
  const [appUpdate, setAppUpdate] = useState(false)
  useEffect(() => {
    if (view !== 'brain') return
    api.getUpdates().then((u: any) => setAppUpdate(!!u?.available)).catch(() => {})
  }, [view])

  const bot = { name: dc?.bot_name || bots.current?.name || 'Olisar', avatar: dc?.bot_avatar || '' }
  const openConsole = () => { if (st?.url) window.open(st.url, '_blank', 'noopener') }
  const powerButton = running && phase !== 'stopping'
    ? <button className="caution" data-morph="power" disabled={actionsLocked} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
    : <button data-morph="power" disabled={actionsLocked || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>

  const stats = reconnect ? (
    <div key="reconnect" className={'wiz-screen' + enter}>
      <h1>Reconnect to your server</h1>
      <p className="step-sub">
        Enter the VM's IP and Olisar re-verifies it over SSH. Nothing is reinstalled.
      </p>
      <div className="callout tip" style={{ marginBottom: 16 }}>
        <span className="ic"><Icon.info size={17} weight="Bold" /></span>
        <div className="callout-body">Its persona, memory, knowledge, and settings are kept.</div>
      </div>
      <Field label="VM public IP address" desc="The VM running Olisar.">
        <Text value={rcHost} onChange={(v) => { setRcHost(v); setRcInstalls([]); setRcDir('') }} placeholder="e.g. 203.0.113.9" mono />
      </Field>
      {rcInstalls.length > 0 && (
        <div className="wiz-appear">
          <Field label="Which bot is this?" desc="This server runs more than one.">
            <Select value={rcDir} onChange={setRcDir} options={rcInstalls.map((i) => ({ value: i.dir, label: i.name }))} />
          </Field>
        </div>
      )}
      <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
        <summary><DisclosureChev /><span className="disclosure-title">Can’t connect? Add this app’s SSH key to the VM</span></summary>
        <div className="disclosure-body">
          <p className="desc">
            Paste this into the VM’s <code>~/.ssh/authorized_keys</code>, then Reconnect. A VM this app already set up trusts it automatically.
          </p>
          <PubkeyBox state={pk} />
          <Field label="SSH user" desc="The VM's login user. Ubuntu images use ubuntu.">
            <Text value={rcUser} onChange={setRcUser} placeholder="ubuntu" mono />
          </Field>
        </div>
      </details>
      {rcErr && (
        <div className="err-block wiz-appear">
          <div className="err">{rcErr}</div>
          <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Reconnecting to my Olisar server failed.', rcErr) }}>
            Report a problem
          </FeedbackButton>
        </div>
      )}
      <div className="onb-actions">
        <button className="primary" disabled={rcBusy} onClick={doReconnect}>{rcBusy ? 'Reconnecting…' : 'Reconnect'}</button>
        <button className="ghost" disabled={rcBusy} onClick={() => showReconnect(false)}>Cancel</button>
      </div>
    </div>
  ) : (
    // The status rows: the facts an operator checks, ruled like a settings section. What the
    // final screen keeps (the title, the badge, the two buttons) is marked data-morph, and what
    // it lets go of is data-fade.
    <div key="panel" className={'wiz-screen srv' + enter}>
      <div className="srv-head">
        <h1 data-morph="title" tabIndex={-1}>Your Olisar server</h1>
        <span data-morph="badge" className="srv-state" role="status">
          <span key={stateLabel} className={chipMoved.current ? 'wiz-pop' : ''}><Badge {...chip}>{stateLabel}</Badge></span>
        </span>
      </div>
      <p className="step-sub" data-fade>Olisar runs on your cloud VM, always on.</p>
      <dl className="srv-rows" data-fade>
        <div>
          <dt>Console</dt>
          <dd>
            {st?.url
              ? <><a className="mono" href={st.url} target="_blank" rel="noreferrer">{st.url.replace(/^https:\/\//, '')}</a><CopyText text={st.url} /></>
              : <span className="srv-none">{loading ? '…' : consoleDown ? 'Can’t be reached' : 'No address yet'}</span>}
          </dd>
        </div>
        <div><dt>Server</dt><dd className="mono">{st?.host || '…'}</dd></div>
        <div><dt>Version</dt><dd className="mono">{st?.version ? displayVersion(st.version) : '…'}</dd></div>
        {uptime !== null && <div><dt>Uptime</dt><dd>{uptime}</dd></div>}
      </dl>

      {updating && (
        <p className="srv-hint wiz-appear" data-fade>
          Updating the VM to match this app. If the new version doesn’t come up, the previous
          one is restored automatically. This can take a few minutes…
        </p>
      )}
      {!loading && !updating && unhealthy && (
        <p className="srv-hint danger wiz-appear" data-fade>Olisar is running but failing its healthcheck. Check the logs under Settings.</p>
      )}
      {!loading && !updating && !reachable && (
        <p className="srv-hint danger wiz-appear" data-fade>Couldn’t reach your server{st?.error ? `: ${st.error}.` : '. Check that the VM is running.'} Still retrying, or use <b>Reconnect</b>.</p>
      )}
      {!updating && updateNote && <UpdateNote note={updateNote} version={st?.version} />}
      {err && <p className="srv-hint danger wiz-appear" data-fade>{err}</p>}

      {dc && dc.intents_missing.length > 0 && (
        <div className="callout warning wiz-appear" data-fade>
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">
            {fixLeft
              ? <>Turn on <b>{intentList(dc.intents_missing)}</b> on <a href={`https://discord.com/developers/applications/${dc.app_id}/bot`} target="_blank" rel="noreferrer">the Bot page</a>, under Privileged Gateway Intents, then try again.</>
              : <>Discord refuses the bot because <b>{intentList(dc.intents_missing)}</b> {dc.intents_missing.length > 1 ? 'are' : 'is'} off.</>}
            <div className="callout-actions">
              <button disabled={fixing} onClick={fixIntents}>{fixing ? 'Working…' : fixLeft ? 'Try again' : 'Turn on and restart'}</button>
            </div>
          </div>
        </div>
      )}
      {consoleDown && (
        <div className="wiz-appear" data-fade>
          <div className="callout warning">
            <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
            <div className="callout-body">Your console can’t be reached. <Linkified text={st?.console_error || ''} /></div>
          </div>
          <Field
            label="Tailscale auth key"
            desc={<>A new Tailscale key is needed per bot. Create one: <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer">Tailscale → Settings → Keys</a>.</>}
          >
            <div className="key-swap">
              <Text value={tsKey} onChange={setTsKey} placeholder="tskey-auth-…" mono />
              <button disabled={!tsKey.trim() || actionsLocked} onClick={replaceKey}>{savingKey ? 'Restarting…' : 'Use key'}</button>
            </div>
          </Field>
        </div>
      )}
      {dc?.redirect && signinMissing.current && <SigninRedirect dc={dc} brain={brain} />}

      <GapClose brain={brain}>
        <button className="primary" data-morph="console" disabled={!st?.url || updating} onClick={openConsole}>Open console ↗</button>
        {powerButton}
        <button ref={reconnectBtn} className="ghost" data-fade disabled={actionsLocked} onClick={openReconnect}>Reconnect</button>
      </GapClose>
    </div>
  )

  return (
    <>
      <Pane resetKey={String(reconnect)}>
        <BotMenu variant="chip" onManage={() => shell.openSettings('bots')} />
        <div ref={screen}>{stats}</div>
      </Pane>
      {shell.over && createPortal(
        <>
          {/* A bot with no picture of its own shows just its name. */}
          {bot.avatar && <div className="orb-face" aria-hidden="true"><img src={bot.avatar} alt="" /></div>}
          <div className="orb-name" aria-hidden="true">{bot.name}</div>
          <BrainHud
            label={stateLabel} chip={chip} uptime={phase === 'running' ? uptime : null}
            power={powerButton} consoleOff={!st?.url || updating} onConsole={openConsole}
            update={appUpdate} onSettings={(s) => shell.openSettings(s)}
            note={(err || (!updating && updateNote)) ? <>
              {/* A Stop pressed here that fails lands back on this screen, so it says why here. */}
              {err && <p className="srv-hint danger wiz-appear">{err}</p>}
              {!updating && updateNote && <UpdateNote note={updateNote} version={st?.version} />}
            </> : null}
          />
          {brain && <Memories brain={brain} bot={bot} />}
          <div className="orb-css" aria-hidden="true" />
        </>,
        shell.over,
      )}
    </>
  )
}

function UpdateNote({ note, version }: { note: { text: string; tone: Tone }; version?: string }) {
  return (
    <p className={'srv-hint wiz-appear ' + note.tone} data-fade>
      {note.text}{' '}
      <FeedbackButton className="linklike" prefill={{
        category: 'Bug report',
        logs: true,
        message: reportBody(
          'Updating my Olisar server failed.',
          `${note.text}${version ? `\nServer version now: v${displayVersion(version)}` : ''}`,
        ),
      }}>Report it</FeedbackButton>
    </p>
  )
}

// The console's sign-in address, only until Discord lists it: it turns to Added, holds long
// enough to be seen, and goes. If the final screen is taking over by then, it goes with the
// rest of the stats screen and is dropped once that's out of sight, so nothing under it jumps
// mid-change.
function SigninRedirect({ dc, brain }: { dc: { app_id: string; redirect: string; added: boolean }; brain: Brain | null }) {
  const [ask, setAsk] = useState(!dc.added)
  const [leaving, setLeaving] = useState(false)
  useEffect(() => {
    if (!dc.added) { setAsk(true); setLeaving(false); return }
    if (!ask) return
    let off = () => {}, b: ReturnType<typeof setTimeout> | undefined
    const gone = () => { setAsk(false); setLeaving(false) }
    const a = setTimeout(() => {
      if (brain?.target === 1) { off = brain.whenSettled((p) => { if (p === 1) gone() }); return }
      setLeaving(true)
      b = setTimeout(gone, 250)
    }, 1350)
    return () => { clearTimeout(a); clearTimeout(b); off() }
  }, [dc.added])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!ask) return null
  return (
    <div className={'srv-redirect wiz-appear' + (leaving ? ' leaving' : '')} data-fade>
      <Field
        plain
        label="Redirect URL"
        desc={<>Signing in to your server’s console needs it. On <a href={`https://discord.com/developers/applications/${dc.app_id}/oauth2`} target="_blank" rel="noreferrer">the OAuth2 page</a>, under <strong>Redirects</strong>, add it and press <strong>Save Changes</strong>.</>}
      >
        <RedirectRow url={dc.redirect} added={dc.added} />
      </Field>
      {!dc.added && <div className="check-line wiz-appear" role="status"><span className="spinner" /> Waiting for Discord to list it…</div>}
    </div>
  )
}

// The buttons close the gap when something above them goes (the redirect, a hint), on a
// transform rather than a height tween. Not while the final screen is taking over: the
// buttons are travelling to the corner then.
function GapClose({ brain, children }: { brain: Brain | null; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const last = useRef<number | null>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const top = el.offsetTop, was = last.current
    last.current = top
    if (was == null || Math.abs(was - top) < 1 || brain?.target === 1) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const ease = getComputedStyle(document.documentElement).getPropertyValue('--ease-out').trim() || 'ease-out'
    el.animate([{ transform: `translateY(${was - top}px)` }, { transform: 'none' }], { duration: 300, easing: ease })
  })
  return <div className="onb-actions" ref={ref}>{children}</div>
}

// ── The final screen's corner ─────────────────────────────────────────────────
// The stats screen's title, status and controls, small. The pieces marked data-morph are the
// ones the stats screen's own travel into; data-extra only fades. The gear carries a dot while
// a new build of the app is waiting, and opens Settings at Updates then.
function BrainHud(props: {
  label: string; chip: BadgeGlyph & { tone: BadgeTone }; uptime: string | null
  power: ReactNode; consoleOff: boolean; onConsole: () => void
  update: boolean; onSettings: (section: 'general' | 'updates') => void; note: ReactNode
}) {
  return (
    <div className="brain-hud">
      <div className="hud-head">
        <img className="brand-logo" data-morph="logo" src="/logo.png" alt="" />
        <h1 data-morph="title" tabIndex={-1}>Your Olisar server</h1>
        <span data-morph="badge" className="srv-state" role="status"><Badge {...props.chip}>{props.label}</Badge></span>
        {props.uptime && <span className="hud-up" data-extra>Up {props.uptime.toLowerCase()}</span>}
      </div>
      <div className="hud-actions">
        <button className="primary" data-morph="console" disabled={props.consoleOff} onClick={props.onConsole}>Open console ↗</button>
        {props.power}
        <button
          className="ghost icon-btn"
          data-extra
          data-tip={props.update ? 'Settings, update available' : 'Settings'}
          aria-label={props.update ? 'Settings, an update is available' : 'Settings'}
          onClick={() => props.onSettings(props.update ? 'updates' : 'general')}
        >
          <Icon.settings size={16} />
          {props.update && <span className="hud-dot" />}
        </button>
      </div>
      {props.note && <div className="hud-note" data-extra>{props.note}</div>}
    </div>
  )
}

// ── Memories ──────────────────────────────────────────────────────────────────
// What the bot has been doing, one small sphere each. Replies show who they answered and how
// the bot was called; the rest, what it learned or noticed. The sphere is drawn by the form
// where brain.ts puts it; this is only the words inside.
const agoShort = (at: number, now: number) => {
  const s = Math.max(0, Math.floor((now - at) / 1000))
  if (s < 45) return 'now'
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`
  if (s < 172800) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}
const agoLong = (at: number, now: number) => {
  const s = Math.max(0, Math.floor((now - at) / 1000))
  if (s < 45) return 'just now'
  if (s < 3600) { const m = Math.max(1, Math.round(s / 60)); return `${m} minute${m === 1 ? '' : 's'} ago` }
  if (s < 172800) { const h = Math.floor(s / 3600); return `${h} hour${h === 1 ? '' : 's'} ago` }
  return `${Math.floor(s / 86400)} days ago`
}
const secsAgo = (at: number, now: number) => Math.max(0, Math.round((now - at) / 1000))

// How the bot was called, as the bot's own docs name it.
const TRIGGERS: Record<string, string> = {
  ask: '/ask', name: 'Name trigger', mention: 'Mention', reply: 'Reply', proactive: 'Chimed in', catchup: '/catchup',
}
const HOW: Record<string, string> = { site: 'a website', page: 'a page', doc: 'a document' }
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s)
const hostOf = (url: string) => { try { return new URL(url).host + new URL(url).pathname.replace(/\/$/, '') } catch { return url } }

type Kind<K extends ActivityItem['kind']> = {
  tag: (m: Extract<ActivityItem, { kind: K }>) => string
  more?: (m: Extract<ActivityItem, { kind: K }>) => string
  say: (m: Extract<ActivityItem, { kind: K }>, ago: string) => string
}
const KINDS: { [K in ActivityItem['kind']]: Kind<K> } = {
  reply: { tag: (m) => TRIGGERS[m.trigger] || 'Replied', more: (m) => m.where, say: (m, a) => `Reply to ${m.who.name}${TRIGGERS[m.trigger] ? ` (${TRIGGERS[m.trigger]})` : ''}${m.where ? ` in ${m.where}` : ''}, ${a}: ${m.text}` },
  member: { tag: () => 'Joined', say: (m, a) => `New member ${m.who.name}, ${a}` },
  people: { tag: () => 'Synced', say: (m, a) => `Member sync, ${a}: ${m.count} members` },
  impression: { tag: () => 'Impression', more: (m) => (m.messages ? `From ${m.messages} messages` : ''), say: (m, a) => `Impression of ${m.who.name}, ${a}: ${m.text}` },
  remembered: { tag: () => 'Memory', more: (m) => m.where, say: (m, a) => `Memory about ${m.who.name}, ${a}: ${m.text}` },
  glossary: { tag: () => 'Glossary', more: (m) => m.where, say: (m, a) => `Glossary, ${a}: ${m.text}` },
  status: { tag: () => 'Status', say: (m, a) => `Status set to “${m.text}”, ${a}` },
  learned: { tag: () => 'Knowledge', say: (m, a) => `Knowledge source ${m.title || m.url}, ${m.count} passages, ${a}` },
  reminder: { tag: () => 'Reminder', more: (m) => m.where, say: (m, a) => `Reminder for ${m.who.name}, ${a}: ${m.text}` },
  image: { tag: () => 'Image', more: (m) => m.where, say: (m, a) => `Image for ${m.who.name}, ${a}: “${m.text}”` },
  health: { tag: () => 'Health check', more: () => 'Every 30 seconds', say: (m) => `Health check passed ${secsAgo(m.at, Date.now())} seconds ago` },
}
function kindOf<K extends ActivityItem['kind']>(m: Extract<ActivityItem, { kind: K }>): Kind<K> {
  return KINDS[m.kind as K] as Kind<K>
}
// The first sentence of an impression, for the ring; the whole of it opens.
const firstSentence = (t: string) => { const m = /^(.+?[.!?])(\s|$)/.exec(t); return m ? m[1] : t }

function Av({ who, size = 20 }: { who: Person; size?: number }) {
  return <img className="mem-av" src={avatarOf(who)} alt="" width={size} height={size} />
}

// An address that doesn't fit starts at the left and trails off to the right, well inside the
// sphere, rather than running out to the dust.
function MemLink({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (el) el.classList.toggle('long', el.scrollWidth > el.clientWidth + 1)
  }, [text])
  return <div className="mem-link" ref={ref}>{text}</div>
}

function MemoryBody({ m }: { m: ActivityItem }) {
  const text = (t: string) => <div className="mem-text">{t}</div>
  const who = (p: Person, size = 20) => <div className="mem-who"><Av who={p} size={size} /><span>{p.name}</span></div>
  switch (m.kind) {
    case 'member': return who(m.who, 36)
    case 'people': return <><div className="mem-faces">{m.faces.slice(0, 3).map((f, i) => <Av key={i} who={f} size={24} />)}</div>{text(`${m.count} members`)}</>
    case 'glossary': return <><span className="mem-ic"><Icon.knowledge size={15} /></span>{text(m.text)}</>
    case 'learned': return <><span className="mem-ic"><Icon.docs size={15} /></span><MemLink text={m.title || hostOf(m.url)} />{text(`${m.count} passages`)}</>
    case 'status': return <div className="mem-text"><i className="mem-dot" />{m.text}</div>
    case 'health': return <Badge tone="success" icon="check-circle">Healthy</Badge>
    case 'image': return <>{who(m.who)}{text(`“${m.text}”`)}</>
    case 'impression': return <>{who(m.who)}{text(firstSentence(m.text))}</>
    default: return <>{who(m.who)}{text(m.text)}</>
  }
}

// One memory on the ring: a button (it opens the memory), its words inside the sphere.
function Memory({ m, now, brain }: { m: ActivityItem; now: number; brain: Brain }) {
  const k = kindOf(m)
  const openIt = () => brain.open(m.id)
  // A time the feed doesn't know (a roster that hasn't synced since the app learned to ask)
  // leaves the age off.
  const foot = m.kind === 'health' ? `Checked ${secsAgo(m.at, now)}s ago` : m.at ? `${k.tag(m)} · ${agoShort(m.at, now)}` : k.tag(m)
  const more = k.more?.(m)
  return (
    <li className={'mem mem-' + m.kind} data-mem={m.id}>
      <div
        className="mem-btn"
        role="button"
        tabIndex={0}
        aria-label={k.say(m, m.at ? agoLong(m.at, now) : 'earlier')}
        onPointerEnter={() => brain.peek(m.id, true)}
        onPointerLeave={() => brain.peek(m.id, false)}
        onBlur={() => brain.peek(m.id, false)}
        onFocus={(e) => { if (e.currentTarget.matches(':focus-visible')) brain.peek(m.id, true) }}
        onClick={openIt}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openIt() } }}
      >
        <div className="mem-mask" aria-hidden="true">
          <div className="mem-in">
            <MemoryBody m={m} />
            <div className="mem-foot">{foot}</div>
            {more && <div className="mem-more">{more}</div>}
          </div>
        </div>
      </div>
    </li>
  )
}

// An opened memory, in the middle: what it was about, in full. A reply shows the message it
// answered and the whole reply; the rest show their whole text and where it came from.
function Msg({ who, text, bot }: { who: Person; text: string; bot?: boolean }) {
  return (
    <div className={'mo-msg' + (bot ? ' bot' : '')}>
      <Av who={who} size={24} />
      <div><b>{who.name}</b><p>{text}</p></div>
    </div>
  )
}
const HEALTH: [keyof NonNullable<Extract<ActivityItem, { kind: 'health' }>['checks']>, string][] = [
  ['vec', 'Vector search'], ['sandbox', 'Sandbox'], ['model', 'Model'],
]
function OpenBody({ m, now, bot }: { m: ActivityItem; now: number; bot: Person }) {
  const Who = ({ p }: { p: Person }) => <div className="mo-who"><Av who={p} size={22} /><span>{p.name}</span></div>
  switch (m.kind) {
    case 'reply': return <div className="mo-chat">{m.ask && <Msg who={m.who} text={m.ask} />}<Msg who={bot} text={m.text} bot /></div>
    case 'impression': return <><Who p={m.who} /><p className="mo-text">{m.text}</p>{m.messages > 0 && <div className="mo-meta">From {m.messages} messages</div>}</>
    case 'remembered': return <><Who p={m.who} /><p className="mo-text mo-strong">{m.text}</p>{m.said && <div className="mo-chat"><Msg who={m.who} text={m.said} /></div>}{m.type && <div className="mo-meta">{cap(m.type)}</div>}</>
    case 'glossary': return <><p className="mo-text mo-strong">{m.text}</p>{m.subject && <div className="mo-meta">{m.subject}</div>}</>
    case 'status': return <><p className="mo-text mo-status"><i className="mem-dot" />{m.text}</p>{m.how && <div className="mo-meta">{m.how}</div>}</>
    case 'learned': return <>
      <span className="mem-ic"><Icon.docs size={18} /></span>
      {m.url ? <p className="mo-url">{m.url}</p> : <p className="mo-text mo-strong">{m.title}</p>}
      <div className="mo-meta">{m.count} passages{HOW[m.how] ? ` from ${HOW[m.how]}` : ''}{m.who ? `, added by ${m.who.name}` : ''}</div>
    </>
    case 'reminder': return <><div className="mo-chat"><Msg who={bot} text={m.text} bot /></div><div className="mo-meta">For {m.who.name}</div></>
    case 'image': return <><Who p={m.who} /><p className="mo-text">“{m.text}”</p></>
    case 'member': return <><Av who={m.who} size={56} /><p className="mo-text mo-strong">{m.who.name}</p>{m.roles.length > 0 && <div className="mo-roles">{m.roles.map((r) => <RoleChip key={r} name={r} />)}</div>}</>
    case 'people': return <><p className="mo-big">{m.count} members</p><div className="mo-faces">{m.faces.map((f, i) => <Av key={i} who={f} size={26} />)}</div></>
    case 'health': return <>
      <Badge tone="success" icon="check-circle">Healthy</Badge>
      {/* Each thing the check covers, as the backend's own self-check reports it. Only one
          that failed is marked; one that couldn't tell ("inconclusive") isn't. */}
      <div className="mo-checks">
        <Badge tone="success" icon="check-circle">Web server</Badge>
        {HEALTH.filter(([key]) => m.checks?.[key] != null).map(([key, name]) => {
          const v = m.checks![key]
          return v === false || v === 'failed'
            ? <Badge key={key} tone="danger" icon="close-circle">{name}</Badge>
            : <Badge key={key} tone="success" icon="check-circle">{name}</Badge>
        })}
      </div>
      <div className="mo-meta">Checked {secsAgo(m.at, now)}s ago, every 30 seconds</div>
    </>
  }
}

function MemoryOpen({ m, isOpen, arrived, now, brain, bot }: {
  m: ActivityItem; isOpen: boolean; arrived: boolean; now: number; brain: Brain; bot: Person
}) {
  const el = useRef<HTMLElement>(null)
  const back = useRef<HTMLButtonElement>(null)
  useInert(el, !isOpen)
  // Once it's in the middle, so Back's tooltip isn't left where the sphere was on its way.
  useEffect(() => { if (isOpen && arrived) back.current?.focus({ preventScroll: true }) }, [m.id, isOpen, arrived])
  const k = kindOf(m)
  const when = m.at ? agoLong(m.at, now) : ''
  const more = 'where' in m ? m.where : ''
  const head = m.kind === 'health' ? 'Health check' : [k.tag(m), more, when].filter(Boolean).join(' · ')
  return (
    <section ref={el} className={'mem-open mem-open-' + m.kind} aria-label={k.say(m, when || 'earlier')}>
      <button className="ghost icon-btn mem-back" ref={back} aria-label="Back" data-tip="Back" onClick={() => brain.close()}>
        <Icon.arrowLeft size={18} />
      </button>
      <div className="mo-in">
        <div className="mo-head">{head}</div>
        <OpenBody m={m} now={now} bot={bot} />
      </div>
    </section>
  )
}

function Memories({ brain, bot }: { brain: Brain; bot: Person }) {
  const [, bump] = useState(0)
  useEffect(() => brain.subscribe(() => bump((n) => n + 1)), [brain])
  // Ages, and the heartbeat's seconds.
  useEffect(() => { const t = setInterval(() => bump((n) => n + 1), 1000); return () => clearInterval(t) }, [])
  const now = Date.now()
  const shown = brain.shown()
  return (
    <>
      <ol className="mems" aria-label="Recent activity">
        {brain.list().map((m) => <Memory key={m.id} m={m} now={now} brain={brain} />)}
      </ol>
      {shown?.item && <MemoryOpen key={shown.item.id} m={shown.item} isOpen={shown.open} arrived={shown.arrived} now={now} brain={brain} bot={bot} />}
    </>
  )
}
