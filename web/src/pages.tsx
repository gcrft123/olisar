import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { DOCS, DOC_GROUPS } from './docs'
import { Icon, CloseX, type IconName } from './icons'
import { confirmDialog, promptDialog, toast } from './overlays'
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
      <PageHead icon="persona" title="Persona" sub="Olisar's Persona dictates who it is and how it behaves in your server. Try changes live in the Test chat — open it from the button in the corner." />
      <Card title="Identity">
        <Field label="Name"><Text value={data.name} onChange={(v) => set('name', v)} /></Field>
        <Field label="System prompt" desc="Olisar's core character, lore, and rules. Safety guardrails are appended automatically.">
          <Area value={data.system_prompt} onChange={(v) => set('system_prompt', v)} rows={9} />
        </Field>
      </Card>
      <div className="grid2">
        <Card title="Style notes" hint="Olisar's voice, tone, and formatting.">
          <Area value={data.tone_notes} onChange={(v) => set('tone_notes', v)} rows={6} />
        </Card>
        <Card
          title="About Me"
          hint={
            <>
              Olisar's public Discord bio (bot-wide, not per-server). To keep Olisar free for everyone, an attribution is added automatically below your text (and stays even if you leave this blank). Your own text: {(data.desired_bio || '').length}/300.
            </>
          }
        >
          <Area value={data.desired_bio} onChange={(v) => set('desired_bio', v)} rows={6} maxLength={300} />
        </Card>
      </div>
      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
      <TestChatDrawer />
    </>
  )
}

// ── Test chat (enclosed sandbox: persona + KB + tools, but no memory) ──
// A slide-over drawer launched from the Persona page so persona edits can be tried
// live. Save the persona first — the sandbox reads the saved persona, not the draft.
type ChatMsg = { role: 'user' | 'assistant'; content: string }

// The chat itself (transcript + composer); the drawer below provides the shell.
function SandboxChat() {
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
          <button className="ghost icon-btn" onClick={() => { setMessages([]); setErr(null) }} disabled={busy || messages.length === 0} data-tip="Clear chat" aria-label="Clear chat"><Icon.eraser size={16} /></button>
          <button className="primary icon-btn" onClick={() => void send()} disabled={busy || !input.trim()} data-tip="Send" aria-label="Send"><Icon.send size={16} /></button>
        </div>
      </div>
    </div>
  )
}

// Slide-over Test chat: a corner launcher opens a right-docked drawer with a dimmed
// backdrop (closes on the backdrop, the close button, or Escape). Always mounted so it
// slides rather than pops and the transcript survives close/reopen.
function TestChatDrawer() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <>
      <button className="testchat-fab" onClick={() => setOpen(true)} aria-label="Open test chat">
        <Icon.sandbox size={17} weight="Bold" /> Test chat
      </button>
      <div className={'chatdrawer-backdrop' + (open ? ' open' : '')} onClick={() => setOpen(false)} aria-hidden="true" />
      <aside className={'chatdrawer' + (open ? ' open' : '')} role="dialog" aria-label="Test chat" aria-hidden={!open}>
        <div className="chatdrawer-head">
          <div className="chatdrawer-titles">
            <div className="chatdrawer-title">Test chat</div>
            <div className="chatdrawer-sub">An enclosed sandbox with the full persona, knowledge base, and tools, but no memory.</div>
          </div>
          <button className="ghost icon-btn sm" onClick={() => setOpen(false)} data-tip="Close" aria-label="Close test chat"><CloseX size={16} /></button>
        </div>
        <SandboxChat />
      </aside>
    </>
  )
}

