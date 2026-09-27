// Imperative overlay primitives: a top-right Toast stack and a centered
// ConfirmDialog, both mounted once via <Overlays/> in main.tsx and driven from
// anywhere by the exported toast() / confirmDialog() / promptDialog() helpers.
// These replace the native alert/confirm/prompt, which break the calm aesthetic.

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Toast, type ToastManagerAddOptions, type ToastObject } from '@base-ui/react/toast'
import { Icon, CloseX, type IconName } from './icons'
import { rectScale, uiScale } from './theme'

// ── Toast ────────────────────────────────────────────────────────────────────
// Base UI's Toast owns the behaviour: the newest three stack in the corner with the older
// ones peeking out behind, hovering or focusing the stack fans it out and pauses every
// timer (so does the window losing focus), a toast swipes away up or right, Escape closes
// the focused one and F6 jumps into the stack from anywhere. This file keeps the
// imperative toast() on top of it, and the tone rules below.
export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'neutral'
type ToastAction = { label: string; onClick: () => void }
type ToastData = { busy?: boolean; action?: ToastAction; sticky: boolean }
type ToastItem = ToastObject<ToastData>
type ToastAdd = ToastManagerAddOptions<ToastData>

export type ToastOpts = {
  /** Override the tone's default expiry (see STICKY) — for work that outlives a timer. */
  sticky?: boolean
  /** Spinner in place of the tone glyph: this toast reports progress, not an outcome. */
  busy?: boolean
  /** Trailing ghost button. Firing it also dismisses the toast. */
  action?: ToastAction
  /** Auto-dismiss delay; ignored when sticky. */
  durationMs?: number
}
/** Handle for a toast the caller has to take back down itself (progress toasts). */
export type ToastControl = { dismiss: () => void }

let toastAdd: ((t: ToastAdd) => void) | null = null
let toastClose: ((id: string) => void) | null = null
let nextId = 1
const pending: ToastAdd[] = []  // calls made before the host mounts are queued

const TOAST_ICON: Record<Tone, IconName> = {
  success: 'check', danger: 'warn', warning: 'warn', info: 'info', neutral: 'info',
}

// Success is a confirmation and can expire. A failure is information the operator may need
// to act on or quote, and putting it on a 3.6s timer meant "Publish failed: <reason>" and
// "Couldn't power down the bot" removed themselves before they could be read twice — with
// no history anywhere. Errors and warnings now wait to be dismissed, and are selectable.
const STICKY: Record<Tone, boolean> = {
  success: false, neutral: false, info: false, danger: true, warning: true,
}

export function toast(message: string, tone: Tone = 'neutral', opts?: ToastOpts): ToastControl {
  const id = `toast-${nextId++}`
  // Tone sets the default; a caller can still pin a toast open for work that outlives a timer.
  const sticky = opts?.sticky ?? STICKY[tone]
  const item: ToastAdd = {
    id, description: message, type: tone,
    // 0 never expires. Leaving it undefined takes the provider's default.
    timeout: sticky ? 0 : opts?.durationMs,
    // High priority is announced as an alert, the rest politely. Keyed off the tone default,
    // so a progress toast (pinned open but not an error) doesn't interrupt.
    priority: STICKY[tone] ? 'high' : 'low',
    data: { busy: opts?.busy, action: opts?.action, sticky },
  }
  if (toastAdd) toastAdd(item)
  else pending.push(item)
  return {
    dismiss: () => {
      if (toastClose) { toastClose(id); return }
      // Dismissed before the host mounted. Drop it from the queue instead — otherwise a
      // sticky progress toast whose work already finished appears after the fact, with
      // nothing left to take it down again.
      const i = pending.findIndex((p) => p.id === id)
      if (i >= 0) pending.splice(i, 1)
    },
  }
}

