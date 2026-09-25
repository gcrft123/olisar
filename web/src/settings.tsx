import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { Icon, CloseX, type IconName } from './icons'
import { Area, Field, Segmented, Select, Spinner, Text, Toggle, hasDraft, useDraft, useFieldIds } from './ui'
import { ActivityCard } from './pages'
import { Modal, toast, confirmDialog } from './overlays'
import { BotMenu, BotsPane, useBots } from './bots'
import { SCALES, getScale, setScale } from './theme'
import { openFeedback, registerFeedbackHost, type FeedbackPrefill } from './feedback'
import { isBeta } from './version'

// A Notion-style settings popup: a centered overlay with a left section nav and a
// right content pane. App-wide operator settings (not per-server) live here.
// 'size' is the member portal's cut-down General — the size control alone, without the
// console-only keyboard shortcuts. 'bots' only exists in the desktop app (see bots.tsx).
export type SectionId = 'general' | 'size' | 'activity' | 'bots' | 'logs' | 'security' | 'remote' | 'updates' | 'desktop' | 'feedback'
export const SECTIONS: { id: SectionId; label: string; ic: IconName }[] = [
  { id: 'general', label: 'General', ic: 'settings' },
  { id: 'size', label: 'Size', ic: 'palette' },
  { id: 'activity', label: 'Activity', ic: 'docs' },
  { id: 'bots', label: 'Bots', ic: 'bolt' },
  { id: 'logs', label: 'Logs', ic: 'pulse' },
  { id: 'security', label: 'Security', ic: 'access' },
  { id: 'remote', label: 'Remote access', ic: 'remote' },
  { id: 'updates', label: 'Updates', ic: 'update' },
  { id: 'desktop', label: 'Desktop app', ic: 'settings' },
  { id: 'feedback', label: 'Feedback', ic: 'messages' },
]

// ── Reporting a blank reply ───────────────────────────────────────────────────
// Olisar puts a "Report this" button on a reply that came back blank; it links here with
// ?report=<token>. The console decides where that lands — admin console or member portal —
// by who signs in, so one link serves both.

const REPORT_KEY = 'olisar_report'

// Read at module load, before React mounts, for two reasons: the link usually arrives at
// the sign-in screen rather than a signed-in console, and the Discord OAuth round trip
// comes back to a bare "/" — the query string is gone by the time auth resolves. Parking it
// in sessionStorage carries it across both, and only for this tab.
;(() => {
  try {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('report')
    if (!token) return
    sessionStorage.setItem(REPORT_KEY, token)
    params.delete('report')
    const query = params.toString()
    history.replaceState({}, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash)
  } catch { /* private mode, or no sessionStorage — the report just won't pre-fill */ }
})()

/** The token a "Report this" button delivered to this tab, or ''. */
export function pendingReport(): string {
  try { return sessionStorage.getItem(REPORT_KEY) || '' } catch { return '' }
}

/** Forget it, so a later visit doesn't reopen a report already filed or dismissed. */
export function clearPendingReport(): void {
  try { sessionStorage.removeItem(REPORT_KEY) } catch { /* nothing to clear */ }
}

