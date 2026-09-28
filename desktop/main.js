// Olisar desktop shell (Electron).
//
// Spawns the PyInstaller-packaged backend (`olisar-backend --gateway --port <p>`) as a
// sidecar, waits for it to become healthy, then shows the dashboard in a window
// and a system-tray menu. Closing the window hides to the tray; quitting kills
// the backend so nothing is left running.
//
// The backend is a gateway: it runs every bot on this install in its own process and
// serves one console, forwarding it to whichever bot is selected. Its bots watch it and
// exit when it does, however it goes.

const { app, BrowserWindow, Tray, Menu, shell, nativeImage, dialog, ipcMain, screen, session } = require('electron')
const updater = require('./updater')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const net = require('net')
const http = require('http')

// Make app.getName() and the userData dir use "Olisar" instead of the npm package
// name "olisar-desktop" (which would create ~/Library/Application Support/olisar-desktop).
app.setName('Olisar')

// A STABLE port so the loopback OAuth redirect URI (which Discord must match
// exactly, and which the operator registers once) is the same on every launch.
// Only falls back to a random free port if this one is genuinely taken.
const PREFERRED_PORT = 8723

let backend = null
let backendExited = Promise.resolve()  // settles when the current backend process exits
let backendPort = 0
let win = null
let tray = null
let lastHealth = { ok: false, vec: null }
let lastTunnel = { available: false, running: false }
let lastDesktop = { show_in_menu_bar: true }

// ── backend sidecar ─────────────────────────────────────────────────────────

function portIsFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer()
    srv.once('error', () => resolve(false))
    srv.once('listening', () => srv.close(() => resolve(true)))
    srv.listen(port, '127.0.0.1')
  })
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

// Prefer the stable port (keeps the OAuth redirect URI constant); only use a
// random free port if it's busy, and warn since the operator must then re-register.
async function choosePort() {
  if (await portIsFree(PREFERRED_PORT)) return PREFERRED_PORT
  const p = await findFreePort()
  console.warn(`[olisar] port ${PREFERRED_PORT} is busy; using ${p}. The OAuth redirect URL will change — re-register it in the Discord portal.`)
  return p
}

function backendBinary() {
  const name = process.platform === 'win32' ? 'olisar-backend.exe' : 'olisar-backend'
  // Packaged: under resources/backend. Dev: the PyInstaller dist next to the repo.
  return app.isPackaged
    ? path.join(process.resourcesPath, 'backend', name)
    : path.join(__dirname, '..', 'dist', 'olisar-backend', name)
}

// The bundled Tailscale Funnel helper, handed to the backend (which manages the tunnel
// so the auth key never leaves it). Returns '' when it isn't bundled — remote access
// then degrades gracefully to "helper not found".
function funnelPath() {
  const name = process.platform === 'win32' ? 'olisar-funnel.exe' : 'olisar-funnel'
  const p = app.isPackaged
    ? path.join(process.resourcesPath, name)
    : path.join(__dirname, 'resources', name)
  return fs.existsSync(p) ? p : ''
}

// When launched out of the repo's `desktop/out/...` build, find the repo root so the
// spawned backend's cwd can see the developer's `.env` (pydantic-settings reads it from
// cwd). For an installed/distributed app there's no .env anywhere up the path, so this
// is a no-op and the wizard collects everything fresh.
function devRepoRoot() {
  let dir = path.resolve(__dirname)
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(dir, '.env')) && fs.existsSync(path.join(dir, 'pyproject.toml'))) {
      return dir
    }
    const up = path.dirname(dir)
    if (up === dir) break
    dir = up
  }
  return null
}

