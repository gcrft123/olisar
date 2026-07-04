import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './icons'
import { SettingsModal } from './settings'
import { Field, Text } from './ui'

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
// hangs or fails (the fetch itself carries a timeout via api.serverPubkey).
export function usePubkey(enabled: boolean) {
  const [pubkey, setPubkey] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const retry = useCallback(() => {
    setLoading(true); setErr('')
    api.serverPubkey()
      .then((r: any) => setPubkey(r.public_key || ''))
      .catch((e: any) => setErr(e?.message || 'Couldn’t generate the SSH key.'))
      .finally(() => setLoading(false))
  }, [])
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

  const [step, setStep] = useState(0)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState<'' | 'local' | 'tunnel'>('')
  const [settingsOpen, setSettingsOpen] = useState(false)

  // Step 1 — bot token
  const [token, setToken] = useState(pf.discord_token || '')
  const [validating, setValidating] = useState(false)
  const [botName, setBotName] = useState<string | null>(null)

  // Step 2 — application
  const [clientId, setClientId] = useState(pf.discord_client_id || '')
  const [clientSecret, setClientSecret] = useState(pf.discord_client_secret || '')
  const [guildId, setGuildId] = useState(pf.target_guild_id || '')

  // Step 3 — hosting mode
  const [mode, setMode] = useState<Mode>(pf.tunnel_token ? 'tunnel' : 'local')
  const [tunnelNode, setTunnelNode] = useState('olisar')
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

  // The app's SSH key: always needed on the Deploy step (new VM); on the connect/reconnect
  // screen it's only a fallback (the key is already on a VM the app set up), fetched lazily
  // when the operator expands "Can't connect?".
  const pk = usePubkey((step === 3 && mode === 'server') || (connectMode && showKey))

  // Step 4 — keys
  const [gemini, setGemini] = useState(pf.gemini_api_key || '')
  const [cfAccount, setCfAccount] = useState(pf.cloudflare_account_id || '')
  const [cfToken, setCfToken] = useState(pf.cloudflare_api_token || '')
  const [uex, setUex] = useState(pf.uex_api_key || '')
  const [saving, setSaving] = useState(false)

  // The last step is "API keys" for local hosting, "Deploy" for server hosting.
  const steps = ['Bot token', 'Application', 'Access', mode === 'server' ? 'Deploy' : 'API keys']
  const last = steps.length - 1

  const redirectLocal = status.local_url.replace(/\/$/, '') + '/auth/callback'
  const redirectTunnel = tunnelUrl ? tunnelUrl.replace(/\/$/, '') + '/auth/callback' : ''

  async function validate() {
    setErr(''); setValidating(true); setBotName(null)
    try {
      const r = await api.validateSetupToken(token.trim())
      setBotName(r.username || 'your bot')
    } catch (e: any) {
      setErr(e?.message || 'token validation failed')
    } finally {
      setValidating(false)
    }
  }

  function next() {
    setErr('')
    if (step === 0 && !token.trim()) return setErr('Paste your bot token to continue.')
    if (step === 1 && !(clientId.trim() && clientSecret.trim()))
      return setErr('Client ID and client secret are both required.')
    if (step === 2 && mode === 'tunnel' && !tunnelDone)
      return setErr('Turn on remote access before continuing (or pick another option).')
    setStep((s) => Math.min(s + 1, last))
  }

  async function enableTunnel() {
    setTunnelErr(''); setProvisioning(true); setTunnelDone(false)
    try {
      const r = await api.enableTunnel({ auth_key: tunnelAuthKey.trim(), hostname: tunnelNode.trim() })
      setTunnelUrl(r.public_url || '')
      setTunnelDone(true)
    } catch (e: any) {
      setTunnelErr(e?.message || 'Couldn’t turn on remote access.')
    } finally {
      setProvisioning(false)
    }
  }

  async function finish() {
    setErr(''); setSaving(true)
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
      setSaving(false)
    }
  }

  // Server hosting: the app SSHes into the operator's VM, installs Docker + the config,
  // and starts the container. On success the app is in server mode (no local bot) and
  // flips to the remote control panel.
  async function deployServer() {
    setDeployErr('')
    if (!serverHost.trim()) return setDeployErr('Enter the VM’s public IP address.')
    if (!(gemini.trim() && tunnelAuthKey.trim()))
      return setDeployErr('A Gemini key and a Tailscale auth key are both required.')
    setDeploying(true); setDeployLog('')
    try {
      const r = await api.serverDeploy({ host: serverHost.trim(), user: serverUser.trim() || 'ubuntu', env: envFile })
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
      const r = await api.serverConnect({ host: serverHost.trim(), user: serverUser.trim() || 'ubuntu' })
      if (r?.ok) { onDone() }
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
    return L.join('\n')
  })()

  return (
    <div className="setup">
      <div className="box">
        <button className="ghost icon-btn sm box-gear" data-tip="Settings" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
          <Icon.settings size={16} />
        </button>
        {settingsOpen && (
          <SettingsModal
            sections={['appearance', 'bot', 'updates', 'desktop', 'feedback']}
            botSwitcherOnly
            onClose={() => setSettingsOpen(false)}
          />
        )}
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        {connectMode ? (
          <>
            <h1>Connect to an existing server</h1>
            <p className="step-sub">
              Point Olisar at a cloud VM that already runs it — it verifies over SSH and takes over start/stop + the dashboard, with no reinstall.
            </p>
            <div className="callout tip" style={{ marginBottom: 16 }}>
              <span className="ic"><Icon.info size={17} weight="Bold" /></span>
              <div className="callout-body">Reconfiguring an existing bot — its persona, memory, knowledge, and settings are kept.</div>
            </div>
            <Field label="VM public IP address" desc="The VM already running Olisar.">
              <Text value={serverHost} onChange={setServerHost} placeholder="e.g. 203.0.113.9" mono />
            </Field>
            <details className="disclosure" onToggle={(e) => setShowKey((e.currentTarget as HTMLDetailsElement).open)}>
              <summary>Can’t connect? Add this app’s SSH key to the VM</summary>
              <div className="desc" style={{ marginTop: 8 }}>
                Paste this into the VM’s <code>~/.ssh/authorized_keys</code> (or the provider’s SSH-keys box), then Connect. A VM this app already set up trusts it automatically — you only need this for a brand-new VM.
              </div>
              <PubkeyBox state={pk} />
              <Field label="SSH user" desc="The VM's login user — Ubuntu images use ubuntu.">
                <Text value={serverUser} onChange={setServerUser} placeholder="ubuntu" mono />
              </Field>
            </details>
            {deploying && (
              <div className="callout note" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Connecting to your VM over SSH…</div>
              </div>
            )}
            {deployErr && <div className="err">{deployErr}</div>}
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

        {step === 0 && (
          <>
            <Field
              label="Discord bot token"
              desc={<>In the {A('https://discord.com/developers/applications', 'Discord Developer Portal')}, open your application → <strong>Bot</strong> → Reset/Copy Token. Enable the <strong>Message Content</strong> and <strong>Server Members</strong> intents there too (and <strong>Presence Intent</strong> only if you want status/voice awareness).</>}
            >
              <Text value={token} onChange={(v) => { setToken(v); setBotName(null) }} placeholder="your bot token" mono />
            </Field>
            <div className="wiz-foot">
              <span className="grow">
                {botName && <span className="ok-pill"><Icon.check size={14} weight="Bold" /> Connected as {botName}</span>}
              </span>
              <button disabled={!token.trim() || validating} onClick={validate}>
                {validating ? 'Checking…' : 'Test token'}
              </button>
            </div>
          </>
        )}

        {step === 1 && (
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
              desc={<>Right-click your server in Discord (with Developer Mode on) → Copy Server ID. Olisar still works in every server it's invited to; this just sets its home for DMs.</>}
            >
              <Text value={guildId} onChange={setGuildId} placeholder="e.g. 1321947496179568680" mono />
            </Field>
          </>
        )}

        {step === 2 && (
          <>
            <div className="mode-grid">
              <div className={'mode-card' + (mode === 'local' ? ' sel' : '')} onClick={() => setMode('local')}>
                <b>Local unshared hosting</b>
                <p>Runs on this machine, reachable only from here. Simplest — nothing exposed.</p>
              </div>
              <div className={'mode-card' + (mode === 'tunnel' ? ' sel' : '')} onClick={() => setMode('tunnel')}>
                <b>Local shared hosting</b>
                <p>Runs on this machine, shared online over Tailscale so other admins can sign in. Free, no domain.</p>
              </div>
              <div className={'mode-card' + (mode === 'server' ? ' sel' : '')} onClick={() => setMode('server')}>
                <b>Server shared hosting</b>
                <p>Runs 24/7 on a free cloud server — this computer can be off. Best if you want it always online.</p>
              </div>
            </div>

            {mode === 'tunnel' && (
              <>
                <div className="tunnel-help">
                  <b>Free remote access via Tailscale — no domain needed</b>
                  <ol>
                    <li>Create a free {A('https://login.tailscale.com/start', 'Tailscale account')} (sign in with Google, GitHub, etc.).</li>
                    <li>Generate an auth key at {A('https://login.tailscale.com/admin/settings/keys', 'Settings → Keys → Generate auth key')} — turn on <strong>Reusable</strong>. Paste it below.</li>
                    <li>Click <strong>Enable remote access</strong>. The first time, Tailscale may ask you to turn on <strong>Funnel</strong> for this device — Olisar shows the exact link to click, then press it again.</li>
                  </ol>
                  <div style={{ marginTop: 8 }}>
                    Olisar then serves your dashboard at a stable <code>https://…ts.net</code> address; other admins just open it and sign in with Discord — they don't need Tailscale.
                  </div>
                </div>
                <Field
                  label="Tailscale auth key"
                  desc="Joins your tailnet. Stored locally; only ever passed to the Tailscale helper."
                >
                  <Text value={tunnelAuthKey} onChange={(v) => { setTunnelAuthKey(v); setTunnelDone(false) }} placeholder="tskey-auth-…" mono />
                </Field>
                <Field
                  label="Device name (optional)"
                  desc="This machine's name on your tailnet — becomes the first part of the URL."
                >
                  <Text value={tunnelNode} onChange={(v) => { setTunnelNode(v); setTunnelDone(false) }} placeholder="olisar" mono />
                </Field>
                <div className="wiz-foot">
                  <span className="grow">
                    {tunnelDone && tunnelUrl && <span className="ok-pill"><Icon.check size={14} weight="Bold" /> Live at {tunnelUrl}</span>}
                    {tunnelErr && <span className="err" style={{ margin: 0 }}>{tunnelErr}</span>}
                  </span>
                  <button disabled={!tunnelAuthKey.trim() || provisioning} onClick={enableTunnel}>
                    {provisioning ? 'Connecting…' : tunnelDone ? 'Reconnect' : 'Enable remote access'}
                  </button>
                </div>
              </>
            )}

            {mode === 'server' && (
              <div className="callout note" style={{ marginBottom: 4 }}>
                <span className="ic"><Icon.info size={17} weight="Bold" /></span>
                <div className="callout-body">
                  Olisar will run on a free cloud server, always on, even with this computer off.
                  You'll create the account and grant access once (guided on the next step); after
                  that it's copy-paste. Continue to get your ready-to-deploy package.
                </div>
              </div>
            )}

            {mode !== 'server' && (
              <Field
                label="Add this redirect URL in the Developer Portal"
                desc={<>Developer Portal → <strong>OAuth2</strong> → Redirects → Add. {mode === 'tunnel' ? 'Add both so login works locally and remotely.' : 'This loopback URL is what Discord redirects back to.'}</>}
              >
                <div className="redirect-box">
                  <span>{redirectLocal}</span>
                  <button className="ghost" onClick={() => { navigator.clipboard?.writeText(redirectLocal); setCopied('local'); setTimeout(() => setCopied(''), 1200) }}>
                    {copied === 'local' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                  </button>
                </div>
                {mode === 'tunnel' && redirectTunnel && (
                  <div className="redirect-box" style={{ marginTop: 8 }}>
                    <span>{redirectTunnel}</span>
                    <button className="ghost" onClick={() => { navigator.clipboard?.writeText(redirectTunnel); setCopied('tunnel'); setTimeout(() => setCopied(''), 1200) }}>
                      {copied === 'tunnel' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                    </button>
                  </div>
                )}
              </Field>
            )}
          </>
        )}

        {step === 3 && mode !== 'server' && (
          <>
            <Field
              label="Gemini API key"
              desc={<>Powers everything Olisar says. Create a free key in {A('https://aistudio.google.com/apikey', 'Google AI Studio')}. You can add this later in Settings, but the bot can't reply without it.</>}
            >
              <Text value={gemini} onChange={setGemini} placeholder="AIza…" mono />
            </Field>
            <Field label="Cloudflare account ID (optional)" desc="Enables image generation. Leave blank to skip.">
              <Text value={cfAccount} onChange={setCfAccount} placeholder="cloudflare account id" mono />
            </Field>
            <Field label="Cloudflare API token (optional)" desc="Workers AI permission (Read).">
              <Text value={cfToken} onChange={setCfToken} placeholder="cloudflare api token" mono />
            </Field>
            <Field label="UEX token (optional)" desc="Only for the Star Citizen extension.">
              <Text value={uex} onChange={setUex} placeholder="uex token" mono />
            </Field>
          </>
        )}

        {step === 3 && mode === 'server' && !connectMode && (
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
                  <li><strong>Menu → Compute → Instances → Create instance</strong>. Image <strong>Ubuntu 22.04</strong>, shape <strong>VM.Standard.A1.Flex</strong> (Ampere — Always Free). If you see <strong>"out of capacity"</strong>, switch Availability Domain / region and retry — free ARM frees up through the day.</li>
                  <li>Under <strong>Add SSH keys</strong>, choose <strong>Paste public keys</strong> and paste the key below. Leave networking on defaults. Create it.</li>
                  <li>Open the instance's details, copy its <strong>Public IP address</strong> into the field below, and press <strong>Deploy to server</strong>. Olisar SSHes in and sets everything up — no terminal needed.</li>
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

            <div className="field">
              <label>SSH public key — paste this when creating the VM</label>
              <div className="desc">The app connects with the matching private key, which never leaves this machine.</div>
              <PubkeyBox state={pk} />
            </div>

            <Field label="VM public IP address" desc="From the instance's details page.">
              <Text value={serverHost} onChange={setServerHost} placeholder="e.g. 203.0.113.9" mono />
            </Field>
            <Field label="Gemini API key" desc={<>Powers everything Olisar says. Free key from {A('https://aistudio.google.com/apikey', 'Google AI Studio')}.</>}>
              <Text value={gemini} onChange={setGemini} placeholder="AIza…" mono />
            </Field>
            <Field label="Tailscale auth key" desc={<>Gives your server its dashboard URL — no domain needed. Reusable key from {A('https://login.tailscale.com/admin/settings/keys', 'Tailscale → Settings → Keys')}.</>}>
              <Text value={tunnelAuthKey} onChange={setTunnelAuthKey} placeholder="tskey-auth-…" mono />
            </Field>
            <Field label="Your Discord username (admin)" desc="Only you (and anyone you list) can sign in to the console. Your Discord username or numeric ID.">
              <Text value={adminUser} onChange={setAdminUser} placeholder="e.g. gcrft123" mono />
            </Field>

            {deploying && (
              <div className="callout note" style={{ marginBottom: 4 }}>
                <span className="ic"><span className="spinner" /></span>
                <div className="callout-body">Installing Olisar on your VM — this takes a few minutes (Docker + image pull). Keep this window open.</div>
              </div>
            )}
            {deployLog && <Cb file="install log" code={deployLog} />}
            {deployErr && <div className="err">{deployErr}</div>}
          </>
        )}

        {err && <div className="err">{err}</div>}

        <div className="wiz-foot">
          <button disabled={step === 0 || saving} onClick={() => { setErr(''); setStep((s) => Math.max(0, s - 1)) }}>
            Back
          </button>
          <span className="grow" />
          {step < last
            ? (step === 0
                ? <div className="cta-reveal">
                    <button className="ghost reveal-btn" onClick={() => { setConnectMode(true); setDeployErr('') }}>Connect to existing server</button>
                    <button className="primary" onClick={next}>Continue</button>
                  </div>
                : <button className="primary" onClick={next}>Continue</button>)
            : mode === 'server'
              ? <button className="primary" disabled={deploying} onClick={deployServer}>{deploying ? 'Deploying…' : 'Deploy to server'}</button>
              : <button className="primary" disabled={saving} onClick={finish}>{saving ? 'Saving…' : 'Finish & start Olisar'}</button>}
        </div>
          </>
        )}
      </div>
    </div>
  )
}
