import { useEffect, useRef, useState } from 'react'
import { api, setGuild as apiSetGuild, setOnUnauthorized, Unauthorized } from './api'
import { toast } from './overlays'
import { Icon, type IconName } from './icons'
import {
  Persona, Behavior, Messages, Channels, Access, Knowledge, Members, Extensions, Usage, ApiKeys, Docs,
} from './pages'
import { Developer } from './developer'
import { SetupWizard, type SetupStatus } from './setup'
import { SettingsModal } from './settings'

const NAV: { id: string; label: string; ic: IconName }[] = [
  { id: 'persona', label: 'Persona', ic: 'persona' },
  { id: 'behavior', label: 'Behavior', ic: 'behavior' },
  { id: 'messages', label: 'Command replies', ic: 'messages' },
  { id: 'channels', label: 'Channels', ic: 'channels' },
  { id: 'access', label: 'Access', ic: 'access' },
  { id: 'knowledge', label: 'Knowledge', ic: 'knowledge' },
  { id: 'members', label: 'Members', ic: 'members' },
  { id: 'extensions', label: 'Extensions', ic: 'extensions' },
  { id: 'keys', label: 'API keys', ic: 'keys' },
  { id: 'usage', label: 'Usage', ic: 'usage' },
]

type Guild = { id: string; name: string; icon: string }
type TunnelInfo = { available: boolean; running: boolean; helper: boolean; hostname: string; public_url: string }
const GUILD_KEY = 'olisar_guild'