function startBackend(port) {
  const bin = backendBinary()
  const repoRoot = devRepoRoot()
  if (repoRoot) console.log(`[olisar] dev launch — using repo .env at ${repoRoot}/.env`)
  backend = spawn(bin, ['--gateway', '--port', String(port)], {
    cwd: repoRoot || undefined,
    env: {
      ...process.env,
      // Force the bundled Python onto UTF-8 I/O — Windows' legacy cp1252 console
      // can't encode the symbols we log (⚠ ✓ …) and would crash the backend at
      // startup. The backend also reconfigures its own streams; this covers output
      // from before our code runs (bootloader / argparse).
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      // The frozen backend can't read its own version (no package metadata / pyproject
      // in the bundle) and otherwise reports 0.0.0 — which made Settings → Updates show
      // v0.0.0 and "update available" forever. Hand it the shell's real version.
      OLISAR_VERSION: app.getVersion(),
      OLISAR_DATA_DIR: app.getPath('userData'),
      OLISAR_PORT: String(port),
      // We hold the backend's stdin open; closing it is how we ask it to stop (see stopBackend).
      OLISAR_PARENT_PIPE: '1',
      ...(funnelPath() ? { OLISAR_FUNNEL: funnelPath() } : {}),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,  // don't pop a console window for the backend on Windows
  })
  const proc = backend
  backendExited = new Promise((resolve) => proc.once('exit', resolve))
  backend.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  backend.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`))
  backend.on('exit', (code, sig) => {
    console.log(`[backend] exited code=${code} sig=${sig}`)
    backend = null
  })
  backend.on('error', (err) => {
    dialog.showErrorBox('Olisar', `Could not start the backend:\n${err.message}\n\nExpected at: ${bin}`)
  })
}

// Stop the backend and wait until it — and through it, every bot — has exited. Closing its
// stdin is the graceful signal on every platform: on Windows a kill is TerminateProcess, which
// would leave the gateway no chance to sign its bots out of Discord, and they'd still be running
// (and holding the backend's files) while an update installed over them. A backend that
// doesn't finish in time is killed.
function stopBackend(timeoutMs = 30000) {
  const proc = backend
  if (!proc) return Promise.resolve()
  try { proc.stdin.end() } catch { /* already closed */ }
  let timer = null
  const deadline = new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs) })
  return Promise.race([backendExited, deadline]).then(() => {
    clearTimeout(timer)
    if (backend === proc) { try { proc.kill() } catch { /* ignore */ } }
  })
}

function pollHealth(port, { timeoutMs = 60000 } = {}) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 1500 }, (res) => {
        let body = ''
        res.on('data', (c) => (body += c))
        res.on('end', () => {
          if (res.statusCode === 200) {
            try { lastHealth = JSON.parse(body) } catch { /* keep previous */ }
            resolve()
          } else retry()
        })
      })
      req.on('error', retry)
      req.on('timeout', () => { req.destroy(); retry() })
    }
    const retry = () => {
      if (Date.now() > deadline) reject(new Error('backend did not become healthy in time'))
      else setTimeout(tryOnce, 400)
    }
    tryOnce()
  })
}

// Small JSON helpers against the local backend.
function reqJson(method, p, body, timeoutMs) {
  return new Promise((resolve, reject) => {
    const payload = body !== undefined ? JSON.stringify(body) : null
    const r = http.request({
      host: '127.0.0.1', port: backendPort, path: p, method, timeout: timeoutMs || 2500,
      headers: payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {},
    }, (res) => {
      let buf = ''
      res.on('data', (c) => (buf += c))
      res.on('end', () => {
        try { resolve({ status: res.statusCode, json: buf ? JSON.parse(buf) : null }) }
        catch { resolve({ status: res.statusCode, json: null }) }
      })
    })
    r.on('error', reject)
    r.on('timeout', () => { r.destroy(); reject(new Error('timeout')) })
    if (payload) r.write(payload)
    r.end()
  })
}

// Refresh cached health + tunnel status (for the tray) without blocking.
async function refreshStatus() {
  if (!backendPort) return
  try { const { json } = await reqJson('GET', '/api/health'); if (json) lastHealth = json } catch { /* ignore */ }
  try { const { json } = await reqJson('GET', '/api/tunnel/status'); if (json) lastTunnel = json } catch { /* ignore */ }
  try { const { json } = await reqJson('GET', '/api/settings/desktop'); if (json) lastDesktop = json } catch { /* ignore */ }
  applyTrayVisibility()
}

// Show or hide the tray icon to match the dashboard's "Show in the menu bar" setting.
function applyTrayVisibility() {
  if (app.isQuitting) return  // quitting waits on the backend; don't bring the tray back meanwhile
  const show = lastDesktop.show_in_menu_bar !== false
  if (show && !tray) { createTray(); return }
  if (!show && tray) { tray.destroy(); tray = null; return }
  rebuildTray()
}

async function toggleTunnel() {
  const action = lastTunnel.running ? 'disable' : 'enable'
  try {
    // Enabling can take a while (joining the tailnet + bringing up Funnel).
    const { status, json } = await reqJson('POST', `/api/tunnel/${action}`, {}, 120000)
    if (status !== 200) {
      dialog.showErrorBox('Remote access', (json && json.detail) || `Could not ${action} remote access.`)
    }
  } catch (e) {
    dialog.showErrorBox('Remote access', `Could not ${action} remote access: ${e.message}`)
  }
  await refreshStatus()
}

// ── window + tray ───────────────────────────────────────────────────────────

function parseUrl(url) { try { return new URL(url) } catch { return null } }
function originOf(url) { const u = parseUrl(url); return u ? u.origin : null }
// The only links the app hands to the OS browser.
function isWebUrl(url) { const u = parseUrl(url); return !!u && (u.protocol === 'https:' || u.protocol === 'http:') }
function isDiscord(url) {
  const u = parseUrl(url)
  return !!u && u.protocol === 'https:' && (u.hostname === 'discord.com' || u.hostname.endsWith('.discord.com'))
}

function createWindow() {
  if (win) { win.show(); win.focus(); return }
  // Open large enough that the dashboard's longest pages fit without scrolling,
  // but never larger than the current screen's usable work area.
  const { width: waW, height: waH } = screen.getPrimaryDisplay().workAreaSize
  win = new BrowserWindow({
    width: Math.min(1480, waW),
    height: Math.min(1000, waH),
    minWidth: 900,
    minHeight: 620,
    title: 'Olisar',
    backgroundColor: '#0a0a0b',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  const consoleOrigin = `http://127.0.0.1:${backendPort}`
  win.loadURL(`${consoleOrigin}/`)
  // Links never open a window inside the app: http(s) goes to the OS browser, anything else
  // (file:, a custom scheme) nowhere. The console renders model replies as Markdown, so it can
  // be handed any link, and one that isn't http(s) used to open a new app window, which could
  // inherit the preload.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isWebUrl(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  // And the window itself stays on the console. The one way off it is through Discord, for the
  // sign-ins that run in the window (the marketplace's publisher check, and "Sign in again" on
  // the access-denied screen): the backend redirects there, which this doesn't see, and
  // Discord's pages navigate among themselves and then back to the backend's callback. So
  // Discord is allowed only once the window is already on it. The console's own sign-in
  // (/auth/login?desktop=…) opens in the OS browser through the handler above, and polls the
  // backend to claim the session, so it never navigates this window at all.
  win.webContents.on('will-navigate', (e) => {
    if (originOf(e.url) === consoleOrigin) return
    if (isDiscord(e.url) && isDiscord(win.webContents.getURL())) return
    e.preventDefault()
    if (isWebUrl(e.url)) shell.openExternal(e.url)
  })
  win.on('close', (e) => {
    if (!app.isQuitting) { e.preventDefault(); win.hide() }  // stay alive in the tray
  })
  // The console holds the window open over unsaved edits (a beforeunload guard). Electron
  // honours that silently, and on a quit the backend was already on its way down, so the app
  // was left running with no window, no tray and no backend, and an update never restarted.
  // Once quitting, the page goes. An update warns about unsaved edits while it downloads.
  win.webContents.on('will-prevent-unload', (e) => { if (app.isQuitting) e.preventDefault() })
  win.on('closed', () => { win = null })
}

function trayImage() {
  // The tray uses the full-colour shield app icon, so it is NOT a macOS template
  // image (template mode would flatten it to a monochrome silhouette).
  return nativeImage.createFromPath(path.join(__dirname, 'assets', 'tray.png'))
}

function rebuildTray() {
  if (!tray) return
  const botLine = lastHealth.ok
    ? `Backend: online${lastHealth.vec === false ? ' (vector engine FAILED)' : ''}`
    : 'Backend: starting…'
  const tunnelItem = lastTunnel.available && lastTunnel.hostname
    ? [{
        label: lastTunnel.running ? 'Disable remote access' : 'Enable remote access',
        click: toggleTunnel,
      }]
    : []
  const update = updater.getAvailableUpdate()
  // A release this build can't install itself (no installer for it on the release) only
  // offers the download, which is what the click does then.
  const selfUpdate = updater.canSelfUpdate() && update && update.hasInstaller
  const updateItems = update
    ? [{
        label: (selfUpdate ? 'Install update & restart' : 'Download update') + ` — v${update.version}`,
        click: () => updater.installUpdate(update),
      }]
    : [{ label: 'Check for Updates…', click: checkForUpdatesInteractive }]
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Dashboard', click: createWindow },
    { type: 'separator' },
    { label: botLine, enabled: false },
    ...(lastTunnel.running ? [{ label: `Remote: ${lastTunnel.public_url || 'on'}`, enabled: false }] : []),
    ...tunnelItem,
    { label: 'Refresh status', click: refreshStatus },
    { type: 'separator' },
    { label: `Olisar ${updater.displayVersion(app.getVersion())}`, enabled: false },
    ...updateItems,
    { type: 'separator' },
    { label: 'Quit Olisar', click: () => { app.isQuitting = true; app.quit() } },
  ]))
}