// ── Behavior (guild_config + proactivity) ──────────────────────────────────
// Mention types Olisar can be barred from pinging (multi-choice).
const MENTION_OPTS = [
  { value: 'everyone', label: '@everyone' },
  { value: 'here', label: '@here' },
  { value: 'roles', label: 'All roles' },
]
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
      <div className="cols2">
        <div className="col">
      <Card title="Engagement" hint="When and where Olisar joins the conversation.">
        <Field label="Name triggers" desc="Comma-separated. Including one of these words in a message addresses Olisar.">
          <Text value={triggers} onChange={(v) => set('name_triggers', v)} placeholder="olisar, oli" />
        </Field>
        <Field label="Reply in DMs"><Toggle value={data.reply_in_dms} onChange={(v) => set('reply_in_dms', v)} label="Answer direct messages" /></Field>
        <Field label="Loose messages" desc="Reply to all messages in talk-enabled channels without a trigger.">
          <Toggle value={data.loose_msg_enabled} onChange={(v) => set('loose_msg_enabled', v)} label="Join freely" />
        </Field>
        <Field label="Don't let Olisar ping" desc="Olisar won't ping these in its replies even if it writes the mention.">
          <div className="choice-row">
            {MENTION_OPTS.map((o) => {
              const on = (data.blocked_mentions || []).includes(o.value)
              return (
                <label key={o.value} className={'choice' + (on ? ' on' : '')}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => {
                      const cur = new Set<string>(data.blocked_mentions || [])
                      if (on) cur.delete(o.value); else cur.add(o.value)
                      set('blocked_mentions', [...cur])
                    }}
                  />
                  {o.label}
                </label>
              )
            })}
          </div>
        </Field>
      </Card>
      <Card title="Model & tools" hint="The model Olisar runs on and the live lookups it may use while replying.">
        <Field label="Primary model" desc="The fallback chain starts here and walks down to the next best on a rate limit.">
          <Select value={data.default_model} onChange={(v) => set('default_model', v)} options={modelOpts.length ? modelOpts : [{ value: data.default_model, label: data.default_model }]} />
        </Field>
        <Field label="Web search (grounding)" desc="Let Olisar look things up on the web to ground its answers.">
          <Toggle value={data.grounding_enabled} onChange={(v) => set('grounding_enabled', v)} label="Allow web search" />
        </Field>
        <Field label="Grounding daily cap" desc="The most web-grounded answers Olisar will run in a day.">
          <Num value={data.grounding_daily_cap} onChange={(v) => set('grounding_daily_cap', v)} min={0} />
        </Field>
        <Field label="Status & voice awareness" desc="Let Olisar check a member's live status/activity and who's in voice. Requires the Presence Intent in the Discord Developer Portal.">
          <Toggle value={data.presence_tools_enabled} onChange={(v) => set('presence_tools_enabled', v)} label="Allow presence & voice lookups" />
        </Field>
      </Card>
      <Card title="Memory & summaries" hint="How often Olisar condenses activity into long-term memory and refreshes what it knows.">
        <Field label="Summary token threshold" desc="Roll a channel up into a summary once it gathers this many new tokens.">
          <Num value={data.summary_token_threshold} onChange={(v) => set('summary_token_threshold', v)} min={500} step={500} />
        </Field>
        <Field label="Glossary mine threshold" desc="Mine the server glossary for new facts after this many new tokens.">
          <Num value={data.glossary_mine_token_threshold} onChange={(v) => set('glossary_mine_token_threshold', v)} min={300} step={250} />
        </Field>
        <Field label="Persona rebuild (messages)" desc="Rebuild a member's persona after this many new messages from them.">
          <Num value={data.user_persona_msg_threshold} onChange={(v) => set('user_persona_msg_threshold', v)} min={5} />
        </Field>
      </Card>
        </div>
        <div className="col">
      <Card title="Proactivity" hint="When and how often Olisar chimes in unprompted.">
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
      <Card title="Passive reactions" hint="When a reply would be overkill, Olisar can add an emoji reaction instead.">
        <Field label="Enabled"><Toggle value={pro.reaction_enabled} onChange={(v) => setP('reaction_enabled', v)} label="Let Olisar react with emoji" /></Field>
        <Field label="Confidence threshold" desc="Minimum confidence (0–1) before it reacts.">
          <Num value={pro.reaction_threshold ?? 0} onChange={(v) => setP('reaction_threshold', v)} min={0} max={1} step={0.05} />
        </Field>
        <div className="row">
          <Field label="Channel cooldown (s)"><Num value={pro.reaction_cooldown_sec} onChange={(v) => setP('reaction_cooldown_sec', v)} min={0} /></Field>
          <Field label="Max per hour"><Num value={pro.reaction_max_per_hour} onChange={(v) => setP('reaction_max_per_hour', v)} min={0} /></Field>
        </div>
      </Card>
        </div>
      </div>
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
      <div className="grid2">
      {Object.keys(data).filter((key) => key !== 'privacy').map((key) => (
        <Card key={key} title={MSG_LABELS[key] ?? key}>
          <Area value={edits[key] ?? ''} onChange={(v) => setEdits({ ...edits, [key]: v })} rows={2} placeholder={data[key].default} />
          <div className="code-default">default: {data[key].default}</div>
          {data[key].placeholders.length > 0 && (
            <div className="placeholders">placeholders: {data[key].placeholders.map((p: string) => <code key={p}>{`{${p}}`} </code>)}</div>
          )}
        </Card>
      ))}
      </div>
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
                        background: r.color || 'var(--text-3)', marginRight: 8, verticalAlign: 'middle',
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
    if (!(await confirmDialog({
      title: 'Clear search index?',
      message: 'Clear the entire message search index? New posts keep indexing live, and "Re-index all" rebuilds history.',
      confirmLabel: 'Clear index',
      tone: 'danger',
      requirePhrase: { phrase: 'clear index' },
    }))) return
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
              <button className="primary" onClick={start} disabled={busy}>
                <Icon.refresh size={14} /> {busy ? 'Working…' : 'Re-index all'}
              </button>
              <button className="danger icon-btn" onClick={clear} disabled={busy || data.indexed_messages === 0} data-tip="Clear index" aria-label="Clear index">
                <Icon.trash size={16} />
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
      <div className="cols2">
        <div className="col">
      <Card title="Knowledge base" hint="A webpage or a crawled site Olisar can reference. Upload documents via /olisar learn-doc in Discord.">
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
        <div className="settings-subhead">Sources ({rows.length})</div>
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
            <button className="danger" onClick={async () => { await api.deleteSource(s.id); reload() }}>
              <Icon.trash size={15} /> Remove
            </button>
          </div>
        ))}
      </Card>
      <Card title="Glossary" hint="Durable server lore and bits of info. Olisar carries these into every reply and also mines them automatically when it summarizes a channel. Subject is the term (optional) and the fact is one short, standalone statement.">
        <div className="row">
          <Field label="Subject"><Text value={subject} onChange={setSubject} placeholder="MN" /></Field>
          <div style={{ flex: 3 }}>
            <Field label="Fact"><Text value={fact} onChange={setFact} placeholder="MN is Movie Night, our Friday watch-party in #cinema" /></Field>
          </div>
        </div>
        <SaveBar saver={factAdder} label="Add fact" />
        <div className="settings-subhead">Glossary ({factRows.length})</div>
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
            <button className="danger" onClick={async () => { await api.deleteFact(f.id); reloadFacts() }}>
              <Icon.trash size={15} /> Delete
            </button>
          </div>
        ))}
      </Card>
        </div>
        <div className="col fill">
          <SearchIndexCard />
        </div>
      </div>
    </>
  )
}

// ── Extensions ───────────────────────────────────────────────────────────────
// A schema-driven settings form: renders whatever fields an extension declares in
// its manifest's settingsSchema and saves them. (Replaces the bespoke welcome panel;
// enable/disable now lives on the extension's toggle, settings save on their own.)
function SettingsForm(props: { extKey: string; schema: any }) {
  const fields: any[] = props.schema?.fields ?? []
  const needsChannels = fields.some((f) => f.type === 'channel')
  const { data: chans } = useAsync<any[]>(needsChannels ? api.getChannels : (() => Promise.resolve([])), [props.extKey])
  const { data: loaded } = useAsync<any>(() => api.getExtensionSettings(props.extKey), [props.extKey])
  const [vals, setVals] = useState<Record<string, any>>({})
  const [init, setInit] = useState(false)
  useEffect(() => { if (loaded && !init) { setVals({ ...(loaded.settings || {}) }); setInit(true) } }, [loaded, init])
  const saver = useSaver(async () => { await api.putExtensionSettings(props.extKey, vals) })
  const set = (k: string, v: any) => setVals((p) => ({ ...p, [k]: v }))
  const chanOpts = [{ value: '', label: '— pick a channel —' }, ...((chans ?? []).map((c: any) => ({ value: String(c.channel_id), label: '#' + (c.name || c.channel_id) })))]
  if (!fields.length) return null
  return (
    <Card title="Settings">
      {fields.map((f) => (
        <Field key={f.key} label={f.label || f.key} desc={f.desc}>
          {f.type === 'channel' ? <Select value={String(vals[f.key] ?? '')} onChange={(v) => set(f.key, v)} options={chanOpts} />
            : f.type === 'textarea' ? <Area value={String(vals[f.key] ?? '')} onChange={(v) => set(f.key, v)} rows={3} />
            : f.type === 'number' ? <Num value={Number(vals[f.key] ?? 0)} onChange={(v) => set(f.key, v)} />
            : f.type === 'toggle' ? <Toggle value={!!vals[f.key]} onChange={(v) => set(f.key, v)} />
            : <Text value={String(vals[f.key] ?? '')} onChange={(v) => set(f.key, v)} />}
        </Field>
      ))}
      <SaveBar saver={saver} label="Save settings" />
    </Card>
  )
}

// The detail panel for one selected extension: what it is, what it adds, its
// capabilities, its enable toggle, and (for operators) a way into the code.
// Plain-English labels for the capability strings, shown on the import-consent screen
// and the detail panel so an operator knows what they're granting.
const PERM_LABELS: Record<string, string> = {
  fetch: 'Make web requests to any public URL',
  'kb.write': 'Add sources to the knowledge base',
  'glossary.write': 'Add glossary / memory facts',
  kv: 'Use its own private key-value storage',
  settings: 'Read its own settings',
  'discord.reply': 'Reply in Discord',
  'discord.modal': 'Show pop-up forms (modals)',
  'discord.components': 'Use buttons and select menus',
  'discord.send': 'Post messages to your channels (no @mentions)',
  'model.generate': 'Generate text with your AI model (uses your quota)',
}

// Risk-score band → CSS class, matching the consent screen's colour cues.
function riskClass(score: number): string {
  if (score >= 70) return 'danger'
  if (score >= 31) return 'warn'
  return 'ok'
}
export function permLabel(p: string): string {
  if (p.startsWith('secret:')) return `Use the “${p.slice(7)}” secret key`
  return PERM_LABELS[p] || p
}

