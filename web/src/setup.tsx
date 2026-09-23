import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { api } from './api'
import { BotMenu, deviceNameFor, serverLabel, sharedServers, useBots } from './bots'
import { Icon } from './icons'
import { FeedbackButton, SettingsModal, useFeedbackHost, type SectionId } from './settings'
import { logTail, reportBody, type FeedbackPrefill } from './feedback'
import { Field, Segmented, Select, Text } from './ui'

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
type StepId = 'token' | 'app' | 'access' | 'remote' | 'keys' | 'deploy'

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

/** First-run wizard, shown full-screen when the backend reports the app is
 *  unconfigured. Collects the operator's Discord credentials + hosting choice.
 *  Local hosting saves + starts the bot here; server hosting instead hands the
 *  operator a turnkey deploy package (env + commands) for a cloud VM. */
export function SetupWizard(
  { status, onDone, initialConnectMode }:
  { status: SetupStatus; onDone: () => void; initialConnectMode?: boolean },
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
  const [copied, setCopied] = useState<'' | 'local' | 'tunnel'>('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsPane, setSettingsPane] = useState<SectionId | undefined>(undefined)
  // Feedback opened from a failure below arrives pre-filled in this screen's own Settings.
  const [fbPrefill, setFbPrefill] = useState<FeedbackPrefill | undefined>(undefined)
  useFeedbackHost((p) => { setFbPrefill(p); setSettingsOpen(true) })
  // Whether `err` is the save failing, rather than a field left empty. Only the first is
  // ours to hear about.
  const [saveFailed, setSaveFailed] = useState(false)

  // Step 1 — bot token
  const [token, setToken] = useState(pf.discord_token || '')
  const [validating, setValidating] = useState(false)
  const [botName, setBotName] = useState<string | null>(null)
  // Beside Test token, where the success reads, rather than under the whole step.
  const [tokenErr, setTokenErr] = useState('')

  // Step 2 — application
  const [clientId, setClientId] = useState(pf.discord_client_id || '')
  const [clientSecret, setClientSecret] = useState(pf.discord_client_secret || '')
  const [guildId, setGuildId] = useState(pf.target_guild_id || '')

  // Step 3 — hosting mode
  const [mode, setMode] = useState<Mode>(pf.tunnel_token ? 'tunnel' : 'local')
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
  const [adminUser, setAdminUser] = useState('')
  // A standalone shortcut (from the first page): adopt a VM that already runs Olisar,
  // skipping the whole setup. Rendered as its own screen, not a wizard step.
  // Reconnect from a reset/reinstall (App passes hosting_mode==='server') opens the connect
  // flow directly instead of the full wizard.
  const [connectMode, setConnectMode] = useState(!!initialConnectMode)
  const [serverUser, setServerUser] = useState('ubuntu')
  const [serverHost, setServerHost] = useState('')
  const [showKey, setShowKey] = useState(false)  // the collapsible "add this key" fallback
  const [deploying, setDeploying] = useState(false)
  const [deployLog, setDeployLog] = useState('')
  const [deployErr, setDeployErr] = useState('')
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

  // Local hosting is four steps. Shared hosting adds one of its own for Tailscale, and server
  // hosting keeps the API keys step and adds Deploy after it: each used to fold its extra
  // setup into a step shaped like the others, so a five-step setup showed four.
  const steps: StepId[] = [
    'token', 'app', 'access',
    ...(mode === 'tunnel' ? ['remote' as const] : []),
    'keys',
    ...(mode === 'server' ? ['deploy' as const] : []),
  ]
  const last = steps.length - 1
  const cur = steps[Math.min(step, last)]

  // The app's SSH key: always needed on the Deploy step (new VM); on the connect/reconnect
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
        setTunnelAuthKey((k) => k || r.tailscale_auth || '')
        setAdminUser((a) => a || r.admin_allowlist || '')
      })
      .catch((e: any) => { if (alive) setShareErr(e?.message || 'Couldn’t use that server.') })
      .finally(() => { if (alive) setShareBusy(false) })
    return () => { alive = false }
  }, [cur, sharing, source])  // eslint-disable-line react-hooks/exhaustive-deps

  // Step 4 — keys
  const [gemini, setGemini] = useState(pf.gemini_api_key || '')
  const [cfAccount, setCfAccount] = useState(pf.cloudflare_account_id || '')
  const [cfToken, setCfToken] = useState(pf.cloudflare_api_token || '')
  const [uex, setUex] = useState(pf.uex_api_key || '')
  const [saving, setSaving] = useState(false)

  const redirectLocal = status.local_url.replace(/\/$/, '') + '/auth/callback'
  const redirectTunnel = tunnelUrl ? tunnelUrl.replace(/\/$/, '') + '/auth/callback' : ''

  async function validate() {
    setErr(''); setTokenErr(''); setValidating(true); setBotName(null)
    try {
      const r = await api.validateSetupToken(token.trim())
      setBotName(r.username || 'your bot')
    } catch (e: any) {
      setTokenErr(e?.message || 'token validation failed')
    } finally {
      setValidating(false)
    }
  }

  function next() {
    setErr(''); setSaveFailed(false)
    if (cur === 'token' && !token.trim()) return setErr('Paste your bot token to continue.')
    if (cur === 'app' && !(clientId.trim() && clientSecret.trim()))
      return setErr('Client ID and client secret are both required.')
    if (cur === 'remote' && !tunnelDone)
      return setErr('Turn on remote access before continuing, or go back and pick another option.')
    if (cur === 'keys' && mode === 'server' && !gemini.trim())
      return setErr('A server can’t start Olisar without a Gemini key.')
    setStep((s) => Math.min(s + 1, last))
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

  async function finish() {
    setErr(''); setSaveFailed(false); setSaving(true)
    try {
      const keys: Record<string, string> = {}
      if (gemini.trim()) keys.gemini_api_key = gemini.trim()
      if (cfAccount.trim()) keys.cloudflare_account_id = cfAccount.trim()
      if (cfToken.trim()) keys.cloudflare_api_token = cfToken.trim()
      if (uex.trim()) keys.uex_api_key = uex.trim()
      if (Object.keys(keys).length) await api.saveSetupKeys(keys)
      await api.saveSetup({
        discord_token: token.trim(),
        discord_client_id: clientId.trim(),
        discord_client_secret: clientSecret.trim(),
        target_guild_id: guildId.trim(),
      })
      onDone()
    } catch (e: any) {
      setErr(e?.message || 'Save failed.')
      setSaveFailed(true)
      setSaving(false)
    }
  }

  // Server hosting: the app SSHes into the operator's VM, installs Docker + the config,
  // and starts the container. On success the app is in server mode (no local bot) and
  // flips to the remote control panel.
  async function deployServer() {
    setDeployErr('')
    const host = sharing ? share?.host || '' : serverHost.trim()
    const user = sharing ? share?.user || 'ubuntu' : serverUser.trim() || 'ubuntu'
    if (sharing && !host) return setDeployErr(shareErr || 'Still connecting to that server.')
    if (!host) return setDeployErr('Enter the VM’s public IP address.')
    if (!(gemini.trim() && tunnelAuthKey.trim()))
      return setDeployErr('A Gemini key and a Tailscale auth key are both required.')
    setDeploying(true); setDeployLog('')
    try {
      const r = await api.serverDeploy({ host, user, env: envFile })
      if (r?.ok) { onDone() }
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
      if (r?.ok) { onDone() }
      else if (r?.choose?.length) { setInstalls(r.choose); setInstallDir(r.choose[0].dir); setDeployErr('') }
      else { setDeployErr(r?.error || 'Couldn’t connect to that VM.') }
    } catch (e: any) {
      setDeployErr(e?.message || 'Couldn’t reach the server.')
    } finally {
      setDeploying(false)
    }
  }

  const A = (href: string, text: string) => (
    <a href={href} target="_blank" rel="noreferrer">{text}</a>
  )

  // The turnkey deploy package the operator runs on their cloud VM.
  const envFile = (() => {
    const L = [
      `DISCORD_TOKEN=${token.trim() || '…'}`,
      `DISCORD_CLIENT_ID=${clientId.trim() || '…'}`,
      `DISCORD_CLIENT_SECRET=${clientSecret.trim() || '…'}`,
    ]
    if (guildId.trim()) L.push(`TARGET_GUILD_ID=${guildId.trim()}`)
    if (adminUser.trim()) L.push(`ADMIN_ALLOWLIST=${adminUser.trim()}`)
    L.push(`GEMINI_API_KEY=${gemini.trim() || '…'}`)
    L.push(`TAILSCALE_AUTH=${tunnelAuthKey.trim() || 'tskey-auth-…'}`)
    L.push(`OLISAR_FUNNEL_HOSTNAME=${tunnelNode.trim() || 'olisar'}`)
    if (cfAccount.trim()) L.push(`CLOUDFLARE_ACCOUNT_ID=${cfAccount.trim()}`)
    if (cfToken.trim()) L.push(`CLOUDFLARE_API_TOKEN=${cfToken.trim()}`)
    if (uex.trim()) L.push(`UEX_API_KEY=${uex.trim()}`)
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
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        {connectMode ? (
          <>
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
              <div className="callout note" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Connecting to your VM over SSH…</div>
              </div>
            )}
            {deployErr && (
              <div className="err-block">
                <div className="err">{deployErr}</div>
                <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Connecting to my existing Olisar server failed.', deployErr) }}>
                  Report a problem
                </FeedbackButton>
              </div>
            )}
            <div className="wiz-foot">
              <button disabled={deploying} onClick={() => { setConnectMode(false); setDeployErr('') }}>Back</button>
              <span className="grow" />
              <button className="primary" disabled={deploying} onClick={connectServer}>{deploying ? 'Connecting…' : 'Connect'}</button>
            </div>
          </>
        ) : (
          <>
        <h1>Set up Olisar</h1>
        <p className="step-sub">
          A one-time setup to connect Olisar to your Discord server.
        </p>
        <div className="steps">
          {steps.map((_, i) => <i key={i} className={i <= step ? 'on' : ''} />)}
        </div>

        {cur === 'token' && (
          <>
            <Field
              label="Discord bot token"
              desc={<>In the {A('https://discord.com/developers/applications', 'Discord Developer Portal')}, open your application → <strong>Bot</strong> → Reset/Copy Token. Turn on the <strong>Message Content</strong> and <strong>Server Members</strong> intents there too, plus <strong>Presence Intent</strong> if you want status and voice awareness.</>}
            >
              <Text value={token} onChange={(v) => { setToken(v); setBotName(null); setTokenErr('') }} placeholder="your bot token" mono />
            </Field>
            <div className="wiz-foot">
              <span className="grow">
                {botName && <span className="ok-pill"><Icon.check size={14} weight="Bold" /> Connected as {botName}</span>}
                {tokenErr && <span className="err">{tokenErr}</span>}
              </span>
              <button disabled={!token.trim() || validating} onClick={validate}>
                {validating ? 'Checking…' : 'Test token'}
              </button>
            </div>
          </>
        )}

        {cur === 'app' && (
          <>
            <Field
              label="Client ID"
              desc={<>Developer Portal → <strong>OAuth2</strong> → Client ID (also called Application ID).</>}
            >
              <Text value={clientId} onChange={setClientId} placeholder="application / client id" mono />
            </Field>
            <Field
              label="Client secret"
              desc={<>Developer Portal → <strong>OAuth2</strong> → Reset Secret. Used so admins can sign in to this console.</>}
            >
              <Text value={clientSecret} onChange={setClientSecret} placeholder="client secret" mono />
            </Field>
            <Field
              label="Main server ID (optional)"
              desc={<>With Developer Mode on, right-click your server in Discord → Copy Server ID. This only sets Olisar's home for DMs; it still works in every server it's invited to.</>}
            >
              <Text value={guildId} onChange={setGuildId} placeholder="e.g. 1321947496179568680" mono />
            </Field>
          </>
        )}

        {cur === 'access' && (
          <>
            <ModeChoice mode={mode} onPick={setMode} />


            {mode === 'local' && (
              <Field
                plain
                label="Add this redirect URL in the Developer Portal"
                desc={<>Developer Portal → <strong>OAuth2</strong> → Redirects → Add.</>}
              >
                <div className="redirect-box">
                  <span>{redirectLocal}</span>
                  <button className="ghost" onClick={() => { navigator.clipboard?.writeText(redirectLocal); setCopied('local'); setTimeout(() => setCopied(''), 1200) }}>
                    {copied === 'local' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                  </button>
                </div>
              </Field>
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
                <li>Click <strong>Enable remote access</strong>. The first time, Tailscale may ask you to turn on <strong>Funnel</strong> for this device. Olisar shows the exact link to click, then press Enable again.</li>
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
                {tunnelDone && tunnelUrl && <span className="ok-pill"><Icon.check size={14} weight="Bold" /> Live at {tunnelUrl}</span>}
                {tunnelErr && <span className="err">{tunnelErr}</span>}
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

            <Field
              plain
              label="Add these redirect URLs in the Developer Portal"
              desc={<>Developer Portal → <strong>OAuth2</strong> → Redirects → Add. Add both, so login works locally and remotely.</>}
            >
              <div className="redirect-box">
                <span>{redirectLocal}</span>
                <button className="ghost" onClick={() => { navigator.clipboard?.writeText(redirectLocal); setCopied('local'); setTimeout(() => setCopied(''), 1200) }}>
                  {copied === 'local' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                </button>
              </div>
              {redirectTunnel && (
                <div className="redirect-box" style={{ marginTop: 8 }}>
                  <span>{redirectTunnel}</span>
                  <button className="ghost" onClick={() => { navigator.clipboard?.writeText(redirectTunnel); setCopied('tunnel'); setTimeout(() => setCopied(''), 1200) }}>
                    {copied === 'tunnel' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                  </button>
                </div>
              )}
            </Field>
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
            <Field label="Cloudflare account ID (optional)" desc="Turns on image generation. Leave blank to skip.">
              <Text value={cfAccount} onChange={setCfAccount} placeholder="cloudflare account id" mono />
            </Field>
            <Field label="Cloudflare API token (optional)" desc="Needs the Workers AI permission (Read is enough).">
              <Text value={cfToken} onChange={setCfToken} placeholder="cloudflare api token" mono />
            </Field>
            <Field label="UEX token (optional)" desc="Only for the Star Citizen extension.">
              <Text value={uex} onChange={setUex} placeholder="uex token" mono />
            </Field>
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
            <Field label="Tailscale auth key" desc={<>Gives your server a dashboard address without needing a domain. Create a reusable key at {A('https://login.tailscale.com/admin/settings/keys', 'Tailscale → Settings → Keys')}.</>}>
              <Text value={tunnelAuthKey} onChange={setTunnelAuthKey} placeholder="tskey-auth-…" mono />
            </Field>
            <Field label="Your Discord username (admin)" desc="Only you (and anyone you list) can sign in to the console. Your Discord username or numeric ID.">
              <Text value={adminUser} onChange={setAdminUser} placeholder="e.g. gcrft123" mono />
            </Field>

            {deploying && (
              <div className="callout note" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Installing Olisar on your VM. This takes a few minutes — keep this window open.</div>
              </div>
            )}
            {deployLog && <Cb file="install log" code={deployLog} />}
            {/* The costliest failure in setup: minutes in, with the log already on screen.
                The report carries both, so nobody has to copy a terminal's worth of text. */}
            {deployErr && (
              <div className="err-block">
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
          <div className="err-block">
            <div className="err">{err}</div>
            <FeedbackButton className="" prefill={{ category: 'Bug report', logs: true, message: reportBody('Finishing setup failed.', err) }}>
              Report a problem
            </FeedbackButton>
          </div>
        ) : <div className="err">{err}</div>)}

        <div className="wiz-foot">
          <button disabled={step === 0 || saving} onClick={() => { setErr(''); setSaveFailed(false); setStep((s) => Math.max(0, s - 1)) }}>
            Back
          </button>
          <span className="grow" />
          {step < last
            ? (cur === 'token'
                ? <div className="cta-reveal">
                    <div className="reveal-slot">
                      <button className="ghost reveal-btn" onClick={() => { setConnectMode(true); setDeployErr('') }}>Connect to existing server</button>
                    </div>
                    <button className="primary" onClick={next}>Continue</button>
                  </div>
                : <button className="primary" onClick={next}>Continue</button>)
            : mode === 'server'
              ? <button className="primary" disabled={deploying || (sharing && shareBusy)} onClick={deployServer}>{deploying ? 'Deploying…' : 'Deploy to server'}</button>
              : <button className="primary" disabled={saving} onClick={finish}>{saving ? 'Saving…' : 'Finish & start Olisar'}</button>}
        </div>
          </>
        )}
      </div>
    </div>
  )
}
