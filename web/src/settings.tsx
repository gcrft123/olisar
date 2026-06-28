import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon, CloseX, type IconName } from './icons'
import { Area, Field, Select, Text, Toggle } from './ui'
import { toast } from './overlays'
import { ACCENTS, DEFAULT_ACCENT, getAccent, setAccent } from './theme'

// A Notion-style settings popup: a centered overlay with a left section nav and a
// right content pane. App-wide operator settings (not per-server) live here.
type SectionId = 'appearance' | 'remote' | 'updates' | 'desktop' | 'feedback'
const SECTIONS: { id: SectionId; label: string; ic: IconName }[] = [
  { id: 'appearance', label: 'Appearance', ic: 'palette' },
  { id: 'remote', label: 'Remote access', ic: 'remote' },
  { id: 'updates', label: 'Updates', ic: 'update' },
  { id: 'desktop', label: 'Desktop app', ic: 'settings' },
  { id: 'feedback', label: 'Feedback', ic: 'messages' },
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
          {section === 'remote' && <Remote />}
          {section === 'updates' && <Updates />}
          {section === 'desktop' && <Desktop />}
          {section === 'feedback' && <Feedback />}
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

// ── Feedback ──────────────────────────────────────────────────────────────────
const FEEDBACK_TYPES = [
  { value: 'Feedback', label: 'Feedback' },
  { value: 'Bug report', label: 'Bug report' },
  { value: 'Question', label: 'Question' },
]
function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(new Error('read failed'))
    r.readAsDataURL(f)
  })
}

function Feedback() {
  const [category, setCategory] = useState('Feedback')
  const [message, setMessage] = useState('')
  const [email, setEmail] = useState('')
  const [files, setFiles] = useState<{ name: string; type: string; content_b64: string }[]>([])
  const [logsAttached, setLogsAttached] = useState(false)
  const [logs, setLogs] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const addFiles = async (list: FileList) => {
    const out = [...files]
    for (const f of Array.from(list)) {
      if (out.length >= 8) break
      if (f.size > 3_000_000) { toast(`${f.name} is too large (max 3 MB each).`, 'warning'); continue }
      out.push({ name: f.name, type: f.type || 'application/octet-stream', content_b64: await fileToB64(f) })
    }
    setFiles(out)
  }
  const attachLogs = async () => {
    try { const d = await api.getLogs(800); setLogs((d.lines || []).join('\n')); setLogsAttached(true) }
    catch { toast('Couldn’t read the bot logs.', 'danger') }
  }
  const submit = async () => {
    if (!message.trim()) { toast('Add a message first.', 'warning'); return }
    setBusy(true)
    try {
      const r = await api.sendFeedback({ category, message: message.trim(), email: email.trim(), logs: logsAttached ? logs : '', attachments: files })
      if (r && r.emailed === false) toast('Sent — but the email didn’t go through. The team will still see it.', 'warning')
      else toast(`Thanks — your ${category.toLowerCase()} was sent.`, 'success')
      setDone(true)
    } catch (e: any) { toast('Couldn’t send: ' + (e?.message || 'try again'), 'danger') }
    finally { setBusy(false) }
  }

  const placeholder = category === 'Bug report'
    ? 'What happened, and what did you expect instead?'
    : category === 'Question' ? 'What would you like to know?' : "What's on your mind?"

  if (done) {
    return (
      <>
        <Head title="Feedback" sub="Send feedback, report a bug, or ask a question — it's emailed straight to the Olisar team." />
        <div className="callout tip">
          <span className="ic"><Icon.check size={17} weight="Bold" /></span>
          <div className="callout-body">Thanks — your {category.toLowerCase()} was sent to the Olisar team.{email.trim() ? ` They'll reply to ${email.trim()} if needed.` : ''}</div>
        </div>
        <div className="settings-row end" style={{ marginTop: 16 }}>
          <button className="ghost" onClick={() => { setDone(false); setMessage(''); setFiles([]); setLogsAttached(false); setLogs('') }}>Send another</button>
        </div>
      </>
    )
  }
  return (
    <>
      <Head title="Feedback" sub="Send feedback, report a bug, or ask a question — it's emailed straight to the Olisar team." />
      <Field label="Type"><Select value={category} onChange={setCategory} options={FEEDBACK_TYPES} /></Field>
      <Field label="Message"><Area value={message} onChange={setMessage} rows={6} placeholder={placeholder} /></Field>
      <Field label="Your email" desc="Optional — so the team can reply."><Text value={email} onChange={setEmail} placeholder="you@example.com" /></Field>
      <div className="settings-subhead">Attachments (optional)</div>
      <div className="report-attach">
        <button className="ghost" onClick={() => fileRef.current?.click()}><Icon.add size={14} /> Add files</button>
        <button className={'ghost' + (logsAttached ? ' on' : '')} onClick={attachLogs}><Icon.docs size={14} /> {logsAttached ? 'Bot logs attached' : 'Add bot logs'}</button>
      </div>
      {files.length > 0 && (
        <div className="report-files">
          {files.map((f, i) => (
            <span key={i} className="tag">{f.name}<button className="tag-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Remove" title="Remove"><CloseX size={11} /></button></span>
          ))}
        </div>
      )}
      <input ref={fileRef} type="file" multiple style={{ display: 'none' }} onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
      <div className="settings-row end" style={{ marginTop: 18 }}>
        <button className="primary" onClick={submit} disabled={busy || !message.trim()}>{busy ? 'Sending…' : 'Send'}</button>
      </div>
    </>
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

// ── Remote access ─────────────────────────────────────────────────────────────
function Remote() {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
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
  // The funnel can only be toggled when the bundled helper is present; flipping it on
  // re-uses the auth key saved during first-run setup (no key → the backend tells us).
  const canToggle = !!st?.available && !!st?.helper
  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) await api.enableTunnel()
      else await api.disableTunnel()
      toast(on ? 'Remote access on' : 'Remote access off', 'success')
      load()
    } catch (e: any) {
      toast(e?.message || 'Could not change remote access', 'danger')
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Head title="Remote access" sub="Reach this console from anywhere over Tailscale Funnel." />
      {err && <div className="settings-err">{err}</div>}
      {!data ? <div className="settings-muted">Loading…</div> : (
        <>
          <div className="status-card">
            <span className={'dot' + (st?.running ? ' on' : ' warn')} />
            <div>
              <div className="status-line">{st?.running ? 'Online' : st?.available ? 'Off' : 'Not available in this build'}</div>
              {isWeb
                ? <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https:\/\//, '')}</a>
                : <span className="settings-muted">{st?.running ? 'Starting…' : 'No public link — turn remote access on.'}</span>}
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <button className="ghost" onClick={() => load(true)} title="Refresh" aria-label="Refresh"><Icon.refresh size={14} /></button>
              {canToggle && <Toggle value={!!st?.running} onChange={toggle} disabled={busy} />}
            </div>
          </div>
          {canToggle && (
            <p className="settings-foot">
              {st?.running
                ? 'The funnel is live. Turning it off closes the public link; you can still reach the console locally.'
                : 'Turning it on exposes the console over Tailscale Funnel using the auth key from setup. You can also toggle it from the menu-bar tray.'}
            </p>
          )}

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
