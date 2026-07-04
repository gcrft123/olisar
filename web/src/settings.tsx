import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon, CloseX, type IconName } from './icons'
import { Area, Field, Select, Text, Toggle } from './ui'
import { toast, confirmDialog, promptDialog } from './overlays'
import { PubkeyBox, usePubkey } from './setup'
import { ACCENTS, DEFAULT_ACCENT, getAccent, setAccent } from './theme'

// A Notion-style settings popup: a centered overlay with a left section nav and a
// right content pane. App-wide operator settings (not per-server) live here.
type SectionId = 'appearance' | 'bot' | 'logs' | 'remote' | 'updates' | 'desktop' | 'feedback'
const SECTIONS: { id: SectionId; label: string; ic: IconName }[] = [
  { id: 'appearance', label: 'Appearance', ic: 'palette' },
  { id: 'bot', label: 'Bot', ic: 'bolt' },
  { id: 'logs', label: 'Logs', ic: 'docs' },
  { id: 'remote', label: 'Remote access', ic: 'remote' },
  { id: 'updates', label: 'Updates', ic: 'update' },
  { id: 'desktop', label: 'Desktop app', ic: 'settings' },
  { id: 'feedback', label: 'Feedback', ic: 'messages' },
]

// `sections` narrows the visible sections (default: all) — the pre-auth login/onboarding
// gears show a subset. `botSwitcherOnly` renders just the bot switcher under Bot (no
// per-server "Clear memory" danger zone, which needs a configured server).
export function SettingsModal(
  { onClose, sections, botSwitcherOnly }:
  { onClose: () => void; sections?: SectionId[]; botSwitcherOnly?: boolean },
) {
  const visible = sections ? SECTIONS.filter((s) => sections.includes(s.id)) : SECTIONS
  const [section, setSection] = useState<SectionId>(visible[0]?.id ?? 'appearance')
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <nav className="settings-nav">
          {visible.map((s) => {
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
          {section === 'bot' && (botSwitcherOnly ? <BotSwitcher /> : <Bot />)}
          {section === 'logs' && <Logs />}
          {section === 'remote' && <Remote />}
          {section === 'updates' && <Updates />}
          {section === 'desktop' && <Desktop />}
          {section === 'feedback' && <Feedback />}
        </div>
      </div>
    </div>
  )
}

// ── Logs ────────────────────────────────────────────────────────────────────
// Bot / Funnel are read from the server VM over SSH (server-hosting mode); This app is the
// local backend's own log buffer. Bot/Funnel return an "only for server-hosted bots" note
// when there's no VM configured.
function Logs() {
  const [which, setWhich] = useState<'bot' | 'funnel' | 'app'>('app')
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = (w: 'bot' | 'funnel' | 'app') => {
    setLoading(true); setErr(''); setText('')
    const p = w === 'app'
      ? api.getLogs(500).then((d: any) => (d.lines || []).join('\n'))
      : api.serverLogs(w).then((d: any) => (d?.ok ? (d.logs || '') : Promise.reject(new Error(d?.error || 'Only available for server-hosted bots.'))))
    p.then((t: string) => setText(t)).catch((e: any) => setErr(e?.message || 'Couldn’t load logs.')).finally(() => setLoading(false))
  }
  useEffect(() => { load(which) }, [which])

  const TABS: { id: 'bot' | 'funnel' | 'app'; label: string }[] = [
    { id: 'bot', label: 'Bot' }, { id: 'funnel', label: 'Funnel' }, { id: 'app', label: 'This app' },
  ]
  return (
    <>
      <Head title="Logs" sub="Recent logs. Bot and Funnel are read from your server over SSH; This app is the local control panel." />
      <div className="log-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={'ghost' + (which === t.id ? ' on' : '')} onClick={() => setWhich(t.id)}>{t.label}</button>
        ))}
        <span className="grow" />
        <button className="ghost icon-btn sm" data-tip="Refresh" aria-label="Refresh" onClick={() => load(which)}>
          <Icon.refresh size={14} />
        </button>
      </div>
      {loading ? <div className="settings-muted">Loading…</div>
        : err ? <div className="settings-err">{err}</div>
        : <pre className="srv-logs">{text || '(no logs)'}</pre>}
    </>
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

// ── Bot switcher (run several bots from one app) ──────────────────────────────
type BotProfile = { id: string; name: string; created: boolean }

function BotSwitcher() {
  const [profiles, setProfiles] = useState<BotProfile[] | null>(null)
  const [activeId, setActiveId] = useState('')
  const [defaultId, setDefaultId] = useState('')
  const [busy, setBusy] = useState(false)
  const [moving, setMoving] = useState<BotProfile | null>(null)

  const load = () =>
    api.botList()
      .then((d: any) => { setProfiles(d.profiles || []); setActiveId(d.active_id || ''); setDefaultId(d.default_id || '') })
      .catch(() => setProfiles([]))
  useEffect(() => { load() }, [])

  const switchTo = async (p: BotProfile) => {
    if (p.id === activeId || busy) return
    const ok = await confirmDialog({
      title: `Switch to ${p.name}?`,
      message: <>This stops the current bot and loads <b>{p.name}</b>. The console will reload.</>,
      confirmLabel: 'Switch',
    })
    if (!ok) return
    setBusy(true)
    try { await api.switchBot(p.id); window.location.reload() }
    catch (e: any) { toast(e?.message || 'Couldn’t switch bots', 'danger'); setBusy(false); load() }
  }

  const rename = async (p: BotProfile) => {
    const name = await promptDialog({
      title: 'Rename bot',
      confirmLabel: 'Rename',
      prompt: { placeholder: 'Bot name', defaultValue: p.name },
    })
    if (name === null || !name.trim() || name.trim() === p.name) return
    try { await api.renameBot(p.id, name.trim()); load() }
    catch (e: any) { toast(e?.message || 'Couldn’t rename the bot', 'danger') }
  }

  const makeDefault = async (p: BotProfile) => {
    if (p.id === defaultId || busy) return
    try { await api.setDefaultBot(p.id); toast(`${p.name} opens on launch`, 'success'); load() }
    catch (e: any) { toast(e?.message || 'Couldn’t set the default', 'danger') }
  }

  const create = async () => {
    const name = await promptDialog({
      title: 'Create a new bot',
      message: 'Give it a name — you’ll connect its Discord token next.',
      confirmLabel: 'Create',
      prompt: { placeholder: 'e.g. Support bot' },
    })
    if (name === null) return
    setBusy(true)
    try {
      const p = await api.createBot(name.trim() || 'New bot')
      await api.switchBot(p.id)
      window.location.reload()
    } catch (e: any) { toast(e?.message || 'Couldn’t create the bot', 'danger'); setBusy(false); load() }
  }

  const del = async (p: BotProfile) => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: `Delete ${p.name}?`,
      message: (
        <>
          This permanently deletes <b>{p.name}</b> and everything it stores — its token,
          settings, and memory. <strong style={{ color: 'var(--danger)' }}>This can’t be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: 'delete' },
      confirmLabel: 'Delete bot',
    })
    if (!ok) return
    try { await api.deleteBot(p.id); toast(`Deleted ${p.name}`, 'neutral'); load() }
    catch (e: any) { toast(e?.message || 'Couldn’t delete the bot', 'danger') }
  }

  const reset = async (p: BotProfile) => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: `Reset ${p.name}'s configuration?`,
      message: (
        <>
          Clears <b>{p.name}</b>’s Discord credentials, API keys, and server/hosting config and signs
          it out — but <b>keeps</b> its persona, memory, knowledge, and settings. You’ll set it up
          again (a server-hosted bot goes to Reconnect).{' '}
          <strong style={{ color: 'var(--danger)' }}>This can’t be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: 'reset' },
      confirmLabel: 'Reset configuration',
    })
    if (!ok) return
    try {
      const r = await api.resetBot(p.id)
      if (r?.active) window.location.reload()  // App re-routes to reconnect / setup
      else { toast(`Reset ${p.name}`, 'neutral'); load() }
    } catch (e: any) { toast(e?.message || 'Couldn’t reset the bot', 'danger') }
  }

  return (
    <>
      <Head title="Bots" sub="Run several bots from one app — each has its own token, settings, and memory. One local bot runs at a time; switching stops the current one and reloads the console." />
      {profiles === null ? <div className="settings-muted">Loading…</div> : (
        <div className="bot-list">
          {profiles.map((p) => {
            const isActive = p.id === activeId
            const isDefault = p.id === defaultId
            return (
              <div key={p.id} className={'bot-row' + (isActive ? ' on' : '')}>
                <span className="bot-ic"><Icon.bolt size={16} weight={isActive ? 'Bold' : 'Linear'} /></span>
                <div className="bot-name">
                  {p.name}
                  {!p.created && <span className="bot-sub">not set up yet</span>}
                </div>
                <span className="grow" />
                <div className="bot-actions">
                  {isDefault && <span className="badge">Default</span>}
                  {isActive && <span className="badge success">Active</span>}
                  {!isActive && <button className="ghost" disabled={busy} onClick={() => switchTo(p)}>Switch</button>}
                  {!isDefault && (
                    <button className="ghost icon-btn sm" data-tip="Set as default" aria-label="Set as default" disabled={busy} onClick={() => makeDefault(p)}>
                      <Icon.star size={14} />
                    </button>
                  )}
                  <button className="ghost icon-btn sm" data-tip="Rename" aria-label="Rename" disabled={busy} onClick={() => rename(p)}>
                    <Icon.edit size={14} />
                  </button>
                  {isActive && (
                    <button className="ghost icon-btn sm" data-tip="Move / change hosting" aria-label="Move / change hosting" disabled={busy} onClick={() => setMoving(p)}>
                      <Icon.remote size={14} />
                    </button>
                  )}
                  <button className="ghost icon-btn sm" data-tip="Reset configuration" aria-label="Reset configuration" disabled={busy} onClick={() => reset(p)}>
                    <Icon.eraser size={14} />
                  </button>
                  {!isActive && (
                    <button className="ghost icon-btn sm" data-tip="Delete bot" aria-label="Delete bot" disabled={busy} onClick={() => del(p)}>
                      <Icon.trash size={14} />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <div className="settings-row">
        <button disabled={busy} onClick={create}><Icon.add size={14} /> Create new bot</button>
      </div>
      {moving && <MoveBotModal profile={moving} onClose={() => setMoving(null)} />}
    </>
  )
}

// A dedicated "Move bot" flow: change where the active bot runs (this computer ↔ a cloud VM),
// carrying its data across and keeping the old copy as a backup. Only the active bot can be
// moved (it swaps the live DB + stops/starts the local bot), so this lives on the active row.
function MoveBotModal({ profile, onClose }: { profile: BotProfile; onClose: () => void }) {
  const [curMode, setCurMode] = useState<'local' | 'server' | ''>('')
  const [curHost, setCurHost] = useState('')
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState<'local' | 'server'>('server')
  const [host, setHost] = useState('')
  const [user, setUser] = useState('ubuntu')
  const [showKey, setShowKey] = useState(false)
  const [moving, setMoving] = useState(false)
  const [err, setErr] = useState('')

  const pk = usePubkey(target === 'server' && showKey)

  useEffect(() => {
    api.activeBot()
      .then((d: any) => {
        const m = d.hosting_mode === 'server' ? 'server' : 'local'
        setCurMode(m); setCurHost(d.server_host || '')
        setTarget(m === 'local' ? 'server' : 'local')  // default to the other host
      })
      .catch((e: any) => setErr(e?.message || 'Couldn’t read the bot’s current hosting.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !moving) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [moving, onClose])

  const sameServer = target === 'server' && curMode === 'server' && host.trim() === curHost && !!curHost
  const canMove = !moving && !loading && !err && (target === 'local' || (host.trim().length > 0 && !sameServer))

  const doMove = async () => {
    setErr(''); setMoving(true)
    try {
      const r = await api.moveBot(profile.id, { target, host: host.trim(), user: user.trim() || 'ubuntu' })
      if (!r?.ok) { setErr(r?.error || 'Move failed.'); setMoving(false); return }
      toast(r.note || `Moved ${profile.name}`, 'success')
      window.location.reload()  // active bot: hosting changed — App re-routes
    } catch (e: any) { setErr(e?.message || 'Move failed.'); setMoving(false) }
  }

  const curLabel = curMode === 'server' ? `a server${curHost ? ` (${curHost})` : ''}` : 'this computer'

  return (
    <div className="modal-backdrop" onClick={() => { if (!moving) onClose() }}>
      <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-head">
          <div className="confirm-icon"><Icon.remote size={22} weight="Bold" /></div>
          <div className="confirm-text">
            <div className="confirm-title">Move {profile.name}</div>
            <div className="confirm-msg">
              {loading ? 'Reading current hosting…' : <>Currently runs on <b>{curLabel}</b>.</>}
            </div>
          </div>
        </div>

        {!loading && (
          <div className="move-body">
            <div className="callout tip">
              <span className="ic"><Icon.info size={17} weight="Bold" /></span>
              <div className="callout-body">Reconfiguring an existing bot — its persona, memory, knowledge, and uploaded docs move with it, and the old copy is kept as a backup.</div>
            </div>

            {curMode === 'server' ? (
              <Field label="Move to" desc="Where this bot should run.">
                <Select value={target} onChange={(v) => setTarget(v as 'local' | 'server')}
                  options={[
                    { value: 'local', label: 'This computer (local)' },
                    { value: 'server', label: 'A different server' },
                  ]} />
              </Field>
            ) : (
              <div className="settings-muted">Move this bot to a cloud server — Olisar deploys it there and uploads all of its data.</div>
            )}

            {target === 'server' && (
              <>
                <Field label="Destination VM public IP" desc="A cloud VM you created for this bot.">
                  <Text value={host} onChange={setHost} placeholder="e.g. 203.0.113.9" mono />
                </Field>
                {sameServer && <div className="err">That’s the current server — pick a different IP or move to this computer.</div>}
                <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
                  <summary>Can’t connect? Add this app’s SSH key to the VM</summary>
                  <div className="desc" style={{ marginTop: 8 }}>
                    Paste this into the VM’s <code>~/.ssh/authorized_keys</code> (or the provider’s SSH-keys box) before moving. A VM this app already set up trusts it automatically.
                  </div>
                  <PubkeyBox state={pk} />
                  <Field label="SSH user" desc="The VM's login user — Ubuntu images use ubuntu.">
                    <Text value={user} onChange={setUser} placeholder="ubuntu" mono />
                  </Field>
                </details>
              </>
            )}

            {moving && (
              <div className="callout note">
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Moving {profile.name} — deploying + transferring data. This can take a few minutes; keep this window open.</div>
              </div>
            )}
            {err && <div className="err">{err}</div>}
          </div>
        )}

        <div className="confirm-foot">
          <button className="ghost" disabled={moving} onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!canMove} onClick={doMove}>{moving ? 'Moving…' : 'Move bot'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Bot (switcher + what Olisar remembers for the active server) ───────────────
function Bot() {
  const [busy, setBusy] = useState(false)
  const clearMemory = async () => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: 'Clear memory',
      message: (
        <>
          This erases everything Olisar has learned about this server — conversation memory, summaries, the
          search index, remembered facts, the glossary, snapshots, usage stats, its read on each member, and
          the knowledge base. Its persona, behaviour, channel roles, and command replies are kept.{' '}
          <strong style={{ color: 'var(--danger)' }}>This can't be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: 'clear olisar memory' },
      confirmLabel: 'Clear memory',
    })
    if (!ok) return
    setBusy(true)
    try {
      const r = await api.clearMemory()
      const c = (r && r.counts) || {}
      toast(`Memory cleared — forgot ${c.messages ?? 0} messages, ${c.facts ?? 0} facts, ${c.profiles ?? 0} member profiles, and ${c.knowledge ?? 0} knowledge sources.`, 'success')
    } catch (e: any) {
      toast(e?.message || 'Couldn’t clear memory', 'danger')
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <BotSwitcher />
      <div className="settings-subhead">Danger zone</div>
      <div className="settings-row between">
        <div>
          <div className="opt-label">Clear memory</div>
          <div className="settings-muted">Erase everything the active bot has learned about the current server — memory, member profiles, facts, the search index, glossary, and the knowledge base. It keeps its persona and your settings. This can't be undone.</div>
        </div>
        <button className="danger" onClick={clearMemory} disabled={busy}>
          {busy ? <><span className="spinner" /> Clearing…</> : 'Clear memory'}
        </button>
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
  // A headless server deployment (Docker / cloud VM) starts the funnel automatically from
  // its env-configured Tailscale key — it's always on and can't be driven from the console.
  const headless = !!st?.headless
  // The funnel can only be toggled when the bundled helper is present; flipping it on
  // re-uses the auth key saved during first-run setup (no key → the backend tells us).
  const canToggle = !!st?.available && !!st?.helper && !headless
  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) await api.enableTunnel()
      else await api.disableTunnel()
      toast(on ? 'Remote access on' : 'Remote access off', 'success')
      load()
      window.dispatchEvent(new Event('olisar:tunnel-changed'))  // refresh the sidebar card now
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
          {headless ? (
            <p className="settings-foot">
              Remote access is managed by the server — the Tailscale Funnel starts automatically from its configured key, so it’s always on and can’t be toggled from here.
            </p>
          ) : canToggle && (
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
