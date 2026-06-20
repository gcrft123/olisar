import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { DOCS, DOC_GROUPS } from './docs'
import { Icon, type IconName } from './icons'
import { Area, Card, Field, Markdown, Num, SaveBar, SaveDock, Select, Text, Toggle, headingsOf, useAsync, useEditable, useSaver } from './ui'

function PageHead(props: { icon: IconName; title: string; sub: string }) {
  const Glyph = Icon[props.icon]
  return (
    <div className="page-head">
      <div className="title-row">
        <div className="title-ic"><Glyph size={19} weight="Linear" /></div>
        <h1>{props.title}</h1>
      </div>
      <p>{props.sub}</p>
    </div>
  )
}

// ── Persona (identity + an enclosed test-chat panel) ───────────────────────
export function Persona() {
  const ed = useEditable<any>(api.getPersona)
  const { data, loading, setData } = ed
  const saver = useSaver(async () => { await api.putPersona(ed.data); ed.markSaved() })
  if (loading || !data) return <Spinner />
  const set = (k: string, v: any) => setData({ ...data, [k]: v })
  return (
    <>
      <PageHead icon="persona" title="Persona" sub="Olisar's Persona dictates who it is and how it will behave in your server. Try it out in the test chat panel alongside." />
      <div className="persona-grid">
        <Card title="Identity">
          <Field label="Name"><Text value={data.name} onChange={(v) => set('name', v)} /></Field>
          <Field label="System prompt" desc="Olisar's core character, lore, and rules. Safety guardrails are appended automatically.">
            <Area value={data.system_prompt} onChange={(v) => set('system_prompt', v)} rows={9} />
          </Field>
          <Field label="Style notes" desc="Tone and formatting guidance.">
            <Area value={data.tone_notes} onChange={(v) => set('tone_notes', v)} rows={5} />
          </Field>
          <Field label="Profile bio (About Me)" desc="Olisar's Discord bio. This applies bot wide, not per-server.">
            <Area value={data.desired_bio} onChange={(v) => set('desired_bio', v)} rows={3} />
          </Field>
        </Card>
        <SandboxPanel />
      </div>
      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
    </>
  )
}

// ── Test-chat panel (enclosed sandbox: persona + KB + tools, but no memory) ──
// Lives beside Identity on the Persona tab so persona edits can be tried live.
// Save the persona first — the sandbox reads the saved persona, not the draft.
type ChatMsg = { role: 'user' | 'assistant'; content: string }

function SandboxPanel() {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  // Keep the transcript pinned to the latest message as it grows / while thinking.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    const next: ChatMsg[] = [...messages, { role: 'user', content: text }]
    setMessages(next)
    setInput('')
    setBusy(true)
    setErr(null)
    try {
      const res = await api.sandboxChat(next.map((m) => ({ role: m.role, content: m.content })))
      setMessages([...next, { role: 'assistant', content: res?.reply || '…' }])
    } catch (e: any) {
      setErr(e?.message || 'Something went wrong — try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Test chat" hint="An enclosed sandbox with Olisar's full persona, knowledge base, and tools, but no memory. Nothing here is saved, and it never touches the server's glossary or chat history.">
      <div className="sandbox">
        <div className="sandbox-log" ref={logRef}>
          {messages.length === 0 && !busy && (
            <div className="sandbox-empty">
              Try out Olisar's persona without affecting server context. 
              Nothing here is saved.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={'sb-msg ' + m.role}>
              <div className="sb-who">{m.role === 'user' ? 'You' : 'Olisar'}</div>
              <div className="sb-bubble">
                {m.role === 'assistant' ? <Markdown md={m.content} /> : m.content}
              </div>
            </div>
          ))}
          {busy && (
            <div className="sb-msg assistant">
              <div className="sb-who">Olisar</div>
              <div className="sb-bubble sb-typing"><span /><span /><span /></div>
            </div>
          )}
        </div>
        {err && <div className="sandbox-err">{err}</div>}
        <div className="sandbox-input">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }}
            placeholder="Message Olisar…"
            rows={2}
            disabled={busy}
          />
          <div className="sandbox-actions">
            <button className="ghost" onClick={() => { setMessages([]); setErr(null) }} disabled={busy || messages.length === 0}>Clear</button>
            <button className="primary" onClick={() => void send()} disabled={busy || !input.trim()}>Send</button>
          </div>
        </div>
      </div>
    </Card>
  )
}

