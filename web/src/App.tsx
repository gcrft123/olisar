import React, { useEffect, useId, useRef, useState } from 'react'
import { api, setGuild as apiSetGuild, setOnUnauthorized, Unauthorized } from './api'
import { Modal, confirmDialog, toast } from './overlays'
import { Icon, type IconName } from './icons'
import {
  Persona, Behavior, Messages, Channels, Access, Knowledge, Members, Extensions, Usage, ApiKeys, Docs,
} from './pages'
import { Developer } from './developer'
import { SetupWizard, type SetupStatus } from './setup'
import { ServerControlPanel } from './server'
import { SECTIONS as SETTINGS_SECTIONS, SettingsModal, type SectionId } from './settings'
import { PageBoundary, currentPageActions, hasUnsavedChanges, usePoll } from './ui'
import { DOCS } from './docs'
import { CommandPalette, usePaletteHotkey, type Command } from './palette'

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

// Every id the router may resolve. Developer is included even when the tab is hidden:
// a non-developer landing on #/developer should be redirected, not shown a blank page.
// Ids the router will resolve. `developer` is deliberately absent: it isn't a tab for most
// operators, so a typed `#/developer` falls back to the current page and rewrites the URL
// rather than rendering operator tooling to someone the rail hides it from.
const TAB_IDS = new Set([...NAV.map((n) => n.id), 'docs'])

// The settings each page owns, so "quiet hours" or "context window" reaches Behavior
// rather than whichever documentation paragraph happens to mention it.
const PAGE_KEYWORDS: Record<string, string> = {
  persona: 'name system prompt style notes about me bio character tone test chat',
  behavior: 'triggers dms mentions ping everyone here model web search context window summary threshold glossary mine persona rebuild proactivity eagerness confidence cooldown quiet hours reactions presence voice',
  messages: 'command replies ping watch unwatch status learn url site doc forget me dm indexing proactive privacy rate limited blank access denied placeholders',
  channels: 'mode memory respond both resource feed off indexing search index category forum',
  access: 'roles allowed blocked open restrict lock out permissions',
  knowledge: 'knowledge base sources crawl glossary facts mine search index reindex clear memory danger zone activity',
  members: 'profiles impressions remembered facts roles avatars',
  extensions: 'marketplace import olx publish permissions welcome star citizen dice calculator',
  keys: 'gemini cloudflare uex api key token secret credentials',
  usage: 'quota rate limits requests tokens rpm tpm by model by process free tier',
  docs: 'documentation help guide reference',
}

type Guild = { id: string; name: string; icon: string }
type TunnelInfo = { available: boolean; running: boolean; helper: boolean; hostname: string; public_url: string }
const GUILD_KEY = 'olisar_guild'

