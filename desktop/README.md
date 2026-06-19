# Olisar desktop app (Electron shell)

This directory holds the Electron wrapper that turns Olisar into a self-hosted
desktop application (macOS + Windows). It spawns the PyInstaller-packaged Python
backend (`python -m olisar.runtime`) as a sidecar, opens the dashboard in an
embedded window, and provides a system-tray menu (open dashboard, bot status,
toggle Tailscale Funnel remote access, check for updates, quit).

Contents:

- `main.js` / `preload.js` — Electron main process: backend lifecycle, tray, window.
- `updater.js` — in-app updater (checks GitHub Releases; self-installs).
- `backend.spec` — PyInstaller spec for the unified backend, incl. the sqlite-vec binary.
- `package.json` — `electron-builder` config: unsigned `.dmg`/`.app` (macOS) + NSIS `.exe`
  (Windows), the GitHub publish target, and the bundled resources.
- `funnel-sidecar/` — the Go Tailscale Funnel helper (`tsnet`); built into
  `resources/olisar-funnel[.exe]` — see `resources/README.md`.

Build instructions: [`../SETUP.md`](../SETUP.md) and [`../RELEASING.md`](../RELEASING.md).
