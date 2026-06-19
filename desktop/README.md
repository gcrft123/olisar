# Olisar desktop app (Electron shell)

This directory holds the Electron wrapper that turns Olisar into a self-hosted
desktop application (macOS + Windows). It spawns the PyInstaller-packaged Python
backend (`python -m olisar.runtime`) as a sidecar, opens the dashboard in an
embedded window, and provides a system-tray menu (open dashboard, toggle
Tailscale Funnel remote access, start/stop the bot, quit).

Populated across the packaging phases:

- `main.js` / `preload.js` / `tray.js` — Electron main process, tray, lifecycle (Phase 4)
- `backend.spec` — PyInstaller spec for the unified backend, incl. the sqlite-vec binary (Phase 3)
- `electron-builder` config — unsigned `.dmg`/`.app` (macOS) + NSIS `.exe` (Windows) (Phase 4)
- bundled `olisar-funnel` helper (Tailscale `tsnet`) for remote access — see `resources/README.md` (Phase 5)

See the implementation plan for the full architecture.
