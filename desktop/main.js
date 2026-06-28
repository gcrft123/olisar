// Olisar desktop shell (Electron).
//
// Spawns the PyInstaller-packaged backend (`olisar-backend --port <p>`) as a
// sidecar, waits for it to become healthy, then shows the dashboard in a window
// and a system-tray menu. Closing the window hides to the tray; quitting kills
// the backend so nothing is left running.

const { app, BrowserWindow, Tray, Menu, shell, nativeImage, dialog, ipcMain, screen } = require('electron')
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
  backend = spawn(bin, ['--port', String(port)], {
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
      ...(funnelPath() ? { OLISAR_FUNNEL: funnelPath() } : {}),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,  // don't pop a console window for the backend on Windows
  })
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
  win.loadURL(`http://127.0.0.1:${backendPort}/`)
  // External links open in the OS browser, not inside the app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) { shell.openExternal(url); return { action: 'deny' } }
    return { action: 'allow' }
  })
  win.on('close', (e) => {
    if (!app.isQuitting) { e.preventDefault(); win.hide() }  // stay alive in the tray
  })
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
  const updateItems = update
    ? [{
        label: (updater.canSelfUpdate() ? 'Install update & restart' : 'Download update') + ` — v${update.version}`,
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
    { label: `Olisar ${app.getVersion()}`, enabled: false },
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
// updater's state, so the dashboard's "Install & restart" button works without the tray).
function updateState() {
  const u = updater.getAvailableUpdate()
  return {
    available: u ? { version: u.version, hasInstaller: !!u.hasInstaller } : null,
    canSelfUpdate: updater.canSelfUpdate(),
    installing: updater.isInstalling(),
  }
}

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
    await updater.installUpdate(u)  // self-installs + relaunches, or opens the download page
    return { ok: true }
  })
}

// ── lifecycle ───────────────────────────────────────────────────────────────

async function boot() {
  registerUpdateIpc()
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
  updater.init({ getMainWindow: () => win })  // lets it show download progress on the dock
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
  app.on('before-quit', () => {
    app.isQuitting = true
    if (backend) { try { backend.kill('SIGTERM') } catch { /* ignore */ } }
  })
}
