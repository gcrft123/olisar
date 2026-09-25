import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { BotMenu, intentList, sharedServers, useBots } from './bots'
import { Icon } from './icons'
import { toast, type Tone } from './overlays'
import { PubkeyBox, RedirectRow, usePubkey } from './setup'
import { FeedbackButton, SettingsModal, useFeedbackHost, type SectionId } from './settings'
import { reportBody, type FeedbackPrefill } from './feedback'
import { Badge, Field, Select, Text, usePoll, type BadgeGlyph, type BadgeTone } from './ui'
import { displayVersion } from './version'

type Status = {
  configured?: boolean
  reachable?: boolean
  running?: boolean
  /** Docker's own healthcheck verdict: healthy | unhealthy | starting | '' (none). */
  health?: string
  /** From the image's OCI labels, so it resolves even while the container is stopped. */
  version?: string
  digest?: string
  url?: string
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

/** Shown (loopback-gated, no Discord login) when the app is in server-hosting mode:
 *  the bot runs on the operator's cloud VM, and this is the local control panel that
 *  starts/stops it over SSH (`docker compose up -d` / `stop`) and links to its console.
 *  A reconnect flow re-adopts the VM after a reinstall / reset / IP change.
 *
 *  Opening the panel only *reads* status. It used to fire an image pull from a mount
 *  effect, which locked every button — including "Open console" — for minutes. Updates
 *  start in the backend the moment it notices this app is ahead of the VM (which is what a
 *  relaunch after a self-update looks like), and the panel reports one it finds in flight.
 *  There's no update button: the VM moves when the app does. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPane, setSettingsPane] = useState<SectionId | undefined>(undefined)
  const bots = useBots()
  const [fbPrefill, setFbPrefill] = useState<FeedbackPrefill | undefined>(undefined)
  useFeedbackHost((p) => { setFbPrefill(p); setSettingsOpen(true) })
  // The last update attempt that needs attention (rolled back or failed). A toast announced
  // it and was dismissed; this keeps it on the panel, with a way to report it, until an
  // update succeeds.
  const [updateNote, setUpdateNote] = useState<{ text: string; tone: Tone } | null>(null)

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

  // What Discord says about the server's bot, which the VM can't: its console's sign-in
  // address has to be registered with the bot's Discord app (setup couldn't show it, since
  // it only exists once the server is up), and a bot whose intents are off is refused while
  // the container still reads as healthy. Read until both are fine, then left alone.
  const [dc, setDc] = useState<{ app_id: string; redirect: string; added: boolean; intents_missing: string[] } | null>(null)
  const dcFine = !!dc && dc.added && !dc.intents_missing.length
  usePoll(() => api.serverDiscord(st?.url || '').then((r: any) => { if (r?.ok) setDc(r) }), 5000, !!st?.url && !dcFine)
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
    // Holder so the unmount cleanup always sees the latest interval id.
    const life = { cancelled: false, poll: undefined as ReturnType<typeof setInterval> | undefined }
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
      life.poll = setInterval(() => { if (!life.cancelled) refresh() }, 15000)
    })()
    return () => {
      life.cancelled = true
      if (life.poll) clearInterval(life.poll)
    }
  }, [])

  // An update the app started (a launch onto a newer build than the VM) finishes while the
  // panel is open. Nothing else would say how it went, so say it here — including
  // the success, since the operator is watching this one happen.
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
    setErr(''); setBusy(true)
    try {
      const r = await api.serverPower(action)
      if (!r?.ok) setErr(r?.error || 'That didn’t work.')
    } catch (e: any) {
      setErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setBusy(false)
      await refresh()
    }
  }

  function openReconnect() {
    setReconnect(true); setRcErr(''); setShowKey(false); setRcHost(st?.host || '')
    setRcInstalls([]); setRcDir('')
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
      if (r?.ok) { setReconnect(false); await refresh() }
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
  const state: BadgeGlyph & { label: string; tone: BadgeTone } = updating
    ? { label: 'Updating…', tone: 'info', busy: true }
    : loading
      ? { label: 'Checking…', tone: 'info', busy: true }
      : !reachable
        ? { label: 'Unreachable', tone: 'danger', icon: 'close-circle' }
        : !running
          ? { label: 'Stopped', tone: 'warning', icon: 'stop-circle' }
          : unhealthy
            ? { label: 'Unhealthy', tone: 'danger', icon: 'danger-circle' }
            : starting
              ? { label: 'Starting…', tone: 'info', busy: true }
              : { label: 'Running', tone: 'success', icon: 'play-circle' }
  const { label: stateLabel, ...stateChip } = state
  const actionsLocked = busy || updating
  const modal = settingsOpen && (
    <SettingsModal
      sections={['general', 'bots', 'logs', 'updates', 'desktop', 'feedback']}
      initialSection={settingsPane}
      prefill={fbPrefill}
      onClose={() => { setSettingsOpen(false); setFbPrefill(undefined) }}
    />
  )

  if (reconnect) {
    return (
      <div className="setup">
        <div className="box">
          <BotMenu variant="chip" onManage={() => { setSettingsPane('bots'); setSettingsOpen(true) }} />
          <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => { setSettingsPane(undefined); setSettingsOpen(true) }}>
            <Icon.settings size={16} />
          </button>
          <img className="brand-logo" src="/logo.png" alt="Olisar" />
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
            <Field label="Which bot is this?" desc="This server runs more than one.">
              <Select value={rcDir} onChange={setRcDir} options={rcInstalls.map((i) => ({ value: i.dir, label: i.name }))} />
            </Field>
          )}
          <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
            <summary>Can’t connect? Add this app’s SSH key to the VM</summary>
            <div className="desc" style={{ marginTop: 8 }}>
              Paste this into the VM’s <code>~/.ssh/authorized_keys</code>, then Reconnect. A VM this app already set up trusts it automatically.
            </div>
            <PubkeyBox state={pk} />
            <Field label="SSH user" desc="The VM's login user. Ubuntu images use ubuntu.">
              <Text value={rcUser} onChange={setRcUser} placeholder="ubuntu" mono />
            </Field>
          </details>
          {rcErr && (
            <div className="err-block">
              <div className="err">{rcErr}</div>
              <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Reconnecting to my Olisar server failed.', rcErr) }}>
                Report a problem
              </FeedbackButton>
            </div>
          )}
          <div className="wiz-foot">
            <button disabled={rcBusy} onClick={() => setReconnect(false)}>Cancel</button>
            <span className="grow" />
            <button className="primary" disabled={rcBusy} onClick={doReconnect}>{rcBusy ? 'Reconnecting…' : 'Reconnect'}</button>
          </div>
        </div>
        {modal}
      </div>
    )
  }

  return (
    <div className="setup">
      <div className="box">
        <BotMenu variant="chip" onManage={() => { setSettingsPane('bots'); setSettingsOpen(true) }} />
        <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => { setSettingsPane(undefined); setSettingsOpen(true) }}>
          <Icon.settings size={16} />
        </button>
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        <div className="srv-head">
          <h1>Your Olisar server</h1>
          <Badge {...stateChip}>{stateLabel}</Badge>
        </div>
        <p className="step-sub">
          Olisar runs on your cloud VM{st?.host ? <> at <code>{st.host}</code></> : ''}, always on. Start or stop it here.
        </p>

        {dc && dc.intents_missing.length > 0 && (
          <div className="callout warning">
            <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
            <div className="callout-body">
              {fixLeft
                ? <>Turn on <b>{intentList(dc.intents_missing)}</b> on <a href={`https://discord.com/developers/applications/${dc.app_id}/bot`} target="_blank" rel="noreferrer">the Bot page</a>, under Privileged Gateway Intents, then try again.</>
                : <>Discord refuses the bot because <b>{intentList(dc.intents_missing)}</b> {dc.intents_missing.length > 1 ? 'are' : 'is'} off.</>}
              <div style={{ marginTop: 10 }}>
                <button disabled={fixing} onClick={fixIntents}>{fixing ? 'Working…' : fixLeft ? 'Try again' : 'Turn on and restart'}</button>
              </div>
            </div>
          </div>
        )}
        {dc?.redirect && signinMissing.current && (
          <Field
            plain
            label="Redirect URL"
            desc={<>Signing in to your server’s console needs it. On <a href={`https://discord.com/developers/applications/${dc.app_id}/oauth2`} target="_blank" rel="noreferrer">the OAuth2 page</a>, under <strong>Redirects</strong>, add it and press <strong>Save Changes</strong>.</>}
          >
            <RedirectRow url={dc.redirect} added={dc.added} />
          </Field>
        )}

        <div className="wiz-foot">
          <button className="ghost" disabled={actionsLocked} onClick={openReconnect}>Reconnect</button>
          <span className="grow" />
          {running
            ? <button className="caution" disabled={actionsLocked} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={actionsLocked || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url || updating} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {updating && (
          <p className="srv-hint">
            Updating the VM to match this app. If the new version doesn’t come up, the previous
            one is restored automatically. This can take a few minutes…
          </p>
        )}
        {!loading && !updating && unhealthy && (
          <p className="srv-hint">Olisar is running but failing its healthcheck. Check the logs under Settings.</p>
        )}
        {!loading && !updating && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? `: ${st.error}.` : '. Check that the VM is running.'} Still retrying, or use <b>Reconnect</b>.</p>
        )}
        {!updating && updateNote && (
          <p className={'srv-hint ' + updateNote.tone}>
            {updateNote.text}{' '}
            <FeedbackButton className="linklike" prefill={{
              category: 'Bug report',
              logs: true,
              message: reportBody(
                'Updating my Olisar server failed.',
                `${updateNote.text}${st?.version ? `\nServer version now: v${displayVersion(st.version)}` : ''}`,
              ),
            }}>Report it</FeedbackButton>
          </p>
        )}
        {err && <div className="err">{err}</div>}
      </div>
      {modal}
    </div>
  )
}
