import React, { useState } from 'react'
import { Icon } from './icons'

export function Card(props: { title?: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card">
      {/* h2, not h3: the page's <h1> is the only heading above it, and a level skip is a
          1.3.1 failure that also breaks heading-jump navigation. */}
      {props.title && <h2>{props.title}</h2>}
      {props.hint && <div className="hint">{props.hint}</div>}
      {props.children}
    </div>
  )
}

// A field's label, description, and control are three siblings, so the label can't wrap the
// control — it has to point at it. Field mints one id per instance and hands it down; the
// primitives below claim it. Without this every input in the console is an unnamed edit box
// to a screen reader, and clicking a label doesn't focus its input.
type FieldIds = { id: string; labelId: string; descId?: string }
const FieldCtx = React.createContext<FieldIds | null>(null)

/** The ids of the enclosing <Field>, for a hand-rolled control that isn't one of the
 *  primitives below. Returns null outside a Field. */
export function useFieldIds(): FieldIds | null {
  return React.useContext(FieldCtx)
}

// Wire a control to its Field: the label names it, the description describes it. Outside a
// Field (a bare filter box, a toolbar search) fall back to the caller's own aria-label.
function labelled(f: FieldIds | null, ariaLabel?: string) {
  return {
    id: f?.id,
    'aria-labelledby': f ? f.labelId : undefined,
    'aria-label': f ? undefined : ariaLabel,
    'aria-describedby': f?.descId,
  }
}

export function Field(
  props: { label: string; desc?: React.ReactNode; children: React.ReactNode; plain?: boolean },
) {
  const uid = React.useId()
  const ids: FieldIds = { id: `${uid}c`, labelId: `${uid}l`, descId: props.desc ? `${uid}d` : undefined }
  return (
    <FieldCtx.Provider value={ids}>
      <div className="field">
        {props.plain
          ? <div className="flabel" id={ids.labelId}>{props.label}</div>
          : <label id={ids.labelId} htmlFor={ids.id}>{props.label}</label>}
        {props.desc && <div className="desc" id={ids.descId}>{props.desc}</div>}
        {props.children}
      </div>
    </FieldCtx.Provider>
  )
}

export function Text(props: { value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean; ariaLabel?: string }) {
  const f = useFieldIds()
  return (
    <input
      type="text"
      {...labelled(f, props.ariaLabel)}
      className={props.mono ? 'mono' : ''}
      value={props.value ?? ''}
      placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
    />
  )
}

export function Area(props: { value: string; onChange: (v: string) => void; rows?: number; placeholder?: string; maxLength?: number; ariaLabel?: string }) {
  const f = useFieldIds()
  const ref = React.useRef<HTMLTextAreaElement>(null)
  // Auto-grow to fit content (no manual resize handle) — reset to auto first so it
  // shrinks back when text is removed, then size to the scroll height + border. Reading
  // scrollHeight right after writing height forces a synchronous reflow, so coalesce it
  // into one frame rather than paying for it on every keystroke.
  const frame = React.useRef(0)
  const grow = React.useCallback(() => {
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => {
      const el = ref.current
      if (!el) return
      el.style.height = 'auto'
      el.style.height = el.scrollHeight + (el.offsetHeight - el.clientHeight) + 'px'
    })
  }, [])
  React.useEffect(() => { grow() }, [props.value, grow])
  React.useEffect(() => () => cancelAnimationFrame(frame.current), [])
  return (
    <textarea
      ref={ref}
      {...labelled(f, props.ariaLabel)}
      rows={props.rows ?? 3}
      value={props.value ?? ''}
      placeholder={props.placeholder}
      maxLength={props.maxLength}
      onChange={(e) => props.onChange(e.target.value)}
      onInput={grow}
    />
  )
}

export function Num(props: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; ariaLabel?: string }) {
  const f = useFieldIds()
  return (
    <input
      type="number"
      {...labelled(f, props.ariaLabel)}
      value={props.value ?? 0}
      min={props.min}
      max={props.max}
      step={props.step}
      onChange={(e) => props.onChange(Number(e.target.value))}
    />
  )
}