// ── Behavior (guild_config + proactivity) ──────────────────────────────────
export function Behavior() {
  const configEd = useEditable<any>(api.getConfig)
  const { data: models } = useAsync<any[]>(api.models)
  const proEd = useEditable<any>(api.getProactivity)
  const saver = useSaver(async () => {
    const cfg = configEd.data
    await api.putConfig({
      ...cfg,
      name_triggers: typeof cfg.name_triggers === 'string'
        ? cfg.name_triggers.split(',').map((s: string) => s.trim()).filter(Boolean)
        : cfg.name_triggers,
    })
    await api.putProactivity(proEd.data)
    configEd.markSaved(); proEd.markSaved()
  })
  if (configEd.loading || !configEd.data || proEd.loading || !proEd.data) return <Spinner />
  const data = configEd.data
  const pro = proEd.data
  const set = (k: string, v: any) => configEd.setData({ ...data, [k]: v })
  const setP = (k: string, v: any) => proEd.setData({ ...pro, [k]: v })
  const triggers = Array.isArray(data.name_triggers) ? data.name_triggers.join(', ') : data.name_triggers
  const modelOpts = (models ?? []).map((m) => ({ value: m.name, label: `${m.name} — ${m.label}` }))
  const qh = pro.quiet_hours || {}
  const quietOn = 'start' in qh
  const setQuiet = (next: any) => setP('quiet_hours', next)

  return (
    <>
      <PageHead icon="behavior" title="Behavior" sub="Control how and when Olisar participates in your server. Fallback message customization lives in Command replies." />
      <Card title="Triggers">
        <Field label="Name triggers" desc="Comma-separated. Including one of these words in a message addresses Olisar.">
          <Text value={triggers} onChange={(v) => set('name_triggers', v)} placeholder="olisar, oli" />
        </Field>
        <Field label="Reply in DMs"><Toggle value={data.reply_in_dms} onChange={(v) => set('reply_in_dms', v)} label="Answer direct messages" /></Field>
        <Field label="Loose messages" desc="Reply to all messages in talk-enabled channels without a trigger.">
          <Toggle value={data.loose_msg_enabled} onChange={(v) => set('loose_msg_enabled', v)} label="Join freely" />
        </Field>
      </Card>
      <Card title="Model & search">
        <Field label="Primary model" desc="The fallback chain starts here and walks down to the next best on a rate limit.">
          <Select value={data.default_model} onChange={(v) => set('default_model', v)} options={modelOpts.length ? modelOpts : [{ value: data.default_model, label: data.default_model }]} />
        </Field>
        <Field label="Web search (grounding)"><Toggle value={data.grounding_enabled} onChange={(v) => set('grounding_enabled', v)} label="Allow web search" /></Field>
        <Field label="Status & voice awareness" desc="Let Olisar check a member's live status/activity and who's in voice. Requires the Presence Intent in the Discord Developer Portal; run /privacy to see how Olisar handles your data.">
          <Toggle value={data.presence_tools_enabled} onChange={(v) => set('presence_tools_enabled', v)} label="Allow presence & voice lookups" />
        </Field>
        <div className="row">
          <Field label="Grounding daily cap"><Num value={data.grounding_daily_cap} onChange={(v) => set('grounding_daily_cap', v)} min={0} /></Field>
          <Field label="Summary token threshold"><Num value={data.summary_token_threshold} onChange={(v) => set('summary_token_threshold', v)} min={500} step={500} /></Field>
          <Field label="Glossary mine threshold"><Num value={data.glossary_mine_token_threshold} onChange={(v) => set('glossary_mine_token_threshold', v)} min={300} step={250} /></Field>
          <Field label="Persona rebuild (msgs)"><Num value={data.user_persona_msg_threshold} onChange={(v) => set('user_persona_msg_threshold', v)} min={5} /></Field>
        </div>
      </Card>
      <Card title="Proactivity" hint="Adjust when/if Olisar chimes in unprompted.">
        <Field label="Enabled"><Toggle value={pro.enabled} onChange={(v) => setP('enabled', v)} label="Let Olisar speak up on its own" /></Field>
        <Field label="Eagerness">
          <Select value={pro.level} onChange={(v) => setP('level', v)} options={[
            { value: 'low', label: 'low — rare, high-confidence' },
            { value: 'med', label: 'medium — balanced' },
            { value: 'high', label: 'high — chatty' },
            { value: 'off', label: 'off' },
          ]} />
        </Field>
        <Field label="Confidence threshold" desc="Minimum classifier confidence (0–1) before it replies.">
          <Num value={pro.confidence_threshold} onChange={(v) => setP('confidence_threshold', v)} min={0} max={1} step={0.05} />
        </Field>
      </Card>
      <Card title="Proactive rate control">
        <div className="row">
          <Field label="Global cooldown (s)"><Num value={pro.global_cooldown_sec} onChange={(v) => setP('global_cooldown_sec', v)} min={0} /></Field>
          <Field label="Channel cooldown (s)"><Num value={pro.channel_cooldown_sec} onChange={(v) => setP('channel_cooldown_sec', v)} min={0} /></Field>
          <Field label="Max per hour"><Num value={pro.max_per_hour} onChange={(v) => setP('max_per_hour', v)} min={0} /></Field>
        </div>
        <Field label="Quiet hours (UTC)" desc="Stay silent during these hours.">
          <Toggle value={quietOn} onChange={(v) => setQuiet(v ? { start: qh.start ?? 23, end: qh.end ?? 7 } : {})} label="Enable quiet hours" />
        </Field>
        {quietOn && (
          <div className="row">
            <Field label="From (hour)"><Num value={qh.start ?? 23} onChange={(v) => setQuiet({ ...qh, start: v })} min={0} max={23} /></Field>
            <Field label="To (hour)"><Num value={qh.end ?? 7} onChange={(v) => setQuiet({ ...qh, end: v })} min={0} max={23} /></Field>
          </div>
        )}
      </Card>
      <Card title="Passive reactions" hint="Passive reactions let Olisar decide to react to a message rather than reply. Useful when a message is overkill but an emoji reaction is warranted.">
        <Field label="Enabled"><Toggle value={pro.reaction_enabled} onChange={(v) => setP('reaction_enabled', v)} label="Let Olisar react with emoji" /></Field>
        <div className="row">
          <Field label="Channel cooldown (s)"><Num value={pro.reaction_cooldown_sec} onChange={(v) => setP('reaction_cooldown_sec', v)} min={0} /></Field>
          <Field label="Max per hour"><Num value={pro.reaction_max_per_hour} onChange={(v) => setP('reaction_max_per_hour', v)} min={0} /></Field>
        </div>
      </Card>
      <SaveDock dirty={configEd.dirty || proEd.dirty} saver={saver} onReset={() => { configEd.reset(); proEd.reset() }} />
    </>
  )
}

