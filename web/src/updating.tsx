import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { createForm, SHAPE, type Form, type Mood } from './form'
import { CheckMark, CloseX, Icon } from './icons'
import { restoreFocus, toast } from './overlays'
import { hasDraft, hasUnsavedChanges } from './ui'
import { isNewer } from './version'

// ── The update screen ───────────────────────────────────────────────────────────
// Installing an update takes the desktop app down: it downloads, unpacks, stops every bot,
// quits and reopens on the new version. Before this screen the only sign of any of it was a
// button reading "Installing…", and then the window vanished. Meanwhile the console stayed
// live under a backend that was about to stop, so a setting saved in that minute could be
// lost, and unsaved edits held the quit open and left the app half shut down.
//
// So once an install starts, from Settings, the tray or a notification, this takes the whole
// window: the first-run frame (see DESIGN.md, "The update screen") with the steps this
// platform takes, the download's progress, and the form sweeping as it does while the server
// updates. The console underneath is inert and its shortcuts are held. The download can be
// cancelled; nothing after it can. A failure says which step failed and why, and leaves the
// way back to the console. The desktop shell (desktop/updater.js) feeds it.

type Phase = 'download' | 'unpack' | 'shutdown' | 'restart'
type Progress = {
  phase: Phase | 'failed'
  /** This platform's steps, in order. Windows has no unpack: its installer does that. */
  steps: Phase[]
  /** The version being installed, and the one running now ("2.0.beta-5"). */
  version: string
  current: string
  received?: number
  total?: number
  failedAt?: Phase
  error?: string
  downloadUrl?: string
}

type DesktopUpdates = {
  state: () => Promise<{ progress?: Progress | null } | null>
  install: () => Promise<{ ok: boolean; reason?: string }>
  cancel?: () => Promise<void>
  dismiss?: () => Promise<void>
  justUpdated?: () => Promise<{ from: string; to: string } | null>
  onProgress?: (fn: (p: Progress | null) => void) => () => void
}
const desktopUpdates = () => (window as any).olisar?.updates as DesktopUpdates | undefined

const LABELS: Record<Phase, (p: Progress) => string> = {
  download: (p) => `Download v${p.version}`,
  unpack: () => 'Unpack',
  shutdown: () => 'Shut down',
  restart: () => 'Restart',
}
// What the live region says as each step starts.
const ANNOUNCE: Record<Phase, string> = {
  download: 'Downloading', unpack: 'Unpacking', shutdown: 'Shutting down', restart: 'Restarting',
}

// The form, by step. Sweeping while the new version comes in, as the server panel's does
// while the VM updates; quieter as the bots stop; drawn in and dim as it closes; the
// server panel's unhealthy tremor when it fails.
type Orb = { shape: number; v?: number; energy: number; mood: Mood }
const ORB: Record<Progress['phase'], Orb> = {
  download: { shape: SHAPE.updating, energy: 0.5, mood: { dim: 0.95 } },
  unpack: { shape: SHAPE.updating, energy: 0.8, mood: { dim: 1 } },
  shutdown: { shape: SHAPE.updating, energy: 0, mood: { dim: 0.75 } },
  restart: { shape: SHAPE.whole, v: 0.7, energy: -0.8, mood: { dim: 0.5 } },
  failed: { shape: SHAPE.whole, v: 1, energy: 1.2, mood: { tremor: 0.3, dim: 0.9 } },
}

const MB = 1024 * 1024
const mb = (n: number) => { const x = n / MB; return x >= 10 ? String(Math.round(x)) : x.toFixed(1) }