export function Select(props: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; ariaLabel?: string; className?: string }) {
  const f = useFieldIds()
  return (
    <select {...labelled(f, props.ariaLabel)} className={props.className} value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

// A switch is a div, so it can't be the target of a <label for>; it takes the Field's label
// by reference instead. Standalone toggles (no Field, no visible `label`) MUST pass
// `ariaLabel` — otherwise the control announces as "switch, on" with no subject.
export function Toggle(props: { value: boolean; onChange: (v: boolean) => void; label?: string; ariaLabel?: string; disabled?: boolean }) {
  const f = useFieldIds()
  const dis = !!props.disabled
  return (
    <div
      className={'toggle' + (props.value ? ' on' : '') + (dis ? ' disabled' : '')}
      role="switch"
      aria-checked={props.value}
      aria-disabled={dis}
      aria-label={props.label ? undefined : (props.ariaLabel ?? undefined)}
      aria-labelledby={!props.label && !props.ariaLabel && f ? f.labelId : undefined}
      aria-describedby={f?.descId}
      tabIndex={dis ? -1 : 0}
      onClick={() => { if (!dis) props.onChange(!props.value) }}
      onKeyDown={(e) => {
        if (dis) return
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); props.onChange(!props.value) }
      }}
    >
      <div className="track"><div className="knob" /></div>
      {props.label && <span className="lbl">{props.label}</span>}
    </div>
  )
}

// ── Segmented (an exclusive choice rendered as a button row) ─────────────────
// The console had four of these and only one carried its state: the other three were
// plain buttons with a visual-only `.on` class, so a screen reader heard "Today / 7
// days / 30 days" with no way to tell which was active — on a page whose numbers
// change meaning with the answer. One component, so that can't drift again.
//
// `contents` renders the group box away (`display: contents`) for a container that is
// already a flex row with other children, keeping the ARIA grouping without the layout.
export function Segmented<T extends string | number>(props: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: React.ReactNode }[]
  ariaLabel: string
  /** Class for the group wrapper — `useg`, `ext-seg`, … */
  className?: string
  /** Per-button class; receives whether that option is selected. */
  buttonClass?: (on: boolean) => string
  contents?: boolean
}) {
  const uid = React.useId()
  const group = React.useRef<HTMLDivElement>(null)
  const idOf = (v: T) => `${uid}-${String(v).replace(/\W/g, '_')}`
  const onKey = (e: React.KeyboardEvent) => {
    const step = /^Arrow(Right|Down)$/.test(e.key) ? 1 : /^Arrow(Left|Up)$/.test(e.key) ? -1 : 0
    if (!step) return
    e.preventDefault()
    const i = props.options.findIndex((o) => o.value === props.value)
    const next = props.options[(Math.max(0, i) + step + props.options.length) % props.options.length]
    props.onChange(next.value)
    group.current?.querySelector<HTMLElement>(`#${CSS.escape(idOf(next.value))}`)?.focus()
  }
  return (
    <div
      ref={group}
      className={props.className}
      style={props.contents ? { display: 'contents' } : undefined}
      role="radiogroup"
      aria-label={props.ariaLabel}
      onKeyDown={onKey}
    >
      {props.options.map((o) => {
        const on = o.value === props.value
        return (
          <button
            key={String(o.value)}
            id={idOf(o.value)}
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            className={props.buttonClass ? props.buttonClass(on) : (on ? 'on' : '')}
            onClick={() => props.onChange(o.value)}
          >
            {o.label}
          </button>
        )
      })}
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
        {s.busy ? <><span className="spinner" /> Saving…</> : props.label ?? 'Save changes'}
      </button>
      {/* The outcome of a save was announced to nobody — role="status" appeared exactly
          once in the console, on the toast. A live region that is always present (rather
          than mounted with its message) is what actually gets announced. */}
      <span role="status">
        {s.saved && <span className="saved"><Icon.check size={15} weight="Bold" /> Saved</span>}
        {s.error && <span className="err">{s.error}</span>}
      </span>
    </div>
  )
}

// ── Unsaved-work registry ────────────────────────────────────────────────────
// The whole console is built on "nothing applies until you press Save" — and every
// page is remounted by a `key` when the tab or the server changes, which threw the
// draft away without a word. The SaveDock lives *inside* the remounted subtree, so
// the bar that just said "You have unsaved changes." vanished along with them.
//
// Each useEditable instance registers its dirty flag here; the shell asks before it
// navigates. Keeping the registry module-level (rather than in context) means pages
// need no wiring at all — they already route their draft through useEditable.
const dirtyPages = new Map<number, () => boolean>()
let nextPageId = 1

/** True when any mounted page is holding edits that haven't been saved. */
export function hasUnsavedChanges(): boolean {
  for (const isDirty of dirtyPages.values()) if (isDirty()) return true
  return false
}

