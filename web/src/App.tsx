import { useEffect, useState } from 'react'
import { api, setGuild as apiSetGuild, setOnUnauthorized, Unauthorized } from './api'
import { Icon, type IconName } from './icons'
import {
  Persona, Behavior, Messages, Channels, Access, Knowledge, Members, Extensions, Usage, ApiKeys, Docs,
} from './pages'
import { SetupWizard, type SetupStatus } from './setup'

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
  { id: 'docs', label: 'Docs', ic: 'docs' },
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

  // Any 401 (e.g. the session was revoked because the account lost Manage Server)
  // drops straight back to the login screen, so a now-powerless page can't linger.
  useEffect(() => { setOnUnauthorized(() => setAuth('out')) }, [])

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

  const pages: Record<string, JSX.Element> = {
    persona: <Persona />,
    behavior: <Behavior />,
    messages: <Messages />,
    channels: <Channels />,
    access: <Access />,
    knowledge: <Knowledge />,
    members: <Members />,
    extensions: <Extensions />,
    keys: <ApiKeys />,
    usage: <Usage />,
    docs: <Docs onNavigate={setTab} />,
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

        <div className="server-switch" title="Switch server">
          {current.icon
            ? <img className="server-icon" src={current.icon} alt="" />
            : <div className="server-icon ph">{(current.name || '?').slice(0, 1).toUpperCase()}</div>}
          <select
            className="server-select"
            value={guild ?? ''}
            onChange={(e) => changeGuild(e.target.value)}
            disabled={guilds.length < 2}
          >
            {guilds.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </div>

        <div className="nav-label">Configure</div>
        {NAV.map((n) => {
          const Glyph = Icon[n.ic]
          const active = tab === n.id
          return (
            <div
              key={n.id}
              className={'nav-item' + (active ? ' active' : '')}
              onClick={() => setTab(n.id)}
            >
              <span className="ic"><Glyph size={18} weight={active ? 'Bold' : 'Linear'} /></span>
              {n.label}
            </div>
          )
        })}
        <div className="spacer" />
        <div className="sidebar-foot">
          <WebLink tunnel={tunnel} />
          <div className="who">
            Signed in as <b>{me?.username}</b>
            <br />
            <span className="muted">
              {me?.granted_via === 'allowlist' ? 'Allowlisted admin' : 'Manage-server admin'}
            </span>
          </div>
          <div className="foot-row">
            <button className="ghost sm" onClick={async () => { await api.logout(); setAuth('out') }}>
              <Icon.logout size={16} /> Log out
            </button>
          </div>
        </div>
      </aside>
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
        <span className="weblink-hint">Turn on remote access from the Olisar menu-bar icon to get a shareable link.</span>
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
        <span className={'dot' + (tunnel.running ? ' on' : ' warn')} />
        <span className="weblink-label">{tunnel.running ? 'Open from the web' : 'Reconnecting…'}</span>
      </div>
      <div className="weblink-row">
        <a href={url} target="_blank" rel="noreferrer" title={url}>{host}</a>
        <button className="ghost sm" onClick={copy}>
          {copied ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
        </button>
      </div>
    </div>
  )
}
