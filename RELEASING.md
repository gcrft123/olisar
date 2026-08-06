# Releasing Olisar

The desktop app checks this repo's **latest GitHub Release** on launch (and every 6 hours,
and on demand from the tray → *Check for Updates…*). When a release with a **higher version
number** than the running app is found, it **installs it in place**: *Install & Restart*
downloads the release `.dmg`, swaps the app bundle, and relaunches into the new version.
The macOS build is signed with a Developer ID certificate and notarized by Apple, so the
swapped-in bundle carries its own valid signature and stapled ticket. (On platforms it
can't self-install yet, it opens the installer to download instead.)

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

## 2. Signing & notarization (macOS, one-time setup)

The macOS `.dmg` is signed with a **Developer ID Application** certificate and **notarized**
by Apple, so it opens without a Gatekeeper detour. Windows is still unsigned.

Nothing here is required to *build* — with no certificate and no credentials the build logs a
warning, skips signing and notarization, and produces the same unsigned `.dmg` it always did.
Set it up once and every release after that is signed.

**What gets signed and notarized.** Every executable inside the bundle — the Electron helpers,
the PyInstaller backend in `Resources/backend` (its `.so`/`.dylib`s and the bundled Python), and
the `olisar-funnel` helper — is signed with the hardened runtime and the entitlements in
[`desktop/build/entitlements.mac.plist`](desktop/build/entitlements.mac.plist). Then the `.app`
is notarized and stapled (electron-builder), the `.dmg` is signed, and the `.dmg` is notarized
and stapled too ([`desktop/build/notarize-dmg.js`](desktop/build/notarize-dmg.js)). Both halves
matter: the stapled image mounts without a prompt, and the stapled app inside it stays valid
when the updater copies it out.

> **The `signIgnore` list in `desktop/package.json`.** electron-builder signs every file it
> thinks is binary, which in the backend means ~1,700 `babel` locale tables and `pytz` zone
> files — pure data, not code, and each one costs a round trip to Apple's timestamp server
> (that alone was most of a ~20-minute build). Skipping those two trees drops the work to the
> ~116 real Mach-O files. They're still covered by the app bundle's own seal, so nothing goes
> unverified. Add to the list if a dependency ever ships another large data tree.

### Locally

You need the *Developer ID Application* certificate in your login keychain (Xcode →
Settings → Accounts → Manage Certificates, or the [Developer portal](https://developer.apple.com/account/resources/certificates)),
and an [app-specific password](https://support.apple.com/en-us/102654) for an Apple ID on the
team. Local builds authenticate exactly the way CI does, so "works on my machine" actually
predicts the release job.

Park the password in your keychain once. Omitting the value after `-w` makes `security` prompt
for it twice, so it never lands in your shell history:

```sh
security add-generic-password -s olisar-notary -a "you@example.com" -w
```

Then, in the shell you build from:

```sh
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD=$(security find-generic-password -s olisar-notary -w)
```

The first signed build pops a keychain prompt for the signing key — choose **Always Allow**.

> **Not `notarytool store-credentials` / `APPLE_KEYCHAIN_PROFILE`.** electron-builder always
> passes `mac.notarize.teamId` through to `@electron/notarize`, whose validator counts *any*
> `teamId` as password credentials — so adding a keychain profile on top fails the build with
> "Cannot use password credentials, API key credentials and keychain credentials at once". A
> stored profile is still fine for `npm run notarize:dmg` on its own; it just can't drive the
> `.app` half. If both are set, the Apple ID wins and the profile is ignored.

### In CI

Add these repository secrets (Settings → Secrets and variables → Actions). Leave them unset
and the release job just builds unsigned:

| Secret | What it is |
| --- | --- |
| `MACOS_CERTIFICATE` | The Developer ID Application certificate exported from Keychain Access as a `.p12`, base64-encoded: `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | The password you set when exporting the `.p12` |
| `APPLE_ID` | An Apple ID on the developer team |
| `APPLE_APP_SPECIFIC_PASSWORD` | An [app-specific password](https://support.apple.com/en-us/102654) for that Apple ID |
| `APPLE_TEAM_ID` | `2R2HK79MH6` (also pinned in `desktop/package.json` as `build.mac.notarize.teamId`) |

## 3. Build & publish

### Automated (recommended)

Push a tag and let CI build and publish — see
[`.github/workflows/release.yml`](.github/workflows/release.yml):

```sh
git tag v0.2.0
git push origin v0.2.0
```

The workflow builds **both** installers in parallel — macOS (Apple-Silicon `.dmg`) and
Windows (`.exe`) — each running the full chain (Tailscale sidecar → dashboard → PyInstaller
backend → electron-builder) on its own runner, and both land on the same `v<tag>` GitHub
Release using the repo's `GITHUB_TOKEN` (`releaseType: "release"`, so it's live immediately).

Windows uploads through electron-builder. macOS signs and notarizes first and then uploads
with `gh` — stapling the notarization ticket rewrites the `.dmg`, so it can't be published
until Apple has answered. Expect the macOS job to take ~10 minutes longer than it used to:
two notarization round trips (the `.app`, then the `.dmg`), each a few minutes at Apple.

### Manual

From the repo root (a Homebrew Python 3.13, [uv](https://docs.astral.sh/uv/), Node 18+,
and Go for the sidecar):

```sh
# 1. (once) build the Tailscale Funnel helper — see desktop/resources/README.md
cd desktop/funnel-sidecar && GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o ../resources/olisar-funnel . && cd ../..

# 2. dashboard + backend
cd web && npm install && npm run build && cd ..
uv run pyinstaller desktop/backend.spec --noconfirm --clean

# 3. build, sign and notarize the .dmg  (see §2 for APPLE_KEYCHAIN_PROFILE)
cd desktop && npm install && npm run release:mac

# 4. publish it
gh release create v0.2.0 --title "v0.2.0" --notes "…"
gh release upload v0.2.0 out/Olisar-0.2.0-arm64.dmg --clobber
```

`npm run release:mac` = `npm run dist:mac` (build + sign + notarize + staple the `.app`,
then build and sign the `.dmg`) followed by `npm run notarize:dmg` (notarize + staple the
`.dmg`, then verify the lot with `codesign`, `stapler` and `spctl`). Publishing is a separate
step on purpose — electron-builder starts uploading an artifact the moment it's written, and
stapling rewrites the file.

On **Windows**, which isn't signed, `npm run release` still does build-and-publish in one go
(`electron-builder --publish always`).

## 4. Write the release notes

CI publishes the GitHub Release with an **empty body**, so add the notes by hand afterward
(`gh release edit vX.Y.Z --notes-file notes.md`, or in the GitHub web UI).

Title the release **`vX.Y.Z — <short summary>`**, and write the body with these four sections
**in this order** — omit any that has nothing, but always keep **Install**:

```md
## Fixes
- bug fixes / regressions

## New
- new features

## Other
- everything else: docs, refactors, dependency bumps, hardening, chores

## Install
Download the macOS `.dmg` (Apple Silicon) or Windows `.exe` below.
- **macOS** — signed and notarized; open the `.dmg` and drag Olisar to Applications.
- **Windows** — unsigned, so SmartScreen may warn; choose **More info → Run anyway**.
```

## 5. Verify

Running an older build, open the tray → **Check for Updates…**. It should report the new
version and offer **Download**. (Or wait — it polls automatically a few seconds after launch
and every 6 hours.)

> **Cross-platform:** the tag-push CI builds macOS *and* Windows automatically. The updater
> picks the `.exe` asset on Windows and the `arm64.dmg` on Apple-Silicon macOS, and self-installs
> on both (macOS swaps the `.app`; Windows runs the NSIS installer). To build a Windows
> installer by hand instead, run `npm run dist:win` (or `npm run release`) on a Windows machine.
