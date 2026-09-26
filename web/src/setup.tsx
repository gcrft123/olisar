import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { api } from './api'
import { BotMenu, deviceNameFor, intentList, serverLabel, sharedServers, useBots } from './bots'
import { DiscordLogo, Icon } from './icons'
import { FeedbackButton, SettingsModal, useFeedbackHost, type SectionId } from './settings'
import { logTail, reportBody, type FeedbackPrefill } from './feedback'
import { Field, Segmented, Select, Text, usePoll } from './ui'

export type SetupPrefill = {
  discord_token?: string
  discord_client_id?: string
  discord_client_secret?: string
  target_guild_id?: string
  gemini_api_key?: string
  cloudflare_account_id?: string
  cloudflare_api_token?: string
  uex_api_key?: string
  tunnel_token?: string
}

export type SetupStatus = {
  configured: boolean
  local_url: string
  redirect_uri: string
  tunnel_enabled: boolean
  hosting_mode?: string
  prefill?: SetupPrefill
}

type Mode = 'local' | 'tunnel' | 'server'

// A code-preview box (DESIGN.md CodeBlock) with a copy button that flips to a check.
export function Cb({ file, code }: { file: string; code: string }) {
  const [done, setDone] = useState(false)
  return (
    <div className="codeblock" style={{ marginBottom: 12 }}>
      <div className="head">
        <span className="file">{file}</span>
        <button
          className="cb-copy"
          aria-label="Copy"
          data-tip={done ? 'Copied' : 'Copy'}
          onClick={() => { navigator.clipboard?.writeText(code); setDone(true); setTimeout(() => setDone(false), 1400) }}
        >
          {done ? <Icon.check size={15} weight="Bold" /> : <Icon.copy size={15} />}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  )
}

// The app's SSH public key, fetched lazily when `enabled` (generated on first backend call).
// Surfaces loading/error/retry so the key box never sticks on "generating…" if the fetch
// hangs or fails (the fetch itself carries a timeout via api.serverPubkey). Each bot has its
// own key: `botId` names one that isn't the bot on screen.
export function usePubkey(enabled: boolean, botId?: string) {
  const [pubkey, setPubkey] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const retry = useCallback(() => {
    setLoading(true); setErr('')
    ;(botId ? api.botPubkey(botId) : api.serverPubkey())
      .then((r: any) => setPubkey(r.public_key || ''))
      .catch((e: any) => setErr(e?.message || 'Couldn’t generate the SSH key.'))
      .finally(() => setLoading(false))
  }, [botId])
  useEffect(() => { if (enabled && !pubkey && !loading && !err) retry() }, [enabled, pubkey, loading, err, retry])
  return { pubkey, loading, err, retry }
}

// Renders the SSH public key with the three states: loading → "generating…", error → message +
// Retry, ready → a copyable code box. Shared by the setup deploy step and the reconnect screens.
export function PubkeyBox({ state }: { state: ReturnType<typeof usePubkey> }) {
  if (state.err) {
    return (
      <div className="pubkey-err">
        <span className="err">{state.err}</span>
        <button className="ghost" onClick={state.retry}>Retry</button>
      </div>
    )
  }
  return <Cb file="app SSH public key" code={state.pubkey && !state.loading ? state.pubkey : 'generating…'} />
}

// The hosting choice — the one decision in the wizard that can't be changed later without a
// move. It was three click-only <div>s, so a keyboard or screen-reader operator was stuck on
// the 'local' default and could never reach shared or server hosting at all. A radiogroup:
// Tab reaches the selected card, ←/↑ and →/↓ move between them, Space/Enter picks.
const MODES: { id: Mode; title: string; blurb: string }[] = [
  { id: 'local', title: 'Local unshared hosting', blurb: 'Runs on this machine, reachable only from here.' },
  { id: 'tunnel', title: 'Local shared hosting', blurb: 'Runs on this machine, shared online over Tailscale so other admins can sign in. Free, no domain.' },
  { id: 'server', title: 'Server shared hosting', blurb: 'Runs 24/7 on a free cloud server, even with this computer off.' },
]

function ModeChoice({ mode, onPick }: { mode: Mode; onPick: (m: Mode) => void }) {
  const group = useRef<HTMLDivElement>(null)
  const onKey = (e: ReactKeyboardEvent) => {
    const step = /^Arrow(Right|Down)$/.test(e.key) ? 1 : /^Arrow(Left|Up)$/.test(e.key) ? -1 : 0
    if (!step) return
    e.preventDefault()
    const i = MODES.findIndex((m) => m.id === mode)
    const next = MODES[(i + step + MODES.length) % MODES.length]
    onPick(next.id)
    group.current?.querySelector<HTMLElement>(`#mode-${next.id}`)?.focus()
  }
  return (
    <div className="mode-grid" ref={group} role="radiogroup" aria-label="Where Olisar runs" onKeyDown={onKey}>
      {MODES.map((m) => (
        <div
          key={m.id}
          id={`mode-${m.id}`}
          className={'mode-card' + (mode === m.id ? ' sel' : '')}
          role="radio"
          aria-checked={mode === m.id}
          tabIndex={mode === m.id ? 0 : -1}
          onClick={() => onPick(m.id)}
          onKeyDown={(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); onPick(m.id) } }}
        >
          <b>{m.title}</b>
          <p>{m.blurb}</p>
        </div>
      ))}
    </div>
  )
}

type StepId = 'where' | 'bot' | 'remote' | 'signin' | 'server' | 'keys' | 'deploy'

// Each hosting choice gets the steps it needs. Shared hosting's Tailscale step comes before
// sign-in, so sign-in can show both redirect URLs at once.
function stepsFor(mode: Mode): StepId[] {
  return [
    'where', 'bot',
    ...(mode === 'tunnel' ? ['remote' as const] : []),
    'signin', 'server', 'keys',
    ...(mode === 'server' ? ['deploy' as const] : []),
  ]
}

