// Minimal preload. The dashboard talks to the local backend over normal HTTP
// (same origin), so the renderer needs no privileged bridge today. Kept as a
// dedicated, contextIsolated preload so any future native hooks have a safe home.
const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('olisar', {
  desktop: true,
  platform: process.platform,
})
