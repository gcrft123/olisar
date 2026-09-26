// ── The wizard ──────────────────────────────────────────────────────────────────
// web/src/setup.tsx, ported to Preact + htm so it runs from one file. Same steps, checks,
// polls, errors and copy. The backend is web/mock/fixture.ts, with the same delays: any
// value that starts with "bad" takes that step's failure path, and a bot token containing
// "intents" makes the Bot step wait for the intents to come on.
;(function () {
  'use strict'
  const { html, render, useState, useEffect, useLayoutEffect, useRef, useCallback } = window.htmPreact
  const params = new URLSearchParams(location.search)
  // `?second` sets up a second bot, so the Deploy step offers the server another bot runs on.
  const SECOND = params.has('second')
  // `?end=server` opens on the server panel as a deploy leaves it; `?end=brain` on the final
  // screen, as a server that has been up for a while.
  const END = params.get('end')

  // ── Icons: Solar, as the console renders them ─────────────────────────────────
  const svg = (size, children, filled) => html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">${children}</svg>`
  const I = {
    check: (s = 14) => svg(s, html`<path fill-rule="evenodd" clip-rule="evenodd" fill="currentColor" d="M22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12ZM16.0303 8.96967C16.3232 9.26256 16.3232 9.73744 16.0303 10.0303L11.0303 15.0303C10.7374 15.3232 10.2626 15.3232 9.96967 15.0303L7.96967 13.0303C7.67678 12.7374 7.67678 12.2626 7.96967 11.9697C8.26256 11.6768 8.73744 11.6768 9.03033 11.9697L10.5 13.4393L12.7348 11.2045L14.9697 8.96967C15.2626 8.67678 15.7374 8.67678 16.0303 8.96967Z"/>`),
    warn: (s = 17) => svg(s, html`<path fill-rule="evenodd" clip-rule="evenodd" fill="currentColor" d="M5.31171 10.7615C8.23007 5.58716 9.68925 3 12 3C14.3107 3 15.7699 5.58716 18.6883 10.7615L19.0519 11.4063C21.4771 15.7061 22.6897 17.856 21.5937 19.428C20.4978 21 17.7864 21 12.3637 21H11.6363C6.21356 21 3.50217 21 2.40626 19.428C1.31034 17.856 2.52291 15.7061 4.94805 11.4063L5.31171 10.7615ZM12 7.25C12.4142 7.25 12.75 7.58579 12.75 8V13C12.75 13.4142 12.4142 13.75 12 13.75C11.5858 13.75 11.25 13.4142 11.25 13V8C11.25 7.58579 11.5858 7.25 12 7.25ZM12 17C12.5523 17 13 16.5523 13 16C13 15.4477 12.5523 15 12 15C11.4477 15 11 15.4477 11 16C11 16.5523 11.4477 17 12 17Z"/>`),
    info: (s = 17) => svg(s, html`<path fill-rule="evenodd" clip-rule="evenodd" fill="currentColor" d="M22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12ZM12 17.75C12.4142 17.75 12.75 17.4142 12.75 17V11C12.75 10.5858 12.4142 10.25 12 10.25C11.5858 10.25 11.25 10.5858 11.25 11V17C11.25 17.4142 11.5858 17.75 12 17.75ZM12 7C12.5523 7 13 7.44772 13 8C13 8.55228 12.5523 9 12 9C11.4477 9 11 8.55228 11 8C11 7.44772 11.4477 7 12 7Z"/>`),
    settings: (s = 16) => svg(s, html`<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.5"/><path stroke="currentColor" stroke-width="1.5" d="M13.7654 2.15224C13.3978 2 12.9319 2 12 2C11.0681 2 10.6022 2 10.2346 2.15224C9.74457 2.35523 9.35522 2.74458 9.15223 3.23463C9.05957 3.45834 9.0233 3.7185 9.00911 4.09799C8.98826 4.65568 8.70226 5.17189 8.21894 5.45093C7.73564 5.72996 7.14559 5.71954 6.65219 5.45876C6.31645 5.2813 6.07301 5.18262 5.83294 5.15102C5.30704 5.08178 4.77518 5.22429 4.35436 5.5472C4.03874 5.78938 3.80577 6.1929 3.33983 6.99993C2.87389 7.80697 2.64092 8.21048 2.58899 8.60491C2.51976 9.1308 2.66227 9.66266 2.98518 10.0835C3.13256 10.2756 3.3397 10.437 3.66119 10.639C4.1338 10.936 4.43789 11.4419 4.43786 12C4.43783 12.5581 4.13375 13.0639 3.66118 13.3608C3.33965 13.5629 3.13248 13.7244 2.98508 13.9165C2.66217 14.3373 2.51966 14.8691 2.5889 15.395C2.64082 15.7894 2.87379 16.193 3.33973 17C3.80568 17.807 4.03865 18.2106 4.35426 18.4527C4.77508 18.7756 5.30694 18.9181 5.83284 18.8489C6.07289 18.8173 6.31632 18.7186 6.65204 18.5412C7.14547 18.2804 7.73556 18.27 8.2189 18.549C8.70224 18.8281 8.98826 19.3443 9.00911 19.9021C9.02331 20.2815 9.05957 20.5417 9.15223 20.7654C9.35522 21.2554 9.74457 21.6448 10.2346 21.8478C10.6022 22 11.0681 22 12 22C12.9319 22 13.3978 22 13.7654 21.8478C14.2554 21.6448 14.6448 21.2554 14.8477 20.7654C14.9404 20.5417 14.9767 20.2815 14.9909 19.902C15.0117 19.3443 15.2977 18.8281 15.781 18.549C16.2643 18.2699 16.8544 18.2804 17.3479 18.5412C17.6836 18.7186 17.927 18.8172 18.167 18.8488C18.6929 18.9181 19.2248 18.7756 19.6456 18.4527C19.9612 18.2105 20.1942 17.807 20.6601 16.9999C21.1261 16.1929 21.3591 15.7894 21.411 15.395C21.4802 14.8691 21.3377 14.3372 21.0148 13.9164C20.8674 13.7243 20.6602 13.5628 20.3387 13.3608C19.8662 13.0639 19.5621 12.558 19.5621 11.9999C19.5621 11.4418 19.8662 10.9361 20.3387 10.6392C20.6603 10.4371 20.8675 10.2757 21.0149 10.0835C21.3378 9.66273 21.4803 9.13087 21.4111 8.60497C21.3592 8.21055 21.1262 7.80703 20.6602 7C20.1943 6.19297 19.9613 5.78945 19.6457 5.54727C19.2249 5.22436 18.693 5.08185 18.1671 5.15109C17.9271 5.18269 17.6837 5.28136 17.3479 5.4588C16.8545 5.71959 16.2644 5.73002 15.7811 5.45096C15.2977 5.17191 15.0117 4.65566 14.9909 4.09794C14.9767 3.71848 14.9404 3.45833 14.8477 3.23463C14.6448 2.74458 14.2554 2.35523 13.7654 2.15224Z"/>`),
    copy: (s = 15) => svg(s, html`<path stroke="currentColor" stroke-width="1.5" d="M6 11C6 8.17157 6 6.75736 6.87868 5.87868C7.75736 5 9.17157 5 12 5H15C17.8284 5 19.2426 5 20.1213 5.87868C21 6.75736 21 8.17157 21 11V16C21 18.8284 21 20.2426 20.1213 21.1213C19.2426 22 17.8284 22 15 22H12C9.17157 22 7.75736 22 6.87868 21.1213C6 20.2426 6 18.8284 6 16V11Z"/><path stroke="currentColor" stroke-width="1.5" d="M6 19C4.34315 19 3 17.6569 3 16V10C3 6.22876 3 4.34315 4.17157 3.17157C5.34315 2 7.22876 2 11 2H15C16.6569 2 18 3.34315 18 5"/>`),
    chevron: (s = 14) => svg(s, html`<path d="M9 5L15 12L9 19" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`),
    doc: (s = 18) => svg(s, html`<path stroke="currentColor" stroke-width="1.5" d="M3 10C3 6.22876 3 4.34315 4.17157 3.17157C5.34315 2 7.22876 2 11 2H13C16.7712 2 18.6569 2 19.8284 3.17157C21 4.34315 21 6.22876 21 10V14C21 17.7712 21 19.6569 19.8284 20.8284C18.6569 22 16.7712 22 13 22H11C7.22876 22 5.34315 22 4.17157 20.8284C3 19.6569 3 17.7712 3 14V10Z"/><path d="M8 12H16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M8 8H16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M8 16H13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`),
    back: (s = 18) => svg(s, html`<path d="M15 5L9 12L15 19" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`),
    book: (s = 16) => svg(s, html`<path stroke="currentColor" stroke-width="1.5" d="M4 8C4 5.17157 4 3.75736 4.87868 2.87868C5.75736 2 7.17157 2 10 2H14C16.8284 2 18.2426 2 19.1213 2.87868C20 3.75736 20 5.17157 20 8V16C20 18.8284 20 20.2426 19.1213 21.1213C18.2426 22 16.8284 22 14 22H10C7.17157 22 5.75736 22 4.87868 21.1213C4 20.2426 4 18.8284 4 16V8Z"/><path stroke="currentColor" stroke-width="1.5" d="M19.8978 16H7.89778C6.96781 16 6.50282 16 6.12132 16.1022C5.08604 16.3796 4.2774 17.1883 4 18.2235"/><path d="M8 7H16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M8 10.5H13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`),
    close: (s = 16) => svg(s, html`<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>`),
    login: (s = 18) => svg(s, html`<path fill-rule="evenodd" clip-rule="evenodd" fill="currentColor" d="M3.5 9.56757V14.4324C3.5 16.7258 3.5 17.8724 4.22162 18.5849C4.87718 19.2321 5.89572 19.2913 7.81827 19.2968C7.81303 19.262 7.80803 19.2271 7.80324 19.192C7.68837 18.3484 7.68839 17.2759 7.68841 15.9453L7.68841 15.8919C7.68841 15.4889 8.01933 15.1622 8.42754 15.1622C8.83575 15.1622 9.16667 15.4889 9.16667 15.8919C9.16667 17.2885 9.16824 18.2626 9.26832 18.9975C9.36554 19.7114 9.54337 20.0895 9.81613 20.3588C10.0889 20.6281 10.4718 20.8037 11.195 20.8996C11.9394 20.9985 12.926 21 14.3406 21H15.3261C16.7407 21 17.7273 20.9985 18.4717 20.8996C19.1948 20.8037 19.5778 20.6281 19.8505 20.3588C20.1233 20.0895 20.3011 19.7114 20.3983 18.9975C20.4984 18.2626 20.5 17.2885 20.5 15.8919V8.10811C20.5 6.71149 20.4984 5.73743 20.3983 5.0025C20.3011 4.28855 20.1233 3.91048 19.8505 3.6412C19.5778 3.37192 19.1948 3.19635 18.4717 3.10036C17.7273 3.00155 16.7407 3 15.3261 3H14.3406C12.926 3 11.9394 3.00155 11.195 3.10036C10.4718 3.19635 10.0889 3.37192 9.81613 3.6412C9.54337 3.91048 9.36554 4.28855 9.26832 5.0025C9.16824 5.73743 9.16667 6.71149 9.16667 8.10811C9.16667 8.51113 8.83575 8.83784 8.42754 8.83784C8.01933 8.83784 7.68841 8.51113 7.68841 8.10811L7.68841 8.05472C7.68839 6.72409 7.68837 5.65156 7.80324 4.80803C7.80803 4.77288 7.81303 4.73795 7.81827 4.70325C5.89572 4.70867 4.87718 4.76792 4.22162 5.41515C3.5 6.12759 3.5 7.27425 3.5 9.56757ZM13.385 14.9484L15.8487 12.516C16.1374 12.231 16.1374 11.769 15.8487 11.484L13.385 9.05157C13.0963 8.76659 12.6283 8.76659 12.3397 9.05157C12.051 9.33655 12.051 9.79859 12.3397 10.0836L13.5417 11.2703H6.45652C6.04831 11.2703 5.71739 11.597 5.71739 12C5.71739 12.403 6.04831 12.7297 6.45652 12.7297H13.5417L12.3397 13.9164C12.051 14.2014 12.051 14.6635 12.3397 14.9484C12.6283 15.2334 13.0963 15.2334 13.385 14.9484Z"/>`),
  }
  const DiscordLogo = (size = 18) => html`<svg width=${size} height=${size * 0.758} viewBox="0 0 127.14 96.36" fill="currentColor" aria-hidden="true" focusable="false"><path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z"/></svg>`

  // ── The backend: web/mock/fixture.ts ──────────────────────────────────────────
  // Sign-in redirects back to whichever address the browser used. Opened from disk there
  // isn't one, so this stands in the desktop app's own.
  const ORIGIN = /^https?:$/.test(location.protocol) ? location.origin : 'http://localhost:8723'
  const MOCK_APP_ID = '1100000000000000001'
  // The bot's Discord name and avatar, for the final screen's centre (the avatar is the mock's).
  const BOT_NAME = 'Olisar'
  const MOCK_PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHq7mZ0x3cN8vWkq2d1p5sQyR4tLb9uFjE6aGhYcTzUo olisar-app'
  const MOCK_INSTALL_LOG = [
    '==> Checking the VM', 'Ubuntu 22.04.4 LTS (aarch64), 23 GB free',
    '==> Installing Docker', 'docker 27.3.1 installed',
    '==> Writing the config to ~/olisar/.env',
    '==> Pulling ghcr.io/gcrft123/olisar:2.0.0-beta.1',
    'Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: temporary failure in name resolution',
  ].join('\n')
  const WAIT = {}
  const waited = (what, ms) => { WAIT[what] ??= Date.now(); return Date.now() - WAIT[what] >= ms }
  const TUNNEL = { url: 'https://olisar.tail4f2a.ts.net' }
  function mockApp(token, polled) {
    const intentsOn = !token.includes('intents') || (polled && waited('intents', 8000))
    const redirectsIn = polled && intentsOn && waited('redirects', 5000)
    return {
      app: {
        id: MOCK_APP_ID, username: 'Olisar', avatar: '', bot_public: true, code_grant: false,
        intents_missing: intentsOn ? [] : ['message_content', 'members'],
        redirect_uris: redirectsIn ? [`${ORIGIN}/auth/callback`, `${TUNNEL.url}/auth/callback`] : [],
        invite_url: `https://discord.com/oauth2/authorize?client_id=${MOCK_APP_ID}&scope=bot+applications.commands&permissions=274878024768`,
      },
      joined: redirectsIn && waited('invite', 4000),
    }
  }
  const later = (ms, v) => new Promise((res) => setTimeout(() => res(typeof v === 'function' ? v() : v), ms))
  const refuse = (ms, status, message) => new Promise((_, rej) => setTimeout(() => rej(Object.assign(new Error(message), { status })), ms))
  const bad = (v) => typeof v === 'string' && v.trim().toLowerCase().startsWith('bad')
  const api = {
    setupBot: (t) => bad(t) ? refuse(800, 400, 'Discord rejected that bot token') : later(800, () => mockApp(t, false).app),
    setupDiscordStatus: (t) => later(300, () => {
      const { app, joined } = mockApp(t, true)
      return { ...app, guilds: joined ? [{ id: '1321947496179568680', name: 'Red Nebula Industries', icon: '' }] : [] }
    }),
    checkSetupSecret: (id, s) => later(500, { ok: !bad(s) }),
    checkSetupGemini: (k) => later(500, { ok: !bad(k) }),
    saveSetupKeys: () => later(400, { ok: true }),
    saveSetup: () => later(900, { ok: true }),
    enableTunnel: ({ auth_key, hostname }) => bad(auth_key)
      ? refuse(2200, 400, 'Funnel isn’t turned on for this tailnet. Turn it on at https://login.tailscale.com/f/funnel?node=olisar, then press Enable again.')
      : later(2200, () => { TUNNEL.url = `https://${(hostname || 'olisar').trim()}.tail4f2a.ts.net`; return { ok: true, public_url: TUNNEL.url } }),
    disableTunnel: () => later(200, { ok: true }),
    serverPubkey: () => later(600, { public_key: MOCK_PUBKEY }),
    shareServer: () => later(1400, { ok: true, host: '203.0.113.9', user: 'ubuntu', tailscale_auth: 'tskey-auth-mock', admin_allowlist: 'gcrft123' }),
    serverDeploy: ({ host }) => later(4500, () => bad(host)
      ? { ok: false, error: 'The install stopped: the VM couldn’t download the Olisar image.', log: MOCK_INSTALL_LOG }
      : { ok: true }),
    serverConnect: ({ host, app_dir }) => later(1800, () => bad(host)
      ? { ok: false, error: `Couldn't reach the VM: connection to ${host}:22 timed out` }
      : String(host).trim().endsWith('.9') && !app_dir
        ? { ok: false, choose: [{ dir: 'olisar', name: 'Support bot' }, { dir: 'olisar-e5f6a7b8', name: 'Staging bot' }] }
        : { ok: true }),
  }
  // bots.tsx: the bot being set up, and the servers other bots already run on.
  const thisBot = SECOND ? { id: 'e5f6a7b8', name: 'Staging bot' } : { id: 'default', name: 'Olisar' }
  const shared = SECOND ? [{ from: { id: 'a1b2c3d4', name: 'Support bot' }, host: '203.0.113.9', names: ['Support bot'] }] : []
  const deviceNameFor = (name) => (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'olisar'
  const serverLabel = (s) => `${s.names.length === 1 ? `${s.names[0]}’s server` : `${s.names.slice(0, -1).join(', ')} and ${s.names[s.names.length - 1]}’s server`} (${s.host})`
  const INTENT_NAMES = { message_content: 'Message Content Intent', members: 'Server Members Intent', presences: 'Presence Intent' }
  const intentList = (names) => names.map((n) => INTENT_NAMES[n] || n).join(' and ')
  const reportBody = (what, error, ask = 'What I was doing:') => [what, ...(error ? ['', 'Error:', error] : []), '', ask, ''].join('\n')
  const logTail = (text, lines = 40) => { const all = (text || '').trimEnd().split('\n'); return all.length > lines ? ['…', ...all.slice(-lines)].join('\n') : all.join('\n') }

  function copyText(text) {
    try { navigator.clipboard.writeText(text).catch(() => fallbackCopy(text)) } catch { fallbackCopy(text) }
  }
  function fallbackCopy(text) {
    const t = document.createElement('textarea')
    t.value = text; t.style.cssText = 'position:fixed;opacity:0'
    document.body.append(t); t.select()
    try { document.execCommand('copy') } catch { /* nothing to do */ }
    t.remove()
  }

  // ui.tsx usePoll: runs while active and the tab is visible, first poll straight away.
  function usePoll(load, everyMs, active) {
    const latest = useRef(load)
    latest.current = load
    useEffect(() => {
      if (!active) return
      let timer
      const tick = () => { try { const r = latest.current(); r?.catch?.(() => {}) } catch { /* next tick */ } timer = setTimeout(tick, everyMs) }
      const start = () => { if (timer === undefined) tick() }
      const stop = () => { clearTimeout(timer); timer = undefined }
      const onVisible = () => (document.hidden ? stop() : start())
      if (!document.hidden) start()
      document.addEventListener('visibilitychange', onVisible)
      return () => { stop(); document.removeEventListener('visibilitychange', onVisible) }
    }, [active, everyMs])
  }

  // ── Pieces of setup.tsx ────────────────────────────────────────────────────────
  let uidSeq = 0
  const useUid = () => useRef(`f${++uidSeq}`).current

  function Field({ id, label, desc, plain, children }) {
    const descId = desc ? `${id}-d` : undefined
    return html`
      <div class="field" role=${plain ? 'group' : undefined} aria-labelledby=${plain ? `${id}-l` : undefined}>
        ${plain ? html`<div class="flabel" id=${`${id}-l`}>${label}</div>` : html`<label id=${`${id}-l`} for=${id}>${label}</label>`}
        ${desc && html`<div class="desc" id=${descId}>${desc}</div>`}
        ${children}
      </div>`
  }
  const Text = ({ id, value, onChange, placeholder, mono, desc, invalid }) => html`
    <input type="text" id=${id} class=${mono ? 'mono' : ''} value=${value} placeholder=${placeholder}
      aria-describedby=${desc ? `${id}-d` : undefined} aria-invalid=${invalid ? 'true' : undefined}
      autocomplete="off" spellcheck=${false}
      onInput=${(e) => onChange(e.currentTarget.value)} />`
  const Select = ({ id, value, onChange, options }) => html`
    <select id=${id} value=${value} onChange=${(e) => onChange(e.currentTarget.value)}>
      ${options.map((o) => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
    </select>`

  function Segmented({ value, onChange, options, ariaLabel, className }) {
    const group = useRef(null)
    const onKey = (e) => {
      const step = /^Arrow(Right|Down)$/.test(e.key) ? 1 : /^Arrow(Left|Up)$/.test(e.key) ? -1 : 0
      if (!step) return
      e.preventDefault()
      const i = options.findIndex((o) => o.value === value)
      const next = options[(Math.max(0, i) + step + options.length) % options.length]
      onChange(next.value)
      setTimeout(() => group.current?.querySelector(`[data-v="${next.value}"]`)?.focus(), 0)
    }
    return html`
      <div ref=${group} class=${className} role="radiogroup" aria-label=${ariaLabel} onKeyDown=${onKey}>
        ${options.map((o) => html`<button key=${o.value} data-v=${o.value} role="radio" aria-checked=${o.value === value}
          tabindex=${o.value === value ? 0 : -1} class=${o.value === value ? 'on' : ''} onClick=${() => onChange(o.value)}>${o.label}</button>`)}
      </div>`
  }

  function Cb({ file, code }) {
    const [done, setDone] = useState(false)
    return html`
      <div class="codeblock">
        <div class="head">
          <span class="file">${file}</span>
          <button class="cb-copy" aria-label="Copy" data-tip=${done ? 'Copied' : 'Copy'}
            onClick=${() => { copyText(code); setDone(true); setTimeout(() => setDone(false), 1400) }}>
            <span class=${'copyglyph' + (done ? ' on' : '')} style="width:15px;height:15px">${I.copy(15)}${I.check(15)}</span>
          </button>
        </div>
        <pre><code>${code}</code></pre>
      </div>`
  }

  function usePubkey(enabled) {
    const [pubkey, setPubkey] = useState('')
    const [loading, setLoading] = useState(false)
    const [err, setErr] = useState('')
    const retry = useCallback(() => {
      setLoading(true); setErr('')
      api.serverPubkey().then((r) => setPubkey(r.public_key || ''))
        .catch((e) => setErr(e?.message || 'Couldn’t generate the SSH key.'))
        .finally(() => setLoading(false))
    }, [])
    useEffect(() => { if (enabled && !pubkey && !loading && !err) retry() }, [enabled, pubkey, loading, err, retry])
    return { pubkey, loading, err, retry }
  }
  function PubkeyBox({ state }) {
    if (state.err) return html`<div class="pubkey-err"><span class="err">${state.err}</span><button class="ghost" onClick=${state.retry}>Retry</button></div>`
    return html`<${Cb} file="app SSH public key" code=${state.pubkey && !state.loading ? state.pubkey : 'generating…'} />`
  }

  const MODES = [
    { id: 'local', title: 'Local unshared hosting', blurb: 'Runs on this machine, reachable only from here.' },
    { id: 'tunnel', title: 'Local shared hosting', blurb: 'Runs on this machine, shared online over Tailscale so other admins can sign in. Free, no domain.' },
    { id: 'server', title: 'Server shared hosting', blurb: 'Runs 24/7 on a free cloud server, even with this computer off.' },
  ]
  function ModeChoice({ mode, onPick }) {
    const group = useRef(null)
    const onKey = (e) => {
      const step = /^Arrow(Right|Down)$/.test(e.key) ? 1 : /^Arrow(Left|Up)$/.test(e.key) ? -1 : 0
      if (!step) return
      e.preventDefault()
      const i = MODES.findIndex((m) => m.id === mode)
      const next = MODES[(i + step + MODES.length) % MODES.length]
      onPick(next.id)
      group.current?.querySelector(`#mode-${next.id}`)?.focus()
    }
    return html`
      <div class="mode-grid" ref=${group} role="radiogroup" aria-label="Where Olisar runs" onKeyDown=${onKey}>
        ${MODES.map((m) => html`
          <div key=${m.id} id=${`mode-${m.id}`} class=${'mode-card' + (mode === m.id ? ' sel' : '')}
            role="radio" aria-checked=${mode === m.id} tabindex=${mode === m.id ? 0 : -1}
            onClick=${() => onPick(m.id)}
            onKeyDown=${(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); onPick(m.id) } }}>
            <b>${m.title}</b>
            <p>${m.blurb}</p>
          </div>`)}
      </div>`
  }

  function stepsFor(mode) {
    return ['where', 'bot', ...(mode === 'tunnel' ? ['remote'] : []), 'signin', 'server', 'keys', ...(mode === 'server' ? ['deploy'] : [])]
  }
  const BAR_SLOTS = Math.max(...MODES.map((m) => stepsFor(m.id).length))
  const PORTAL = 'https://discord.com/developers/applications'

  function useLiveCheck(value, run, key = '') {
    const [st, setSt] = useState({ state: 'idle', error: '' })
    const seq = useRef(0)
    const runRef = useRef(run)
    runRef.current = run
    const start = useCallback((v) => {
      const n = ++seq.current
      if (!v) { setSt({ state: 'idle', error: '' }); return }
      setSt({ state: 'checking', error: '' })
      runRef.current(v)
        .then((r) => { if (n === seq.current) setSt({ state: r.ok ? 'ok' : 'bad', result: r.result, error: '' }) })
        .catch((e) => {
          if (n !== seq.current) return
          const refused = e?.status >= 400 && e?.status < 500
          setSt({ state: refused ? 'bad' : 'error', error: e?.message || 'Couldn’t check that.' })
        })
    }, [])
    const v = value.trim()
    useEffect(() => {
      seq.current++
      setSt({ state: v ? 'checking' : 'idle', error: '' })
      if (!v) return
      const t = setTimeout(() => start(v), 450)
      return () => clearTimeout(t)
    }, [v, key, start])
    return { ...st, recheck: () => start(v) }
  }

  const ArrivingLine = ({ id, children }) => html`<span key=${id} class="check-line-in wiz-appear">${children}</span>`
  function CheckLine({ check, ok, bad: badText }) {
    if (check.state === 'idle') return null
    const told = check.state === 'checking' || check.state === 'ok'
    return html`
      <div class=${'check-line' + (check.state === 'ok' ? ' ok' : told ? '' : ' err')} role=${told ? 'status' : 'alert'}>
        <${ArrivingLine} id=${check.state}>
          ${check.state === 'checking' ? html`<span class="spinner"></span> Checking…`
            : check.state === 'ok' ? ok
            : check.state === 'bad' ? badText
            : html`${check.error} <button class="linklike" onClick=${check.recheck}>Try again</button>`}
        <//>
      </div>`
  }

  function CopyText({ text, label = 'Copy' }) {
    const [done, setDone] = useState(false)
    return html`<button class="ghost" onClick=${() => { copyText(text); setDone(true); setTimeout(() => setDone(false), 1200) }}>
      ${done ? html`${I.check(13)} Copied` : label}</button>`
  }
  const RedirectRow = ({ url, added }) => html`
    <div class="redirect-box">
      <span>${url}</span>
      ${added ? html`<span class="ok-pill wiz-pop">${I.check(14)} Added</span>` : html`<${CopyText} text=${url} />`}
    </div>`
  const Linkified = ({ text }) => text.split(/(https?:\/\/\S+)/g).map((part) => {
    if (!/^https?:\/\//.test(part)) return part
    const url = part.replace(/[.,;:]+$/, '')
    return html`<a href=${url} target="_blank" rel="noreferrer">${url}</a>${part.slice(url.length)}`
  })
  const A = (href, text) => html`<a href=${href} target="_blank" rel="noreferrer">${text}</a>`

  // settings.tsx FeedbackButton: opens Feedback in Settings, filled in.
  const FeedbackButton = ({ className, prefill, openSettings, children }) => html`
    <button type="button" class=${className ?? 'ghost'} onClick=${() => openSettings('feedback', prefill)}>${children}</button>`

  // ── The form, told what the wizard is doing ────────────────────────────────────
  const form = () => window.__form

  // A wrong value shakes its field, the way a refused password does, and the form shivers
  // once: the counterpart of the pulse a confirmation sends through it. Under reduced motion
  // the field stays still and its fill flashes the danger colour instead.
  const SHAKE = [0, -7, 6, -4.5, 3, -1.5, 0].map((x) => ({ transform: `translateX(${x}px)` }))
  function shake(id) {
    const el = document.getElementById(id)
    if (!el) return
    form()?.reject()
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.getAnimations().forEach((a) => a.cancel())
    el.animate(still
      ? [{ backgroundColor: 'rgb(46, 26, 29)' }, { backgroundColor: 'rgb(15, 15, 18)' }]
      : SHAKE, { duration: still ? 700 : 420, easing: still ? 'ease-out' : 'linear' })
  }
  function usePulseOn(cond, amount) {
    const was = useRef(cond)
    useEffect(() => {
      if (cond && !was.current) form()?.pulse(amount)
      was.current = cond
    }, [cond])
  }

  // ── The server panel, after a deploy or a connect ──────────────────────────────
  // What server.tsx shows once Olisar runs on a VM, fed by the mock: status checks first, then
  // the container comes up healthy, Discord lists the console's sign-in address six seconds
  // in, and an update is waiting. Every state the real panel can report is here, and each has
  // its own form: the orb is the server's state, quietly. Running and healthy with the address
  // listed, the panel gives way to the final screen (the App decides; brain.js moves it).
  const PHASES = {
    checking: { label: 'Checking…', tone: 'info', orb: { shape: 8, v: 0.92, energy: 0.3, mood: { dim: 0.45 } } },
    starting: { label: 'Starting…', tone: 'info', orb: { shape: 8, v: 1, energy: 0.8, mood: { dim: 0.85 }, regather: true } },
    running: { label: 'Running', tone: 'success', orb: { shape: 8, v: 1, energy: 0, mood: {}, pulse: 0.8 } },
    updating: { label: 'Updating…', tone: 'info', orb: { shape: 9, energy: 0.5, mood: { dim: 0.95 } } },
    unhealthy: { label: 'Unhealthy', tone: 'error', orb: { shape: 8, v: 1, energy: 1.2, mood: { tremor: 0.3, dim: 0.9 } } },
    unreachable: { label: 'Unreachable', tone: 'error', orb: { shape: 8, v: 1, energy: 1.2, mood: { tremor: 0.3, dim: 0.9 } } },
    stopping: { label: 'Stopping…', tone: 'info', orb: { shape: 8, v: 0.8, energy: 0.6, mood: { dim: 0.75 } } },
    stopped: { label: 'Stopped', tone: 'warning', orb: { shape: 8, v: 0.7, energy: -0.8, mood: { dim: 0.5 } } },
    reconnecting: { label: 'Reconnecting…', tone: 'info', orb: { shape: 7, v: 0.7, energy: 0.7, mood: { dim: 0.85 }, regather: true } },
  }
  const PREVIEW = ['checking', 'starting', 'running', 'updating', 'unhealthy', 'unreachable', 'stopped']
  function upFor(ms) {
    const m = Math.floor(ms / 60000)
    if (m < 1) return 'Under a minute'
    if (m < 60) return `${m} minute${m === 1 ? '' : 's'}`
    const h = Math.floor(m / 60)
    return `${h} hour${h === 1 ? '' : 's'}`
  }
  // Runs once setup has finished to a server (`active`). `settled` starts it running, listed,
  // and up for a few hours: the final screen straight away.
  function useServer(active, settled) {
    const [phase, setPhase] = useState(settled ? 'running' : 'checking')
    const [since, setSince] = useState(() => Date.now() - (settled ? 3 * 3600000 + 720000 : 0))
    const [listed, setListed] = useState(!!settled)
    const [version, setVersion] = useState('2.0.beta-1')      // displayVersion('2.0.0-beta.1')
    const [available, setAvailable] = useState('2.0.beta-2')
    const [, tick] = useState(0)
    const timers = useRef([])
    const clear = () => { timers.current.forEach(clearTimeout); timers.current = [] }
    const later = (ms, fn) => { timers.current.push(setTimeout(fn, ms)) }
    useEffect(() => {
      if (!active) return
      if (!settled) {
        later(900, () => setPhase('starting'))
        later(3000, () => { setPhase('running'); setSince(Date.now()) })
        later(6000, () => setListed(true))
      }
      const c = setInterval(() => tick((n) => n + 1), 15000)
      return () => { clear(); clearInterval(c) }
    }, [active])
    const go = (ms, during, then, after) => {
      clear()
      setPhase(during)
      later(ms, () => { setPhase(then); if (then === 'running') setSince(Date.now()); after?.() })
    }
    // Each state's form, and back to calm when the panel goes.
    useEffect(() => {
      if (!active) return
      const f = form(); if (!f) return
      const o = PHASES[phase].orb
      f.set(o.shape, o.v)
      f.energy(o.energy)
      f.mood(o.mood)
      if (o.regather) f.regather()
      if (o.pulse) f.pulse(o.pulse)
    }, [phase, active])
    useEffect(() => () => { const f = form(); if (f) { f.mood({}); f.energy(0) } }, [])
    const busy = !['running', 'stopped', 'unhealthy', 'unreachable'].includes(phase)
    return {
      phase, listed, version, available, busy, ...PHASES[phase],
      up: phase === 'running' || phase === 'unhealthy' ? upFor(Date.now() - since)
        : phase === 'stopped' ? 'Not running' : phase === 'unreachable' ? 'Unknown' : '…',
      stop: () => go(1200, 'stopping', 'stopped'),
      start: () => go(1600, 'starting', 'running'),
      reconnect: () => go(1800, 'reconnecting', 'running'),
      update: () => go(5200, 'updating', 'running', () => { setVersion(available); setAvailable(null) }),
      // Prototype only: hold one state to look at it, or take the redirect away again.
      preview: (p) => { clear(); setPhase(p); if (p === 'running') setSince(Date.now()) },
      toggleListed: () => setListed((v) => !v),
    }
  }

  // The buttons close the gap when something above them goes (the redirect, a hint), on a
  // transform rather than a height tween.
  function useGapClose(ref) {
    const last = useRef(null)
    useLayoutEffect(() => {
      const el = ref.current
      if (!el) return
      const top = el.offsetTop, was = last.current
      last.current = top
      if (was == null || Math.abs(was - top) < 1 || window.__brain?.target === 1) return
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
      el.animate([{ transform: `translateY(${was - top}px)` }, { transform: 'none' }], { duration: 300, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1)' })
    })
  }

  // The status rows: the facts an operator checks, ruled like a settings section. What the
  // final screen keeps (the title, the badge, the two buttons) is marked data-morph, and
  // what it lets go of is data-fade.
  function ServerPanel({ srv, host, user, url, appId }) {
    const redirect = `${url}/auth/callback`
    const oauth = `${PORTAL}/${appId}/oauth2`
    const running = srv.phase === 'running' || srv.phase === 'unhealthy'
    // The redirect is only asked for until Discord lists it: it turns to Added, holds long
    // enough to be seen, and goes.
    const [ask, setAsk] = useState(!srv.listed)
    const [leaving, setLeaving] = useState(false)
    useEffect(() => {
      if (!srv.listed) { setAsk(true); setLeaving(false); return }
      if (!ask) return
      let off = () => {}, b = 0
      const gone = () => { setAsk(false); setLeaving(false) }
      // If the final screen is taking over, the field goes with the rest of the panel and is
      // dropped once the panel is out of sight, so nothing under it jumps mid-change.
      const a = setTimeout(() => {
        if (window.__brain.target === 1) { off = window.__brain.whenSettled((p) => { if (p === 1) gone() }); return }
        setLeaving(true)
        b = setTimeout(gone, 250)
      }, 1350)
      return () => { clearTimeout(a); clearTimeout(b); off() }
    }, [srv.listed])
    const actions = useRef(null)
    useGapClose(actions)
    const power = running
      ? html`<button class="caution" data-morph="power" onClick=${srv.stop}>Stop server</button>`
      : srv.phase === 'stopped' || srv.phase === 'unreachable'
        ? html`<button data-morph="power" disabled=${srv.phase === 'unreachable'} onClick=${srv.start}>Start server</button>`
        : html`<button data-morph="power" disabled>Working…</button>`
    return html`
      <div class="srv srv-v1">
        <div class="srv-head">
          <h1 data-morph="title" tabindex="-1">Your Olisar server</h1>
          <span class=${'badge ' + srv.tone} role="status" data-morph="badge">${srv.label}</span>
        </div>
        <p class="step-sub" data-fade>Olisar runs on your cloud VM, always on.</p>
        <dl class="srv-rows" data-fade>
          <div><dt>Console</dt><dd><a class="mono" href=${url} target="_blank" rel="noreferrer">${url.replace(/^https:\/\//, '')}</a><${CopyText} text=${url} /></dd></div>
          <div><dt>Server</dt><dd class="mono">${user}@${host}</dd></div>
          <div><dt>Version</dt><dd><span class="mono">${srv.phase === 'updating' && srv.available ? `${srv.version} → ${srv.available}` : srv.version}</span>
            ${srv.available && (srv.phase === 'running' || srv.phase === 'updating') && html`
              <button class="ghost" disabled=${srv.phase === 'updating'} onClick=${srv.update}>${srv.phase === 'updating' ? 'Updating…' : `Update to v${srv.available}`}</button>`}</dd></div>
          <div><dt>Uptime</dt><dd>${srv.up}</dd></div>
        </dl>
        ${srv.phase === 'updating' && html`<p class="srv-hint wiz-appear" data-fade>Updating the VM to match this app. If the new version doesn’t come up, the previous one is restored automatically. This can take a few minutes…</p>`}
        ${srv.phase === 'unhealthy' && html`<p class="srv-hint danger wiz-appear" data-fade>Olisar is running but failing its healthcheck. Check the logs under Settings.</p>`}
        ${srv.phase === 'unreachable' && html`<p class="srv-hint danger wiz-appear" data-fade>Couldn’t reach your server. Check that the VM is running. Still retrying, or use <b>Reconnect</b>.</p>`}
        ${ask && html`
          <div class=${'srv-redirect' + (leaving ? ' leaving' : '')} data-fade>
            <${Field} id="srv-redirect" plain label="Redirect URL"
              desc=${html`Signing in to your server’s console needs it. On ${A(oauth, 'the OAuth2 page')}, under <strong>Redirects</strong>, add it and press <strong>Save Changes</strong>.`}>
              <${RedirectRow} url=${redirect} added=${srv.listed} />
            <//>
            ${!srv.listed && html`<div class="check-line wiz-appear" role="status"><span class="spinner"></span> Waiting for Discord to list it…</div>`}
          </div>`}
        <div class="onb-actions" ref=${actions}>
          <button class="primary" data-morph="console">Open console ↗</button>
          ${power}
          <button class="ghost" data-fade disabled=${srv.busy} onClick=${srv.reconnect}>Reconnect</button>
        </div>
      </div>`
  }

  // Prototype only: hold the panel in any state the real one can report, take the redirect
  // away and give it back, and add a memory on demand.
  function PreviewStrip({ srv }) {
    return html`
      <nav class="state-strip" aria-label="Preview a server state">
        <span class="state-strip-l">Preview</span>
        ${PREVIEW.map((p) => html`<button key=${p} aria-pressed=${srv.phase === p} onClick=${() => srv.preview(p)}>${PHASES[p].label.replace('…', '')}</button>`)}
        <span class="state-strip-sep" aria-hidden="true"></span>
        <button aria-pressed=${srv.listed} onClick=${srv.toggleListed}>Redirect added</button>
        <button onClick=${() => window.__brain.add()}>New activity</button>
      </nav>`
  }

  // ── The final screen ──────────────────────────────────────────────────────────
  // The corner: the panel's title, status and controls, small. The pieces marked data-morph
  // are the ones the stats screen's own travel into; data-extra only fades.
  function BrainHud({ srv, openSettings }) {
    const power = srv.phase === 'running' || srv.phase === 'unhealthy'
      ? html`<button class="caution" data-morph="power" onClick=${srv.stop}>Stop server</button>`
      : html`<button data-morph="power" disabled>Working…</button>`
    const update = !!srv.available
    return html`
      <div class="brain-hud">
        <div class="hud-head">
          <img class="brand-logo" data-morph="logo" src=${window.ASSETS.logo} alt="" />
          <h1 data-morph="title" tabindex="-1">Your Olisar server</h1>
          <span class=${'badge ' + srv.tone} role="status" data-morph="badge">${srv.label}</span>
          ${srv.phase === 'running' && html`<span class="hud-up" data-extra>Up ${srv.up.toLowerCase()}</span>`}
        </div>
        <div class="hud-actions">
          <button class="primary" data-morph="console">Open console ↗</button>
          ${power}
          <button class="ghost icon-btn" data-extra data-tip=${update ? 'Settings, update available' : 'Settings'}
            aria-label=${update ? 'Settings, an update is available' : 'Settings'} onClick=${() => openSettings(update ? 'updates' : 'general')}>
            ${I.settings(16)}${update && html`<span class="hud-dot"></span>`}
          </button>
        </div>
      </div>`
  }

  // Memories: what the bot has been doing, one small sphere each. Replies show who they
  // answered and how the bot was called; the rest, what it learned or noticed. The sphere is
  // drawn by the engine where brain.js puts it; this is only the words inside.
  const agoShort = (at, now) => {
    const s = Math.max(0, Math.floor((now - at) / 1000))
    return s < 45 ? 'now' : s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : `${Math.floor(s / 3600)}h`
  }
  const agoLong = (at, now) => {
    const s = Math.max(0, Math.floor((now - at) / 1000))
    if (s < 45) return 'just now'
    if (s < 3600) { const m = Math.max(1, Math.round(s / 60)); return `${m} minute${m === 1 ? '' : 's'} ago` }
    const h = Math.floor(s / 3600); return `${h} hour${h === 1 ? '' : 's'} ago`
  }
  const Av = ({ name, size = 20 }) => html`<img class="mem-av" src=${window.Brain.avatarFor(name)} alt="" width=${size} height=${size} />`
  const KINDS = {
    reply: { tag: (m) => m.trigger, more: (m) => m.where, say: (m, a) => `Reply to ${m.who} (${m.trigger}) in ${m.where}, ${a}: ${m.text}` },
    member: { tag: () => 'Joined', say: (m, a) => `New member ${m.who}, ${a}` },
    people: { tag: () => 'Synced', say: (m, a) => `Member sync, ${a}: ${m.count} members` },
    impression: { tag: () => 'Impression', more: (m) => `From ${m.messages} messages`, say: (m, a) => `Impression of ${m.who}, ${a}: ${m.text}` },
    remembered: { tag: () => 'Memory', more: (m) => m.where, say: (m, a) => `Memory about ${m.who}, ${a}: ${m.text}` },
    glossary: { tag: () => 'Glossary', more: (m) => m.where, say: (m, a) => `Glossary, ${a}: ${m.text}` },
    status: { tag: () => 'Status', say: (m, a) => `Status set to “${m.text}”, ${a}` },
    learned: { tag: () => 'Knowledge', say: (m, a) => `Knowledge source ${m.text}, ${m.count} passages, ${a}` },
    reminder: { tag: () => 'Reminder', more: (m) => m.where, say: (m, a) => `Reminder for ${m.who}, ${a}: ${m.text}` },
    image: { tag: () => 'Image', more: (m) => m.where, say: (m, a) => `Image for ${m.who}, ${a}: “${m.text}”` },
    health: { tag: () => 'Health check', more: () => 'Every 30 seconds', say: (m) => `Health check passed ${Math.max(0, Math.round((Date.now() - m.at) / 1000))} seconds ago` },
  }
  // An address that doesn't fit starts at the left and trails off to the right, well inside the
  // sphere, rather than running out to the dust.
  function MemLink({ text }) {
    const ref = useRef(null)
    useLayoutEffect(() => {
      const el = ref.current
      if (el) el.classList.toggle('long', el.scrollWidth > el.clientWidth + 1)
    }, [text])
    return html`<div class="mem-link" ref=${ref}>${text}</div>`
  }
  function MemoryBody({ m, now }) {
    const who = m.who && html`<div class="mem-who">${Av({ name: m.who, size: m.kind === 'member' ? 36 : 20 })}<span>${m.who}</span></div>`
    const text = (t) => html`<div class="mem-text">${t}</div>`
    switch (m.kind) {
      case 'member': return html`${who}`
      case 'people': return html`<div class="mem-faces">${m.faces.map((n) => Av({ name: n, size: 24 }))}</div>${text(`${m.count} members`)}`
      case 'glossary': return html`<span class="mem-ic">${I.book(15)}</span>${text(m.text)}`
      case 'learned': return html`<span class="mem-ic">${I.doc(15)}</span><${MemLink} text=${m.text} />${text(`${m.count} passages`)}`
      case 'status': return html`<div class="mem-text"><i class="mem-dot"></i>${m.text}</div>`
      case 'health': return html`<span class="mem-ic ok">${I.check(16)}</span><div class="mem-text"><b>Healthy</b></div>`
      case 'image': return html`${who}${text(`“${m.text}”`)}`
      default: return html`${who}${text(m.text)}`
    }
  }
  // One memory on the ring: a button (it opens the memory), its words inside the sphere.
  function Memory({ m, now }) {
    const k = KINDS[m.kind] || KINDS.reply
    const peek = (on) => () => window.__brain.peek(m.id, on)
    const openIt = () => window.__brain.open(m.id)
    const foot = m.kind === 'health' ? `Checked ${Math.max(0, Math.round((now - m.at) / 1000))}s ago` : `${k.tag(m)} · ${agoShort(m.at, now)}`
    const more = k.more?.(m)
    return html`
      <li class=${'mem mem-' + m.kind} data-mem=${m.id}>
        <div class="mem-btn" role="button" tabindex="0" aria-label=${k.say(m, agoLong(m.at, now))}
          onPointerEnter=${peek(true)} onPointerLeave=${peek(false)} onBlur=${peek(false)}
          onFocus=${(e) => { if (e.currentTarget.matches(':focus-visible')) window.__brain.peek(m.id, true) }}
          onClick=${openIt} onKeyDown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openIt() } }}>
          <div class="mem-mask" aria-hidden="true">
            <div class="mem-in">
              <${MemoryBody} m=${m} now=${now} />
              <div class="mem-foot">${foot}</div>
              ${more && html`<div class="mem-more">${more}</div>`}
            </div>
          </div>
        </div>
      </li>`
  }

  // An opened memory, in the middle: what it was about, in full. A reply shows the message it
  // answered and the whole reply; the rest show their whole text and where it came from.
  const Msg = ({ name, av, text, bot }) => html`
    <div class=${'mo-msg' + (bot ? ' bot' : '')}>
      <img class="mem-av" src=${av} alt="" width="24" height="24" />
      <div><b>${name}</b><p>${text}</p></div>
    </div>`
  const userMsg = (who, text) => Msg({ name: who, av: window.Brain.avatarFor(who), text })
  const botMsg = (text) => Msg({ name: BOT_NAME, av: window.ASSETS.bot, text, bot: true })
  const Who = ({ name, size = 22 }) => html`<div class="mo-who">${Av({ name, size })}<span>${name}</span></div>`
  const HEALTH = ['Web server', 'Vector search', 'Sandbox', 'Model']
  function OpenBody({ m, now }) {
    switch (m.kind) {
      case 'reply': return html`<div class="mo-chat">${userMsg(m.who, m.ask)}${botMsg(m.text)}</div>`
      case 'impression': return html`<${Who} name=${m.who} /><p class="mo-text">${m.full || m.text}</p><div class="mo-meta">From ${m.messages} messages</div>`
      case 'remembered': return html`<${Who} name=${m.who} /><p class="mo-text mo-strong">${m.text}</p><div class="mo-chat">${userMsg(m.who, m.said)}</div><div class="mo-meta">${m.type}</div>`
      case 'glossary': return html`<p class="mo-text mo-strong">${m.text}</p><div class="mo-chat">${userMsg(m.who, m.said)}</div>`
      case 'status': return html`<p class="mo-text mo-status"><i class="mem-dot"></i>${m.text}</p><div class="mo-meta">${m.how}</div>`
      case 'learned': return html`<span class="mem-ic">${I.doc(18)}</span><p class="mo-url">${m.url}</p><div class="mo-meta">${m.count} passages · added by ${m.who} with <code>${m.how}</code></div>`
      case 'reminder': return html`<div class="mo-chat">${userMsg(m.who, m.ask)}${botMsg(m.text)}</div>`
      case 'image': return html`<div class="mo-chat">${userMsg(m.who, m.ask)}</div><img class="mo-pic" src=${window.Brain.pictureFor(m.text)} alt=${m.text} /><div class="mo-meta">“${m.text}”</div>`
      case 'member': return html`${Av({ name: m.who, size: 56 })}<p class="mo-text mo-strong">${m.who}</p>${m.roles?.length ? html`<div class="mo-roles">${m.roles.map((r) => html`<span key=${r}>${r}</span>`)}</div>` : null}`
      case 'people': return html`<p class="mo-big">${m.count} members</p><div class="mo-faces">${[...m.faces, ...(m.more || [])].map((n) => Av({ name: n, size: 26 }))}</div><div class="mo-meta">Synced when ${BOT_NAME} started</div>`
      case 'health': return html`<span class="mem-ic ok">${I.check(20)}</span><p class="mo-text mo-strong">Healthy</p>
        <div class="mo-checks">${HEALTH.map((h) => html`<div key=${h}>${I.check(14)}<span>${h}</span></div>`)}</div>
        <div class="mo-meta">Checked ${Math.max(0, Math.round((now - m.at) / 1000))}s ago, every 30 seconds</div>`
      default: return html`<p class="mo-text">${m.text}</p>`
    }
  }
  function MemoryOpen({ m, isOpen, now }) {
    const back = useRef(null)
    useEffect(() => { if (isOpen) back.current?.focus({ preventScroll: true }) }, [m.id, isOpen])
    const k = KINDS[m.kind] || KINDS.reply
    const when = agoLong(m.at, now)
    const head = m.kind === 'health' ? 'Health check' : [k.tag(m), m.where, when].filter(Boolean).join(' · ')
    return html`
      <section class=${'mem-open mem-open-' + m.kind} aria-label=${k.say(m, when)} inert=${!isOpen}>
        <button class="ghost icon-btn mem-back" ref=${back} aria-label="Back" data-tip="Back" onClick=${() => window.__brain.close()}>${I.back(18)}</button>
        <div class="mo-in">
          <div class="mo-head">${head}</div>
          <${OpenBody} m=${m} now=${now} />
        </div>
      </section>`
  }
  function Memories() {
    const [, bump] = useState(0)
    useEffect(() => window.__brain.subscribe(() => bump((n) => n + 1)), [])
    // Ages, and the heartbeat's seconds.
    useEffect(() => { const t = setInterval(() => bump((n) => n + 1), 1000); return () => clearInterval(t) }, [])
    const now = Date.now()
    const shown = window.__brain.shown()
    return html`
      <ol class="mems" aria-label="Recent activity">
        ${window.__brain.list().map((m) => html`<${Memory} key=${m.id} m=${m} now=${now} />`)}
      </ol>
      ${shown?.item && html`<${MemoryOpen} key=${shown.item.id} m=${shown.item} isOpen=${shown.open} now=${now} />`}`
  }

  // ── The screen ────────────────────────────────────────────────────────────────
  function SetupWizard({ openSettings, openDocs, srv, onServer }) {
    const [step, setStep] = useState(0)
    const [err, setErr] = useState('')
    const [saveFailed, setSaveFailed] = useState(false)
    const [errSeq, setErrSeq] = useState(0)
    const [moved, setMoved] = useState(null)
    const enter = (what) => moved?.what === what ? ' enter' + (moved.back ? ' back' : '') : ''
    // 'local' | 'server' once setup is done. `?end=server` opens straight on the server panel.
    const [finished, setFinished] = useState(END === 'server' || END === 'brain' ? 'server' : null)

    const [mode, setMode] = useState('local')
    const [token, setToken] = useState('')
    const tokenCheck = useLiveCheck(token, (t) => api.setupBot(t).then((r) => ({ ok: true, result: r })))
    const [bot, setBot] = useState(null)
    const [guilds, setGuilds] = useState([])
    useEffect(() => {
      setBot(tokenCheck.state === 'ok' ? tokenCheck.result ?? null : null)
      setGuilds([])
    }, [tokenCheck.state, tokenCheck.result])

    const [secret, setSecret] = useState('')
    const secretCheck = useLiveCheck(bot ? secret : '', (s) => api.checkSetupSecret(bot.id, s), bot?.id)

    const [guildId, setGuildId] = useState('')
    useEffect(() => {
      if (guilds.length && !guilds.some((g) => g.id === guildId)) setGuildId(guilds[0].id)
    }, [guilds])

    const [tunnelNode, setTunnelNode] = useState(thisBot.id !== 'default' ? deviceNameFor(thisBot.name) : 'olisar')
    const [tunnelAuthKey, setTunnelAuthKey] = useState('')
    const [provisioning, setProvisioning] = useState(false)
    const [tunnelDone, setTunnelDone] = useState(false)
    const [tunnelUrl, setTunnelUrl] = useState('')
    const [tunnelErr, setTunnelErr] = useState('')

    const [adminUser, setAdminUser] = useState('')
    const [connectMode, setConnectMode] = useState(false)
    const [serverUser, setServerUser] = useState('ubuntu')
    const [serverHost, setServerHost] = useState('')
    const [showKey, setShowKey] = useState(false)
    const [deploying, setDeploying] = useState(false)
    const [deployLog, setDeployLog] = useState('')
    const [deployErr, setDeployErr] = useState('')
    const [installs, setInstalls] = useState([])
    const [installDir, setInstallDir] = useState('')

    const [sourceChoice, setSourceChoice] = useState(null)
    const source = sourceChoice ?? (shared[0]?.from.id || 'new')
    const sharing = source !== 'new'
    const [share, setShare] = useState(null)
    const [shareBusy, setShareBusy] = useState(false)
    const [shareErr, setShareErr] = useState('')

    const [gemini, setGemini] = useState('')
    const geminiCheck = useLiveCheck(gemini, (k) => api.checkSetupGemini(k))
    const [saving, setSaving] = useState(false)

    const steps = stepsFor(mode)
    const last = steps.length - 1
    const cur = steps[Math.min(step, last)]

    const redirects = mode === 'server' ? [] : [
      ORIGIN + '/auth/callback',
      ...(mode === 'tunnel' && tunnelUrl ? [tunnelUrl.replace(/\/$/, '') + '/auth/callback'] : []),
    ]
    const added = (u) => !!bot?.redirect_uris.includes(u)
    const redirectsIn = redirects.length > 0 && redirects.every(added)
    const redirectPending = cur === 'signin' && !redirects.every(added)

    const watching = !!bot && (
      (cur === 'bot' && (bot.intents_missing.length > 0 || bot.code_grant))
      || (cur === 'signin' && !redirects.every(added))
      || cur === 'server'
    )
    usePoll(() => api.setupDiscordStatus(token.trim()).then((r) => {
      const { guilds: gs, ...app } = r
      setBot(app)
      setGuilds(gs)
    }), 3000, watching && !finished)

    const pk = usePubkey((cur === 'deploy' && !sharing) || (connectMode && showKey))

    useEffect(() => {
      if (!(cur === 'deploy' && sharing) || share?.from === source) return
      let alive = true
      setShareBusy(true); setShareErr('')
      api.shareServer(source)
        .then((r) => {
          if (!alive) return
          if (!r?.ok) throw new Error(r?.error || 'Couldn’t use that server.')
          setShare({ from: source, host: r.host, user: r.user || 'ubuntu' })
          setTunnelAuthKey((k) => k || r.tailscale_auth || '')
          setAdminUser((a) => a || r.admin_allowlist || '')
        })
        .catch((e) => { if (alive) setShareErr(e?.message || 'Couldn’t use that server.') })
        .finally(() => { if (alive) setShareBusy(false) })
      return () => { alive = false }
    }, [cur, sharing, source])

    function pickMode(m) {
      if (m !== 'tunnel' && tunnelDone) {
        api.disableTunnel().catch(() => {})
        setTunnelDone(false); setTunnelUrl('')
      }
      setMode(m)
    }

    function blocker() {
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
    const blocked = blocker()
    // A refused Continue's reason goes once it no longer holds: when nothing blocks the step,
    // or when the reason has become a different one ("Paste your bot token" once a token is
    // pasted). The next press says what's in the way now.
    useEffect(() => { if (!saveFailed && err && err !== blocked) setErr('') }, [blocked])

    // The field a refused Continue is about, when the value is at fault: empty, or turned
    // down. A check that is still running, or couldn't reach Discord, isn't the value's fault.
    function blockerField() {
      if (cur === 'bot' && (!token.trim() || tokenCheck.state === 'bad')) return 's-token'
      if (cur === 'remote' && !tunnelDone && !tunnelAuthKey.trim()) return 's-ts'
      if (cur === 'signin' && (!secret.trim() || secretCheck.state === 'bad')) return 's-secret'
      if (cur === 'keys' && ((mode === 'server' && !gemini.trim()) || (gemini.trim() && geminiCheck.state === 'bad'))) return 's-gemini'
      return null
    }
    // That field stays marked until it's edited or the step changes.
    const [flagged, setFlagged] = useState(null)
    useEffect(() => { setFlagged(null) }, [cur, connectMode])
    const flag = (id) => { if (id) { setFlagged(id); shake(id) } }
    const edit = (id, set) => (v) => { set(v); if (flagged === id) setFlagged(null) }
    // A value Discord or Google turns down shakes as the answer arrives.
    useEffect(() => { if (tokenCheck.state === 'bad') shake('s-token') }, [tokenCheck.state])
    useEffect(() => { if (secretCheck.state === 'bad') shake('s-secret') }, [secretCheck.state])
    useEffect(() => { if (geminiCheck.state === 'bad') shake('s-gemini') }, [geminiCheck.state])

    function next() {
      setSaveFailed(false)
      const why = blocker()
      setErr(why)
      if (why) { setErrSeq((n) => n + 1); flag(blockerField()); return }
      setMoved({ what: 'step', back: false })
      setStep((s) => Math.min(s + 1, last))
    }
    function back() {
      setErr(''); setSaveFailed(false)
      setMoved({ what: 'step', back: true })
      setStep((s) => Math.max(0, s - 1))
    }
    function showConnect(on) {
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
        setErr('')
      } catch (e) {
        setTunnelErr(e?.message || 'Couldn’t turn on remote access.')
      } finally {
        setProvisioning(false)
      }
    }

    function done(as) {
      setMoved({ what: 'screen', back: false })
      setFinished(as)
    }

    async function finish() {
      setSaveFailed(false)
      const why = blocker()
      setErr(why)
      if (why) { setErrSeq((n) => n + 1); flag(blockerField()); return }
      setSaving(true)
      try {
        if (gemini.trim()) await api.saveSetupKeys({ gemini_api_key: gemini.trim() })
        await api.saveSetup({ discord_token: token.trim(), discord_client_id: bot?.id || '', discord_client_secret: secret.trim(), target_guild_id: guildId })
        setSaving(false)
        done('local')
      } catch (e) {
        setErr(e?.message || 'Save failed.')
        setErrSeq((n) => n + 1)
        setSaveFailed(true)
        setSaving(false)
      }
    }

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
      return L.join('\n')
    })()

    async function deployServer() {
      setDeployErr('')
      const host = sharing ? share?.host || '' : serverHost.trim()
      const user = sharing ? share?.user || 'ubuntu' : serverUser.trim() || 'ubuntu'
      if (sharing && !host) return setDeployErr(shareErr || 'Still connecting to that server.')
      if (!host) { flag('s-host'); return setDeployErr('Enter the VM’s public IP address.') }
      if (!(gemini.trim() && tunnelAuthKey.trim())) {
        if (!tunnelAuthKey.trim()) flag('s-ts2')
        return setDeployErr('A Gemini key and a Tailscale auth key are both required.')
      }
      setDeploying(true); setDeployLog('')
      try {
        const r = await api.serverDeploy({ host, user, env: envFile })
        if (r?.ok) { setDeploying(false); done('server'); return }
        setDeployErr(r?.error || 'Deploy failed.'); setDeployLog(r?.log || '')
      } catch (e) {
        setDeployErr(e?.message || 'Couldn’t reach the server.')
      }
      setDeploying(false)
    }

    async function connectServer() {
      setDeployErr('')
      if (!serverHost.trim()) { flag('c-host'); return setDeployErr('Enter the VM’s public IP address.') }
      setDeploying(true)
      try {
        const via = shared.find((x) => x.host === serverHost.trim())
        if (via) await api.shareServer(via.from.id).catch(() => null)
        const r = await api.serverConnect({ host: serverHost.trim(), user: serverUser.trim() || 'ubuntu', app_dir: installDir || undefined })
        if (r?.ok) { setDeploying(false); done('server'); return }
        if (r?.choose?.length) { setInstalls(r.choose); setInstallDir(r.choose[0].dir); setDeployErr('') }
        else { setDeployErr(r?.error || 'Couldn’t connect to that VM.') }
      } catch (e) {
        setDeployErr(e?.message || 'Couldn’t reach the server.')
      }
      setDeploying(false)
    }

    const primary = connectMode
      ? { label: deploying ? 'Connecting…' : 'Connect', run: connectServer, off: deploying }
      : step < last
        ? { label: 'Continue', run: next, off: redirectPending }
        : mode === 'server'
          ? { label: deploying ? 'Deploying…' : 'Deploy to server', run: deployServer, off: deploying || (sharing && shareBusy) }
          : { label: saving ? 'Saving…' : 'Finish & start Olisar', run: finish, off: saving }

    const scroller = useRef(null)
    const flow = useRef(null)
    const revealBtn = useRef(null)
    useEffect(() => {
      if (moved?.what !== 'screen') return
      if (connectMode) flow.current?.querySelector('input')?.focus()
      else revealBtn.current?.focus()
    }, [connectMode])
    // A new step starts at its top.
    useLayoutEffect(() => { if (scroller.current) scroller.current.scrollTop = 0 }, [cur, connectMode, finished])
    // The buttons stay put while a long step scrolls under them, so whatever arrives at the
    // bottom of it (a refused Continue's reason, the deploy notice, a failure) is brought
    // into view rather than left below the fold.
    useEffect(() => {
      const all = scroller.current?.querySelectorAll('[data-arrival]')
      const el = all?.[all.length - 1]
      if (!el || !(err || deployErr || deploying || deployLog || tunnelErr)) return
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      el.scrollIntoView({ block: 'nearest', behavior: smooth ? 'smooth' : 'auto' })
    }, [errSeq, deployErr, deploying, deployLog, tunnelErr])

    // What the form shows. Only the step's form and a few of its states: which hosting
    // choice is picked, whether Discord has listed the redirects, whether the bot has joined,
    // whether the key works, how far a connect has got.
    useEffect(() => {
      const f = form(); if (!f) return
      if (finished === 'local') return f.set(8)
      if (finished) return      // the server panel steers the form from here
      if (connectMode) return f.set(7, deploying ? 0.62 : 0.12)
      if (cur === 'where') return f.set(0, { local: 0, tunnel: 1, server: 2 }[mode])
      if (cur === 'bot') return f.set(1)
      if (cur === 'remote') return f.set(2)
      if (cur === 'signin') return f.set(3, redirectsIn ? 1 : 0)
      if (cur === 'server') return f.set(4, guilds.length ? 1 : 0)
      if (cur === 'keys') return f.set(5, geminiCheck.state === 'ok' ? 1 : 0.45)
      if (cur === 'deploy') return f.set(6)
    }, [finished, connectMode, deploying, cur, mode, redirectsIn, guilds.length, geminiCheck.state])
    // Livelier while something is being waited on, calm again once it answers.
    const waiting = !finished && (tokenCheck.state === 'checking' || secretCheck.state === 'checking' || geminiCheck.state === 'checking'
      || (cur === 'bot' && !!bot?.intents_missing.length) || redirectPending || (cur === 'server' && !!bot && !guilds.length)
      || provisioning || deploying || saving || shareBusy)
    useEffect(() => { if (finished !== 'server') form()?.energy(waiting ? 0.7 : 0) }, [waiting, finished])
    // Each thing Discord, Tailscale or Google confirms passes through the form once.
    usePulseOn(tokenCheck.state === 'ok', 1)
    usePulseOn(!!bot && !bot.intents_missing.length, 0.6)
    usePulseOn(secretCheck.state === 'ok', 0.6)
    usePulseOn(redirectsIn, 0.8)
    usePulseOn(guilds.length > 0, 1)
    usePulseOn(tunnelDone, 0.9)
    usePulseOn(geminiCheck.state === 'ok', 0.8)
    usePulseOn(!!finished, 1)

    // Where the finished server lives, for the panel and the final screen's corner.
    const serverInfo = {
      host: share?.host || serverHost.trim() || '203.0.113.9', user: share?.user || serverUser.trim() || 'ubuntu',
      url: `https://${tunnelNode.trim() || 'olisar'}.tail4f2a.ts.net`, appId: bot?.id || MOCK_APP_ID,
    }
    useEffect(() => { if (finished === 'server') onServer?.(serverInfo) }, [finished])

    const body = finished === 'local' ? html`
      <div key="signin" class=${'wiz-screen' + enter('screen')}>
        <h1>Olisar Secure Console</h1>
        <p class="step-sub">Sign in with Discord. Only server admins can reach this console.</p>
        <button class="btn-discord cta">${I.login(18)} Continue with Discord</button>
      </div>`
    : finished === 'server' ? html`
      <div key="server" class=${'wiz-screen' + enter('screen')}>
        <${ServerPanel} srv=${srv} ...${serverInfo} />
      </div>`
    : connectMode ? html`
      <div key="connect" class=${'wiz-screen' + enter('screen')}>
        <h1>Connect to an existing server</h1>
        <p class="step-sub">Point Olisar at a cloud VM that already runs it. Nothing is reinstalled.</p>
        <div class="callout tip" style="margin-bottom:16px">
          <span class="ic">${I.info(17)}</span>
          <div class="callout-body">Its persona, memory, knowledge, and settings are kept.</div>
        </div>
        <${Field} id="c-host" label="VM public IP address" desc="The VM already running Olisar.">
          <${Text} id="c-host" desc value=${serverHost} invalid=${flagged === 'c-host'} onChange=${(v) => { setServerHost(v); setInstalls([]); setInstallDir(''); if (flagged === 'c-host') setFlagged(null) }} placeholder="e.g. 203.0.113.9" mono />
        <//>
        ${installs.length > 0 && html`
          <${Field} id="c-install" label="Which bot is this?" desc="This server runs more than one.">
            <${Select} id="c-install" value=${installDir} onChange=${setInstallDir} options=${installs.map((i) => ({ value: i.dir, label: i.name }))} />
          <//>`}
        <details class="disclosure" onToggle=${(e) => setShowKey(e.currentTarget.open)}>
          <summary><span class="disclosure-chev">${I.chevron(14)}</span>Can’t connect? Add this app’s SSH key to the VM</summary>
          <div class="desc" style="margin:8px 0 12px;color:var(--text-2);font-size:12px">
            Paste this into the VM’s <code>~/.ssh/authorized_keys</code>, or the provider’s SSH-keys box, then Connect. A VM this app already set up trusts it automatically.
          </div>
          <${PubkeyBox} state=${pk} />
          <${Field} id="c-user" label="SSH user" desc="The VM's login user. Ubuntu images use ubuntu.">
            <${Text} id="c-user" desc value=${serverUser} onChange=${setServerUser} placeholder="ubuntu" mono />
          <//>
        </details>
        ${deploying && html`
          <div class="callout note wiz-appear" style="margin-bottom:4px" data-arrival>
            <span class="ic"><span class="spinner"></span></span>
            <div class="callout-body">Connecting to your VM over SSH…</div>
          </div>`}
        ${deployErr && html`
          <div class="err-block wiz-appear" data-arrival>
            <div class="err">${deployErr}</div>
            <${FeedbackButton} className="" openSettings=${openSettings} prefill=${{ category: 'Bug report', message: reportBody('Connecting to my existing Olisar server failed.', deployErr) }}>Report a problem<//>
          </div>`}
      </div>`
    : html`
      <div key="wizard" class=${'wiz-screen' + enter('screen')}>
        <div class="steps" style=${{ gridTemplateColumns: Array.from({ length: BAR_SLOTS }, (_, i) => (i < steps.length ? '1fr' : '0fr')).join(' ') }}
          role="progressbar" aria-label="Setup progress" aria-valuemin="1" aria-valuemax=${steps.length} aria-valuenow=${Math.min(step, last) + 1}>
          ${Array.from({ length: BAR_SLOTS }, (_, i) => html`<i key=${i} class=${i <= step ? 'on' : ''}></i>`)}
        </div>
        <h1>Set up Olisar</h1>
        <p class="step-sub">A one-time setup to connect Olisar to your Discord server.</p>

        <div key=${cur} class=${'wiz-step' + enter('step')}>
          ${cur === 'where' && html`<${ModeChoice} mode=${mode} onPick=${pickMode} />`}

          ${cur === 'bot' && html`
            <div class="tunnel-help">
              <ol>
                <li>Create an application in the ${A(PORTAL, 'Discord Developer Portal')}, or open the one you have.</li>
                <li>Open <strong>Bot</strong>, press <strong>Reset Token</strong>, and copy the token.</li>
              </ol>
            </div>
            <${Field} id="s-token" label="Bot token">
              <${Text} id="s-token" value=${token} invalid=${tokenCheck.state === 'bad' || flagged === 's-token'} onChange=${edit('s-token', setToken)} placeholder="your bot token" mono />
            <//>
            <${CheckLine} check=${tokenCheck}
              ok=${html`${bot?.avatar ? html`<img class="check-avatar" src=${bot.avatar} alt="" />` : I.check(14)}<span>Connected as <b>${bot?.username}</b></span>`}
              bad="Discord didn’t accept that token." />
            ${bot && bot.intents_missing.length > 0 && html`
              <div class="callout warning wiz-appear">
                <span class="ic">${I.warn(17)}</span>
                <div class="callout-body">Turn on <strong>${intentList(bot.intents_missing)}</strong> on ${A(`${PORTAL}/${bot.id}/bot`, 'the Bot page')}, under Privileged Gateway Intents.</div>
              </div>`}
            ${bot?.code_grant && html`
              <div class="callout warning wiz-appear">
                <span class="ic">${I.warn(17)}</span>
                <div class="callout-body">Turn off <strong>Requires OAuth2 Code Grant</strong> on ${A(`${PORTAL}/${bot.id}/bot`, 'the Bot page')}, or the invite link won’t work.</div>
              </div>`}`}

          ${cur === 'remote' && html`
            <div class="tunnel-help">
              <b>Free remote access via Tailscale — no domain needed</b>
              <ol>
                <li>Create a free ${A('https://login.tailscale.com/start', 'Tailscale account')} (sign in with Google, GitHub, etc.).</li>
                <li>Generate an auth key at ${A('https://login.tailscale.com/admin/settings/keys', 'Settings → Keys → Generate auth key')}, turning on <strong>Reusable</strong>. Paste it below.</li>
                <li>Click <strong>Enable remote access</strong>. The first time, Tailscale may ask you to turn on <strong>Funnel</strong> for your tailnet: follow the link in the message, then press Enable again.</li>
              </ol>
              <div style="margin-top:8px">Your dashboard then lives at a stable <code>https://…ts.net</code> address. Other admins just open it and sign in with Discord; they don't need Tailscale themselves.</div>
            </div>
            <${Field} id="s-ts" label="Tailscale auth key" desc="Stored on this machine and only ever handed to Tailscale.">
              <${Text} id="s-ts" desc value=${tunnelAuthKey} invalid=${flagged === 's-ts'} onChange=${(v) => { setTunnelAuthKey(v); setTunnelDone(false); if (flagged === 's-ts') setFlagged(null) }} placeholder="tskey-auth-…" mono />
            <//>
            <${Field} id="s-node" label="Device name (optional)" desc="Becomes the first part of your dashboard's web address.">
              <${Text} id="s-node" desc value=${tunnelNode} onChange=${(v) => { setTunnelNode(v); setTunnelDone(false) }} placeholder="olisar" mono />
            <//>
            <div class="act-row">
              <button disabled=${!tunnelAuthKey.trim() || provisioning} onClick=${enableTunnel}>
                ${provisioning ? 'Connecting…' : tunnelDone ? 'Reconnect' : 'Enable remote access'}
              </button>
              <span class="grow">
                ${tunnelDone && tunnelUrl && html`<span class="ok-pill wiz-pop">${I.check(14)} Live at ${tunnelUrl}</span>`}
                ${tunnelErr && html`<span class="err wiz-appear"><${Linkified} text=${tunnelErr} /></span>`}
              </span>
            </div>
            ${tunnelErr && html`
              <p class="err-help" data-arrival>Stuck?${' '}<${FeedbackButton} className="linklike" openSettings=${openSettings}
                prefill=${{ category: 'Question', message: reportBody('I\'m stuck turning on remote access with Tailscale.', tunnelErr, 'What I\'ve tried:') }}>Ask the team<//></p>`}`}

          ${cur === 'signin' && bot && html`
            <${Field} id="s-secret" label="Client secret" desc=${html`On ${A(`${PORTAL}/${bot.id}/oauth2`, 'the OAuth2 page')}, press <strong>Reset Secret</strong> and copy it.`}>
              <${Text} id="s-secret" desc value=${secret} invalid=${secretCheck.state === 'bad' || flagged === 's-secret'} onChange=${edit('s-secret', setSecret)} placeholder="client secret" mono />
            <//>
            <${CheckLine} check=${secretCheck} ok=${html`${I.check(14)} Secret matches`} bad=${`That isn’t ${bot.username}’s client secret.`} />
            ${redirects.length > 0 && html`
              <${Field} id="s-redirects" plain label=${redirects.length > 1 ? 'Redirect URLs' : 'Redirect URL'}
                desc=${html`On the same page, under <strong>Redirects</strong>, add ${redirects.length > 1 ? 'both' : 'it'} and press <strong>Save Changes</strong>.`}>
                <div class="redirect-list">${redirects.map((u) => html`<${RedirectRow} key=${u} url=${u} added=${added(u)} />`)}</div>
              <//>
              ${!redirects.every(added) && html`
                <div class="check-line wiz-appear" role="status"><span class="spinner"></span> Waiting for Discord to list ${redirects.length > 1 ? 'them' : 'it'}…</div>`}`}`}

          ${cur === 'server' && bot && html`
            <${Field} id="s-invite" plain label=${`Add ${bot.username} to your server`}>
              <div class="invite-row">
                <a class="btn-discord" href=${bot.invite_url} target="_blank" rel="noreferrer">${DiscordLogo()} Add to Discord</a>
                <${CopyText} text=${bot.invite_url} label="Copy link" />
              </div>
            <//>
            <div class=${'check-line' + (guilds.length ? ' ok' : '')} role="status">
              <${ArrivingLine} id=${guilds.length ? 'joined' : 'waiting'}>
                ${guilds.length === 0
                  ? html`<span class="spinner"></span> Waiting for ${bot.username} to join a server…`
                  : html`${I.check(14)} <span>In ${guilds.map((g) => g.name).join(', ')}</span>`}
              <//>
            </div>
            ${guilds.length > 1 && html`
              <${Field} id="s-guild" label="Main server" desc="Its persona and settings also apply in DMs.">
                <${Select} id="s-guild" value=${guildId} onChange=${setGuildId} options=${guilds.map((g) => ({ value: g.id, label: g.name }))} />
              <//>`}`}

          ${cur === 'keys' && html`
            <${Field} id="s-gemini" label="Gemini API key" desc=${mode === 'server'
              ? html`Powers everything Olisar says. Create a free key in ${A('https://aistudio.google.com/apikey', 'Google AI Studio')}.`
              : html`Powers everything Olisar says. Create a free key in ${A('https://aistudio.google.com/apikey', 'Google AI Studio')}. You can add it later, but the bot can't reply without it.`}>
              <${Text} id="s-gemini" desc value=${gemini} invalid=${geminiCheck.state === 'bad' || flagged === 's-gemini'} onChange=${edit('s-gemini', setGemini)} placeholder="AIza…" mono />
            <//>
            <${CheckLine} check=${geminiCheck} ok=${html`${I.check(14)} Key works`} bad="Google didn’t accept that key." />`}

          ${cur === 'deploy' && html`
            ${shared.length > 0 && html`
              <${Segmented} className="deploy-seg" ariaLabel="Which server" value=${source} onChange=${setSourceChoice}
                options=${[...shared.map((x) => ({ value: x.from.id, label: serverLabel(x) })), { value: 'new', label: 'A new server' }]} />`}
            ${sharing ? html`
              <div class=${'callout ' + (shareErr ? 'warning' : 'note')} style="margin-bottom:16px">
                <span class="ic">${shareBusy ? html`<span class="spinner"></span>` : I.info(17)}</span>
                <div class="callout-body">
                  ${shareBusy ? 'Connecting to that server…' : shareErr ? shareErr : html`Olisar adds this bot to <b>${share?.host}</b>, next to the one already there. Nothing to set up on the server.`}
                </div>
              </div>` : html`
              <div class="callout note">
                <span class="ic">${I.info(17)}</span>
                <div class="callout-body">No VM yet? <button type="button" class="linklike" onClick=${() => openDocs('host-server')}>Host on a server</button> in the docs covers a free one on Oracle Cloud.</div>
              </div>
              <${Field} id="s-pubkey" plain label="SSH public key — paste this when creating the VM" desc="The matching private key never leaves this machine.">
                <${PubkeyBox} state=${pk} />
              <//>
              <${Field} id="s-host" label="VM public IP address" desc="From the instance's details page.">
                <${Text} id="s-host" desc value=${serverHost} invalid=${flagged === 's-host'} onChange=${edit('s-host', setServerHost)} placeholder="e.g. 203.0.113.9" mono />
              <//>`}
            <${Field} id="s-ts2" label="Tailscale auth key" desc=${html`Gives your server a dashboard address without needing a domain. Create a reusable key at ${A('https://login.tailscale.com/admin/settings/keys', 'Tailscale → Settings → Keys')}.`}>
              <${Text} id="s-ts2" desc value=${tunnelAuthKey} invalid=${flagged === 's-ts2'} onChange=${edit('s-ts2', setTunnelAuthKey)} placeholder="tskey-auth-…" mono />
            <//>
            ${deploying && html`
              <div class="callout note wiz-appear" style="margin-bottom:4px" data-arrival>
                <span class="ic"><span class="spinner"></span></span>
                <div class="callout-body">Installing Olisar on your VM. This takes a few minutes — keep this window open.</div>
              </div>`}
            ${deployLog && html`<div data-arrival><${Cb} file="install log" code=${deployLog} /></div>`}
            ${deployErr && html`
              <div class="err-block wiz-appear" data-arrival>
                <div class="err">${deployErr}</div>
                <${FeedbackButton} className="" openSettings=${openSettings} prefill=${{
                  category: 'Bug report',
                  message: ['Deploying Olisar to my server failed.', '', 'Error:', deployErr,
                    ...(deployLog ? ['', 'Install log (last lines):', logTail(deployLog)] : []), '', 'What I was doing:', ''].join('\n'),
                }}>Send this to the Olisar team<//>
              </div>`}`}

          ${err && (saveFailed ? html`
            <div key=${errSeq} class="err-block wiz-appear" data-arrival>
              <div class="err">${err}</div>
              <${FeedbackButton} className="" openSettings=${openSettings} prefill=${{ category: 'Bug report', message: reportBody('Finishing setup failed.', err) }}>Report a problem<//>
            </div>` : html`<div key=${errSeq} class="err wiz-appear" role="alert" data-arrival>${err}</div>`)}
        </div>
      </div>`

    // The buttons sit right under the step, primary first. One primary button for every step
    // and both screens, so the one that was pressed is still there afterwards and Enter walks
    // the whole wizard. Beside it is the one other way out: Back, or on the first step,
    // connecting to a server that already runs Olisar.
    const actions = finished ? null
    : html`
      <div class="onb-actions">
        <button class="primary" disabled=${primary.off} onClick=${primary.run}>${primary.label}</button>
        ${connectMode
          ? html`<button class="ghost" disabled=${deploying} onClick=${() => showConnect(false)}>Back</button>`
          : step === 0
            ? html`<button class="ghost" ref=${revealBtn} onClick=${() => showConnect(true)}>Connect to existing server</button>`
            : html`<button class="ghost" disabled=${saving} onClick=${back}>Back</button>`}
      </div>`

    return html`
      <div class="onb-pane wiz" ref=${scroller}>
        <main class="onb-body" ref=${flow}>
          ${body}
          ${actions}
        </main>
      </div>`
  }

  // A stand-in for the console's Settings: the same sections, and Feedback filled in the
  // way a failure fills it. The rest of the console's settings aren't in this prototype.
  const SECTIONS = [['general', 'General'], ['bots', 'Bots'], ['updates', 'Updates'], ['desktop', 'Desktop app'], ['feedback', 'Feedback']]
  function SettingsStub({ section, prefill, onClose }) {
    const [pane, setPane] = useState(section || 'general')
    const [category, setCategory] = useState(prefill?.category || 'Bug report')
    const [message, setMessage] = useState(prefill?.message || '')
    const card = useRef(null)
    const opener = useRef(document.activeElement)
    useEffect(() => {
      const first = card.current?.querySelector('textarea, select, button.settings-nav-item.active')
      first?.focus()
      const onKey = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); onClose() }
        if (e.key === 'Tab') {
          const f = [...card.current.querySelectorAll('button, textarea, select, [tabindex="0"]')].filter((x) => !x.disabled)
          if (!f.length) return
          const i = f.indexOf(document.activeElement)
          if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus() }
          else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus() }
        }
      }
      document.addEventListener('keydown', onKey)
      return () => { document.removeEventListener('keydown', onKey); opener.current?.focus?.() }
    }, [])
    const label = SECTIONS.find((s) => s[0] === pane)[1]
    return html`
      <div class="modal-backdrop" onMouseDown=${(e) => { if (e.target === e.currentTarget) onClose() }}>
        <div class="settings-modal" ref=${card} role="dialog" aria-modal="true" aria-labelledby="settings-title">
          <nav class="settings-nav" aria-label="Settings sections">
            <div class="settings-nav-title">Settings</div>
            ${SECTIONS.map(([id, name]) => html`<button key=${id} class=${'settings-nav-item' + (pane === id ? ' active' : '')} onClick=${() => setPane(id)}>${name}</button>`)}
          </nav>
          <div class="settings-body">
            <button class="ghost icon-btn settings-close" aria-label="Close settings" data-tip="Close" onClick=${onClose}>${I.close(16)}</button>
            <h2 id="settings-title">${label}</h2>
            ${pane === 'feedback' ? html`
              <${Field} id="fb-cat" label="Category">
                <${Select} id="fb-cat" value=${category} onChange=${setCategory} options=${['Bug report', 'Question', 'Idea'].map((x) => ({ value: x, label: x }))} />
              <//>
              <${Field} id="fb-msg" label="Message">
                <textarea id="fb-msg" rows="8" value=${message} onInput=${(e) => setMessage(e.currentTarget.value)}></textarea>
              <//>
              <div style="display:flex;justify-content:flex-end"><button class="primary" onClick=${onClose}>Send</button></div>`
            : html`<p>The console’s ${label} settings open here. They aren’t part of this prototype.</p>`}
          </div>
        </div>
      </div>`
  }

  // overlays.tsx TooltipHost: one tooltip, for icon-only buttons (data-tip).
  function tooltips() {
    let tip = null, on = null
    const show = (el) => {
      hide(); on = el
      tip = document.createElement('div')
      tip.textContent = el.getAttribute('data-tip')
      // Below the control, unless it's too near the bottom of the window (the settings gear).
      const r = el.getBoundingClientRect(), z = parseFloat(getComputedStyle(document.documentElement).zoom) || 1
      // Beside the rail's buttons; below anything else, or above it near the window's bottom.
      if (el.closest('.onb-rail') && window.innerWidth > 560) {
        tip.className = 'tooltip right'
        document.body.append(tip)
        tip.style.left = `${(r.right + 10) / z}px`
        tip.style.top = `${(r.top + r.height / 2) / z}px`
        return
      }
      const below = r.bottom + 44 < window.innerHeight
      tip.className = 'tooltip' + (below ? ' below' : '')
      document.body.append(tip)
      tip.style.left = `${(r.left + r.width / 2) / z}px`
      tip.style.top = `${(below ? r.bottom + 8 : r.top - 8) / z}px`
    }
    const hide = () => { tip?.remove(); tip = null; on = null }
    document.addEventListener('pointerover', (e) => { const el = e.target.closest?.('[data-tip]'); if (el && el !== on) show(el); else if (!el) hide() })
    document.addEventListener('focusin', (e) => { const el = e.target.closest?.('[data-tip]'); if (el && e.target.matches(':focus-visible')) show(el) })
    document.addEventListener('focusout', hide)
    document.addEventListener('pointerdown', hide)
  }

  // ── The rail: the logo, settings, and the docs ────────────────────────────────
  function Rail({ docsOpen, onDocs, onSettings }) {
    return html`
      <nav class="onb-rail" aria-label="Olisar">
        <img class="brand-logo" data-morph="logo" src=${window.ASSETS.logo} alt="Olisar" />
        <button class="ghost icon-btn" data-tip="Settings" aria-label="Settings" onClick=${onSettings}>${I.settings(18)}</button>
        <button class=${'ghost icon-btn' + (docsOpen ? ' on' : '')} data-tip=${docsOpen ? 'Close docs' : 'Docs'}
          aria-label=${docsOpen ? 'Close the docs' : 'Open the docs'} aria-expanded=${docsOpen} aria-controls="docs-panel" onClick=${onDocs}>
          <span class=${'swapglyph' + (docsOpen ? ' on' : '')}>${I.doc(18)}${I.back(18)}</span>
        </button>
      </nav>`
  }

  // The docs, sliding out from behind the rail: the whole docs site (docs/docs.html) with its
  // own section nav and search, minus the marketing bar. It's loaded the first time the drawer
  // opens. Closed, the drawer stays mounted (so it slides rather than pops) and inert, so
  // nothing in it can be tabbed to. A section id opens it at that page.
  function Docs({ section, onClose }) {
    const open = !!section
    const frame = useRef(null)
    const ready = useRef(false)
    useEffect(() => {
      if (!open) return
      const f = frame.current
      const go = () => { if (section !== 'top') f.contentWindow.location.hash = section }
      if (!f.srcdoc) {
        f.addEventListener('load', () => {
          ready.current = true
          // Escape closes the drawer from inside the docs too.
          f.contentDocument.addEventListener('keydown', (e) => { if (e.key === 'Escape') onClose() })
          go()
        }, { once: true })
        f.srcdoc = window.ASSETS.docs
      } else if (ready.current) go()
      const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('.modal-backdrop')) onClose() }
      document.addEventListener('keydown', onKey)
      return () => document.removeEventListener('keydown', onKey)
    }, [section])
    return html`
      <aside id="docs-panel" class=${'docs-panel' + (open ? ' open' : '')} aria-label="Docs" inert=${!open}>
        <iframe ref=${frame} class="docs-frame" title="Olisar docs"></iframe>
      </aside>`
  }

  function App() {
    const [docs, setDocs] = useState(null)          // the section showing, or null when closed
    const [settings, setSettings] = useState(null)  // { section, prefill } while open
    const openSettings = (section, prefill) => setSettings({ section, prefill })
    // Where setup finished to, once it finished to a server.
    const [server, setServer] = useState(() => (END === 'server' || END === 'brain'
      ? { host: '203.0.113.9', user: 'ubuntu', url: 'https://olisar.tail4f2a.ts.net', appId: MOCK_APP_ID } : null))
    const srv = useServer(!!server, END === 'brain')
    // The final screen: running, healthy, and the console's redirect listed. It waits a moment
    // before taking over, so the redirect's Added is seen; anything else takes it back at once.
    const wantBrain = !!server && srv.phase === 'running' && srv.listed
    const [view, setView] = useState(END === 'brain' ? 'brain' : 'stats')
    useEffect(() => {
      if (!wantBrain) { setView('stats'); return }
      const t = setTimeout(() => setView('brain'), 1200)
      return () => clearTimeout(t)
    }, [wantBrain])
    useEffect(() => { window.__brain.setTarget(view === 'brain' ? 1 : 0) }, [view])
    return html`
      <${Rail} docsOpen=${!!docs} onDocs=${() => setDocs((d) => (d ? null : 'top'))} onSettings=${() => openSettings('general')} />
      <${Docs} section=${docs} onClose=${() => setDocs(null)} />
      <${SetupWizard} openSettings=${openSettings} openDocs=${(s) => setDocs(s)} srv=${srv} onServer=${setServer} />
      <div class="onb-stage" aria-hidden="true"></div>
      <div class="orb-layer" aria-hidden="true"><canvas id="form"></canvas></div>
      ${server && html`
        <div class="orb-face" aria-hidden="true"><img src=${window.ASSETS.bot} alt="" /></div>
        <div class="orb-name" aria-hidden="true">${BOT_NAME}</div>
        <${BrainHud} srv=${srv} openSettings=${openSettings} />
        <${Memories} />
        <div class="orb-css" aria-hidden="true"></div>
        <${PreviewStrip} srv=${srv} />`}
      ${settings && html`<${SettingsStub} section=${settings.section} prefill=${settings.prefill} onClose=${() => setSettings(null)} />`}`
  }

  function start() {
    const root = document.getElementById('onb')
    // The form is decoration: without WebGL2 the wizard takes the whole window (and the final
    // screen draws its memories as rings). `?nogl` shows that.
    const probe = document.createElement('canvas')
    let ok = false
    try { ok = !!probe.getContext('webgl2') && !params.has('nogl') } catch { ok = false }
    if (!ok) root.classList.add('no-form')
    window.__brain = window.Brain.createBrain({ activity: window.Brain.createActivity(), open: END === 'brain' })
    render(html`<${App} />`, root)
    tooltips()
    const f = ok ? window.createForm(document.getElementById('form')) : null
    if (ok && !f) root.classList.add('no-form')
    if (f) {
      window.__form = f
      if (END === 'brain') f.set(8, 1)
      else f.set(0, 0)
    }
    window.__brain.attach(f)
    f?.start()
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start)
  else start()
})()