function ToastView({ item, close }: { item: ToastItem; close: (id: string) => void }) {
  const tone = item.type as Tone
  const { busy, action, sticky } = item.data!
  const Glyph = Icon[TOAST_ICON[tone]]
  const selectable = sticky && !busy
  return (
    // Swipes go toward the nearest edges. The other two directions still drag, damped, and
    // spring back.
    <Toast.Root toast={item} swipeDirection={['up', 'right']}
      className={'toast ' + tone + (sticky ? ' sticky' : '') + (busy ? ' busy' : '')}>
      <Toast.Content className="toast-content">
        <span className="ic">
          {busy ? <span className="spinner" aria-hidden /> : <Glyph size={20} weight="Bold" />}
        </span>
        {/* A drag across selectable text is a selection, not a swipe. */}
        <Toast.Description className="toast-msg"
          {...(selectable ? { 'data-base-ui-swipe-ignore': '' } : {})} />
        {/* An action toast gets no close ×: the action IS the way out, and dismissing the only
            handle on work still running would strand it. */}
        {action ? (
          <Toast.Action className="ghost toast-action"
            onClick={() => { action.onClick(); close(item.id) }}>
            {action.label}
          </Toast.Action>
        ) : sticky && (
          <Toast.Close className="ghost icon-btn sm toast-x" data-tip="Dismiss" aria-label="Dismiss">
            <CloseX size={14} />
          </Toast.Close>
        )}
      </Toast.Content>
    </Toast.Root>
  )
}

function ToastList() {
  const { toasts, add, close } = Toast.useToastManager<ToastData>()
  useEffect(() => {
    toastAdd = add
    toastClose = close
    pending.splice(0).forEach(add)
    return () => { toastAdd = null; toastClose = null }
  }, [add, close])
  return <>{toasts.map((t) => <ToastView key={t.id} item={t} close={close} />)}</>
}

/** Whether a key event happened in the toast stack. F6 moves focus there from anywhere, and
 *  keys pressed there are the stack's own: Escape closes the toast, Tab walks the toasts. A
 *  shell that traps Tab or closes on Escape (the Modal, the Test chat drawer, the narrow nav
 *  drawer) leaves these alone, or Escape on a toast closes the drawer under it too. */
export function fromToasts(e: Event): boolean {
  return !!(e.target as Element | null)?.closest?.('.toast-viewport')
}

function ToastStack() {
  // The portal lands on <body> for the same reason the modal card does: `Overlays` renders
  // inside #root, and an open dialog marks #root inert. A toast raised from inside Settings
  // (every "Couldn't rename the bot" / "Couldn't send" path) painted but could not be
  // clicked shut and was hidden from assistive tech until the dialog closed.
  return (
    <Toast.Provider>
      <Toast.Portal>
        <Toast.Viewport className="toast-viewport">
          <ToastList />
        </Toast.Viewport>
      </Toast.Portal>
    </Toast.Provider>
  )
}

// ── Modal shell ──────────────────────────────────────────────────────────────
// Every overlay in the console goes through this. Hand-rolled backdrops drifted: some
// closed on Escape and some didn't, none announced as a dialog, and focus stayed on the
// trigger behind the overlay with the whole page still tabbable underneath.
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

// Dialogs nest (Settings ▸ Bot ▸ Move bot), so the inert flag is refcounted.
let openModals = 0
// Mount order of the open modals. The last one is the top-most and owns Escape.
const escStack: symbol[] = []

/** Matches `.modal-backdrop.closing` / `.closing > *` in index.css. */
const EXIT_MS = 140

// Enter and Space click a button. Chrome and Safari do it on keydown for Enter and on
// keyup for Space; Firefox does both on keyup, and every browser sends the keyup to
// whatever is focused when the key comes up. A dialog that hands focus back to its
// trigger in the same turn gives that keyup to the trigger, so the button that opened
// the dialog clicks again and the dialog comes straight back.
const activatorDown = new Set<string>()
const isActivator = (key: string) => key === 'Enter' || key === ' '

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', (e) => { if (isActivator(e.key)) activatorDown.add(e.key) }, true)
  document.addEventListener('keyup', (e) => {
    if (!isActivator(e.key)) return
    // After this keyup's default action, so a click it dispatched still sees the key
    // as down. The focus return below then knows the click already happened.
    queueMicrotask(() => activatorDown.delete(e.key))
  }, true)
}

