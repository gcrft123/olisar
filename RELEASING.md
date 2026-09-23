# Releasing Olisar

The desktop app checks this repo's **GitHub Releases** on launch (and every 6 hours,
and on demand from the tray → *Check for Updates…*). When a release with a **higher version
number** than the running app is found, it **installs it in place**: *Install & Restart*
downloads the release `.dmg`, swaps the app bundle, and relaunches into the new version.
The macOS build is signed with a Developer ID certificate and notarized by Apple, so the
swapped-in bundle carries its own valid signature and stapled ticket. (On platforms it
can't self-install yet, it opens the installer to download instead.)

So "cutting a release" = publishing a GitHub Release whose tag/version is newer than what
people are running, with the installers attached as assets.

## Channels and version numbers

Every install follows one of two channels, picked in Settings → Updates:

- **Stable** gets stable releases only.
- **Beta** gets betas and every stable release as it ships.

A server-hosted bot's VM follows the app's channel.

From 2.0 on, a stable release has **two numbers**: `2.0`, `2.1`, `2.2`. There's no third
number, so a fix after a release is simply the next release. A beta carries the number of
the stable release it leads up to, counted from 1: `2.1.beta-1`, `2.1.beta-2`, then `2.1`.
Betas sort below their own release, so a beta tester moves onto `2.1` when it ships. Releases
before 2.0 had three numbers (`1.5.0`); they still sort below everything after them.

Switching from beta to stable doesn't downgrade anyone. The install stays on its beta until a
stable release passes it. A build with no channel saved follows the kind it is, so installing
a beta by hand (the only way onto the first one, since a stable build never sees betas) joins
the beta channel.

The parsing and ordering live in [`olisar/versioning.py`](olisar/versioning.py), with ports in
[`desktop/updater.js`](desktop/updater.js) and [`web/src/version.ts`](web/src/version.ts).

## 1. Bump the version

The release version lives in **three** files, and they must all match the tag — or the
release misfires (electron-builder builds/publishes under the wrong version and the tagged
GitHub release ends up empty; this bit v0.4.0). Bump all three:

- [`desktop/package.json`](desktop/package.json) — what electron-builder builds
- [`pyproject.toml`](pyproject.toml) — the Python project version
- [`web/package.json`](web/package.json) — the dashboard

npm and electron-builder only accept semver, so the files spell the version differently
from the tag:

| Tag | Version files |
|---|---|
| `v2.0.beta-1` | `2.0.0-beta.1` |
| `v2.0` | `2.0.0` |

Then run `npm install --package-lock-only` in `web/` and `uv lock` so the lockfiles follow,
and confirm everything agrees before tagging:

```sh
python3 scripts/check_release_version.py               # do the files agree with each other?
python3 scripts/check_release_version.py v2.0.beta-1   # …and with the tag you're about to push?
```

The check names the exact string the files need when they're off, and refuses a tag that
isn't spelled as above (`v2.0.0`, `v2.0-beta.1`). CI runs it first (the `version-check` job)
and **fails the release fast** if anything is out of sync, so a mismatch can't silently ship.

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

To check the shell you're about to build from before you spend a build on it:

```sh
scripts/check_mac_signing.sh
```

It resolves the certificate and the notarization credentials exactly the way the build does,
authenticates against Apple without submitting anything, and names every problem it finds.

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

**Prove them before you tag.** Run the **macOS signing preflight** workflow (Actions →
*macOS signing preflight* → *Run workflow*). It runs the same
[`scripts/check_mac_signing.sh`](scripts/check_mac_signing.sh) against the repository secrets
and takes about a minute — a green run means the next tag produces a signed, notarized `.dmg`.
Do this after adding or rotating any secret above. Release builds run it as their first job,
so a bad secret fails there instead of ten minutes into the macOS build.

> **Why it exists.** `release.yml` only triggers on a `v*` tag, so a signing secret used to be
> untestable except by cutting a release. v1.4.0 burned four tagged builds discovering that
> `MACOS_CERTIFICATE` was unset: electron-builder read the empty `CSC_LINK` as a *path*,
> resolved it to the working directory, and failed with `⨯ …/desktop not a file` — an error
> that names neither signing nor the secret. That release shipped from a local build instead.

## 3. Build & publish

### Automated (recommended)

Push a tag and let CI build and publish — see
[`.github/workflows/release.yml`](.github/workflows/release.yml):

```sh
git tag v2.0.beta-1
git push origin v2.0.beta-1
```

The workflow builds **both** installers in parallel — macOS (Apple-Silicon `.dmg`) and
Windows (`.exe`) — each running the full chain (Tailscale sidecar → dashboard → PyInstaller
backend → electron-builder) on its own runner, and both land on the same `v<tag>` GitHub
Release using the repo's `GITHUB_TOKEN`. It's live immediately. A beta is published as a
**pre-release**, which keeps it off every stable install, and the server image's `:latest`
tag only moves for a stable release.

Both jobs build with `--publish never` and upload with `gh`. electron-builder can only
publish to a tag spelled `v` + the package.json version (`v2.0.0-beta.1`), which isn't the
release's tag. macOS has a second reason: it signs and notarizes first, and stapling the
notarization ticket rewrites the `.dmg`, so it can't be published until Apple has answered.
Expect the macOS job to take ~10 minutes longer than it used to: two notarization round
trips (the `.app`, then the `.dmg`), each a few minutes at Apple.

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

# 4. publish it (drop --prerelease for a stable release)
gh release create v2.0.beta-1 --title "v2.0.beta-1" --notes "…" --prerelease
gh release upload v2.0.beta-1 out/Olisar-2.0.0-beta.1-arm64.dmg --clobber
```

`npm run release:mac` = `npm run dist:mac` (build + sign + notarize + staple the `.app`,
then build and sign the `.dmg`) followed by `npm run notarize:dmg` (notarize + staple the
`.dmg`, then verify the lot with `codesign`, `stapler` and `spctl`). Publishing is a separate
step on purpose — electron-builder starts uploading an artifact the moment it's written, and
stapling rewrites the file.

On **Windows**, which isn't signed, `npm run dist:win` builds the installer into
`desktop/out/`; upload the `.exe` the same way.

## 4. Write the release notes

[`CHANGELOG.md`](CHANGELOG.md) **is** the release notes. Entries go under `## [Unreleased]`
as the work is done, by hand — never generated from commit subjects or `git log` afterwards.
A version with nothing written under it shouldn't go out.

Cutting a stable release renames that section to `## [2.1] — YYYY-MM-DD` (em dash, ISO
date) and leaves `## [Unreleased]` empty above it. A beta doesn't get a heading: it ships the
`## [Unreleased]` section as it stands and leaves it in place, so the section keeps growing
through the betas and each beta's notes cover everything since the last stable release. CI
publishes the GitHub Release with an **empty body**, so paste the section in afterwards — it
ships verbatim:

```sh
gh release edit v2.1 --notes-file notes.md   # notes.md = that section, minus its heading
```

Title the release **`v2.1 — <short summary>`** (or **`v2.1.beta-1 — <short summary>`**).

The shape of a section:

```markdown
## [2.1] — 2026-09-13

One or two paragraphs on what was wrong and what the release does about it.
The reasoning lives here.

### Changed

[90ad8a4] — The exporter writes one file per locale instead of one combined file.

[90ad8a4] — `--locale` picks a single one.
```

- Newest release first; `## [Unreleased]` sits at the top and is empty between releases.
- Groups are `### New`, `### Changed`, `### Fixed`, in that order. Omit the empty ones; don't
  invent other names.
- Every entry is one change on one line: the short hash of the commit carrying it, an em dash,
  then one plain sentence. Hashes are plain text, not links.
- Entries are paragraphs separated by blank lines, not bullets, and never wrap.
- A commit that changed several things gets several entries; an entry covering more than one
  commit gets split by commit.
- The summary carries the why, the entries carry the what. Don't restate one in the other.

## 5. Verify

Running an older build on the right channel (a beta needs Settings → Updates → **Beta**),
open the tray → **Check for Updates…**. It should report the new version and offer
**Download**. (Or wait — it polls automatically a few seconds after launch and every 6 hours.)

> **Cross-platform:** the tag-push CI builds macOS *and* Windows automatically. The updater
> picks the `.exe` asset on Windows and the `arm64.dmg` on Apple-Silicon macOS, and self-installs
> on both (macOS swaps the `.app`; Windows runs the NSIS installer). To build a Windows
> installer by hand instead, run `npm run dist:win` on a Windows machine.