export default function App() {
  const [setup, setSetup] = useState<'checking' | 'needed' | 'done'>('checking')
  const [setupInfo, setSetupInfo] = useState<SetupStatus | null>(null)
  const [auth, setAuth] = useState<'loading' | 'in' | 'out'>('loading')
  const [me, setMe] = useState<any>(null)
  const [tab, setTab] = useState('persona')
  const [guilds, setGuilds] = useState<Guild[] | null>(null)
  const [guild, setGuildState] = useState<string | null>(null)
  const [tunnel, setTunnel] = useState<TunnelInfo | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [isDev, setIsDev] = useState(false)
  const [standing, setStanding] = useState<{ status: string; message?: string; acknowledged?: boolean } | null>(null)
  const [warnDismissed, setWarnDismissed] = useState(false)

  // Any 401 (e.g. the session was revoked because the account lost Manage Server)
  // drops straight back to the login screen, so a now-powerless page can't linger.
  useEffect(() => { setOnUnauthorized(() => setAuth('out')) }, [])

  // Landing back from the marketplace Discord-verification round-trip.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search)
    if (p.has('verified') || p.has('verify')) {
      const ok = p.get('verified') === '1'
      window.history.replaceState({}, '', window.location.pathname)
      toast(ok
        ? 'Publisher verified with Discord — your published extensions now show a verified badge.'
        : 'Discord verification didn’t complete.', ok ? 'success' : 'warning')
    }
  }, [])

  // First-run gate: if the backend reports no config yet, show the setup wizard
  // before the normal Discord login. If the status call fails (e.g. an older
  // backend), assume configured and proceed.
  useEffect(() => {
    api.setupStatus()
      .then((s: SetupStatus) => { setSetupInfo(s); setSetup(s.configured ? 'done' : 'needed') })
      .catch(() => setSetup('done'))
  }, [])

  useEffect(() => {
    if (setup !== 'done') return
    api.me()
      .then((m) => { setMe(m); setAuth('in') })
      .catch((e) => setAuth(e instanceof Unauthorized ? 'out' : 'out'))
  }, [setup])

  // Remote-access (Tailscale Funnel) status, so the sidebar can surface the public
  // web link. Polled lightly since the operator can toggle it from the menu-bar tray.
  useEffect(() => {
    if (auth !== 'in') return
    let alive = true
    const pull = () => api.tunnelStatus().then((t: TunnelInfo) => { if (alive) setTunnel(t) }).catch(() => {})
    pull()
    const id = setInterval(pull, 20000)
    // Refresh immediately when the funnel is toggled from Settings, so the sidebar
    // card flips on/off right away instead of waiting for the next poll.
    window.addEventListener('olisar:tunnel-changed', pull)
    return () => { alive = false; clearInterval(id); window.removeEventListener('olisar:tunnel-changed', pull) }
  }, [auth])

  // Is this operator a whitelisted platform developer? Gates the Developer tab.
  useEffect(() => {
    if (auth !== 'in') { setIsDev(false); return }
    api.devStatus().then((d) => setIsDev(!!d?.is_developer)).catch(() => setIsDev(false))
  }, [auth])

  // Poll the operator's own moderation standing — a ban locks the console, a warning shows
  // once. Checked continuously (not just at login), so it takes effect within ~a poll.
  useEffect(() => {
    if (auth !== 'in') return
    let alive = true
    const pull = () => api.devStanding().then((s) => { if (alive) setStanding(s) }).catch(() => {})
    pull()
    const id = setInterval(pull, 20000)
    return () => { alive = false; clearInterval(id) }
  }, [auth])

  useEffect(() => {
    if (auth !== 'in') return
    api.guilds()
      .then((gs: Guild[]) => {
        setGuilds(gs)
        if (gs.length) {
          const saved = localStorage.getItem(GUILD_KEY)
          const sel = gs.find((g) => g.id === saved)?.id ?? gs[0].id
          apiSetGuild(sel)
          setGuildState(sel)
        }
      })
      .catch(() => setGuilds([]))
  }, [auth])

  // Bounced here by the OAuth callback because the Discord account isn't an admin of
  // any server Olisar is in. Takes precedence over the normal auth flow.
  if (new URLSearchParams(window.location.search).has('denied')) return <AccessDenied />
  if (setup === 'checking') return <div className="loading">Loading…</div>
  if (setup === 'needed' && setupInfo) return <SetupWizard status={setupInfo} onDone={() => setSetup('done')} />
  if (auth === 'loading') return <div className="loading">Loading…</div>
  if (auth === 'out') return <Login />
  if (guilds === null) return <div className="loading">Loading your servers…</div>
  if (guilds.length === 0) return <NoServers username={me?.username} onLogout={async () => { await api.logout(); setAuth('out') }} />

  const changeGuild = (id: string) => {
    apiSetGuild(id)
    localStorage.setItem(GUILD_KEY, id)
    setGuildState(id)
  }
  const current = guilds.find((g) => g.id === guild) ?? guilds[0]

  // Authoring extension code is operator-only; the merged Extensions tab shows the
  // editor drill-in only to operators (everyone else just sees the toggles).
  const isOperator = me?.granted_via === 'allowlist'

  const pages: Record<string, JSX.Element> = {
    persona: <Persona />,
    behavior: <Behavior />,
    messages: <Messages />,
    channels: <Channels />,
    access: <Access />,
    knowledge: <Knowledge />,
    members: <Members />,
    extensions: <Extensions isOperator={isOperator} />,
    keys: <ApiKeys />,
    usage: <Usage />,
    docs: <Docs onNavigate={setTab} />,
    developer: <Developer />,
  }
  // The Developer tab only appears for whitelisted platform developers; Docs always sits
  // last in the rail, below Developer.
  const docsNav = { id: 'docs', label: 'Docs', ic: 'docs' as IconName }
  const nav = isDev
    ? [...NAV, { id: 'developer', label: 'Developer', ic: 'developer' as IconName }, docsNav]
    : [...NAV, docsNav]

  // A banned account is locked out of the console entirely (re-checked every poll).
  if (standing?.status === 'banned') {
    return <Banned message={standing.message} onLogout={async () => { await api.logout(); setAuth('out') }} />
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src="/logo.png" alt="Olisar" />
          <div>
            <div className="name">Olisar</div>
            <div className="sub">Secure Console</div>
          </div>
        </div>

        <ServerMenu guilds={guilds} current={current} onPick={changeGuild} />

        {nav.map((n) => {
          const Glyph = Icon[n.ic]
          const active = tab === n.id
          return (
            <div
              key={n.id}
              className={'nav-item' + (active ? ' active' : '')}
              role="button"
              tabIndex={0}
              data-tab={n.id}
              aria-current={active ? 'page' : undefined}
              onClick={() => setTab(n.id)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab(n.id) } }}
            >
              <span className="ic"><Glyph size={18} weight={active ? 'Bold' : 'Linear'} /></span>
              {n.label}
            </div>
          )
        })}
        <div className="spacer" />
        <div className="sidebar-foot">
          <BotPower />
          <WebLink tunnel={tunnel} />
          <div className="who">
            Signed in as <b>{me?.username}</b>
            <br />
            <span className="muted">
              {me?.granted_via === 'allowlist' ? 'Allowlisted admin' : 'Manage-server admin'}
            </span>
          </div>
          <div className="foot-row">
            <button className="ghost" onClick={() => setSettingsOpen(true)}>
              <Icon.settings size={16} /> Settings
            </button>
            <button className="ghost" onClick={async () => { await api.logout(); setAuth('out') }}>
              <Icon.logout size={16} /> Log out
            </button>
          </div>
        </div>
      </aside>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
      {standing?.status === 'warned' && !standing.acknowledged && !warnDismissed && (
        <WarnModal
          message={standing.message}
          onClose={async () => { setWarnDismissed(true); try { await api.devStandingAck() } catch { /* will reshow next poll */ } }}
        />
      )}
      {/* Keyed by guild so switching servers remounts the page and refetches its settings. */}
      <main key={guild ?? ''} className={'main' + (tab === 'docs' ? ' docs-mode' : '')}>{pages[tab]}</main>
    </div>
  )
}

