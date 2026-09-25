import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { configureMock, handle } from './mock/fixture'

// ── Dev-only fixture ─────────────────────────────────────────────────────────
// `USAGE_MOCK=1 npm run dev` serves canned API responses for the whole console from
// mock/fixture.ts. Off by default (normal dev proxies to :8000, prod is unaffected).
// SETUP_MOCK, FRESH_MOCK and MOCK_ROLE pick which state it starts in (see MockEnv).
const MOCK = !!process.env.USAGE_MOCK

function mockPlugin(): Plugin {
  configureMock({ setup: process.env.SETUP_MOCK, fresh: process.env.FRESH_MOCK, role: process.env.MOCK_ROLE })
  return {
    name: 'olisar-usage-mock',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        handle(req, req.url || '', (obj, status = 200) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(obj))
        }, next)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), ...(MOCK ? [mockPlugin()] : [])],
  server: {
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    // In dev, proxy API + auth to the FastAPI server so the browser sees a
    // single origin (:5173). This keeps the OAuth cookie/redirect flow simple —
    // leave VITE_API_BASE empty so the app calls same-origin /api and /auth.
    // With USAGE_MOCK the mock plugin answers /api itself, so skip the proxy.
    proxy: MOCK ? undefined : {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
