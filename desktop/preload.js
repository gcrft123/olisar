// Preload bridge. The dashboard talks to the local backend over normal HTTP, so most
// of it needs no privileged access — but a few things only the Electron main process
// can do (self-update: swap the app on disk and relaunch) are exposed here as a narrow,
// contextIsolated API the renderer can call.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('olisar', {
  desktop: true,
  platform: process.platform,
  updates: {
    // { available: {version, hasInstaller} | null, canSelfUpdate, installing, progress }
    state: () => ipcRenderer.invoke('updates:state'),
    // Re-check GitHub Releases now; returns the same shape as state().
    check: () => ipcRenderer.invoke('updates:check'),
    // Download + install the available update and relaunch (or open the download page
    // if this build can't self-install). The app quits on success; otherwise resolves
    // { ok: false, reason }.
    install: () => ipcRenderer.invoke('updates:install'),
    // Call off the download. Only the download: after it the install is underway.
    cancel: () => ipcRenderer.invoke('updates:cancel'),
    // Leave a failed update's screen for the console.
    dismiss: () => ipcRenderer.invoke('updates:dismiss'),
    // { from, to } once after an update lands, otherwise null.
    justUpdated: () => ipcRenderer.invoke('updates:just-updated'),
    // The update screen's feed: the install's progress each time it moves, or null when it
    // ends without restarting. Returns the unsubscribe.
    onProgress: (fn) => {
      const listener = (_e, progress) => fn(progress)
      ipcRenderer.on('updates:progress', listener)
      return () => ipcRenderer.removeListener('updates:progress', listener)
    },
  },
})
