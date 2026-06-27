// Developer console (platform owner) — manage every marketplace extension, triage abuse
// reports, moderate publishers (warn/ban), tune the publish-risk policy, and read the bot
// + funnel logs. Visible only when the registry says this bot's owner is a whitelisted
// developer (App gates the nav item on api.devStatus()). All data is proxied to the
// registry behind the bot's publisher token.

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './icons'

type DevTab = 'extensions' | 'reports' | 'blocked' | 'moderation' | 'logs' | 'funnel' | 'policy'

const TABS: { id: DevTab; label: string }[] = [
  { id: 'extensions', label: 'Extensions' },
  { id: 'reports', label: 'Reports' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'moderation', label: 'Moderation' },
  { id: 'logs', label: 'Bot logs' },
  { id: 'funnel', label: 'Funnel logs' },
  { id: 'policy', label: 'Policy' },
]

function riskCls(score: number): string {
  if (score >= 70) return 'danger'
  if (score >= 31) return 'warn'
  return 'ok'
}
function fmtDate(s?: string): string {
  if (!s) return '—'
  const d = new Date(s.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z'))
  return isNaN(+d) ? s : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}
function Loading() { return <div className="empty" style={{ padding: 24 }}>Loading…</div> }

export function Developer() {
  const [tab, setTab] = useState<DevTab>('extensions')
  return (
    <div className="dev">
      <div className="page-head">
        <div className="title-row">
          <div className="title-ic"><Icon.developer size={19} weight="Linear" /></div>
          <div>
            <h1>Developer</h1>
            <div className="page-sub">Marketplace management, reports, and moderation for the Olisar platform.</div>
          </div>
        </div>
      </div>
      <div className="dev-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={'dev-tab' + (tab === t.id ? ' active' : '')} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>
      {tab === 'extensions' && <DevExtensions />}
      {tab === 'reports' && <DevReports />}
      {tab === 'blocked' && <DevBlocked />}
      {tab === 'moderation' && <DevModeration />}
      {tab === 'logs' && <DevLogs kind="bot" />}
      {tab === 'funnel' && <DevLogs kind="funnel" />}
      {tab === 'policy' && <DevPolicy />}
    </div>
  )
}

// ── Extensions spreadsheet ───────────────────────────────────────────────────
function DevExtensions() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: 'installs', dir: -1 })
  const [code, setCode] = useState<any>(null)

  const load = () => { setErr(null); api.devExtensions().then((d) => setRows(d.extensions || [])).catch((e) => setErr(e.message)) }
  useEffect(load, [])

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = (rows || []).filter((x) =>
      !ql || x.id.toLowerCase().includes(ql) || (x.publisher || '').toLowerCase().includes(ql) || String(x.publisher_discord_id || '').includes(ql))
    return [...r].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key]
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sort.dir
      return String(av ?? '').localeCompare(String(bv ?? '')) * sort.dir
    })
  }, [rows, q, sort])

  const th = (key: string, label: string) => (
    <th className={'sortable' + (sort.key === key ? ' on' : '')}
      onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
    </th>
  )

  const viewCode = async (r: any) => {
    try { const d = await api.devSource(r.namespace, r.name, r.version); setCode({ id: r.id, ...d }) }
    catch (e: any) { alert('Couldn’t load source: ' + e.message) }
  }
  const yank = async (r: any) => {
    if (!confirm(`Yank ${r.id} from the marketplace? It stops appearing for everyone.`)) return
    try { await api.devYank(r.namespace, r.name); load() } catch (e: any) { alert('Yank failed: ' + e.message) }
  }
  const moderate = async (r: any, status: 'warn' | 'ban') => {
    if (!r.publisher_discord_id) { alert('No publisher Discord ID on record for this extension.'); return }
    const msg = status === 'ban'
      ? `Ban ${r.publisher} (Discord ${r.publisher_discord_id})?\n\nTheir extensions are de-listed and they’re blocked from Olisar (console + bot).`
      : `Warn ${r.publisher} (Discord ${r.publisher_discord_id})? They’ll see a notice next time they open the console.`
    if (!confirm(msg)) return
    try { await api.devModeration(String(r.publisher_discord_id), status, ''); alert(status === 'ban' ? 'Publisher banned.' : 'Publisher warned.') }
    catch (e: any) { alert('Failed: ' + e.message) }
  }

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  return (
    <div className="card">
      <div className="dev-toolbar">
        <input type="text" className="dev-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, publisher, or Discord ID…" />
        <span className="settings-muted">{filtered.length} of {rows.length}</span>
        <span className="grow" />
        <button className="ghost sm" onClick={load}><Icon.refresh size={14} /> Refresh</button>
      </div>
      <div className="dev-table-wrap">
        <table className="dev-table">
          <thead>
            <tr>
              {th('id', 'Extension')}{th('publisher', 'Publisher')}{th('publisher_discord_id', 'Discord ID')}
              {th('version', 'Version')}{th('installs', 'Installs')}{th('risk_score', 'Risk')}
              {th('status', 'Status')}{th('published_at', 'Published')}
              <th>Permissions</th><th aria-label="actions"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>{r.publisher_verified ? '✓ ' : ''}{r.publisher || '—'}</td>
                <td className="mono">{r.publisher_discord_id || '—'}</td>
                <td>{r.version}</td>
                <td>{r.installs}</td>
                <td>{r.risk_score == null ? '—' : <span className={'risk-pill ' + riskCls(r.risk_score)}>{r.risk_score}</span>}</td>
                <td><span className={'badge' + (r.status !== 'published' ? ' error' : '')}>{r.status}</span></td>
                <td className="muted">{fmtDate(r.published_at)}</td>
                <td className="dev-perms">{(r.permissions || []).map((p: string) => <span key={p} className="tag">{p}</span>)}</td>
                <td className="dev-row-actions">
                  <button className="iconbtn" onClick={() => viewCode(r)} title="View code"><Icon.code size={15} /></button>
                  <button className="iconbtn" onClick={() => yank(r)} title="Yank"><Icon.trash size={15} /></button>
                  <button className="iconbtn warn" onClick={() => moderate(r, 'warn')} title="Warn publisher"><Icon.warn size={15} /></button>
                  <button className="iconbtn danger" onClick={() => moderate(r, 'ban')} title="Ban publisher"><Icon.ban size={15} /></button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={10} className="empty">No extensions.</td></tr>}
          </tbody>
        </table>
      </div>
      {code && <CodeModal code={code} onClose={() => setCode(null)} />}
    </div>
  )
}

