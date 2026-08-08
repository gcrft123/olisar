import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './icons'
import { PubkeyBox, usePubkey } from './setup'
import { SettingsModal } from './settings'
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

/** One line describing an update attempt — ours, or one the VM's timer ran unattended. */
function noteFor(r: UpdateResult | null | undefined): string {
  if (!r || !r.at) return ''
  const tag = r.tag || 'the latest release'
  if (r.rolled_back) return `${tag} failed its healthcheck and was rolled back. The server is on the previous version.`
  if (r.updated) return `Server updated to ${tag}.`
  if (r.ok === false) return `Last update attempt failed: ${r.message || r.error || 'unknown error'}.`
  return ''
}

/** Shown (loopback-gated, no Discord login) when the app is in server-hosting mode:
 *  the bot runs on the operator's cloud VM, and this is the local control panel that
 *  starts/stops it over SSH (`docker compose up -d` / `stop`) and links to its console.
 *  A reconnect flow re-adopts the VM after a reinstall / reset / IP change.
 *
 *  Opening the panel only *reads* status. It used to fire an image pull from a mount
 *  effect, which locked every button — including "Open console" — for minutes, and meant
 *  a server whose operator rarely opened the app never updated at all. The VM now runs its
 *  own daily update timer; "Update now" here is the same script, on demand. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [updateNote, setUpdateNote] = useState('')
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

  async function refresh() {
    // Degrade gracefully: a failed status read (VM down, container restarting, timeout)
    // resolves to "Unreachable" with the real error — never a stuck "Checking…".
    try {
      setSt(await api.serverStatus())
    } catch (e: any) {
      setSt({
        configured: true,
        reachable: false,
        error: e?.message || 'status check failed',
      })
    }
  }

  useEffect(() => {
    // Holder so the unmount cleanup always sees the latest interval id.
    const life = { cancelled: false, poll: undefined as ReturnType<typeof setInterval> | undefined }
    ;(async () => {
      await refresh()
      if (life.cancelled) return
      // Surface what the VM's update timer did while the app was closed — otherwise an
      // unattended update (or a rollback) would leave no trace the operator ever sees.
      try {
        const last = await api.serverLastUpdate()
        if (!life.cancelled) setUpdateNote(noteFor(last))
      } catch { /* informational only */ }
      if (life.cancelled) return
      life.poll = setInterval(() => { if (!life.cancelled) refresh() }, 15000)
    })()
    return () => {
      life.cancelled = true
      if (life.poll) clearInterval(life.poll)
    }
  }, [])

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
    setErr(''); setBusy(true); setUpdateNote('')
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
    setErr(''); setUpdateNote(''); setUpdating(true)
    try {
      const r: UpdateResult = await api.serverUpdate()
      setUpdateNote(noteFor(r) || (r?.ok ? 'Already on the latest release.' : 'The update didn’t complete.'))
      if (r?.ok) setAvailable('')
    } catch (e: any) {
      setUpdateNote(`Couldn’t update the server: ${e?.message || 'request failed'}`)
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
  const stateLabel = updating
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
  const stateTone = updating || loading || starting
    ? 'info'
    : !reachable || unhealthy
      ? 'error'
      : running
        ? 'success'
        : 'warning'
  const actionsLocked = busy || updating

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
          {rcErr && <div className="err">{rcErr}</div>}
          <div className="wiz-foot">
            <button disabled={rcBusy} onClick={() => setReconnect(false)}>Cancel</button>
            <span className="grow" />
            <button className="primary" disabled={rcBusy} onClick={doReconnect}>{rcBusy ? 'Reconnecting…' : 'Reconnect'}</button>
          </div>
        </div>
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
              {updating ? 'Updating…' : `Update to v${available}`}
            </button>
          )}
          {running
            ? <button className="caution" disabled={actionsLocked} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={actionsLocked || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url || updating} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {st?.version && (
          <p className="srv-hint">
            Server version <b>v{st.version}</b>
            {available ? <>, and <b>v{available}</b> is available.</> : <>, up to date.</>}
          </p>
        )}
        {updating && (
          <p className="srv-hint">
            Updating the VM. If the new version doesn’t come up, the previous one is restored
            automatically. This can take a few minutes…
          </p>
        )}
        {!updating && updateNote && (
          <p className="srv-hint">{updateNote}</p>
        )}
        {!loading && !updating && unhealthy && (
          <p className="srv-hint">Olisar is running but failing its healthcheck. Check the logs under Settings.</p>
        )}
        {!loading && !updating && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? `: ${st.error}.` : '. Check that the VM is running.'} Still retrying, or use <b>Reconnect</b>.</p>
        )}
        {err && <div className="err">{err}</div>}
      </div>
      {settingsOpen && (
        <SettingsModal
          sections={['appearance', 'bot', 'logs', 'updates', 'desktop', 'feedback']}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  )
}
