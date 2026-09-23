import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './icons'
import { toast, type Tone } from './overlays'
import { PubkeyBox, usePubkey } from './setup'
import { FeedbackButton, SettingsModal, useFeedbackHost } from './settings'
import { reportBody, type FeedbackPrefill } from './feedback'
import { Field, Text } from './ui'

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
  /** An update the app started by itself is in flight (see remote.autoupdate). */
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

/** Numeric version compare, matching the backend's (leading "v" and any suffix ignored). */
function isNewer(remote: string, local: string): boolean {
  const parts = (v: string) => (String(v || '').match(/\d+/g) || ['0']).map(Number)
  const a = parts(remote)
  const b = parts(local)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0
    const y = b[i] || 0
    if (x !== y) return x > y
  }
  return false
}

/** Version strings arrive tagged ("v1.4.2") from the server's image labels and bare
 *  ("1.4.2") from the releases API. Strip it so the one `v` we render is our own —
 *  the panel used to print the server's version as "vv1.4.2". */
const bareVersion = (v: string | undefined): string => String(v || '').replace(/^v/i, '')

/** What an update attempt should say — the one we pressed, or the one the app applied for
 *  us at launch. Tone drives how it's delivered: a success expires on its own, a rollback or
 *  failure is something the operator has to act on, so it sticks until dismissed. */