/** Focus `el` once an Enter or Space that is still down has come up, so that key's
 *  keyup can't click it. A pointer or Escape close has no such key and focuses now. */
export function restoreFocus(el: HTMLElement | null) {
  if (!el) return
  const go = () => { if (el.isConnected) el.focus() }
  const pending = [...activatorDown]
  if (!pending.length) { go(); return }

  let settled = false
  const stop = () => {
    document.removeEventListener('keyup', onUp, true)
    window.removeEventListener('blur', onBlur)
  }
  const settle = () => {
    if (settled) return
    settled = true
    stop()
    // After the keyup's default action, which is the click.
    queueMicrotask(go)
  }
  const onUp = (e: KeyboardEvent) => {
    const i = pending.indexOf(e.key)
    if (i < 0) return
    pending.splice(i, 1)
    if (pending.length) return
    e.preventDefault()
    settle()
  }
  // Alt-tab swallows the keyup. Don't leave focus on the page waiting for a key that
  // isn't coming back.
  const onBlur = () => { settle() }
  document.addEventListener('keyup', onUp, true)
  window.addEventListener('blur', onBlur)
  queueMicrotask(() => {
    if (settled) return
    // The click that closed the dialog was itself this key's keyup. Waiting for
    // another one would leave focus on the page.
    if (pending.every((k) => !activatorDown.has(k))) settle()
  })
}