// ── Command replies ─────────────────────────────────────────────────────────
const MSG_LABELS: Record<string, string> = {
  ping: '/ping', watch: '/olisar watch', unwatch: '/olisar unwatch',
  channel_status: '/olisar status', learn_url: '/olisar learn-url',
  learn_site: '/olisar learn-site', learn_doc: '/olisar learn-doc',
  forget_me: '/forget-me', forget_me_optout: '/forget-me (opt-out line)',
  proactive: '/olisar proactive', privacy: '/privacy',
  rate_limit: 'When rate-limited', blank_fallback: 'When it draws a blank',
  access_denied: 'When access is denied',
}

export function Messages() {
  const { data, loading } = useAsync<any>(api.getMessages)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const base = useRef('')
  useEffect(() => {
    if (data) {
      const init: Record<string, string> = {}
      for (const k of Object.keys(data)) init[k] = data[k].custom ?? ''
      base.current = JSON.stringify(init)
      setEdits(init)
    }
  }, [data])
  const dirty = base.current !== '' && JSON.stringify(edits) !== base.current
  const saver = useSaver(async () => { await api.putMessages(edits); base.current = JSON.stringify(edits) })
  if (loading || !data) return <Spinner />

  return (
    <>
      <PageHead icon="messages" title="Command replies" sub="Customize the text Olisar sends when slash commands are run or it can't respond. Leave blank to use the default. Use {placeholders} where shown." />
      {Object.keys(data).map((key) => (
        <Card key={key} title={MSG_LABELS[key] ?? key}>
          <Area value={edits[key] ?? ''} onChange={(v) => setEdits({ ...edits, [key]: v })} rows={key === 'privacy' ? 6 : 2} placeholder={data[key].default} />
          <div className="code-default">default: {data[key].default}</div>
          {data[key].placeholders.length > 0 && (
            <div className="placeholders">placeholders: {data[key].placeholders.map((p: string) => <code key={p}>{`{${p}}`} </code>)}</div>
          )}
        </Card>
      ))}
      <SaveDock dirty={dirty} saver={saver} onReset={() => base.current && setEdits(JSON.parse(base.current))} />
    </>
  )
}

// ── Channels ────────────────────────────────────────────────────────────────
const MODE_OPTS = [
  { value: 'off', label: 'off — ignore' },
  { value: 'memory', label: 'memory — read only' },
  { value: 'respond', label: 'respond — talk only' },
  { value: 'both', label: 'both — read & talk' },
  { value: 'resource', label: 'resource — reference context' },
  { value: 'feed', label: 'feed — last 3, no summary' },
]

const INDEX_OPTS = [
  { value: 'on', label: 'indexed' },
  { value: 'off', label: 'not indexed' },
]

