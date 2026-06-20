// Preload bridge. The dashboard talks to the local backend over normal HTTP, so most
// of it needs no privileged access — but a few things only the Electron main process
// can do (self-update: swap the app on disk and relaunch) are exposed here as a narrow,
// contextIsolated API the renderer can call.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('olisar', {
  desktop: true,
  platform: process.platform,
  updates: {
    // { available: {version, hasInstaller} | null, canSelfUpdate, installing }
    state: () => ipcRenderer.invoke('updates:state'),
    // Re-check GitHub Releases now; returns the same shape as state().
    check: () => ipcRenderer.invoke('updates:check'),
    // Download + install the available update and relaunch (or open the download page
    // if this build can't self-install). The app quits on success.
    install: () => ipcRenderer.invoke('updates:install'),
  },
})
