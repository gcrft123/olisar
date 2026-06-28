// Developer console (platform owner) — manage every marketplace extension, triage abuse
// reports, moderate publishers (warn/ban), tune the publish-risk policy, and read the bot
// + funnel logs. Visible only when the registry says this bot's owner is a whitelisted
// developer (App gates the nav item on api.devStatus()). All data is proxied to the
// registry behind the bot's publisher token.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { Icon, CloseX } from './icons'
import { toast, confirmDialog } from './overlays'

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

const COUNTED: Record<string, true> = { reports: true, blocked: true, moderation: true }

export function Developer() {
  const [tab, setTab] = useState<DevTab>('extensions')
  const [counts, setCounts] = useState<Record<string, number>>({})
  const barRef = useRef<HTMLDivElement>(null)
  const [ind, setInd] = useState<{ left: number; width: number }>({ left: 0, width: 0 })
  useEffect(() => {
    api.devReports().then((d) => setCounts((c) => ({ ...c, reports: (d.reports || []).length }))).catch(() => {})
    api.devBlocked().then((d) => setCounts((c) => ({ ...c, blocked: (d.blocked || []).length }))).catch(() => {})
    api.devModerationList().then((d) => setCounts((c) => ({ ...c, moderation: (d.entries || []).length }))).catch(() => {})
  }, [])
  useLayoutEffect(() => {
    const el = barRef.current?.querySelector('.dev-tab.active') as HTMLElement | null
    if (el) setInd({ left: el.offsetLeft, width: el.offsetWidth })
  }, [tab, counts])
  return (
    <div className="dev">
      <div className="page-head">
        <div className="title-row">
          <div className="title-ic"><Icon.developer size={19} weight="Linear" /></div>
          <h1>Developer</h1>
        </div>
        <p>Marketplace management, reports, and moderation for the Olisar platform.</p>
      </div>
      <div className="dev-tabs" ref={barRef}>
        {TABS.map((t) => (
          <button key={t.id} className={'dev-tab' + (tab === t.id ? ' active' : '')} onClick={() => setTab(t.id)}>
            {t.label}
            {COUNTED[t.id] && counts[t.id] != null && <span className="dev-tab-count">{counts[t.id]}</span>}
          </button>
        ))}
        <span className="dev-tab-ind" style={{ left: ind.left, width: ind.width }} />
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

  const th = (key: string, label: string, numeric = false) => (
    <th className={(numeric ? 'num ' : '') + 'sortable' + (sort.key === key ? ' on' : '')}
      onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
      <span className="th-label">
        {label}
        {sort.key === key && (
          <Icon.chevron size={11} className="th-sort" style={{ transform: sort.dir === 1 ? 'rotate(180deg)' : undefined }} />
        )}
      </span>
    </th>
  )

  const viewCode = async (r: any) => {
    try { const d = await api.devSource(r.namespace, r.name, r.version); setCode({ id: r.id, ...d }) }
    catch (e: any) { toast('Couldn’t load source: ' + e.message, 'danger') }
  }
  const yank = async (r: any) => {
    if (!(await confirmDialog({ title: `Yank ${r.id}?`, message: 'It stops appearing in the marketplace for everyone.', confirmLabel: 'Yank', tone: 'danger' }))) return
    try { await api.devYank(r.namespace, r.name); load() } catch (e: any) { toast('Yank failed: ' + e.message, 'danger') }
  }
  const moderate = async (r: any, status: 'warn' | 'ban') => {
    if (!r.publisher_discord_id) { toast('No publisher Discord ID on record for this extension.', 'warning'); return }
    const message = status === 'ban'
      ? `Their extensions are de-listed and they’re blocked from Olisar (console + bot).`
      : `They’ll see a notice next time they open the console.`
    if (!(await confirmDialog({
      title: `${status === 'ban' ? 'Ban' : 'Warn'} ${r.publisher} (Discord ${r.publisher_discord_id})?`,
      message, confirmLabel: status === 'ban' ? 'Ban' : 'Warn', tone: 'danger',
    }))) return
    try { await api.devModeration(String(r.publisher_discord_id), status, ''); toast(status === 'ban' ? 'Publisher banned.' : 'Publisher warned.', 'success') }
    catch (e: any) { toast('Failed: ' + e.message, 'danger') }
  }

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  return (
    <div className="card">
      <div className="dev-toolbar">
        <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', flex: '0 1 320px' }}>
          <Icon.search size={15} style={{ position: 'absolute', left: 11, color: 'var(--text-3)', pointerEvents: 'none' }} />
          <input type="text" className="dev-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, publisher, or Discord ID…" style={{ paddingLeft: 32, width: '100%' }} />
        </span>
        <span className="settings-muted">{filtered.length} of {rows.length}</span>
        <span className="grow" />
        <button className="ghost icon-btn sm" onClick={load} title="Refresh" aria-label="Refresh"><Icon.refresh size={15} /></button>
      </div>
      <div className="dev-table-wrap">
        <table className="dev-table">
          <thead>
            <tr>
              {th('id', 'Extension')}{th('publisher', 'Publisher')}{th('publisher_discord_id', 'Discord ID')}
              {th('version', 'Version')}{th('installs', 'Installs', true)}{th('risk_score', 'Risk', true)}
              {th('status', 'Status')}{th('published_at', 'Published')}
              <th>Permissions</th><th aria-label="actions"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>{r.publisher_verified && <Icon.check size={13} weight="Bold" style={{ color: 'var(--ok)', verticalAlign: '-2px', marginRight: 4 }} />}{r.publisher || '—'}</td>
                <td className="mono">{r.publisher_discord_id || '—'}</td>
                <td>{r.version}</td>
                <td className="num">{r.installs}</td>
                <td className="num">{r.risk_score == null ? '—' : <span className={'risk-pill ' + riskCls(r.risk_score)}>{r.risk_score}</span>}</td>
                <td><span className={'badge ' + (r.status === 'published' ? 'success' : r.status === 'yanked' ? 'error' : 'pending')}>{r.status}</span></td>
                <td className="muted">{fmtDate(r.published_at)}</td>
                <td className="dev-perms">{(r.permissions || []).map((p: string) => <span key={p} className="tag">{p}</span>)}</td>
                <td className="dev-row-actions">
                  <button className="ghost icon-btn sm" onClick={() => viewCode(r)} title="View code"><Icon.code size={15} /></button>
                  <button className="danger icon-btn sm" onClick={() => yank(r)} title="Yank" aria-label="Yank"><Icon.trash size={15} /></button>
                  <button className="caution icon-btn sm" onClick={() => moderate(r, 'warn')} title="Warn publisher" aria-label="Warn publisher"><Icon.warn size={15} /></button>
                  <button className="danger icon-btn sm" onClick={() => moderate(r, 'ban')} title="Ban publisher" aria-label="Ban publisher"><Icon.ban size={15} /></button>
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
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
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

  const clearAll = async () => {
    if (!(await confirmDialog({ title: 'Clear all reports?', message: 'Removes every standing report from the list for all developers. This can’t be undone.', confirmLabel: 'Clear all', tone: 'danger' }))) return
    try { await api.devClearReports(); toast('Reports cleared.', 'success'); load() }
    catch (e: any) { toast('Couldn’t clear reports: ' + e.message, 'danger') }
  }

  const moderate = async (discordId: string, status: 'warn' | 'ban') => {
    if (!discordId) { toast('No publisher Discord ID on this report.', 'warning'); return }
    if (!(await confirmDialog({ title: `${status === 'ban' ? 'Ban' : 'Warn'} Discord ${discordId}?`, confirmLabel: status === 'ban' ? 'Ban' : 'Warn', tone: 'danger' }))) return
    try { await api.devModeration(discordId, status, ''); toast(status === 'ban' ? 'Banned.' : 'Warned.', 'success') }
    catch (e: any) { toast('Failed: ' + e.message, 'danger') }
  }

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  if (rows.length === 0) return <div className="card"><div className="empty">No reports filed.</div></div>
  return (
    <div className="card">
      <div className="dev-toolbar"><span className="settings-muted">{rows.length} report{rows.length === 1 ? '' : 's'}</span><span className="grow" /><button className="ghost icon-btn sm" onClick={clearAll} data-tip="Clear all reports" aria-label="Clear all reports"><Icon.trash size={15} /></button><button className="ghost icon-btn sm" onClick={load} title="Refresh" aria-label="Refresh"><Icon.refresh size={15} /></button></div>
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
              <button className="caution" disabled={!r.publisher_discord_id} onClick={() => moderate(r.publisher_discord_id, 'warn')}>Warn publisher</button>
              <button className="danger" disabled={!r.publisher_discord_id} onClick={() => moderate(r.publisher_discord_id, 'ban')}>Ban publisher</button>
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

  const clearAll = async () => {
    if (!(await confirmDialog({ title: 'Clear all blocked publishes?', message: 'Removes every recorded blocked-publish from the list for all developers. This can’t be undone.', confirmLabel: 'Clear all', tone: 'danger' }))) return
    try { await api.devClearBlocked(); toast('Blocked publishes cleared.', 'success'); load() }
    catch (e: any) { toast('Couldn’t clear: ' + e.message, 'danger') }
  }

  if (err) return <div className="card"><div className="settings-err">{err}</div></div>
  if (!rows) return <Loading />
  if (rows.length === 0) return <div className="card"><div className="empty">No publishes have been blocked.</div></div>
  return (
    <div className="card">
      <div className="dev-toolbar"><span className="settings-muted">{rows.length} blocked publish{rows.length === 1 ? '' : 'es'}</span><span className="grow" /><button className="ghost icon-btn sm" onClick={clearAll} data-tip="Clear all blocked" aria-label="Clear all blocked"><Icon.trash size={15} /></button><button className="ghost icon-btn sm" onClick={load} title="Refresh" aria-label="Refresh"><Icon.refresh size={15} /></button></div>
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
              <div className="dev-blocked-reasons" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {r.bullets.map((b: string, i: number) => (
                  <div key={i} className="callout warning">
                    <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                    <div className="callout-body">{b}</div>
                  </div>
                ))}
              </div>
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
    if (!discordId.trim()) { toast('Enter a Discord ID.', 'warning'); return }
    try { await api.devModeration(discordId.trim(), status, message); setId(''); setMsg(''); load() }
    catch (e: any) { toast('Failed: ' + e.message, 'danger') }
  }

  return (
    <div className="card">
      <div className="settings-subhead">Warn or ban a Discord ID</div>
      <div className="dev-mod-form">
        <input type="text" className="dev-search" value={id} onChange={(e) => setId(e.target.value)} placeholder="Discord user ID" />
        <input type="text" className="dev-search" value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="Message (shown to the user, optional)" />
        <button className="caution" onClick={() => act(id, 'warn', msg)}>Warn</button>
        <button className="danger" onClick={() => act(id, 'ban', msg)}>Ban</button>
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
              <button className="ghost" onClick={() => act(m.discord_id, 'clear')}>Clear</button>
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
        <button className="ghost icon-btn sm" onClick={load} title="Refresh" aria-label="Refresh"><Icon.refresh size={15} /></button>
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
        <input type="range" min={1} max={100} value={v ?? 70} onChange={(e) => setV(Number(e.target.value))} className="dev-range" style={{ '--fill': `${v ?? 70}%` } as any} />
        <span className={'risk-pill ' + riskCls(v ?? 70)} style={{ minWidth: 38, textAlign: 'center' }}>{v ?? 70}</span>
        <button className="primary" onClick={save}>{saved ? <><Icon.check size={14} weight="Bold" /> Saved</> : 'Save'}</button>
      </div>
    </div>
  )
}