async function downloadOlx(key: string) {
  const doc = await api.exportAuthoring(key)
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${doc.id}-${doc.version}.olx`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function ExtensionDetail(props: { e: any; isOperator?: boolean; onToggle: (k: string, v: boolean) => void; onEdit: (k: string) => void; onUpdate?: (k: string) => void; mkt?: any; pub?: any; onPublished?: () => void }) {
  const { e, mkt, pub } = props
  const tools: string[] = e.tools ?? []
  const commands: string[] = e.commands ?? []
  const perms: string[] = e.permissions ?? []
  const requested: string[] = e.requested_permissions ?? []
  const ungranted = requested.filter((p) => !perms.includes(p))
  const marketplace = e.origin === 'marketplace'
  const imported = e.origin === 'imported'
  const fromElsewhere = marketplace || imported
  // Publishable = locally-authored (not a built-in, not something installed from elsewhere).
  const publishable = e.editable && (!e.origin || e.origin === 'local')
  // Already live on the marketplace under this bot's handle (from /marketplace/published).
  const isPublished = publishable && !!pub
  const [reporting, setReporting] = useState(false)
  // Publish flow: open the scan modal, run the review-only endpoint, then show pass/blocked.
  const [reviewing, setReviewing] = useState(false)
  const [reviewResult, setReviewResult] = useState<any>(null)
  const [publishing, setPublishing] = useState(false)
  const ref = e.marketplace_ref

  const startReview = async () => {
    setReviewResult(null); setReviewing(true)
    try {
      setReviewResult(await api.marketplaceReview(e.key))
    } catch (err: any) {
      setReviewing(false)
      toast('Scan failed: ' + err.message, 'danger')
    }
  }

  const publishToMarketplace = async () => {
    try {
      const info = await api.marketplacePublisher()
      if (!info.registered) {
        const handle = (await promptDialog({
          title: 'Choose a publisher handle',
          message: 'Your marketplace namespace (a-z 0-9 _ -).',
          prompt: { placeholder: 'handle' },
          confirmLabel: 'Register',
        }))?.trim()
        if (!handle) return
        await api.marketplaceRegister(handle)
      }
    } catch (err: any) { toast('Publish failed: ' + err.message, 'danger'); return }
    await startReview()
  }

  // Push the current local source to an already-published extension. If the version
  // number hasn't moved, warn — the registry overwrites it in place, so anyone who
  // already installed it won't be offered an update unless the version is bumped.
  const pushUpdate = async () => {
    if (pub && !pub.version_is_new && pub.has_changes) {
      const ok = await confirmDialog({
        title: `Re-publish v${pub.local_version} in place?`,
        message:
          `The version number hasn't changed, so anyone who already installed it won't be offered ` +
          `an update. Bump the version in your code to ship it as an update. Push these changes to ` +
          `v${pub.local_version} anyway?`,
        confirmLabel: 'Push anyway',
        tone: 'warning',
      })
      if (!ok) return
    }
    await startReview()
  }

  // Clicked from the "pass" screen — actually ship it (the server re-reviews as the gate).
  const confirmPublish = async () => {
    setPublishing(true)
    try {
      const r = await api.marketplacePublish(e.key)
      setReviewing(false); setReviewResult(null)
      toast(`Published ${r.id} v${r.version} to the marketplace.`, 'success')
      props.onPublished?.()
    } catch (err: any) {
      const d = err?.detail
      if (d && typeof d === 'object' && d.code === 'risk_blocked') {
        setReviewResult({ ...d, blocked: true, review_available: true })  // server caught it after all
      } else if (d && typeof d === 'object' && d.code === 'review_unavailable') {
        setReviewResult({ review_available: false, blocked: false, message: d.message })  // quota died mid-flow
      } else {
        setReviewing(false); toast('Publish failed: ' + err.message, 'danger')
      }
    } finally { setPublishing(false) }
  }
  return (
    <>
      <Card>
        <div className="ext-dhead">
          <div className="grow">
            <div className="ext-dtitle">{e.name}</div>
            <div className="ext-chips">
              <span className="badge">{e.category}</span>
              {marketplace
                ? <span className="badge info">Marketplace</span>
                : imported
                  ? <span className="badge info">Imported</span>
                  : e.editable
                    ? <span className="badge info">Custom</span>
                    : <span className="badge">Built-in</span>}
              {e.user_modified && <span className="badge">edited</span>}
              {isPublished && <span className="badge info">Published</span>}
              {isPublished && pub.has_changes && <span className="badge warning">Unpublished changes</span>}
              {mkt?.update_available && <span className="badge info">Update available</span>}
              {mkt?.yanked && <span className="badge warning">Removed from marketplace</span>}
              <span className={'badge' + (e.enabled ? ' ready' : '')}>{e.enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
          </div>
          <div className="ext-dactions">
            {props.isOperator && marketplace && mkt?.update_available && (
              <button className="primary" onClick={() => props.onUpdate?.(e.key)}>Update to v{mkt.latest_version}</button>
            )}
            {props.isOperator && publishable && !isPublished && (
              <button className="ghost" onClick={publishToMarketplace}>Publish</button>
            )}
            {props.isOperator && isPublished && pub.has_changes && (
              <button className="primary" onClick={pushUpdate}>Push update</button>
            )}
            {props.isOperator && isPublished && !pub.has_changes && (
              <button className="ghost" onClick={pushUpdate}>Re-publish</button>
            )}
            {props.isOperator && e.has_code && (
              <button className="ghost icon-btn" onClick={() => downloadOlx(e.key).catch((err) => toast('Export failed: ' + err.message, 'danger'))} data-tip="Export .olx" aria-label="Export extension"><Icon.upload size={16} /></button>
            )}
            {props.isOperator && e.has_code && (
              <button className="ghost icon-btn" onClick={() => props.onEdit(e.key)} data-tip="Edit code" aria-label="Edit extension code"><Icon.edit size={16} /></button>
            )}
            {marketplace && ref && (
              <button className="danger icon-btn sm" title="Report this extension" onClick={() => setReporting(true)} aria-label="Report"><Icon.flag size={15} /></button>
            )}
            <Toggle value={e.enabled} onChange={(v) => props.onToggle(e.key, v)} />
          </div>
        </div>
        {reporting && ref && (
          <ReportModal target={{ namespace: ref.namespace, name: ref.name, version: ref.version, id: e.key }} onClose={() => setReporting(false)} />
        )}
        {reviewing && (
          <PublishReviewModal
            subject={`${e.key} · v${e.version}`}
            result={reviewResult} publishing={publishing}
            onPublish={confirmPublish} onClose={() => { setReviewing(false); setReviewResult(null) }}
          />
        )}

        <div className="ext-desc">{e.description || 'No description provided.'}</div>
        {fromElsewhere && (
          <div className="ext-prov">
            {marketplace ? 'From the marketplace' : 'Imported'}{e.publisher ? ` · published by ${e.publisher}` : ''}
            {e.signature_verified && e.signed_by
              ? ` · signed & verified (${e.signed_by})`
              : ' · unsigned'}
          </div>
        )}
        {isPublished && (
          <div className="ext-prov">
            Published to the marketplace as <code>{pub.namespace}/{e.key}</code> · v{pub.published_version}
            {pub.verified && <> · <span className="ok-text"><Icon.check size={13} weight="Bold" /> verified publisher</span></>}
            {pub.has_changes && (
              <> · <span style={{ color: 'var(--warn)' }}>
                {pub.version_is_new
                  ? `local v${pub.local_version} not pushed yet`
                  : 'local edits not pushed yet'}
              </span></>
            )}
          </div>
        )}
        {mkt?.yanked && (
          <div className="ext-prov" style={{ color: 'var(--warn)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Icon.warn size={14} weight="Bold" /> Removed from the marketplace{mkt.gone ? '' : ' by the publisher'} — it keeps working but won't get updates.
          </div>
        )}

        {(tools.length > 0 || commands.length > 0 || e.behavior) && (
          <div className="ext-block">
            <div className="ext-block-l">What it adds</div>
            <div className="ext-caps">
              {tools.map((t) => <span key={'t' + t} className="tag">{t}()</span>)}
              {commands.map((c) => <span key={'c' + c} className="tag">/{c}</span>)}
              {e.behavior && <span className="badge">Shapes replies</span>}
            </div>
          </div>
        )}

        {perms.length > 0 && (
          <div className="ext-block">
            <div className="ext-block-l">Capabilities it uses</div>
            <div className="ext-caps">{perms.map((p) => <span key={p} className="tag">{p}</span>)}</div>
          </div>
        )}

        {fromElsewhere && ungranted.length > 0 && (
          <div className="ext-block">
            <div className="ext-block-l">Requested but not granted</div>
            <div className="ext-caps">{ungranted.map((p) => <span key={p} className="tag" style={{ opacity: 0.55 }}>{p}</span>)}</div>
          </div>
        )}
      </Card>

      {e.settings_schema?.fields?.length > 0 && <SettingsForm key={e.key} extKey={e.key} schema={e.settings_schema} />}
    </>
  )
}

// The consent gate shared by file-import and marketplace-install: shows what the
// extension adds, its signature status, and the capabilities it requests; the operator
// grants a (possibly narrower) set. The server re-verifies and enforces granted ⊆ requested.
function ConsentModal(props: {
  preview: any
  busy: boolean
  err: string | null
  title: string
  subtitle: string
  onClose: () => void
  onInstall: (granted: string[]) => void
}) {
  const { preview } = props
  const reqPerms: string[] = preview?.requested_permissions ?? []
  // Host secrets (gemini/cloudflare/uex) are barred from installed (third-party) extensions
  // server-side — show them as unavailable and never grant them.
  const isHostSecret = (p: string) => p.startsWith('secret:')
  const [granted, setGranted] = useState<Set<string>>(() => new Set(reqPerms.filter((p) => !isHostSecret(p))))
  const [accepted, setAccepted] = useState(false)
  const sig = preview?.signature
  const blocked = preview.exists || preview.is_builtin_key || sig?.status === 'invalid'
  const risk = preview?.risk
  const togglePerm = (p: string) =>
    setGranted((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n })

  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="import-modal" onClick={(ev) => ev.stopPropagation()}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head"><h2>{props.title}</h2><p>{props.subtitle}</p></div>

        <div className="import-review">
          <div className="import-title">{preview.name} <span className="import-ver">v{preview.version}</span></div>
          <div className="import-sub">
            <span className="badge">{preview.category}</span>
            <code>{preview.id}</code>
            {preview.author?.name && <span className="settings-muted">by {preview.author.name}</span>}
          </div>

          {sig && (
            <div className={'import-sig ' + sig.status}>
              {sig.status === 'valid'
                ? <>Signed &amp; verified · <code>{sig.fingerprint}</code></>
                : sig.status === 'invalid'
                  ? <>Signature invalid — this bundle may have been tampered with.</>
                  : <>Unsigned — its author and integrity can’t be verified.</>}
            </div>
          )}

          {preview.description && <div className="ext-desc" style={{ marginTop: 10 }}>{preview.description}</div>}

          {(preview.tools?.length > 0 || preview.commands?.length > 0 || preview.behavior) && (
            <>
              <div className="settings-subhead">What it adds</div>
              <div className="ext-caps">
                {(preview.tools || []).map((t: string) => <span key={'t' + t} className="tag">{t}()</span>)}
                {(preview.commands || []).map((c: string) => <span key={'c' + c} className="tag">/{c}</span>)}
                {preview.behavior && <span className="badge">Shapes replies</span>}
              </div>
            </>
          )}

          {risk && (
            <>
              <div className="settings-subhead">Risk assessment</div>
              {risk.ok ? (
                <div className="risk-box">
                  <div className="risk-head">
                    <span className={'risk-score ' + riskClass(risk.score)}>{risk.score}<span className="risk-max">/100</span></span>
                    {risk.summary && <span className="risk-summary">{risk.summary}</span>}
                  </div>
                  {risk.bullets?.length > 0 && (
                    <ul className="risk-bullets">
                      {risk.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}
                    </ul>
                  )}
                </div>
              ) : (
                <div className="settings-muted">Automated risk review unavailable — read the capabilities below carefully before installing.</div>
              )}
            </>
          )}

          <div className="settings-subhead">Capabilities to grant</div>
          {reqPerms.length === 0 ? (
            <div className="settings-muted">This extension requests no special capabilities.</div>
          ) : (
            <>
              <div className="import-perms">
                {reqPerms.map((p) => isHostSecret(p) ? (
                  <label key={p} className="import-perm" style={{ opacity: 0.55, cursor: 'default' }}>
                    <input type="checkbox" checked={false} disabled />
                    <span className="pl">{permLabel(p)} <span className="settings-muted">— host secret, not available to installed extensions</span></span>
                    <span className="pk">{p}</span>
                  </label>
                ) : (
                  <label key={p} className="import-perm">
                    <input type="checkbox" checked={granted.has(p)} onChange={() => togglePerm(p)} />
                    <span className="pl">{permLabel(p)}</span>
                    <span className="pk">{p}</span>
                  </label>
                ))}
              </div>
              <div className="import-warn">This runs third-party code in your bot. Grant only what you trust; ungranted capabilities will simply be unavailable to it.</div>
            </>
          )}

          {preview.exists && <div className="settings-err" style={{ marginTop: 14 }}>An extension named “{preview.id}” is already installed — delete it first to reinstall.</div>}
          {preview.is_builtin_key && <div className="settings-err" style={{ marginTop: 14 }}>“{preview.id}” is a reserved built-in name and can’t be installed.</div>}
        </div>

        {props.err && <div className="settings-err" style={{ marginTop: 14 }}>{props.err}</div>}

        {!blocked && (
          <label className="import-accept">
            <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
            <span>I understand this is third-party code and accept the risks of installing it.</span>
          </label>
        )}

        <div className="import-foot">
          <button className="ghost" onClick={props.onClose} disabled={props.busy}>Cancel</button>
          <button className="primary" onClick={() => props.onInstall(Array.from(granted))} disabled={props.busy || blocked || !accepted}>
            {props.busy ? 'Installing…' : granted.size ? `Install · grant ${granted.size}` : 'Install'}
          </button>
        </div>
      </div>
    </div>
  )
}

function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(new Error('read failed'))
    r.readAsDataURL(f)
  })
}

// Report a marketplace extension: describe the problem, optionally attach files + bot logs.
// The report is emailed to the platform owner and shows up in the developer console.
function ReportModal(props: {
  target: { namespace: string; name: string; version?: string; id?: string }
  onClose: () => void
}) {
  const [desc, setDesc] = useState('')
  const [files, setFiles] = useState<{ name: string; type: string; content_b64: string }[]>([])
  const [logsAttached, setLogsAttached] = useState(false)
  const [logs, setLogs] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const addFiles = async (list: FileList) => {
    setErr(null)
    const out = [...files]
    for (const f of Array.from(list)) {
      if (out.length >= 8) break
      if (f.size > 3_000_000) { setErr(`${f.name} is too large (max 3 MB each).`); continue }
      out.push({ name: f.name, type: f.type || 'application/octet-stream', content_b64: await fileToB64(f) })
    }
    setFiles(out)
  }
  const attachLogs = async () => {
    try { const d = await api.getLogs(800); setLogs((d.lines || []).join('\n')); setLogsAttached(true) }
    catch { setErr('Couldn’t read the bot logs.') }
  }
  const submit = async () => {
    if (!desc.trim()) { setErr('Please describe what happened.'); return }
    setBusy(true); setErr(null)
    try {
      await api.marketplaceReport({
        namespace: props.target.namespace, name: props.target.name, version: props.target.version,
        description: desc, logs: logsAttached ? logs : '', attachments: files,
      })
      setDone(true)
    } catch (e: any) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="import-modal" onClick={(ev) => ev.stopPropagation()}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head">
          <h2>Report extension</h2>
          <p>{props.target.id || `${props.target.namespace}/${props.target.name}`}</p>
        </div>
        {done ? (
          <>
            <div className="import-review"><div className="settings-muted">Thanks — your report was sent to the Olisar team. They’ll review it.</div></div>
            <div className="import-foot"><button className="primary" onClick={props.onClose}>Done</button></div>
          </>
        ) : (
          <>
            <div className="import-review">
              <div className="settings-subhead">What went wrong?</div>
              <Area
                value={desc} onChange={setDesc} rows={5}
                placeholder="Describe the behaviour you saw — what the extension did, when, and why it concerned you."
              />
              <div className="settings-subhead">Evidence (optional)</div>
              <div className="report-attach">
                <button className="ghost" onClick={() => fileRef.current?.click()}>
                  <Icon.add size={14} /> Add attachments
                </button>
                <button className={'ghost' + (logsAttached ? ' on' : '')} onClick={attachLogs}>
                  <Icon.docs size={14} /> {logsAttached ? 'Bot logs attached' : 'Add bot logs'}
                </button>
              </div>
              {files.length > 0 && (
                <div className="report-files">
                  {files.map((f, i) => (
                    <span key={i} className="tag">
                      {f.name}
                      <button className="tag-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Remove" title="Remove"><CloseX size={11} /></button>
                    </span>
                  ))}
                </div>
              )}
              <input
                ref={fileRef} type="file" multiple style={{ display: 'none' }}
                onChange={(ev) => { if (ev.target.files) addFiles(ev.target.files); ev.target.value = '' }}
              />
            </div>
            {err && <div className="settings-err" style={{ marginTop: 14 }}>{err}</div>}
            <div className="import-foot">
              <button className="ghost" onClick={props.onClose} disabled={busy}>Cancel</button>
              <button className="primary" onClick={submit} disabled={busy || !desc.trim()}>
                {busy ? 'Sending…' : 'Send report'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// A circular risk gauge. While `scanning`, an indeterminate sweep rotates the ring; once a
// score lands, an arc sweeps to it (synced with the counting number), colour-graded by band,
// (no threshold tick — the band colour + score carry the verdict).
function RiskMeter({ score, band, scanning }: { score: number; band: string; scanning?: boolean }) {
  const [shown, setShown] = useState(0)
  useEffect(() => {
    if (scanning) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setShown(score); return }
    let raf = 0
    const start = performance.now()
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / 1100)
      setShown(Math.round(score * (1 - Math.pow(1 - p, 3)))) // easeOutCubic
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [score, scanning])
  const R = 80
  return (
    <div className={'riskmeter ' + (scanning ? 'scanning' : band)}>
      <svg viewBox="0 0 200 200" className="riskmeter-svg">
        <circle className="rm-track" cx={100} cy={100} r={R} pathLength={100} />
        {scanning ? (
          <circle className="rm-sweep" cx={100} cy={100} r={R} pathLength={100} />
        ) : (
          <circle className="rm-arc" cx={100} cy={100} r={R} pathLength={100} style={{ strokeDasharray: `${shown} 100` }} />
        )}
      </svg>
      <div className="riskmeter-center">
        {scanning ? (
          <div className="rm-scanning">SCANNING…</div>
        ) : (
          <>
            <div className="rm-score">{shown}</div>
            <div className="rm-of">/ 100</div>
            <div className="rm-label">RISK</div>
          </>
        )}
      </div>
    </div>
  )
}

// Render a plain string with inline `code` spans turned into <code> (used by the security
// review callouts and the docs TOC).
function inlineCode(text: string) {
  return text.split('`').map((seg, i) => (i % 2 === 1 ? <code key={i}>{seg}</code> : seg))
}

