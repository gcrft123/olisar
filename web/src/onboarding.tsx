import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createForm, type Form } from './form'
import { Icon } from './icons'
import { fromToasts, restoreFocus } from './overlays'
import { Docs } from './pages'
import { SettingsModal, useFeedbackHost, type SectionId } from './settings'
import type { FeedbackPrefill } from './feedback'

// ── The first-run shell ─────────────────────────────────────────────────────────────
// Setup, the server panel and sign-in share one frame: a thin rail on the far left (the logo,
// settings, the docs), the screen itself in the left half, and on the right a faded 3D form,
// set partly off the window's edge, that changes with what the screen is doing (form.ts). The
// frame stays mounted from one of these screens to the next, so the form carries on across a
// finished setup instead of starting over. See DESIGN.md, "First-run screens".

type Shell = {
  /** The form, once WebGL2 has started it; null without it (the screen takes the window). */
  form: Form | null
  /** The shell's own element, which brain.ts finds its pieces in. */
  root: HTMLElement | null
  /** Where the final server screen's corner and memories render (outside the scrolling half). */
  over: HTMLElement | null
  openSettings: (section?: SectionId, prefill?: FeedbackPrefill) => void
  openDocs: (section: string) => void
  closeDocs: () => void
}

const Ctx = createContext<Shell>({ form: null, root: null, over: null, openSettings: () => {}, openDocs: () => {}, closeDocs: () => {} })
export const useShell = () => useContext(Ctx)
export const useForm = () => useContext(Ctx).form

// A screen that arrives from another one in this frame (setup handing over to the server
// panel or sign-in) slides in as the next screen rather than just appearing. Set by the one
// leaving, read once by the one arriving.
let arriving = false
export const handOff = () => { arriving = true }
export function useArrived(): boolean {
  // Cleared once mounted rather than while reading it: StrictMode renders a mount twice.
  const [was] = useState(() => arriving)
  useEffect(() => { arriving = false }, [])
  return was
}

export function Onboarding({ sections, children }: { sections: SectionId[]; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null)
  const over = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [noForm, setNoForm] = useState(false)
  const [mounted, setMounted] = useState<{ root: HTMLElement; over: HTMLElement } | null>(null)
  useLayoutEffect(() => { if (root.current && over.current) setMounted({ root: root.current, over: over.current }) }, [])

  // The form is decoration: without WebGL2 the screen takes the whole window.
  useEffect(() => {
    const c = canvas.current
    if (!c) return
    const f = createForm(c)
    if (!f) { setNoForm(true); return }
    f.start()
    setForm(f)
    return () => { f.destroy(); setForm(null) }
  }, [])

  const [settings, setSettings] = useState<{ section?: SectionId; prefill?: FeedbackPrefill } | null>(null)
  const openSettings = (section?: SectionId, prefill?: FeedbackPrefill) => setSettings({ section, prefill })
  // Feedback opened from a failure arrives filled in, in this frame's Settings.
  useFeedbackHost((prefill) => setSettings({ section: 'feedback', prefill }))

  const [docs, setDocs] = useState<string | null>(null)   // the section showing, or null when closed
  // Closing the drawer with focus inside it (Escape, or the final screen taking over) would
  // drop focus to the page: it goes back to the button that opens the docs.
  const closeDocs = () => {
    const drawer = document.getElementById('onb-docs')
    if (drawer?.contains(document.activeElement)) restoreFocus(root.current?.querySelector<HTMLElement>('[aria-controls="onb-docs"]') ?? null)
    setDocs(null)
  }
  const shell: Shell = {
    form, root: mounted?.root ?? null, over: mounted?.over ?? null, openSettings, openDocs: (s) => setDocs(s), closeDocs,
  }

  return (
    <Ctx.Provider value={shell}>
      <div className={'onb' + (noForm ? ' no-form' : '')} ref={root}>
        {/* Past the rail. Moves focus rather than navigating, as the console's does: the
            address is read as a route. */}
        <a
          className="skip-link"
          href="#onb-main"
          onClick={(e) => { e.preventDefault(); document.getElementById('onb-main')?.focus() }}
        >
          Skip to content
        </a>
        {/* The rail's button reopens the docs where they were left; a link names a page. */}
        <Rail docsOpen={!!docs} onDocs={() => (docs ? closeDocs() : setDocs('where-left'))} onSettings={() => openSettings()} />
        <DocsDrawer section={docs} onClose={closeDocs} />
        {children}
        <div className="onb-stage" aria-hidden="true" />
        <div className="orb-layer" aria-hidden="true"><canvas ref={canvas} /></div>
        <div className="onb-over" ref={over} />
      </div>
      {settings && (
        <SettingsModal
          sections={sections}
          initialSection={settings.section}
          prefill={settings.prefill}
          onClose={() => setSettings(null)}
        />
      )}
    </Ctx.Provider>
  )
}