// Overlays enter over .22s and used to leave on the frame they closed, because the caller
// owns the mounting (`{open && <Thing/>}`) and React can't hold an unmount open from
// inside the child. Rather than thread a `closing` flag through all eleven call sites —
// SettingsModal alone is rendered from five files — the shell hands its own corpse off on
// the way out: a frozen, inert copy of the backdrop plays the exit and removes itself.
//
// It's display-only, so the things a clone loses (React handlers, focus) are things an
// exiting dialog shouldn't have anyway — focus goes back to the trigger, not to this.
// Scroll offsets ARE copied: a tall Settings modal that snapped to the top for the last
// 140ms would be a worse artifact than no animation at all.
function playExit(back: HTMLDivElement | null) {
  // No isConnected check: React may already have detached the node by now, and a detached
  // node still clones and still reports the scrollTops we want. Layout comes from the
  // stylesheet once the clone is in the document, not from the original's box.
  if (!back || !back.firstElementChild) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

  const ghost = back.cloneNode(true) as HTMLDivElement
  const from = [back, ...back.querySelectorAll<HTMLElement>('*')]
  const to = [ghost, ...ghost.querySelectorAll<HTMLElement>('*')]
  for (let i = 0; i < from.length; i++) {
    if (from[i].scrollTop) to[i].scrollTop = from[i].scrollTop
    if (from[i].scrollLeft) to[i].scrollLeft = from[i].scrollLeft
  }

  ghost.classList.add('closing')
  ghost.setAttribute('aria-hidden', 'true')
  ghost.inert = true
  document.body.appendChild(ghost)
  setTimeout(() => ghost.remove(), EXIT_MS)
}

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
  const backdrop = useRef<HTMLDivElement>(null)
  const dismissable = props.dismissable !== false
  const close = props.onClose

  // Captured during RENDER, not in the effect below. React applies a child's `autoFocus`
  // while committing — before effects run — so reading activeElement in the effect returned
  // the dialog's own input on every modal that has one. It then unmounted with the dialog,
  // `isConnected` was false, and the restore silently did nothing: open the ⌘K palette,
  // press Escape, and focus was on <body>. Modals without an autoFocus child (Settings)
  // restored correctly, which is exactly why this hid.
  const returnTo = useRef<HTMLElement | null>(null)
  if (returnTo.current === null) returnTo.current = document.activeElement as HTMLElement | null

  useEffect(() => {
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
    // Read at mount, not in the cleanup: React detaches object refs before it runs effect
    // cleanups for a deleted tree, so `backdrop.current` is already null down there and the
    // exit silently did nothing.
    const leaving = backdrop.current
    return () => {
      if (app && --openModals <= 0) { openModals = 0; app.inert = false }
      // StrictMode's dev-only rehearsal runs this cleanup with the dialog still on screen and
      // mounts it straight back. Playing the exit then laid a fully opaque clone over the
      // entering dialog, so every modal opened on a frame of its final state and then two
      // copies crossing. A real unmount has detached the node by the time this settles.
      queueMicrotask(() => {
        if (leaving?.isConnected) return
        // Enter and Space click on keyup, and that keyup goes to whatever is focused
        // when the key comes up. Focusing the trigger here would click it, and the
        // dialog that just closed would open again.
        restoreFocus(returnTo.current)
        playExit(leaving)
      })
    }
  }, [])

  // Which modal owns Escape. Every instance listens on `document` in capture, and
  // stopPropagation does NOT stop other listeners on the same node — so one Escape inside
  // a nested dialog used to fire the parent's handler too: Settings ▸ Delete bot ▸ Escape
  // closed the confirm *and* Settings. A non-dismissable modal was worse, because it fell
  // straight through to the parent and unmounted the window that says "keep this open".
  // Only the top of the stack reacts, and it always consumes the key either way.
  const idRef = useRef<symbol>(null as unknown as symbol)
  if (idRef.current == null) idRef.current = Symbol('modal')
  useEffect(() => {
    escStack.push(idRef.current)
    return () => {
      const i = escStack.indexOf(idRef.current)
      if (i >= 0) escStack.splice(i, 1)
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // F6 moves focus into the toast stack, which lives outside the dialog. Keys pressed
      // there are the stack's own: Escape closes the toast, Tab walks the toasts. Trapping
      // them here closed the dialog underneath and yanked focus back out.
      if (fromToasts(e)) return
      if (e.key === 'Escape') {
        if (escStack[escStack.length - 1] !== idRef.current) return
        e.stopPropagation()
        if (dismissable) close?.()
        return
      }
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
      ref={backdrop}
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
  // High-friction confirm: show the phrase (not copyable) and only arm the confirm
  // button once the user types it back exactly (case/whitespace-insensitive).
  requirePhrase?: { phrase: string; placeholder?: string }
  /** A third button between cancel and confirm; resolves the dialog with 'extra'. */
  extraLabel?: string
}

let dialogShow: ((o: DialogOpts, resolve: (v: boolean | string | null) => void) => void) | null = null

// Resolves true (confirmed), false (cancelled), or 'extra' when the optional third action
// was taken — so a caller offering "Save and leave" can tell it apart from a plain confirm.
export function confirmDialog(opts: DialogOpts): Promise<boolean | 'extra'> {
  return new Promise((resolve) => {
    if (dialogShow) dialogShow(opts, (v) => resolve(v === 'extra' ? 'extra' : v === true))
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

function ConfirmHost() {
  const [state, setState] = useState<{ opts: DialogOpts; resolve: (v: boolean | string | null) => void } | null>(null)
  const [value, setValue] = useState('')
  const titleId = React.useId()

  // One host serves every dialog, so a second `confirmDialog()` opened while the first is
  // still up used to replace `state` wholesale — dropping the first `resolve` on the floor and
  // leaving its `await` pending forever. In `leaveGuard` that means the navigation promise
  // never settles and the guard is stuck. Two fast Back presses reach it. Settle the one
  // being displaced as *cancelled*, which is the safe answer to a question nobody answered:
  // it declines to proceed rather than discarding anything.
  const current = React.useRef<{ opts: DialogOpts; resolve: (v: boolean | string | null) => void } | null>(null)
  current.current = state
  useEffect(() => {
    dialogShow = (opts, resolve) => {
      const prev = current.current
      if (prev) prev.resolve(prev.opts.prompt ? null : false)
      setValue(opts.prompt?.defaultValue ?? '')
      setState({ opts, resolve })
    }
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
  // "warning" is destructive too — it is the tone the leave guard uses to ask about
  // discarding edits. Anything that loses work gets the destructive footer.
  const destructive = opts.tone === 'danger' || opts.tone === 'warning'
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
          {/* Shown, deliberately not copyable. `requirePhrase` exists to make the operator's
              own hand prove intent; a copy button reduced it to a two-click confirm, which
              is friction theatre. Retyping it is the entire mechanism. */}
          <div className="confirm-phrase"><span>Type</span> <code className="phrase">{phrase}</code> <span>to confirm.</span></div>
          <div className="confirm-input">
            <input type="text" autoFocus value={value} autoComplete="off" spellCheck={false} aria-label={inputLabel}
              placeholder={opts.requirePhrase?.placeholder ?? 'Type the phrase to confirm'}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onConfirm() } }} />
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
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onConfirm() } }} />
          )}
        </div>
      )}
      {/* On a destructive dialog the SAFE choice is the primary, and the confirm is the
          plainly-marked destructive one. The leave guard passes tone "warning" and used to
          render "Discard" as the bright primary — the dialog that exists to protect unsaved
          work was pointing at throwing it away. Only a neutral dialog gets a primary confirm. */}
      <div className={'confirm-foot' + (destructive ? ' destructive' : '')}>
        <button className={destructive ? 'primary' : 'ghost'} onClick={onCancel}>
          {opts.cancelLabel ?? 'Cancel'}
        </button>
        {opts.extraLabel && (
          <button onClick={() => close('extra')}>{opts.extraLabel}</button>
        )}
        <button className={destructive ? 'danger' : 'primary'} onClick={onConfirm} disabled={!phraseOK}>
          {opts.confirmLabel ?? 'Confirm'}
        </button>
      </div>
    </Modal>
  )
}

