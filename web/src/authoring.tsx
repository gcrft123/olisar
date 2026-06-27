// The extension code editor — the "Build" mode of the Extensions tab. A focused,
// full-width Monaco editor over the Olisar SDK; author TypeScript, transpile in-browser
// (esbuild-wasm), and save to the backend. Lazy-loaded by pages.tsx so the heavy
// editor/transpiler only load when an operator drills in to create or edit.
import { useEffect, useState } from 'react'
import { api } from './api'
import { Card, Field, Text, useSaver } from './ui'
import { Icon } from './icons'
import { confirmDialog } from './overlays'

const TEMPLATE = `// A new Olisar extension. Autocomplete shows the host.* capabilities you can use.
defineExtension({
  id: "my_extension",          // lowercase letters/digits/underscores; this is its key
  name: "My extension",
  version: "1.0.0",
  category: "General",
  description: "What this extension does.",
  permissions: [],             // e.g. ["fetch", "secret:uex_api_key"]
  tools: [
    {
      name: "my_tool",
      description: "Tell the model when to call this.",
      parameters: {
        type: "object",
        properties: { input: { type: "string", description: "what to act on" } },
        required: ["input"],
      },
      handler: async (args, ctx) => {
        return "you said: " + args.input;
      },
    },
  ],
});
`

type Status = { kind: 'ok' | 'err' | 'info'; msg: string } | null
const STATUS_COLOR = { ok: 'var(--ok)', err: 'var(--danger)', info: 'var(--text-3)' }

export default function ExtensionEditor(props: {
  editKey: string | null
  onBack: () => void
  onChanged: () => void
}) {
  const [key, setKey] = useState<string | null>(props.editKey)
  const [kind, setKind] = useState<string>('user')  // built-ins are editable too, just not deletable
  const [source, setSource] = useState(props.editKey ? '' : TEMPLATE)
  const [name, setName] = useState('')
  const [manifest, setManifest] = useState<any>(null)
  const [status, setStatus] = useState<Status>(props.editKey ? { kind: 'info', msg: 'Loading…' } : null)
  const [Editor, setEditor] = useState<any>(null)

  // Lazy-load Monaco + SDK types only on first open of the editor.
  useEffect(() => {
    let alive = true
    ;(async () => {
      const [mod, setup] = await Promise.all([import('@monaco-editor/react'), import('./monaco-setup')])
      try { const t = await api.authoringTypes(); setup.ensureSdkTypes(t.dts) } catch { /* types optional */ }
      if (alive) setEditor(() => mod.default)
    })()
    return () => { alive = false }
  }, [])

  // Load the package being edited (if any).
  useEffect(() => {
    if (!props.editKey) return
    let alive = true
    ;(async () => {
      try {
        const p = await api.getAuthoring(props.editKey!)
        if (!alive) return
        setKey(props.editKey)
        setKind(p.kind || 'user')
        setSource(p.source_ts || p.compiled_js || '')
        setName(p.name || '')
        setManifest(p.manifest)
        setStatus(p.kind === 'builtin'
          ? { kind: 'info', msg: 'Built-in extension — your edits persist and stop it auto-updating.' }
          : null)
      } catch (e: any) { if (alive) setStatus({ kind: 'err', msg: e.message }) }
    })()
    return () => { alive = false }
  }, [props.editKey])

  // The server transpiles the source (it's the source of truth and the trust boundary),
  // so the client just sends TypeScript — no in-browser transpile on save/validate.
  const validate = async () => {
    setStatus({ kind: 'info', msg: 'Validating…' })
    try {
      const r = await api.validateAuthoring({ source_ts: source })
      setManifest(r.manifest)
      const m = r.manifest
      setStatus({ kind: 'ok', msg: `Valid: ${m.id} — ${(m.tools || []).length} tool(s), ${(m.commands || []).length} command(s)` })
    } catch (e: any) { setStatus({ kind: 'err', msg: e.message }) }
  }

  const saver = useSaver(async () => {
    const body = { source_ts: source, name: name || undefined }
    if (key) await api.updateAuthoring(key, body)
    else { const r = await api.createAuthoring(body); setKey(r.key) }
    props.onChanged()
    setStatus({ kind: 'ok', msg: 'Saved — live now.' })
  })

  const del = async () => {
    if (!key) return
    if (!(await confirmDialog({
      title: `Delete extension "${key}"?`,
      message: "This can't be undone.",
      confirmLabel: 'Delete',
      tone: 'danger',
    }))) return
    try { await api.deleteAuthoring(key); props.onChanged(); props.onBack() }
    catch (e: any) { setStatus({ kind: 'err', msg: e.message }) }
  }

  const title = key ? `Editing: ${key}` : 'New extension'

  return (
    <>
      <div className="page-head">
        <button className="ghost" onClick={props.onBack} style={{ marginBottom: 14 }}><Icon.arrowLeft size={14} /> Extensions</button>
        <div className="title-row">
          <div className="title-ic"><Icon.code size={19} /></div>
          <h1>{title}</h1>
        </div>
        <p>
          Write your extension in TypeScript against the Olisar SDK. It runs in a secure
          sandbox and can add tools, knowledge, and slash commands. Autocomplete is on.
        </p>
      </div>

      <Card>
        <Field label="Display name" desc="Shown in the catalog (optional; defaults to the manifest name).">
          <Text value={name} onChange={setName} placeholder="My extension" />
        </Field>
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden', marginTop: 8 }}>
          {Editor ? (
            <Editor
              height="480px"
              defaultLanguage="typescript"
              theme="olisar-dark"
              value={source}
              onChange={(v: string | undefined) => setSource(v ?? '')}
              // fixedOverflowWidgets keeps the autocomplete/hover popups from being clipped
              // by this card's `overflow: hidden` + rounded corners when they open near an edge.
              options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, tabSize: 2, padding: { top: 12 }, fixedOverflowWidgets: true }}
            />
          ) : (
            <div className="empty" style={{ padding: 56 }}>Loading editor…</div>
          )}
        </div>

        {manifest?.permissions?.length ? (
          <div style={{ marginTop: 14 }}>
            <div className="meta" style={{ marginBottom: 6 }}>Capabilities this extension uses:</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {manifest.permissions.map((p: string) => <span className="tag" key={p}>{p}</span>)}
            </div>
          </div>
        ) : null}

        {status && (
          <div className="meta" style={{ marginTop: 14, color: STATUS_COLOR[status.kind] }}>{status.msg}</div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 16 }}>
          <button className="primary" disabled={saver.busy} onClick={saver.run}>
            {saver.busy ? <><span className="spinner" /> Saving…</> : (key ? 'Save changes' : 'Create extension')}
          </button>
          <button className="ghost" onClick={validate}>Validate</button>
          {key && kind === 'user' && <button className="danger" onClick={del}>Delete</button>}
          {saver.saved && <span className="saved"><Icon.check size={15} weight="Bold" /> Saved — live now</span>}
          {saver.error && <span className="err">{saver.error}</span>}
        </div>
      </Card>
    </>
  )
}