// `sections` narrows the visible sections (default: all) — the pre-auth login/onboarding
// gears show a subset. `report` opens Feedback pre-filled from a parked blank reply;
// `prefill` opens it pre-filled from whatever screen sent the operator here.
export function SettingsModal(
  { onClose, sections, initialSection, report, prefill }:
  { onClose: () => void; sections?: SectionId[]; initialSection?: SectionId; report?: string; prefill?: FeedbackPrefill },
) {
  // 'size' is the member portal's cut-down General; the console shows General instead,
  // so an unfiltered modal must not offer both. 'bots' needs the desktop app's gateway.
  const bots = useBots()
  const visible = (sections ? SECTIONS.filter((s) => sections.includes(s.id))
    : SECTIONS.filter((s) => s.id !== 'size'))
    .filter((s) => s.id !== 'bots' || bots.available || bots.loading)
  const hasFeedback = visible.some((v) => v.id === 'feedback')
  const first = prefill && hasFeedback ? 'feedback' : initialSection
  const [section, setSection] = useState<SectionId>(
    (first && visible.some((v) => v.id === first) ? first : visible[0]?.id) ?? 'general',
  )
  // The pre-fill Feedback opens with. Keyed, so a second hand-off from inside this modal
  // (Logs → "Send with a bug report") starts a fresh form rather than keeping the last one.
  const [fb, setFb] = useState<{ prefill?: FeedbackPrefill; n: number }>({ prefill, n: 0 })
  const goFeedback = (p: FeedbackPrefill) => { setFb((f) => ({ prefill: p, n: f.n + 1 })); setSection('feedback') }

  // Escape and a backdrop click reach this sheet from anywhere inside it, including the
  // Feedback pane's composer. Ask before discarding text the operator typed but never sent.
  const guardedClose = async () => {
    if (hasDraft()) {
      const ok = await confirmDialog({
        title: 'Discard your message?',
        message: 'You’ve written a message but haven’t sent it. Closing settings will discard it.',
        confirmLabel: 'Discard',
        cancelLabel: 'Keep writing',
        tone: 'warning',
      })
      if (ok !== true) return
    }
    onClose()
  }

  return (
    <Modal className={'settings-modal' + (visible.length === 1 ? ' single' : '')} label={visible.length === 1 ? visible[0].label : 'Settings'} onClose={guardedClose}>
        {/* A modal opened for one pane (Feedback from a screen that has no Settings of its
            own) gets no nav: a column holding a single item is a menu with nothing to pick. */}
        {visible.length > 1 && <nav className="settings-nav" aria-label="Settings sections">
          {visible.map((s) => {
            const Glyph = Icon[s.ic]
            return (
              <button
                key={s.id}
                className={'settings-nav-item' + (section === s.id ? ' active' : '')}
                aria-current={section === s.id ? 'page' : undefined}
                onClick={() => setSection(s.id)}
              >
                <Glyph size={16} weight={section === s.id ? 'Bold' : 'Linear'} /> {s.label}
              </button>
            )
          })}
        </nav>}
        <div className="settings-body">
          <button className="settings-close" onClick={guardedClose} aria-label="Close settings" title="Close (Esc)">
            <CloseX size={18} />
          </button>
          {section === 'general' && <General />}
          {section === 'size' && <SizeOnly />}
          {section === 'activity' && <Activity />}
          {section === 'bots' && <BotsPane Head={Head} />}
          {section === 'logs' && <Logs onReport={hasFeedback ? () => goFeedback({ category: 'Bug report', logs: true }) : undefined} />}
          {section === 'security' && <Security />}
          {section === 'remote' && <Remote />}
          {section === 'updates' && <Updates />}
          {section === 'desktop' && <Desktop />}
          {section === 'feedback' && <Feedback key={fb.n} report={report} prefill={fb.prefill} />}
        </div>
    </Modal>
  )
}

// The top corners of every screen outside the console: which bot this is on the left, and
// Settings on the right. Setup and sign-in had them; the screens that stop you getting in (no
// servers, access denied, a bot that won't start) didn't, so Feedback, Logs and the bot list
// were out of reach exactly when something was wrong.
export const PRE_CONSOLE_SECTIONS: SectionId[] = ['general', 'bots', 'logs', 'updates', 'desktop', 'feedback']

export function ScreenCorners({ sections = PRE_CONSOLE_SECTIONS }: { sections?: SectionId[] }) {
  const [open, setOpen] = useState(false)
  const [pane, setPane] = useState<SectionId | undefined>(undefined)
  return (
    <>
      <BotMenu variant="chip" onManage={() => { setPane('bots'); setOpen(true) }} />
      <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => { setPane(undefined); setOpen(true) }}>
        <Icon.settings size={16} />
      </button>
      {open && <SettingsModal sections={sections} initialSection={pane} onClose={() => setOpen(false)} />}
    </>
  )
}