// ── Tooltip ──────────────────────────────────────────────────────────────────
// One delegated, portal-rendered tooltip for any element carrying data-tip="…"
// (or a native title, which is migrated to data-tip so the OS tooltip never shows),
// with a shortcut chip from data-tip-kbd="…". Fixed-positioned so it's never clipped
// by an overflow:hidden modal; flips below the target when there's no room above, and
// stays inside the window with its stem still on the target. Monaco owns its own
// hovers, so it's skipped.
//
// It behaves like React Bits' Warm Tooltip, with the whole console as one group. The
// first tooltip waits TIP_COLD_DELAY and pops out of its trigger. While one is showing,
// and for TIP_WARM_WINDOW after it closes, the next skips the wait: from a tooltip still
// on screen it glides across, its label sliding the way it moved. Keyboard focus shows
// it at once, since that's someone stepping through the controls to learn what they are.
const TIP_COLD_DELAY = 400
const TIP_WARM_WINDOW = 300
// Long enough to cross the gap between two toolbar buttons without it starting to close.
const TIP_GRACE = 80
// --dur-fast: closing, and one label swapping for the next.
const TIP_FAST = 120
const TIP_GAP = 8          // trigger to tooltip
const TIP_GAP_SIDE = 10    // beside a rail button
const TIP_EDGE = 8         // kept clear of the window's edge
const TIP_RADIUS = 8       // --radius-xs
const TIP_STEM = 5         // how far the stem reaches out, and half its width at the edge
// The stem's center keeps this far from the tooltip's ends, clear of the rounded corners.
const TIP_STEM_INSET = TIP_RADIUS + TIP_STEM + 1
// Outer edge to label: the 1px outline plus 9px / 5px of padding.
const TIP_PAD_X = 10
const TIP_PAD_Y = 6

