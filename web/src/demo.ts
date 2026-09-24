// A demo build (`npm run demo`, into dist-demo/) answers the API in the browser from the dev fixture
// (mock/fixture.ts), so the console can be published as static files with nothing behind it:
// no backend, no database, no Discord. `?demo=` picks the state it opens in. A request the
// fixture doesn't cover is a 404, never a network call.
import { configureMock, handle, type MockEnv } from '../mock/fixture'

const STATES: { id: string; label: string; env: MockEnv }[] = [
  // The wizard, then the console just after setup. Deploying to a server lands on a control
  // panel whose bot Discord refuses, so its fix shows too.
  { id: 'setup', label: 'Setup', env: { setup: 'intents', fresh: '1' } },
  { id: 'console', label: 'After setup', env: { fresh: '1' } },
  { id: 'refused', label: 'Bot refused', env: { fresh: 'refused' } },
  { id: 'admin', label: 'Server admin', env: { fresh: '1', role: 'admin' } },
]

export function installDemo(): void {
  const asked = new URLSearchParams(location.search).get('demo')
  const state = STATES.find((s) => s.id === asked) ?? STATES[0]
  configureMock(state.env)

  const passThrough = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(raw, location.href)
    if (url.origin !== location.origin || !/^\/(api|auth)\//.test(url.pathname)) return passThrough(input, init)
    const body = typeof init?.body === 'string' ? init.body : ''
    return new Promise<Response>((resolve) => {
      // What the fixture reads off a Node request: the method, headers, and the body as
      // one 'data' event and then 'end'.
      const listeners: Record<string, (chunk?: string) => void> = {}
      const req = {
        method: (init?.method || 'GET').toUpperCase(),
        headers: { host: location.host, origin: location.origin },
        on(event: string, cb: (chunk?: string) => void) {
          listeners[event] = cb
          if (event === 'end') setTimeout(() => { listeners.data?.(body); listeners.end?.() }, 0)
          return req
        },
      }
      const send = (obj: unknown, status = 200) => resolve(new Response(JSON.stringify(obj), {
        status, headers: { 'Content-Type': 'application/json' },
      }))
      handle(req, url.pathname + url.search, send, () => send({ detail: 'Not part of the demo.' }, 404))
    })
  }

  showStates(state.id)
}

// A strip in the corner for moving between the states, since a reload starts the one you're
// in over and nothing inside the console leads to the others.
function showStates(current: string): void {
  const bar = document.createElement('nav')
  bar.setAttribute('aria-label', 'Demo states')
  bar.style.cssText = [
    'position:fixed', 'left:12px', 'bottom:12px', 'z-index:1000', 'display:flex', 'gap:2px',
    'align-items:center', 'padding:4px', 'border-radius:10px', 'font:500 12px/1 var(--font-sans, system-ui)',
    'background:var(--panel, #111)', 'border:1px solid var(--border-strong, #333)',
    'box-shadow:var(--shadow-pop, none)',
  ].join(';')
  const tag = document.createElement('span')
  tag.textContent = 'Demo'
  tag.style.cssText = 'padding:6px 8px;color:var(--text-3, #888);text-transform:uppercase;letter-spacing:.04em;font-size:11px'
  bar.append(tag)
  for (const s of STATES) {
    const a = document.createElement('a')
    a.href = `?demo=${s.id}`
    a.textContent = s.label
    const on = s.id === current
    a.style.cssText = `padding:6px 9px;border-radius:7px;text-decoration:none;color:${on ? 'var(--text, #fff)' : 'var(--text-2, #aaa)'};background:${on ? 'var(--bg-inset, #222)' : 'transparent'}`
    if (on) a.setAttribute('aria-current', 'page')
    bar.append(a)
  }
  document.body.append(bar)
}
