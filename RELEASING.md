# Releasing Olisar

The desktop app checks this repo's **latest GitHub Release** on launch (and every 6 hours,
and on demand from the tray → *Check for Updates…*). When a release with a **higher version
number** than the running app is found, it **installs it in place**: *Install & Restart*
downloads the release `.dmg`, swaps the app bundle, and relaunches into the new version.
This works even though the app is unsigned — a file the app downloads itself isn't
Gatekeeper-quarantined. (On platforms it can't self-install yet, it opens the installer to
download instead.)

So "cutting a release" = publishing a GitHub Release whose tag/version is newer than what
people are running, with the installers attached as assets.

## 1. Bump the version

The release version lives in **three** files, and they must all match the tag — or the
release misfires (electron-builder builds/publishes under the wrong version and the tagged
GitHub release ends up empty; this bit v0.4.0). Bump all three:

- [`desktop/package.json`](desktop/package.json) — what electron-builder builds & publishes
- [`pyproject.toml`](pyproject.toml) — the Python project version
- [`web/package.json`](web/package.json) — the dashboard

Then confirm they agree before tagging:

```sh
python3 scripts/check_release_version.py          # do the files agree with each other?
python3 scripts/check_release_version.py v0.4.0   # …and with the tag you're about to push?
```

CI runs this same check first (the `version-check` job) and **fails the release fast** if
anything is out of sync, so a mismatch can't silently ship.

Tag names should be `v<version>` (e.g. `v0.2.0`). The updater strips the leading `v` and
compares numerically, so `v0.2.0` > `0.1.0`.

## 2. Build & publish

### Automated (recommended)

Push a tag and let CI build and publish — see
[`.github/workflows/release.yml`](.github/workflows/release.yml):

```sh
git tag v0.2.0
git push origin v0.2.0
```

The workflow builds **both** installers in parallel — macOS (Apple-Silicon `.dmg`) and
Windows (`.exe`) — each running the full chain (Tailscale sidecar → dashboard → PyInstaller
backend → electron-builder) on its own runner, and both publish to the same `v<tag>` GitHub
Release using the repo's `GITHUB_TOKEN` (`releaseType: "release"`, so it's live immediately).

### Manual

From the repo root (a Homebrew Python 3.13, [uv](https://docs.astral.sh/uv/), Node 18+,
and Go for the sidecar):

```sh
# 1. (once) build the Tailscale Funnel helper — see desktop/resources/README.md
cd desktop/funnel-sidecar && GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o ../resources/olisar-funnel . && cd ../..

# 2. dashboard + backend
cd web && npm install && npm run build && cd ..
uv run pyinstaller desktop/backend.spec --noconfirm --clean

# 3. build AND publish to GitHub Releases (electron-builder reads build.publish)
export GH_TOKEN=$(gh auth token)          # a token with `repo` scope
cd desktop && npm install && npm run release
```

`npm run release` runs `electron-builder --publish always`, which creates/updates the
GitHub Release for the current version and uploads the installer + update metadata.

Prefer to attach the artifact yourself? Build without publishing
(`cd desktop && npm run dist:mac`) and then:

```sh
gh release create v0.2.0 desktop/out/Olisar-0.2.0-arm64.dmg --title "v0.2.0" --notes "…"
```

## 3. Verify

Running an older build, open the tray → **Check for Updates…**. It should report the new
version and offer **Download**. (Or wait — it polls automatically a few seconds after launch
and every 6 hours.)

> **Cross-platform:** the tag-push CI builds macOS *and* Windows automatically. The updater
> picks the `.exe` asset on Windows and the `arm64.dmg` on Apple-Silicon macOS, and self-installs
> on both (macOS swaps the `.app`; Windows runs the NSIS installer). To build a Windows
> installer by hand instead, run `npm run dist:win` (or `npm run release`) on a Windows machine.
