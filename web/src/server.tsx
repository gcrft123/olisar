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
  logs?: string
  host?: string
  error?: string
}

/** Shown (loopback-gated, no Discord login) when the app is in server-hosting mode:
 *  the bot runs on the operator's cloud VM, and this is the local control panel that
 *  starts/stops it over SSH (`docker compose up -d` / `stop`) and links to its console.
 *  A reconnect flow re-adopts the VM after a reinstall / reset / IP change. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)

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
    // Degrade gracefully: a failed status read (VM down, container restarting, an older
    // backend) resolves to "Unreachable" — never a stuck "Checking…" or a raw parse error.
    try { setSt(await api.serverStatus()) } catch { setSt({ configured: true, reachable: false }) }
  }
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 15000)
    return () => clearInterval(t)
  }, [])

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
  const stateLabel = loading ? 'Checking…' : !reachable ? 'Unreachable' : running ? 'Running' : 'Stopped'
  const stateTone = loading ? 'info' : !reachable ? 'error' : running ? 'success' : 'warning'

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
          the full dashboard is served from the VM.
        </p>

        <div className="wiz-foot">
          <button className="ghost" onClick={openReconnect}>Reconnect</button>
          <span className="grow" />
          {running
            ? <button className="caution" disabled={busy} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={busy || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {!loading && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? ` — ${st.error}` : ' — check the VM is running.'} Retrying, or use <b>Reconnect</b>.</p>
        )}
        {err && <div className="err">{err}</div>}
        {st?.logs && <pre className="srv-logs">{st.logs}</pre>}
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