/** Register a dirty-flag source for a page that doesn't use `useEditable`. */
export function useDirtyGuard(isDirty: () => boolean): void {
  const latest = React.useRef(isDirty)
  latest.current = isDirty
  React.useEffect(() => {
    const id = nextPageId++
    dirtyPages.set(id, () => latest.current())
    return () => { dirtyPages.delete(id) }
  }, [])
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
  // Memoized on `data`: this used to re-serialize the whole page state on every render, so
  // typing one character into a filter box re-stringified every channel, role or extension row.
  const dirty = React.useMemo(
    () => data != null && JSON.stringify(data) !== base.current,
    [data],
  )
  useDirtyGuard(() => dirty)
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
        {/* "unsaved changes" -> "Saved" -> an error is the product's core state machine,
            and none of it reached a screen reader. */}
        <span className="savedock-msg" role="status">
          {s.error ? <span className="err">{s.error}</span>
            : s.saved ? <span className="saved"><Icon.check size={15} weight="Bold" /> Saved</span>
            : <>You have unsaved changes.</>}
        </span>
        <div className="savedock-actions">
          {props.onReset && (
            <button className="ghost" disabled={s.busy || !props.dirty} onClick={props.onReset}>Reset</button>
          )}
          <button className="primary" disabled={s.busy || !props.dirty} onClick={s.run}>
            {s.busy ? <><span className="spinner" /> Saving…</> : props.label ?? 'Save changes'}
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

// Friendly labels for the code-preview box header, keyed by the fence's info string.
const LANG_LABEL: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
  json: 'json', bash: 'shell', sh: 'shell', py: 'python', md: 'markdown',
}

const KEYWORDS = new Set([
  'const', 'let', 'var', 'function', 'return', 'async', 'await', 'if', 'else', 'for', 'while',
  'do', 'of', 'in', 'new', 'class', 'extends', 'implements', 'import', 'from', 'export', 'default',
  'true', 'false', 'null', 'undefined', 'void', 'typeof', 'instanceof', 'interface', 'type', 'enum',
  'public', 'private', 'protected', 'readonly', 'static', 'this', 'super', 'try', 'catch', 'finally',
  'throw', 'switch', 'case', 'break', 'continue', 'yield', 'as', 'satisfies',
])

// Minimal TS/JS/JSON syntax highlighter for the code-preview box, using the exact
// DESIGN.md token colours: comment var(--text-3) · string #7fd1a0 · keyword #b69cff ·
// fn/number #e0a458. Other languages render uncoloured.
function highlight(code: string, lang: string): React.ReactNode {
  const l = lang.toLowerCase()
  if (l && !['ts', 'tsx', 'js', 'jsx', 'javascript', 'typescript', 'json'].includes(l)) return code
  const re = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d[\w.]*)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)|([\s\S])/g
  const out: React.ReactNode[] = []
  let m: RegExpExecArray | null
  let key = 0
  while ((m = re.exec(code))) {
    if (m[1]) out.push(<span key={key++} className="tok-com">{m[1]}</span>)
    else if (m[2]) out.push(<span key={key++} className="tok-str">{m[2]}</span>)
    else if (m[3]) out.push(<span key={key++} className="tok-num">{m[3]}</span>)
    else if (m[4]) out.push(KEYWORDS.has(m[4])
      ? <span key={key++} className="tok-kw">{m[4]}</span>
      : <span key={key++} className="tok-fn">{m[4]}</span>)
    else if (m[5]) out.push(KEYWORDS.has(m[5]) ? <span key={key++} className="tok-kw">{m[5]}</span> : m[5])
    else out.push(m[6])
  }
  return out
}

// A docs code-preview box (DESIGN.md): filename head + a trailing copy button whose
// glyph swaps to a green check-circle with a small pop on click.
function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard blocked — code is still selectable */ }
  }
  return (
    <div className="codeblock">
      <div className="head">
        <span className="file">{LANG_LABEL[lang.toLowerCase()] || lang || 'typescript'}</span>
        <button className="cb-copy" onClick={copy} data-tip={copied ? 'Copied' : 'Copy'} aria-label="Copy code">
          {copied ? <Icon.check size={15} weight="Bold" className="cb-check" /> : <Icon.copy size={15} />}
        </button>
      </div>
      <pre><code>{highlight(code, lang)}</code></pre>
    </div>
  )
}

