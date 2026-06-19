// GitHub Releases update checker for the Olisar desktop app.
//
// Olisar ships UNSIGNED (v1), and macOS won't let an unsigned app silently replace
// itself, so this doesn't do Squirrel-style in-place auto-install. Instead it polls the
// repo's latest GitHub Release; when a newer version is cut it notifies the user (a
// native toast in the background, a dialog on an explicit check) and opens the right
// installer (.dmg / .exe) to download. The tray also exposes the available update.
//
// When the app is eventually code-signed, this can be swapped for electron-updater's
// full download-and-install flow (the `build.publish` config already points here).

const https = require('https')
const { app, dialog, shell, Notification } = require('electron')

const REPO = 'gcrft123/olisar'
const LATEST_URL = `https://api.github.com/repos/${REPO}/releases/latest`
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`

let available = null        // the newest update found, or null
let notifiedVersion = null  // suppress repeat background prompts for the same version

// ── helpers ───────────────────────────────────────────────────────────────────

function getJson(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      url,
      { headers: { 'User-Agent': 'Olisar-Updater', Accept: 'application/vnd.github+json' }, timeout: 8000 },
      (res) => {
        if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 3) {
          res.resume()
          return resolve(getJson(res.headers.location, redirects + 1))
        }
        if (res.statusCode === 404) { res.resume(); return resolve(null) } // no releases yet
        if (res.statusCode !== 200) { res.resume(); return reject(new Error(`GitHub API HTTP ${res.statusCode}`)) }
        let body = ''
        res.on('data', (c) => (body += c))
        res.on('end', () => { try { resolve(JSON.parse(body)) } catch (e) { reject(e) } })
      },
    )
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
  })
}

// Compare dotted numeric versions (ignoring any leading "v" and pre-release suffix).
// Returns true when `remote` is strictly newer than `local`.
function isNewer(remote, local) {
  const parse = (v) => String(v || '').replace(/^v/i, '').split('-')[0].split('.').map((n) => parseInt(n, 10) || 0)
  const a = parse(remote)
  const b = parse(local)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0
    const y = b[i] || 0
    if (x !== y) return x > y
  }
  return false
}

// Pick the installer asset for this platform/arch from a release's assets.
function assetForPlatform(release) {
  const assets = release.assets || []
  const ext = process.platform === 'win32' ? '.exe' : process.platform === 'darwin' ? '.dmg' : '.appimage'
  const matches = assets.filter((a) => (a.name || '').toLowerCase().endsWith(ext))
  if (process.platform === 'darwin') {
    const arch = process.arch === 'arm64' ? 'arm64' : 'x64'
    const byArch = matches.find((a) => (a.name || '').toLowerCase().includes(arch))
    if (byArch) return byArch
  }
  return matches[0] || null
}

async function fetchUpdate() {
  const rel = await getJson(LATEST_URL)
  if (!rel || rel.draft || rel.prerelease) return null
  const tag = rel.tag_name || rel.name || ''
  if (!isNewer(tag, app.getVersion())) return null
  const asset = assetForPlatform(rel)
  return {
    version: String(tag).replace(/^v/i, ''),
    downloadUrl: asset ? asset.browser_download_url : (rel.html_url || RELEASES_PAGE),
    pageUrl: rel.html_url || RELEASES_PAGE,
    hasInstaller: !!asset,
  }
}

// ── public API ────────────────────────────────────────────────────────────────

function getAvailableUpdate() {
  return available
}

function openDownload() {
  shell.openExternal(available ? available.downloadUrl : RELEASES_PAGE)
}

// Check the latest release. `interactive` = triggered by the user (show a dialog and an
// "up to date" confirmation); otherwise it's a quiet background poll (toast only, once
// per version). Returns the update (or null). Never throws.
async function checkForUpdates({ interactive = false } = {}) {
  let update = null
  try {
    update = await fetchUpdate()
  } catch (err) {
    if (interactive) {
      dialog.showMessageBox({
        type: 'warning',
        message: 'Could not check for updates',
        detail: String(err && err.message ? err.message : err),
        buttons: ['OK'],
      })
    }
    return available  // keep any previously-known update
  }

  available = update

  if (!update) {
    if (interactive) {
      dialog.showMessageBox({
        type: 'info',
        message: "You're up to date",
        detail: `Olisar ${app.getVersion()} is the latest version.`,
        buttons: ['OK'],
      })
    }
    return null
  }

  if (interactive) {
    promptDownload(update)
  } else if (notifiedVersion !== update.version) {
    notifiedVersion = update.version
    toastUpdate(update)
  }
  return update
}

function promptDownload(update) {
  dialog
    .showMessageBox({
      type: 'info',
      buttons: ['Download', 'Later'],
      defaultId: 0,
      cancelId: 1,
      message: `Olisar ${update.version} is available`,
      detail: update.hasInstaller
        ? `You're on ${app.getVersion()}. Download the new installer, then replace the app to update.`
        : `You're on ${app.getVersion()}. Open the release page to download the new version.`,
    })
    .then(({ response }) => { if (response === 0) shell.openExternal(update.downloadUrl) })
}

function toastUpdate(update) {
  if (!Notification.isSupported()) return
  const n = new Notification({
    title: `Olisar ${update.version} is available`,
    body: 'Click to download the update.',
  })
  n.on('click', () => shell.openExternal(update.downloadUrl))
  n.show()
}

module.exports = { checkForUpdates, getAvailableUpdate, openDownload }
// Exported for unit tests only.
module.exports._internal = { isNewer, assetForPlatform }