type TipSide = 'top' | 'bottom' | 'right'
type TipLabel = { key: number; text: string; kbd: string | null }
type Tip = TipLabel & {
  side: TipSide
  // The point the stem touches, in the tooltip's own (zoomed) CSS space.
  x: number
  y: number
  // How it got here: popped out after the wait, straight in (warm, or from the keyboard),
  // or across from the tooltip before it.
  arrive: 'cold' | 'warm' | 'move'
  closing: boolean
  // The label on its way out while this one comes in, and which way they travel.
  swap: (TipLabel & { dx: number; dy: number }) | null
}

const clampTo = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi))
// Along an edge `len` long. A one-line tooltip beside the rail is shorter than two insets, and
// there the middle is the only place for the stem.
const stemAt = (v: number, len: number) =>
  len < TIP_STEM_INSET * 2 ? len / 2 : clampTo(v, TIP_STEM_INSET, len - TIP_STEM_INSET)

// The tooltip's outline: body and stem as ONE path, filled and stroked once. The stem used to
// be a second shape (a bordered diamond) laid against the body's CSS border, and the two only
// met cleanly when the browser happened to rasterize both onto the same subpixel; one stroke
// has no joint to come apart. Centered half a pixel in, so the 1px stroke sits where a 1px
// border would. For a given side every path has the same commands, so CSS can tween `d`
// from one tooltip to the next on a glide.
function tipPath(side: TipSide, w: number, h: number, at: number): string {
  const o = 0.5, r = TIP_RADIUS - o, R = w - o, B = h - o, s = TIP_STEM
  const arc = (x: number, y: number) => `A${r} ${r} 0 0 1 ${x} ${y}`
  const stemUp = side === 'bottom' ? `H${at - s}L${at} ${o - s}L${at + s} ${o}` : ''
  const stemDown = side === 'top' ? `H${at + s}L${at} ${B + s}L${at - s} ${B}` : ''
  const stemLeft = side === 'right' ? `V${at + s}L${o - s} ${at}L${o} ${at - s}` : ''
  return `M${o + r} ${o}${stemUp}H${R - r}${arc(R, o + r)}V${B - r}${arc(R - r, B)}`
    + `${stemDown}H${o + r}${arc(o, B - r)}${stemLeft}V${o + r}${arc(o + r, o)}Z`
}

function TipText({ label, labelRef }: { label: TipLabel; labelRef?: React.Ref<HTMLSpanElement> }) {
  return (
    <span ref={labelRef} className="tooltip-label">
      {label.text}
      {label.kbd && <kbd className="tooltip-kbd">{label.kbd}</kbd>}
    </span>
  )
}