function createTray() {
  tray = new Tray(trayImage())
  tray.setToolTip('Olisar')
  rebuildTray()
  tray.on('click', createWindow)  // Windows/Linux convenience
}

// Background poll of GitHub Releases; refresh the tray if an update is found.
async function checkUpdates() {
  await updater.checkForUpdates()
  rebuildTray()
}

// User-initiated check from the tray — shows a dialog either way.
async function checkForUpdatesInteractive() {
  await updater.checkForUpdates({ interactive: true })
  rebuildTray()
}

const UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000  // re-check every 6 hours

// Update state for the in-app Settings → Updates panel (a serializable slice of the
// updater's state, so the dashboard's "Install & restart" button works without the tray),
// and for the update screen, which reads `progress` when a window loads mid-update.
function updateState() {
  const u = updater.getAvailableUpdate()
  return {
    available: u ? { version: u.version, hasInstaller: !!u.hasInstaller } : null,
    canSelfUpdate: updater.canSelfUpdate(),
    installing: updater.isInstalling(),
    progress: updater.getProgress(),
  }
}

// The version this install ran before an update landed, read once by the window so it can
// say the update worked. Set at launch by clearCacheOnNewVersion.
let updatedFrom = null

// IPC for the renderer's in-app updater (exposed via preload as window.olisar.updates).
function registerUpdateIpc() {
  ipcMain.handle('updates:state', () => updateState())
  ipcMain.handle('updates:check', async () => {
    await updater.checkForUpdates()
    rebuildTray()
    return updateState()
  })
  ipcMain.handle('updates:install', async () => {
    let u = updater.getAvailableUpdate()
    if (!u) u = await updater.checkForUpdates()  // renderer may ask before the background poll ran
    if (!u) return { ok: false, reason: 'up-to-date' }
    return updater.installUpdate(u)  // self-installs + relaunches, or opens the download page
  })
  ipcMain.handle('updates:cancel', () => updater.cancelInstall())
  ipcMain.handle('updates:dismiss', () => updater.dismissFailure())
  ipcMain.handle('updates:just-updated', () => {
    const from = updatedFrom
    updatedFrom = null
    return from ? { from: updater.displayVersion(from), to: updater.displayVersion(app.getVersion()) } : null
  })
  ipcMain.handle('updates:whats-new', () => {
    let v = ''
    try { v = fs.readFileSync(whatsNewFile(), 'utf8').trim() } catch { return null }
    return v === app.getVersion() ? updater.displayVersion(v) : null
  })
  ipcMain.handle('updates:close-whats-new', () => {
    try { fs.rmSync(whatsNewFile(), { force: true }) } catch { /* it shows again next launch */ }
  })
}

