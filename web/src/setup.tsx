import { useState } from 'react'
import { api } from './api'
import { Icon } from './icons'
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
  prefill?: SetupPrefill
}

const STEPS = ['Bot token', 'Application', 'Access', 'API keys']

/** First-run wizard, shown full-screen when the backend reports the app is
 *  unconfigured. Collects the operator's Discord credentials + tunnel choice,
 *  shows the exact OAuth redirect URI to register, then saves and hands off to
 *  the normal Discord login. */
export function SetupWizard({ status, onDone }: { status: SetupStatus; onDone: () => void }) {
  // Pre-fill from `.env` when the backend supplied it (loopback + not configured).
  // Lets the operator skip retyping on every fresh install / data dir.
  const pf = status.prefill || {}

  const [step, setStep] = useState(0)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState<'' | 'local' | 'tunnel'>('')

  // Step 1 — bot token
  const [token, setToken] = useState(pf.discord_token || '')
  const [validating, setValidating] = useState(false)
  const [botName, setBotName] = useState<string | null>(null)

  // Step 2 — application
  const [clientId, setClientId] = useState(pf.discord_client_id || '')
  const [clientSecret, setClientSecret] = useState(pf.discord_client_secret || '')
  const [guildId, setGuildId] = useState(pf.target_guild_id || '')

  // Step 3 — access mode (remote = Tailscale Funnel)
  const [mode, setMode] = useState<'local' | 'tunnel'>(pf.tunnel_token ? 'tunnel' : 'local')
  const [tunnelNode, setTunnelNode] = useState('olisar')
  const [tunnelAuthKey, setTunnelAuthKey] = useState(pf.tunnel_token || '')
  const [provisioning, setProvisioning] = useState(false)
  const [tunnelDone, setTunnelDone] = useState(false)
  const [tunnelUrl, setTunnelUrl] = useState('')
  const [tunnelErr, setTunnelErr] = useState('')

  // Step 4 — keys
  const [gemini, setGemini] = useState(pf.gemini_api_key || '')
  const [cfAccount, setCfAccount] = useState(pf.cloudflare_account_id || '')
  const [cfToken, setCfToken] = useState(pf.cloudflare_api_token || '')
  const [uex, setUex] = useState(pf.uex_api_key || '')
  const [saving, setSaving] = useState(false)

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
      return setErr('Turn on remote access before continuing (or switch to local-only).')
    setStep((s) => Math.min(s + 1, STEPS.length - 1))
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
      // Tunnel config (if any) was already stored by createTunnel(); save just the rest.
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

  const A = (href: string, text: string) => (
    <a href={href} target="_blank" rel="noreferrer">{text}</a>
  )

  return (
    <div className="setup">
      <div className="box">
        <img className="brand-logo" src="/logo.png" alt="Olisar" />
        <h1>Set up Olisar</h1>
        <p className="step-sub">
          A one-time setup to connect Olisar to your Discord server. Everything stays on this machine.
        </p>
        <div className="steps">
          {STEPS.map((_, i) => <i key={i} className={i <= step ? 'on' : ''} />)}
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
                <b>This machine only</b>
                <p>You manage Olisar from here. Simplest, nothing to expose.</p>
              </div>
              <div className={'mode-card' + (mode === 'tunnel' ? ' sel' : '')} onClick={() => setMode('tunnel')}>
                <b>Remote access</b>
                <p>Other admins sign in over Tailscale. Free, no domain needed.</p>
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

            <Field
              label="Add this redirect URL in the Developer Portal"
              desc={<>Developer Portal → <strong>OAuth2</strong> → Redirects → Add. {mode === 'tunnel' ? 'Add both so login works locally and remotely.' : 'This loopback URL is what Discord redirects back to.'}</>}
            >
              <div className="redirect-box">
                <span>{redirectLocal}</span>
                <button className="ghost sm" onClick={() => { navigator.clipboard?.writeText(redirectLocal); setCopied('local'); setTimeout(() => setCopied(''), 1200) }}>
                  {copied === 'local' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                </button>
              </div>
              {mode === 'tunnel' && redirectTunnel && (
                <div className="redirect-box" style={{ marginTop: 8 }}>
                  <span>{redirectTunnel}</span>
                  <button className="ghost sm" onClick={() => { navigator.clipboard?.writeText(redirectTunnel); setCopied('tunnel'); setTimeout(() => setCopied(''), 1200) }}>
                    {copied === 'tunnel' ? <><Icon.check size={13} weight="Bold" /> Copied</> : 'Copy'}
                  </button>
                </div>
              )}
            </Field>
          </>
        )}

        {step === 3 && (
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

        {err && <div className="err">{err}</div>}

        <div className="wiz-foot">
          <button disabled={step === 0 || saving} onClick={() => { setErr(''); setStep((s) => Math.max(0, s - 1)) }}>
            Back
          </button>
          <span className="grow" />
          {step < STEPS.length - 1
            ? <button className="primary" onClick={next}>Continue</button>
            : <button className="primary" disabled={saving} onClick={finish}>{saving ? 'Saving…' : 'Finish & start Olisar'}</button>}
        </div>
      </div>
    </div>
  )
}