function TooltipHost() {
  const [tip, setTip] = useState<Tip | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const labelRef = useRef<HTMLSpanElement>(null)
  const shapeRef = useRef<SVGPathElement>(null)

  useEffect(() => {
    let hovered: Element | null = null   // the tip-carrying element under the pointer
    let shown: Element | null = null     // the element the tooltip is naming
    let phase: 'closed' | 'open' | 'closing' = 'closed'
    let byKey = false                    // opened from the keyboard, so it also closes at once
    let warmUntil = 0
    let key = 0
    let openTimer = 0, leaveTimer = 0, closeTimer = 0
    const warm = () => phase !== 'closed' || performance.now() < warmUntil

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

    // Where the stem touches, and which side of the target the tooltip takes.
    const anchor = (el: Element): { side: TipSide; x: number; y: number } => {
      const r = el.getBoundingClientRect()
      // `x`/`y` are read back in the zoomed coordinate space, so divide out however much
      // zoom the rect already carries — see rectScale(). Getting this from --ui-scale
      // instead was right in the browser (Chromium 128+) and wrong in the desktop app
      // (Chromium 126), where it threw the tip left of its target.
      const k = rectScale()
      // Beside a rail's buttons (`data-tip-side="right"`), where above or below would cover
      // the next button. Not on a phone, where the rail is a bar across the top.
      if (el.getAttribute('data-tip-side') === 'right' && window.innerWidth > 560) {
        return { side: 'right', x: Math.round((r.right + TIP_GAP_SIDE) / k), y: Math.round((r.top + r.height / 2) / k) }
      }
      const below = r.top < 52 * k   // a CSS-px threshold, compared against a rect
      return {
        side: below ? 'bottom' : 'top',
        x: Math.round((r.left + r.width / 2) / k),
        y: Math.round((below ? r.bottom + TIP_GAP : r.top - TIP_GAP) / k),
      }
    }

    const open = (el: Element, how: 'cold' | 'warm' | 'key') => {
      clearTimeout(openTimer); clearTimeout(leaveTimer); clearTimeout(closeTimer)
      const text = el.isConnected ? textOf(el) : null
      if (!text) return
      const was = phase === 'closed' ? null : shown
      shown = el; phase = 'open'; byKey = how === 'key'
      if (was === el) { setTip((t) => t && { ...t, closing: false }); return }
      const at = anchor(el)
      const label = { key: ++key, text, kbd: el.getAttribute('data-tip-kbd') }
      setTip((t) => {
        // Still on screen on the same side: glide over, the labels sliding the way it goes.
        if (t && was && how !== 'key' && t.side === at.side) {
          const across = at.side === 'right'
          const d = Math.sign(across ? at.y - t.y : at.x - t.x) || 1
          return { ...label, ...at, arrive: 'move', closing: false,
            swap: { key: t.key, text: t.text, kbd: t.kbd, dx: across ? 0 : d, dy: across ? d : 0 } }
        }
        return { ...label, ...at, arrive: how === 'cold' ? 'cold' : 'warm', closing: false, swap: null }
      })
    }

    const finish = () => { clearTimeout(closeTimer); phase = 'closed'; shown = null; setTip(null) }
    const close = (now: boolean) => {
      clearTimeout(openTimer)
      if (phase === 'closed') return
      if (now || byKey) { clearTimeout(leaveTimer); warmUntil = performance.now() + TIP_WARM_WINDOW; finish(); return }
      if (phase === 'closing') return
      clearTimeout(leaveTimer)
      leaveTimer = window.setTimeout(() => {
        phase = 'closing'
        warmUntil = performance.now() + TIP_WARM_WINDOW
        setTip((t) => t && { ...t, closing: true })
        closeTimer = window.setTimeout(finish, TIP_FAST)
      }, TIP_GRACE)
    }

    const tipOf = (e: Event) => (e.target as Element)?.closest?.('[data-tip],[title]') ?? null
    const onOver = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      const el = tipOf(e)
      if (el === hovered) return   // a move onto one of its own children
      hovered = el
      if (!el || e.buttons !== 0 || el.closest('.monaco-editor')) return
      textOf(el)   // migrate a title now, before the OS tooltip can show during the wait
      clearTimeout(openTimer)
      if (warm()) open(el, 'warm')
      else openTimer = window.setTimeout(() => open(el, 'cold'), TIP_COLD_DELAY)
    }
    const onOut = (e: PointerEvent) => {
      const el = tipOf(e)
      if (!el || el !== hovered) return
      const to = e.relatedTarget as Node | null
      if (to && el.contains(to)) return   // moved onto a child — keep showing
      hovered = null
      clearTimeout(openTimer)
      if (el === shown) close(false)
    }
    // A press closes it, and `hovered` stays put so it won't reopen until the pointer
    // leaves and comes back.
    const onDown = () => close(false)
    // Keyboard focus only. A click fires pointerdown (which closes) and then focusin, so
    // showing on every focus made the tip blink back the instant you pressed the button
    // it belongs to. :focus-visible is exactly the "focused, but not by pointer" test.
    const onFocus = (e: Event) => {
      const el = tipOf(e)
      if (el && el.matches(':focus-visible') && !el.closest('.monaco-editor')) open(el, 'key')
    }
    const onBlur = (e: Event) => { if (shown && shown.contains(e.target as Node)) close(true) }
    // Escape closes dialogs, taking the hovered control with it — but pointerout never fires
    // for an element that was removed, so the tip outlived the button it named ("Close"
    // hanging over the page after the modal went away).
    const onKey = () => close(true)
    const onGone = () => close(true)
    const onHidden = () => { if (document.visibilityState === 'hidden') close(true) }
    document.addEventListener('pointerover', onOver, true)
    document.addEventListener('pointerout', onOut, true)
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('focusin', onFocus)
    document.addEventListener('focusout', onBlur)
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('visibilitychange', onHidden)
    window.addEventListener('scroll', onGone, true)
    window.addEventListener('resize', onGone)
    const gone = new MutationObserver(() => {
      if (hovered && !hovered.isConnected) hovered = null
      if (shown && !shown.isConnected) close(true)
    })
    gone.observe(document.body, { childList: true, subtree: true })
    return () => {
      clearTimeout(openTimer); clearTimeout(leaveTimer); clearTimeout(closeTimer)
      document.removeEventListener('pointerover', onOver, true)
      document.removeEventListener('pointerout', onOut, true)
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('focusout', onBlur)
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('visibilitychange', onHidden)
      window.removeEventListener('scroll', onGone, true)
      window.removeEventListener('resize', onGone)
      gone.disconnect()
    }
  }, [])

  // Size the box from its label and place it before it paints: centered on the target,
  // pushed back inside the window if it would cross the edge, with the stem moved along
  // it to stay on the target. On a glide, CSS carries the box and the stem to the new values.
  useLayoutEffect(() => {
    const box = boxRef.current, label = labelRef.current
    if (!tip || !box || !label) return
    // offsetWidth is element-local, so it needs no zoom correction; the window does.
    const w = label.offsetWidth + TIP_PAD_X * 2
    const h = label.offsetHeight + TIP_PAD_Y * 2
    const s = uiScale()
    let left: number, top: number, at: number
    if (tip.side === 'right') {
      left = tip.x
      top = Math.round(clampTo(tip.y - h / 2, TIP_EDGE, window.innerHeight / s - TIP_EDGE - h))
      at = stemAt(tip.y - top, h)
    } else {
      left = Math.round(clampTo(tip.x - w / 2, TIP_EDGE, window.innerWidth / s - TIP_EDGE - w))
      top = tip.side === 'top' ? tip.y - h : tip.y
      at = stemAt(tip.x - left, w)
    }
    box.style.left = `${left}px`
    box.style.top = `${top}px`
    box.style.width = `${w}px`
    box.style.height = `${h}px`
    box.style.setProperty('--tip-at', `${at}px`)
    shapeRef.current?.setAttribute('d', tipPath(tip.side, w, h, at))
  }, [tip?.key, tip?.side, tip?.x, tip?.y])

  // The outgoing label is only there for the length of the swap.
  const swapping = !!tip?.swap
  useEffect(() => {
    if (!swapping) return
    const k = tip!.key
    const t = window.setTimeout(() => setTip((p) => (p && p.key === k ? { ...p, swap: null } : p)), TIP_FAST)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tip?.key, swapping])

  if (!tip) return null
  const travel = tip.swap ? ({ '--dx': tip.swap.dx, '--dy': tip.swap.dy } as React.CSSProperties) : undefined
  return createPortal(
    <div ref={boxRef} className="tooltip" role="tooltip" data-side={tip.side} data-arrive={tip.arrive}
      data-closing={tip.closing ? '' : undefined}>
      <svg className="tooltip-shape" aria-hidden="true"><path ref={shapeRef} /></svg>
      <span className="tooltip-clip">
        {tip.swap && (
          <span key={tip.swap.key} className="tooltip-layer out" style={travel} aria-hidden="true">
            <TipText label={tip.swap} />
          </span>
        )}
        <span key={tip.key} className={'tooltip-layer' + (tip.swap ? ' in' : '')} style={travel}>
          <TipText label={tip} labelRef={labelRef} />
        </span>
      </span>
    </div>,
    document.body,
  )
}

// Mounted once, near the app root.
export function Overlays() {
  return <><ToastStack /><ConfirmHost /><TooltipHost /></>
}