// The console lives at one URL, so Back left the app entirely, a refresh always landed on
// Persona, and no view could be linked to or bookmarked. The tab is in the hash: cheap
// (no server routing needed for a file-served SPA), survives a reload, and gives the
// browser's own Back/Forward something real to move through.
function useTabRouting(
  tab: string,
  setTab: (id: string) => void,
  guard: (what: string) => Promise<boolean>,
  isTab: (id: string) => boolean,
) {
  // An unknown id renders nothing — `pages[id]` is simply undefined, so no boundary catches
  // it and the operator gets a blank console with no active nav item. Treat anything that
  // isn't a real tab as "no route" and rewrite the URL to match what's on screen.
  const hashTab = () => {
    const raw = decodeURIComponent(location.hash.replace(/^#\/?/, '')).split('?')[0]
    return isTab(raw) ? raw : ''
  }
  // Adopt the hash on first paint, so a bookmarked or shared link opens its page.
  useEffect(() => {
    const initial = hashTab()
    if (initial && initial !== tab) setTab(initial)
    else if (!initial) history.replaceState(null, '', '#/' + tab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Publish tab changes without adding history noise for the initial adopt.
  useEffect(() => {
    if (hashTab() !== tab) history.pushState(null, '', '#/' + tab)
  }, [tab])
  // Back/Forward runs through the same unsaved-work guard as a nav click; if the operator
  // keeps editing, put the hash back so the URL never disagrees with the screen.
  useEffect(() => {
    const onPop = async () => {
      const next = hashTab()
      // An unrecognised hash resolves to '' — keep the screen where it is and rewrite the
      // URL, so a stale bookmark or a stray anchor never leaves the address bar describing
      // a page that isn't showing.
      if (!next) { history.replaceState(null, '', '#/' + tab); return }
      if (next === tab) return
      if (await guard('this page')) setTab(next)
      else history.pushState(null, '', '#/' + tab)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [tab, setTab, guard])
}

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
  const [settingsPane, setSettingsPane] = useState<SectionId | undefined>(undefined)
  const [isDev, setIsDev] = useState(false)
  const [standing, setStanding] = useState<{ status: string; message?: string; acknowledged?: boolean } | null>(null)
  const [warnDismissed, setWarnDismissed] = useState(false)
  // Below 860px the rail is a drawer rather than a column (see index.css).
  const [navOpen, setNavOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Any 401 (e.g. the session was revoked because the account lost Manage Server)
  // drops straight back to the login screen, so a now-powerless page can't linger.
  useEffect(() => { setOnUnauthorized(() => setAuth('out')) }, [])

  // Closing the window is the third way to lose a draft, and the only one the browser
  // owns. The prompt text is the browser's, not ours — returnValue just has to be set.
  useEffect(() => {
    const onLeave = (e: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges()) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onLeave)
    return () => window.removeEventListener('beforeunload', onLeave)
  }, [])

  // The drawer covers the page but wasn't modal: the content behind stayed focusable and
  // the body still scrolled, so tabbing out of the drawer landed on controls the operator
  // couldn't see. The Settings modal gets this right through the Modal shell; the drawer
  // is hand-rolled, so it has to do the same work itself.
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setNavOpen(false) }
    const wide = window.matchMedia('(min-width: 861px)')
    const onWide = () => { if (wide.matches) setNavOpen(false) }
    const main = document.getElementById('console-main')
    // The topbar sits outside <main>, so inerting only the content left the hamburger
    // tabbable behind the open drawer — Tab walked out of the drawer onto a control it
    // covers. Everything that isn't the drawer goes inert.
    const topbar = document.querySelector<HTMLElement>('.topbar')
    // Where focus came from, so closing puts it back. Without this, Escape left focus on a
    // control inside a drawer that is now visibility:hidden and translated off-canvas, and
    // the next Tab resumed from an element nobody can see.
    const openedFrom = document.activeElement as HTMLElement | null
    if (main) main.inert = true
    if (topbar) topbar.inert = true
    const prevOverflow = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    document.getElementById('console-nav')?.querySelector<HTMLElement>('button, [role="button"]')?.focus()
    window.addEventListener('keydown', onKey)
    wide.addEventListener('change', onWide)
    return () => {
      if (main) main.inert = false
      if (topbar) topbar.inert = false
      document.documentElement.style.overflow = prevOverflow
      window.removeEventListener('keydown', onKey)
      wide.removeEventListener('change', onWide)
      const back = openedFrom?.isConnected
        ? openedFrom
        : document.querySelector<HTMLElement>('[aria-label="Open navigation"]')
      back?.focus()
    }
  }, [navOpen])

  // Landing back from the marketplace Discord-verification round-trip.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search)
    if (p.has('verified') || p.has('verify')) {
      const ok = p.get('verified') === '1'
      window.history.replaceState({}, '', window.location.pathname)
      toast(ok
        ? 'Verified with Discord. Your published extensions now show a verified badge.'
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
    // Refresh immediately when the funnel is toggled from Settings, so the sidebar
    // card flips on/off right away instead of waiting for the next poll.
    const pull = () => api.tunnelStatus().then(setTunnel).catch(() => {})
    window.addEventListener('olisar:tunnel-changed', pull)
    return () => window.removeEventListener('olisar:tunnel-changed', pull)
  }, [auth])
  usePoll(() => { api.tunnelStatus().then(setTunnel).catch(() => {}) }, 20000, auth === 'in')

  usePaletteHotkey(React.useCallback(() => setPaletteOpen(true), []))

  // ⌘S / Ctrl-S. Every page is built around Save and it was mouse-only; the browser's own
  // "save page" is meaningless inside an app whose whole contract is an explicit save.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 's' || !(e.metaKey || e.ctrlKey)) return
      // Always consume it. Settings advertises ⌘S as a console shortcut, and returning
      // early on a page with nothing to save handed the key to the browser's Save-Page-As
      // dialog — the one outcome an operator pressing it here never wants.
      e.preventDefault()
      const save = currentPageActions().find((a) => a.id === 'save')
      if (save) save.run()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const isTab = React.useCallback((id: string) => TAB_IDS.has(id) || (isDev && id === 'developer'), [isDev])

  // Declared here, above every early return: `useTabRouting` is a hook, and a hook called
  // only on the renders that get past the loading gates is a different hook count than the
  // render before it — which React treats as fatal rather than degraded.
  const leaveGuard = async (what: string) => {
    if (!hasUnsavedChanges()) return true
    // "Discard or stay" is a false choice: the operator's actual intent, almost always, is
    // to keep the work AND go. The page already publishes its save action, so offer it.
    const save = currentPageActions().find((a) => a.id === 'save')
    const r = await confirmDialog({
      title: 'You have unsaved changes',
      message: <>Leaving {what} discards them.</>,
      confirmLabel: 'Discard',
      cancelLabel: 'Keep editing',
      extraLabel: save ? 'Save and leave' : undefined,
      tone: 'warning',
    })
    if (r === 'extra' && save) {
      // Awaited: the operator picked the option that protects their work, and if the PUT
      // fails the dock that would show the error unmounts with the page. On failure, stay
      // put with the draft and the message intact.
      const ok = await save.run()
      if (ok === false) return false
      toast('Saved', 'success')
      return true
    }
    return r === true
  }
  useTabRouting(tab, setTab, leaveGuard, isTab)

  // Is this operator a whitelisted platform developer? Gates the Developer tab.
  useEffect(() => {
    if (auth !== 'in') { setIsDev(false); return }
    api.devStatus().then((d) => setIsDev(!!d?.is_developer)).catch(() => setIsDev(false))
  }, [auth])

  // Poll the operator's own moderation standing — a ban locks the console, a warning shows
  // once. Checked continuously (not just at login), so it takes effect within ~a poll.
  usePoll(() => { api.devStanding().then(setStanding).catch(() => {}) }, 20000, auth === 'in')

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
  if (setup === 'checking') return <div className="loading" role="status"><span className="spinner" /> Loading…</div>
  if (setup === 'needed' && setupInfo) return <SetupWizard status={setupInfo} initialConnectMode={setupInfo.hosting_mode === 'server'} onDone={async () => {
    // Re-read status so routing sees the just-saved config. A server-hosting setup (deploy /
    // reconnect) changes hosting_mode to 'server'; without this refresh, the stale mount-time
    // status still says local and we'd fall through to the local Discord login — which has no
    // client id in server mode and dead-ends at Discord's "Invalid form body".
    try { setSetupInfo(await api.setupStatus()) } catch { /* keep prior status */ }
    setSetup('done')
  }} />
  // Server hosting: the bot lives on the operator's VM. This local install is the loopback
  // control panel (start/stop over SSH) — no Discord login, no local console.
  if (setup === 'done' && setupInfo?.hosting_mode === 'server') return <ServerControlPanel />
  if (auth === 'loading') return <div className="loading" role="status"><span className="spinner" /> Loading…</div>
  if (auth === 'out') return <Login />
  if (guilds === null) return <div className="loading" role="status"><span className="spinner" /> Loading your servers…</div>
  if (guilds.length === 0) return <NoServers username={me?.username} onLogout={async () => { await api.logout(); setAuth('out') }} />

  // Both `tab` and `guild` key a remount below, which throws away whatever the page was
  // holding. Ask first. Gated here rather than on the nav item because Docs reaches
  // setTab directly through its `tab:` deep links and would otherwise slip past.
  const goTab = async (id: string) => { if (id !== tab && await leaveGuard('this page')) setTab(id) }
  const changeGuild = async (id: string) => {
    if (id === guild || !(await leaveGuard('this server'))) return
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
    knowledge: <Knowledge serverName={current.name} />,
    members: <Members />,
    extensions: <Extensions isOperator={isOperator} />,
    keys: <ApiKeys />,
    usage: <Usage />,
    docs: <Docs onNavigate={goTab} />,
    // Gated here, not only in the rail: the rail hides the item for non-developers, but the
    // route is reachable by typing the hash. The page is operator-tooling, so it renders the
    // ordinary not-found path instead.
    ...(isDev ? { developer: <Developer /> } : {}),
  }
  // The Developer tab only appears for whitelisted platform developers; Docs always sits
  // last in the rail, below Developer.
  // Everything above the rule configures this server; Developer and Docs don't. A hairline
  // rather than named groups — eleven items don't need taxonomy, but the two items that
  // aren't configuration shouldn't sit in the same run as the nine that are.
  const docsNav = { id: 'docs', label: 'Docs', ic: 'docs' as IconName, rule: true }
  const nav = isDev
    ? [...NAV, { id: 'developer', label: 'Developer', ic: 'developer' as IconName, rule: true }, docsNav]
    : [...NAV, docsNav]

  // Everything the rail can reach, plus the server switcher, plus whatever the page in
  // front of the operator is currently offering. A palette that can only do what the
  // always-visible rail does is a slower way to click something you can already see.
  const commands: Command[] = [
    ...currentPageActions().map((a) => ({
      id: 'action:' + a.id, label: a.label, group: 'This page', ic: 'bolt' as IconName,
      keywords: 'action save', run: a.run,
    })),
    ...nav.map((n) => ({
      id: 'tab:' + n.id, label: n.label, group: 'Page', ic: n.ic,
      // What the page actually contains, so a setting's own name finds its page.
      keywords: n.id + ' ' + (PAGE_KEYWORDS[n.id] ?? ''),
      run: () => { void goTab(n.id) },
    })),
    ...guilds.map((g) => ({
      id: 'guild:' + g.id, label: g.name, group: 'Server', ic: 'members' as IconName,
      keywords: 'switch server guild', run: () => { void changeGuild(g.id) },
    })),
    // Every settings pane and every documentation section, not just the eleven things the
    // rail already shows. An operator who types "quiet hours" should land somewhere, and
    // "Search" is a promise the palette has to keep.
    ...SETTINGS_SECTIONS.map((sec) => ({
      id: 'settings:' + sec.id, label: sec.label, group: 'Settings', ic: sec.ic,
      keywords: 'settings preferences ' + sec.id,
      run: () => { setSettingsPane(sec.id); setSettingsOpen(true) },
    })),
    ...DOCS.map((d) => ({
      id: 'doc:' + d.id, label: d.title, group: 'Docs', ic: 'docs' as IconName,
      // The body is searchable but never displayed, so typing a phrase from a page finds it.
      keywords: d.id,
      body: d.body,
      run: () => { void goTab('docs'); window.dispatchEvent(new CustomEvent('olisar:goto-doc', { detail: d.id })) },
    })),
  ]

  // A banned account is locked out of the console entirely (re-checked every poll).
  if (standing?.status === 'banned') {
    return <Banned message={standing.message} onLogout={async () => { await api.logout(); setAuth('out') }} />
  }

  return (
    <div className="shell">
      {/* ~16 controls sit between the top of the page and the content on every tab. */}
      {/* Moves focus rather than navigating: the router reads the hash as a tab id, so
          `href="#console-main"` left the URL at a tab that doesn't exist — a blank console
          with no active nav item and no route back. `<main>` takes tabindex="-1" below so
          the focus actually lands (Firefox and Safari won't focus a bare container). */}
      <a
        className="skip-link"
        href="#console-main"
        onClick={(e) => {
          e.preventDefault()
          const m = document.getElementById('console-main')
          m?.focus()
          m?.scrollIntoView({ block: 'start' })
        }}
      >
        Skip to content
      </a>
      {/* Narrow widths only — CSS hides it once the rail is back in the flow. */}
      <header className="topbar">
        <button className="ghost icon-btn sm" aria-label="Open navigation" aria-expanded={navOpen}
          aria-controls="console-nav" onClick={() => setNavOpen(true)}>
          <Icon.menu size={18} />
        </button>
        <img className="brand-logo" src="/logo.png" alt="" />
        <span className="name">Olisar</span>
      </header>
      <div className={'nav-backdrop' + (navOpen ? ' open' : '')} onClick={() => setNavOpen(false)} aria-hidden="true" />
      <aside id="console-nav" className={'sidebar' + (navOpen ? ' open' : '')}>
        <div className="brand">
          <img className="brand-logo" src="/logo.png" alt="Olisar" />
          <div>
            <div className="name">Olisar</div>
            <div className="sub">Secure Console</div>
          </div>
        </div>

        <ServerMenu guilds={guilds} current={current} onPick={changeGuild} />

        {/* An accelerator nobody can discover isn't one. This is the only thing in the
            console that advertises the palette; it's also a real button, so the feature is
            reachable without knowing the chord at all. */}
        <button className="cmdk-hint" onClick={() => setPaletteOpen(true)}>
          <Icon.search size={14} />
          <span>Search</span>
          <kbd>⌘K</kbd>
        </button>

        <nav aria-label="Console sections">
        {nav.map((n, i) => {
          const Glyph = Icon[n.ic]
          const active = tab === n.id
          const firstAfterRule = (n as any).rule && !(nav[i - 1] as any)?.rule
          return (
            <React.Fragment key={n.id}>
            {firstAfterRule && <div className="nav-rule" role="separator" />}
            {/* A real anchor now that the tab lives in the hash: middle-click and
                open-in-new-tab work, the status bar shows where the row goes, and a screen
                reader hears a link in a nav rather than a button. The click is still
                intercepted so the unsaved-work guard runs — but a modified click (new tab,
                new window) is left to the browser, because it isn't leaving this page. */}
            <a
              className={'nav-item' + (active ? ' active' : '')}
              href={'#/' + n.id}
              data-tab={n.id}
              aria-current={active ? 'page' : undefined}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
                e.preventDefault()
                void goTab(n.id)
                setNavOpen(false)
              }}
            >
              <span className="ic"><Glyph size={18} weight={active ? 'Bold' : 'Linear'} /></span>
              {n.label}
            </a>
            </React.Fragment>
          )
        })}
        </nav>
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
      {settingsOpen && <SettingsModal initialSection={settingsPane} onClose={() => { setSettingsOpen(false); setSettingsPane(undefined) }} />}
      <CommandPalette commands={commands} open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      {standing?.status === 'warned' && !standing.acknowledged && !warnDismissed && (
        <WarnModal
          message={standing.message}
          onClose={async () => { setWarnDismissed(true); try { await api.devStandingAck() } catch { /* will reshow next poll */ } }}
        />
      )}
      {/* Keyed by guild so switching servers remounts the page and refetches its settings. */}
      <main key={guild ?? ''} id="console-main" tabIndex={-1} className={'main' + (tab === 'docs' ? ' docs-mode' : '')}>
        {/* Keyed by tab too, so moving to another page clears a failed one. */}
        <PageBoundary key={tab}>{pages[tab]}</PageBoundary>
      </main>
    </div>
  )
}

function Login() {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const pollRef = useRef<number | null>(null)
  // In the desktop app, OAuth must run in the system browser — a chromeless app window can
  // strand you on Discord's page with no way back, and embedded-webview OAuth is disallowed.
  const isDesktop = !!(window as any).olisar?.desktop

  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  const startDesktopSignIn = () => {
    if (pollRef.current) clearTimeout(pollRef.current)
    const nonce = (crypto as any)?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
    // Opens in the system browser (main.js routes window.open(http…) to shell.openExternal).
    window.open(`${window.location.origin}/auth/login?desktop=${encodeURIComponent(nonce)}`, '_blank', 'noopener')
    setWaiting(true)
    const startedAt = Date.now()
    const poll = async () => {
      if (Date.now() - startedAt > 300_000) { setWaiting(false); return }  // give up after 5 min
      try {
        const r = await api.desktopClaim(nonce)
        if (r?.ok) { window.location.reload(); return }
        if (r?.denied) { window.location.href = `${window.location.origin}/?denied=role`; return }
      } catch { /* backend blip — keep polling */ }
      pollRef.current = window.setTimeout(poll, 1500)
    }
    pollRef.current = window.setTimeout(poll, 1500)
  }

  const cancel = () => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null }
    setWaiting(false)
  }

  return (
    <div className="login">
      <div className="box">
        <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
          <Icon.settings size={16} />
        </button>
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        <h1>Olisar Secure Console</h1>
        {waiting ? (
          <>
            <p>Continue signing in with Discord in your browser, then come back here.</p>
            <div className="login-actions">
              <button className="primary" onClick={startDesktopSignIn}>Reopen browser</button>
              <button className="ghost" onClick={cancel}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <p>Sign in with Discord. Only server admins can reach this console.</p>
            {isDesktop ? (
              <button className="btn-discord" onClick={startDesktopSignIn}>
                <Icon.login size={18} weight="Bold" /> Continue with Discord
              </button>
            ) : (
              <a className="btn-discord" href={api.loginUrl()}>
                <Icon.login size={18} weight="Bold" /> Continue with Discord
              </a>
            )}
          </>
        )}
      </div>
      {settingsOpen && (
        <SettingsModal
          sections={['general', 'bot', 'updates', 'desktop', 'feedback']}
          onClose={() => setSettingsOpen(false)}
        />
      )}
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
          Manage Server. Add the bot to one, then reload.
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
          The console is only for members with <b>Manage Server</b> on a server Olisar is in.
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
        <p>{props.message || 'This account has been banned from Olisar. If you think that’s a mistake, contact the Olisar team.'}</p>
        <div className="login-actions">
          <button className="ghost" onClick={props.onLogout}><Icon.logout size={16} /> Log out</button>
        </div>
      </div>
    </div>
  )
}

