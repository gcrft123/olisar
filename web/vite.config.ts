import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    // In dev, proxy API + auth to the FastAPI server so the browser sees a
    // single origin (:5173). This keeps the OAuth cookie/redirect flow simple —
    // leave VITE_API_BASE empty so the app calls same-origin /api and /auth.
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