function Login() {
  return (
    <div className="login">
      <div className="box">
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        <h1>Olisar Secure Console</h1>
        <p>Sign in with Discord. Only server admins can reach this console.</p>
        <a className="btn-discord" href={api.loginUrl()}>
          <Icon.login size={18} weight="Bold" /> Continue with Discord
        </a>
      </div>
    </div>
  )
}

function NoServers(props: { username?: string; onLogout: () => void }) {
  return (
    <div className="login">
      <div className="box">
        <div className="mark info"><Icon.add size={26} weight="Bold" /></div>
        <h1>No servers yet</h1>
        <p>
          You're signed in as <b>{props.username}</b>, but Olisar isn't in any server where you have
          Manage Server. Add the bot to a server (and make sure you have Manage Server there), then reload.
        </p>
        <div className="login-actions">
          <button className="primary" onClick={() => window.location.reload()}>Reload</button>
          <button className="ghost" onClick={props.onLogout}>
            <Icon.logout size={16} /> Log out
          </button>
        </div>
      </div>
    </div>
  )
}

// Bounced here by the OAuth callback (`/?denied=…`) because the signed-in Discord
// account isn't an admin of any server Olisar is in. Shown instead of a raw 403.
function AccessDenied() {
  return (
    <div className="login">
      <div className="box wide">
        <div className="mark warn"><Icon.access size={26} weight="Bold" /></div>
        <h1>Access denied</h1>
        <p>
          You signed in with Discord, but this account can't manage Olisar. The console is only for
          members with <b>Manage Server</b> on a server Olisar is in.
        </p>
        <ul className="hint-list">
          <li>Ask a server admin to give you <b>Manage Server</b>, then sign in again.</li>
          <li>Have another account that's an admin? Sign in with that one.</li>
          <li>Just got the role, or just added the bot? Sign in again to refresh.</li>
        </ul>
        <a className="btn-discord" href={api.loginUrl()}>
          <Icon.login size={18} weight="Bold" /> Sign in again
        </a>
      </div>
    </div>
  )
}