function CodeModal(props: { code: { id: string; source?: string; version?: string }; onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="dev-code-modal" onClick={(e) => e.stopPropagation()}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close">✕</button>
        <div className="settings-head"><h2>{props.code.id}</h2><p>v{props.code.version} · source</p></div>
        <pre className="dev-code">{props.code.source || '(no source)'}</pre>
      </div>
    </div>
  )
}

// ── Reports ──────────────────────────────────────────────────────────────────
function DevReports() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => { setErr(null); api.devReports().then((d) => setRows(d.reports || [])).catch((e) => setErr(e.message)) }
  useEffect(load, [])

  const moderate = async (discordId: string, status: 'warn' | 'ban') => {
    if (!discordId) { alert('No publisher Discord ID on this report.'); return }
    if (!confirm(`${status === 'ban' ? 'Ban' : 'Warn'} Discord ${discordId}?`)) return
    try { await api.devModeration(discordId, status, ''); alert(status === 'ban' ? 'Banned.' : 'Warned.') }
    catch (e: any) { alert('Failed: ' + e.message) }
  }

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  if (rows.length === 0) return <div className="card"><div className="empty">No reports filed.</div></div>
  return (
    <div className="card">
      <div className="dev-toolbar"><span className="settings-muted">{rows.length} report{rows.length === 1 ? '' : 's'}</span><span className="grow" /><button className="ghost sm" onClick={load}><Icon.refresh size={14} /> Refresh</button></div>
      <div className="dev-reports">
        {rows.map((r) => (
          <div key={r.id} className="dev-report">
            <div className="dev-report-head">
              <span className="mono">{r.namespace}/{r.name}{r.version ? ` @ ${r.version}` : ''}</span>
              <span className="grow" />
              <span className="settings-muted">{fmtDate(r.created_at)}</span>
            </div>
            <div className="dev-report-body">{r.description || <span className="settings-muted">(no description)</span>}</div>
            <div className="dev-report-meta">
              <span>publisher <code>{r.publisher_discord_id || 'unknown'}</code></span>
              <span>reporter <code>{r.reporter_discord_id || 'unknown'}</code></span>
              {r.logs_r2_key && <span className="badge">logs + attachments emailed</span>}
            </div>
            <div className="dev-report-actions">
              <button className="ghost sm warn" disabled={!r.publisher_discord_id} onClick={() => moderate(r.publisher_discord_id, 'warn')}>Warn publisher</button>
              <button className="ghost sm danger" disabled={!r.publisher_discord_id} onClick={() => moderate(r.publisher_discord_id, 'ban')}>Ban publisher</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Blocked publishes ────────────────────────────────────────────────────────
function DevBlocked() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => { setErr(null); api.devBlocked().then((d) => setRows(d.blocked || [])).catch((e) => setErr(e.message)) }
  useEffect(load, [])

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  if (rows.length === 0) return <div className="card"><div className="empty">No publishes have been blocked.</div></div>
  return (
    <div className="card">
      <div className="dev-toolbar"><span className="settings-muted">{rows.length} blocked publish{rows.length === 1 ? '' : 'es'}</span><span className="grow" /><button className="ghost sm" onClick={load}><Icon.refresh size={14} /> Refresh</button></div>
      <div className="dev-reports">
        {rows.map((r) => (
          <div key={r.id} className="dev-report">
            <div className="dev-report-head">
              <span className="mono">{r.namespace ? r.namespace + '/' : ''}{r.name}{r.version ? ` @ ${r.version}` : ''}</span>
              <span className={'risk-pill ' + riskCls(r.risk_score ?? 0)}>{r.risk_score ?? '—'}</span>
              <span className="settings-muted">/ threshold {r.threshold ?? '—'}</span>
              <span className="grow" />
              <span className="settings-muted">{fmtDate(r.created_at)}</span>
            </div>
            {(r.bullets || []).length > 0 && (
              <ul className="dev-blocked-reasons">
                {r.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}
              </ul>
            )}
            <div className="dev-report-meta"><span>by <code>{r.reporter_discord_id || 'unknown'}</code></span></div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Moderation ───────────────────────────────────────────────────────────────
function DevModeration() {
  const [entries, setEntries] = useState<any[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [id, setId] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => { setErr(null); api.devModerationList().then((d) => setEntries(d.entries || [])).catch((e) => setErr(e.message)) }
  useEffect(load, [])

  const act = async (discordId: string, status: 'warn' | 'ban' | 'clear', message = '') => {
    if (!discordId.trim()) { alert('Enter a Discord ID.'); return }
    try { await api.devModeration(discordId.trim(), status, message); setId(''); setMsg(''); load() }
    catch (e: any) { alert('Failed: ' + e.message) }
  }

  return (
    <div className="card">
      <div className="settings-subhead">Warn or ban a Discord ID</div>
      <div className="dev-mod-form">
        <input type="text" className="dev-search" value={id} onChange={(e) => setId(e.target.value)} placeholder="Discord user ID" />
        <input type="text" className="dev-search" value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="Message (shown to the user, optional)" />
        <button className="ghost sm warn" onClick={() => act(id, 'warn', msg)}>Warn</button>
        <button className="ghost sm danger" onClick={() => act(id, 'ban', msg)}>Ban</button>
      </div>
      <div className="import-warn">A ban de-lists the publisher’s extensions and blocks them from Olisar (console + bot), enforced within ~a minute on every bot. A warning shows once in their console.</div>

      <div className="settings-subhead" style={{ marginTop: 18 }}>Current standing</div>
      {err && <div className="settings-err">{err}</div>}
      {!entries ? <Loading /> : entries.length === 0 ? <div className="empty">No warned or banned users.</div> : (
        <div className="dev-mod-list">
          {entries.map((m) => (
            <div key={m.discord_id} className="list-row">
              <span className={'badge' + (m.status === 'banned' ? ' error' : ' pending')}>{m.status}</span>
              <code className="grow">{m.discord_id}</code>
              {m.message && <span className="settings-muted">{m.message}</span>}
              <span className="settings-muted">{fmtDate(m.updated_at)}</span>
              <button className="ghost sm" onClick={() => act(m.discord_id, 'clear')}>Clear</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Logs (bot + funnel) ──────────────────────────────────────────────────────
function DevLogs({ kind }: { kind: 'bot' | 'funnel' }) {
  const [lines, setLines] = useState<string[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const preRef = useRef<HTMLPreElement>(null)
  const load = () => {
    setErr(null)
    const p = kind === 'bot' ? api.getLogs(1500).then((d) => d.lines || []) : api.getRemote().then((d) => d.logs || [])
    p.then(setLines).catch((e) => setErr(e.message))
  }
  useEffect(load, [kind])
  useEffect(() => { if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight }, [lines])

  return (
    <div className="card">
      <div className="dev-toolbar">
        <span className="settings-muted">{kind === 'bot' ? 'Backend (bot + API) logs' : 'Remote-access (Tailscale Funnel) logs'}</span>
        <span className="grow" />
        <button className="ghost sm" onClick={load}><Icon.refresh size={14} /> Refresh</button>
      </div>
      {err && <div className="settings-err">{err}</div>}
      <pre className="logview" ref={preRef} style={{ height: 520, maxHeight: 'none' }}>{(lines || []).join('\n') || (lines ? '(no log lines)' : 'Loading…')}</pre>
    </div>
  )
}

// ── Policy ───────────────────────────────────────────────────────────────────
function DevPolicy() {
  const [v, setV] = useState<number | null>(null)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.marketplacePolicy().then((d) => setV(d.risk_threshold)).catch((e) => setErr(e.message)) }, [])

  const save = async () => {
    if (v == null) return
    setErr(null); setSaved(false)
    try { const d = await api.setMarketplacePolicy(v); setV(d.risk_threshold); setSaved(true); setTimeout(() => setSaved(false), 1800) }
    catch (e: any) { setErr(e.message) }
  }
  if (v == null && !err) return <Loading />
  return (
    <div className="card">
      <div className="settings-subhead">Publish risk threshold</div>
      <div className="settings-muted" style={{ marginBottom: 12 }}>
        Publishing an extension is blocked when its AI risk score is at or above this value (0–100, higher = riskier). The same review is shown to anyone installing it.
      </div>
      {err && <div className="settings-err">{err}</div>}
      <div className="dev-policy-row">
        <input type="range" min={1} max={100} value={v ?? 70} onChange={(e) => setV(Number(e.target.value))} className="dev-range" />
        <span className={'risk-pill ' + riskCls(v ?? 70)} style={{ minWidth: 38, textAlign: 'center' }}>{v ?? 70}</span>
        <button className="primary sm" onClick={save}>{saved ? 'Saved ✓' : 'Save'}</button>
      </div>
    </div>
  )
}