// After an update to a stable release, the window shows a card on what's new in it
// (web/src/whatsnew.tsx). The release waits in this file until the card is closed, so quitting
// before then brings the card back at the next launch. Any other version change drops a card
// still waiting, since it describes a release this install has moved on from. A beta gets
// only the "Updated to" toast.
function whatsNewFile() { return path.join(app.getPath('userData'), 'whats-new') }

function recordWhatsNew() {
  if (!updatedFrom) return
  const now = app.getVersion()
  try {
    if (!updater.isBeta(now) && updater.isNewer(now, updatedFrom)) fs.writeFileSync(whatsNewFile(), now)
    else fs.rmSync(whatsNewFile(), { force: true })
  } catch { /* no card is the worst case */ }
}

// ── lifecycle ───────────────────────────────────────────────────────────────

// The first launch of a new version starts from an empty HTTP cache. The console's page is
// served no-cache now (olisar/runtime/console_files.py), but a page cached from an older build
// carries that build's headers, which had none: Chromium can count it fresh for hours and open
// the previous version's console against the new backend. That happened on 1.5 → 2.0.beta-2,
// and the server-side fix can't reach a copy already in the cache. Only the HTTP cache goes;
// cookies and local storage stay.
//
// 2.0.beta-3 was the first release to write the marker, so an install that last ran 1.5 (or
// 2.0.beta-1 or -2) has none, and looked like a new install: it updated to 2.0 with no What's
// new card and no "Updated to" toast. Its data folder gives it away. Every backend keeps its
// database there (and 2.0's its bots, in profiles.json and profiles/), and this launch's backend
// hasn't started yet to make one. Such an install counts as coming from 2.0.beta-2, the newest
// release that could have left no marker. Nothing shows that version; it only has to sort below
// this one.
const BEFORE_MARKER = '2.0.0-beta.2'
function ranBefore() {
  const dir = app.getPath('userData')
  return ['olisar.db', 'profiles.json', 'profiles'].some((n) => fs.existsSync(path.join(dir, n)))
}

