// Command palette — ⌘K / Ctrl-K.
//
// DESIGN.md's audience is "an operator who knows what they're doing", and the console gave
// them one path to everything: point at the rail and click. This is the accelerator that
// costs a novice nothing, because it is invisible until asked for.
//
// Everything reachable from the rail plus the server switcher is in here, so the two things
// an operator does most — change page, change server — are one keystroke and a few letters
// away rather than a trip to the sidebar.

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Modal } from './overlays'
import { Icon, type IconName } from './icons'

export type Command = {
  id: string
  label: string
  /** Shown to the right; says which kind of thing this is. */
  group: string
  ic: IconName
  /** Extra words that should match this command without being displayed. */
  keywords?: string
  run: () => void
}

/** Subsequence match: "cmr" finds "Command replies". Returns null when it doesn't match. */
function score(needle: string, hay: string): number | null {
  if (!needle) return 0
  const n = needle.toLowerCase()
  const h = hay.toLowerCase()
  const direct = h.indexOf(n)
  if (direct >= 0) return direct === 0 ? 0 : 1 + direct / 100   // prefix beats contains
  let i = 0
  let gaps = 0
  let last = -1
  for (const ch of n) {
    const at = h.indexOf(ch, i)
    if (at < 0) return null
    if (last >= 0) gaps += at - last - 1
    last = at
    i = at + 1
  }
  return 50 + gaps / 100   // subsequence ranks below a substring
}

export function CommandPalette(props: { commands: Command[]; open: boolean; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const titleId = React.useId()

  useEffect(() => { if (props.open) { setQ(''); setSel(0) } }, [props.open])

  const hits = useMemo(() => {
    const out: { c: Command; s: number }[] = []
    for (const c of props.commands) {
      const s = score(q, c.label + ' ' + c.group + ' ' + (c.keywords ?? ''))
      if (s !== null) out.push({ c, s })
    }
    return out.sort((a, b) => a.s - b.s).slice(0, 12).map((x) => x.c)
  }, [q, props.commands])

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('.cmdk-item.sel')
    el?.scrollIntoView({ block: 'nearest' })
  }, [sel, hits])

  if (!props.open) return null
  const clamped = Math.min(sel, Math.max(0, hits.length - 1))
  const choose = (c: Command | undefined) => { if (!c) return; props.onClose(); c.run() }

  return (
    <Modal className="cmdk" labelledBy={titleId} onClose={props.onClose}>
      <h2 id={titleId} className="visually-hidden">Command palette</h2>
      <div className="cmdk-input">
        <Icon.search size={17} />
        <input
          autoFocus
          type="text"
          value={q}
          placeholder="Go to a page or switch server…"
          aria-label="Search commands"
          aria-controls="cmdk-list"
          aria-activedescendant={hits[clamped] ? 'cmdk-' + hits[clamped].id : undefined}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => { setQ(e.target.value); setSel(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
            else if (e.key === 'Enter') { e.preventDefault(); choose(hits[clamped]) }
          }}
        />
        <kbd className="cmdk-esc">esc</kbd>
      </div>
      <div className="cmdk-list" id="cmdk-list" role="listbox" aria-label="Commands" ref={listRef}>
        {hits.length === 0 && <div className="cmdk-empty">Nothing matches “{q}”.</div>}
        {hits.map((c, i) => (
          <div
            key={c.id}
            id={'cmdk-' + c.id}
            role="option"
            aria-selected={i === clamped}
            className={'cmdk-item' + (i === clamped ? ' sel' : '')}
            onMouseEnter={() => setSel(i)}
            onMouseDown={(e) => { e.preventDefault(); choose(c) }}
          >
            {React.createElement(Icon[c.ic], { size: 16 })}
            <span className="cmdk-label">{c.label}</span>
            <span className="cmdk-group">{c.group}</span>
          </div>
        ))}
      </div>
    </Modal>
  )
}

/** ⌘K anywhere; Ctrl-K everywhere except inside a text field. */
export function usePaletteHotkey(open: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'k' || !(e.metaKey || e.ctrlKey)) return
      // Ctrl-K is "kill to end of line" in every macOS text field, and this hook was
      // swallowing it globally — in the system prompt, all fourteen command-reply boxes,
      // the test-chat composer and the extension editor. ⌘K is unambiguous and still works
      // everywhere; Ctrl-K yields to the field the operator is typing in.
      if (!e.metaKey && e.ctrlKey) {
        const el = document.activeElement as HTMLElement | null
        const tag = el?.tagName
        if (tag === 'TEXTAREA' || tag === 'INPUT' || el?.isContentEditable) return
      }
      e.preventDefault()
      open()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
}
