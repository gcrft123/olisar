import React, { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { DOCS, DOC_GROUPS } from './docs'
import { Icon, CloseX, type IconName } from './icons'
import { Modal, confirmDialog, promptDialog, toast } from './overlays'
import { uiScale } from './theme'
import { Area, Card, Disclosure, Field, Markdown, Num, SaveBar, SaveDock, Segmented, Select, Text, Toggle, headingsOf, useAsync, useDirtyGuard, useEditable, useFieldIds, usePoll, useSaver } from './ui'

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
      <PageHead icon="persona" title="Persona" sub="Who Olisar is and how it behaves in your server." />
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
          title="About me"
          hint={
            <>
              Olisar's public Discord bio. It's the same across every server, and a short attribution line is added below whatever you write. {(data.desired_bio || '').length}/300.
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
      setErr(e?.message || 'Something went wrong. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sandbox">
      <div className="sandbox-log" ref={logRef}>
        {messages.length === 0 && !busy && (
          <div className="sandbox-empty">
            Try out Olisar's persona. Nothing here is saved.
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
          aria-label="Message Olisar"
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
  const drawer = useRef<HTMLElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  // The drawer stays mounted so it slides rather than pops, which means while closed it sat
  // off-screen with `aria-hidden` over a live textarea and three buttons — tabbable, but
  // hidden from the screen reader describing them. `inert` takes them out of both.
  useEffect(() => { if (drawer.current) drawer.current.inert = !open }, [open])
  return (
    <>
      <button className="testchat-fab" onClick={() => setOpen(true)} aria-label="Open test chat">
        <Icon.sandbox size={17} weight="Bold" /> Test chat
      </button>
      <div className={'chatdrawer-backdrop' + (open ? ' open' : '')} onClick={() => setOpen(false)} aria-hidden="true" />
      <aside ref={drawer} className={'chatdrawer' + (open ? ' open' : '')} role="dialog" aria-label="Test chat">
        <div className="chatdrawer-head">
          <div>
            <div className="chatdrawer-title">Test chat</div>
            <div className="chatdrawer-sub">Uses the saved persona, knowledge base, and tools, but keeps no memory.</div>
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
      <PageHead icon="behavior" title="Behavior" sub="How and when Olisar joins in." />
      <div className="cols2">
        <div className="col">
      <Card title="Engagement" hint="When and where Olisar joins the conversation.">
        <Field label="Name triggers" desc="Comma-separated. Including one of these words in a message addresses Olisar.">
          <Text value={triggers} onChange={(v) => set('name_triggers', v)} placeholder="olisar, oli" />
        </Field>
        <Field label="Reply in DMs"><Toggle value={data.reply_in_dms} onChange={(v) => set('reply_in_dms', v)} label="Answer direct messages" /></Field>
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
      <Card title="Model & tools">
        <Field label="Primary model" desc="If this model is busy, Olisar falls back to the next one down the chain.">
          <Select value={data.default_model} onChange={(v) => set('default_model', v)} options={modelOpts.length ? modelOpts : [{ value: data.default_model, label: data.default_model }]} />
        </Field>
        <Field label="Web search" desc="Let Olisar look things up on the web.">
          <Toggle value={data.grounding_enabled} onChange={(v) => set('grounding_enabled', v)} label="Allow web search" />
        </Field>
        <Field label="Web searches per day" desc="The most lookups Olisar will run in a day.">
          <Num value={data.grounding_daily_cap} onChange={(v) => set('grounding_daily_cap', v)} min={0} unit="searches / day" def={100} />
        </Field>
        <Field label="Status & voice awareness" desc="Let Olisar check a member's live status/activity and who's in voice. Requires the Presence Intent in the Discord Developer Portal.">
          <Toggle value={data.presence_tools_enabled} onChange={(v) => set('presence_tools_enabled', v)} label="Allow presence & voice lookups" />
        </Field>
      </Card>
      <Card title="Memory & summaries">
        <Field label="Context window (messages)" desc="How many recent messages Olisar keeps in view when replying. Higher follows longer conversations but costs more tokens.">
          <Num value={data.context_message_limit} onChange={(v) => set('context_message_limit', v)} min={3} max={100} unit="messages" def={12} />
        </Field>
        {/* Three thresholds that are quota trade-offs, not everyday settings — the sane
            default is the right answer until the free tier starts biting. Folded away so
            the page opens with the one memory control an operator actually reaches for. */}
        <Disclosure summary="Tuning thresholds" hint="Only worth touching if you're hitting rate limits.">
          <Field label="Summary token threshold" desc="Roll a channel up into a summary once it gathers this many new tokens. Lower summarizes more often and costs more quota.">
            <Num value={data.summary_token_threshold} onChange={(v) => set('summary_token_threshold', v)} min={500} step={500} unit="tokens" def={4000} />
          </Field>
          <Field label="Glossary mine threshold" desc="Mine the server glossary for new facts after this many new tokens.">
            <Num value={data.glossary_mine_token_threshold} onChange={(v) => set('glossary_mine_token_threshold', v)} min={300} step={250} unit="tokens" def={1500} />
          </Field>
          <Field label="Persona rebuild (messages)" desc="Rebuild a member's persona after this many new messages from them.">
            <Num value={data.user_persona_msg_threshold} onChange={(v) => set('user_persona_msg_threshold', v)} min={5} unit="messages" def={15} />
          </Field>
        </Disclosure>
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
        <Field label="Confidence threshold" desc="How sure it has to be (0–1) before it speaks up.">
          <Num value={pro.confidence_threshold} onChange={(v) => setP('confidence_threshold', v)} min={0} max={1} step={0.05} def={0.7} />
        </Field>
        <div className="row">
          <Field label="Global cooldown (s)"><Num value={pro.global_cooldown_sec} onChange={(v) => setP('global_cooldown_sec', v)} min={0} unit="seconds" def={60} /></Field>
          <Field label="Channel cooldown (s)"><Num value={pro.channel_cooldown_sec} onChange={(v) => setP('channel_cooldown_sec', v)} min={0} unit="seconds" def={300} /></Field>
          <Field label="Max per hour"><Num value={pro.max_per_hour} onChange={(v) => setP('max_per_hour', v)} min={0} unit="messages" def={6} /></Field>
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
        <Field label="Confidence threshold" desc="How sure it has to be (0–1) before it reacts.">
          <Num value={pro.reaction_threshold ?? 0} onChange={(v) => setP('reaction_threshold', v)} min={0} max={1} step={0.05} def={0} />
        </Field>
        <div className="row">
          <Field label="Channel cooldown (s)"><Num value={pro.reaction_cooldown_sec} onChange={(v) => setP('reaction_cooldown_sec', v)} min={0} unit="seconds" def={60} /></Field>
          <Field label="Max per hour"><Num value={pro.reaction_max_per_hour} onChange={(v) => setP('reaction_max_per_hour', v)} min={0} unit="reactions" def={6} /></Field>
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
  dm_indexing: '/dm-indexing',
  proactive: '/olisar proactive', privacy: '/privacy',
  rate_limit: 'When rate-limited', blank_fallback: 'When it draws a blank',
  access_denied: 'When access is denied',
}

// What a reply actually looks like where the reader will see it. The console's job on
// this page is to answer "does this sound like Olisar" — which was previously an act of
// imagination performed against grey `default: …` text under a bare textarea.
function DiscordPreview({ name, avatar, text }: { name: string; avatar?: string; text: string }) {
  const initial = (name || 'O').trim().slice(0, 1).toUpperCase()
  // Split on {placeholder} so the slots read as slots — they're substituted at send time,
  // and showing them as literal prose is the one thing this preview must not do.
  const parts = text.split(/(\{[a-z_]+\})/gi)
  return (
    <div className="dcp">
      <div className="dcp-msg">
        <div className="dcp-av">
          {avatar ? <img src={avatar} alt="" /> : initial}
        </div>
        <div className="dcp-body">
          <div className="dcp-row">
            <span className="dcp-name">{name || 'Olisar'}</span>
            <span className="dcp-tag">APP</span>
            <span className="dcp-time">Today at 9:14 PM</span>
          </div>
          <div className="dcp-text">
            {text.trim()
              ? parts.map((seg, i) => (/^\{[a-z_]+\}$/i.test(seg)
                  ? <span className="dcp-slot" key={i}>{seg}</span>
                  : <span key={i}>{seg}</span>))
              : <span className="dcp-empty">Olisar stays quiet.</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

export function Messages() {
  const { data, loading } = useAsync<any>(api.getMessages)
  const { data: persona } = useAsync<any>(api.getPersona)
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
  useDirtyGuard(() => dirty)   // not useEditable-backed, so register by hand
  const saver = useSaver(async () => { await api.putMessages(edits); base.current = JSON.stringify(edits) })
  if (loading || !data) return <Spinner />

  return (
    <>
      <PageHead icon="messages" title="Command replies" sub="Rewrite what Olisar says for each command. Leave a box blank to keep the default." />
      <div className="grid2">
      {Object.keys(data).filter((key) => key !== 'privacy').map((key) => {
        // Read defensively: this page renders whatever `/api/messages` returns, and a key
        // that arrives without `placeholders` used to take the whole page to the error
        // boundary. The backend and this frontend ship independently.
        const m = data[key] || {}
        const placeholders: string[] = Array.isArray(m.placeholders) ? m.placeholders : []
        const fallback = typeof m.default === 'string' ? m.default : ''
        return (
        <Card key={key} title={MSG_LABELS[key] ?? key}>
          {/* The card title is the only thing naming this box, and a card title is not a
              label — every one of these announced as an unnamed edit box, fourteen in a
              row. A placeholder is not a name either; it's the default text. */}
          <Area
            value={edits[key] ?? ''}
            onChange={(v) => setEdits({ ...edits, [key]: v })}
            rows={2}
            placeholder={fallback}
            ariaLabel={`${MSG_LABELS[key] ?? key} — reply text`}
          />
          {/* The effective message — your override if you've written one, otherwise the
              default that would actually be sent. Updates as you type. */}
          <DiscordPreview
            name={persona?.name || 'Olisar'}
            avatar={persona?.bot_avatar}
            text={(edits[key] ?? '').trim() || fallback}
          />
          {placeholders.length > 0 && (
            <div className="placeholders">placeholders: {placeholders.map((p: string) => <code key={p}>{`{${p}}`} </code>)}</div>
          )}
        </Card>
        )
      })}
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

// What a row's settings actually mean, in a sentence, derived from the two controls beside
// it. The mode legend answers this once at the top of the page and then scrolls out of
// view; this answers it per channel, where the decision is made.
function channelEffect(mode: string, indexed: boolean, proactive: boolean): string {
  if (mode === 'off') return 'Ignored entirely.'
  const parts: string[] = []
  if (mode === 'memory') parts.push('Reads and remembers')
  else if (mode === 'respond') parts.push('Replies when addressed')
  else if (mode === 'both') parts.push('Reads, remembers and replies when addressed')
  else if (mode === 'resource') parts.push('Carried as reference in every reply')
  else if (mode === 'feed') parts.push('Remembers the last 3 messages')
  // Proactivity is gated on exactly these two modes in bot/cogs/proactive.py, so the
  // clause is only added where the bot can actually act on it.
  if (proactive && (mode === 'respond' || mode === 'both')) parts.push('may chime in unprompted')
  parts.push(indexed ? 'searchable' : 'not searchable')
  return parts.join(' · ') + '.'
}

// The API returns channels already ordered by Discord `position`, and Discord keeps a
// category's channels contiguous within that order — so walking runs of equal `category`
// reproduces the real tree without needing a category id, and degrades correctly through
// the two serializer fallbacks that emit `category: ""`.
function groupByCategory(rows: any[]): { category: string; rows: any[] }[] {
  const groups: { category: string; rows: any[] }[] = []
  for (const c of rows) {
    const cat = c.category || ''
    const last = groups[groups.length - 1]
    if (last && last.category === cat) last.rows.push(c)
    else groups.push({ category: cat, rows: [c] })
  }
  return groups
}

export function Channels() {
  const ed = useEditable<any[]>(api.getChannels)
  const { data: pro } = useAsync<any>(api.getProactivity)
  const [q, setQ] = useState('')
  const saver = useSaver(async () => {
    // One patch per changed row carrying both fields, and rows in parallel. Retuning 30
    // channels used to be 60 requests awaited one after another behind a single Save.
    const origById = new Map((ed.baseline() ?? []).map((c: any) => [c.channel_id, c]))
    const changed = (ed.data ?? []).flatMap((c) => {
      const o = origById.get(c.channel_id)
      if (!o) return []
      const patch: any = { channel_id: c.channel_id }
      if (c.mode !== o.mode) patch.mode = c.mode
      if (c.indexed !== o.indexed) patch.indexed = c.indexed
      return Object.keys(patch).length > 1 ? [patch] : []
    })
    await Promise.all(changed.map((patch) => api.putChannel(patch)))
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
        <div className="hint">Indexing is separate from the mode: it decides whether a channel's messages can be found by search. Turning it off also wipes what's already been indexed there.</div>
      </Card>
      {/* "Channels — 9 configured" over ten rows left the reader counting: is 9 the total,
          or the subset that isn't off? Say both numbers, and say which is which. */}
      <Card title={`Channels — ${configured} of ${rows.length} active`}>
        {rows.length === 0 ? (
          <div className="empty">No channels synced yet. The bot populates this list shortly after it starts; you can also run <code>/olisar watch</code> in a channel.</div>
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <Text value={q} onChange={setQ} placeholder="Filter channels…" ariaLabel="Filter channels" />
            </div>
            {groupByCategory(shown).map((g) => (
              <div className="chan-group" key={g.category || '__none'}>
                {/* Setting a mode on ten channels was ten identical decisions with no way to
                    express "this whole category behaves the same" — which is how operators
                    actually think about a Discord server. Applies to the rows currently
                    shown, so it respects the filter above. */}
                <div className="chan-cat-row">
                  <div className="chan-cat">{g.category || 'No category'}</div>
                  <Select
                    className="chan-bulk"
                    value=""
                    options={[{ value: '', label: `Set all ${g.rows.length}…` }, ...MODE_OPTS]}
                    ariaLabel={`Set the mode for all ${g.rows.length} channels in ${g.category || 'no category'}`}
                    onChange={(v) => { if (v) g.rows.forEach((c: any) => patchRow(c.channel_id, { mode: v })) }}
                  />
                </div>
                {g.rows.map((c) => (
                  <div className="list-row" key={c.channel_id}>
                    <div className="grow">
                      <div className="title">#{c.name} {c.kind === 'forum' && <span className="tag">forum</span>}</div>
                      <div className="meta">{channelEffect(c.mode, c.indexed !== false, !!pro?.enabled)}</div>
                    </div>
                    <div className="chan-ctl mode">
                      <Select value={c.mode} options={MODE_OPTS} onChange={(v) => patchRow(c.channel_id, { mode: v })}
                        ariaLabel={`Mode for #${c.name}`} />
                    </div>
                    <div className="chan-ctl index">
                      <Select
                        value={c.indexed === false ? 'off' : 'on'}
                        options={INDEX_OPTS}
                        ariaLabel={`Search indexing for #${c.name}`}
                        // Turning indexing off doesn't just stop future indexing — it wipes
                        // what this channel already has, threads included. That is a delete
                        // hidden inside a dropdown, so it asks first.
                        onChange={async (v) => {
                          if (v === 'off' && c.indexed !== false) {
                            if (!(await confirmDialog({
                              title: `Stop indexing #${c.name}?`,
                              message: <>This also <strong>erases what's already indexed</strong> for this channel and its threads, so those messages stop turning up in search. Re-enabling it indexes new posts from that point on; <code>/olisar reindex</code> reads the history back.</>,
                              confirmLabel: 'Stop indexing and erase',
                              tone: 'danger',
                            }))) return
                          }
                          patchRow(c.channel_id, { indexed: v === 'on' })
                        }}
                      />
                    </div>
                  </div>
                ))}
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
  { value: 'open', label: 'open — no restriction' },
  { value: 'allow', label: 'allowed — only these roles' },
  { value: 'block', label: 'blocked — never' },
]

// A Discord role chip: the role's own colour as a dot and a tinted border, the way it
// reads in Discord's member list. `color` is "" for an uncoloured role.
function RoleChip({ name, color }: { name: string; color?: string }) {
  const c = color || ''
  return (
    <span className={'rolechip' + (c ? '' : ' plain')} style={c ? { '--rc': c } as React.CSSProperties : undefined}>
      <span className="rolechip-dot" />
      {name}
    </span>
  )
}

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
      : 'Open to everyone. No role restrictions are set.'

  return (
    <>
      <PageHead icon="access" title="Access" sub="Which roles can use Olisar. Server admins always can, and /privacy and /forget-me stay open to everyone." />
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
              <Text value={q} onChange={setQ} placeholder="Filter roles…" ariaLabel="Filter roles" />
            </div>
            {shown.map((r) => (
              <div className="list-row" key={r.role_id}>
                <div className="grow">
                  <div className="title"><RoleChip name={r.name} color={r.color} /></div>
                </div>
                <div style={{ width: 220 }}>
                  <Select value={stateOf(r.role_id)} options={ACCESS_OPTS} onChange={(v) => setState(r.role_id, v)}
                    ariaLabel={`Access for the ${r.name} role`} />
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
  // Poll fast while a backfill is in flight, then once a minute — a settled index doesn't
  // change on its own, and this used to hit the backend every 3.5s for the life of the page.
  const working = !data || !!data.running || (data.channels || []).some((c: any) => c.status !== 'done')
  const poll = usePoll(() => api.reindexStatus().then(setData), working ? 3500 : 60000)
  const start = async () => {
    setBusy(true)
    try { await api.reindex(); setData(await api.reindexStatus()) }
    catch (e: any) { toast(e?.message || 'Couldn’t start re-indexing', 'danger') }
    finally { setBusy(false) }
  }
  const clear = async () => {
    if (!(await confirmDialog({
      title: 'Clear search index?',
      message: 'New messages will still be indexed as they arrive, and "Re-index all" rebuilds the history.',
      confirmLabel: 'Clear index',
      tone: 'danger',
      requirePhrase: { phrase: 'clear index' },
    }))) return
    setBusy(true)
    try { await api.clearIndex(); setData(await api.reindexStatus()); toast('Search index cleared', 'neutral') }
    catch (e: any) { toast(e?.message || 'Couldn’t clear the index', 'danger') }
    finally { setBusy(false) }
  }
  const pct = data && data.total ? Math.round((data.done / data.total) * 100) : 0
  // Active (queued/indexing) first, then done — channels stay listed with their count.
  const rank: Record<string, number> = { indexing: 0, queued: 1, done: 2 }
  const channels = [...(data?.channels || [])].sort(
    (a: any, b: any) => (rank[a.status] - rank[b.status]) || (b.indexed - a.indexed)
  )
  return (
    <Card title="Message search index" hint="Lets Olisar search back through your server's history.">
      {poll.stale && !data && (
        <div className="callout warning">
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">Can't reach the backend, so the index status is unknown.</div>
        </div>
      )}
      {!data ? (poll.stale ? null : <div className="empty">Loading…</div>) : (
        <>
          <div className="reindex-top">
            <div className="reindex-stat">
              {/* `done / total` is BACKFILL progress, not what the index holds — so with no
                  backfill running it read "0 / 0 channels indexed · 128,431 messages",
                  which says both nothing and everything is indexed. Two different facts;
                  only show the progress one while there is progress to report.
                  Coerced rather than read straight: a drifted payload used to throw here
                  and take the whole page to the error boundary. */}
              {Number(data.total ?? 0) > 0 ? (
                <>
                  <b>{data.done ?? 0}</b> / {data.total} channels backfilled
                  <span className="rx-dim"> · {Number(data.indexed_messages ?? 0).toLocaleString()} messages searchable</span>
                </>
              ) : (
                <>
                  <b>{Number(data.indexed_messages ?? 0).toLocaleString()}</b> messages searchable
                  <span className="rx-dim"> · new posts are indexed as they arrive</span>
                </>
              )}
            </div>
            <div className="reindex-actions">
              <button className="primary" onClick={start} disabled={busy}>
                <Icon.refresh size={14} /> {busy ? 'Working…' : 'Re-index all'}
              </button>
              <button className="danger icon-btn" onClick={clear} disabled={busy || !data.indexed_messages} data-tip="Clear index" aria-label="Clear index">
                <Icon.trash size={16} />
              </button>
            </div>
          </div>
          {/* The overall bar only while there's work in flight; hidden once complete. */}
          {data.running && (
            <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
              aria-label="Channels indexed">
              <div className="progress-fill" style={{ transform: `scaleX(${pct / 100})` }} />
            </div>
          )}
          {channels.length > 0 && (
            <div className="reindex-list">
              {channels.map((c: any) => (
                <div className="reindex-row" key={c.channel_id}>
                  <span className="rx-name">{c.kind === 'dm' ? c.name : '#' + c.name}</span>
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

// Erasing everything Olisar has learned is per-*server* destruction, and it used to sit in
// the per-install Settings modal with no server named anywhere on screen. It now lives under
// the glossary and the search index it wipes — and names the server in the dialog, which the
// modal structurally could not do.
function ClearMemoryCard({ serverName }: { serverName?: string }) {
  const [busy, setBusy] = useState(false)
  const clearMemory = async () => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: serverName ? `Clear Olisar's memory of ${serverName}?` : 'Clear memory',
      message: (
        <>
          This erases everything Olisar has learned about this server: conversation memory, summaries, the
          search index, remembered facts, the glossary, usage stats, its read on each member, and the
          knowledge base. Its persona, behavior, channel modes, and command replies are kept.{' '}
          <strong style={{ color: 'var(--danger)' }}>This can't be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: 'clear olisar memory' },
      confirmLabel: 'Clear memory',
    })
    if (!ok) return
    setBusy(true)
    try {
      const r = await api.clearMemory()
      const c = (r && r.counts) || {}
      toast(`Memory cleared. Forgot ${c.messages ?? 0} messages, ${c.facts ?? 0} facts, ${c.profiles ?? 0} member profiles, and ${c.knowledge ?? 0} knowledge sources.`, 'success')
    } catch (e: any) {
      toast(e?.message || 'Couldn’t clear memory', 'danger')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card danger-zone">
      <h2>Danger zone</h2>
      <div className="settings-row between" style={{ marginTop: 0 }}>
        <div>
          <div className="opt-label">Clear memory</div>
          <div className="settings-muted">
            Erases everything on this page and everything Olisar remembers about this server —
            the glossary, the search index, and its read on each member. Persona, behavior,
            channel modes and command replies are kept. This can't be undone.
          </div>
        </div>
        <button className="danger" onClick={clearMemory} disabled={busy}>
          {busy ? <><span className="spinner" /> Clearing…</> : 'Clear memory'}
        </button>
      </div>
    </div>
  )
}

// The counts a destructive action reports are the most consequential receipt in the
// product, and until now they existed for 3.6 seconds inside a toast. record_audit has
// been writing them to audit_log all along; this reads it back.
function ActivityCard() {
  const { data, loading, reload } = useAsync<any>(() => api.getAudit(60))
  const entries: any[] = data?.entries ?? []
  const when = (ts: string | null) => {
    if (!ts) return ''
    const d = new Date(ts)
    return isNaN(+d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  }
  // clear_memory stores its deleted-row counts in `after`; other actions carry other
  // shapes, so render whatever numbers are there rather than assuming a schema.
  const receipt = (after: any): string => {
    const counts = after?.counts
    if (!counts || typeof counts !== 'object') return ''
    return Object.entries(counts)
      .filter(([, v]) => typeof v === 'number' && v > 0)
      .map(([k, v]) => `${(v as number).toLocaleString()} ${k}`)
      .join(' · ')
  }
  return (
    <Card
      title="Activity"
      hint={<>What has been changed on this install, newest first. {data?.install_wide && 'Covers every server this install manages.'}</>}
    >
      {loading ? <div className="empty">Loading…</div>
        : entries.length === 0 ? <div className="empty">Nothing recorded yet.</div> : (
        <>
          <div className="activity">
            {entries.map((e) => (
              <div className={'act-row' + (e.destructive ? ' destructive' : '')} key={e.id}>
                <span className="act-when">{when(e.ts)}</span>
                <span className="act-what">
                  {e.label}
                  {receipt(e.after) && <span className="act-receipt">{receipt(e.after)}</span>}
                </span>
                <span className="act-who">{e.actor}</span>
              </div>
            ))}
          </div>
          <div className="savebar">
            <button className="ghost" onClick={reload}><Icon.refresh size={14} /> Refresh</button>
          </div>
        </>
      )}
    </Card>
  )
}

export function Knowledge({ serverName }: { serverName?: string } = {}) {
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
  const [mining, setMining] = useState('')
  const mine = async (mode: 'memory' | 'index') => {
    setMining(mode)
    try {
      const r: any = mode === 'memory' ? await api.mineFacts() : await api.deepMineFacts()
      const where = mode === 'memory' ? 'memory' : 'the search index'
      const n = r.added || 0
      let msg = n ? `Learned ${n} new fact${n === 1 ? '' : 's'} from ${where}.` : `No new facts found in ${where}.`
      if (mode === 'memory' && r.remaining) msg += ` ${r.remaining} message${r.remaining === 1 ? '' : 's'} left. Mine again to continue.`
      toast(msg, n ? 'success' : 'neutral')
      reloadFacts()
    } catch (e: any) {
      toast(e?.message || 'Mining failed', 'danger')
    } finally {
      setMining('')
    }
  }

  if (loading || lf) return <Spinner />
  const rows = data ?? []
  const factRows = facts ?? []
  return (
    <>
      <PageHead icon="knowledge" title="Knowledge" sub="What you've taught Olisar. The knowledge base holds pages and documents it can look things up in; the glossary holds short facts about your server." />
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
            <Field label="Max pages"><Num value={maxPages} onChange={setMaxPages} min={1} max={100} unit="pages" def={25} /></Field>
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
            <span className={'badge ' + s.status}>{SOURCE_STATUS[s.status] ?? s.status}</span>
            {/* A failed source used to offer Remove and nothing else, so recovering from a
                transient 404 or timeout meant deleting the row and retyping the URL. The
                console already knows the URL; retrying is the obvious next step and it was
                simply missing. */}
            {s.status === 'error' && (
              <button onClick={async () => {
                try {
                  await api.addSource({ type: s.type, uri: s.uri })
                  toast('Queued again — Olisar will retry reading it.', 'success')
                  reload()
                } catch (e: any) { toast(e?.message || 'Couldn’t queue the retry', 'danger') }
              }}>
                <Icon.refresh size={15} /> Retry
              </button>
            )}
            <button className="danger" onClick={async () => {
              // Removing a source drops every passage Olisar read out of it. Re-adding means
              // re-crawling and re-reading against the free quota, so this is not a cheap undo.
              if (!(await confirmDialog({
                title: `Remove ${s.title || s.uri}?`,
                message: <>Olisar forgets {s.chunks ? <><strong>{s.chunks}</strong> passages</> : 'everything'} it read from this source. Re-adding it means reading the whole thing again.</>,
                confirmLabel: 'Remove source',
                tone: 'danger',
              }))) return
              await api.deleteSource(s.id); reload()
            }}>
              <Icon.trash size={15} /> Remove
            </button>
          </div>
        ))}
      </Card>
      <Card title="Glossary" hint="Short facts Olisar carries into every reply: your abbreviations, in-jokes, and who's who. It also picks these up on its own as channels stay active.">
        <div className="row">
          <Field label="Subject"><Text value={subject} onChange={setSubject} placeholder="MN" /></Field>
          <div style={{ flex: 3 }}>
            <Field label="Fact"><Text value={fact} onChange={setFact} placeholder="MN is Movie Night, our Friday watch-party in #cinema" /></Field>
          </div>
        </div>
        <SaveBar saver={factAdder} label="Add fact" />
        <div className="settings-subhead">Mine for facts</div>
        <div className="btn-row">
          <button onClick={() => mine('memory')} disabled={!!mining}>
            <Icon.bolt size={15} /> {mining === 'memory' ? 'Mining…' : 'Mine from memory'}
          </button>
          <button onClick={() => mine('index')} disabled={!!mining}>
            <Icon.search size={15} /> {mining === 'index' ? 'Mining…' : 'Deep mine from index'}
          </button>
        </div>
        <div className="settings-subhead">Glossary ({factRows.length})</div>
        {factRows.length === 0 && <div className="empty">Nothing learned yet. Olisar fills this in as it summarizes active channels, or add the first fact above.</div>}
        {factRows.map((f) => (
          <div className="list-row" key={f.id}>
            <div className="grow">
              <div className="title" data-tip={f.fact}>{f.fact}</div>
              <div className="meta">
                {f.subject && <span className="tag">{f.subject}</span>}
                {f.mentions > 1 ? `seen ${f.mentions}×` : 'seen once'}
              </div>
            </div>
            <button className="danger" onClick={async () => {
              if (!(await confirmDialog({
                title: 'Delete this fact?',
                message: <>“{f.fact}” — Olisar stops carrying this into replies. It may mine it again later if it comes up in conversation.</>,
                confirmLabel: 'Delete fact',
                tone: 'danger',
              }))) return
              await api.deleteFact(f.id); reloadFacts()
            }}>
              <Icon.trash size={15} /> Delete
            </button>
          </div>
        ))}
      </Card>
        </div>
        <div className="col">
          <SearchIndexCard />
        </div>
      </div>
      <ActivityCard />
      <ClearMemoryCard serverName={serverName} />
    </>
  )
}

// Badge text is written in the case it renders (the stylesheet no longer capitalizes),
// and these two come off the API as lowercase enum values.
const SOURCE_STATUS: Record<string, string> = {
  ready: 'Ready', ingesting: 'Ingesting', queued: 'Queued', error: 'Error',
}
const MEMORY_KIND: Record<string, string> = {
  fact: 'Fact', preference: 'Preference', event: 'Event',
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
          `an update. Bump the version in your code to ship it as one.`,
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
              {e.user_modified && <span className="badge">Edited</span>}
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
            <Toggle value={e.enabled} onChange={(v) => props.onToggle(e.key, v)} ariaLabel={`Enable ${e.name}`} />
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
            <Icon.warn size={14} weight="Bold" /> Removed from the marketplace{mkt.gone ? '' : ' by the publisher'}. It keeps working, but won't get updates.
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
  const titleId = useId()
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
    <Modal className="import-modal" labelledBy={titleId} onClose={props.onClose} dismissable={!props.busy}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head"><h2 id={titleId}>{props.title}</h2><p>{props.subtitle}</p></div>

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
                <div className="settings-muted">No automated risk review this time. Read the capabilities below carefully before installing.</div>
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
              <div className="import-warn">This runs someone else's code in your bot. Grant only what you trust. Anything you leave unchecked won't work for it.</div>
            </>
          )}

          {preview.exists && <div className="settings-err" style={{ marginTop: 14 }}>An extension named “{preview.id}” is already installed. Delete it first to reinstall.</div>}
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
    </Modal>
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
  const titleId = useId()

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
    <Modal className="import-modal" labelledBy={titleId} onClose={props.onClose} dismissable={!busy}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head">
          <h2 id={titleId}>Report extension</h2>
          <p>{props.target.id || `${props.target.namespace}/${props.target.name}`}</p>
        </div>
        {done ? (
          <>
            <div className="import-review"><div className="settings-muted">Thanks — your report was sent to the Olisar team.</div></div>
            <div className="import-foot"><button className="primary" onClick={props.onClose}>Done</button></div>
          </>
        ) : (
          <>
            <div className="import-review">
              <div className="settings-subhead">What went wrong?</div>
              <Area
                value={desc} onChange={setDesc} rows={5}
                placeholder="What the extension did, when, and why it concerned you."
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
    </Modal>
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
  const titleId = useId()
  if (!r) {
    return (
      <Modal className="deny-modal scan" labelledBy={titleId} onClose={props.onClose}>
          <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
          <h2 className="deny-title" id={titleId}>Security review</h2>
          <div className="deny-sub">{props.subject}</div>
          <RiskMeter score={0} band="ok" scanning />
          <div className="deny-verdict" style={{ textAlign: 'center' }}>Checking the source for risky behavior…</div>
      </Modal>
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
    <Modal className={'deny-modal ' + (unavailable ? '' : 'split ') + tone} labelledBy={titleId}
      onClose={props.onClose} dismissable={!props.publishing}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <h2 className="deny-title" id={titleId}>{title}</h2>
        <div className="deny-sub">{props.subject}</div>

        {unavailable ? (
          <div className="callout warning">
            <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
            <div className="callout-body">
              {inlineCode(r.message || 'The security review couldn’t run, so publishing is blocked. Try again later.')}
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
    </Modal>
  )
}

// Import an .olx file: pick → preview → the shared consent gate → install.
function ImportDialog(props: { onClose: () => void; onImported: (key: string) => void }) {
  const [bundle, setBundle] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const titleId = useId()

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
    <Modal className="import-modal" labelledBy={titleId} onClose={props.onClose} dismissable={!busy}>
        <button className="settings-close" onClick={props.onClose} aria-label="Close" title="Close"><CloseX size={16} /></button>
        <div className="settings-head">
          <h2 id={titleId}>Import extension</h2>
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
          aria-label="Choose an .olx file"
          onChange={(ev) => { const f = ev.target.files?.[0]; if (f) onFile(f); ev.target.value = '' }}
        />
    </Modal>
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
      message: 'Your new namespace in the marketplace (a-z 0-9 _ -). Verification carries over.',
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
          <Text value={q} onChange={setQ} placeholder="Search the marketplace…" ariaLabel="Search the marketplace" />
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

// The code editor ("Build" mode) is heavy (Monaco) and operator-only, so it loads only
// when an operator drills in to create or edit an extension.
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
    const changed = (ed.data ?? []).filter((e) => e.enabled !== orig.get(e.key))
    await Promise.all(changed.map((e) => api.putExtension({ key: e.key, enabled: e.enabled })))
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
      // Which row the detail pane is showing was carried by a CSS class alone, so six
      // buttons announced identically. `.on` is a green dot with no text equivalent, which
      // is meaning in colour only — the state goes in the accessible name instead.
      aria-current={effective?.key === e.key ? 'true' : undefined}
      onClick={() => setSelKey(e.key)}
    >
      <span className="dot" />
      <span className="nm">{e.name}</span>
      <span className="visually-hidden">{e.enabled ? '— enabled' : '— disabled'}</span>
      {mktStatus[e.key]?.update_available && <span className="cust" style={{ color: 'var(--accent)' }} role="img" aria-label="Update available" data-tip="Update available"><Icon.update size={13} /></span>}
      {mktStatus[e.key]?.yanked && <span className="cust" style={{ color: 'var(--warn)' }} role="img" aria-label="Removed from marketplace" data-tip="Removed from marketplace"><Icon.warn size={13} /></span>}
      {pubStatus[e.key]?.has_changes && <span className="dot" style={{ background: 'var(--warn)', boxShadow: 'none' }} role="img" aria-label="Unpublished changes" data-tip="Unpublished changes" />}
      {e.editable && <span className="cust">{e.origin === 'marketplace' ? 'Market' : 'Custom'}</span>}
    </button>
  )

  return (
    <>
      <div className="head-row">
        <PageHead icon="extensions" title="Extensions" sub="Optional packages of extra features." />
        {props.isOperator && (
          <div className="head-actions">
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
          <div className="ext-rail-head"><Text value={q} onChange={setQ} placeholder="Search extensions…" ariaLabel="Search extensions" /></div>
          <Segmented className="ext-seg" ariaLabel="Filter extensions" value={filter} onChange={setFilter}
            options={[
              { value: 'all' as const, label: 'All' },
              { value: 'on' as const, label: 'Enabled' },
              { value: 'custom' as const, label: 'Custom' },
            ]} />
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
      <nav className="docs-nav" aria-label="Documentation">
        <input
          className="docs-search"
          type="text"
          value={q}
          placeholder="Search docs…"
          aria-label="Search docs"
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
                  role="button"
                  tabIndex={0}
                  aria-current={s.id === active ? 'page' : undefined}
                  onClick={() => setActive(s.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActive(s.id) } }}
                >
                  {s.title}
                </div>
              ))}
            </div>
          )
        })}
      </nav>

      {/* A plain div, not <main>: the console already has one <main> around the whole page,
          and a nested second one leaves assistive tech two "main content" targets. */}
      <div className="docs-content">
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
      </div>

      {headings.length > 0 && (
        <aside className="docs-toc" aria-label="On this page">
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
type MemberRole = { id: string; name: string }

function RolesChip({ count, roles, colourOf }: { count: number; roles: MemberRole[]; colourOf: (r: MemberRole) => string }) {
  const [open, setOpen] = useState(false)
  const [up, setUp] = useState(false)
  const [right, setRight] = useState(false)
  const ref = useRef<HTMLButtonElement>(null)
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (r) {
      const below = window.innerHeight - r.bottom
      setUp(below < 280 && r.top > below)  // open upward only when there's more room above
      // …and to the left when the 300px card would run off the right edge. The card's width
      // is a CSS length so it scales with --ui-scale; the rect and innerWidth do not.
      setRight(r.left + 300 * uiScale() > window.innerWidth - 16)
    }
    setOpen(true)
  }
  return (
    // A real button. This was a bare `tabIndex={0}` span with no role and no name, so a
    // keyboard user landed on something that announced only "+2" — and `.rolepop-wrap`
    // killed the UA outline without providing a replacement, making it the one control in
    // the console you could focus with no indication you had.
    <button ref={ref} type="button" className="tag more rolepop-wrap"
      aria-label={`Show all ${roles.length} roles`} aria-expanded={open}
      onMouseEnter={show} onMouseLeave={() => setOpen(false)} onFocus={show} onBlur={() => setOpen(false)}
      onClick={() => (open ? setOpen(false) : show())}>
      +{count}
      {open && (
        <span className={'rolepop ' + (up ? 'up' : 'down') + (right ? ' right' : '')} role="tooltip">
          <span className="rolepop-head">All roles ({roles.length})</span>
          <span className="rolepop-list">{roles.map((r) => <RoleChip key={r.id || r.name} name={r.name} color={colourOf(r)} />)}</span>
        </span>
      )}
    </button>
  )
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) } catch { return '—' }
}

export function Members() {
  const { data, loading } = useAsync<any[]>(api.getProfiles)
  // Roles come back on the profile as {id, name}; the colour lives on /api/roles. Join by
  // id — role names are not unique in a Discord guild.
  const { data: guildRoles } = useAsync<any[]>(api.getRoles)
  const roleColour = React.useMemo(() => {
    const byId = new Map((guildRoles ?? []).map((r: any) => [String(r.role_id), r.color || '']))
    return (r: MemberRole) => byId.get(String(r.id)) || ''
  }, [guildRoles])
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
        || (r.roles || []).some((x: MemberRole) => (x.name || '').toLowerCase().includes(term))
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
        sub="The private impression Olisar forms of each member. Anyone can wipe theirs with /forget-me."
      />
      <Card title={`${rows.length} known · ${learned} with an impression`}>
        <Text value={q} onChange={setQ} placeholder="Filter by name, role, or impression…" ariaLabel="Filter members by name, role, or impression" />
      </Card>
      {rows.length === 0 && <Card title="Profiles"><div className="empty">No member profiles yet. Olisar builds them as people talk in channels it remembers.</div></Card>}
      {rows.length > 0 && shown.length === 0 && <Card title="Profiles"><div className="empty">No members match “{q}”.</div></Card>}
      <div className="member-grid">
        {shown.map((p) => {
          const roles: MemberRole[] = p.roles || []
          const extra = roles.length - MAX_ROLES
          const impression = impressionOf(p)
          const busy = !!building[p.user_id]
          return (
            <div className="member-card" key={p.user_id}>
              <div className="member-head">
                <span className="member-av">
                  {p.avatar
                    ? <img src={p.avatar} alt="" loading="lazy" />
                    : (p.display_name || '?').trim().slice(0, 1).toUpperCase()}
                </span>
                <span className="member-name">{p.display_name}</span>
              </div>
              {roles.length > 0 && (
                <div className="member-roles">
                  {roles.slice(0, MAX_ROLES).map((r) => (
                    <RoleChip key={r.id || r.name} name={r.name} color={roleColour(r)} />
                  ))}
                  {extra > 0 && <RolesChip count={extra} roles={roles} colourOf={roleColour} />}
                </div>
              )}
              {impression
                ? <div className="member-impression">{impression}</div>
                : <div className="member-none">No impression yet.</div>}
              {p.memories?.length > 0 && (
                <div className="member-memories">
                  {p.memories.map((m: any, i: number) => (
                    <div className="mem" key={i}><span className={'badge ' + m.kind}>{MEMORY_KIND[m.kind] ?? m.kind}</span> {m.content}</div>
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

// A key input is a plain <input> rather than <Text> because it carries autoComplete/spellCheck
// of its own — so it has to claim the enclosing Field's ids by hand.
//
// Masked by default. These are secrets: the page's own header says a saved key is never
// shown again, and the app calls itself a secure console — but the field rendered a pasted
// Gemini key in full, at 13.5px, for anyone screen-sharing or recording. Reveal is a
// deliberate act, and it re-masks on blur so a revealed key can't be left on screen.
function KeyInput(props: { value: string; placeholder: string; onChange: (v: string) => void }) {
  const f = useFieldIds()
  const [shown, setShown] = useState(false)
  return (
    <div className="key-input">
      <input
        type={shown ? 'text' : 'password'}
        id={f?.id}
        aria-labelledby={f?.labelId}
        aria-describedby={f?.descId}
        autoComplete="off"
        spellCheck={false}
        className="mono"
        placeholder={props.placeholder}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        onBlur={() => setShown(false)}
      />
      {!!props.value && (
        <button
          type="button"
          className="ghost icon-btn key-reveal"
          onClick={() => setShown((v) => !v)}
          data-tip={shown ? 'Hide' : 'Reveal'}
          aria-label={shown ? 'Hide the key' : 'Reveal the key'}
          aria-pressed={shown}
        >
          {shown ? <Icon.eyeOff size={16} /> : <Icon.eye size={16} />}
        </button>
      )}
    </div>
  )
}

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
  // The placeholder is an example, never the status: a placeholder disappears the moment
  // you type, so "a key is already saved" vanished exactly as you were about to overwrite
  // it. Status is a persistent line under the field instead.
  const placeholder = props.example || 'Paste your key'
  const state = s.dashboard
    ? 'Saved here. Leave the field blank to keep it.'
    : s.env
      ? 'Coming from this machine’s environment. Paste a key to override it.'
      : 'Not set.'
  return (
    <Field label={props.label} desc={props.desc}>
      <KeyInput placeholder={placeholder} value={props.value} onChange={props.onChange} />
      <div className="key-status">
        {s.dashboard ? (
          <>
            {/* "set in dashboard" read equally as "done" and as an instruction to go do it. */}
            <span className="badge ready">Saved</span>
            <button className="ghost icon-btn" onClick={props.onClear} data-tip="Remove this key" aria-label={`Remove the saved ${props.label}`}>
              <Icon.trash size={16} />
            </button>
          </>
        ) : s.env ? (
          <span className="badge">From environment</span>
        ) : (
          <span className="badge missing">Not set</span>
        )}
        <span className="key-state">{state}</span>
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
  // Both of these must run before the loading return: a hook called only on the render
  // where data has arrived is a different hook count than the render before it, which is
  // a hard React crash rather than a degraded page. `edits` exists from the first render,
  // so there is nothing to wait for.
  const dirty = Object.values(edits).some((v) => v.trim() !== '')
  useDirtyGuard(() => dirty)   // not useEditable-backed, so register by hand

  if (loading || !data) return <Spinner />
  const set = (k: string, v: string) => setEdits({ ...edits, [k]: v })
  // Autofilled from the environment (local-only) unless the operator has edited the field.
  const val = (k: string) => edits[k] ?? (data[k]?.value ?? '')
  const clear = async (k: string) => { await api.clearKey(k); reload() }
  const st = (k: string): KeyStatus => data[k] ?? { dashboard: false, env: false }
  const A = (href: string, text: string) => <a href={href} target="_blank" rel="noreferrer">{text}</a>

  return (
    <>
      <PageHead
        icon="keys"
        title="API keys"
        sub="One set of keys powers every server on this install. Once saved, a key is never shown again."
      />

      <div className="cols2">
        <div className="col">
      <Card
        title="Google Gemini"
        hint="Required. Powers everything Olisar says. The free tier is enough to run the bot."
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
        hint="Optional. Only used by the Star Citizen extension, and only to raise its rate limits."
      >
        <KeyField
          fieldKey="uex_api_key"
          label="UEX API token"
          desc={<>Register an app at {A('https://uexcorp.uk/api', 'uexcorp.uk → API')} to get a token. Leave blank to use UEX's public access.</>}
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
        hint="Optional. Turns on image generation. Without it, Olisar says it can't make images."
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
// ── Usage & rate limits ─────────────────────────────────────────────────────
const U_SERIES = ['us0', 'us1', 'us2', 'us3', 'us4', 'us5']
const U_RPD_LIMIT = 1500 // free-tier requests-per-day, per model — the daily limit line
const U_SOURCE_LABEL: Record<string, string> = {
  conversation: 'Conversation', summary: 'Summaries', persona: 'Personas', glossary: 'Glossary',
  embed: 'Embeddings', vision: 'Vision', grounding: 'Grounding', proactivity: 'Proactivity',
  catchup: 'Catch-up', review: 'Extension review', extension: 'Extensions', status: 'Status', other: 'Other',
}
// Plain-language explanations for the more technical process labels — shown as a hover
// tooltip on that legend row (data-tip). Add entries here to explain more of them.
const U_SOURCE_TIP: Record<string, string> = {
  embed: 'Lets Olisar search its memory and knowledge base by meaning, not just exact words.',
}
const uShort = (m: string) => m.replace('gemini-', '').replace(/-latest$/, '').replace(/-0*(\d)/, '-$1')
const uReq = (n: number) => (n >= 1000 ? n.toLocaleString() : String(n))
const uTok = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'k' : String(n))
function uSmooth(pts: { x: number; y: number }[]) {
  if (!pts.length) return ''
  let d = `M${pts[0].x} ${pts[0].y}`
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], mx = (a.x + b.x) / 2
    d += ` C${mx} ${a.y} ${mx} ${b.y} ${b.x} ${b.y}`
  }
  return d
}

// Both charts used to draw into a fixed 940-unit viewBox stretched to the card with
// `preserveAspectRatio="none"`, which scales x and y independently — so every label, tick and
// caption inside them condensed as the card narrowed. Measure the box and draw at its real
// size instead: one unit of viewBox is one CSS pixel, so nothing is scaled at all.
function useChartWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(fallback)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => {
      const next = Math.round(e.contentRect.width)
      if (next > 0) setW(next)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

// Endpoint value tags sit at their series' last y, so two series that finish close together
// print on top of each other. Walk them in y order and push apart to a minimum gap.
function spread(ys: number[], min = 13): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y)
  for (let k = 1; k < order.length; k++) {
    if (order[k].y - order[k - 1].y < min) order[k].y = order[k - 1].y + min
  }
  const out = ys.slice()
  for (const { y, i } of order) out[i] = y
  return out
}

// A chart is `role="img"`, which is a leaf — nothing inside it reaches a screen reader, and
// an aria-label can't carry a series. Every chart therefore ships the same numbers as a
// real table, visually hidden, so the data is available rather than merely described.
function ChartTable({ caption, columns, rows }: {
  caption: string
  columns: string[]
  rows: (string | number)[][]
}) {
  // The class goes on a wrapping div, not the table: `display: table` treats `width: 1px`
  // as a *minimum* and expands to fit its content, so an sr-only table sized itself to its
  // widest row and pushed the page sideways. A block wrapper actually clips.
  return (
    <div className="visually-hidden">
    <table>
      <caption>{caption}</caption>
      <thead><tr>{columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <th scope="row">{r[0]}</th>
            {r.slice(1).map((v, j) => <td key={j}>{v}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}

function DailyReqChart({ series, labels, limit }: { series: { key: string; cls: string; values: number[] }[]; labels: string[]; limit: number }) {
  const [box, W] = useChartWidth(940)
  const H = 250, x0 = 46, x1 = W - 66, y0 = H - 26, y1 = 18
  const n = labels.length
  const dataMax = Math.max(1, ...series.flatMap((s) => s.values))
  // Zoom to the data with headroom above the top point; only let the limit lift the scale
  // when it sits just above the data, so a far-off cap doesn't squash the lines.
  let yMax = dataMax * 1.18
  if (limit > 0 && limit <= dataMax * 1.25) yMax = Math.max(yMax, limit * 1.06)
  const limitInRange = limit > 0 && limit <= yMax
  const xAt = (i: number) => (n <= 1 ? x0 : x0 + ((x1 - x0) * i) / (n - 1))
  const yAt = (v: number) => y0 - (v / yMax) * (y0 - y1)
  const primary = series[0]
  const ticks = [yMax * 0.33, yMax * 0.66].map((v) => Math.round(v / 100) * 100)
  // Roughly 46px per label before they start colliding at this type size.
  const stride = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((x1 - x0) / 46))))
  const tagY = spread(series.map((s) => yAt(s.values[n - 1] || 0)))
  return (
    <div className="u-chartbox" ref={box}>
    <svg className="u-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img"
      aria-label={`Daily requests per model over the last ${n} days`}>
      <line className="u-grid" x1={x0} y1={y0} x2={x1} y2={y0} />
      {ticks.map((v, i) => (
        <g key={i}>
          <line className="u-grid" x1={x0} y1={yAt(v)} x2={x1} y2={yAt(v)} strokeDasharray="2 6" />
          <text className="u-axis" x={x0 - 8} y={yAt(v) + 3} textAnchor="end">{v >= 1000 ? v / 1000 + 'k' : v}</text>
        </g>
      ))}
      <text className="u-axis" x={x0 - 8} y={y0 + 3} textAnchor="end">0</text>
      {limitInRange ? (
        <>
          <line className="u-limit" x1={x0} y1={yAt(limit)} x2={x1} y2={yAt(limit)} strokeDasharray="5 4" />
          <text className="u-limit-txt" x={x0 + 4} y={yAt(limit) - 5}>RPD limit · {limit.toLocaleString()} / model</text>
        </>
      ) : (
        <text className="u-limit-txt" x={x1} y={y1 + 9} textAnchor="end">RPD limit · {limit.toLocaleString()} / model</text>
      )}
      {primary && <path className={'u-area ' + primary.cls} d={`${uSmooth(primary.values.map((v, i) => ({ x: xAt(i), y: yAt(v) })))} L${x1} ${y0} L${x0} ${y0} Z`} />}
      {series.slice().reverse().map((s) => (
        <path key={s.key} className={'u-line ' + s.cls + (s === primary ? ' primary' : '')} d={uSmooth(s.values.map((v, i) => ({ x: xAt(i), y: yAt(v) })))} />
      ))}
      {series.map((s, si) => {
        const v = s.values[n - 1] || 0
        return (
          <g key={s.key} className={s.cls}>
            <circle className="u-dot" cx={x1} cy={yAt(v)} r={s === primary ? 4.5 : 4} />
            <text className="u-tag" x={x1 + 8} y={tagY[si] + 3}>{uReq(v)}</text>
          </g>
        )
      })}
      {labels.map((l, i) => (i % stride === 0 || i === n - 1 ? <text key={i} className="u-axis" x={xAt(i)} y={y0 + 18} textAnchor="middle">{l}</text> : null))}
    </svg>
    </div>
  )
}

function MiniArea({ values, limit, limitLabel, cls }: { values: number[]; limit: number; limitLabel: string; cls: string }) {
  const [box, W] = useChartWidth(440)
  const H = 138, x0 = 8, x1 = W - 8, y0 = H - 16, y1 = 22
  const n = values.length
  const dataMax = Math.max(1, ...values)
  let yMax = dataMax * 1.2
  if (limit > 0 && limit <= dataMax * 1.25) yMax = Math.max(yMax, limit * 1.06)
  const limitInRange = limit > 0 && limit <= yMax
  const xAt = (i: number) => (n <= 1 ? x1 : x0 + ((x1 - x0) * i) / (n - 1))
  const yAt = (v: number) => y0 - (v / yMax) * (y0 - y1)
  const pts = values.map((v, i) => ({ x: xAt(i), y: yAt(v) }))
  return (
    <div className="u-chartbox" ref={box}>
    <svg className={'u-chart ' + cls} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img"
      aria-label={`Daily peak tokens per minute, against ${limitLabel}`}>
      <line className="u-grid" x1={x0} y1={y0} x2={x1} y2={y0} />
      {limitInRange ? (
        <>
          <line className="u-limit" x1={x0} y1={yAt(limit)} x2={x1} y2={yAt(limit)} strokeDasharray="5 4" />
          <text className="u-limit-txt" x={x1} y={yAt(limit) - 5} textAnchor="end">{limitLabel}</text>
        </>
      ) : (
        <text className="u-limit-txt" x={x1} y={y1 + 9} textAnchor="end">{limitLabel}</text>
      )}
      <path className="u-area" d={`${uSmooth(pts)} L${x1} ${y0} L${x0} ${y0} Z`} />
      <path className="u-line" d={uSmooth(pts)} />
    </svg>
    </div>
  )
}

function DonutChart({ items, total, unit }: { items: { label: string; value: number; tip?: string }[]; total: number; unit: string }) {
  const size = 190, c = size / 2, r = 74, sw = 16, gapPx = 8
  const C = 2 * Math.PI * r
  const priced = items.filter((it) => it.value > 0).map((it) => ({ ...it, frac: it.value / (total || 1) }))
  // Fold negligible (<5%) slices into one "Other" so tiny arcs don't overlap. The
  // Other legend row lists what's inside (name + share) on hover via data-tip.
  const small = priced.filter((it) => it.frac < 0.05)
  let base: { label: string; value: number; frac: number; tip?: string }[]
  if (small.length >= 2) {
    const val = small.reduce((s, x) => s + x.value, 0)
    base = [
      ...priced.filter((it) => it.frac >= 0.05),
      { label: 'Other', value: val, frac: val / (total || 1), tip: small.map((x) => `${x.label} ${Math.round(x.frac * 100)}%`).join(' · ') },
    ]
  } else {
    base = priced
  }
  // Give every slice a minimum rendered arc so tiny ones don't collapse under the round
  // caps and overlap; pay for that surplus by shrinking the largest slice, so the ring
  // stays 360° and roughly in proportion (the legend keeps the true percentages).
  const drawable = C - base.length * gapPx
  const minArc = sw + 3
  const arcs = base.map((s) => ({ ...s, arc: s.frac * drawable }))
  let deficit = 0
  for (const a of arcs) if (a.arc < minArc) { deficit += minArc - a.arc; a.arc = minArc }
  if (deficit > 0) {
    const big = arcs.reduce((x, y) => (y.arc > x.arc ? y : x))
    big.arc = Math.max(minArc, big.arc - deficit)
  }
  let cursor = 0
  const segs = arcs.map((s, i) => {
    const startPx = cursor
    cursor += s.arc + gapPx
    return { ...s, startPx, cls: s.label === 'Other' ? 'us-mut' : U_SERIES[i % U_SERIES.length] }
  })
  return (
    <div className="u-donut-wrap">
      <svg className="u-donut" viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        <circle cx={c} cy={c} r={r} fill="none" className="u-donut-track" strokeWidth={sw} />
        {segs.map((s) => {
          const dash = Math.max(0.5, s.arc - sw)
          const rot = ((s.startPx + sw / 2) / C) * 360 - 90
          return (
            <circle key={s.label} cx={c} cy={c} r={r} fill="none" className={'u-donut-seg ' + s.cls}
              strokeWidth={sw} strokeLinecap="round" strokeDasharray={`${dash} ${C - dash}`}
              transform={`rotate(${rot} ${c} ${c})`} />
          )
        })}
        <text x={c} y={c - 2} textAnchor="middle" className="u-donut-total">{uReq(total)}</text>
        <text x={c} y={c + 15} textAnchor="middle" className="u-donut-sub">{unit}</text>
      </svg>
      <div className="u-donut-legend">
        {segs.map((s) => (
          <div className={'u-dl ' + s.cls} key={s.label} data-tip={s.tip} aria-label={s.tip ? `${s.label}: ${s.tip}` : undefined}>
            <span className="d" /><span className="nm">{s.label}</span>
            <span className="v">{uReq(s.value)}</span>
            <span className="pc">{Math.round(s.frac * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// 0 = all time. "Today" is gone: with a one-day window `prev` fell back to {0,0}, so both
// KPI tiles printed a green "+0% vs yesterday" for a comparison that was never made, and a
// single-point series pinned every x to x0 and drew a fictional diagonal across the card.
const U_RANGES: { value: number; label: string }[] = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 0, label: 'Forever' },
]

export function Usage() {
  const [days, setDays] = useState(7)
  const [showIdle, setShowIdle] = useState(false)
  const { data, loading } = useAsync<any>(() => api.getUsage(days), [days])
  const [live, setLive] = useState<any>(null)
  // No .catch here on purpose: usePoll needs the rejection to know the backend is gone.
  const livePoll = usePoll(() => api.getUsageLive().then(setLive), 4000)
  if (loading || !data) return <Spinner />

  const models: any[] = data.by_model || []
  const clsFor: Record<string, string> = {}
  models.forEach((m, i) => { clsFor[m.model] = U_SERIES[i % U_SERIES.length] })
  const daily: any[] = data.daily || []
  // The server buckets a long window (weekly past ~10 weeks, monthly past ~2 years) and
  // says so, so the axis labels the bucket rather than guessing from the point count.
  const bucket: number = data.bucket_days || 1
  const fmt = (d: string, opts: Intl.DateTimeFormatOptions) =>
    new Date(d + 'T00:00:00Z').toLocaleDateString(undefined, opts)
  const labels = daily.map((d) => (
    bucket === 1 ? fmt(d.day, { weekday: 'short' })
      : bucket === 7 ? fmt(d.day, { month: 'short', day: 'numeric' })
      : fmt(d.day, { month: 'short', year: '2-digit' })
  ))
  // What the range control is actually showing, said once and reused.
  const windowLabel = data.all_time
    ? `all time · since ${fmt(data.start, { month: 'short', day: 'numeric', year: 'numeric' })}`
    : `last ${data.window_days} ${data.window_days === 1 ? 'day' : 'days'}`
  const bucketLabel = bucket === 1 ? 'per day' : bucket === 7 ? 'per week' : 'per month'
  const chartSeries = models.filter((m) => m.requests > 0).slice(0, 6).map((m) => ({ key: m.model, cls: clsFor[m.model], values: daily.map((d) => d.by_model[m.model] || 0) }))
  const last = daily[daily.length - 1] || { requests: 0, tokens: 0 }
  const prev = daily[daily.length - 2] || { requests: 0, tokens: 0 }
  const pct = (a: number, b: number) => (b > 0 ? Math.round(((a - b) / b) * 100) : 0)
  const peak = data.peak || { rpm: {}, tpm: 0, tpm_limit: 1000000 }
  const tpmLimit = peak.tpm_limit || 1000000
  const tpmSeries = daily.map((d) => d.peak_tpm || 0)
  const bySource: any[] = data.by_source || []
  const srcTotal = bySource.reduce((s, x) => s + x.requests, 0) || 1
  const liveModels: any[] = (live && live.models) || []
  const delta = (p: number) => (<span className={p >= 0 ? 'up' : 'dn'}>{p >= 0 ? '+' : ''}{p}%</span>)
  const rpmHot = !!(peak.rpm && peak.rpm.cap && peak.rpm.value / peak.rpm.cap > 0.75)
  // The fallback chain is ten models deep and most of it is idle on any given day. Six
  // all-zero rows with empty meters sat between the reader and the four rows carrying data;
  // the chain is worth being able to see, not worth reading past every time.
  const usedModels = models.filter((m: any) => m.requests_today > 0 || m.requests > 0)
  const idleModels = models.filter((m: any) => !(m.requests_today > 0 || m.requests > 0))
  const shownModels = showIdle ? models : usedModels

  return (
    <>
      <PageHead icon="usage" title="Usage & rate limits" sub="Every Gemini call this install makes, across all servers: by model, by day, and what's driving it." />
      <div className="u-kpis">
        <Card>
          <h2 className="u-eyebrow">Requests · today</h2>
          <div className="u-big">{uReq(last.requests)}</div>
          <div className="u-delta">{delta(pct(last.requests, prev.requests))} vs yesterday</div>
        </Card>
        <Card>
          <h2 className="u-eyebrow">Tokens · today</h2>
          <div className="u-big">{uTok(last.tokens)}</div>
          <div className="u-delta">{delta(pct(last.tokens, prev.tokens))} vs yesterday</div>
        </Card>
        <Card>
          <h2 className="u-eyebrow">Peak · requests / min</h2>
          <div className="u-big">{peak.rpm?.value || 0} <s>/ {peak.rpm?.cap || '—'}</s></div>
          <div className="u-track"><i className={rpmHot ? 'warn' : ''} style={{ width: `${Math.min(100, peak.rpm?.cap ? (peak.rpm.value / peak.rpm.cap) * 100 : 0)}%` }} /></div>
          <div className="u-delta">{peak.rpm?.model ? uShort(peak.rpm.model) : 'no calls yet today'}</div>
        </Card>
        <Card>
          <h2 className="u-eyebrow">Peak · tokens / min</h2>
          <div className="u-big">{uTok(peak.tpm || 0)} <s>/ {uTok(tpmLimit)}</s></div>
          <div className="u-track"><i style={{ width: `${Math.min(100, ((peak.tpm || 0) / tpmLimit) * 100)}%` }} /></div>
          <div className="u-delta">today's peak per-minute tokens</div>
        </Card>
      </div>

      <Card>
        {/* The control lives here, on the card it governs, rather than floating above four
            KPI tiles that are always today's. */}
        <div className="u-cardhead">
          <div><h2 className="u-ttl">Requests over time</h2>
            <div className="u-hint">per model · {bucketLabel} · {windowLabel} · dashed line = daily request limit</div></div>
          <Segmented className="useg" ariaLabel="Usage range" value={days} onChange={setDays} options={U_RANGES} />
        </div>
        <div className="u-legend">{chartSeries.map((s) => (<span key={s.key} className={'lg ' + s.cls}><span className="d" />{uShort(s.key)}</span>))}</div>
        {daily.length ? (
          <>
            <DailyReqChart series={chartSeries} labels={labels} limit={U_RPD_LIMIT} />
            <ChartTable
              caption={`Requests per model, ${bucketLabel}, ${windowLabel}`}
              columns={['Period', ...chartSeries.map((x) => uShort(x.key))]}
              rows={labels.map((l, i) => [l, ...chartSeries.map((x) => x.values[i] ?? 0)])}
            />
          </>
        ) : <div className="empty">No usage recorded yet.</div>}
      </Card>

      <div className="u-mins">
        <Card>
          <div className="u-cardhead"><div><h2 className="u-ttl">Requests / min</h2><div className="u-hint">live · per model against its cap</div></div>
            <div className="u-livehead" style={{ marginLeft: 'auto' }}>
              <span className={'u-livedot' + (livePoll.stale ? ' stale' : '')} />
              <span className="u-hint">{livePoll.stale ? 'not responding' : 'live'}</span>
            </div></div>
          <div style={{ marginTop: 14 }}>
            {livePoll.stale && (
              <div className="callout warning" style={{ marginBottom: 12 }}>
                <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                <div className="callout-body">
                  These numbers stopped updating — the console can't reach the backend.
                  What's shown is the last reading, not the current one.
                </div>
              </div>
            )}
            {!livePoll.stale && liveModels.length === 0 && <div className="u-hint">No calls in the last minute.</div>}
            {liveModels.map((m) => (
              <div className={'u-meter ' + (clsFor[m.model] || 'us0')} key={m.model}><b>{uShort(m.model)}</b>
                <div className="bar"><i className={m.rpm / Math.max(m.cap, 1) > 0.75 ? 'warn' : ''} style={{ width: `${Math.min(100, (m.rpm / Math.max(m.cap, 1)) * 100)}%` }} /></div>
                <span className="v">{m.rpm}/{m.cap}{m.cooldown ? ' · cd' : ''}</span></div>
            ))}
          </div>
        </Card>
        <Card>
          <div className="u-cardhead"><div><h2 className="u-ttl">Tokens / min</h2><div className="u-hint">daily peak · {windowLabel}</div></div></div>
          {daily.length ? (
            <>
              <MiniArea values={tpmSeries} limit={tpmLimit} limitLabel={`cap ${uTok(tpmLimit)}/min`} cls="us1" />
              <ChartTable
                caption={`Peak tokens per minute, ${bucketLabel}, ${windowLabel}. Cap ${uTok(tpmLimit)} per minute.`}
                columns={['Period', 'Peak tokens / min']}
                rows={labels.map((l, i) => [l, uTok(tpmSeries[i] ?? 0)])}
              />
            </>
          ) : <div className="empty">No usage yet.</div>}
        </Card>
      </div>

      <div className="u-cols">
        <Card>
          <div className="u-cardhead"><div><h2 className="u-ttl">By model</h2><div className="u-hint">today · each against its own free-tier caps</div></div></div>
          {/* A real table, not a div grid: the four column labels used to read once and then
              ~40 loose values streamed past with no column association. */}
          {models.length === 0 ? <div className="empty">No usage recorded yet.</div> : (
            <table className="u-mtable">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Peak rpm vs cap</th>
                  <th scope="col" className="num">Requests</th>
                  <th scope="col" className="num">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {shownModels.map((m) => (
                  <tr key={m.model}>
                    <th scope="row" className={'u-mname ' + clsFor[m.model]}>
                      <span className="d" /><b>{uShort(m.model)}</b><span>{m.role}</span>
                    </th>
                    <td className={'u-rpm ' + clsFor[m.model]}>
                      {m.peak_rpm_today}
                      <span className="bar"><i className={m.peak_rpm_today / Math.max(m.cap, 1) > 0.75 ? 'warn' : ''} style={{ width: `${Math.min(100, (m.peak_rpm_today / Math.max(m.cap, 1)) * 100)}%` }} /></span>
                      <span>{m.cap}</span>
                    </td>
                    <td className="u-num">{uReq(m.requests_today)}</td>
                    <td className="u-num">{uTok(m.tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {idleModels.length > 0 && (
            <button className="ghost u-idle-toggle" onClick={() => setShowIdle((v) => !v)} aria-expanded={showIdle}>
              {showIdle
                ? `Hide ${idleModels.length} idle models`
                : `Show ${idleModels.length} idle models in the fallback chain`}
            </button>
          )}
        </Card>
        <Card>
          <div className="u-cardhead"><div><h2 className="u-ttl">By process</h2><div className="u-hint">requests · {windowLabel}</div></div></div>
          {bySource.length === 0 ? <div className="empty">Nothing recorded yet.</div>
            : (
              <>
                <DonutChart total={srcTotal} unit="requests" items={bySource.map((s) => ({ label: U_SOURCE_LABEL[s.source] || s.source, value: s.requests, tip: U_SOURCE_TIP[s.source] }))} />
                <ChartTable
                  caption={`Requests by process, ${windowLabel}`}
                  columns={['Process', 'Requests', 'Share']}
                  rows={bySource.map((x) => [
                    U_SOURCE_LABEL[x.source] || x.source, uReq(x.requests),
                    Math.round((x.requests / srcTotal) * 100) + '%',
                  ])}
                />
              </>
            )}
        </Card>
      </div>

      <div className="callout note"><span className="ic"><Icon.info size={17} /></span><div className="callout-body">Free-tier limits reset daily at 00:00 UTC. When a model hits its limit, Olisar rests it for two minutes and falls back to the next one in its chain.</div></div>
    </>
  )
}

// The component called Spinner had no spinner in it — every tab switch blanked the page to
// bare left-aligned grey text, which reads as "empty" rather than "working". A moving
// indicator is the difference, and `role="status"` means the state reaches a screen reader
// too, since the visual cue can't.
function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="page-loading" role="status">
      <span className="spinner" />
      <span>{label}</span>
    </div>
  )
}
