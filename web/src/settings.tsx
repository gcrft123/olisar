import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon, CloseX, type IconName } from './icons'
import { Toggle } from './ui'
import { toast } from './overlays'
import { ACCENTS, DEFAULT_ACCENT, getAccent, setAccent } from './theme'

// A Notion-style settings popup: a centered overlay with a left section nav and a
// right content pane. App-wide operator settings (not per-server) live here.
type SectionId = 'appearance' | 'logs' | 'remote' | 'updates' | 'desktop'
const SECTIONS: { id: SectionId; label: string; ic: IconName }[] = [
  { id: 'appearance', label: 'Appearance', ic: 'palette' },
  { id: 'logs', label: 'Bot logs', ic: 'docs' },
  { id: 'remote', label: 'Remote access', ic: 'remote' },
  { id: 'updates', label: 'Updates', ic: 'update' },
  { id: 'desktop', label: 'Desktop app', ic: 'settings' },
]

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState<SectionId>('appearance')
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <nav className="settings-nav">
          <div className="settings-nav-title">Settings</div>
          {SECTIONS.map((s) => {
            const Glyph = Icon[s.ic]
            return (
              <button
                key={s.id}
                className={'settings-nav-item' + (section === s.id ? ' active' : '')}
                onClick={() => setSection(s.id)}
              >
                <Glyph size={16} weight={section === s.id ? 'Bold' : 'Linear'} /> {s.label}
              </button>
            )
          })}
        </nav>
        <div className="settings-body">
          <button className="settings-close" onClick={onClose} title="Close (Esc)">
            <CloseX size={18} />
          </button>
          {section === 'appearance' && <Appearance />}
          {section === 'logs' && <Logs />}
          {section === 'remote' && <Remote />}
          {section === 'updates' && <Updates />}
          {section === 'desktop' && <Desktop />}
        </div>
      </div>
    </div>
  )
}

function Head({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="settings-head">
      <h2>{title}</h2>
      <p>{sub}</p>
    </div>
  )
}

// ── Appearance ──────────────────────────────────────────────────────────────
function Appearance() {
  const [accent, setAccentState] = useState(getAccent())
  const pick = (c: string) => { setAccent(c); setAccentState(c) }
  return (
    <>
      <Head title="Appearance" sub="The accent color used across the console. Saved on this device." />
      <div className="swatches">
        {ACCENTS.map((a) => (
          <button
            key={a.value}
            className={'swatch' + (accent.toLowerCase() === a.value.toLowerCase() ? ' active' : '')}
            style={{ background: a.value }}
            title={a.name}
            onClick={() => pick(a.value)}
          >
            {accent.toLowerCase() === a.value.toLowerCase() && <Icon.check size={15} weight="Bold" />}
          </button>
        ))}
      </div>
      <div className="settings-row">
        <label className="custom-color">
          <input type="color" value={accent} onChange={(e) => pick(e.target.value)} />
          <span>Custom — {accent}</span>
        </label>
        <button className="ghost" onClick={() => pick(DEFAULT_ACCENT)} disabled={accent.toLowerCase() === DEFAULT_ACCENT}>
          Reset
        </button>
      </div>
    </>
  )
}

