// Imperative overlay primitives: a bottom-right Toast stack and a centered
// ConfirmDialog, both mounted once via <Overlays/> in main.tsx and driven from
// anywhere by the exported toast() / confirmDialog() / promptDialog() helpers.
// These replace the native alert/confirm/prompt, which break the calm aesthetic.

import React, { useCallback, useEffect, useState } from 'react'
import { Icon, type IconName } from './icons'

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

function ToastView({ item, onDone }: { item: ToastItem; onDone: (id: number) => void }) {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const a = requestAnimationFrame(() => setShow(true))
    const hide = setTimeout(() => setShow(false), 3600)
    const done = setTimeout(() => onDone(item.id), 3920)
    return () => { cancelAnimationFrame(a); clearTimeout(hide); clearTimeout(done) }
  }, [item.id, onDone])
  const Glyph = Icon[TOAST_ICON[item.tone]]
  return (
    <div className={'toast ' + item.tone + (show ? ' show' : '')} role="status">
      <span className="ic"><Glyph size={20} weight="Bold" /></span>
      <span className="toast-msg">{item.message}</span>
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

function ConfirmHost() {
  const [state, setState] = useState<{ opts: DialogOpts; resolve: (v: boolean | string | null) => void } | null>(null)
  const [value, setValue] = useState('')

  useEffect(() => {
    dialogShow = (opts, resolve) => { setValue(opts.prompt?.defaultValue ?? ''); setState({ opts, resolve }) }
    return () => { dialogShow = null }
  }, [])

  useEffect(() => {
    if (!state) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(state.opts.prompt ? null : false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state])

  if (!state) return null
  const { opts, resolve } = state
  const close = (result: boolean | string | null) => { setState(null); resolve(result) }
  const onConfirm = () => close(opts.prompt ? value : true)
  const onCancel = () => close(opts.prompt ? null : false)
  const toneClass = opts.tone === 'danger' ? 'danger' : opts.tone === 'warning' ? 'warning' : ''
  const Glyph = Icon[opts.icon ?? (opts.tone === 'danger' ? 'warn' : opts.tone === 'warning' ? 'warn' : 'info')]

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-head">
          <div className={'confirm-icon ' + toneClass}><Glyph size={22} weight="Bold" /></div>
          <div className="confirm-text">
            <div className="confirm-title">{opts.title}</div>
            {opts.message && <div className="confirm-msg">{opts.message}</div>}
          </div>
        </div>
        {opts.prompt && (
          <div className="confirm-input">
            {opts.prompt.multiline ? (
              <textarea autoFocus value={value} placeholder={opts.prompt.placeholder}
                onChange={(e) => setValue(e.target.value)} />
            ) : (
              <input type="text" autoFocus value={value} placeholder={opts.prompt.placeholder}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') onConfirm() }} />
            )}
          </div>
        )}
        <div className="confirm-foot">
          <button className="ghost" onClick={onCancel}>{opts.cancelLabel ?? 'Cancel'}</button>
          <button className={opts.tone === 'danger' ? 'danger' : 'primary'} onClick={onConfirm}>
            {opts.confirmLabel ?? 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

// Mounted once, near the app root.
export function Overlays() {
  return <><ToastStack /><ConfirmHost /></>
}