// ── Logs ────────────────────────────────────────────────────────────────────
// Bot / Funnel are read from the server VM over SSH (server-hosting mode); This app is the
// local backend's own log buffer. Bot/Funnel return an "only for server-hosted bots" note
// when there's no VM configured.
function Logs({ onReport }: { onReport?: () => void }) {
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [source, setSource] = useState<'vm' | 'local'>('vm')
  const [loading, setLoading] = useState(false)

  // One view, two ways of reaching the same output.
  //
  // /api/server/* is loopback-gated: it drives a remote VM over SSH, so it must not be
  // reachable from the public tunnel. That means the desktop control panel can ask the VM
  // for `docker compose logs`, but the console *served by* that VM cannot. From inside the
  // container there is no docker socket either, so the closest equivalent is the in-process
  // ring buffer, fed by the same logger that writes the container's stdout.
  const load = () => {
    setLoading(true); setErr(''); setText('')
    api.serverLogs('bot')
      .then((d: any) => {
        if (!d?.ok) throw new Error(d?.error || 'unavailable')
        setSource('vm'); setText(d.logs || '')
      })
      .catch(() =>
        api.getLogs(500)
          .then((d: any) => { setSource('local'); setText((d.lines || []).join('\n')) })
          .catch((e: any) => setErr(e?.message || 'Couldn’t load logs.')))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  // Newest first. Both sources emit oldest-first, which puts the line you opened this pane
  // to read at the bottom of a 500-line block. Reversing is per-line, so a stack trace reads
  // bottom-up where it appears — the exception line lands first, which is the one being
  // looked for anyway.
  const shown = text ? text.split('\n').reverse().join('\n') : ''

  return (
    <>
      <Head
        title="Logs"
        sub={source === 'vm'
          ? 'Container logs from the server, newest first.'
          : 'This bot’s own output, newest first.'}
      />
      {/* Same toolbar as the Activity ledger, so reload sits in one place across the modal.
          Nobody opens the logs when things are fine, so the way to report what they show sits
          beside them, and arrives with "Add bot logs" already on. */}
      <div className="act-toolbar">
        {onReport && <button className="ghost" onClick={onReport}>Send with a bug report</button>}
        <button className="ghost icon-btn" data-tip="Refresh" aria-label="Refresh logs" onClick={load}>
          <Icon.refresh size={15} />
        </button>
      </div>
      {loading ? <Spinner />
        : err ? <div className="settings-err" role="alert">{err}</div>
        : <pre className="srv-logs">{shown || '(no logs)'}</pre>}
    </>
  )
}

function Head({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="settings-head">
      <h2>{title}</h2>
      {sub && <p>{sub}</p>}
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

// Where the blank happened, in the fewest words that let someone recognize it. A DM has no
// channel worth naming and no server the reporter would think of by name.
function reportPlace(r: { server?: string; channel?: string; trigger?: string }): string {
  if (r.trigger === 'dm') return 'in a DM'
  if (r.channel) return `in #${r.channel}`
  return r.server ? `in ${r.server}` : ''
}

// The bug report, already written as far as the facts go. What's left is the one thing only
// the reporter knows, and the placeholder under it says so.
function reportDraft(r: { prompt?: string; when?: string; server?: string; channel?: string; trigger?: string }): string {
  const place = reportPlace(r)
  // Same shape the Activity ledger uses: no seconds, no year. This is "which reply",
  // not a timestamp anyone reads back digit by digit.
  const when = r.when
    ? new Date(r.when).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : ''
  const where = [place, when && `on ${when}`].filter(Boolean).join(' ')
  return [
    `Olisar drew a blank${where ? ' ' + where : ''}.`,
    '',
    'What I asked:',
    r.prompt || '(nothing recorded)',
    '',
    'What I expected instead:',
    '',
  ].join('\n')
}

function Feedback({ report, prefill }: { report?: string; prefill?: FeedbackPrefill }) {
  const [category, setCategory] = useState<string>(report ? 'Bug report' : prefill?.category ?? 'Feedback')
  const [message, setMessage] = useState(prefill?.message ?? '')
  const [email, setEmail] = useState('')
  const [files, setFiles] = useState<{ name: string; type: string; content_b64: string }[]>([])
  const [logsAttached, setLogsAttached] = useState(!!prefill?.logs)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  // The parked failure this report is about, once the server confirms it's ours to claim.
  const [claimed, setClaimed] = useState<{ has_logs?: boolean } | null>(null)
  const [claimError, setClaimError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  // What we pre-filled, so an untouched draft isn't mistaken for theirs. Without it,
  // opening a report and deciding not to file it asked "Discard your message?" about text
  // the console wrote — a guard on work nobody did.
  const prefilled = useRef(prefill?.message ?? '')
  // A written-but-unsent message is work. Escape used to bin it silently.
  useDraft(() => !done && message.trim() !== '' && message !== prefilled.current)

  // Arriving from a "Report this" button: fetch the parked failure and write the report.
  // The prompt never travelled in the URL, so this is the first time the page sees it.
  useEffect(() => {
    if (!report) return
    let live = true
    api.getReport(report)
      .then((r: any) => {
        if (!live) return
        const draft = reportDraft(r)
        setClaimed(r)
        setCategory('Bug report')
        prefilled.current = draft
        setMessage(draft)
        setLogsAttached(!!r.has_logs)
      })
      .catch((e: any) => {
        if (!live) return
        // Expired, already gone, or someone else's link. Say so and leave the composer
        // usable — they came here to report something either way.
        setClaimError(e?.message || 'That report link isn’t valid any more.')
        clearPendingReport()
      })
    return () => { live = false }
  }, [report])

  const addFiles = async (list: FileList) => {
    const out = [...files]
    for (const f of Array.from(list)) {
      if (out.length >= 8) break
      if (f.size > 3_000_000) { toast(`${f.name} is too large (max 3 MB each).`, 'warning'); continue }
      out.push({ name: f.name, type: f.type || 'application/octet-stream', content_b64: await fileToB64(f) })
    }
    setFiles(out)
  }
  // A toggle, not a fetch. The client used to read the logs and post them back, which
  // needs admin — so in the member portal this button could only ever fail. The server
  // attaches its own now, and the reporter (member or operator) never sees them.
  const toggleLogs = () => setLogsAttached((on) => !on)
  const submit = async () => {
    if (!message.trim()) { toast('Add a message first.', 'warning'); return }
    setBusy(true)
    try {
      const r = await api.sendFeedback({
        category, message: message.trim(), email: email.trim(),
        include_logs: logsAttached, attachments: files,
        // Which logs to attach, not whether: the toggle above decides that. Ignored by the
        // server once the parked failure has expired or if it was never ours.
        report_token: claimed ? report || '' : '',
      })
      if (r && r.emailed === false) toast('Sent, but the email didn’t go through. The team will still see it.', 'warning')
      else toast(`Thanks — your ${category.toLowerCase()} was sent.`, 'success')
      // Filed. A refresh or a second visit in this tab shouldn't reopen it.
      clearPendingReport()
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
        <Head title="Feedback" sub="Goes straight to the Olisar team." />
        <div className="callout tip">
          <span className="ic"><Icon.check size={17} weight="Bold" /></span>
          <div className="callout-body">Thanks — your {category.toLowerCase()} was sent.{email.trim() ? ` The team will reply to ${email.trim()} if needed.` : ''}</div>
        </div>
        <div className="settings-row end" style={{ marginTop: 16 }}>
          {/* Clears the claimed report too: the next message is a fresh one, and it must
              not quietly ship the previous failure's logs under it. */}
          <button className="ghost" onClick={() => {
            setDone(false); setMessage(''); setFiles([]); setLogsAttached(false)
            setClaimed(null); setClaimError(''); prefilled.current = ''
          }}>Send another</button>
        </div>
      </>
    )
  }
  return (
    <>
      <Head title="Feedback" sub="Goes straight to the Olisar team." />
      {/* Only the failure case gets a callout. A report that opened correctly explains
          itself — the type is set, the message is written, the logs button is on — and a
          banner narrating what the reader can already see is one more thing to read. */}
      {claimError && (
        <div className="callout warning">
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">{claimError} You can still describe what happened below.</div>
        </div>
      )}
      <Field label="Type"><Select value={category} onChange={setCategory} options={FEEDBACK_TYPES} /></Field>
      <Field label="Message"><Area value={message} onChange={setMessage} rows={claimed || prefill?.message ? 9 : 6} placeholder={placeholder} /></Field>
      <Field label="Your email" desc="Optional, so the team can reply."><Text value={email} onChange={setEmail} placeholder="you@example.com" /></Field>
      <div className="settings-subhead">Attachments (optional)</div>
      <div className="report-attach">
        <button className="ghost" onClick={() => fileRef.current?.click()}><Icon.add size={14} /> Add files</button>
        {!prefill?.noLogs && <button className={'ghost' + (logsAttached ? ' on' : '')} aria-pressed={logsAttached} onClick={toggleLogs}><Icon.pulse size={14} /> {logsAttached ? 'Bot logs attached' : 'Add bot logs'}</button>}
      </div>
      {files.length > 0 && (
        <div className="report-files">
          {files.map((f, i) => (
            <span key={i} className="tag">{f.name}<button className="tag-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`}><CloseX size={11} /></button></span>
          ))}
        </div>
      )}
      <input ref={fileRef} type="file" multiple style={{ display: 'none' }} aria-label="Add attachments" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
      <div className="settings-row end" style={{ marginTop: 18 }}>
        <button className="primary" onClick={submit} disabled={busy || !message.trim()}>{busy ? 'Sending…' : 'Send'}</button>
      </div>
    </>
  )
}

// ── Opening Feedback from elsewhere ───────────────────────────────────────────
/** Make this screen's Settings modal the one `openFeedback` opens. */
export function useFeedbackHost(open: (prefill?: FeedbackPrefill) => void): void {
  const latest = useRef(open)
  latest.current = open
  useEffect(() => registerFeedbackHost((p) => latest.current(p)), [])
}

/** For a parent that owns a Settings modal but can't call hooks where it renders it (App's
 *  console branch sits below a run of early returns). Mounted only while that branch is. */
export function FeedbackHost({ onOpen }: { onOpen: (prefill?: FeedbackPrefill) => void }) {
  useFeedbackHost(onOpen)
  return null
}

/** A button that opens Feedback pre-filled. On a screen with a Settings modal it opens there;
 *  on one without (access denied, suspended), it opens a Feedback-only modal of its own. */
export function FeedbackButton(props: { prefill?: FeedbackPrefill; className?: string; children: ReactNode }) {
  const [local, setLocal] = useState<FeedbackPrefill | null>(null)
  return (
    <>
      <button type="button" className={props.className ?? 'ghost'}
        onClick={() => { if (!openFeedback(props.prefill)) setLocal(props.prefill ?? {}) }}>
        {props.children}
      </button>
      {local && <SettingsModal sections={['feedback']} prefill={local} onClose={() => setLocal(null)} />}
    </>
  )
}

// ── Activity ───────────────────────────────────────────────────────────────
// The same ledger Knowledge shows beside its danger zone, reachable from anywhere. Every
// page can change something durable; only one of them could show you what it changed.
function Activity() {
  return (
    <>
      <Head title="Activity" sub="What has been changed in this console, newest first." />
      <ActivityCard bare />
    </>
  )
}

// The member portal's General. Size alone: the console's keyboard shortcuts below are all
// console actions (⌘K palette, ⌘S save) that the portal doesn't have, so listing them there
// would advertise keys that do nothing.
function SizeOnly() {
  return (
    <>
      <Head title="Size" sub="Saved in this browser, so everyone sets their own." />
      <div className="settings-row">
        <SizeChoice />
      </div>
      <p className="settings-foot">
        Scales the whole interface, the way your browser's zoom does.
      </p>
    </>
  )
}

// ── General ────────────────────────────────────────────────────────────────
function General() {
  return (
    <>
      <Head title="General" sub="Saved on this device, so everyone who signs in sets their own." />
      <div className="settings-subhead">Size</div>
      <div className="settings-row">
        <SizeChoice />
      </div>
      <p className="settings-foot">
        Scales the whole interface, the way your browser's zoom does. Applies to this browser only.
      </p>

      {/* This pane held one three-option control in a 900x620 sheet — about 85% empty on the
          modal's default view. Shortcuts belong to "how the console behaves for me", they
          are the one thing in the product with nowhere to be discovered, and they fill the
          space with something the operator can use rather than with padding. */}
      <div className="settings-subhead">Keyboard</div>
      <dl className="shortcuts">
        {[
          [['⌘', 'K'], 'Open the command palette', 'Jump to any page, pane or doc — or run this page’s actions'],
          [['⌘', 'S'], 'Save this page', 'Applies the edits the bar at the bottom is holding'],
          [['Esc'], 'Close', 'Dismisses a dialog, the palette, or the nav drawer'],
          [['Tab'], 'Move through the page', 'The skip link is the first stop'],
          [['↑', '↓'], 'Move in a list', 'In the palette, and in any mode picker'],
          [['Enter'], 'Confirm', 'Runs the highlighted command or the dialog’s safe action'],
        ].map(([keys, what, why]) => (
          <div className="shortcut" key={what as string}>
            <dt>{(keys as string[]).map((k) => <kbd key={k}>{k}</kbd>)}</dt>
            <dd><b>{what as string}</b><span>{why as string}</span></dd>
          </div>
        ))}
      </dl>
      <p className="settings-foot">
        On Windows and Linux, <kbd>Ctrl</kbd> stands in for <kbd>⌘</kbd>.
      </p>
    </>
  )
}

// The interface-size preference. Same exclusive-choice contract as every other
// segmented control in the console, so it uses the same component.
function SizeChoice() {
  const [scale, setScaleState] = useState(getScale)
  return (
    <Segmented
      className="useg"
      ariaLabel="Interface size"
      value={scale}
      onChange={(v) => { setScale(v); setScaleState(v) }}
      options={SCALES.map((x) => ({ value: x.value, label: x.label }))}
    />
  )
}

// ── Security (the tool PIN) ──────────────────────────────────────────────────
// One PIN for the whole install, so it lives here rather than on a per-server page: the
// bot is one Discord account however many servers it's in, and a PIN that differed by
// server would be four digits nobody could keep straight.

const WAIT_OPTS = [
  { value: '30', label: '30 seconds' },
  { value: '60', label: '1 minute' },
  { value: '120', label: '2 minutes' },
  { value: '300', label: '5 minutes' },
]

// A four-digit field: numeric keypad on a phone, masked by default, and never offered to a
// password manager. `inputMode` rather than `type="number"`, which would bring spinners and
// strip a leading zero — 0042 is a PIN, not the number forty-two.
function PinInput(props: { value: string; onChange: (v: string) => void; label: string }) {
  const f = useFieldIds()
  const [shown, setShown] = useState(false)
  return (
    <div className="key-input">
      <input
        type={shown ? 'text' : 'password'}
        id={f?.id}
        aria-labelledby={f?.labelId}
        aria-describedby={f?.descId}
        inputMode="numeric"
        autoComplete="off"
        maxLength={4}
        className="mono"
        placeholder="••••"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value.replace(/\D/g, '').slice(0, 4))}
        onBlur={() => setShown(false)}
      />
      {!!props.value && (
        <button
          type="button"
          className="ghost icon-btn key-reveal"
          onClick={() => setShown((v) => !v)}
          data-tip={shown ? 'Hide' : 'Reveal'}
          aria-label={shown ? `Hide the ${props.label.toLowerCase()}` : `Reveal the ${props.label.toLowerCase()}`}
          aria-pressed={shown}
        >
          {shown ? <Icon.eyeOff size={16} /> : <Icon.eye size={16} />}
        </button>
      )}
    </div>
  )
}

function Security() {
  const [data, setData] = useState<any>(null)
  const [pin, setPin] = useState('')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api.getPin().then(setData).catch((e: any) => setErr(e?.message || 'Could not load the PIN status'))
  useEffect(() => { load() }, [])

  const save = async () => {
    if (pin.length !== 4) { setErr('The PIN is four digits.'); return }
    if (pin !== confirm) { setErr('The two PINs don’t match.'); return }
    setErr('')
    setBusy(true)
    try {
      await api.putPin({ pin })
      setPin(''); setConfirm('')
      await load()
      window.dispatchEvent(new Event('olisar:pin-changed'))
      toast(data?.is_set ? 'PIN changed' : 'PIN set', 'success')
    } catch (e: any) {
      setErr(e?.message || 'Could not save the PIN')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    const ok = await confirmDialog({
      title: 'Remove the PIN?',
      message: 'Anything that asks for it can’t be confirmed, so Olisar won’t run it.',
      confirmLabel: 'Remove',
      cancelLabel: 'Keep it',
      tone: 'danger',
    })
    if (ok !== true) return
    setErr('')
    setBusy(true)
    try {
      await api.clearPin(); await load()
      window.dispatchEvent(new Event('olisar:pin-changed'))
      toast('PIN removed', 'neutral')
    }
    catch (e: any) { setErr(e?.message || 'Could not remove the PIN') }
    finally { setBusy(false) }
  }

  const setWait = async (v: string) => {
    setErr('')
    setData({ ...data, timeout_sec: Number(v) })
    try { await api.putPin({ timeout_sec: Number(v) }) }
    catch (e: any) { setErr(e?.message || 'Could not save that'); load() }
  }

  const isSet = !!data?.is_set
  return (
    <>
      <Head
        title="Security"
        sub="A 4-digit PIN that confirms certain actions in Discord before Olisar takes them. Anyone who has it can confirm. Each server picks its actions under Access."
      />
      {err && <div className="settings-err" role="alert">{err}</div>}
      {!data ? <Spinner /> : (
        <>
          <div className="status-card">
            <span className={'dot' + (isSet ? ' on' : ' warn')} />
            <div>
              <div className="status-line">{isSet ? 'PIN set' : 'No PIN set'}</div>
              {isSet && (
                <span className="settings-muted">
                  {`Last changed ${data.updated_at ? new Date(data.updated_at).toLocaleString() : 'recently'}`}
                </span>
              )}
            </div>
          </div>

          <div className="settings-subhead">{isSet ? 'Change the PIN' : 'Set a PIN'}</div>
          {/* Entered twice because it's masked, four characters long, and the first place a
              typo would show up is a prompt in Discord that won't accept it. */}
          <Field label="New PIN">
            <PinInput value={pin} onChange={setPin} label="New PIN" />
          </Field>
          <Field label="Confirm">
            <PinInput value={confirm} onChange={setConfirm} label="Confirm" />
          </Field>
          <Field label="PIN prompt timer">
            <Select
              value={String(data.timeout_sec ?? 120)}
              onChange={setWait}
              options={WAIT_OPTS}
              ariaLabel="PIN prompt timer"
            />
          </Field>
          <div className="settings-row end">
            {isSet && <button className="danger" onClick={remove} disabled={busy}>Remove PIN</button>}
            <button className="primary" onClick={save} disabled={busy || !pin || !confirm}>
              {isSet ? 'Change PIN' : 'Set PIN'}
            </button>
          </div>
        </>
      )}
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
      <Head title="Remote access" sub="Reach this console from anywhere, over Tailscale." />
      {err && <div className="settings-err" role="alert">{err}</div>}
      {!data ? <Spinner /> : (
        <>
          <div className="status-card">
            <span className={'dot' + (st?.running ? ' on' : ' warn')} />
            <div>
              <div className="status-line">{st?.running ? 'Online' : st?.available ? 'Off' : 'Not available in this build'}</div>
              {isWeb
                ? <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https:\/\//, '')}</a>
                : <span className="settings-muted">{st?.running ? 'Starting…' : 'No public link yet.'}</span>}
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <button className="ghost icon-btn sm" onClick={() => load(true)} data-tip="Refresh" aria-label="Refresh"><Icon.refresh size={14} /></button>
              {canToggle && <Toggle value={!!st?.running} onChange={toggle} disabled={busy} ariaLabel="Remote access" />}
            </div>
          </div>
          {headless ? (
            <p className="settings-foot">
              Your server manages remote access, so it’s always on and can’t be turned off from here.
            </p>
          ) : canToggle && (
            <p className="settings-foot">
              {st?.running
                ? 'Turning it off closes the public link. You can still reach the console from this machine.'
                : 'Turning it on publishes the console using the Tailscale key from setup.'}
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

type Channel = 'stable' | 'beta'

function Updates() {
  const [data, setData] = useState<any>(null)
  const [checking, setChecking] = useState(false)
  const [canSelfUpdate, setCanSelfUpdate] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [channel, setChannel] = useState<Channel | null>(null)
  const du = desktopUpdates()

  const load = (notify = false) => {
    setChecking(true)
    Promise.all([
      // Keep the reason. Replacing it with a fixed string left the operator with nothing to
      // act on and nothing to quote — "couldn't check" is true of every possible cause.
      api.getUpdates().catch((e: any) => ({ error: e?.message || "couldn't check for updates" })),
      du ? du.check().catch(() => null) : Promise.resolve(null),
    ])
      .then(([backend, desk]: [any, any]) => {
        setData(backend); if (desk) setCanSelfUpdate(!!desk.canSelfUpdate)
        if (backend?.channel) setChannel(backend.channel)
        if (notify) {
          if (backend?.error) toast(backend.error, 'danger')
          else if (backend?.available) toast(`Update available — ${backend.latest}`, 'success')
          else toast('Up to date', 'success')
        }
      })
      .finally(() => setChecking(false))
  }
  useEffect(() => { load() }, [])

  // Saved by the backend, where the desktop shell reads it too, then re-checked so the
  // card and the tray both answer for the new channel.
  const pickChannel = async (next: Channel) => {
    const prev = channel
    setChannel(next)
    try {
      await api.putUpdateChannel(next)
      load()
    } catch (e: any) {
      setChannel(prev)
      toast(e?.message || "Couldn't change the update channel", 'danger')
    }
  }

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
      <Head title="Updates" />
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
      <div className="settings-subhead">Channel</div>
      <div className="settings-row">
        {channel === null ? <span className="settings-muted">…</span> : (
          <Segmented
            className="useg"
            ariaLabel="Update channel"
            value={channel}
            onChange={pickChannel}
            options={[{ value: 'stable', label: 'Stable' }, { value: 'beta', label: 'Beta' }]}
          />
        )}
      </div>
      {channel === 'stable' && isBeta(data?.current) && (
        <p className="settings-foot">You'll stay on v{data.current} until a newer stable release is out.</p>
      )}
      {!du && (
        <p className="settings-foot">Updates are installed from the Olisar desktop app.</p>
      )}
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
      <Head title="Desktop app" />
      <div className="settings-row between">
        <div>
          <div className="opt-label">Show in the menu bar</div>
          <div className="settings-muted">Keep Olisar's tray icon for quick access and remote-access control.</div>
        </div>
        {on === null ? <span className="settings-muted">…</span> : <Toggle value={on} onChange={toggle} ariaLabel="Show in the menu bar" />}
      </div>
      {!isDesktop && (
        <p className="settings-foot">This applies to the installed desktop app, which picks it up on its next launch.</p>
      )}
    </>
  )
}
