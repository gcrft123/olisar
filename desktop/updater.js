// GitHub Releases updater for the Olisar desktop app.
//
// Olisar ships UNSIGNED, so it can't use Squirrel's signed auto-install. Instead it applies
// updates itself on macOS: download the latest release's .dmg, mount it, copy the new
// Olisar.app out, and a small detached script swaps it over the running bundle and
// relaunches. Gatekeeper allows this even unsigned because a file the app writes itself
// (not via a browser) isn't quarantined. On other platforms / non-packaged builds it falls
// back to opening the installer download.

const https = require('https')
const fs = require('fs')
const path = require('path')
const os = require('os')
const { spawn, execFile } = require('child_process')
const { app, dialog, shell, Notification } = require('electron')

const REPO = 'gcrft123/olisar'
const LATEST_URL = `https://api.github.com/repos/${REPO}/releases/latest`
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

// Compare dotted numeric versions (ignoring any leading "v" and pre-release suffix).
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

// ── public API ──────────────────────────────────────────────────────────────

function getAvailableUpdate() { return available }
function isInstalling() { return installing }
function openDownload() { shell.openExternal(available ? available.downloadUrl : RELEASES_PAGE) }

// Whether this build can replace itself in place (vs. just opening the download).
function canSelfUpdate() {
  return process.platform === 'darwin' && app.isPackaged && !!currentAppPath()
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
      dialog.showMessageBox({ type: 'info', message: "You're up to date", detail: `Olisar ${app.getVersion()} is the latest version.`, buttons: ['OK'] })
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
        detail: `You're on ${app.getVersion()}. Olisar can download it and restart into the new version.`,
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
        detail: `You're on ${app.getVersion()}. Download the new installer to update.`,
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
  const appPath = currentAppPath()
  if (!appPath) { shell.openExternal(update.downloadUrl); return }

  installing = true
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'olisar-upd-'))
  const dmgPath = path.join(tmpRoot, 'Olisar.dmg')
  const mountPoint = path.join(tmpRoot, 'mnt')
  const staging = path.join(path.dirname(appPath), `.olisar-update-${Date.now()}`)
  let mounted = false
  try {
    if (Notification.isSupported()) new Notification({ title: 'Updating Olisar', body: `Downloading ${update.version}…` }).show()
    setProgress(0)
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

    setProgress(-1)
    app.isQuitting = true
    app.quit() // before-quit kills the backend; the script then swaps + relaunches
  } catch (err) {
    installing = false
    setProgress(-1)
    if (mounted) { try { await execFileP('hdiutil', ['detach', mountPoint, '-force']) } catch { /* ignore */ } }
    try { fs.rmSync(tmpRoot, { recursive: true, force: true }) } catch { /* ignore */ }
    try { fs.rmSync(staging, { recursive: true, force: true }) } catch { /* ignore */ }
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

module.exports = { init, checkForUpdates, getAvailableUpdate, openDownload, installUpdate, isInstalling, canSelfUpdate }
// Exported for unit tests only.
module.exports._internal = { isNewer, assetForPlatform, swapScript, currentAppPath, downloadFile }
