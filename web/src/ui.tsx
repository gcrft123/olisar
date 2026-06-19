import React, { useState } from 'react'
import { Icon } from './icons'

export function Card(props: { title?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="card">
      {props.title && <h3>{props.title}</h3>}
      {props.hint && <div className="hint">{props.hint}</div>}
      {props.children}
    </div>
  )
}

export function Field(props: { label: string; desc?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>{props.label}</label>
      {props.desc && <div className="desc">{props.desc}</div>}
      {props.children}
    </div>
  )
}

export function Text(props: { value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean }) {
  return (
    <input
      type="text"
      className={props.mono ? 'mono' : ''}
      value={props.value ?? ''}
      placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
    />
  )
}

export function Area(props: { value: string; onChange: (v: string) => void; rows?: number; placeholder?: string }) {
  return (
    <textarea
      rows={props.rows ?? 4}
      value={props.value ?? ''}
      placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
    />
  )
}

export function Num(props: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number }) {
  return (
    <input
      type="number"
      value={props.value ?? 0}
      min={props.min}
      max={props.max}
      step={props.step}
      onChange={(e) => props.onChange(Number(e.target.value))}
    />
  )
}

export function Select(props: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <select value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

export function Toggle(props: { value: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <div className={'toggle' + (props.value ? ' on' : '')} onClick={() => props.onChange(!props.value)}>
      <div className="track"><div className="knob" /></div>
      {props.label && <span className="lbl">{props.label}</span>}
    </div>
  )
}

// A save button with status feedback, given an async save function.
export function useSaver(save: () => Promise<void>) {
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = async () => {
    setBusy(true); setError(null); setSaved(false)
    try {
      await save()
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e: any) {
      setError(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }
  return { busy, saved, error, run }
}

export function SaveBar(props: { saver: ReturnType<typeof useSaver>; label?: string }) {
  const s = props.saver
  return (
    <div className="savebar">
      <button className="primary" disabled={s.busy} onClick={s.run}>
        {s.busy ? 'Saving…' : props.label ?? 'Save changes'}
      </button>
      {s.saved && (
        <span className="saved"><Icon.check size={15} weight="Bold" /> Saved — live now</span>
      )}
      {s.error && <span className="err">{s.error}</span>}
    </div>
  )
}

// Like useAsync, but tracks whether the editable `data` has diverged from what was
// last loaded/saved. Pages edit `data` freely (nothing hits the server) and render a
// <SaveDock> driven by `dirty`; saving calls `markSaved()`, reset reverts.
export function useEditable<T>(loader: () => Promise<T>, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const base = React.useRef<string>('')
  const reload = React.useCallback(() => {
    setLoading(true)
    loader()
      .then((d) => { base.current = JSON.stringify(d); setData(d); setLoading(false) })
      .catch(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  React.useEffect(() => { reload() }, [reload])
  const dirty = data != null && JSON.stringify(data) !== base.current
  return {
    data, setData, loading, reload, dirty,
    reset: () => { if (base.current) setData(JSON.parse(base.current)) },
    markSaved: () => { if (data != null) base.current = JSON.stringify(data) },
    baseline: (): T | null => (base.current ? JSON.parse(base.current) : null),
  }
}

// A hovering "unsaved changes" dock (Discord-style): slides up from the bottom when
// there are pending edits, with Reset + Save. Nothing is applied until Save.
export function SaveDock(props: {
  dirty: boolean
  saver: ReturnType<typeof useSaver>
  onReset?: () => void
  label?: string
}) {
  const s = props.saver
  const show = props.dirty || s.busy || s.saved || !!s.error
  return (
    <div className={'savedock' + (show ? ' show' : '')} aria-hidden={!show}>
      <div className="savedock-inner">
        <span className="savedock-msg">
          {s.error ? <span className="err">{s.error}</span>
            : s.saved ? <span className="saved"><Icon.check size={15} weight="Bold" /> Saved — live now</span>
            : <>Careful — you have unsaved changes.</>}
        </span>
        <div className="savedock-actions">
          {props.onReset && (
            <button className="ghost sm" disabled={s.busy || !props.dirty} onClick={props.onReset}>Reset</button>
          )}
          <button className="primary sm" disabled={s.busy || !props.dirty} onClick={s.run}>
            {s.busy ? 'Saving…' : props.label ?? 'Save changes'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Minimal Markdown renderer (no dependency) ───────────────────────────────
// Supports: ## / ### headings, - bullet lists, blank-line paragraphs, and inline
// **bold**, `code`, and [text](url). Content is trusted (authored in docs.tsx),
// and we render React nodes (no dangerouslySetInnerHTML).
function inline(text: string, key: string, onLink?: (id: string) => void): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('**')) nodes.push(<strong key={key + i}>{t.slice(2, -2)}</strong>)
    else if (t.startsWith('`')) nodes.push(<code key={key + i}>{t.slice(1, -1)}</code>)
    else {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)!
      const url = mm[2]
      if (url.startsWith('#') || url.startsWith('tab:')) {
        // In-app link: between doc pages (#id / #heading) or to a dashboard tab (tab:id).
        nodes.push(
          <a key={key + i} href={url.startsWith('#') ? url : '#'} onClick={(e) => { e.preventDefault(); onLink?.(url) }}>{mm[1]}</a>,
        )
      } else {
        nodes.push(<a key={key + i} href={url} target="_blank" rel="noreferrer">{mm[1]}</a>)
      }
    }
    last = m.index + t.length
    i++
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

// Heading list for an "On this page" TOC: ## -> level 1, ### -> level 2.
export function headingsOf(md: string): { level: number; text: string; slug: string }[] {
  const out: { level: number; text: string; slug: string }[] = []
  for (const raw of md.split('\n')) {
    const line = raw.trim()
    if (line.startsWith('### ')) out.push({ level: 2, text: line.slice(4), slug: slugify(line.slice(4)) })
    else if (line.startsWith('## ')) out.push({ level: 1, text: line.slice(3), slug: slugify(line.slice(3)) })
  }
  return out
}

const CALLOUT_LABELS: Record<string, string> = { tip: 'Tip', note: 'Note', warning: 'Warning', info: 'Info' }

function splitRow(line: string): string[] {
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim())
}
function isTableSep(line: string): boolean {
  const l = line.trim()
  return /^\|?[\s:|-]+\|?$/.test(l) && l.includes('-') && l.includes('|')
}

// Block-level Markdown: paragraphs, bullet lists, ## / ### headings, GitHub pipe
// tables, and :::tip / :::note / :::warning / :::info callouts (nestable). Recursive
// so callout bodies are full Markdown. Content is trusted (docs.tsx); rendered as
// React nodes (no dangerouslySetInnerHTML).
function renderBlocks(lines: string[], kb: string, onLink?: (id: string) => void): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let list: string[] = []
  let para: string[] = []
  const flushList = (k: string) => {
    if (list.length) {
      const items = list
      out.push(<ul key={'ul' + k}>{items.map((li, j) => <li key={j}>{inline(li, 'li' + k + j, onLink)}</li>)}</ul>)
      list = []
    }
  }
  const flushPara = (k: string) => {
    if (para.length) { out.push(<p key={'p' + k}>{inline(para.join(' '), 'pp' + k, onLink)}</p>); para = [] }
  }

  let i = 0
  while (i < lines.length) {
    const k = kb + i
    const line = lines[i].trim()

    const cm = line.match(/^:::(tip|note|warning|info)\s*(.*)$/)
    if (cm) {
      flushList(k); flushPara(k)
      const inner: string[] = []
      i++
      while (i < lines.length && lines[i].trim() !== ':::') { inner.push(lines[i]); i++ }
      i++ // skip closing :::
      out.push(
        <div key={'c' + k} className={'callout callout-' + cm[1]}>
          <div className="callout-label">{cm[2].trim() || CALLOUT_LABELS[cm[1]]}</div>
          <div className="callout-body">{renderBlocks(inner, 'in' + k, onLink)}</div>
        </div>,
      )
      continue
    }

    if (line.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushList(k); flushPara(k)
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(splitRow(lines[i].trim())); i++ }
      out.push(
        <div key={'tw' + k} className="doc-table-wrap">
          <table className="doc-table">
            <thead><tr>{header.map((h, j) => <th key={j}>{inline(h, 'th' + k + j, onLink)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c, 'td' + k + ri + ci, onLink)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
      continue
    }

    if (!line) { flushList(k); flushPara(k); i++; continue }
    if (line.startsWith('### ')) {
      flushList(k); flushPara(k)
      const t = line.slice(4)
      out.push(<h4 key={i} id={slugify(t)}>{inline(t, 'h' + k, onLink)}</h4>)
      i++; continue
    }
    if (line.startsWith('## ')) {
      flushList(k); flushPara(k)
      const t = line.slice(3)
      out.push(<h3 key={i} id={slugify(t)}>{inline(t, 'h' + k, onLink)}</h3>)
      i++; continue
    }
    if (line.startsWith('- ')) { flushPara(k); list.push(line.slice(2)); i++; continue }
    if (list.length) { list[list.length - 1] += ' ' + line; i++; continue } // wrapped bullet
    para.push(line); i++ // paragraph line (joined across wraps)
  }
  flushList('e' + kb); flushPara('e' + kb)
  return out
}

export function Markdown(props: { md: string; onDocLink?: (id: string) => void }) {
  return <div className="doc">{renderBlocks(props.md.trim().split('\n'), '', props.onDocLink)}</div>
}

export function useAsync<T>(loader: () => Promise<T>, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const reload = React.useCallback(() => {
    setLoading(true)
    loader().then((d) => { setData(d); setLoading(false) }).catch(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  React.useEffect(() => { reload() }, [reload])
  return { data, loading, reload, setData }
}