// Leading icon per callout tone (the Resend-style callout has no uppercase eyebrow).
const CALLOUT_ICON: Record<string, React.ReactNode> = {
  tip: <Icon.check size={17} weight="Bold" />,
  note: <Icon.info size={17} weight="Bold" />,
  info: <Icon.info size={17} weight="Bold" />,
  warning: <Icon.warn size={17} weight="Bold" />,
}

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

    // Fenced code block: ``` … ``` rendered verbatim in a framed code-preview box.
    if (line.startsWith('```')) {
      flushList(k); flushPara(k)
      const lang = line.slice(3).trim()
      const code: string[] = []
      i++
      while (i < lines.length && lines[i].trim() !== '```') { code.push(lines[i]); i++ }
      i++ // skip closing ```
      out.push(<CodeBlock key={'pre' + k} lang={lang} code={code.join('\n')} />)
      continue
    }

    const cm = line.match(/^:::(tip|note|warning|info)\s*(.*)$/)
    if (cm) {
      flushList(k); flushPara(k)
      const inner: string[] = []
      i++
      while (i < lines.length && lines[i].trim() !== ':::') { inner.push(lines[i]); i++ }
      i++ // skip closing :::
      out.push(
        <div key={'c' + k} className={'callout ' + cm[1]}>
          <span className="ic">{CALLOUT_ICON[cm[1]] ?? CALLOUT_ICON.note}</span>
          <div className="callout-body">
            {cm[2].trim() && <div className="callout-title">{cm[2].trim()}</div>}
            {renderBlocks(inner, 'in' + k, onLink)}
          </div>
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
    // ## / ### map to h2 / h3: the doc page's own title is the h1, so ## must be the next
    // level down. Rendering it as h3 skipped a level on every documentation page.
    if (line.startsWith('### ')) {
      flushList(k); flushPara(k)
      const t = line.slice(4)
      out.push(<h3 key={i} id={slugify(t)}>{inline(t, 'h' + k, onLink)}</h3>)
      i++; continue
    }
    if (line.startsWith('## ')) {
      flushList(k); flushPara(k)
      const t = line.slice(3)
      out.push(<h2 key={i} id={slugify(t)}>{inline(t, 'h' + k, onLink)}</h2>)
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

// One page throwing used to unmount the whole console — sidebar, navigation and the toast
// host with it — leaving a blank window and no way back but a reload. The backend and this
// frontend update independently, so a payload that drifted shape is a real case, not a
// hypothetical. Keep the shell up and let the operator retry or move to another tab.
export class PageBoundary extends React.Component<
  { children: React.ReactNode; onReset?: () => void },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error) { console.error('[olisar] page render failed', error) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <>
        <div className="page-head">
          <div className="title-row">
            <div className="title-ic"><Icon.warn size={19} weight="Linear" /></div>
            <h1>This page didn't load</h1>
          </div>
          <p>The rest of the console still works — pick another tab, or try this one again.</p>
        </div>
        <div className="card">
          <div className="callout danger">
            <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
            <div className="callout-body">
              <div className="callout-title">{this.state.error.name}</div>
              {this.state.error.message || 'No further detail.'}
            </div>
          </div>
          <div className="savebar">
            <button className="primary" onClick={() => { this.setState({ error: null }); this.props.onReset?.() }}>
              <Icon.refresh size={14} /> Try again
            </button>
          </div>
        </div>
      </>
    )
  }
}

/**
 * Poll `load` every `everyMs`, but only while the tab is actually being looked at.
 *
 * The console used to run five independent `setInterval`s — bot status every 5s, tunnel and
 * moderation standing every 20s, live usage every 4s, re-index every 3.5s — none of which
 * stopped when the window went to the background. A console left open in a tab kept the
 * backend busy indefinitely, and in server-hosting mode each poll is an SSH round-trip.
 *
 * Hidden tab: nothing runs, and the first poll fires immediately on return. `active: false`
 * (e.g. the re-index finished) stops it entirely without unmounting the caller.
 */
export function usePoll(load: () => void | Promise<unknown>, everyMs: number, active = true) {
  const latest = React.useRef(load)
  latest.current = load
  // Every caller wrapped its own poll in `.catch(() => {})`, so a backend that had gone
  // away rendered as one that simply had nothing to say: live meters froze at their last
  // value with the green "live" dot still pulsing. Count consecutive failures instead and
  // let the caller show it. Two in a row, so one dropped request isn't an outage.
  const [failures, setFailures] = React.useState(0)
  React.useEffect(() => {
    if (!active) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = () => {
      let settled = false
      try {
        const r = latest.current()
        if (r && typeof (r as Promise<unknown>).then === 'function') {
          settled = true
          ;(r as Promise<unknown>).then(
            () => setFailures(0),
            () => setFailures((n) => n + 1),
          )
        }
      } catch {
        setFailures((n) => n + 1)
      }
      if (!settled) setFailures(0)
      timer = setTimeout(tick, everyMs)
    }
    const start = () => { if (timer === undefined) tick() }
    const stop = () => { clearTimeout(timer); timer = undefined }
    const onVisible = () => (document.hidden ? stop() : start())
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisible)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisible) }
  }, [everyMs, active])
  return { failures, stale: failures >= 2 }
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
