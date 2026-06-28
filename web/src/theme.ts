// Accent color — a per-browser preference applied to the dashboard's CSS variables.
// Kept client-side (localStorage) so it applies instantly with no flash and needs no
// backend round-trip. Changing it updates --accent and the derived --accent-soft live.

const KEY = 'olisar_accent'
export const DEFAULT_ACCENT = '#5b9cf6'

export const ACCENTS: { name: string; value: string }[] = [
  { name: 'Violet', value: '#8a8af2' },
  { name: 'Iris', value: '#7c6cf0' },
  { name: 'Blue', value: '#5b9cf6' },
  { name: 'Teal', value: '#2dd4bf' },
  { name: 'Green', value: '#43cf8e' },
  { name: 'Amber', value: '#e0a458' },
  { name: 'Rose', value: '#f2728a' },
  { name: 'Red', value: '#ff6369' },
]

export function getAccent(): string {
  try {
    return localStorage.getItem(KEY) || DEFAULT_ACCENT
  } catch {
    return DEFAULT_ACCENT
  }
}

function softFrom(hex: string, alpha = 0.16): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return `rgba(138, 138, 242, ${alpha})`
  const n = parseInt(m[1], 16)
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function applyAccent(hex?: string): void {
  const c = hex || getAccent()
  const root = document.documentElement
  root.style.setProperty('--accent', c)
  root.style.setProperty('--accent-soft', softFrom(c))
  // The ambient background blobs are tinted with the accent too.
  root.style.setProperty('--glow-a', softFrom(c, 0.18))
  root.style.setProperty('--glow-b', softFrom(c, 0.10))
}

export function setAccent(hex: string): void {
  try { localStorage.setItem(KEY, hex) } catch { /* private mode — apply for the session */ }
  applyAccent(hex)
}