/** The screen's half: it scrolls as one page when a step runs long, and a new screen or step
 *  starts at its top. */
export function Pane({ children, resetKey }: { children: ReactNode; resetKey?: string }) {
  const scroller = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => { if (scroller.current) scroller.current.scrollTop = 0 }, [resetKey])
  return (
    <div className="onb-pane" ref={scroller}>
      <main className="onb-body" id="onb-main" tabIndex={-1}>{children}</main>
    </div>
  )
}

// ── The rail: the logo, settings, and the docs ────────────────────────────────
function Rail({ docsOpen, onDocs, onSettings }: { docsOpen: boolean; onDocs: () => void; onSettings: () => void }) {
  return (
    <nav className="onb-rail" aria-label="Olisar">
      <img className="brand-logo" data-morph="logo" src="/logo.png" alt="Olisar" />
      <button className="ghost icon-btn" data-tip="Settings" data-tip-side="right" aria-label="Settings" onClick={onSettings}>
        <Icon.settings size={18} />
      </button>
      <button
        className={'ghost icon-btn' + (docsOpen ? ' on' : '')}
        data-tip={docsOpen ? 'Close docs' : 'Docs'}
        data-tip-side="right"
        aria-label={docsOpen ? 'Close the docs' : 'Open the docs'}
        aria-expanded={docsOpen}
        aria-controls="onb-docs"
        onClick={onDocs}
      >
        {/* The docs glyph and the chevron that closes them share one cell and cross-fade, the
            way the copy glyph does, so the button never reflows. */}
        <span className={'swapglyph' + (docsOpen ? ' on' : '')}>
          <Icon.docs size={18} aria-hidden />
          <Icon.arrowLeft size={18} aria-hidden />
        </span>
      </button>
    </nav>
  )
}

// The docs, sliding out from behind the rail over the screen: the console's own Docs page,
// with its section nav and search. Mounted the first time it opens; closed, it stays mounted
// (so it slides rather than pops) and inert, so nothing in it can be tabbed to. A section id
// opens it at that page. Escape closes it, unless a dialog above it takes the key.
function DocsDrawer({ section, onClose }: { section: string | null; onClose: () => void }) {
  const open = !!section
  const [ever, setEver] = useState(false)
  const el = useRef<HTMLElement>(null)
  useEffect(() => { if (open) setEver(true) }, [open])
  useInert(el, !open)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !fromToasts(e) && !document.querySelector('[aria-modal="true"]')) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])
  return (
    <aside id="onb-docs" ref={el} className={'docs-panel' + (open ? ' open' : '')} aria-label="Docs">
      {ever && <Docs embedded start={section || undefined} />}
    </aside>
  )
}

/** Sets `inert` on an element (React 18 has no prop for it). */
export function useInert(ref: { current: HTMLElement | null }, on: boolean) {
  useLayoutEffect(() => { if (ref.current) ref.current.inert = on })
}

// ── The form, told what the screen is doing ─────────────────────────────────────
// A wrong value shakes its field, the way a refused password does, and the form shivers once:
// the counterpart of the pulse a confirmation sends through it. Under reduced motion the field
// stays still and its fill flashes the danger colour instead.
const SHAKE = [0, -7, 6, -4.5, 3, -1.5, 0].map((x) => ({ transform: `translateX(${x}px)` }))
export function shake(form: Form | null, field: string | null) {
  if (!field) return
  const el = document.querySelector<HTMLElement>(`[data-field="${field}"]`)
  if (!el) return
  form?.reject()
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.getAnimations().forEach((a) => a.cancel())
  if (still) {
    const s = getComputedStyle(document.documentElement)
    const flash = `color-mix(in srgb, ${s.getPropertyValue('--danger').trim()} 20%, ${s.getPropertyValue('--input-bg').trim()})`
    el.animate([{ backgroundColor: flash }, { backgroundColor: s.getPropertyValue('--input-bg').trim() }], { duration: 700, easing: 'ease-out' })
  } else {
    el.animate(SHAKE, { duration: 420, easing: 'linear' })
  }
}

/** Sends a pulse through the form each time `cond` turns true. */
export function usePulseOn(form: Form | null, cond: boolean, amount: number) {
  const was = useRef(cond)
  useEffect(() => {
    if (cond && !was.current) form?.pulse(amount)
    was.current = cond
  }, [cond])  // eslint-disable-line react-hooks/exhaustive-deps
}