export function Channels() {
  const ed = useEditable<any[]>(api.getChannels)
  const [q, setQ] = useState('')
  const saver = useSaver(async () => {
    const origById = new Map((ed.baseline() ?? []).map((c: any) => [c.channel_id, c]))
    for (const c of ed.data ?? []) {
      const o = origById.get(c.channel_id)
      if (!o) continue
      if (c.mode !== o.mode) await api.putChannel({ channel_id: c.channel_id, mode: c.mode })
      if (c.indexed !== o.indexed) await api.putChannel({ channel_id: c.channel_id, indexed: c.indexed })
    }
    ed.markSaved()
  })
  const patchRow = (id: number, patch: any) =>
    ed.setData((prev: any[] | null) => (prev ?? []).map((c) => (c.channel_id === id ? { ...c, ...patch } : c)))
  if (ed.loading) return <Spinner />
  const rows = ed.data ?? []
  const configured = rows.filter((c) => c.mode !== 'off').length
  const term = q.trim().toLowerCase()
  const shown = term ? rows.filter((c) => (c.name || c.channel_id).toLowerCase().includes(term)) : rows

  return (
    <>
      <PageHead icon="channels" title="Channels" sub="Customize how Olisar treats each of your channels." />
      <Card title="What the modes mean">
        <div className="mode-legend">
          <div><span className="tag">memory</span> reads &amp; remembers; doesn't speak </div>
          <div><span className="tag">respond</span> speaks; doesn't read or remember</div>
          <div><span className="tag">both</span> reads, remembers &amp; speaks</div>
          <div><span className="tag">resource</span> durable reference content Olisar always carries (e.g. #rules, #roles-list)</div>
          <div><span className="tag">feed</span> remembers just the last 3 messages without summaries; doesn't speak (e.g. #announcements, #game-news)</div>
          <div><span className="tag">off</span> ignored entirely</div>
        </div>
        <div className="hint">Indexing is separate: it controls whether a channel's messages go into the server-wide <b>search index</b> (what <code>search_messages</code> looks through). Turn it off to exclude a channel and wipe its currently indexed messages. </div>
      </Card>
      <Card title={`Channels — ${configured} configured`}>
        {rows.length === 0 ? (
          <div className="empty">No channels synced yet. The bot populates this list shortly after it starts; you can also run <code>/olisar watch</code> in a channel.</div>
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <Text value={q} onChange={setQ} placeholder="Filter channels…" />
            </div>
            {shown.map((c) => (
              <div className="list-row" key={c.channel_id}>
                <div className="grow">
                  <div className="title">#{c.name} {c.kind === 'forum' && <span className="tag">forum</span>}</div>
                  {c.category && <div className="meta">{c.category}</div>}
                </div>
                <div style={{ width: 180 }}>
                  <Select value={c.mode} options={MODE_OPTS} onChange={(v) => patchRow(c.channel_id, { mode: v })} />
                </div>
                <div style={{ width: 140, marginLeft: 8 }}>
                  <Select
                    value={c.indexed === false ? 'off' : 'on'}
                    options={INDEX_OPTS}
                    onChange={(v) => patchRow(c.channel_id, { indexed: v === 'on' })}
                  />
                </div>
              </div>
            ))}
            {shown.length === 0 && <div className="empty">No channels match “{q}”.</div>}
          </>
        )}
      </Card>
      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
    </>
  )
}

// ── Access (role-based) ──────────────────────────────────────────────────────
const ACCESS_OPTS = [
  { value: 'open', label: 'Open' },
  { value: 'allow', label: 'Allowed' },
  { value: 'block', label: 'Blocked' },
]

export function Access() {
  const ed = useEditable<any>(api.getConfig)
  const { data: roles, loading: lr } = useAsync<any[]>(api.getRoles)
  const [q, setQ] = useState('')
  const config = ed.data
  const setConfig = ed.setData
  const saver = useSaver(async () => {
    await api.putConfig({
      allowed_role_ids: ed.data.allowed_role_ids ?? [],
      blocked_role_ids: ed.data.blocked_role_ids ?? [],
    })
    ed.markSaved()
  })
  if (ed.loading || lr || !config) return <Spinner />

  const allowed: string[] = config.allowed_role_ids ?? []
  const blocked: string[] = config.blocked_role_ids ?? []
  const stateOf = (id: string) => (blocked.includes(id) ? 'block' : allowed.includes(id) ? 'allow' : 'open')
  const setState = (id: string, s: string) => {
    const a = new Set(allowed)
    const b = new Set(blocked)
    a.delete(id); b.delete(id)
    if (s === 'allow') a.add(id)
    if (s === 'block') b.add(id)
    setConfig({ ...config, allowed_role_ids: [...a], blocked_role_ids: [...b] })
  }

  const rows = roles ?? []
  const term = q.trim().toLowerCase()
  const shown = term ? rows.filter((r) => (r.name || r.role_id).toLowerCase().includes(term)) : rows
  const summary = allowed.length
    ? 'Restricted: only allowed roles (and server admins) can use Olisar.'
    : blocked.length
      ? 'Open except blocked: everyone can use Olisar except the blocked roles.'
      : 'Open to everyone — no role restrictions are set.'

  return (
    <>
      <PageHead icon="access" title="Access" sub="Choose which roles have access to Olisar in chat and via slash commands like /ask. Server admins always have access and /privacy &amp; /forget-me stay open to everyone." />
      <Card title="How access works">
        <div className="mode-legend">
          <div><span className="tag">Allowed</span> if any role is marked allowed, only those roles (and admins) can use Olisar</div>
          <div><span className="tag">Blocked</span> these roles can never use Olisar even if they also have an allowed role</div>
          <div><span className="tag">Open</span> unset — this role adds no restriction</div>
        </div>
        <div className="hint">{summary}</div>
      </Card>
      <Card title={`Roles (${rows.length})`}>
        {rows.length === 0 ? (
          <div className="empty">No roles synced yet. The bot populates this list shortly after it starts.</div>
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <Text value={q} onChange={setQ} placeholder="Filter roles…" />
            </div>
            {shown.map((r) => (
              <div className="list-row" key={r.role_id}>
                <div className="grow">
                  <div className="title">
                    <span
                      style={{
                        display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
                        background: r.color || 'var(--muted, #888)', marginRight: 8, verticalAlign: 'middle',
                      }}
                    />
                    {r.name}
                  </div>
                </div>
                <div style={{ width: 220 }}>
                  <Select value={stateOf(r.role_id)} options={ACCESS_OPTS} onChange={(v) => setState(r.role_id, v)} />
                </div>
              </div>
            ))}
            {shown.length === 0 && <div className="empty">No roles match “{q}”.</div>}
          </>
        )}
      </Card>
      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
    </>
  )
}

// ── Knowledge (knowledge base + glossary) ───────────────────────────────────
// The server-wide message search index: a re-index action and per-channel backfill
// progress. Polls while any channel is still queued/indexing.
function SearchIndexCard() {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    const load = () => api.reindexStatus().then((d) => { if (alive) setData(d) }).catch(() => {})
    load()
    const id = setInterval(load, 3500)
    return () => { alive = false; clearInterval(id) }
  }, [])
  const start = async () => {
    setBusy(true)
    try { await api.reindex(); setData(await api.reindexStatus()) } catch { /* ignore */ } finally { setBusy(false) }
  }
  const clear = async () => {
    if (!window.confirm('Clear the entire message search index? New posts keep indexing live, and "Re-index all" rebuilds history.')) return
    setBusy(true)
    try { await api.clearIndex(); setData(await api.reindexStatus()) } catch { /* ignore */ } finally { setBusy(false) }
  }
  const pct = data && data.total ? Math.round((data.done / data.total) * 100) : 0
  // Active (queued/indexing) first, then done — channels stay listed with their count.
  const rank: Record<string, number> = { indexing: 0, queued: 1, done: 2 }
  const channels = [...(data?.channels || [])].sort(
    (a: any, b: any) => (rank[a.status] - rank[b.status]) || (b.indexed - a.indexed)
  )
  return (
    <Card title="Message search index" hint="A server-wide index of past messages so Olisar can search history. Re-indexing rebuilds it from each channel's history in the background.">
      {!data ? <div className="empty">Loading…</div> : (
        <>
          <div className="reindex-top">
            <div className="reindex-stat">
              <b>{data.done}</b> / {data.total} channels indexed
              <span className="rx-dim"> · {data.indexed_messages.toLocaleString()} messages</span>
            </div>
            <div className="reindex-actions">
              <button className="primary sm" onClick={start} disabled={busy}>
                <Icon.refresh size={14} /> {busy ? 'Working…' : 'Re-index all'}
              </button>
              <button className="danger sm" onClick={clear} disabled={busy || data.indexed_messages === 0}>
                <Icon.trash size={14} /> Clear index
              </button>
            </div>
          </div>
          {/* The overall bar only while there's work in flight; hidden once complete. */}
          {data.running && <div className="progress"><div className="progress-fill" style={{ width: pct + '%' }} /></div>}
          {channels.length > 0 && (
            <div className="reindex-list">
              {channels.map((c: any) => (
                <div className="reindex-row" key={c.channel_id}>
                  <span className="rx-name">#{c.name}</span>
                  <span className="rx-count">{c.indexed.toLocaleString()}<span className="rx-dim"> msgs</span></span>
                  <span className={'rx-chip ' + c.status}>
                    {c.status === 'done'
                      ? <><Icon.check size={12} weight="Bold" /> indexed</>
                      : c.status === 'indexing' ? 'indexing…' : 'queued'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  )
}

export function Knowledge() {
  const { data, loading, reload } = useAsync<any[]>(api.getKnowledge)
  const [type, setType] = useState('url')
  const [uri, setUri] = useState('')
  const [depth, setDepth] = useState(1)
  const [maxPages, setMaxPages] = useState(25)
  const adder = useSaver(async () => {
    await api.addSource({ type, uri, crawl_depth: depth, max_pages: maxPages })
    setUri('')
    reload()
  })

  const { data: facts, loading: lf, reload: reloadFacts } = useAsync<any[]>(api.getFacts)
  const [subject, setSubject] = useState('')
  const [fact, setFact] = useState('')
  const factAdder = useSaver(async () => {
    await api.addFact({ subject: subject.trim() || null, fact })
    setSubject(''); setFact('')
    reloadFacts()
  })

  if (loading || lf) return <Spinner />
  const rows = data ?? []
  const factRows = facts ?? []
  return (
    <>
      <PageHead icon="knowledge" title="Knowledge" sub="What Olisar knows about your world. The knowledge base holds webpages and documents it can reference at any time and the glossary stores bits of server-specific info which is updated regularly by Olisar." />
      <Card title="Add a source" hint="A webpage or a crawled site Olisar can reference. Upload documents via /olisar learn-doc in Discord.">
        <div className="row">
          <Field label="Type"><Select value={type} onChange={setType} options={[{ value: 'url', label: 'single page' }, { value: 'website', label: 'crawl a website' }]} /></Field>
          <Field label="URL"><Text value={uri} onChange={setUri} placeholder="https://…" /></Field>
        </div>
        {type === 'website' && (
          <div className="row">
            <Field label="Crawl depth (0–3)"><Num value={depth} onChange={setDepth} min={0} max={3} /></Field>
            <Field label="Max pages"><Num value={maxPages} onChange={setMaxPages} min={1} max={100} /></Field>
          </div>
        )}
        <SaveBar saver={adder} label="Add & ingest" />
      </Card>
      <Card title={`Sources (${rows.length})`}>
        {rows.length === 0 && <div className="empty">Nothing yet.</div>}
        {rows.map((s) => (
          <div className="list-row" key={s.id}>
            <div className="grow">
              <div className="title">{s.title || s.uri}</div>
              <div className="meta">
                {s.type} · {s.chunks} chunks
                {s.error && <span className="meta-warn"><Icon.warn size={13} weight="Bold" /> {s.error}</span>}
              </div>
            </div>
            <span className={'badge ' + s.status}>{s.status}</span>
            <button className="danger sm" onClick={async () => { await api.deleteSource(s.id); reload() }}>
              <Icon.trash size={15} /> Remove
            </button>
          </div>
        ))}
      </Card>
      <SearchIndexCard />
      <Card title="Add a glossary fact" hint="Durable server lore and bits of info. Olisar carries these into every reply and also mines them automatically when it summarizes a channel. Subject is the term (optional) and the fact is one short, standalone statement.">
        <div className="row">
          <Field label="Subject"><Text value={subject} onChange={setSubject} placeholder="MN" /></Field>
          <div style={{ flex: 3 }}>
            <Field label="Fact"><Text value={fact} onChange={setFact} placeholder="MN is Movie Night, our Friday watch-party in #cinema" /></Field>
          </div>
        </div>
        <SaveBar saver={factAdder} label="Add fact" />
      </Card>
      <Card title={`Glossary (${factRows.length})`}>
        {factRows.length === 0 && <div className="empty">Nothing learned yet. Olisar fills this in as it summarizes active channels, or add the first fact above.</div>}
        {factRows.map((f) => (
          <div className="list-row" key={f.id}>
            <div className="grow">
              <div className="title">{f.fact}</div>
              <div className="meta">
                {f.subject && <span className="tag">{f.subject}</span>}
                {f.mentions > 1 ? `seen ${f.mentions}×` : 'seen once'}
              </div>
            </div>
            <button className="danger sm" onClick={async () => { await api.deleteFact(f.id); reloadFacts() }}>
              <Icon.trash size={15} /> Delete
            </button>
          </div>
        ))}
      </Card>
    </>
  )
}

// ── Extensions ───────────────────────────────────────────────────────────────
function WelcomeConfig(props: { enabled: boolean }) {
  const { data: chans } = useAsync<any[]>(api.getChannels)
  const { data: loaded } = useAsync<any>(() => api.getExtensionSettings('welcome'))
  const [enabled, setEnabled] = useState(false)
  const [channelId, setChannelId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [init, setInit] = useState(false)
  useEffect(() => {
    if (loaded && !init) {
      setEnabled(props.enabled)
      setChannelId(String(loaded.settings?.channel_id || ''))
      setPrompt(loaded.settings?.prompt || '')
      setInit(true)
    }
  }, [loaded, init, props.enabled])
  const saver = useSaver(async () => {
    await api.putExtensionSettings('welcome', { channel_id: channelId, prompt })
    await api.putExtension({ key: 'welcome', enabled })
  })
  const opts = [{ value: '', label: '— pick a channel —' }, ...((chans ?? []).map((c: any) => ({ value: String(c.channel_id), label: '#' + (c.name || c.channel_id) })))]
  return (
    <Card title="Welcome message" hint="Greets new members in Olisar's voice plus your prompt. Use {user} for the new member.">
      <Field label="Enabled"><Toggle value={enabled} onChange={setEnabled} label="Greet new members on join" /></Field>
      <Field label="Channel"><Select value={channelId} onChange={setChannelId} options={opts} /></Field>
      <Field label="Prompt" desc="Layered on top of the persona — e.g. 'warmly welcome {user} and ask what brought them here', or 'roast {user} on their username'.">
        <Area value={prompt} onChange={setPrompt} rows={3} />
      </Field>
      <SaveBar saver={saver} label="Save welcome" />
    </Card>
  )
}

export function Extensions() {
  const ed = useEditable<any[]>(api.getExtensions)
  const saver = useSaver(async () => {
    const orig = new Map((ed.baseline() ?? []).map((e: any) => [e.key, e.enabled]))
    for (const e of ed.data ?? []) {
      if (e.enabled !== orig.get(e.key)) await api.putExtension({ key: e.key, enabled: e.enabled })
    }
    ed.markSaved()
  })
  const toggle = (key: string, v: boolean) =>
    ed.setData((prev: any[] | null) => (prev ?? []).map((e) => (e.key === key ? { ...e, enabled: v } : e)))
  if (ed.loading) return <Spinner />
  const rows = ed.data ?? []
  // 'welcome' has its own panel (with its own enable toggle), so keep it out of the list.
  const cats = Array.from(new Set(rows.filter((e) => e.key !== 'welcome').map((e) => e.category)))
  return (
    <>
      <PageHead icon="extensions" title="Extensions" sub="Togglable packages of extra features." />
      {rows.length === 0 && (
        <Card title="Extensions"><div className="empty">No extensions registered.</div></Card>
      )}
      {cats.map((cat) => (
        <Card key={cat} title={cat}>
          {rows.filter((e) => e.category === cat && e.key !== 'welcome').map((e) => (
            <div className="list-row" key={e.key}>
              <div className="grow">
                <div className="title">{e.name}</div>
                <div className="meta">{e.description}</div>
              </div>
              <Toggle value={e.enabled} onChange={(v) => toggle(e.key, v)} />
            </div>
          ))}
        </Card>
      ))}
      {rows.some((e) => e.key === 'welcome') && <WelcomeConfig enabled={!!rows.find((e) => e.key === 'welcome')?.enabled} />}
      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
    </>
  )
}

// ── Docs (OpenClaw-style: left nav · content · on-this-page) ─────────────────
export function Docs(props: { onNavigate?: (tab: string) => void }) {
  const [active, setActive] = useState(DOCS[0].id)
  const [q, setQ] = useState('')
  useEffect(() => { window.scrollTo({ top: 0 }) }, [active])

  const section = DOCS.find((s) => s.id === active) ?? DOCS[0]
  // Linear order follows the grouped sidebar, not the raw DOCS array, so the
  // prev/next buttons match what's shown in the nav.
  const order = DOC_GROUPS.flatMap((g) => g.ids)
  const oidx = order.indexOf(section.id)
  const prev = DOCS.find((s) => s.id === order[oidx - 1])
  const next = DOCS.find((s) => s.id === order[oidx + 1])
  const headings = headingsOf(section.body)

  const term = q.trim().toLowerCase()
  const matches = (s: { title: string; body: string }) =>
    !term || s.title.toLowerCase().includes(term) || s.body.toLowerCase().includes(term)

  // In-doc links: `tab:id` jumps to a dashboard settings tab; `#id` jumps to another
  // doc page (or scrolls to a heading on the current one).
  const goLink = (url: string) => {
    if (url.startsWith('tab:')) { props.onNavigate?.(url.slice(4)); return }
    const id = url.replace(/^#/, '')
    if (DOCS.some((s) => s.id === id)) setActive(id)
    else document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className={'docs-shell' + (headings.length ? '' : ' no-toc')}>
      <aside className="docs-nav">
        <input
          className="docs-search"
          type="text"
          value={q}
          placeholder="Search docs…"
          onChange={(e) => setQ(e.target.value)}
        />
        {DOC_GROUPS.map((g) => {
          const items = g.ids
            .map((id) => DOCS.find((s) => s.id === id))
            .filter((s): s is (typeof DOCS)[number] => !!s && matches(s))
          if (!items.length) return null
          return (
            <div className="docs-group" key={g.label}>
              <div className="docs-nav-label">{g.label}</div>
              {items.map((s) => (
                <div
                  key={s.id}
                  className={'docs-nav-item' + (s.id === active ? ' active' : '')}
                  onClick={() => setActive(s.id)}
                >
                  {s.title}
                </div>
              ))}
            </div>
          )
        })}
      </aside>

      <main className="docs-content">
        <div className="docs-eyebrow">Olisar documentation</div>
        <h1 className="docs-title">{section.title}</h1>
        <Markdown md={section.body} onDocLink={goLink} />
        <div className="docs-prevnext">
          {prev ? (
            <button className="ghost" onClick={() => setActive(prev.id)}>← {prev.title}</button>
          ) : <span />}
          {next ? (
            <button className="ghost" onClick={() => setActive(next.id)}>{next.title} →</button>
          ) : <span />}
        </div>
      </main>

      {headings.length > 0 && (
        <aside className="docs-toc">
          <div className="docs-toc-label">On this page</div>
          {headings.map((h) => (
            <a key={h.slug} href={`#${h.slug}`} className={'lvl' + h.level}>{h.text}</a>
          ))}
        </aside>
      )}
    </div>
  )
}

// ── Members (the profiles Olisar builds) ─────────────────────────────────────
const MAX_ROLES = 3  // cap role chips per card so they don't overflow

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) } catch { return '—' }
}

export function Members() {
  const { data, loading } = useAsync<any[]>(api.getProfiles)
  const [q, setQ] = useState('')
  const [building, setBuilding] = useState<Record<string, boolean>>({})
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [errs, setErrs] = useState<Record<string, string>>({})
  if (loading) return <Spinner />
  const rows = data ?? []
  const impressionOf = (p: any): string => overrides[p.user_id] ?? p.impression
  const learned = rows.filter((r) => impressionOf(r) || r.memories?.length).length
  const term = q.trim().toLowerCase()
  const shown = term
    ? rows.filter((r) =>
        r.display_name.toLowerCase().includes(term)
        || (r.roles || []).some((x: string) => x.toLowerCase().includes(term))
        || (impressionOf(r) || '').toLowerCase().includes(term))
    : rows

  const build = async (uid: string) => {
    setBuilding({ ...building, [uid]: true })
    setErrs({ ...errs, [uid]: '' })
    try {
      const r = await api.buildImpression(uid)
      if (r.ok) setOverrides((o) => ({ ...o, [uid]: r.impression }))
      else setErrs((e) => ({ ...e, [uid]: r.error || 'Could not build.' }))
    } catch (e: any) {
      setErrs((er) => ({ ...er, [uid]: e?.message || 'Request failed.' }))
    } finally {
      setBuilding((b) => ({ ...b, [uid]: false }))
    }
  }

  return (
    <>
      <PageHead
        icon="members"
        title="Members"
        sub="The private impression Olisar forms of each member from what they say, their roles, and facts it remembers. Anyone can wipe theirs with /forget-me."
      />
      <Card title={`${rows.length} known · ${learned} with an impression`}>
        <Text value={q} onChange={setQ} placeholder="Filter by name, role, or impression…" />
      </Card>
      {rows.length === 0 && <Card title="Profiles"><div className="empty">No member profiles yet. Olisar builds them as people talk in channels it remembers.</div></Card>}
      {rows.length > 0 && shown.length === 0 && <Card title="Profiles"><div className="empty">No members match “{q}”.</div></Card>}
      <div className="member-grid">
        {shown.map((p) => {
          const roles: string[] = p.roles || []
          const extra = roles.length - MAX_ROLES
          const impression = impressionOf(p)
          const busy = !!building[p.user_id]
          return (
            <div className="member-card" key={p.user_id}>
              <div className="member-name">{p.display_name}</div>
              {roles.length > 0 && (
                <div className="member-roles">
                  {roles.slice(0, MAX_ROLES).map((r) => <span className="tag" key={r}>{r}</span>)}
                  {extra > 0 && <span className="tag more" title={roles.slice(MAX_ROLES).join(', ')}>+{extra}</span>}
                </div>
              )}
              {impression
                ? <div className="member-impression">{impression}</div>
                : <div className="member-none">No impression yet.</div>}
              {p.memories?.length > 0 && (
                <div className="member-memories">
                  {p.memories.map((m: any, i: number) => (
                    <div className="mem" key={i}><span className={'badge ' + m.kind}>{m.kind}</span> {m.content}</div>
                  ))}
                </div>
              )}
              <div className="member-actions">
                <button className="ghost sm" disabled={busy} onClick={() => build(p.user_id)}>
                  {busy ? 'Building…' : impression ? 'Rebuild impression' : 'Create impression'}
                </button>
                {errs[p.user_id] && <span className="err sm">{errs[p.user_id]}</span>}
              </div>
              <div className="member-meta">last seen {fmtDate(p.last_seen)}</div>
            </div>
          )
        })}
      </div>
    </>
  )
}

// ── API keys ────────────────────────────────────────────────────────────────
// `value` autofills the field from the operator's environment on a local request
// (the backend only sends it over loopback) — same as the first-run wizard.
type KeyStatus = { dashboard: boolean; env: boolean; value?: string }

function KeyField(props: {
  fieldKey: string
  label: string
  desc: ReactNode
  status: KeyStatus
  value: string
  example?: string
  onChange: (v: string) => void
  onClear: () => void
}) {
  const s = props.status
  // Same field stylization as the first-run wizard: a plain styled text input
  // (mono), with an example placeholder when nothing is set yet.
  const placeholder = s.dashboard
    ? '•••••••• — set; leave blank to keep'
    : s.env
      ? 'Using the .env value — paste to override'
      : props.example || 'Paste your key'
  return (
    <Field label={props.label} desc={props.desc}>
      <input
        type="text"
        autoComplete="off"
        spellCheck={false}
        className="mono"
        placeholder={placeholder}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      />
      <div className="key-status">
        {s.dashboard ? (
          <>
            <span className="badge ready">set in dashboard</span>
            <button className="danger sm" onClick={props.onClear}>
              <Icon.trash size={14} /> Clear
            </button>
          </>
        ) : s.env ? (
          <span className="badge">env fallback</span>
        ) : (
          <span className="badge missing">not set</span>
        )}
      </div>
    </Field>
  )
}

export function ApiKeys() {
  const { data, loading, reload } = useAsync<Record<string, KeyStatus>>(api.getKeys)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const saver = useSaver(async () => {
    const body: Record<string, string> = {}
    for (const [k, v] of Object.entries(edits)) if (v.trim()) body[k] = v.trim()
    await api.putKeys(body)
    setEdits({})
    reload()
  })
  if (loading || !data) return <Spinner />
  const set = (k: string, v: string) => setEdits({ ...edits, [k]: v })
  // Autofilled from the environment (local-only) unless the operator has edited the field.
  const val = (k: string) => edits[k] ?? (data[k]?.value ?? '')
  const clear = async (k: string) => { await api.clearKey(k); reload() }
  const st = (k: string): KeyStatus => data[k] ?? { dashboard: false, env: false }
  const A = (href: string, text: string) => <a href={href} target="_blank" rel="noreferrer">{text}</a>
  const dirty = Object.values(edits).some((v) => v.trim() !== '')

  return (
    <>
      <PageHead
        icon="keys"
        title="API keys"
        sub="Bring your own keys. A key entered here is stored for this server and values are write-only meaning they never come back to a remote browser."
      />

      <Card
        title="Google Gemini"
        hint="Powers everything Olisar says — chat, memory, summaries, and image understanding. Required. The free tier is enough to run the bot (it just rate-limits under load)."
      >
        <KeyField
          fieldKey="gemini_api_key"
          label="Gemini API key"
          desc={<>Create a free key in {A('https://aistudio.google.com/apikey', 'Google AI Studio → Get API key')}.</>}
          status={st('gemini_api_key')}
          value={val('gemini_api_key')}
          example="AIza…"
          onChange={(v) => set('gemini_api_key', v)}
          onClear={() => clear('gemini_api_key')}
        />
      </Card>

      <Card
        title="Cloudflare Workers AI"
        hint="Optional — enables image generation (FLUX). Without it, Olisar simply says it can't make images. Needs your account ID and an API token with the Workers AI permission."
      >
        <KeyField
          fieldKey="cloudflare_account_id"
          label="Account ID"
          desc={<>Find it in the {A('https://dash.cloudflare.com/', 'Cloudflare dashboard')} → any domain's Overview, or on the Workers &amp; Pages page (right sidebar).</>}
          status={st('cloudflare_account_id')}
          value={val('cloudflare_account_id')}
          example="cloudflare account id"
          onChange={(v) => set('cloudflare_account_id', v)}
          onClear={() => clear('cloudflare_account_id')}
        />
        <KeyField
          fieldKey="cloudflare_api_token"
          label="API token"
          desc={<>Create one at {A('https://dash.cloudflare.com/profile/api-tokens', 'My Profile → API Tokens → Create Token')} with the <strong>Workers AI</strong> permission (Read is enough).</>}
          status={st('cloudflare_api_token')}
          value={val('cloudflare_api_token')}
          example="cloudflare api token"
          onChange={(v) => set('cloudflare_api_token', v)}
          onClear={() => clear('cloudflare_api_token')}
        />
      </Card>

      <Card
        title="UEX (Star Citizen)"
        hint="Optional — only used by the Star Citizen extension. The UEX tools already work on public endpoints; a token just raises the rate limits."
      >
        <KeyField
          fieldKey="uex_api_key"
          label="UEX API token"
          desc={<>Register an app at {A('https://uexcorp.uk/api', 'uexcorp.uk → API')} to get a bearer token. Leave blank to use UEX's public access.</>}
          status={st('uex_api_key')}
          value={val('uex_api_key')}
          example="uex token"
          onChange={(v) => set('uex_api_key', v)}
          onClear={() => clear('uex_api_key')}
        />
      </Card>

      <SaveDock dirty={dirty} saver={saver} onReset={() => setEdits({})} label="Save keys" />
    </>
  )
}

// ── Usage ───────────────────────────────────────────────────────────────────
export function Usage() {
  const { data, loading } = useAsync<any>(api.getStats)
  if (loading || !data) return <Spinner />
  const byModel = data.by_model ?? {}
  return (
    <>
      <PageHead icon="usage" title="Usage" sub="Gemini free-tier usage. Today's counts and lifetime totals per model." />
      <Card title="Today">
        <div className="stat-grid">
          <div className="stat"><div className="n">{data.today.requests}</div><div className="k">requests</div></div>
          <div className="stat"><div className="n">{data.today.grounding}</div><div className="k">web searches</div></div>
        </div>
      </Card>
      <Card title="By model (lifetime)">
        {Object.keys(byModel).length === 0 && <div className="empty">No usage recorded yet.</div>}
        {Object.entries(byModel).map(([model, agg]: any) => (
          <div className="list-row" key={model}>
            <div className="grow"><div className="title mono">{model}</div></div>
            <div className="meta">{agg.requests} reqs · {agg.tokens.toLocaleString()} tokens · {agg.grounding} searches</div>
          </div>
        ))}
      </Card>
    </>
  )
}

function Spinner() {
  return <div className="muted" style={{ padding: 20 }}>Loading…</div>
}
