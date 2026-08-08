// Imperative overlay primitives: a bottom-right Toast stack and a centered
// ConfirmDialog, both mounted once via <Overlays/> in main.tsx and driven from
// anywhere by the exported toast() / confirmDialog() / promptDialog() helpers.
// These replace the native alert/confirm/prompt, which break the calm aesthetic.

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon, CloseX, type IconName } from './icons'
import { uiScale } from './theme'

// ── Toast ────────────────────────────────────────────────────────────────────
type Tone = 'success' | 'danger' | 'warning' | 'info' | 'neutral'
type ToastItem = { id: number; message: string; tone: Tone }

let toastPush: ((t: ToastItem) => void) | null = null
let nextId = 1
const pending: ToastItem[] = []  // calls made before the host mounts are queued

const TOAST_ICON: Record<Tone, IconName> = {
  success: 'check', danger: 'warn', warning: 'warn', info: 'info', neutral: 'info',
}

export function toast(message: string, tone: Tone = 'neutral') {
  const item: ToastItem = { id: nextId++, message, tone }
  if (toastPush) toastPush(item)
  else pending.push(item)
}

// Success is a confirmation and can expire. A failure is information the operator may need
// to act on or quote, and putting it on a 3.6s timer meant "Publish failed: <reason>" and
// "Couldn't power down the bot" removed themselves before they could be read twice — with
// no history anywhere. Errors and warnings now wait to be dismissed, and are selectable.
const STICKY: Record<Tone, boolean> = {
  success: false, neutral: false, info: false, danger: true, warning: true,
}

function ToastView({ item, onDone }: { item: ToastItem; onDone: (id: number) => void }) {
  const [show, setShow] = useState(false)
  const sticky = STICKY[item.tone]
  useEffect(() => {
    const a = requestAnimationFrame(() => setShow(true))
    if (sticky) return () => cancelAnimationFrame(a)
    const hide = setTimeout(() => setShow(false), 3600)
    const done = setTimeout(() => onDone(item.id), 3920)
    return () => { cancelAnimationFrame(a); clearTimeout(hide); clearTimeout(done) }
  }, [item.id, onDone, sticky])
  const dismiss = () => { setShow(false); setTimeout(() => onDone(item.id), 320) }
  const Glyph = Icon[TOAST_ICON[item.tone]]
  return (
    // alert, not status: a failure should interrupt rather than queue behind whatever is
    // currently being read.
    <div className={'toast ' + item.tone + (show ? ' show' : '') + (sticky ? ' sticky' : '')}
      role={sticky ? 'alert' : 'status'}>
      <span className="ic"><Glyph size={20} weight="Bold" /></span>
      <span className="toast-msg">{item.message}</span>
      {sticky && (
        <button className="ghost icon-btn sm toast-x" onClick={dismiss}
          data-tip="Dismiss" aria-label="Dismiss">
          <CloseX size={14} />
        </button>
      )}
    </div>
  )
}

function ToastStack() {
  const [items, setItems] = useState<ToastItem[]>([])
  useEffect(() => {
    toastPush = (t) => setItems((xs) => [...xs, t])
    if (pending.length) { setItems((xs) => [...xs, ...pending]); pending.length = 0 }
    return () => { toastPush = null }
  }, [])
  const remove = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), [])
  if (!items.length) return null
  return <div className="toast-stack">{items.map((t) => <ToastView key={t.id} item={t} onDone={remove} />)}</div>
}

// ── Modal shell ──────────────────────────────────────────────────────────────
// Every overlay in the console goes through this. Hand-rolled backdrops drifted: some
// closed on Escape and some didn't, none announced as a dialog, and focus stayed on the
// trigger behind the overlay with the whole page still tabbable underneath.
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

// Dialogs nest (Settings ▸ Bot ▸ Move bot), so the inert flag is refcounted.
let openModals = 0

