// Olisar's release versions — a port of olisar/versioning.py (and desktop/updater.js);
// keep the three in step. Stable is "2.0", a beta leading up to it is "2.0.beta-1", the
// app reports itself as "2.0.0-beta.1", and releases before 2.0 had three numbers ("1.5.0").

const VERSION_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.-]?(?:beta|b)[.-]?(\d+))?$/i

type Parsed = { major: number; minor: number; patch: number; beta: number | null }

function parse(v: string | undefined | null): Parsed | null {
  const m = VERSION_RE.exec(String(v || '').trim())
  if (!m) return null
  return { major: +m[1], minor: +m[2], patch: +(m[3] || 0), beta: m[4] ? +m[4] : null }
}

/** A beta sorts below the stable release it leads up to: 2.0.beta-9 < 2.0. */
function sortKey(v: string | undefined | null): number[] {
  const p = parse(v)
  if (!p) {
    const nums = (String(v || '').match(/\d+/g) || []).slice(0, 3).map(Number)
    while (nums.length < 3) nums.push(0)
    return [...nums, 1, 0]
  }
  return p.beta === null ? [p.major, p.minor, p.patch, 1, 0] : [p.major, p.minor, p.patch, 0, p.beta]
}

export function isNewer(remote: string | undefined | null, local: string | undefined | null): boolean {
  const a = sortKey(remote)
  const b = sortKey(local)
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i]
  return false
}

export function isBeta(v: string | undefined | null): boolean {
  const p = parse(v)
  return !!(p && p.beta !== null)
}

/** How a person reads a version, without the leading "v": "2.0.0-beta.1" → "2.0.beta-1".
 *  Version strings arrive tagged ("v2.0") from the server's image labels and the releases
 *  API, so the one `v` we render is always our own. */
export function displayVersion(v: string | undefined | null): string {
  const p = parse(v)
  if (!p) return String(v || '').trim().replace(/^v/i, '')
  let base = `${p.major}.${p.minor}`
  if (p.patch || p.major < 2) base += `.${p.patch}`
  return p.beta === null ? base : `${base}.beta-${p.beta}`
}
