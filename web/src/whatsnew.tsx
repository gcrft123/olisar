import { useEffect, useState } from 'react'
import { CloseX } from './icons'
import { displayVersion, isBeta } from './version'

// ── What's new ──────────────────────────────────────────────────────────────────
// The first time the desktop app opens on a new stable release, a card in the bottom-left
// corner says what came with it: the release's banner, the few changes worth knowing about,
// and a link to the full notes. It stays until its × closes it, across restarts too. The shell
// (desktop/main.js) keeps which release is waiting; this file shows it.
//
// Each release's card is two files in whats-new/, named for the release as people read it:
// 2.1.webp, the 16:9 banner at 1280×720, and 2.1.json, its alt text and points. Nothing here
// changes to add one. `scripts/whats_new.py add` makes both from a poster, and the release
// workflow won't build a stable release without them (`whats_new.py check`, which also holds
// the rules they have to meet). A beta gets the "Updated to v…" toast instead (updating.tsx).

type Card = {
  /** What the banner shows, including any words set in it. */
  alt: string
  /** Two or three, a line or two each at the card's width. */
  points: string[]
}
type Notes = Card & { banner: string }

const cards = import.meta.glob<Card>('./whats-new/*.json', { eager: true, import: 'default' })
const banners = import.meta.glob<string>('./whats-new/*.webp', { eager: true, import: 'default' })

// Keyed by the release as people read it ("2.0", not "2.0.0").
const NOTES: Record<string, Notes | undefined> = {}
for (const [path, card] of Object.entries(cards)) {
  const version = path.slice('./whats-new/'.length, -'.json'.length)
  const banner = banners[`./whats-new/${version}.webp`]
  if (banner) NOTES[version] = { ...card, banner }
}

const releasePage = (v: string) => `https://github.com/gcrft123/olisar/releases/tag/v${v}`

/** Whether a release gets a card, so the update toast can leave the news to it. */
export function hasWhatsNew(version: string): boolean {
  return !isBeta(version) && !!NOTES[displayVersion(version)]
}

type Bridge = {
  whatsNew?: () => Promise<string | null>
  closeWhatsNew?: () => Promise<void>
}
const bridge = () => (window as any).olisar?.updates as Bridge | undefined

/** Matches the .14s exit in index.css. */
const EXIT_MS = 140

export function WhatsNew() {
  const [version, setVersion] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)

  useEffect(() => {
    let live = true
    bridge()?.whatsNew?.().then(async (v) => {
      const notes = v ? NOTES[v] : undefined
      if (!notes) return
      // After the page, and with the banner decoded, so the card doesn't arrive with a
      // blank banner and fill in.
      const img = new Image()
      img.src = notes.banner
      await Promise.all([img.decode().catch(() => {}), new Promise((r) => setTimeout(r, 600))])
      if (live) setVersion(v)
    }).catch(() => {})
    return () => { live = false }
  }, [])

  if (!version) return null
  const notes = NOTES[version]!
  const close = () => {
    if (closing) return
    void bridge()?.closeWhatsNew?.()
    setClosing(true)
    setTimeout(() => setVersion(null), EXIT_MS)
  }

  // Not a dialog: it opens on its own rather than from anything pressed, so it takes no focus
  // and holds none. It sits after the page in the tab order, and Escape closes it from inside.
  return (
    <aside
      className={'wn' + (closing ? ' closing' : '')}
      aria-labelledby="wn-title"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); close() } }}
    >
      <button className="ghost icon-btn sm wn-close" data-tip="Close" aria-label="Close what’s new" onClick={close}>
        <CloseX size={14} />
      </button>
      <img className="wn-banner" src={notes.banner} alt={notes.alt} width={1280} height={720} />
      <div className="wn-body">
        <h2 id="wn-title" className="wn-title">What’s new in v{version}</h2>
        <ul className="wn-points">
          {notes.points.map((p) => <li key={p}>{p}</li>)}
        </ul>
        <a className="wn-link" href={releasePage(version)} target="_blank" rel="noreferrer">
          Full changelog<span aria-hidden="true"> ↗</span>
        </a>
      </div>
    </aside>
  )
}
