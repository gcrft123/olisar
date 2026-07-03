import { useEffect, useState } from 'react'
import { api } from './api'

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
 *  starts/stops it over SSH (`docker compose up -d` / `stop`) and links to its console. */
export function ServerControlPanel() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

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

  const loading = st === null
  const running = !!st?.running
  const reachable = st?.reachable !== false
  const stateLabel = loading ? 'Checking…' : !reachable ? 'Unreachable' : running ? 'Running' : 'Stopped'
  const stateTone = loading ? 'info' : !reachable ? 'error' : running ? 'success' : 'warning'

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
          <span className="grow" />
          {running
            ? <button className="caution" disabled={busy} onClick={() => power('stop')}>{busy ? 'Working…' : 'Stop server'}</button>
            : <button disabled={busy || loading || !reachable} onClick={() => power('up')}>{busy ? 'Working…' : 'Start server'}</button>}
          <button className="primary" disabled={!st?.url} onClick={() => st?.url && window.open(st.url, '_blank', 'noopener')}>Open console ↗</button>
        </div>

        {!loading && !reachable && (
          <p className="srv-hint">Couldn’t reach your server{st?.error ? ` — ${st.error}` : ' — check the VM is running.'} Retrying…</p>
        )}
        {err && <div className="err">{err}</div>}
        {st?.logs && <pre className="srv-logs">{st.logs}</pre>}
      </div>
    </div>
  )
}