async function clearCacheOnNewVersion() {
  const marker = path.join(app.getPath('userData'), 'last-launched-version')
  let last = ''
  try { last = fs.readFileSync(marker, 'utf8').trim() } catch { /* first launch, or before the marker */ }
  if (!last && ranBefore()) last = BEFORE_MARKER
  if (last === app.getVersion()) return
  if (last) updatedFrom = last
  try { await session.defaultSession.clearCache() } catch { /* a stale page is the worst case */ }
  try { fs.writeFileSync(marker, app.getVersion()) } catch { /* retried next launch */ }
}

async function boot() {
  registerUpdateIpc()
  updater.cleanUpLeftovers()  // an update cut off last time: its temp files, mount, staged copy
  await clearCacheOnNewVersion()
  recordWhatsNew()
  backendPort = await choosePort()
  startBackend(backendPort)
  createTray()
  try {
    await pollHealth(backendPort)
  } catch (err) {
    dialog.showErrorBox('Olisar', `The backend didn't start.\n\n${err.message}`)
    return
  }
  await refreshStatus()
  createWindow()
  setInterval(refreshStatus, 10000)  // keep the tray status fresh
  // Check for a newer GitHub release shortly after launch, then periodically.
  // getMainWindow lets it show download progress on the dock and feed the update screen;
  // showWindow brings that screen forward; stopBackend lets it wait for every bot to exit
  // before an installer replaces the files they run from.
  updater.init({ getMainWindow: () => win, showWindow: createWindow, stopBackend })
  setTimeout(checkUpdates, 8000)
  setInterval(checkUpdates, UPDATE_INTERVAL_MS)
}

// Single-instance: a second launch just focuses the existing window.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', createWindow)
  app.whenReady().then(boot)
  app.on('window-all-closed', (e) => { /* stay in tray; don't quit on macOS or others */ })
  app.on('activate', createWindow)
  // Quitting waits for the backend to stop its bots, so none is left signed in to Discord, and
  // for an update it calls off (a download, or macOS's unpack) to put things back. The window
  // and tray go at once; the wait happens out of sight.
  let readyToQuit = false
  app.on('before-quit', (e) => {
    app.isQuitting = true
    if (readyToQuit) return
    // Settles once the update is undone; null when there's none to call off. A stuck unwind
    // gives up after a while: the leftovers are cleared at the next launch.
    const abandoned = updater.abandonInstall()
    if (!abandoned && !backend) return
    e.preventDefault()
    // An update keeps its window up to show the last steps; any other quit hides it at once.
    if (!updater.isCommitted()) for (const w of BrowserWindow.getAllWindows()) w.hide()
    if (tray) { tray.destroy(); tray = null }
    const unwound = abandoned && Promise.race([abandoned, new Promise((resolve) => setTimeout(resolve, 20000))])
    Promise.all([unwound, stopBackend()]).finally(() => { readyToQuit = true; app.quit() })
  })
}
