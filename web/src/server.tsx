import { useEffect, useState } from 'react'
import { api } from './api'
import { Cb } from './setup'
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
 *  A reconnect flow re-adopts the VM after a reinstall / key rotation / IP change. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Reconnect sub-flow
  const [reconnect, setReconnect] = useState(false)
  const [rcPubkey, setRcPubkey] = useState('')
  const [rcHost, setRcHost] = useState('')
  const [rcUser, setRcUser] = useState('ubuntu')
  const [rcBusy, setRcBusy] = useState(false)
  const [rcErr, setRcErr] = useState('')

  async function refresh() {
    // Degrade gracefully: a failed status read (VM down, container restarting, an older
    // backend returning HTML) resolves to "Unreachable" — never a stuck "Checking…" or a
    // raw JSON parse error.
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

  async function openReconnect() {
    setReconnect(true); setRcErr(''); setRcHost(st?.host || '')
    try { const r = await api.serverPubkey(); setRcPubkey(r.public_key || '') } catch { /* shown as generating… */ }
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
            Re-adopt the VM after a reinstall, an SSH-key rotation, or a changed IP. No reinstall — it just re-verifies.
          </p>
          <div className="field">
            <label>SSH public key</label>
            <div className="desc">If the VM doesn't already trust this app's key, add it to <code>~/.ssh/authorized_keys</code>.</div>
            <Cb file="app SSH public key" code={rcPubkey || 'generating…'} />
          </div>
          <Field label="VM public IP address" desc="The VM running Olisar.">
            <Text value={rcHost} onChange={setRcHost} placeholder="e.g. 203.0.113.9" mono />
          </Field>
          <Field label="SSH user (optional)" desc="The VM's login user — Ubuntu images use ubuntu.">
            <Text value={rcUser} onChange={setRcUser} placeholder="ubuntu" mono />
          </Field>
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
    </div>
  )
}
