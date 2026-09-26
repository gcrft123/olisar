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
let progress = null         // what the window's update screen shows, or null when there's none
let abortDownload = null    // calls off the download, the one step that can be undone
let getMainWindow = () => null
let showWindow = () => {}   // main.js: show the dashboard window, creating it if it's gone
let stopBackend = async () => {}  // main.js: stop the backend (and every bot) and wait for it

// main.js calls this so the updater can show download progress on the dock/taskbar icon,
// bring the window forward for the update screen, and stop the backend before it quits.
function init(opts = {}) {
  if (typeof opts.getMainWindow === 'function') getMainWindow = opts.getMainWindow
  if (typeof opts.showWindow === 'function') showWindow = opts.showWindow
  if (typeof opts.stopBackend === 'function') stopBackend = opts.stopBackend
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
function getProgress() { return progress }
// Past the download there's no going back: the bots are stopping and the app is about to quit.
function isCommitted() { return !!progress && (progress.phase === 'shutdown' || progress.phase === 'restart') }
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

// ── the update screen ───────────────────────────────────────────────────────
// While an install runs, the window shows a full-screen account of it (web/src/updating.tsx)
// that covers the console so nothing in it can be changed underneath. It's fed from here: the
// steps this platform takes, which one is running, the download's bytes, and how it failed.
// A window that loads mid-update reads the same state through updates:state.

const STEPS = process.platform === 'win32'
  ? ['download', 'shutdown', 'restart']              // the installer unpacks, installs and relaunches
  : ['download', 'unpack', 'shutdown', 'restart']

let lastSent = 0
function report(next) {
  progress = next
  // Download chunks arrive thousands of times a second; the screen needs a few updates a second.
  const now = Date.now()
  const finished = next && next.total && next.received >= next.total
  if (next && next.phase === 'download' && next.received && !finished && now - lastSent < 120) return
  lastSent = now
  const win = getMainWindow()
  if (win && !win.isDestroyed()) win.webContents.send('updates:progress', progress)
}

function step(phase, extra = {}) {
  report({ ...progress, phase, ...extra })
}

// The screen's Cancel. Only the download can be called off; after it, the install is
// underway and stopping halfway would leave nothing to fall back on.
function cancelInstall() {
  if (progress && progress.phase === 'download' && abortDownload) abortDownload()
}

// The screen's way back to the console after a failure.
function dismissFailure() {
  if (progress && progress.phase === 'failed') report(null)
}

function execFileP(cmd, args) {
  return new Promise((resolve, reject) =>
    execFile(cmd, args, { maxBuffer: 1 << 20 }, (err, stdout, stderr) => {
      if (!err) return resolve(stdout)
      // The message repeats the whole command line; what went wrong is stderr's last line.
      err.detail = String(stderr || '').trim().split('\n').pop() || undefined
      reject(err)
    }))
}

// ── what went wrong, in words ───────────────────────────────────────────────
// The update screen shows a failure as a sentence someone can act on, with the system's own
// reason after it in brackets so it can still be quoted in a report. Errors this file raises
// itself are sentences already (`plain`); the rest are put into words here.

const plain = (message) => Object.assign(new Error(message), { plain: true })
const UNREACHABLE = new Set(['ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ENETUNREACH', 'EHOSTUNREACH'])

function describeFailure(err, phase) {
  const reason = String((err && (err.detail || err.message)) || err)
  if (err && err.plain) return reason
  if (err && err.status) {
    return err.status === 404
      ? "The download isn't on GitHub anymore (HTTP 404)."
      : `GitHub couldn't send the download (HTTP ${err.status}).`
  }
  if (err && err.code === 'ENOSPC') return "There isn't enough free disk space for the update."
  if (err && UNREACHABLE.has(err.code)) return `Couldn't reach GitHub (${reason}).`
  if (phase === 'unpack') return `Couldn't unpack the update (${reason}).`
  return `The download failed (${reason}).`
}

// `onProgress(received, total)`; total is 0 when the server doesn't say. Aborting `signal`
// rejects with an AbortError.
function downloadFile(url, dest, onProgress, signal, redirects = 0) {
  return new Promise((resolve, reject) => {
    let file = null
    const fail = (e) => {
      if (file) { file.destroy(); try { fs.unlinkSync(dest) } catch { /* ignore */ } }
      reject(e)
    }
    const req = https.get(url, { headers: { 'User-Agent': 'Olisar-Updater' }, timeout: 60000, signal }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 5) {
        res.resume()
        return resolve(downloadFile(res.headers.location, dest, onProgress, signal, redirects + 1))
      }
      if (res.statusCode !== 200) {
        res.resume()
        return reject(Object.assign(new Error(`HTTP ${res.statusCode}`), { status: res.statusCode }))
      }
      const total = parseInt(res.headers['content-length'] || '0', 10)
      let got = 0
      file = fs.createWriteStream(dest)
      res.on('data', (c) => { got += c.length; if (onProgress) onProgress(got, total) })
      // A connection dropped partway through ends the response, not the request. Without this
      // the promise never settled and the install sat on a half-written file for good.
      const interrupted = () => fail(plain('The download was interrupted.'))
      res.on('error', interrupted)
      res.on('aborted', interrupted)
      res.pipe(file)
      file.on('finish', () => file.close(() => {
        if (total && got < total) reject(plain('The download was interrupted.'))
        else resolve(dest)
      }))
      file.on('error', fail)
    })
    req.on('error', fail)
    req.on('timeout', () => req.destroy(plain('The download timed out.')))
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

// Downloads the update and hands over to whatever swaps it in, reporting each step to the
// window's update screen. Resolves { ok: true } once the app is on its way out to restart,
// or { ok: false, reason } while it's still running: 'cancelled', 'failed', 'busy', or
// 'manual' when this build can only open the download.
async function installUpdate(update) {
  if (installing) return { ok: false, reason: 'busy' }
  if (!canSelfUpdate() || !update || !update.hasInstaller) {
    shell.openExternal(update ? update.downloadUrl : RELEASES_PAGE)
    return { ok: false, reason: 'manual' }
  }

  installing = true
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'olisar-upd-'))
  const ctrl = new AbortController()
  abortDownload = () => ctrl.abort()
  report({
    phase: 'download', steps: STEPS, version: update.version,
    current: displayVersion(app.getVersion()), received: 0, total: 0,
  })
  // Started from the tray or a notification, the window may be hidden. The update is about to
  // take the app down, so it comes forward to say so.
  showWindow()
  const onDownload = (received, total) => {
    if (total) setProgress(received / total)
    step('download', { received, total })
  }
  try {
    setProgress(0)
    if (process.platform === 'win32') await _applyWindows(update, tmpRoot, onDownload, ctrl.signal)
    else await _applyMac(update, tmpRoot, onDownload, ctrl.signal)
    step('restart')
    setProgress(-1)
    app.isQuitting = true
    app.quit() // the backend is already down; the installer/script then swaps + relaunches
    return { ok: true }
  } catch (err) {
    installing = false
    abortDownload = null
    setProgress(-1)
    try { fs.rmSync(tmpRoot, { recursive: true, force: true }) } catch { /* ignore */ }
    if (ctrl.signal.aborted) { report(null); return { ok: false, reason: 'cancelled' } }
    step('failed', {
      failedAt: progress ? progress.phase : 'download',
      error: describeFailure(err, progress ? progress.phase : 'download'),
      downloadUrl: update.downloadUrl,
    })
    showWindow()
    return { ok: false, reason: 'failed' }
  }
}

