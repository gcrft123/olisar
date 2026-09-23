// GitHub Releases updater for the Olisar desktop app.
//
// Olisar doesn't use Squirrel/electron-updater — it applies updates itself: on macOS it
// downloads the release .dmg, mounts it, and a detached script swaps the new Olisar.app over
// the running one and relaunches; on Windows it downloads the NSIS installer and runs it
// (which closes the app, installs over it, and relaunches). Non-packaged / unsupported builds
// fall back to opening the installer download.
//
// The macOS .app inside the .dmg is signed and notarized with its ticket stapled (see
// RELEASING.md), so the copy this lands in /Applications validates on its own — no online
// check, no Gatekeeper prompt.
//
// It follows the install's update channel: stable reads GitHub's latest release (never a
// pre-release), beta takes the newest release of either kind. The channel is chosen in the
// dashboard and stored by the backend in updates.json in the data directory, which is this
// app's userData (main.js hands it over as OLISAR_DATA_DIR), so it's read fresh from there
// before every check. See olisar/updates.py.

const https = require('https')
const fs = require('fs')
const path = require('path')
const os = require('os')
const { spawn, execFile } = require('child_process')
const { app, dialog, shell, Notification } = require('electron')

const REPO = 'gcrft123/olisar'
const LATEST_URL = `https://api.github.com/repos/${REPO}/releases/latest`
const RELEASES_URL = `https://api.github.com/repos/${REPO}/releases?per_page=30`
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`

let available = null        // the newest update found, or null
let notifiedVersion = null  // suppress repeat background prompts for the same version
let installing = false      // an update download/swap is in progress
let getMainWindow = () => null

// main.js calls this so the updater can show download progress on the dock/taskbar icon.
function init(opts = {}) {
  if (typeof opts.getMainWindow === 'function') getMainWindow = opts.getMainWindow
}

// ── fetch + version compare ─────────────────────────────────────────────────

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

// ── versions (a port of olisar/versioning.py; keep them in step) ─────────────
// Stable is "2.0", a beta leading up to it is "2.0.beta-1", and this app reports itself in
// the semver spelling its package.json needs ("2.0.0-beta.1"). Releases before 2.0 had three
// numbers ("1.5.0").

const VERSION_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.-]?(?:beta|b)[.-]?(\d+))?$/i

function parseVersion(v) {
  const m = VERSION_RE.exec(String(v || '').trim())
  if (!m) return null
  return { major: +m[1], minor: +m[2], patch: +(m[3] || 0), beta: m[4] ? +m[4] : null }
}

// A beta sorts below the stable release it leads up to: 2.0.beta-9 < 2.0.
function sortKey(v) {
  const p = parseVersion(v)
  if (!p) {
    const nums = (String(v || '').match(/\d+/g) || []).slice(0, 3).map(Number)
    while (nums.length < 3) nums.push(0)
    return [...nums, 1, 0]
  }
  return p.beta === null ? [p.major, p.minor, p.patch, 1, 0] : [p.major, p.minor, p.patch, 0, p.beta]
}

function isNewer(remote, local) {
  const a = sortKey(remote)
  const b = sortKey(local)
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i]
  return false
}

function isBeta(v) {
  const p = parseVersion(v)
  return !!(p && p.beta !== null)
}

// "2.0.0-beta.1" -> "2.0.beta-1", "2.0.0" -> "2.0", "1.5.0" stays "1.5.0".
function displayVersion(v) {
  const p = parseVersion(v)
  if (!p) return String(v || '').trim().replace(/^v/i, '')
  let base = `${p.major}.${p.minor}`
  if (p.patch || p.major < 2) base += `.${p.patch}`
  return p.beta === null ? base : `${base}.beta-${p.beta}`
}

// The install's update channel. With no choice saved, a beta build follows beta: the first
// beta has to be installed by hand, and that should be all it takes to join.
function channel() {
  try {
    const saved = JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), 'updates.json'), 'utf8')).channel
    if (saved === 'stable' || saved === 'beta') return saved
  } catch { /* no choice saved yet */ }
  return isBeta(app.getVersion()) ? 'beta' : 'stable'
}

// The newest release `ch` should offer. Stable also refuses a beta-shaped tag that wasn't
// flagged as a pre-release, so a hand-published beta can't reach every stable install.
function pickRelease(releases, ch) {
  let best = null
  for (const rel of releases || []) {
    const tag = String((rel && rel.tag_name) || '').trim()
    if (!rel || rel.draft || !parseVersion(tag)) continue
    if (ch !== 'beta' && (rel.prerelease || isBeta(tag))) continue
    if (!best || isNewer(tag, best.tag_name)) best = rel
  }
  return best
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
  const ch = channel()
  const data = await getJson(ch === 'beta' ? RELEASES_URL : LATEST_URL)
  const rel = pickRelease(Array.isArray(data) ? data : data ? [data] : [], ch)
  if (!rel) return null
  const tag = rel.tag_name.trim()
  if (!isNewer(tag, app.getVersion())) return null
  const asset = assetForPlatform(rel)
  return {
    version: displayVersion(tag),
    downloadUrl: asset ? asset.browser_download_url : (rel.html_url || RELEASES_PAGE),
    pageUrl: rel.html_url || RELEASES_PAGE,
    hasInstaller: !!asset,
  }
}

// ── public API ──────────────────────────────────────────────────────────────

function getAvailableUpdate() { return available }
function isInstalling() { return installing }
function openDownload() { shell.openExternal(available ? available.downloadUrl : RELEASES_PAGE) }

// Whether this build can apply an update itself (vs. just opening the download). macOS
// swaps the .app bundle; Windows runs the NSIS installer (which replaces + relaunches).
function canSelfUpdate() {
  if (!app.isPackaged) return false
  if (process.platform === 'darwin') return !!currentAppPath()
  if (process.platform === 'win32') return true
  return false
}

// Check the latest release. `interactive` = user-triggered (always shows a dialog);
// otherwise a quiet background poll (toast once per version). Never throws.
async function checkForUpdates({ interactive = false } = {}) {
  if (installing) return available
  let update = null
  try {
    update = await fetchUpdate()
  } catch (err) {
    if (interactive) {
      dialog.showMessageBox({ type: 'warning', message: 'Could not check for updates', detail: String(err && err.message ? err.message : err), buttons: ['OK'] })
    }
    return available
  }
  available = update
  if (!update) {
    if (interactive) {
      dialog.showMessageBox({ type: 'info', message: "You're up to date", detail: `Olisar ${displayVersion(app.getVersion())} is the latest ${channel() === 'beta' ? 'beta' : 'version'}.`, buttons: ['OK'] })
    }
    return null
  }
  if (interactive) promptInstall(update)
  else if (notifiedVersion !== update.version) { notifiedVersion = update.version; toastUpdate(update) }
  return update
}

function promptInstall(update) {
  if (canSelfUpdate() && update.hasInstaller) {
    dialog
      .showMessageBox({
        type: 'info',
        buttons: ['Install & Restart', 'Later'],
        defaultId: 0,
        cancelId: 1,
        message: `Olisar ${update.version} is available`,
        detail: `You're on ${displayVersion(app.getVersion())}. Olisar can download it and restart into the new version.`,
      })
      .then(({ response }) => { if (response === 0) installUpdate(update) })
  } else {
    dialog
      .showMessageBox({
        type: 'info',
        buttons: ['Download', 'Later'],
        defaultId: 0,
        cancelId: 1,
        message: `Olisar ${update.version} is available`,
        detail: `You're on ${displayVersion(app.getVersion())}. Download the new installer to update.`,
      })
      .then(({ response }) => { if (response === 0) shell.openExternal(update.downloadUrl) })
  }
}

