// Dev only: a stand-in for the desktop app's preload bridge (desktop/preload.js), so the update
// screen (src/updating.tsx) can be seen in a browser. main.tsx loads it under `vite` when the
// address has `?update`, and it plays an install the way desktop/updater.js reports one.
//
//   ?update           macOS: download, unpack, shut down, restart, then reopen with the toast
//   ?update=windows   Windows' steps, which have no unpack
//   ?update=failed    the download fails partway
//   ?update=stable    reopened on a stable release, with its What's new card (src/whatsnew.tsx):
//                     the newest card in src/whats-new/, or `&whatsnew=2.1` for that one. Closing
//                     the card keeps it closed for the rest of the tab's session
//
// Cancel, Try again and Back to Olisar all work. `olisarMockUpdate()` in the console starts
// another, e.g. after editing a page, to see the unsaved-changes warning.

import { isNewer } from '../src/version'

type Phase = 'download' | 'unpack' | 'shutdown' | 'restart' | 'failed'
type Progress = { phase: Phase; received?: number; [k: string]: unknown }
type Result = { ok: boolean; reason?: string }

export function installDesktopMock(): void {
  const mode = new URLSearchParams(location.search).get('update') || 'mac'
  const TOTAL = 158.4 * 1024 * 1024
  const base = {
    steps: mode === 'windows' ? ['download', 'shutdown', 'restart'] : ['download', 'unpack', 'shutdown', 'restart'],
    version: '2.0.beta-5', current: '2.0.beta-4', total: TOTAL,
  }
  // The steps after the download, and how long each takes here.
  const after: [Phase, number][] = [
    ...(mode === 'windows' ? [] : [['unpack', 2500] as [Phase, number]]),
    ['shutdown', 3000], ['restart', 1500],
  ]

  let progress: Progress | null = null
  let listeners: ((p: Progress | null) => void)[] = []
  let timer: ReturnType<typeof setTimeout> | undefined
  let settle: ((r: Result) => void) | null = null
  let told = false
  // Just reopened on the new version, rather than about to install one.
  const reopened = mode === 'done' || mode === 'stable'
  const WN_CLOSED = 'olisar.mock.whatsNewClosed'
  const cards = Object.keys(import.meta.glob('../src/whats-new/*.json'))
    .map((p) => p.slice(p.lastIndexOf('/') + 1, -'.json'.length))
  const card = new URLSearchParams(location.search).get('whatsnew')
    || cards.reduce((a, b) => (isNewer(b, a) ? b : a), cards[0] || '2.0')

  const push = (p: Progress | null) => { progress = p; listeners.forEach((f) => f(p)) }
  const finish = (r: Result) => { settle?.(r); settle = null }

  function download(received: number) {
    if (mode === 'failed' && received > TOTAL * 0.13) {
      push({
        ...base, phase: 'failed', failedAt: 'download', received,
        error: "Couldn't reach GitHub (getaddrinfo ENOTFOUND objects.githubusercontent.com).",
        downloadUrl: 'https://github.com/gcrft123/olisar/releases/latest',
      })
      return finish({ ok: false, reason: 'failed' })
    }
    if (received >= TOTAL) return step(0)
    push({ ...base, phase: 'download', received })
    timer = setTimeout(() => download(Math.min(TOTAL, received + TOTAL / 70)), 120)
  }
  function step(i: number) {
    // The app quits here, and opens again on the new version.
    if (i === after.length) return location.replace('?update=done')
    const [phase, ms] = after[i]
    push({ ...base, phase, received: TOTAL })
    timer = setTimeout(() => step(i + 1), ms)
  }
  function start(): Promise<Result> {
    clearTimeout(timer)
    finish({ ok: false, reason: 'busy' })
    download(0)
    return new Promise((r) => { settle = r })
  }

  const available = { available: { version: '2.0.beta-5', hasInstaller: true }, canSelfUpdate: true }
  ;(window as any).olisar = {
    desktop: true,
    platform: mode === 'windows' ? 'win32' : 'darwin',
    updates: {
      state: async () => ({ ...available, installing: !!progress && progress.phase !== 'failed', progress }),
      check: async () => ({ ...available, installing: !!progress && progress.phase !== 'failed' }),
      install: start,
      cancel: async () => {
        if (progress?.phase !== 'download') return
        clearTimeout(timer)
        push(null)
        finish({ ok: false, reason: 'cancelled' })
      },
      dismiss: async () => { if (progress?.phase === 'failed') push(null) },
      // Once, like the app: StrictMode asks twice.
      justUpdated: async () => {
        if (!reopened || told) return null
        told = true
        return mode === 'stable' ? { from: `${card}.beta-5`, to: card } : { from: '2.0.beta-4', to: '2.0.beta-5' }
      },
      whatsNew: async () => (mode === 'stable' && !sessionStorage.getItem(WN_CLOSED) ? card : null),
      closeWhatsNew: async () => { sessionStorage.setItem(WN_CLOSED, '1') },
      onProgress: (fn: (p: Progress | null) => void) => {
        listeners.push(fn)
        return () => { listeners = listeners.filter((f) => f !== fn) }
      },
    },
  }
  ;(window as any).olisarMockUpdate = () => { void start() }
  // A moment of the console first, so the takeover is seen.
  if (!reopened) setTimeout(() => { void start() }, 1200)
}