export function Modal(props: {
  /** Class on the dialog card itself — `.settings-modal`, `.import-modal`, `.confirm-dialog`, … */
  className: string
  /** id of the element that titles this dialog (its <h2> / .confirm-title). */
  labelledBy?: string
  label?: string
  onClose?: () => void
  /** Set false while an irreversible action is in flight, so a stray Escape can't abandon it. */
  dismissable?: boolean
  children: React.ReactNode
}) {
  const card = useRef<HTMLDivElement>(null)
  const dismissable = props.dismissable !== false
  const close = props.onClose

  useEffect(() => {
    const returnTo = document.activeElement as HTMLElement | null
    const el = card.current
    // Don't fight an autoFocus'd input — React has already focused it by now.
    if (el && !el.contains(document.activeElement)) {
      (el.querySelector<HTMLElement>(FOCUSABLE) ?? el).focus()
    }
    // aria-modal alone is a promise, not a mechanism: the page behind stayed in the
    // accessibility tree and the skip link stayed focusable. `inert` is the mechanism.
    // Counted, because dialogs nest (Settings ▸ Bot ▸ Move bot).
    const app = document.getElementById('root')
    if (app) {
      openModals += 1
      app.inert = true
    }
    return () => {
      if (app && --openModals <= 0) { openModals = 0; app.inert = false }
      if (returnTo?.isConnected) returnTo.focus()
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dismissable) { e.stopPropagation(); close?.(); return }
      if (e.key !== 'Tab') return
      const el = card.current
      if (!el) return
      const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null || n === document.activeElement)
      if (!items.length) { e.preventDefault(); el.focus(); return }
      const first = items[0]
      const last = items[items.length - 1]
      // Wrap at both ends, and pull focus back in if it escaped to the page behind.
      if (!el.contains(document.activeElement)) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [dismissable, close])

  // Portalled to <body>, outside #root — otherwise marking the app inert above would
  // take the dialog with it. React context still flows through a portal, so callers
  // are unaffected, and the backdrop was already fixed-positioned.
  return createPortal(
    <div
      className="modal-backdrop"
      // mousedown, not click: a text selection that starts inside the card and releases on
      // the backdrop fires a click on this element and used to close the dialog mid-drag.
      onMouseDown={(e) => { if (e.target === e.currentTarget && dismissable) close?.() }}
    >
      <div
        ref={card}
        className={props.className}
        role="dialog"
        aria-modal="true"
        aria-labelledby={props.labelledBy}
        aria-label={props.labelledBy ? undefined : props.label}
        tabIndex={-1}
      >
        {props.children}
      </div>
    </div>,
    document.body,
  )
}

// ── Confirm / prompt dialog ──────────────────────────────────────────────────
type DialogTone = 'default' | 'danger' | 'warning'
type DialogOpts = {
  title: string
  message?: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: DialogTone
  icon?: IconName
  prompt?: { placeholder?: string; defaultValue?: string; multiline?: boolean }
  // High-friction confirm: show the phrase in a CopyField and only arm the confirm
  // button once the user types it back exactly (case/whitespace-insensitive).
  requirePhrase?: { phrase: string; placeholder?: string }
}

let dialogShow: ((o: DialogOpts, resolve: (v: boolean | string | null) => void) => void) | null = null

export function confirmDialog(opts: DialogOpts): Promise<boolean> {
  return new Promise((resolve) => {
    if (dialogShow) dialogShow(opts, (v) => resolve(v === true))
    else resolve(false)
  })
}

export function promptDialog(
  opts: DialogOpts & { prompt: NonNullable<DialogOpts['prompt']> },
): Promise<string | null> {
  return new Promise((resolve) => {
    if (dialogShow) dialogShow(opts, (v) => resolve(typeof v === 'string' ? v : null))
    else resolve(null)
  })
}

// CopyField — a value in an inset box with a trailing copy button that flips to a
// green check on click (DESIGN.md). Used by the requirePhrase confirm friction.
function CopyField({ value }: { value: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1400) }
    catch { /* clipboard blocked — the phrase is still selectable */ }
  }
  return (
    <span className="copy">
      <span className="val">{value}</span>
      <button type="button" className={'btn' + (done ? ' done' : '')} onClick={copy}
        data-tip={done ? 'Copied' : 'Copy'} aria-label="Copy phrase">
        {done ? <Icon.check size={15} weight="Bold" /> : <Icon.copy size={15} />}
      </button>
    </span>
  )
}

function ConfirmHost() {
  const [state, setState] = useState<{ opts: DialogOpts; resolve: (v: boolean | string | null) => void } | null>(null)
  const [value, setValue] = useState('')
  const titleId = React.useId()

  useEffect(() => {
    dialogShow = (opts, resolve) => { setValue(opts.prompt?.defaultValue ?? ''); setState({ opts, resolve }) }
    return () => { dialogShow = null }
  }, [])

  if (!state) return null
  const { opts, resolve } = state
  const close = (result: boolean | string | null) => { setState(null); resolve(result) }
  const phrase = opts.requirePhrase?.phrase
  const phraseOK = !phrase || value.trim().toLowerCase().replace(/\s+/g, ' ') === phrase.trim().toLowerCase()
  const onConfirm = () => { if (!phraseOK) return; close(opts.prompt ? value : true) }
  const onCancel = () => close(opts.prompt ? null : false)
  const toneClass = opts.tone === 'danger' ? 'danger' : opts.tone === 'warning' ? 'warning' : ''
  const Glyph = Icon[opts.icon ?? (opts.tone === 'danger' ? 'warn' : opts.tone === 'warning' ? 'warn' : 'info')]
  const inputLabel = phrase ? 'Type the confirmation phrase' : opts.prompt?.placeholder || opts.title

  return (
    <Modal className="confirm-dialog" labelledBy={titleId} onClose={onCancel}>
      <div className="confirm-head">
        <div className={'confirm-icon ' + toneClass}><Glyph size={22} weight="Bold" aria-hidden /></div>
        <div className="confirm-text">
          <div className="confirm-title" id={titleId}>{opts.title}</div>
          {opts.message && <div className="confirm-msg">{opts.message}</div>}
        </div>
      </div>
      {phrase && (
        <>
          <div className="confirm-phrase"><span>Type</span> <CopyField value={phrase} /> <span>to confirm.</span></div>
          <div className="confirm-input">
            <input type="text" autoFocus value={value} autoComplete="off" spellCheck={false} aria-label={inputLabel}
              placeholder={opts.requirePhrase?.placeholder ?? 'Type the phrase to confirm'}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') onConfirm() }} />
          </div>
        </>
      )}
      {opts.prompt && (
        <div className="confirm-input">
          {opts.prompt.multiline ? (
            <textarea autoFocus value={value} placeholder={opts.prompt.placeholder} aria-label={inputLabel}
              onChange={(e) => setValue(e.target.value)} />
          ) : (
            <input type="text" autoFocus value={value} placeholder={opts.prompt.placeholder} aria-label={inputLabel}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') onConfirm() }} />
          )}
        </div>
      )}
      <div className="confirm-foot">
        <button className="ghost" onClick={onCancel}>{opts.cancelLabel ?? 'Cancel'}</button>
        <button className={opts.tone === 'danger' ? 'danger' : 'primary'} onClick={onConfirm} disabled={!phraseOK}>
          {opts.confirmLabel ?? 'Confirm'}
        </button>
      </div>
    </Modal>
  )
}