function toastUpdate(update) {
  if (!Notification.isSupported()) return
  const selfUpdate = canSelfUpdate() && update.hasInstaller
  const n = new Notification({
    title: `Olisar ${update.version} is available`,
    body: selfUpdate ? 'Click to install and restart.' : 'Click to download the update.',
  })
  n.on('click', () => { if (selfUpdate) installUpdate(update); else shell.openExternal(update.downloadUrl) })
  n.show()
}

// ── in-place install (macOS) ────────────────────────────────────────────────

// /…/Olisar.app/Contents/MacOS/Olisar -> /…/Olisar.app
function currentAppPath() {
  const exe = process.execPath || ''
  const i = exe.indexOf('.app/')
  return i === -1 ? null : exe.slice(0, i + 4)
}

function setProgress(fraction) {
  const win = getMainWindow()
  if (win && !win.isDestroyed()) win.setProgressBar(fraction)
}

function execFileP(cmd, args) {
  return new Promise((resolve, reject) =>
    execFile(cmd, args, { maxBuffer: 1 << 20 }, (err, stdout) => (err ? reject(err) : resolve(stdout))))
}

function downloadFile(url, dest, onProgress, redirects = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'Olisar-Updater' }, timeout: 60000 }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 5) {
        res.resume()
        return resolve(downloadFile(res.headers.location, dest, onProgress, redirects + 1))
      }
      if (res.statusCode !== 200) { res.resume(); return reject(new Error(`download HTTP ${res.statusCode}`)) }
      const total = parseInt(res.headers['content-length'] || '0', 10)
      let got = 0
      const file = fs.createWriteStream(dest)
      res.on('data', (c) => { got += c.length; if (total && onProgress) onProgress(got / total) })
      res.pipe(file)
      file.on('finish', () => file.close(() => resolve(dest)))
      file.on('error', (e) => { try { fs.unlinkSync(dest) } catch { /* ignore */ } reject(e) })
    })
    req.on('error', reject)
    req.on('timeout', () => req.destroy(new Error('download timed out')))
  })
}