// ── Bot logs ────────────────────────────────────────────────────────────────
function Logs() {
  const [lines, setLines] = useState<string[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const preRef = useRef<HTMLPreElement>(null)
  const load = (notify = false) => {
    setErr(null)
    api.getLogs(1000)
      .then((r: { lines: string[] }) => { setLines(r.lines || []); if (notify) toast('Logs refreshed', 'success') })
      .catch((e: any) => { const m = e?.message || 'failed to load logs'; setErr(m); if (notify) toast(m, 'danger') })
  }
  useEffect(() => { load() }, [])
  useEffect(() => { if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight }, [lines])
  return (
    <>
      <Head title="Bot logs" sub="Recent backend activity — the bot and the dashboard API, newest at the bottom." />
      <div className="settings-row end">
        <button className="ghost" onClick={() => load(true)}><Icon.refresh size={14} /> Refresh</button>
      </div>
      {err && <div className="settings-err">{err}</div>}
      <pre className="logview fill" ref={preRef}>{lines === null ? 'Loading…' : (lines.length ? lines.join('\n') : 'No logs yet.')}</pre>
    </>
  )
}

// ── Remote access ─────────────────────────────────────────────────────────────
function Remote() {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = (notify = false) => {
    setErr(null)
    api.getRemote()
      .then((d: any) => { setData(d); if (notify) toast('Remote access refreshed', 'success') })
      .catch((e: any) => { const m = e?.message || 'failed'; setErr(m); if (notify) toast(m, 'danger') })
  }
  useEffect(() => { load() }, [])
  const st = data?.status
  const url = (st?.public_url || '').replace(/\/$/, '')
  const isWeb = /^https:\/\//.test(url)
  return (
    <>
      <Head title="Remote access" sub="Reach this console from anywhere over Tailscale Funnel. Toggle it from the menu-bar tray." />
      {err && <div className="settings-err">{err}</div>}
      {!data ? <div className="settings-muted">Loading…</div> : (
        <>
          <div className="status-card">
            <span className={'dot' + (st?.running ? ' on' : ' warn')} />
            <div>
              <div className="status-line">{st?.running ? 'Online' : st?.available ? 'Off' : 'Not available in this build'}</div>
              {isWeb
                ? <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https:\/\//, '')}</a>
                : <span className="settings-muted">No public link — turn on remote access from the tray.</span>}
            </div>
            <button className="ghost" onClick={() => load(true)} style={{ marginLeft: 'auto' }}><Icon.refresh size={14} /></button>
          </div>

          <div className="settings-subhead">Who can access ({data.users?.length || 0})</div>
          <div className="userlist">
            {(data.users || []).length === 0 && <div className="settings-muted">No one has signed in yet.</div>}
            {(data.users || []).map((u: any) => (
              <div className="userrow" key={u.username + (u.last_login || '')}>
                <span className="uname">{u.username}</span>
                <span className="ubadge">{u.is_allowlisted ? 'Operator' : 'Admin'}</span>
                <span className="umeta">{u.guild_count} server{u.guild_count === 1 ? '' : 's'}</span>
                <span className="umeta">{u.last_login ? new Date(u.last_login).toLocaleString() : 'never'}</span>
              </div>
            ))}
          </div>

          <div className="settings-subhead">Remote-access logs</div>
          <pre className="logview short">{(data.logs || []).length ? data.logs.join('\n') : 'No remote-access activity logged.'}</pre>
        </>
      )}
    </>
  )
}

// ── Updates ───────────────────────────────────────────────────────────────────
const desktopUpdates = () => (window as any).olisar?.updates as
  | { state: () => Promise<any>; check: () => Promise<any>; install: () => Promise<any> }
  | undefined

function Updates() {
  const [data, setData] = useState<any>(null)
  const [checking, setChecking] = useState(false)
  const [canSelfUpdate, setCanSelfUpdate] = useState(false)
  const [installing, setInstalling] = useState(false)
  const du = desktopUpdates()

  const load = (notify = false) => {
    setChecking(true)
    Promise.all([
      api.getUpdates().catch(() => ({ error: "couldn't check" })),
      du ? du.check().catch(() => null) : Promise.resolve(null),
    ])
      .then(([backend, desk]: [any, any]) => {
        setData(backend); if (desk) setCanSelfUpdate(!!desk.canSelfUpdate)
        if (notify) {
          if (backend?.error) toast(backend.error, 'danger')
          else if (backend?.available) toast(`Update available — ${backend.latest}`, 'success')
          else toast('Up to date', 'success')
        }
      })
      .finally(() => setChecking(false))
  }
  useEffect(() => { load() }, [])

  const install = async () => {
    if (!du) return
    setInstalling(true)
    try {
      const r = await du.install()  // app quits + relaunches on a successful self-install
      if (r && r.ok === false) setInstalling(false)
    } catch {
      setInstalling(false)
    }
  }

  return (
    <>
      <Head title="Updates" sub="Olisar checks GitHub Releases for a newer version." />
      <div className="update-card">
        <div>
          <div className="settings-muted">Current version</div>
          <div className="version-now">v{data?.current ?? '…'}</div>
        </div>
        <div className="update-state">
          {!data ? 'Checking…'
            : data.error ? <span className="warn-text">{data.error}</span>
            : data.available
              ? <span className="ok-text"><Icon.update size={15} weight="Bold" /> Update available — {data.latest}</span>
              : <span className="ok-text"><Icon.check size={15} weight="Bold" /> Up to date</span>}
        </div>
      </div>
      {data?.available && !du && (
        <div className="update-direct">
          <Icon.update size={15} weight="Bold" /> Open the Olisar desktop app to install this update.
        </div>
      )}
      <div className="settings-row">
        {data?.available && du && (
          <button className="primary" onClick={install} disabled={installing}>
            <Icon.update size={15} weight="Bold" /> {installing ? 'Installing…' : (canSelfUpdate ? `Install ${data.latest} & restart` : `Download ${data.latest}`)}
          </button>
        )}
        <button className="ghost" onClick={() => load(true)} disabled={checking || installing}><Icon.refresh size={14} /> {checking ? 'Checking…' : 'Check again'}</button>
      </div>
      <p className="settings-foot">
        {du
          ? 'Installing downloads the new build and restarts Olisar automatically.'
          : 'Updates are installed from the Olisar desktop app — open it and update there.'}
      </p>
    </>
  )
}

// ── Desktop app ───────────────────────────────────────────────────────────────
function Desktop() {
  const [on, setOn] = useState<boolean | null>(null)
  const isDesktop = !!(window as any).olisar?.desktop
  useEffect(() => { api.getDesktop().then((d: any) => setOn(!!d.show_in_menu_bar)).catch(() => setOn(true)) }, [])
  const toggle = async (v: boolean) => {
    setOn(v)
    try { await api.putDesktop({ show_in_menu_bar: v }) } catch { setOn(!v) }
  }
  return (
    <>
      <Head title="Desktop app" sub="Settings for the Olisar desktop application." />
      <div className="settings-row between">
        <div>
          <div className="opt-label">Show in the menu bar</div>
          <div className="settings-muted">Keep Olisar's tray icon for quick access and remote-access control.</div>
        </div>
        {on === null ? <span className="settings-muted">…</span> : <Toggle value={on} onChange={toggle} />}
      </div>
      {!isDesktop && (
        <p className="settings-foot">You're viewing the web console — this applies to the installed desktop app, which picks it up on its next launch.</p>
      )}
    </>
  )
}
