import { useEffect, useId, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './icons'
import { Modal, confirmDialog, promptDialog, toast } from './overlays'
import { PubkeyBox, usePubkey } from './setup'
import { Field, Select, Text } from './ui'

// Every bot on a desktop install runs at once, each in its own process behind the gateway
// (olisar/runtime/gateway.py); the console shows one of them. This module is everything that
// looks across bots: the switcher, Settings ▸ Bots, moving a bot, and lending one bot's server
// to another. A console served by a bot directly (a VM, a source run) has no gateway, so
// `/api/bots` isn't there and none of this renders.

export type Bot = {
  id: string
  name: string
  created: boolean
  created_at?: string
  /** The bot's process: starting | ready | restarting | failed | stopped. */
  state: string
  /** Everything below comes from the bot itself, so it's absent until the process is up. */
  configured?: boolean
  hosting_mode?: 'local' | 'server'
  server_host?: string
  bot?: { running: boolean; ready: boolean; id: string; name: string; avatar: string }
  remote?: boolean
  /** The last lines a bot that couldn't start printed. */
  log?: string
}

type BotList = { profiles: Bot[]; active_id: string; default_id: string }

// One fetch shared by every consumer on screen (the rail, a pre-auth chip, Settings), so they
// can't disagree about which bot is showing. `null` = not loaded yet; `false` = no gateway.
let store: BotList | null | false = null
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

export function loadBots(): Promise<void> {
  if (inflight) return inflight
  inflight = api.botList()
    .then((d: BotList) => { store = d })
    .catch(() => { if (store === null) store = false })
    .finally(() => { inflight = null; listeners.forEach((l) => l()) })
  return inflight
}

export function useBots(pollMs = 0) {
  const [, bump] = useState(0)
  useEffect(() => {
    const l = () => bump((n) => n + 1)
    listeners.add(l)
    if (store === null) void loadBots()
    const t = pollMs ? setInterval(() => { void loadBots() }, pollMs) : undefined
    return () => { listeners.delete(l); if (t) clearInterval(t) }
  }, [pollMs])
  const list = store || null
  const current = list?.profiles.find((b) => b.id === list.active_id) ?? null
  return {
    /** False on a console with no gateway (a VM, a source run) — hide everything bot-level. */
    available: store !== false && store !== null,
    loading: store === null,
    bots: list?.profiles ?? [],
    current,
    activeId: list?.active_id ?? '',
    defaultId: list?.default_id ?? '',
    reload: loadBots,
  }
}

/** A short, honest line about where a bot stands, for the switcher and the list. */
export function botStatus(b: Bot): { label: string; tone: '' | 'success' | 'warning' | 'error' | 'info' } {
  if (b.state === 'failed') return { label: 'Couldn’t start', tone: 'error' }
  if (b.state !== 'ready') return { label: 'Starting…', tone: 'info' }
  if (!b.configured) return { label: 'Not set up', tone: '' }
  if (b.hosting_mode === 'server') return { label: 'On a server', tone: 'info' }
  if (b.bot?.ready) return { label: 'Online', tone: 'success' }
  if (b.bot?.running) return { label: 'Connecting…', tone: 'info' }
  return { label: 'Offline', tone: 'warning' }
}

export function BotAvatar({ bot, size = 'sm' }: { bot: Bot; size?: 'sm' | 'md' }) {
  const cls = 'server-icon' + (size === 'sm' ? ' sm' : ' bot-av-md')
  return bot.bot?.avatar
    ? <img className={cls} src={bot.bot.avatar} alt="" />
    : <div className={cls + ' ph'} aria-hidden="true">{(bot.name || '?').slice(0, 1).toUpperCase()}</div>
}

/** Point the console at another bot and reload onto it. Nothing stops — every bot keeps
 *  running; auth, servers and settings on screen all belong to the one being shown. */
export async function openBot(id: string): Promise<void> {
  try {
    await api.switchBot(id)
    window.location.reload()
  } catch (e: any) {
    toast(e?.message || 'Couldn’t switch bots', 'danger')
  }
}

export async function addBot(): Promise<void> {
  const name = await promptDialog({
    title: 'Add a bot',
    message: 'You’ll connect its Discord bot next.',
    confirmLabel: 'Add bot',
    prompt: { placeholder: 'e.g. Support bot' },
  })
  if (name === null) return
  try {
    const p = await api.createBot(name.trim() || 'New bot')
    await openBot(p.id)
  } catch (e: any) {
    toast(e?.message || 'Couldn’t add the bot', 'danger')
  }
}

/** A slug for a bot's Tailscale device name, so two bots' addresses don't collide. */
export function deviceNameFor(name: string): string {
  const slug = (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
  return slug || 'olisar'
}

// ── The switcher ──────────────────────────────────────────────────────────────
// `rail` takes the brand's place at the top of the console's nav rail; `chip` sits in the
// corner of a pre-auth card (setup, sign-in, the server panel), where a bot you can't sign
// into yet still needs a way back to the others. Only rendered once there are two bots —
// with one, there's nothing to switch to and the rail keeps its brand.
export function BotMenu(
  { variant, guard, onManage }:
  { variant: 'rail' | 'chip'; guard?: () => Promise<boolean>; onManage?: () => void },
) {
  const { available, bots, current } = useBots(variant === 'rail' ? 30000 : 0)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [focusAt, setFocusAt] = useState(0)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    void loadBots()  // statuses move; opening the menu is when they're read
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])
  // Focus starts on the bot you're on, so the list opens where you are.
  useEffect(() => {
    if (!open) return
    const i = Math.max(0, bots.findIndex((b) => b.id === current?.id))
    setFocusAt(i)
    const t = setTimeout(() => itemRefs.current[i]?.focus(), 0)
    return () => clearTimeout(t)
  }, [open])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!available || !current || bots.length < 2) return null

  const pick = async (b: Bot) => {
    setOpen(false)
    if (b.id === current.id) return
    if (guard && !(await guard())) return
    await openBot(b.id)
  }
  const actions = [
    { key: 'add', label: 'Add a bot', ic: Icon.add, run: () => { setOpen(false); void addBot() } },
    ...(onManage ? [{ key: 'manage', label: 'Manage bots', ic: Icon.settings, run: () => { setOpen(false); onManage() } }] : []),
  ]
  const count = bots.length + actions.length
  const status = botStatus(current)

  return (
    <div className={(variant === 'rail' ? 'bot-switch' : 'bot-chip') + (open ? ' open' : '')} ref={ref}>
      <button
        className={variant === 'rail' ? 'bot-switch-btn' : 'bot-chip-btn'}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`${current.name}, ${status.label}. Switch bot`}
      >
        <BotAvatar bot={current} size={variant === 'rail' ? 'md' : 'sm'} />
        {variant === 'rail' ? (
          <span className="bot-switch-text">
            <span className="name">{current.name}</span>
            <span className="sub">{status.label}</span>
          </span>
        ) : <span className="bot-chip-name">{current.name}</span>}
        <Icon.chevron size={14} className="server-chev" />
      </button>
      {open && (
        <div
          id={menuId}
          className="server-menu bot-menu"
          role="menu"
          aria-label="Switch bot"
          onKeyDown={(e) => {
            const last = count - 1
            let next = focusAt
            if (e.key === 'ArrowDown') next = focusAt >= last ? 0 : focusAt + 1
            else if (e.key === 'ArrowUp') next = focusAt <= 0 ? last : focusAt - 1
            else if (e.key === 'Home') next = 0
            else if (e.key === 'End') next = last
            else return
            e.preventDefault()
            setFocusAt(next)
            itemRefs.current[next]?.focus()
          }}
        >
          {bots.map((b, i) => {
            const s = botStatus(b)
            const on = b.id === current.id
            return (
              <button
                key={b.id}
                ref={(el) => { itemRefs.current[i] = el }}
                role="menuitemradio"
                aria-checked={on}
                tabIndex={i === focusAt ? 0 : -1}
                className={'server-menu-item bot-menu-item' + (on ? ' on' : '')}
                onFocus={() => setFocusAt(i)}
                onClick={() => { void pick(b) }}
              >
                <BotAvatar bot={b} />
                <span className="bot-menu-text">
                  <span className="server-menu-name">{b.name}</span>
                  <span className={'bot-menu-sub' + (s.tone ? ' ' + s.tone : '')}>{s.label}</span>
                </span>
                {on && <Icon.check size={14} weight="Bold" className="server-menu-check" />}
              </button>
            )
          })}
          <div className="bot-menu-rule" role="separator" />
          {actions.map((a, j) => {
            const i = bots.length + j
            const Glyph = a.ic
            return (
              <button
                key={a.key}
                ref={(el) => { itemRefs.current[i] = el }}
                role="menuitem"
                tabIndex={i === focusAt ? 0 : -1}
                className="server-menu-item bot-menu-action"
                onFocus={() => setFocusAt(i)}
                onClick={a.run}
              >
                <span className="bot-menu-ic"><Glyph size={15} /></span>
                <span className="server-menu-name">{a.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── A bot whose process keeps failing ─────────────────────────────────────────
// Shown instead of the console when the bot on screen couldn't start. Without it the console
// would sit on "couldn't reach the backend" with no way to the bots that are fine.
export function BotFailed({ bot }: { bot: Bot }) {
  const [busy, setBusy] = useState(false)
  // The error is the last line a crash prints, so open the log at its end.
  const logRef = useRef<HTMLPreElement>(null)
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [bot.log])
  const retry = async () => {
    setBusy(true)
    try { await api.restartBot(bot.id) } catch { /* the reload shows where it stands */ }
    setTimeout(() => window.location.reload(), 4000)
  }
  return (
    <div className="login">
      <div className="box wide">
        <BotMenu variant="chip" />
        <div className="mark warn"><Icon.warn size={26} weight="Bold" /></div>
        <h1>{bot.name} couldn’t start</h1>
        <p>Your other bots aren’t affected. The end of its output:</p>
        <pre ref={logRef} className="srv-logs bot-failed-log">{(bot.log || '').trim() || '(no output)'}</pre>
        <div className="login-actions">
          <button className="primary" disabled={busy} onClick={retry}>{busy ? 'Restarting…' : 'Try again'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Settings ▸ Bots ───────────────────────────────────────────────────────────

export function BotsPane({ Head }: { Head: (p: { title: string; sub?: string }) => JSX.Element }) {
  const { bots, activeId, defaultId, loading, reload } = useBots(8000)
  const [busy, setBusy] = useState(false)
  const [moving, setMoving] = useState<Bot | null>(null)

  const rename = async (b: Bot) => {
    const name = await promptDialog({
      title: 'Rename bot',
      confirmLabel: 'Rename',
      prompt: { placeholder: 'Bot name', defaultValue: b.name },
    })
    if (name === null || !name.trim() || name.trim() === b.name) return
    try { await api.renameBot(b.id, name.trim()); void reload() }
    catch (e: any) { toast(e?.message || 'Couldn’t rename the bot', 'danger') }
  }

  const makeDefault = async (b: Bot) => {
    if (b.id === defaultId || busy) return
    try { await api.setDefaultBot(b.id); toast(`The console opens on ${b.name}`, 'success'); void reload() }
    catch (e: any) { toast(e?.message || 'Couldn’t set the default', 'danger') }
  }

  const restart = async (b: Bot) => {
    try { await api.restartBot(b.id); toast(`Restarting ${b.name}`, 'neutral'); void reload() }
    catch (e: any) { toast(e?.message || 'Couldn’t restart the bot', 'danger') }
  }

  const del = async (b: Bot) => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: `Delete ${b.name}?`,
      message: (
        <>
          This stops <b>{b.name}</b> and permanently deletes everything this app stores for it:
          its token, settings, and memory.
          {b.hosting_mode === 'server' && <> Its server keeps running until you stop it there.</>}{' '}
          <strong style={{ color: 'var(--danger)' }}>This can’t be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: b.name },
      confirmLabel: 'Delete bot',
    })
    if (!ok) return
    setBusy(true)
    try { await api.deleteBot(b.id); toast(`Deleted ${b.name}`, 'neutral') }
    catch (e: any) { toast(e?.message || 'Couldn’t delete the bot', 'danger') }
    finally { setBusy(false); void reload() }
  }

  const reset = async (b: Bot) => {
    const ok = await confirmDialog({
      tone: 'danger',
      title: `Reset ${b.name}'s configuration?`,
      message: (
        <>
          Clears <b>{b.name}</b>’s Discord credentials, API keys, and hosting setup, and takes it
          offline. It <b>keeps</b> its persona, memory, knowledge, and settings, and you’ll set it
          up again.{' '}
          <strong style={{ color: 'var(--danger)' }}>This can’t be undone.</strong>
        </>
      ),
      requirePhrase: { phrase: b.name },
      confirmLabel: 'Reset configuration',
    })
    if (!ok) return
    try {
      const r = await api.resetBot(b.id)
      if (r?.active) window.location.reload()  // App re-routes to reconnect / setup
      else { toast(`Reset ${b.name}`, 'neutral'); void reload() }
    } catch (e: any) { toast(e?.message || 'Couldn’t reset the bot', 'danger') }
  }

  return (
    <>
      <Head title="Bots" sub="They all run at once. Switching changes which one this console shows." />
      {loading ? null : (
        <div className="bot-list">
          {bots.map((b) => {
            const isCurrent = b.id === activeId
            const isDefault = b.id === defaultId
            const s = botStatus(b)
            const up = b.state === 'ready'
            // Healthy is the default and says nothing, so only a bot that isn't up gets a chip.
            // A server-hosted bot's line is where it runs, not whether it's down, so it stays text.
            const healthy = up && !!b.configured && (b.hosting_mode === 'server' || !!b.bot?.ready)
            const hostedAt = healthy && b.hosting_mode === 'server'
              ? 'On a server' + (b.server_host ? ` · ${b.server_host}` : '') : ''
            return (
              <div key={b.id} className={'bot-row' + (isCurrent ? ' on' : '')}>
                <span className="bot-ic"><BotAvatar bot={b} /></span>
                {/* Where a healthy server bot runs is a hover detail, not a second line of text
                    competing with its name for the row's ~120px. The visually hidden copy is
                    what a screen reader gets, since the tooltip is drawn for the pointer. */}
                <div className="bot-name">
                  <span className="bot-title" data-tip={hostedAt || undefined}>{b.name}</span>
                  {hostedAt && <span className="visually-hidden">, {hostedAt}</span>}
                </div>
                {/* No Default chip: the filled star beside Open already says this bot opens on
                    launch, and the chip was the same fact twice in one row. */}
                <div className="bot-badges">
                  {!healthy && <span className={'badge' + (s.tone ? ' ' + s.tone : '')}>{s.label}</span>}
                  {isCurrent && <span className="badge success">Current</span>}
                </div>
                <div className="bot-actions">
                  {/* Disabled, not hidden: every row shows the same controls in the same
                      order, so a position always means the same action and the row never
                      reflows as state changes. The Current chip and the filled star already
                      say why a control is off, on screen. */}
                  {b.state === 'failed'
                    ? <button disabled={busy} onClick={() => restart(b)} aria-label={`Try starting ${b.name} again`}>Retry</button>
                    : <button disabled={busy || isCurrent} onClick={() => openBot(b.id)} aria-label={`Open ${b.name}`}>Open</button>}
                  <button className="ghost icon-btn sm" data-tip="Open on launch"
                    aria-label={isDefault ? `${b.name} already opens on launch` : `Open ${b.name} when the app starts`}
                    disabled={busy || isDefault} onClick={() => makeDefault(b)}>
                    <Icon.star size={14} weight={isDefault ? 'Bold' : 'Linear'} />
                  </button>
                  <button className="ghost icon-btn sm" data-tip="Rename" aria-label={`Rename ${b.name}`} disabled={busy} onClick={() => rename(b)}>
                    <Icon.edit size={14} />
                  </button>
                  <button className="ghost icon-btn sm" data-tip="Move / change hosting"
                    aria-label={`Move ${b.name} or change its hosting`}
                    disabled={busy || !up || !b.configured} onClick={() => setMoving(b)}>
                    <Icon.remote size={14} />
                  </button>
                  <span className="row-divider" aria-hidden="true" />
                  <button className="danger icon-btn sm" data-tip="Reset configuration" aria-label={`Reset ${b.name}'s configuration`}
                    disabled={busy || !up} onClick={() => reset(b)}>
                    <Icon.eraser size={14} />
                  </button>
                  <button className="danger" aria-label={`Delete ${b.name}`}
                    disabled={busy || isCurrent || bots.length < 2} onClick={() => del(b)}>
                    <Icon.trash size={14} /> Delete
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
      <div className="settings-row">
        <button disabled={busy} onClick={addBot}><Icon.add size={14} /> Add a bot</button>
      </div>
      {moving && (
        <MoveBotModal
          bot={moving}
          others={bots.filter((o) => o.id !== moving.id)}
          onClose={() => setMoving(null)}
          onMoved={() => { if (moving.id === activeId) window.location.reload(); else { setMoving(null); void reload() } }}
        />
      )}
    </>
  )
}

/** Servers other bots already run on, one entry per host — what a bot can be put next to. */
export function sharedServers(bots: Bot[], exceptId: string): { from: Bot; host: string; names: string[] }[] {
  const byHost = new Map<string, { from: Bot; host: string; names: string[] }>()
  for (const b of bots) {
    if (b.id === exceptId || b.state !== 'ready' || b.hosting_mode !== 'server' || !b.server_host) continue
    const seen = byHost.get(b.server_host)
    if (seen) seen.names.push(b.name)
    else byHost.set(b.server_host, { from: b, host: b.server_host, names: [b.name] })
  }
  return [...byHost.values()]
}

export function serverLabel(s: { host: string; names: string[] }): string {
  const who = s.names.length === 1 ? `${s.names[0]}’s server` : `${s.names.slice(0, -1).join(', ')} and ${s.names[s.names.length - 1]}’s server`
  return `${who} (${s.host})`
}

// ── Moving a bot ──────────────────────────────────────────────────────────────
// Change where a bot runs (this computer ↔ a cloud VM), carrying its data across and keeping
// the old copy as a backup. Any bot can move — it happens inside that bot's own process — and
// "a server" can be one another bot already runs on, which needs nothing new from the operator.
function MoveBotModal(
  { bot, others, onClose, onMoved }:
  { bot: Bot; others: Bot[]; onClose: () => void; onMoved: () => void },
) {
  const curMode = bot.hosting_mode === 'server' ? 'server' : 'local'
  const curHost = bot.server_host || ''
  const shared = sharedServers(others, bot.id).filter((s) => s.host !== curHost)
  // 'local' | 'new' (a VM the operator names) | 'share:<bot id>'
  const [target, setTarget] = useState<string>(curMode === 'local' ? (shared.length ? 'share:' + shared[0].from.id : 'new') : 'local')
  const [host, setHost] = useState('')
  const [user, setUser] = useState('ubuntu')
  const [showKey, setShowKey] = useState(false)
  const [moving, setMoving] = useState(false)
  const [err, setErr] = useState('')
  const titleId = useId()
  const pk = usePubkey(target === 'new' && showKey, bot.id)

  const sameServer = target === 'new' && curMode === 'server' && host.trim() === curHost && !!curHost
  const canMove = !moving && (target === 'local' || target.startsWith('share:') || (host.trim().length > 0 && !sameServer))

  const options = [
    ...(curMode === 'server' ? [{ value: 'local', label: 'This computer (local)' }] : []),
    ...shared.map((s) => ({ value: 'share:' + s.from.id, label: serverLabel(s) })),
    { value: 'new', label: curMode === 'server' ? 'A different server' : 'A new server' },
  ]

  const doMove = async () => {
    setErr(''); setMoving(true)
    try {
      let dest = { target: target === 'local' ? 'local' as const : 'server' as const, host: host.trim(), user: user.trim() || 'ubuntu' }
      if (target.startsWith('share:')) {
        const s = await api.shareServer(target.slice(6), bot.id)
        if (!s?.ok) { setErr(s?.error || 'Couldn’t use that server.'); setMoving(false); return }
        dest = { target: 'server', host: s.host, user: s.user || 'ubuntu' }
      }
      const r = await api.moveBot(bot.id, dest)
      if (!r?.ok) { setErr(r?.error || 'Move failed.'); setMoving(false); return }
      toast(r.note || `Moved ${bot.name}`, 'success')
      onMoved()
    } catch (e: any) { setErr(e?.message || 'Move failed.'); setMoving(false) }
  }

  const curLabel = curMode === 'server' ? `a server${curHost ? ` (${curHost})` : ''}` : 'this computer'

  return (
    <Modal className="confirm-dialog" labelledBy={titleId} onClose={onClose} dismissable={!moving}>
        <div className="confirm-head">
          <div className="confirm-icon"><Icon.remote size={22} weight="Bold" aria-hidden /></div>
          <div className="confirm-text">
            <div className="confirm-title" id={titleId}>Move {bot.name}</div>
            <div className="confirm-msg">Currently runs on <b>{curLabel}</b>.</div>
          </div>
        </div>

        <div className="move-body">
          <div className="callout tip">
            <span className="ic"><Icon.info size={17} weight="Bold" /></span>
            <div className="callout-body">Its persona, memory, knowledge, and uploaded docs move with it. The old copy is kept as a backup.</div>
          </div>

          {options.length > 1 && (
            <Field label="Move to">
              <Select value={target} onChange={setTarget} options={options} />
            </Field>
          )}

          {target === 'new' && (
            <>
              <Field label="Destination VM public IP" desc="A cloud VM you created for this bot.">
                <Text value={host} onChange={setHost} placeholder="e.g. 203.0.113.9" mono />
              </Field>
              {sameServer && <div className="err">That’s the current server. Pick a different IP, or move to this computer.</div>}
              <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
                <summary>Can’t connect? Add this app’s SSH key to the VM</summary>
                <div className="desc" style={{ marginTop: 8 }}>
                  Paste this into the VM’s <code>~/.ssh/authorized_keys</code>, or the provider’s SSH-keys box, before moving. A VM this app already set up trusts it automatically.
                </div>
                <PubkeyBox state={pk} />
                <Field label="SSH user" desc="The VM's login user. Ubuntu images use ubuntu.">
                  <Text value={user} onChange={setUser} placeholder="ubuntu" mono />
                </Field>
              </details>
            </>
          )}

          {moving && (
            <div className="callout note">
              <span className="ic"><span className="spinner" /></span>
              <div className="callout-body">Moving {bot.name}. This can take a few minutes — keep this window open.</div>
            </div>
          )}
          {err && <div className="err">{err}</div>}
        </div>

        <div className="confirm-foot">
          <button className="ghost" disabled={moving} onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!canMove} onClick={doMove}>{moving ? 'Moving…' : 'Move bot'}</button>
        </div>
    </Modal>
  )
}