// ── Tooltip ──────────────────────────────────────────────────────────────────
// One delegated, portal-rendered tooltip for any element carrying data-tip="…"
// (or a native title, which is migrated to data-tip so the OS tooltip never shows).
// Fixed-positioned so it's never clipped by an overflow:hidden modal; flips below
// the target when there's no room above. Monaco owns its own hovers, so it's skipped.
function TooltipHost() {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; below: boolean } | null>(null)
  useEffect(() => {
    let current: Element | null = null
    const hide = () => { current = null; setTip(null) }
    const textOf = (el: Element): string | null => {
      let t = el.getAttribute('data-tip')
      if (!t && el.hasAttribute('title')) {
        t = el.getAttribute('title')
        el.setAttribute('data-tip', t || '')
        // Stripping `title` suppresses the native tooltip — but on a control whose ONLY name
        // was that title, it also deletes the accessible name, and this runs on focusin, so
        // merely tabbing to the control silenced it. Carry the name over first. Only when the
        // element has no name of its own: an <a title={url}>host</a> keeps its link text.
        if (t && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')
            && !(el.textContent || '').trim()) {
          el.setAttribute('aria-label', t)
        }
        el.removeAttribute('title')
      }
      return t || null
    }
    // Pops up instantly on hover (no delay), per the design system.
    const show = (el: Element) => {
      if (el.closest('.monaco-editor')) return
      const text = textOf(el)
      if (!text) return
      current = el
      const r = el.getBoundingClientRect()
      const below = r.top < 52
      // getBoundingClientRect is in viewport px; `left`/`top` below are read back in the
      // zoomed coordinate space, so divide the scale out or the tip lands scale-1 × its
      // distance from the origin away from its target (104px at 1.1, near the right edge).
      const k = uiScale()
      setTip({
        text,
        x: Math.round((r.left + r.width / 2) / k),
        y: Math.round((below ? r.bottom + 8 : r.top - 8) / k),
        below,
      })
    }
    const onOver = (e: Event) => {
      const el = (e.target as Element)?.closest?.('[data-tip],[title]')
      if (el && el !== current) show(el)
    }
    const onOut = (e: MouseEvent) => {
      const el = (e.target as Element)?.closest?.('[data-tip],[title]')
      if (el && el === current) {
        const to = e.relatedTarget as Node | null
        if (to && el.contains(to)) return   // moved onto a child — keep showing
        hide()
      }
    }
    const onFocus = (e: Event) => { const el = (e.target as Element)?.closest?.('[data-tip],[title]'); if (el) show(el) }
    document.addEventListener('mouseover', onOver, true)
    document.addEventListener('mouseout', onOut as EventListener, true)
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', hide)
    document.addEventListener('mousedown', hide, true)
    window.addEventListener('scroll', hide, true)
    return () => {
      document.removeEventListener('mouseover', onOver, true)
      document.removeEventListener('mouseout', onOut as EventListener, true)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', hide)
      document.removeEventListener('mousedown', hide, true)
      window.removeEventListener('scroll', hide, true)
    }
  }, [])
  if (!tip) return null
  return (
    <div className={'tooltip' + (tip.below ? ' below' : '')} style={{ left: tip.x, top: tip.y }} role="tooltip">
      {tip.text}
    </div>
  )
}

// Mounted once, near the app root.
export function Overlays() {
  return <><ToastStack /><ConfirmHost /><TooltipHost /></>
}