// Shown in place of the whole console when this account is banned from Olisar (a global
// moderation ban set by a platform developer; re-checked on every standing poll).
function Banned(props: { message?: string; onLogout: () => void }) {
  return (
    <div className="login">
      <div className="box wide">
        <div className="mark warn"><Icon.ban size={26} weight="Bold" /></div>
        <h1>Account suspended</h1>
        <p>{props.message || 'This account has been banned from Olisar. If you believe this is a mistake, contact the Olisar team.'}</p>
        <div className="login-actions">
          <button className="ghost" onClick={props.onLogout}><Icon.logout size={16} /> Log out</button>
        </div>
      </div>
    </div>
  )
}

// A one-time warning notice (acknowledged on close, so it doesn't reappear).
function WarnModal(props: { message?: string; onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="import-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head"><h2>A note from the Olisar team</h2></div>
        <div className="callout warning" style={{ marginTop: 4 }}>
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">
            {props.message || 'Your account has received a warning. Please review the marketplace guidelines.'}
          </div>
        </div>
        <div className="import-foot"><button className="primary" onClick={props.onClose}>I understand</button></div>
      </div>
    </div>
  )
}

// Server picker: a popup menu that always opens (even with a single server), instead of
// a native select that disables itself when there's only one option.
function ServerMenu({ guilds, current, onPick }: { guilds: Guild[]; current: Guild; onPick: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])
  const icon = (g: Guild, cls = '') => (g.icon
    ? <img className={'server-icon ' + cls} src={g.icon} alt="" />
    : <div className={'server-icon ph ' + cls}>{(g.name || '?').slice(0, 1).toUpperCase()}</div>)
  return (
    <div className={'server-switch' + (open ? ' open' : '')} ref={ref}>
      {icon(current)}
      <button className="server-select-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}>
        <span className="server-name">{current.name}</span>
        <Icon.chevron size={14} className="server-chev" />
      </button>
      {open && (
        <div className="server-menu" role="listbox">
          {guilds.map((g) => (
            <button
              key={g.id}
              role="option"
              aria-selected={g.id === current.id}
              className={'server-menu-item' + (g.id === current.id ? ' on' : '')}
              onClick={() => { onPick(g.id); setOpen(false) }}
            >
              {icon(g, 'sm')}
              <span className="server-menu-name">{g.name}</span>
              {g.id === current.id && <Icon.check size={14} weight="Bold" className="server-menu-check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

type BotState = { available: boolean; running: boolean; ready: boolean; can_power: boolean }
const HOLD_MS = 1400  // press-and-hold duration to power the bot down (matches the CSS ring)

// Operator-only control to take the Discord bot offline (and back). Powering down is a
// deliberate press-and-hold — a ring fills around the button and only fires if you keep
// holding — so it can't be hit by accident. Powering on is a single tap.
function BotPower() {
  const [st, setSt] = useState<BotState | null>(null)
  const [phase, setPhase] = useState<'idle' | 'holding' | 'stopping' | 'starting'>('idle')
  const hold = useRef<number | null>(null)
  // Once we've seen a powerable bot, keep the card mounted — a transient poll during a
  // power cycle can momentarily report unavailable, which used to make the card vanish.
  const seen = useRef(false)
  // True while a hold-to-power-down is completing, so the release click that follows
  // doesn't immediately power the bot back on. (Declared up here — all hooks must run
  // before the early return below, or the hook order changes when the card appears.)
  const didPowerDown = useRef(false)

  const pull = () => api.botStatus().then((s: BotState) => setSt(s)).catch(() => {})
  useEffect(() => {
    let alive = true
    const tick = () => api.botStatus().then((s: BotState) => { if (alive) setSt(s) }).catch(() => {})
    tick()
    const id = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  if (st && st.available && st.can_power) seen.current = true
  if (!st || !seen.current) return null

  const busy = phase === 'stopping' || phase === 'starting'
  const online = st.running && st.ready && !busy
  const starting = phase === 'starting' || (st.running && !st.ready && phase !== 'stopping')
  const offline = !st.running && !busy

  const clearHold = () => { if (hold.current) { clearTimeout(hold.current); hold.current = null } }

  async function powerDown() {
    didPowerDown.current = true
    setPhase('stopping')
    try { setSt(await api.botPower(false)); toast('Bot powered down', 'neutral') }
    catch { toast('Couldn’t power down the bot', 'danger') }
    setPhase('idle'); pull()
  }
  async function powerUp() {
    setPhase('starting')
    try { setSt(await api.botPower(true)) } catch { toast('Couldn’t start the bot', 'danger') }
    // poll until the gateway connection is actually ready
    for (let i = 0; i < 25; i++) {
      await new Promise((r) => setTimeout(r, 700))
      const s = await api.botStatus().catch(() => null)
      if (s) { setSt(s); if (s.ready) { toast('Bot is online', 'success'); break } if (!s.running) break }
    }
    setPhase('idle')
  }

  const startHold = () => {
    setPhase('holding')
    hold.current = window.setTimeout(() => { hold.current = null; powerDown() }, HOLD_MS)
  }
  const endHold = () => { clearHold(); setPhase((p) => (p === 'holding' ? 'idle' : p)) }
  const onPointerDown = () => { didPowerDown.current = false; if (online) startHold() }
  const onClick = () => {
    if (didPowerDown.current) { didPowerDown.current = false; return }  // swallow the post-hold release
    if (offline) powerUp()
  }

  const cls = phase === 'holding' ? 'holding' : phase === 'stopping' ? 'stopping'
    : starting ? 'starting' : online ? 'online' : 'offline'
  const label = phase === 'holding' ? 'Keep holding…'
    : phase === 'stopping' ? 'Powering down…'
    : starting ? 'Starting up…'
    : online ? 'Bot online' : 'Bot offline'
  const hint = phase === 'holding' ? 'release to cancel'
    : online ? 'hold to power down'
    : offline ? 'tap to power on' : ' '

  return (
    <div className={'botpower ' + cls}>
      <button
        className="power-btn"
        disabled={busy}
        onPointerDown={onPointerDown}
        onPointerUp={endHold}
        onPointerLeave={endHold}
        onPointerCancel={endHold}
        onClick={onClick}
      >
        <svg className="power-ring" viewBox="0 0 44 44" aria-hidden="true">
          <circle cx="22" cy="22" r="19" />
        </svg>
        <Icon.bolt size={17} weight="Bold" />
      </button>
      <div className="botpower-text">
        <div className="bp-status">{label}</div>
        <div className="bp-hint">{hint}</div>
      </div>
    </div>
  )
}

// The public web address to reach this dashboard, surfaced in the sidebar. Only a
// real `https://…` (Tailscale Funnel) origin counts as a shareable web link; a plain
// loopback origin means remote access is off, so we show how to turn it on instead.
function WebLink({ tunnel }: { tunnel: TunnelInfo | null }) {
  const [copied, setCopied] = useState(false)
  if (!tunnel) return null

  const url = (tunnel.public_url || '').replace(/\/$/, '')
  const isWeb = /^https:\/\//.test(url)

  if (!isWeb) {
    // Local-only: no web link yet. Keep the hint tiny and only when remote access
    // is actually supported by this build (the Funnel helper is bundled).
    if (!tunnel.helper) return null
    return (
      <div className="weblink off">
        <span className="weblink-label">Web access off</span>
        <span className="weblink-hint">Turn it on under Settings → Remote access (or the menu-bar icon) to get a shareable link.</span>
      </div>
    )
  }

  const host = url.replace(/^https:\/\//, '')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch { /* clipboard blocked — the link is still selectable */ }
  }

  return (
    <div className="weblink">
      <div className="weblink-head">
        <span className="weblink-label">{tunnel.running ? 'Open from the web' : 'Reconnecting…'}</span>
      </div>
      <div className="weblink-row">
        <a href={url} target="_blank" rel="noreferrer" title={url}>{host}</a>
        <button className="ghost icon-btn sm" onClick={copy} title="Copy link" aria-label="Copy link">
          {copied ? <Icon.check size={14} weight="Bold" style={{ color: 'var(--ok)' }} /> : <Icon.copy size={14} />}
        </button>
      </div>
    </div>
  )
}