function noteFor(r: UpdateResult | null | undefined): { text: string; tone: Tone } | null {
  if (!r || !r.at) return null
  const tag = r.tag ? `v${bareVersion(r.tag)}` : 'the latest release'
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
 *  effect, which locked every button — including "Open console" — for minutes. Updates now
 *  start in the backend the moment it notices this app is ahead of the VM (which is what a
 *  relaunch after a self-update looks like); the panel reports one it finds in flight, and
 *  "Update to vX" runs the same script on demand. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [fbPrefill, setFbPrefill] = useState<FeedbackPrefill | undefined>(undefined)
  useFeedbackHost((p) => { setFbPrefill(p); setSettingsOpen(true) })
  // The last update attempt that needs attention (rolled back or failed). A toast announced
  // it and was dismissed; this keeps it on the panel, with a way to report it, until an
  // update succeeds.
  const [updateNote, setUpdateNote] = useState<{ text: string; tone: Tone } | null>(null)
  const [updating, setUpdating] = useState(false)
  const [available, setAvailable] = useState('')  // newer release tag, if any

  // Reconnect sub-flow
  const [reconnect, setReconnect] = useState(false)
  const [rcHost, setRcHost] = useState('')
  const [rcUser, setRcUser] = useState('ubuntu')
  const [rcBusy, setRcBusy] = useState(false)
  const [rcErr, setRcErr] = useState('')
  const [showKey, setShowKey] = useState(false)  // the collapsible "add this key" fallback
  // The app's SSH key is only a fallback here (a VM the app set up already trusts it), so
  // fetch it lazily when the operator expands the disclosure — never blocks the panel.
  const pk = usePubkey(reconnect && showKey)

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

  // An update the app started for itself (a launch onto a newer build than the VM) finishes
  // while the panel is open. Nothing else would say how it went, so say it here — including
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

  // Is there a newer release than what the VM is actually running? Compared against the
  // server's version (from its image labels), not this app's — they update separately.
  useEffect(() => {
    if (!st?.version) return
    let cancelled = false
    api.getUpdates()
      .then((r: any) => {
        if (cancelled || !r?.latest) return
        setAvailable(isNewer(r.latest, st.version || '') ? String(r.latest).replace(/^v/i, '') : '')
      })
      .catch(() => { /* offline: just don't offer an update */ })
    return () => { cancelled = true }
  }, [st?.version])

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

  async function runUpdate() {
    setErr(''); setUpdating(true)
    try {
      const r: UpdateResult = await api.serverUpdate()
      const note = noteFor(r)
      setUpdateNote(note && note.tone !== 'success' ? note : null)
      if (note) toast(note.text, note.tone)
      else toast(r?.ok ? 'Already on the latest release.' : 'The update didn’t complete.',
        r?.ok ? 'neutral' : 'danger')
      if (r?.ok) setAvailable('')
    } catch (e: any) {
      toast(`Couldn’t update the server: ${e?.message || 'request failed'}`, 'danger')
    } finally {
      setUpdating(false)
      await refresh()
    }
  }

  function openReconnect() {
    setReconnect(true); setRcErr(''); setShowKey(false); setRcHost(st?.host || '')
  }
  async function doReconnect() {
    setRcErr('')
    if (!rcHost.trim()) return setRcErr('Enter the VM’s public IP address.')
    setRcBusy(true)
    try {
      const r = await api.serverConnect({ host: rcHost.trim(), user: rcUser.trim() || 'ubuntu' })
      if (r?.ok) { setReconnect(false); await refresh() }
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
  // One "Updating…" state, whether we pressed the button or the backend started it at
  // launch. It outranks every other reading: mid-update the container is *meant* to be
  // recreated, so "Stopped" or "Unreachable" would be alarming and wrong.
  const busyUpdating = updating || !!st?.auto_updating
  const stateLabel = busyUpdating
    ? 'Updating…'
    : loading
      ? 'Checking…'
      : !reachable
        ? 'Unreachable'
        : !running
          ? 'Stopped'
          : unhealthy
            ? 'Unhealthy'
            : starting
              ? 'Starting…'
              : 'Running'
  const stateTone = busyUpdating || loading || starting
    ? 'info'
    : !reachable || unhealthy
      ? 'error'
      : running
        ? 'success'
        : 'warning'
  const actionsLocked = busy || busyUpdating
  const modal = settingsOpen && (
    <SettingsModal
      sections={['general', 'logs', 'updates', 'desktop', 'feedback']}
      prefill={fbPrefill}
      onClose={() => { setSettingsOpen(false); setFbPrefill(undefined) }}
    />
  )

  if (reconnect) {
    return (
      <div className="setup">
        <div className="box">
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
            <Text value={rcHost} onChange={setRcHost} placeholder="e.g. 203.0.113.9" mono />
          </Field>
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
        <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
          <Icon.settings size={16} />
        </button>
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        <div className="srv-head">
          <h1>Your Olisar server</h1>
          <span className={'badge ' + stateTone}>{stateLabel}</span>
        </div>
        <p className="step-sub">
          Olisar runs on your cloud VM{st?.host ? <> at <code>{st.host}</code></> : ''}, always on. Start or stop it here.
        </p>

        <div className="wiz-foot">
          <button className="ghost" disabled={actionsLocked} onClick={openReconnect}>Reconnect</button>
          <span className="grow" />
          {available && (
            <button disabled={actionsLocked || !reachable} onClick={runUpdate}>
              {busyUpdating ? 'Updating…' : `Update to v${bareVersion(available)}`}
            </button>
          )}
          {running
            ? <button className="caution" disabled={actionsLocked} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={actionsLocked || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url || busyUpdating} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {st?.version && (
          <p className="srv-hint">
            Server version <b>v{bareVersion(st.version)}</b>
            {available ? <>, and <b>v{bareVersion(available)}</b> is available.</> : <>, up to date.</>}
          </p>
        )}
        {busyUpdating && (
          <p className="srv-hint">
            Updating the VM to match this app. If the new version doesn’t come up, the previous
            one is restored automatically. This can take a few minutes…
          </p>
        )}
        {!loading && !busyUpdating && unhealthy && (
          <p className="srv-hint">Olisar is running but failing its healthcheck. Check the logs under Settings.</p>
        )}
        {!loading && !busyUpdating && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? `: ${st.error}.` : '. Check that the VM is running.'} Still retrying, or use <b>Reconnect</b>.</p>
        )}
        {!busyUpdating && updateNote && (
          <p className={'srv-hint ' + updateNote.tone}>
            {updateNote.text}{' '}
            <FeedbackButton className="linklike" prefill={{
              category: 'Bug report',
              logs: true,
              message: reportBody(
                'Updating my Olisar server failed.',
                `${updateNote.text}${st?.version ? `\nServer version now: v${bareVersion(st.version)}` : ''}`,
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
