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
  url?: string
  host?: string
  error?: string
}

/** Shown (loopback-gated, no Discord login) when the app is in server-hosting mode:
 *  the bot runs on the operator's cloud VM, and this is the local control panel that
 *  starts/stops it over SSH (`docker compose up -d` / `stop`) and links to its console.
 *  On open it pulls the latest GHCR image so the VM stays current without a manual
 *  `compose pull`. A reconnect flow re-adopts the VM after a reinstall / reset / IP change. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Startup image pull (once per panel mount). Non-fatal if the VM is down — status still runs.
  const [updating, setUpdating] = useState(true)
  const [updateNote, setUpdateNote] = useState('')

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
      setUpdating(true)
      setUpdateNote('')
      try {
        const r = await api.serverUpdate()
        if (life.cancelled) return
        if (r?.ok) {
          if (r.updated) setUpdateNote('Server image updated to the latest release.')
        } else if (r?.error) {
          // Soft fail: still show status so a down VM doesn't block the panel forever.
          setUpdateNote(`Couldn’t update the server image — ${r.error}`)
        }
      } catch (e: any) {
        if (!life.cancelled) setUpdateNote(`Couldn’t update the server image — ${e?.message || 'request failed'}`)
      } finally {
        if (life.cancelled) return
        setUpdating(false)
        await refresh()
        if (life.cancelled) return
        // Status polls only after the pull finishes so we don't contend for SSH.
        life.poll = setInterval(() => { if (!life.cancelled) refresh() }, 15000)
      }
    })()
    return () => {
      life.cancelled = true
      if (life.poll) clearInterval(life.poll)
    }
  }, [])

  async function power(action: 'up' | 'stop') {
    setErr(''); setBusy(true); setUpdateNote('')
    try {
      const r = await api.serverPower(action)
      if (!r?.ok) setErr(r?.error || 'That didn’t work.')
      else if (action === 'up') setUpdateNote('Started with the latest server image.')
    } catch (e: any) {
      setErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setBusy(false)
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

  const loading = st === null && !updating
  const running = !!st?.running
  const reachable = st?.reachable !== false
  const stateLabel = updating
    ? 'Updating…'
    : loading
      ? 'Checking…'
      : !reachable
        ? 'Unreachable'
        : running
          ? 'Running'
          : 'Stopped'
  const stateTone = updating || loading
    ? 'info'
    : !reachable
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
            Re-adopt the VM after a reset, a reinstall, or a changed IP — enter its IP and Olisar re-verifies over SSH. No reinstall.
          </p>
          <div className="callout tip" style={{ marginBottom: 16 }}>
            <span className="ic"><Icon.info size={17} weight="Bold" /></span>
            <div className="callout-body">Reconfiguring an existing bot — its persona, memory, knowledge, and settings are kept.</div>
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
            <Field label="SSH user" desc="The VM's login user — Ubuntu images use ubuntu.">
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
          Olisar runs on your cloud VM{st?.host ? <> at <code>{st.host}</code></> : ''}, always on. Start or stop it here;
          the full dashboard is served from the VM. The app pulls the latest server image when it opens
          {updating ? '…' : '.'}
        </p>

        <div className="wiz-foot">
          <button className="ghost" disabled={actionsLocked} onClick={openReconnect}>Reconnect</button>
          <span className="grow" />
          {running
            ? <button className="caution" disabled={actionsLocked} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={actionsLocked || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url || updating} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {updating && (
          <p className="srv-hint">Checking for a newer server image and applying it if needed. This can take a few minutes…</p>
        )}
        {!updating && updateNote && (
          <p className="srv-hint">{updateNote}</p>
        )}
        {!loading && !updating && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? ` — ${st.error}` : ' — check the VM is running.'} Retrying, or use <b>Reconnect</b>.</p>
        )}
        {err && <div className="err">{err}</div>}
      </div>
      {settingsOpen && (
        <SettingsModal
          sections={['appearance', 'bot', 'logs', 'updates', 'desktop', 'feedback']}
          botSwitcherOnly
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  )
}