// The progress bar has a slot for each step of the longest choice and opens as many as this
// choice has, from the left, so picking a choice grows or shrinks the bar at its end rather
// than redrawing it. Slots used to belong to steps by name, which slid the middle of the bar
// across between the two shared choices: six steps each, but one's extra step is third and
// the other's is last.
const BAR_SLOTS = Math.max(...MODES.map((m) => stepsFor(m.id).length))

// The setup card's height follows its content, which changes on every step and whenever a
// check, a warning or an error arrives. It used to snap, and since the card is centred its
// top jumped by half of every change: the title up to 110px between steps, and the token
// field 63px while the operator was typing into it. `body` now tweens from the height it had
// to the one its content needs, so the card glides to its new centre instead, clipped along
// the bottom so the footer rides the moving edge.
//
// `flow` is measured, not `body`: it always sits at its content's height, while `body`'s is
// the one being animated. A width change (window drag, interface size) snaps, since a card
// trailing the window by a few hundred ms reads as lag rather than motion.
//
// A deploy or a connect swaps the wizard for the server panel, a different card. `handOff`
// leaves this body's height for the next card that mounts with `arrive`, whose first reading
// tweens from it instead of snapping, so the swap resizes the card like a step change does.
// It's read while the new card renders, when the old one is still on screen, and dropped
// when the old one unmounts, so a later mount can't pick up a stale height.
let handedOff: (() => number) | null = null

export function useHeightTween({ arrive = false } = {}) {
  const body = useRef<HTMLDivElement>(null)
  const flow = useRef<HTMLDivElement>(null)
  const height = useRef<() => number>(() => 0)
  const [arrivedFrom] = useState(() => (arrive && handedOff ? handedOff() : null))
  useLayoutEffect(() => {
    const b = body.current, f = flow.current
    if (!b || !f || typeof ResizeObserver === 'undefined') return
    let last: { w: number; h: number } | null = null
    let anim: Animation | null = null
    const read = () => (anim?.playState === 'running' ? parseFloat(getComputedStyle(b).height) : last?.h ?? 0)
    height.current = read
    const ro = new ResizeObserver(([e]) => {
      const { width: w, height: h } = e.contentRect
      const prev = last ?? (arrivedFrom === null ? null : { w, h: arrivedFrom })
      last = { w, h }
      if (!prev) return
      // Mid-tween, start from where the edge is now rather than where it was headed.
      const from = anim?.playState === 'running' ? parseFloat(getComputedStyle(b).height) : prev.h
      anim?.cancel()
      anim = null
      if (w !== prev.w || Math.abs(h - from) < 1) return
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
      // Growing, the new content overflows until the edge reaches it. Clip only the bottom:
      // the sides stay open so a focus ring or a step sliding in isn't shaved.
      const clip = 'inset(-12px -24px 0 -24px)'
      const ease = getComputedStyle(document.documentElement).getPropertyValue('--ease-out').trim() || 'ease-out'
      anim = b.animate(
        [{ height: `${from}px`, clipPath: clip }, { height: `${h}px`, clipPath: clip }],
        // Longer for a longer move: a check line's 20px in ~190ms, a whole step in ~300ms.
        { duration: Math.min(340, 180 + Math.abs(h - from) * 0.6), easing: ease },
      )
    })
    ro.observe(f)
    return () => {
      ro.disconnect(); anim?.cancel()
      if (handedOff === read) handedOff = null
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const handOff = () => { handedOff = height.current }
  return { body, flow, handOff, arrived: arrivedFrom !== null }
}

// What /api/setup/bot and /api/setup/discord-status say about the bot's Discord application.
type BotApp = {
  id: string; username: string; avatar: string; bot_public: boolean; code_grant: boolean
  intents_missing: string[]; redirect_uris: string[]; invite_url: string
}
type BotGuild = { id: string; name: string; icon: string }

const PORTAL = 'https://discord.com/developers/applications'

type CheckState = 'idle' | 'checking' | 'ok' | 'bad' | 'error'
type LiveCheck<T> = { state: CheckState; result?: T; error: string; recheck: () => void }

// Checks a pasted value once typing pauses, so there's no Test button to find and press.
// `run` answers whether the value is good; a 4xx from the backend also means no, while
// anything else is an outage the operator can retry. Only the latest value's answer lands:
// a slow reply about what was in the field a keystroke ago is dropped. `key` re-runs the
// check when something else it depends on changes.
function useLiveCheck<T>(
  value: string, run: (v: string) => Promise<{ ok: boolean; result?: T }>, key = '',
): LiveCheck<T> {
  const [st, setSt] = useState<{ state: CheckState; result?: T; error: string }>({ state: 'idle', error: '' })
  const seq = useRef(0)
  const runRef = useRef(run)
  runRef.current = run
  const start = useCallback((v: string) => {
    const n = ++seq.current
    if (!v) { setSt({ state: 'idle', error: '' }); return }
    setSt({ state: 'checking', error: '' })
    runRef.current(v)
      .then((r) => { if (n === seq.current) setSt({ state: r.ok ? 'ok' : 'bad', result: r.result, error: '' }) })
      .catch((e: any) => {
        if (n !== seq.current) return
        const refused = e?.status >= 400 && e?.status < 500
        setSt({ state: refused ? 'bad' : 'error', error: e?.message || 'Couldn’t check that.' })
      })
  }, [])
  const v = value.trim()
  useEffect(() => {
    // Straight to "checking", so an answer about the previous value can't pass for this one
    // while the pause runs out.
    seq.current++
    setSt({ state: v ? 'checking' : 'idle', error: '' })
    if (!v) return
    const t = setTimeout(() => start(v), 450)
    return () => clearTimeout(t)
  }, [v, key, start])
  return { ...st, recheck: () => start(v) }
}

// The line under a live-checked field. Each answer arrives as new words rather than changing
// under a line that sat still, but only the words are replaced: the live region itself stays,
// because a screen reader announces what changes inside one and can miss one that turns up
// already filled.
function CheckLine({ check, ok, bad }: { check: LiveCheck<unknown>; ok: ReactNode; bad: string }) {
  if (check.state === 'idle') return null
  const told = check.state === 'checking' || check.state === 'ok'
  return (
    <div className={'check-line' + (check.state === 'ok' ? ' ok' : told ? '' : ' err')} role={told ? 'status' : 'alert'}>
      <ArrivingLine id={check.state}>
        {check.state === 'checking' ? <><span className="spinner" /> Checking…</>
          : check.state === 'ok' ? ok
          : check.state === 'bad' ? bad
          : <>{check.error} <button className="linklike" onClick={check.recheck}>Try again</button></>}
      </ArrivingLine>
    </div>
  )
}

// A check line's contents, replayed whenever `id` changes (see CheckLine).
function ArrivingLine({ id, children }: { id: string; children: ReactNode }) {
  return <span key={id} className="check-line-in wiz-appear">{children}</span>
}

function CopyText({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button className="ghost" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200) }}>
      {done ? <><Icon.check size={13} weight="Bold" /> Copied</> : label}
    </button>
  )
}