export function UpdateScreen() {
  const du = desktopUpdates()
  const [p, setP] = useState<Progress | null>(null)

  useEffect(() => {
    if (!du) return
    // Subscribe before asking, so a step that starts between the two isn't missed: the answer
    // reflects every event sent before it, and anything later arrives after it.
    const off = du.onProgress?.(setP)
    du.state().then((s) => { if (s && s.progress !== undefined) setP(s.progress) }).catch(() => {})
    // The window closed on the old version and opened on this one: say it worked.
    du.justUpdated?.().then((r) => {
      if (r && isNewer(r.to, r.from)) toast(`Updated to v${r.to}`, 'success')
    }).catch(() => {})
    return off
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!du || !p) return null
  return <Screen p={p} du={du} />
}

function Screen({ p, du }: { p: Progress; du: DesktopUpdates }) {
  const root = useRef<HTMLDivElement>(null)
  const title = useRef<HTMLHeadingElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const failed = p.phase === 'failed'
  const steps = p.steps?.length ? p.steps : (['download', 'unpack', 'shutdown', 'restart'] as Phase[])
  const at = failed ? (p.failedAt || 'download') : p.phase
  const atIndex = Math.max(0, steps.indexOf(at as Phase))

  // Everything else on the page goes inert: the console, and whatever is portalled beside it
  // (an open Settings, toasts, a dialog that opens later). Put back as it was on the way out.
  useLayoutEffect(() => {
    const own = root.current
    if (!own) return
    const prior = new Map<HTMLElement, boolean>()
    const hold = (n: Node) => {
      if (n === own || !(n instanceof HTMLElement) || prior.has(n)) return
      prior.set(n, n.inert)
      n.inert = true
    }
    Array.from(document.body.children).forEach(hold)
    const mo = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(hold)))
    mo.observe(document.body, { childList: true })
    return () => {
      mo.disconnect()
      prior.forEach((was, n) => { n.inert = was })
    }
  }, [])

  // The console's shortcuts (the palette, a dialog's Escape and its focus trap) listen on the
  // document and would still fire under an inert page. Held here, ahead of all of them. A
  // button's Enter and Space are its own default action and aren't stopped by this. Tab
  // wraps within the screen, as it does in a dialog.
  const back = () => { void du.dismiss?.() }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      e.stopPropagation()
      if (e.key === 'Escape' && failed) back()
      if (e.key !== 'Tab' || !root.current) return
      const items = Array.from(root.current.querySelectorAll<HTMLElement>('button:not([disabled])'))
      const first = items[0], last = items[items.length - 1]
      const at = document.activeElement
      if (!first) { e.preventDefault(); title.current?.focus() }
      else if (e.shiftKey && (at === first || at === title.current)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [failed])  // eslint-disable-line react-hooks/exhaustive-deps

  // Focus lands on the title, so it's read first, and again when it changes to the failure.
  // It goes back to wherever it was (the Install button, most often) when the screen does.
  useLayoutEffect(() => {
    const was = document.activeElement as HTMLElement | null
    return () => { restoreFocus(was) }
  }, [])
  useEffect(() => { title.current?.focus() }, [failed])

  // The form: decoration, so without WebGL2 the steps take the window.
  const [form, setForm] = useState<Form | null>(null)
  const [noForm, setNoForm] = useState(false)
  useEffect(() => {
    const c = canvas.current
    if (!c) return
    const f = createForm(c)
    if (!f) { setNoForm(true); return }
    f.start()
    setForm(f)
    return () => { f.destroy(); setForm(null) }
  }, [])
  useEffect(() => {
    if (!form) return
    const o = ORB[p.phase]
    form.set(o.shape, o.v)
    form.energy(o.energy)
    form.mood(o.mood)
    if (failed) form.reject()
  }, [form, p.phase])  // eslint-disable-line react-hooks/exhaustive-deps

  // Unsaved edits don't survive the restart. Said while there's still a way back to save them.
  const unsaved = p.phase === 'download' && (hasUnsavedChanges() || hasDraft())

  const received = p.received || 0
  const total = p.total || 0
  const downloadDetail = (i: number) => {
    if (i < atIndex) return total ? `${mb(total)} MB` : null
    if (i > atIndex || !received) return null
    return total ? `${mb(received)} of ${mb(total)} MB` : `${mb(received)} MB`
  }

  return createPortal(
    <div ref={root} className="upd" role="dialog" aria-modal="true" aria-labelledby="upd-title">
      <div className={'onb' + (noForm ? ' no-form' : '')}>
        <div className="onb-rail">
          <img className="brand-logo" src="/logo.png" alt="" />
        </div>
        <div className="onb-pane">
          <div className="onb-body">
            <h1 id="upd-title" ref={title} tabIndex={-1}>{failed ? 'Update failed' : 'Updating Olisar'}</h1>
            <p className="step-sub">
              {failed ? `You're still on v${p.current}.` : 'Olisar will close and reopen on its own.'}
            </p>
            <span className="visually-hidden" aria-live="polite">
              {failed ? '' : ANNOUNCE[p.phase as Phase]}
            </span>

            <ol className="upd-steps">
              {steps.map((s, i) => {
                const state = i < atIndex ? 'done' : i > atIndex ? 'pending' : failed ? 'failed' : 'active'
                const detail = s === 'download' ? downloadDetail(i) : null
                return (
                  <li key={s} className={'upd-step ' + state} aria-current={state === 'active' ? 'step' : undefined}>
                    <span className="upd-mark" aria-hidden="true">
                      {state === 'done' && <CheckMark size={14} />}
                      {state === 'failed' && <CloseX size={12} />}
                    </span>
                    <span className="upd-label">{LABELS[s](p)}</span>
                    {detail && <span className="upd-detail">{detail}</span>}
                    {s === 'download' && state === 'active' && (
                      total ? (
                        <div className="progress" role="progressbar" aria-label="Download"
                          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((received / total) * 100)}>
                          <div className="progress-fill" style={{ transform: `scaleX(${Math.min(1, received / total)})` }} />
                        </div>
                      ) : (
                        <div className="progress indeterminate" role="progressbar" aria-label="Download">
                          <div className="progress-bar" />
                        </div>
                      )
                    )}
                    {state === 'failed' && p.error && <p className="upd-err">{p.error}</p>}
                  </li>
                )
              })}
            </ol>

            {unsaved && (
              <div className="callout warning upd-note wiz-appear">
                <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                <div className="callout-body">Unsaved changes will be lost when Olisar restarts. Cancel to go back and save them.</div>
              </div>
            )}

            {p.phase === 'download' && (
              <div className="onb-actions">
                <button onClick={() => { void du.cancel?.() }}>Cancel</button>
              </div>
            )}
            {failed && (
              <div className="onb-actions">
                <button className="primary" onClick={() => { void du.install() }}>Try again</button>
                {p.downloadUrl && (
                  <button onClick={() => window.open(p.downloadUrl, '_blank', 'noopener')}>Download installer</button>
                )}
                <button className="ghost" onClick={back}>Back to Olisar</button>
              </div>
            )}
          </div>
        </div>
        <div className="onb-stage" aria-hidden="true" />
        <div className="orb-layer" aria-hidden="true"><canvas ref={canvas} /></div>
      </div>
    </div>,
    document.body,
  )
}