// A one-time warning notice (acknowledged on close, so it doesn't reappear).
function WarnModal(props: { message?: string; onClose: () => void }) {
  const titleId = useId()
  return (
    <Modal className="import-modal" labelledBy={titleId} onClose={props.onClose}>
        <div className="settings-head"><h2 id={titleId}>A note from the Olisar team</h2></div>
        <div className="callout warning" style={{ marginTop: 4 }}>
          <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
          <div className="callout-body">
            {props.message || 'Your account has received a warning. Please review the marketplace guidelines.'}
          </div>
        </div>
        <div className="import-foot"><button className="primary" onClick={props.onClose}>I understand</button></div>
    </Modal>
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

  const [cooling, setCooling] = useState(false)
  // No `.catch` here: usePoll counts consecutive rejections, and swallowing them was why
  // a dead backend could never be distinguished from a quiet one.
  const pull = () => api.botStatus().then((s: BotState) => setSt(s))
  // 5s is the right cadence while the operator is watching a power cycle land, but this ran
  // forever, on every tab, backgrounded or not — 12 requests a minute for a status dot.
  const poll = usePoll(pull, 5000)
  // "Online" isn't the whole truth: a bot that has exhausted a model's free-tier quota is
  // connected and silent. The rate limiter already reports that per model, so surface it
  // here rather than only on the Usage page, which is tab ten.
  usePoll(() => {
    api.getUsageLive()
      .then((d: any) => setCooling(((d?.models) || []).some((m: any) => m.cooldown)))
      .catch(() => {})
  }, 15000)

  if (st && st.available && st.can_power) seen.current = true
  // Returning null deleted the bot-status control from the sidebar whenever the backend was
  // unreachable — the one moment an operator most wants to know the bot's state, answered by
  // an absence. Hold the row and say what is actually known.
  if (!st || !seen.current) {
    return (
      <div className="botpower unknown" role="status">
        <span className="power-btn" aria-hidden="true"><Icon.bolt size={15} /></span>
        <div className="botpower-text">
          <div className="bp-status">Bot status unknown</div>
          {/* `stale` (two consecutive failures), not `seen` — gating the honest message on
              "we once had a good reading" meant a backend that was down at page load said
              "checking…" forever, which is exactly the dead-poll-as-idle-poll failure the
              design guide warns about. */}
          <div className="bp-hint">{poll.stale ? "can't reach the backend" : 'checking…'}</div>
        </div>
      </div>
    )
  }

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

  // Up but resting a rate-limited model is its own state - neither healthy nor broken.
  const limited = online && cooling
  const cls = phase === 'holding' ? 'holding' : phase === 'stopping' ? 'stopping'
    : starting ? 'starting' : limited ? 'limited' : online ? 'online' : 'offline'
  const label = phase === 'holding' ? 'Keep holding…'
    : phase === 'stopping' ? 'Powering down…'
    : starting ? 'Starting up…'
    : limited ? 'Rate-limited'
    : online ? 'Bot online' : 'Bot offline'
  const hint = phase === 'holding' ? 'release to cancel'
    : limited ? 'resting a model — see Usage'
    : online ? 'hold to power down'
    : offline ? 'tap to power on' : ' '

  return (
    <div className={'botpower ' + cls}>
      <button
        className="power-btn"
        aria-label={online ? 'Power the bot down (press and hold)' : offline ? 'Power the bot on' : label}
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
        <span className="weblink-hint">Turn it on under Settings → Remote access to get a shareable link.</span>
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
        <a href={url} target="_blank" rel="noreferrer" data-tip={url}>{host}</a>
        <button className="ghost icon-btn sm" onClick={copy} data-tip="Copy link" aria-label="Copy link">
          {copied ? <Icon.check size={14} weight="Bold" style={{ color: 'var(--ok)' }} /> : <Icon.copy size={14} />}
        </button>
      </div>
    </div>
  )
}