// A redirect URL to register, ticked off once Discord lists it.
export function RedirectRow({ url, added }: { url: string; added: boolean }) {
  return (
    <div className="redirect-box">
      <span>{url}</span>
      {added
        ? <span className="ok-pill wiz-pop"><Icon.check size={14} weight="Bold" /> Added</span>
        : <CopyText text={url} />}
    </div>
  )
}

// Tailscale's errors end in a help link, which as plain text had to be retyped.
export function Linkified({ text }: { text: string }) {
  return (
    <>
      {text.split(/(https?:\/\/\S+)/g).map((part, i) => {
        if (!/^https?:\/\//.test(part)) return part
        const url = part.replace(/[.,;:]+$/, '')
        return <Fragment key={i}><a href={url} target="_blank" rel="noreferrer">{url}</a>{part.slice(url.length)}</Fragment>
      })}
    </>
  )
}

/** First-run wizard, shown full-screen when the backend reports the app is
 *  unconfigured. Hosting comes first, since it decides the steps after it. The bot token
 *  then stands in for most of what the Developer Portal used to be visited for: the client
 *  ID comes from it, the intents are switched on through it, and the invite link is built
 *  from it. Local hosting saves + starts the bot here; server hosting instead installs
 *  Olisar onto the operator's VM over SSH. */
export function SetupWizard(
  { status, onDone }:
  { status: SetupStatus; onDone: () => void },
) {
  // Pre-fill from `.env` when the backend supplied it (loopback + not configured).
  const pf = status.prefill || {}
  // In the desktop app this may be one of several bots. Its Tailscale device name defaults to
  // its own name, so two bots' web addresses don't collide; the original keeps "olisar".
  const bots = useBots()
  const thisBot = bots.current
  const shared = sharedServers(bots.bots, bots.activeId)

  const [step, setStep] = useState(0)
  const [err, setErr] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPane, setSettingsPane] = useState<SectionId | undefined>(undefined)
  // Feedback opened from a failure below arrives pre-filled in this screen's own Settings.
  const [fbPrefill, setFbPrefill] = useState<FeedbackPrefill | undefined>(undefined)
  useFeedbackHost((p) => { setFbPrefill(p); setSettingsOpen(true) })
  // Whether `err` is the save failing, rather than a field left empty. Only the first is
  // ours to hear about.
  const [saveFailed, setSaveFailed] = useState(false)
  // Bumped each time Continue or Finish is refused, so the reason arrives again even when
  // it's the one already on screen. Otherwise a second press did nothing anyone could see.
  const [errSeq, setErrSeq] = useState(0)

  // What just changed, so only that part plays its entrance: a step (from the side it came
  // from) or the whole screen (the wizard ↔ connecting to an existing server). Nothing plays
  // on first paint.
  const [moved, setMoved] = useState<{ what: 'step' | 'screen'; back: boolean } | null>(null)
  const enter = (what: 'step' | 'screen') =>
    moved?.what === what ? ' enter' + (moved.back ? ' back' : '') : ''
  const { body, flow, handOff } = useHeightTween()

  // Where it runs
  const [mode, setMode] = useState<Mode>(pf.tunnel_token ? 'tunnel' : 'local')

  // The bot. Checking the token also gets its application ready (see /api/setup/bot).
  const [token, setToken] = useState(pf.discord_token || '')
  const tokenCheck = useLiveCheck<BotApp>(token, (t) => api.setupBot(t).then((r: BotApp) => ({ ok: true, result: r })))
  // The application as last read: from the token check, then from the polls below.
  const [bot, setBot] = useState<BotApp | null>(null)
  const [guilds, setGuilds] = useState<BotGuild[]>([])
  useEffect(() => {
    setBot(tokenCheck.state === 'ok' ? tokenCheck.result ?? null : null)
    setGuilds([])
  }, [tokenCheck.state, tokenCheck.result])

  // Sign-in
  const [secret, setSecret] = useState(pf.discord_client_secret || '')
  const secretCheck = useLiveCheck(
    bot ? secret : '', (s) => api.checkSetupSecret(bot!.id, s), bot?.id,
  )

  // Main server: whichever one the bot joins, unless it's already in several.
  const [guildId, setGuildId] = useState(pf.target_guild_id || '')
  useEffect(() => {
    if (guilds.length && !guilds.some((g) => g.id === guildId)) setGuildId(guilds[0].id)
  }, [guilds])  // eslint-disable-line react-hooks/exhaustive-deps

  // Remote access (shared hosting)
  const [tunnelNode, setTunnelNode] = useState(
    thisBot && thisBot.id !== 'default' ? deviceNameFor(thisBot.name) : 'olisar',
  )
  const [tunnelAuthKey, setTunnelAuthKey] = useState(pf.tunnel_token || '')
  const [provisioning, setProvisioning] = useState(false)
  const [tunnelDone, setTunnelDone] = useState(false)
  const [tunnelUrl, setTunnelUrl] = useState('')
  const [tunnelErr, setTunnelErr] = useState('')

  // Server-hosting extras (collected on the Deploy step)
  const [provider, setProvider] = useState<'oracle' | 'other'>('oracle')
  // Only ever carried over from a server another bot runs on (see the share effect). Asking
  // for it outright went wrong: the operator is already whoever owns the Discord app, and the
  // username the field invited isn't something ADMIN_ALLOWLIST can parse.
  const [adminUser, setAdminUser] = useState('')
  // A standalone shortcut (from the first page): adopt a VM that already runs Olisar,
  // skipping the whole setup. Rendered as its own screen, not a wizard step.
  const [connectMode, setConnectMode] = useState(false)
  const [serverUser, setServerUser] = useState('ubuntu')
  const [serverHost, setServerHost] = useState('')
  const [showKey, setShowKey] = useState(false)  // the collapsible "add this key" fallback
  const [deploying, setDeploying] = useState(false)
  const [deployLog, setDeployLog] = useState('')
  const [deployErr, setDeployErr] = useState('')
  // The bot deployed and is running, but its console has no address (Tailscale refused the
  // key, most often). Not a failed deploy: the fix is a new key and another Deploy.
  const [consoleErr, setConsoleErr] = useState('')
  // A VM running several bots: which install is this one (the connect screen asks).
  const [installs, setInstalls] = useState<{ dir: string; name: string }[]>([])
  const [installDir, setInstallDir] = useState('')

  // Server hosting can go on a server another bot already runs on: nothing to create, no key
  // to paste — that bot lets this one in (see api.shareServer). Offered first when there is
  // one. `source` is the other bot's id, or 'new'.
  const [sourceChoice, setSourceChoice] = useState<string | null>(null)
  const source = sourceChoice ?? (shared[0]?.from.id || 'new')
  const sharing = source !== 'new'
  const [share, setShare] = useState<{ from: string; host: string; user: string } | null>(null)
  const [shareBusy, setShareBusy] = useState(false)
  const [shareErr, setShareErr] = useState('')

  // Keys. Cloudflare and UEX aren't asked for here: images and the Star Citizen extension
  // are added from the console. A developer `.env` still carries them through.
  const [gemini, setGemini] = useState(pf.gemini_api_key || '')
  const geminiCheck = useLiveCheck(gemini, (k) => api.checkSetupGemini(k))
  const [saving, setSaving] = useState(false)

  const steps = stepsFor(mode)
  const last = steps.length - 1
  const cur = steps[Math.min(step, last)]

  // Sign-in redirects back to whichever address the browser used, so that's the one to
  // register: this window's own. A server's console lives on the VM, not here.
  const redirects = mode === 'server' ? [] : [
    window.location.origin + '/auth/callback',
    ...(mode === 'tunnel' && tunnelUrl ? [tunnelUrl.replace(/\/$/, '') + '/auth/callback'] : []),
  ]
  const added = (u: string) => !!bot?.redirect_uris.includes(u)
  // Sign-in can't move on until Discord lists every redirect: without them, the first sign-in
  // after setup fails on Discord's own error page, with nothing here to say why.
  const redirectPending = cur === 'signin' && !redirects.every(added)

  // While the operator is off in the Developer Portal or inviting the bot, keep reading the
  // application so each step ticks itself off without a "Check again" button.
  const watching = !!bot && (
    (cur === 'bot' && (bot.intents_missing.length > 0 || bot.code_grant))
    || (cur === 'signin' && !redirects.every(added))
    || cur === 'server'
  )
  usePoll(() => api.setupDiscordStatus(token.trim()).then((r: BotApp & { guilds: BotGuild[] }) => {
    const { guilds: gs, ...app } = r
    setBot(app)
    setGuilds(gs)
  }), 3000, watching)

  // The app's SSH key: always needed on the Deploy step (new VM); on the connect
  // screen it's only a fallback (the key is already on a VM the app set up), fetched lazily
  // when the operator expands "Can't connect?".
  const pk = usePubkey((cur === 'deploy' && !sharing) || (connectMode && showKey))

  // Reaching the Deploy step with another bot's server picked: get this bot let in, and
  // carry over what the new install should reuse unless the operator already typed it.
  useEffect(() => {
    if (!(cur === 'deploy' && sharing) || share?.from === source) return
    let alive = true
    setShareBusy(true); setShareErr('')
    api.shareServer(source)
      .then((r: any) => {
        if (!alive) return
        if (!r?.ok) throw new Error(r?.error || 'Couldn’t use that server.')
        setShare({ from: source, host: r.host, user: r.user || 'ubuntu' })
        setAdminUser((a) => a || r.admin_allowlist || '')
      })
      .catch((e: any) => { if (alive) setShareErr(e?.message || 'Couldn’t use that server.') })
      .finally(() => { if (alive) setShareBusy(false) })
    return () => { alive = false }
  }, [cur, sharing, source])  // eslint-disable-line react-hooks/exhaustive-deps

  function pickMode(m: Mode) {
    // Remote access turned on for shared hosting and then abandoned for another choice was
    // left running, publishing this machine for a setup that no longer wanted it.
    if (m !== 'tunnel' && tunnelDone) {
      api.disableTunnel().catch(() => {})
      setTunnelDone(false); setTunnelUrl('')
    }
    setMode(m)
  }

  // What stops the current step from moving on, in words that say what to do about it.
  function blocker(): string {
    if (cur === 'bot') {
      if (!token.trim()) return 'Paste your bot token to continue.'
      if (!bot) return tokenCheck.state === 'checking' ? 'Still checking the token.' : 'Olisar needs a token Discord accepts.'
      if (bot.intents_missing.length) return 'Turn on the intents above to continue.'
    }
    if (cur === 'remote' && !tunnelDone) return 'Turn on remote access before continuing, or go back and pick another option.'
    if (cur === 'signin') {
      if (!secret.trim()) return 'Paste the client secret to continue.'
      if (secretCheck.state !== 'ok')
        return secretCheck.state === 'checking' ? 'Still checking the secret.' : 'Olisar needs the secret that belongs to this bot.'
      if (!redirects.every(added)) return redirects.length > 1 ? 'Add both redirect URLs to continue.' : 'Add the redirect URL to continue.'
    }
    if (cur === 'server' && !guilds.length) return `Add ${bot?.username || 'your bot'} to a server to continue.`
    if (cur === 'keys') {
      if (mode === 'server' && !gemini.trim()) return 'A server can’t start Olisar without a Gemini key.'
      if (gemini.trim() && geminiCheck.state === 'bad') return 'Fix the Gemini key, or clear it to add it later.'
    }
    return ''
  }

  // A "do this first" message goes once it's done. The checks and polls resolve on their own,
  // and a stale instruction under a step that's ready reads as still blocked.
  const blocked = blocker()
  useEffect(() => {
    if (!blocked && !saveFailed) setErr('')
  }, [blocked])  // eslint-disable-line react-hooks/exhaustive-deps

  function next() {
    setSaveFailed(false)
    const why = blocker()
    setErr(why)
    if (why) { setErrSeq((n) => n + 1); return }
    setMoved({ what: 'step', back: false })
    setStep((s) => Math.min(s + 1, last))
  }

  function back() {
    setErr(''); setSaveFailed(false)
    setMoved({ what: 'step', back: true })
    setStep((s) => Math.max(0, s - 1))
  }

  function showConnect(on: boolean) {
    setDeployErr('')
    setMoved({ what: 'screen', back: !on })
    setConnectMode(on)
  }

  async function enableTunnel() {
    setTunnelErr(''); setProvisioning(true); setTunnelDone(false)
    try {
      const r = await api.enableTunnel({ auth_key: tunnelAuthKey.trim(), hostname: tunnelNode.trim() })
      setTunnelUrl(r.public_url || '')
      setTunnelDone(true)
      setErr('')  // "turn on remote access first" no longer applies
    } catch (e: any) {
      setTunnelErr(e?.message || 'Couldn’t turn on remote access.')
    } finally {
      setProvisioning(false)
    }
  }

  // Keys a developer `.env` supplied, which this wizard no longer has fields for.
  const envKeys: Record<string, string> = {}
  if (pf.cloudflare_account_id) envKeys.cloudflare_account_id = pf.cloudflare_account_id
  if (pf.cloudflare_api_token) envKeys.cloudflare_api_token = pf.cloudflare_api_token
  if (pf.uex_api_key) envKeys.uex_api_key = pf.uex_api_key

  async function finish() {
    setSaveFailed(false)
    const why = blocker()
    setErr(why)
    if (why) { setErrSeq((n) => n + 1); return }
    setSaving(true)
    try {
      const keys: Record<string, string> = { ...envKeys }
      if (gemini.trim()) keys.gemini_api_key = gemini.trim()
      if (Object.keys(keys).length) await api.saveSetupKeys(keys)
      await api.saveSetup({
        discord_token: token.trim(),
        discord_client_id: bot?.id || '',
        discord_client_secret: secret.trim(),
        target_guild_id: guildId,
      })
      onDone()
    } catch (e: any) {
      setErr(e?.message || 'Save failed.')
      setErrSeq((n) => n + 1)
      setSaveFailed(true)
      setSaving(false)
    }
  }

  // Server hosting: the app SSHes into the operator's VM, installs Docker + the config,
  // and starts the container. On success the app is in server mode (no local bot) and
  // flips to the remote control panel.
  async function deployServer() {
    setDeployErr(''); setConsoleErr('')
    const host = sharing ? share?.host || '' : serverHost.trim()
    const user = sharing ? share?.user || 'ubuntu' : serverUser.trim() || 'ubuntu'
    if (sharing && !host) return setDeployErr(shareErr || 'Still connecting to that server.')
    if (!host) return setDeployErr('Enter the VM’s public IP address.')
    if (!(gemini.trim() && tunnelAuthKey.trim()))
      return setDeployErr('A Gemini key and a Tailscale auth key are both required.')
    setDeploying(true); setDeployLog('')
    try {
      const r = await api.serverDeploy({ host, user, env: envFile })
      if (r?.ok && r.console_error) setConsoleErr(r.console_error)
      else if (r?.ok) { handOff(); onDone() }
      else { setDeployErr(r?.error || 'Deploy failed.'); setDeployLog(r?.log || '') }
    } catch (e: any) {
      setDeployErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setDeploying(false)
    }
  }

  // Connect to a VM that already runs Olisar (no reinstall) — the app just verifies over
  // SSH and adopts it, then flips to the control panel.
  async function connectServer() {
    setDeployErr('')
    if (!serverHost.trim()) return setDeployErr('Enter the VM’s public IP address.')
    setDeploying(true)
    try {
      // Another bot here already runs on that VM: have it let this bot's key in first.
      const via = shared.find((x) => x.host === serverHost.trim())
      if (via) await api.shareServer(via.from.id).catch(() => null)
      const r = await api.serverConnect({
        host: serverHost.trim(), user: serverUser.trim() || 'ubuntu', app_dir: installDir || undefined,
      })
      if (r?.ok) { handOff(); onDone() }
      else if (r?.choose?.length) { setInstalls(r.choose); setInstallDir(r.choose[0].dir); setDeployErr('') }
      else { setDeployErr(r?.error || 'Couldn’t connect to that VM.') }
    } catch (e: any) {
      setDeployErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setDeploying(false)
    }
  }

  // The footer's primary action, per screen and step.
  const primary = connectMode
    ? { label: deploying ? 'Connecting…' : 'Connect', run: connectServer, off: deploying }
    : step < last
      ? { label: 'Continue', run: next, off: redirectPending }
      : mode === 'server'
        ? { label: deploying ? 'Deploying…' : 'Deploy to server', run: deployServer, off: deploying || (sharing && shareBusy) }
        : { label: saving ? 'Saving…' : 'Finish & start Olisar', run: finish, off: saving }

  // "Connect to existing server" goes with the screen it was on, and Back comes home to a
  // first step where Back is disabled. Either way focus would drop to the page: land on the
  // address the connect screen asks for, and on the way back on the button that opened it.
  const revealBtn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (moved?.what !== 'screen') return
    if (connectMode) flow.current?.querySelector<HTMLInputElement>('input')?.focus()
    else revealBtn.current?.focus()
  }, [connectMode])  // eslint-disable-line react-hooks/exhaustive-deps

  const A = (href: string, text: string) => (
    <a href={href} target="_blank" rel="noreferrer">{text}</a>
  )

  // The turnkey deploy package the operator runs on their cloud VM.
  const envFile = (() => {
    const L = [
      `DISCORD_TOKEN=${token.trim() || '…'}`,
      `DISCORD_CLIENT_ID=${bot?.id || '…'}`,
      `DISCORD_CLIENT_SECRET=${secret.trim() || '…'}`,
    ]
    if (guildId) L.push(`TARGET_GUILD_ID=${guildId}`)
    if (adminUser.trim()) L.push(`ADMIN_ALLOWLIST=${adminUser.trim()}`)
    L.push(`GEMINI_API_KEY=${gemini.trim() || '…'}`)
    L.push(`TAILSCALE_AUTH=${tunnelAuthKey.trim() || 'tskey-auth-…'}`)
    L.push(`OLISAR_FUNNEL_HOSTNAME=${tunnelNode.trim() || 'olisar'}`)
    if (envKeys.cloudflare_account_id) L.push(`CLOUDFLARE_ACCOUNT_ID=${envKeys.cloudflare_account_id}`)
    if (envKeys.cloudflare_api_token) L.push(`CLOUDFLARE_API_TOKEN=${envKeys.cloudflare_api_token}`)
    if (envKeys.uex_api_key) L.push(`UEX_API_KEY=${envKeys.uex_api_key}`)
    return L.join('\n')
  })()


  return (
    <div className="setup">
      <div className="box">
        <BotMenu variant="chip" onManage={() => { setSettingsPane('bots'); setSettingsOpen(true) }} />
        <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => { setSettingsPane(undefined); setSettingsOpen(true) }}>
          <Icon.settings size={16} />
        </button>
        {settingsOpen && (
          <SettingsModal
            sections={['general', 'bots', 'updates', 'desktop', 'feedback']}
            initialSection={settingsPane}
            prefill={fbPrefill}
            onClose={() => { setSettingsOpen(false); setFbPrefill(undefined) }}
          />
        )}
        <div className="box-body" ref={body}>
        <div ref={flow}>
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        {connectMode ? (
          <div key="connect" className={'wiz-screen' + enter('screen')}>
            <h1>Connect to an existing server</h1>
            <p className="step-sub">
              Point Olisar at a cloud VM that already runs it. Nothing is reinstalled.
            </p>
            <div className="callout tip" style={{ marginBottom: 16 }}>
              <span className="ic"><Icon.info size={17} weight="Bold" /></span>
              <div className="callout-body">Its persona, memory, knowledge, and settings are kept.</div>
            </div>
            <Field label="VM public IP address" desc="The VM already running Olisar.">
              <Text value={serverHost} onChange={(v) => { setServerHost(v); setInstalls([]); setInstallDir('') }} placeholder="e.g. 203.0.113.9" mono />
            </Field>
            {installs.length > 0 && (
              <Field label="Which bot is this?" desc="This server runs more than one.">
                <Select value={installDir} onChange={setInstallDir} options={installs.map((i) => ({ value: i.dir, label: i.name }))} />
              </Field>
            )}
            <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
              <summary>Can’t connect? Add this app’s SSH key to the VM</summary>
              <div className="desc" style={{ marginTop: 8 }}>
                Paste this into the VM’s <code>~/.ssh/authorized_keys</code>, or the provider’s SSH-keys box, then Connect. A VM this app already set up trusts it automatically.
              </div>
              <PubkeyBox state={pk} />
              <Field label="SSH user" desc="The VM's login user. Ubuntu images use ubuntu.">
                <Text value={serverUser} onChange={setServerUser} placeholder="ubuntu" mono />
              </Field>
            </details>
            {deploying && (
              <div className="callout note wiz-appear" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Connecting to your VM over SSH…</div>
              </div>
            )}
            {deployErr && (
              <div className="err-block wiz-appear">
                <div className="err">{deployErr}</div>
                <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Connecting to my existing Olisar server failed.', deployErr) }}>
                  Report a problem
                </FeedbackButton>
              </div>
            )}
          </div>
        ) : (
          <div key="wizard" className={'wiz-screen' + enter('screen')}>
        <h1>Set up Olisar</h1>
        <p className="step-sub">
          A one-time setup to connect Olisar to your Discord server.
        </p>
        <div className="steps" style={{ gridTemplateColumns: Array.from({ length: BAR_SLOTS }, (_, i) => (i < steps.length ? '1fr' : '0fr')).join(' ') }}>
          {Array.from({ length: BAR_SLOTS }, (_, i) => <i key={i} className={i <= step ? 'on' : ''} />)}
        </div>

        <div key={cur} className={'wiz-step' + enter('step')}>
        {cur === 'where' && <ModeChoice mode={mode} onPick={pickMode} />}

        {cur === 'bot' && (
          <>
            <div className="tunnel-help">
              <ol>
                <li>Create an application in the {A(PORTAL, 'Discord Developer Portal')}, or open the one you have.</li>
                <li>Open <strong>Bot</strong>, press <strong>Reset Token</strong>, and copy the token.</li>
              </ol>
            </div>
            <Field label="Bot token">
              <Text value={token} onChange={setToken} placeholder="Paste the token here" mono />
            </Field>
            <CheckLine
              check={tokenCheck}
              ok={<>
                {bot?.avatar ? <img className="check-avatar" src={bot.avatar} alt="" /> : <Icon.check size={14} weight="Bold" />}
                <span>Connected as <b>{bot?.username}</b></span>
              </>}
              bad="Discord didn’t accept that token."
            />
            {bot && bot.intents_missing.length > 0 && (
              <div className="callout warning wiz-appear">
                <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                <div className="callout-body">
                  Turn on <strong>{intentList(bot.intents_missing)}</strong> on {A(`${PORTAL}/${bot.id}/bot`, 'the Bot page')}, under Privileged Gateway Intents.
                </div>
              </div>
            )}
            {bot?.code_grant && (
              <div className="callout warning wiz-appear">
                <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                <div className="callout-body">
                  Turn off <strong>Requires OAuth2 Code Grant</strong> on {A(`${PORTAL}/${bot.id}/bot`, 'the Bot page')}, or the invite link won’t work.
                </div>
              </div>
            )}
          </>
        )}

        {cur === 'remote' && (
          <>
            <div className="tunnel-help">
              <b>Free remote access via Tailscale — no domain needed</b>
              <ol>
                <li>Create a free {A('https://login.tailscale.com/start', 'Tailscale account')} (sign in with Google, GitHub, etc.).</li>
                <li>Generate an auth key at {A('https://login.tailscale.com/admin/settings/keys', 'Settings → Keys → Generate auth key')}, turning on <strong>Reusable</strong>. Paste it below.</li>
                <li>Click <strong>Enable remote access</strong>. The first time, Tailscale may ask you to turn on <strong>Funnel</strong> for your tailnet: follow the link in the message, then press Enable again.</li>
              </ol>
              <div style={{ marginTop: 8 }}>
                Your dashboard then lives at a stable <code>https://…ts.net</code> address. Other admins just open it and sign in with Discord; they don't need Tailscale themselves.
              </div>
            </div>
            <Field
              label="Tailscale auth key"
              desc="Stored on this machine and only ever handed to Tailscale."
            >
              <Text value={tunnelAuthKey} onChange={(v) => { setTunnelAuthKey(v); setTunnelDone(false) }} placeholder="tskey-auth-…" mono />
            </Field>
            <Field
              label="Device name (optional)"
              desc="Becomes the first part of your dashboard's web address."
            >
              <Text value={tunnelNode} onChange={(v) => { setTunnelNode(v); setTunnelDone(false) }} placeholder="olisar" mono />
            </Field>
            <div className="wiz-foot">
              <span className="grow">
                {tunnelDone && tunnelUrl && <span className="ok-pill wiz-pop"><Icon.check size={14} weight="Bold" /> Live at {tunnelUrl}</span>}
                {tunnelErr && <span className="err wiz-appear"><Linkified text={tunnelErr} /></span>}
              </span>
              <button disabled={!tunnelAuthKey.trim() || provisioning} onClick={enableTunnel}>
                {provisioning ? 'Connecting…' : tunnelDone ? 'Reconnect' : 'Enable remote access'}
              </button>
            </div>
            {/* Funnel is the step people get stuck on, and the fix usually lives in
                Tailscale's admin panel rather than here: a question, not a bug. */}
            {tunnelErr && (
              <p className="err-help">
                Stuck?{' '}
                <FeedbackButton className="linklike" prefill={{
                  category: 'Question',
                  message: reportBody('I\'m stuck turning on remote access with Tailscale.', tunnelErr, 'What I\'ve tried:'),
                }}>Ask the team</FeedbackButton>
              </p>
            )}
          </>
        )}

        {cur === 'signin' && bot && (
          <>
            <Field
              label="Client secret"
              desc={<>On {A(`${PORTAL}/${bot.id}/oauth2`, 'the OAuth2 page')}, press <strong>Reset Secret</strong> and copy it.</>}
            >
              <Text value={secret} onChange={setSecret} placeholder="Paste the secret here" mono />
            </Field>
            <CheckLine
              check={secretCheck}
              ok={<><Icon.check size={14} weight="Bold" /> Secret matches</>}
              bad={`That isn’t ${bot.username}’s client secret.`}
            />
            {redirects.length > 0 && (<>
              <Field
                plain
                label={redirects.length > 1 ? 'Redirect URLs' : 'Redirect URL'}
                desc={<>On the same page, under <strong>Redirects</strong>, add {redirects.length > 1 ? 'both' : 'it'} and press <strong>Save Changes</strong>.</>}
              >
                <div className="redirect-list">
                  {redirects.map((u) => <RedirectRow key={u} url={u} added={added(u)} />)}
                </div>
              </Field>
              {!redirects.every(added) && (
                <div className="check-line wiz-appear" role="status">
                  <span className="spinner" /> Waiting for Discord to list {redirects.length > 1 ? 'them' : 'it'}…
                </div>
              )}
            </>)}
          </>
        )}

        {cur === 'server' && bot && (
          <>
            <Field plain label={`Add ${bot.username} to your server`}>
              <div className="invite-row">
                <a className="btn-discord" href={bot.invite_url} target="_blank" rel="noreferrer">
                  <DiscordLogo /> Add to Discord
                </a>
                <CopyText text={bot.invite_url} label="Copy link" />
              </div>
            </Field>
            <div className={'check-line' + (guilds.length ? ' ok' : '')} role="status">
              <ArrivingLine id={guilds.length ? 'joined' : 'waiting'}>
                {guilds.length === 0
                  ? <><span className="spinner" /> Waiting for {bot.username} to join a server…</>
                  : <><Icon.check size={14} weight="Bold" /> <span>In {guilds.map((g) => g.name).join(', ')}</span></>}
              </ArrivingLine>
            </div>
            {guilds.length > 1 && (
              <Field label="Main server" desc="Its persona and settings also apply in DMs.">
                <Select value={guildId} onChange={setGuildId} options={guilds.map((g) => ({ value: g.id, label: g.name }))} />
              </Field>
            )}
          </>
        )}

        {cur === 'keys' && (
          <>
            {/* Required for a server, which can't start without it; optional here, where the
                console can add it later. */}
            <Field
              label="Gemini API key"
              desc={mode === 'server'
                ? <>Powers everything Olisar says. Create a free key in {A('https://aistudio.google.com/apikey', 'Google AI Studio')}.</>
                : <>Powers everything Olisar says. Create a free key in {A('https://aistudio.google.com/apikey', 'Google AI Studio')}. You can add it later, but the bot can't reply without it.</>}
            >
              <Text value={gemini} onChange={setGemini} placeholder="AIza…" mono />
            </Field>
            <CheckLine
              check={geminiCheck}
              ok={<><Icon.check size={14} weight="Bold" /> Key works</>}
              bad="Google didn’t accept that key."
            />
          </>
        )}

        {cur === 'deploy' && !connectMode && (
          <>
            {shared.length > 0 && (
              <Segmented
                className="deploy-seg"
                ariaLabel="Which server"
                value={source}
                onChange={setSourceChoice}
                options={[
                  ...shared.map((x) => ({ value: x.from.id, label: serverLabel(x) })),
                  { value: 'new', label: 'A new server' },
                ]}
              />
            )}

            {sharing ? (
              <div className={'callout ' + (shareErr ? 'warning' : 'note')} style={{ marginBottom: 16 }}>
                <span className="ic">{shareBusy ? <span className="spinner" /> : <Icon.info size={17} weight="Bold" />}</span>
                <div className="callout-body">
                  {shareBusy ? 'Connecting to that server…'
                    : shareErr ? shareErr
                    : <>Olisar adds this bot to <b>{share?.host}</b>, next to the one already there. Nothing to set up on the server.</>}
                </div>
              </div>
            ) : (
            <>
            <div className="deploy-seg">
              <button className={provider === 'oracle' ? 'on' : ''} onClick={() => setProvider('oracle')}>Oracle Cloud · free</button>
              <button className={provider === 'other' ? 'on' : ''} onClick={() => setProvider('other')}>Other cloud</button>
            </div>

            {provider === 'oracle' ? (
              <div className="tunnel-help">
                <b>Create a free Oracle Cloud VM — Olisar installs itself onto it</b>
                <ol>
                  <li>Create a free {A('https://www.oracle.com/cloud/free/', 'Oracle Cloud account')}. A card is needed to verify identity, but the Always Free ARM server costs nothing.</li>
                  <li><strong>Menu → Compute → Instances → Create instance</strong>. Image <strong>Ubuntu 22.04</strong>, shape <strong>VM.Standard.A1.Flex</strong> (Ampere — Always Free). If you see <strong>"out of capacity"</strong>, switch Availability Domain or region and retry. Free ARM frees up through the day.</li>
                  <li>Under <strong>Add SSH keys</strong>, choose <strong>Paste public keys</strong> and paste the key below. Leave networking on defaults. Create it.</li>
                  <li>Open the instance's details, copy its <strong>Public IP address</strong> into the field below, and press <strong>Deploy to server</strong>. Olisar SSHes in and sets everything up, with no terminal needed.</li>
                </ol>
              </div>
            ) : (
              <div className="tunnel-help">
                <b>Any Linux VM — Olisar installs itself over SSH</b>
                <ol>
                  <li>Create an <strong>Ubuntu 22.04</strong> VM (1 GB+ RAM) anywhere — DigitalOcean, Hetzner, AWS EC2, etc. — with user <code>ubuntu</code> and passwordless <code>sudo</code>.</li>
                  <li>Add the SSH public key below to the VM (its "SSH keys" box, or <code>~/.ssh/authorized_keys</code>).</li>
                  <li>Copy the VM's public IP into the field below and press <strong>Deploy to server</strong>.</li>
                </ol>
              </div>
            )}

            <Field plain label="SSH public key — paste this when creating the VM"
              desc="The matching private key never leaves this machine.">
              <PubkeyBox state={pk} />
            </Field>

            <Field label="VM public IP address" desc="From the instance's details page.">
              <Text value={serverHost} onChange={setServerHost} placeholder="e.g. 203.0.113.9" mono />
            </Field>
            </>
            )}
            <Field label="Tailscale auth key" desc={sharing
              ? <>A new Tailscale key is needed per bot. Create one: {A('https://login.tailscale.com/admin/settings/keys', 'Tailscale → Settings → Keys')}.</>
              : <>Gives your server a dashboard address without needing a domain. Create a reusable key at {A('https://login.tailscale.com/admin/settings/keys', 'Tailscale → Settings → Keys')}.</>}>
              <Text value={tunnelAuthKey} onChange={setTunnelAuthKey} placeholder="tskey-auth-…" mono />
            </Field>

            {deploying && (
              <div className="callout note wiz-appear" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Installing Olisar on your VM. This takes a few minutes — keep this window open.</div>
              </div>
            )}
            {deployLog && <Cb file="install log" code={deployLog} />}
            {consoleErr && !deploying && (
              <div className="callout warning wiz-appear">
                <span className="ic"><Icon.warn size={17} weight="Bold" /></span>
                <div className="callout-body">Olisar is running on your server, but its console can’t be reached. <Linkified text={consoleErr} /></div>
              </div>
            )}
            {/* The costliest failure in setup: minutes in, with the log already on screen.
                The report carries both, so nobody has to copy a terminal's worth of text. */}
            {deployErr && (
              <div className="err-block wiz-appear">
                <div className="err">{deployErr}</div>
                <FeedbackButton className="" prefill={{
                  category: 'Bug report',
                  logs: true,
                  message: [
                    `Deploying Olisar to my server failed (${provider === 'oracle' ? 'Oracle Cloud' : 'another cloud'}).`,
                    '', 'Error:', deployErr,
                    ...(deployLog ? ['', 'Install log (last lines):', logTail(deployLog)] : []),
                    '', 'What I was doing:', '',
                  ].join('\n'),
                }}>
                  Send this to the Olisar team
                </FeedbackButton>
              </div>
            )}
          </>
        )}

        {err && (saveFailed ? (
          <div key={errSeq} className="err-block wiz-appear">
            <div className="err">{err}</div>
            <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Finishing setup failed.', err) }}>
              Report a problem
            </FeedbackButton>
          </div>
        ) : <div key={errSeq} className="err wiz-appear">{err}</div>)}
        </div>
          </div>
        )}
        </div>
        </div>

        {/* Outside the tweened body, so it rides the card's bottom edge. One footer and one
            primary button for every step and both screens: the button that was pressed is
            still there afterwards, so focus stays on it and Enter walks the whole wizard. */}
        <div className="wiz-foot">
          <button disabled={connectMode ? deploying : step === 0 || saving} onClick={connectMode ? () => showConnect(false) : back}>
            Back
          </button>
          <span className="grow" />
          <div className="cta-reveal">
            <div className="reveal-slot">
              {!connectMode && cur === 'where' && (
                <button ref={revealBtn} className="ghost reveal-btn" onClick={() => showConnect(true)}>Connect to existing server</button>
              )}
            </div>
            <button className="primary" disabled={primary.off} onClick={primary.run}>{primary.label}</button>
          </div>
        </div>
      </div>
    </div>
  )
}