// The publish flow's modal: first a security-scan screen, then it becomes the verdict —
// either a BLOCKED readout (with reasons) or a PASSED card with a Publish button. Same size
// throughout, so the scan animation morphs into the result in place.
function PublishReviewModal(props: {
  subject: string; result: any; publishing?: boolean;
  onPublish: () => void; onClose: () => void;
}) {
  const r = props.result
  if (!r) {
    return (
      <div className="modal-backdrop" onClick={props.onClose}>
        <div className="deny-modal scan" onClick={(e) => e.stopPropagation()}>
          <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
          <h2 className="deny-title">Security review</h2>
          <div className="deny-sub">{props.subject}</div>
          <RiskMeter score={0} band="ok" scanning />
          <div className="deny-verdict" style={{ textAlign: 'center' }}>Analysing the source for risky behaviour…</div>
        </div>
      </div>
    )
  }
  const score = Number(r.risk_score ?? 0)
  const threshold = Number(r.threshold ?? 70)
  const bullets: string[] = r.bullets || []
  const blocked = !!r.blocked
  // No score came back (e.g. the AI review quota is exhausted). Publishing fails closed
  // server-side, so the modal must not offer a Publish button here either.
  const unavailable = !blocked && r.review_available === false
  const band = score >= 70 ? 'danger' : score >= 31 ? 'warn' : 'ok'
  // Callout tone tracks the risk band (a block is never shown green); the matching
  // leading icon: a check when clean, otherwise the warning glyph.
  const passTone = band === 'ok' ? 'tip' : band === 'warn' ? 'warning' : 'danger'
  const tone = blocked ? band : unavailable ? 'warn' : 'pass ' + band
  const title = blocked ? 'Publish blocked' : unavailable ? 'Review unavailable' : 'Review passed'
  // The verdict laid out two-up — meter + description on the left, callout cards on the
  // right — except the unavailable state, which has no score so stays single-column.
  const blockCallout = (text: string, key?: number) => (
    <div key={key} className={'callout ' + (band === 'danger' ? 'danger' : 'warning')}>
      <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
      <div className="callout-body">{inlineCode(text)}</div>
    </div>
  )
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className={'deny-modal ' + (unavailable ? '' : 'split ') + tone} onClick={(e) => e.stopPropagation()}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <h2 className="deny-title">{title}</h2>
        <div className="deny-sub">{props.subject}</div>

        {unavailable ? (
          <div className="callout warning">
            <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
            <div className="callout-body">
              {inlineCode(r.message || 'The security review couldn’t run (your Gemi quota may be exhausted). Publishing is blocked until a review completes — try again later.')}
            </div>
          </div>
        ) : (
          <div className="deny-body">
            <div className="deny-side">
              <RiskMeter score={score} band={band} />
              <div className="deny-verdict">
                {blocked
                  ? <>Scored <b>{score}</b> — over your block threshold of <b>{threshold}</b>.</>
                  : <>Scored <b>{score}</b> — under your threshold of <b>{threshold}</b>.</>}
              </div>
            </div>
            <div className="deny-callouts">
              {blocked
                ? (bullets.length > 0
                  ? bullets.map((b, i) => blockCallout(b, i))
                  : blockCallout(r.summary || 'The security review flagged concerns in the source.'))
                : (
                  <div className={'callout ' + passTone}>
                    <span className="ic">{band === 'ok' ? <Icon.check size={17} weight="Bold" /> : <Icon.warn size={17} weight="Bold" />}</span>
                    <div className="callout-body">{inlineCode(r.summary || 'No major concerns found.')}</div>
                  </div>
                )}
            </div>
          </div>
        )}

        <div className="deny-foot">
          {blocked || unavailable ? (
            <button className="primary" onClick={props.onClose}>{unavailable ? 'Close' : 'Got it'}</button>
          ) : (
            <>
              <button className="ghost" onClick={props.onClose} disabled={props.publishing}>Cancel</button>
              <button className="primary" onClick={props.onPublish} disabled={props.publishing}>
                {props.publishing ? <><span className="spinner" /> Publishing…</> : 'Publish'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// Import an .olx file: pick → preview → the shared consent gate → install.
function ImportDialog(props: { onClose: () => void; onImported: (key: string) => void }) {
  const [bundle, setBundle] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const onFile = async (file: File) => {
    setErr(null); setBusy(true)
    try {
      let data: any
      try { data = JSON.parse(await file.text()) } catch { throw new Error('That file isn’t a valid .olx (not JSON).') }
      const p = await api.importPreview(data)
      setBundle(data); setPreview(p)
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const install = async (granted: string[]) => {
    setBusy(true); setErr(null)
    try { const r = await api.importAuthoring(bundle, granted); toast('Extension imported', 'success'); props.onImported(r.key) }
    catch (e: any) { setErr(e.message); setBusy(false) }
  }

  if (preview) {
    return (
      <ConsentModal
        preview={preview} busy={busy} err={err}
        title="Import extension" subtitle="Review what it adds and what it can access before granting."
        onClose={props.onClose} onInstall={install}
      />
    )
  }
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="import-modal" onClick={(ev) => ev.stopPropagation()}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head">
          <h2>Import extension</h2>
          <p>Install an <code>.olx</code> bundle exported from Olisar.</p>
        </div>
        <div className="import-drop">
          <div className="settings-muted">Choose a <code>.olx</code> file.</div>
          <button className="primary" style={{ marginTop: 14 }} onClick={() => fileRef.current?.click()} disabled={busy}>
            {busy ? 'Reading…' : 'Choose .olx file…'}
          </button>
        </div>
        {err && <div className="settings-err" style={{ marginTop: 14 }}>{err}</div>}
        <input
          ref={fileRef} type="file" accept=".olx,application/json" style={{ display: 'none' }}
          onChange={(ev) => { const f = ev.target.files?.[0]; if (f) onFile(f); ev.target.value = '' }}
        />
      </div>
    </div>
  )
}

// Browse the marketplace registry and install via the shared consent flow. The bot
// proxies to the registry and re-verifies every bundle locally before installing.
function Marketplace(props: { onBack: () => void; onInstalled: (key: string) => void }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [sel, setSel] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [perr, setPerr] = useState<string | null>(null)
  const [pubInfo, setPubInfo] = useState<any>(null)
  const [report, setReport] = useState<any>(null)

  const runSearch = async () => {
    setLoading(true); setErr(null)
    try { const d = await api.marketplaceSearch(q); setResults(d.results || []) }
    catch (e: any) { setErr(e.message) } finally { setLoading(false) }
  }
  useEffect(() => { runSearch(); api.marketplacePublisher().then(setPubInfo).catch(() => {}) }, []) // initial load

  const openInstall = async (item: any) => {
    setSel(item); setPreview(null); setPerr(null); setBusy(true)
    try {
      const p = await api.marketplaceInstallPreview({ namespace: item.namespace, name: item.name, version: item.version })
      setPreview(p)
    } catch (e: any) { setPerr(e.message); setSel(null); toast('Couldn’t load: ' + e.message, 'danger') } finally { setBusy(false) }
  }
  const doInstall = async (granted: string[]) => {
    if (!sel) return
    setBusy(true); setPerr(null)
    try {
      const r = await api.marketplaceInstall({ namespace: sel.namespace, name: sel.name, version: sel.version, granted_permissions: granted })
      toast('Extension installed', 'success')
      props.onInstalled(r.key)
    } catch (e: any) { setPerr(e.message); setBusy(false) }
  }
  const doYank = async (item: any) => {
    if (!(await confirmDialog({
      title: `Yank ${item.id}?`,
      message: "It'll stop appearing in the marketplace for everyone.",
      confirmLabel: 'Yank',
      tone: 'danger',
      requirePhrase: { phrase: `yank ${item.id}` },
    }))) return
    try { await api.marketplaceYank(item.name); await runSearch() }  // whole extension, all versions
    catch (e: any) { toast('Yank failed: ' + e.message, 'danger') }
  }
  const changeHandle = async () => {
    const h = (await promptDialog({
      title: 'Change publisher handle',
      message: 'New publisher handle (a-z 0-9 _ -). Re-registering rotates your token; verification carries over.',
      prompt: { defaultValue: pubInfo?.handle || '', placeholder: 'handle' },
      confirmLabel: 'Change handle',
    }))?.trim()
    if (!h || h === pubInfo?.handle) return
    try { await api.marketplaceRegister(h); setPubInfo(await api.marketplacePublisher()); await runSearch() }
    catch (e: any) { toast('Couldn’t change handle: ' + e.message, 'danger') }
  }

  return (
    <>
      <div className="mkt-head">
        <button className="ghost" onClick={props.onBack}><Icon.arrowLeft size={15} /> Back</button>
        <form className="mkt-search" onSubmit={(e) => { e.preventDefault(); runSearch() }}>
          <Text value={q} onChange={setQ} placeholder="Search the marketplace…" />
          <button type="submit">Search</button>
        </form>
      </div>

      {pubInfo?.registered && (
        <div className="mkt-pubbar">
          <span>Publishing as <code>{pubInfo.handle}</code></span>
          {pubInfo.verified
            ? <span className="badge publisher"><Icon.verified size={13} weight="Bold" /> Discord-verified</span>
            : <button className="ghost" onClick={() => { window.location.href = api.marketplaceVerifyStartUrl() }}>Verify with Discord</button>}
          <span className="grow" />
          <button className="ghost" onClick={changeHandle}>Change handle</button>
        </div>
      )}

      {loading ? <Spinner /> : err ? (
        <Card><div className="settings-err">{err}</div></Card>
      ) : results.length === 0 ? (
        <Card><div className="ext-overview"><div>No extensions found.</div></div></Card>
      ) : (
        <div className="mkt-grid">
          {results.map((r) => (
            <div key={r.id} className="mkt-card">
              <div className="mkt-card-top">
                <div className="mkt-name">{r.name} <span className="import-ver">v{r.version}</span></div>
                <span className="badge">{r.category}</span>
              </div>
              <div className="mkt-pub">
                {r.publisher_verified
                  ? <span className="badge publisher"><Icon.verified size={13} weight="Bold" /> {r.publisher}</span>
                  : <span className="badge publisher">{r.publisher || 'unknown publisher'}</span>}
              </div>
              {r.description && <div className="mkt-desc">{r.description}</div>}
              {r.permissions?.length > 0 && (
                <div className="mkt-perms">{r.permissions.map((p: string) => <span key={p} className="tag">{p}</span>)}</div>
              )}
              <div className="mkt-card-foot">
                <button className="danger icon-btn sm" title="Report this extension" onClick={() => setReport(r)} aria-label="Report"><Icon.flag size={15} /></button>
                {pubInfo?.handle && r.publisher === pubInfo.handle && (
                  <button className="danger" onClick={() => doYank(r)}>Yank</button>
                )}
                <button className="primary" onClick={() => openInstall(r)} disabled={busy && sel?.id === r.id}>Install</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {sel && preview && (
        <ConsentModal
          preview={preview} busy={busy} err={perr}
          title="Install from marketplace" subtitle="Review what it adds and what it can access before granting."
          onClose={() => { setSel(null); setPreview(null); setPerr(null) }} onInstall={doInstall}
        />
      )}

      {report && <ReportModal target={report} onClose={() => setReport(null)} />}
    </>
  )
}

// The code editor ("Build" mode) is heavy (Monaco + esbuild-wasm) and operator-only,
// so it loads only when an operator drills in to create or edit an extension.
const ExtensionEditor = lazy(() => import('./authoring'))

export function Extensions(props: { isOperator?: boolean } = {}) {
  const ed = useEditable<any[]>(api.getExtensions)
  const [view, setView] = useState<'catalog' | 'editor' | 'marketplace'>('catalog')
  const [editKey, setEditKey] = useState<string | null>(null)
  const [selKey, setSelKey] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<'all' | 'on' | 'custom'>('all')
  const [importing, setImporting] = useState(false)
  const [mktStatus, setMktStatus] = useState<Record<string, any>>({})
  const [pubStatus, setPubStatus] = useState<Record<string, any>>({})
  const [updKey, setUpdKey] = useState<string | null>(null)
  const [updPreview, setUpdPreview] = useState<any>(null)
  const [updBusy, setUpdBusy] = useState(false)
  const [updErr, setUpdErr] = useState<string | null>(null)
  // Per-marketplace-extension update/yank status, and per-authored-extension publish
  // status (is it live, are there unpushed local changes). Fetched once; few extensions.
  const reloadPubStatus = () => { api.marketplacePublished().then(setPubStatus).catch(() => {}) }
  // A yanked/removed marketplace extension is reverted to a local one server-side (so it
  // loses the Marketplace label and can be re-published). When that happens, drop its stale
  // marketplace status and refresh the catalog + publish status so the change shows.
  const reloadMktStatus = () => api.marketplaceInstalled().then((s: Record<string, any>) => {
    const detached = Object.keys(s).filter((k) => s[k]?.detached)
    if (detached.length) {
      const cleaned = { ...s }; detached.forEach((k) => delete cleaned[k])
      setMktStatus(cleaned); ed.reload(); reloadPubStatus()
    } else {
      setMktStatus(s)
    }
  }).catch(() => {})
  useEffect(() => { reloadMktStatus(); reloadPubStatus() }, [])
  const saver = useSaver(async () => {
    const orig = new Map((ed.baseline() ?? []).map((e: any) => [e.key, e.enabled]))
    for (const e of ed.data ?? []) {
      if (e.enabled !== orig.get(e.key)) await api.putExtension({ key: e.key, enabled: e.enabled })
    }
    ed.markSaved()
  })
  const toggle = (key: string, v: boolean) =>
    ed.setData((prev: any[] | null) => (prev ?? []).map((e) => (e.key === key ? { ...e, enabled: v } : e)))
  const openEditor = (key: string | null) => { setEditKey(key); setView('editor') }
  const startUpdate = async (key: string) => {
    setUpdErr(null); setUpdPreview(null); setUpdKey(key)
    try { setUpdPreview(await api.marketplaceUpdatePreview(key)) }
    catch (e: any) { setUpdKey(null); toast('Update check failed: ' + e.message, 'danger') }
  }
  const applyUpdate = async (granted: string[]) => {
    if (!updKey) return
    setUpdBusy(true); setUpdErr(null)
    try {
      await api.marketplaceUpdate(updKey, granted)
      setUpdKey(null); setUpdPreview(null); setUpdBusy(false)
      ed.reload(); reloadMktStatus()
    } catch (e: any) { setUpdErr(e.message); setUpdBusy(false) }
  }

  // ── Build mode: the focused code editor (drill-in) ──
  if (view === 'editor') {
    return (
      <Suspense fallback={<Spinner />}>
        <ExtensionEditor editKey={editKey} onBack={() => { setView('catalog'); ed.reload(); reloadPubStatus() }} onChanged={ed.reload} />
      </Suspense>
    )
  }
  // ── Marketplace mode: browse the registry and install ──
  if (view === 'marketplace') {
    return (
      <Marketplace
        onBack={() => { setView('catalog'); ed.reload() }}
        onInstalled={(key) => { setView('catalog'); setSelKey(key); ed.reload() }}
      />
    )
  }
  if (ed.loading) return <Spinner />

  // ── Catalog mode: a searchable rail + a rich detail panel ──
  const rows = ed.data ?? []
  const ql = q.trim().toLowerCase()
  const match = (e: any) =>
    (filter === 'all' || (filter === 'on' && e.enabled) || (filter === 'custom' && e.editable)) &&
    (!ql || e.name.toLowerCase().includes(ql) || (e.description || '').toLowerCase().includes(ql))
  const shown = rows.filter(match)
  // Group every extension (built-in, custom, marketplace) under its own category, adding a
  // group for any category present. Sorted A→Z with the catch-all "General" last.
  const catOf = (e: any) => e.category || 'General'
  const cats = Array.from(new Set(shown.map(catOf))).sort((a, b) =>
    a === b ? 0 : a === 'General' ? 1 : b === 'General' ? -1 : a.localeCompare(b))
  const effective = shown.find((e) => e.key === selKey) ?? shown[0] ?? null
  const enabledCount = rows.filter((e) => e.enabled).length
  const customCount = rows.filter((e) => e.editable).length

  const railItem = (e: any) => (
    <button
      key={e.key}
      className={'ext-item' + (e.enabled ? ' on' : '') + (effective?.key === e.key ? ' active' : '')}
      onClick={() => setSelKey(e.key)}
    >
      <span className="dot" />
      <span className="nm">{e.name}</span>
      {mktStatus[e.key]?.update_available && <span className="cust" style={{ color: 'var(--accent)' }} title="Update available"><Icon.update size={13} /></span>}
      {mktStatus[e.key]?.yanked && <span className="cust" style={{ color: 'var(--warn)' }} title="Removed from marketplace"><Icon.warn size={13} /></span>}
      {pubStatus[e.key]?.has_changes && <span className="dot" style={{ background: 'var(--warn)', boxShadow: 'none' }} title="Unpublished changes" />}
      {e.editable && <span className="cust">{e.origin === 'marketplace' ? 'Market' : 'Custom'}</span>}
    </button>
  )

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <PageHead icon="extensions" title="Extensions" sub="Togglable packages of extra features." />
        {props.isOperator && (
          <div style={{ display: 'flex', gap: 8, flexShrink: 0, marginTop: 4 }}>
            <button className="ghost" onClick={() => setView('marketplace')}>Marketplace</button>
            <button className="ghost icon-btn" onClick={() => setImporting(true)} data-tip="Import .olx" aria-label="Import .olx"><Icon.download size={16} /></button>
            <button className="primary" onClick={() => openEditor(null)}><Icon.add size={14} /> New extension</button>
          </div>
        )}
      </div>

      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          onImported={(key) => { setImporting(false); setSelKey(key); ed.reload() }}
        />
      )}

      {updKey && updPreview && (
        <ConsentModal
          preview={updPreview} busy={updBusy} err={updErr}
          title={`Update ${updPreview.name}`}
          subtitle={`Updating ${updPreview.from_version ?? ''} → ${updPreview.to_version ?? ''}. Re-check what it can access before granting.`}
          onClose={() => { setUpdKey(null); setUpdPreview(null); setUpdErr(null) }}
          onInstall={applyUpdate}
        />
      )}

      <div className="ext-wrap">
        <aside className="ext-rail">
          <div className="ext-rail-head"><Text value={q} onChange={setQ} placeholder="Search extensions…" /></div>
          <div className="ext-seg">
            {(['all', 'on', 'custom'] as const).map((f) => (
              <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
                {f === 'all' ? 'All' : f === 'on' ? 'Enabled' : 'Custom'}
              </button>
            ))}
          </div>
          <div className="ext-list">
            {shown.length === 0 && <div className="ext-empty-rail">No extensions match.</div>}
            {cats.map((cat) => (
              <div key={cat}>
                <div className="ext-glabel">{cat}</div>
                {shown.filter((e) => catOf(e) === cat).map(railItem)}
              </div>
            ))}
          </div>
        </aside>

        <section>
          {effective ? (
            <ExtensionDetail key={effective.key} e={effective} isOperator={props.isOperator} onToggle={toggle} onEdit={openEditor} onUpdate={startUpdate} mkt={mktStatus[effective.key]} pub={pubStatus[effective.key]} onPublished={reloadPubStatus} />
          ) : (
            <Card>
              <div className="ext-overview">
                <div className="ext-stats">
                  <div className="ext-stat"><div className="n">{rows.length}</div><div className="l">Available</div></div>
                  <div className="ext-stat"><div className="n">{enabledCount}</div><div className="l">Enabled</div></div>
                  <div className="ext-stat"><div className="n">{customCount}</div><div className="l">Custom</div></div>
                </div>
                <div>Select an extension to see what it does{props.isOperator ? ', or create your own.' : '.'}</div>
              </div>
            </Card>
          )}
        </section>
      </div>

      <SaveDock dirty={ed.dirty} saver={saver} onReset={ed.reset} />
    </>
  )
}

// ── Docs (OpenClaw-style: left nav · content · on-this-page) ─────────────────

export function Docs(props: { onNavigate?: (tab: string) => void }) {
  const [active, setActive] = useState(DOCS[0].id)
  const [q, setQ] = useState('')
  const [activeHeading, setActiveHeading] = useState('')
  useEffect(() => { window.scrollTo({ top: 0 }) }, [active])

  const section = DOCS.find((s) => s.id === active) ?? DOCS[0]
  // Linear order follows the grouped sidebar, not the raw DOCS array, so the
  // prev/next buttons match what's shown in the nav.
  const order = DOC_GROUPS.flatMap((g) => g.ids)
  const oidx = order.indexOf(section.id)
  const prev = DOCS.find((s) => s.id === order[oidx - 1])
  const next = DOCS.find((s) => s.id === order[oidx + 1])
  const headings = headingsOf(section.body)

  // Scroll-spy: highlight the in-view heading in the "On this page" rail.
  useEffect(() => {
    setActiveHeading('')
    const els = headings.map((h) => document.getElementById(h.slug)).filter((e): e is HTMLElement => !!e)
    if (!els.length) return
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting)
        if (vis.length) {
          const top = vis.reduce((a, b) => (a.boundingClientRect.top < b.boundingClientRect.top ? a : b))
          setActiveHeading((top.target as HTMLElement).id)
        }
      },
      { rootMargin: '0px 0px -65% 0px', threshold: 0 },
    )
    els.forEach((el) => obs.observe(el))
    return () => obs.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])

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
        <h1 className="docs-title">{section.title}</h1>
        <Markdown md={section.body} onDocLink={goLink} />
        <div className="docs-prevnext">
          {prev ? (
            <button className="ghost" onClick={() => setActive(prev.id)}><Icon.arrowLeft size={15} /> {prev.title}</button>
          ) : <span />}
          {next ? (
            <button className="ghost" onClick={() => setActive(next.id)}>{next.title} <Icon.arrowRight size={15} /></button>
          ) : <span />}
        </div>
      </main>

      {headings.length > 0 && (
        <aside className="docs-toc">
          <div className="docs-toc-label">On this page</div>
          <div className="docs-toc-rail">
            {headings.map((h) => (
              <a
                key={h.slug}
                href={`#${h.slug}`}
                className={'lvl' + h.level + (activeHeading === h.slug ? ' active' : '')}
                onClick={() => setActiveHeading(h.slug)}
              >
                {inlineCode(h.text)}
              </a>
            ))}
          </div>
        </aside>
      )}
    </div>
  )
}

// ── Members (the profiles Olisar builds) ─────────────────────────────────────
const MAX_ROLES = 3  // cap role chips per card so they don't overflow

// The "+N" chip on a member card: hover/focus opens a wide, height-capped popup with every
// role. It flips above or below the chip toward whichever side has more room.
function RolesChip({ count, roles }: { count: number; roles: string[] }) {
  const [open, setOpen] = useState(false)
  const [up, setUp] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (r) {
      const below = window.innerHeight - r.bottom
      setUp(below < 280 && r.top > below)  // open upward only when there's more room above
    }
    setOpen(true)
  }
  return (
    <span ref={ref} className="tag more rolepop-wrap" tabIndex={0}
      onMouseEnter={show} onMouseLeave={() => setOpen(false)} onFocus={show} onBlur={() => setOpen(false)}>
      +{count}
      {open && (
        <span className={'rolepop ' + (up ? 'up' : 'down')} role="tooltip">
          <span className="rolepop-head">All roles ({roles.length})</span>
          <span className="rolepop-list">{roles.map((r) => <span className="tag" key={r}>{r}</span>)}</span>
        </span>
      )}
    </span>
  )
}

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
                  {extra > 0 && <RolesChip count={extra} roles={roles} />}
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
                <button className="ghost" disabled={busy} onClick={() => build(p.user_id)}>
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
            <button className="ghost icon-btn" onClick={props.onClear} data-tip="Clear key" aria-label="Clear key">
              <Icon.trash size={16} />
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

      <div className="cols2">
        <div className="col">
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
        </div>
        <div className="col">
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
        </div>
      </div>

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