// The detached script that, once this process exits, swaps the new app over the old one
// and relaunches. Backs up the old bundle and rolls back if the move fails.
function swapScript({ pid, newApp, target, staging, tmpRoot }) {
  const q = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`
  return `#!/bin/bash
PID=${pid}
NEW=${q(newApp)}
TARGET=${q(target)}
STAGING=${q(staging)}
TMP=${q(tmpRoot)}
# wait (up to ~60s) for the running app to exit
for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
sleep 1
if [ -d "$NEW" ]; then
  BACKUP="$TARGET.old-$$"
  if mv "$TARGET" "$BACKUP" 2>/dev/null; then
    if mv "$NEW" "$TARGET" 2>/dev/null; then
      xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
      rm -rf "$BACKUP" 2>/dev/null || true
    else
      mv "$BACKUP" "$TARGET" 2>/dev/null || true
    fi
  fi
fi
rm -rf "$STAGING" 2>/dev/null || true
rm -rf "$TMP" 2>/dev/null || true
open "$TARGET" 2>/dev/null || true
`
}

async function installUpdate(update) {
  if (installing) return
  if (!canSelfUpdate() || !update || !update.hasInstaller) { shell.openExternal(update ? update.downloadUrl : RELEASES_PAGE); return }

  installing = true
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'olisar-upd-'))
  try {
    if (Notification.isSupported()) new Notification({ title: 'Updating Olisar', body: `Downloading ${update.version}…` }).show()
    setProgress(0)
    if (process.platform === 'win32') await _applyWindows(update, tmpRoot)
    else await _applyMac(update, tmpRoot)
    setProgress(-1)
    app.isQuitting = true
    app.quit() // before-quit stops the backend; the installer/script then swaps + relaunches
  } catch (err) {
    installing = false
    setProgress(-1)
    try { fs.rmSync(tmpRoot, { recursive: true, force: true }) } catch { /* ignore */ }
    dialog
      .showMessageBox({
        type: 'error',
        buttons: ['Open Download', 'Close'],
        defaultId: 0,
        cancelId: 1,
        message: 'Update failed',
        detail: `${String(err && err.message ? err.message : err)}\n\nYou can download ${update.version} manually instead.`,
      })
      .then(({ response }) => { if (response === 0) shell.openExternal(update.downloadUrl) })
  }
}

// Windows: download the NSIS installer and run it. electron-builder's installer closes the
// running app, installs over it, and relaunches. The temp file is left in place because the
// installer executes from it (the OS reaps temp later).
async function _applyWindows(update, tmpRoot) {
  const installer = path.join(tmpRoot, 'OlisarSetup.exe')
  await downloadFile(update.downloadUrl, installer, setProgress)
  setProgress(2)
  spawn(installer, [], { detached: true, stdio: 'ignore' }).unref()
}

// macOS: download the .dmg, mount it, stage the new .app on the target volume, and hand a
// detached script the swap + relaunch. Copying out of the mounted image preserves the app's
// signature and stapled notarization ticket, so the swapped-in bundle is as valid as the one
// a user would have dragged across by hand.
async function _applyMac(update, tmpRoot) {
  const appPath = currentAppPath()
  if (!appPath) throw new Error('could not locate the app bundle')
  const dmgPath = path.join(tmpRoot, 'Olisar.dmg')
  const mountPoint = path.join(tmpRoot, 'mnt')
  const staging = path.join(path.dirname(appPath), `.olisar-update-${Date.now()}`)
  let mounted = false
  try {
    await downloadFile(update.downloadUrl, dmgPath, setProgress)
    setProgress(2) // indeterminate while we swap
    fs.mkdirSync(mountPoint, { recursive: true })
    await execFileP('hdiutil', ['attach', dmgPath, '-nobrowse', '-noverify', '-mountpoint', mountPoint])
    mounted = true
    const srcApp = path.join(mountPoint, 'Olisar.app')
    if (!fs.existsSync(srcApp)) throw new Error('Olisar.app not found in the downloaded image')
    fs.mkdirSync(staging, { recursive: true })
    await execFileP('cp', ['-R', srcApp, staging]) // staging/Olisar.app
    await execFileP('hdiutil', ['detach', mountPoint, '-force']).catch(() => {})
    mounted = false
    const newApp = path.join(staging, 'Olisar.app')
    if (!fs.existsSync(newApp)) throw new Error('failed to stage the new app')
    const scriptPath = path.join(tmpRoot, 'swap.sh')
    fs.writeFileSync(scriptPath, swapScript({ pid: process.pid, newApp, target: appPath, staging, tmpRoot }), { mode: 0o755 })
    spawn('/bin/bash', [scriptPath], { detached: true, stdio: 'ignore' }).unref()
  } catch (err) {
    if (mounted) { try { await execFileP('hdiutil', ['detach', mountPoint, '-force']) } catch { /* ignore */ } }
    try { fs.rmSync(staging, { recursive: true, force: true }) } catch { /* ignore */ }
    throw err
  }
}

module.exports = { init, checkForUpdates, getAvailableUpdate, openDownload, installUpdate, isInstalling, canSelfUpdate, displayVersion }
// Exported for unit tests only.
module.exports._internal = { isNewer, parseVersion, pickRelease, assetForPlatform, swapScript, currentAppPath, downloadFile }