// Every bot runs from the backend binary an update replaces, and each takes a moment to sign
// out of Discord. The window stays up meanwhile, on the screen's "Shut down" step.
async function shutDown() {
  abortDownload = null
  step('shutdown')
  setProgress(2)
  await stopBackend()
}

// Windows: download the NSIS installer and run it. electron-builder's installer closes the
// running app, installs over it, and relaunches. The temp file is left in place because the
// installer executes from it (the OS reaps temp later).
async function _applyWindows(update, tmpRoot, onDownload, signal) {
  const installer = path.join(tmpRoot, 'OlisarSetup.exe')
  await downloadFile(update.downloadUrl, installer, onDownload, signal)
  // Let every bot go first, or Windows holds the files the installer is replacing open.
  await shutDown()
  spawn(installer, [], { detached: true, stdio: 'ignore' }).unref()
}

// macOS: download the .dmg, mount it, stage the new .app on the target volume, and hand a
// detached script the swap + relaunch. Copying out of the mounted image preserves the app's
// signature and stapled notarization ticket, so the swapped-in bundle is as valid as the one
// a user would have dragged across by hand.
async function _applyMac(update, tmpRoot, onDownload, signal) {
  const appPath = currentAppPath()
  if (!appPath) throw plain("Couldn't find where Olisar is installed.")
  const dmgPath = path.join(tmpRoot, 'Olisar.dmg')
  const mountPoint = path.join(tmpRoot, 'mnt')
  const staging = path.join(path.dirname(appPath), `.olisar-update-${Date.now()}`)
  let mounted = false
  try {
    await downloadFile(update.downloadUrl, dmgPath, onDownload, signal)
    abortDownload = null
    step('unpack')
    setProgress(2) // indeterminate while we swap
    fs.mkdirSync(mountPoint, { recursive: true })
    await execFileP('hdiutil', ['attach', dmgPath, '-nobrowse', '-noverify', '-mountpoint', mountPoint])
    mounted = true
    const srcApp = path.join(mountPoint, 'Olisar.app')
    if (!fs.existsSync(srcApp)) throw plain("The downloaded update doesn't contain Olisar.app.")
    fs.mkdirSync(staging, { recursive: true })
    await execFileP('cp', ['-R', srcApp, staging]) // staging/Olisar.app
    await execFileP('hdiutil', ['detach', mountPoint, '-force']).catch(() => {})
    mounted = false
    const newApp = path.join(staging, 'Olisar.app')
    if (!fs.existsSync(newApp)) throw plain("Couldn't copy the new version into place.")
    const scriptPath = path.join(tmpRoot, 'swap.sh')
    fs.writeFileSync(scriptPath, swapScript({ pid: process.pid, newApp, target: appPath, staging, tmpRoot }), { mode: 0o755 })
    spawn('/bin/bash', [scriptPath], { detached: true, stdio: 'ignore' }).unref()
  } catch (err) {
    if (mounted) { try { await execFileP('hdiutil', ['detach', mountPoint, '-force']) } catch { /* ignore */ } }
    try { fs.rmSync(staging, { recursive: true, force: true }) } catch { /* ignore */ }
    throw err
  }
  // The script waits for this process to exit before it swaps, so the bots can take their time.
  await shutDown()
}

module.exports = {
  init, checkForUpdates, getAvailableUpdate, openDownload, installUpdate, isInstalling, canSelfUpdate, displayVersion,
  getProgress, isCommitted, cancelInstall, dismissFailure,
}
// Exported for unit tests only.
module.exports._internal = { isNewer, parseVersion, pickRelease, assetForPlatform, swapScript, currentAppPath, downloadFile, describeFailure }
