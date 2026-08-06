#!/usr/bin/env node
// Notarizes, staples and verifies the built macOS .dmg.  Run after `npm run dist:mac`
// (`npm run release:mac` does both), and *before* the .dmg is uploaded anywhere.
//
// electron-builder already notarizes and staples the .app inside the image (see
// `build.mac.notarize` in package.json) — that's what makes the app open cleanly once it's
// dragged to /Applications, and what lets the in-app updater copy it straight out of a
// mounted image. This script covers the second half: the disk image itself, so the download
// mounts without a Gatekeeper prompt. Stapling has to happen here rather than inside
// electron-builder because electron-builder starts uploading an artifact as soon as it's
// written, and stapling rewrites the file.
//
// Credentials come from the environment, same names electron-builder reads (never hardcode
// them — an app-specific password or API key is a secret):
//
//   APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD [+ APPLE_TEAM_ID]   ← what CI and local builds use
//   APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER        ← App Store Connect API key
//   APPLE_KEYCHAIN_PROFILE [+ APPLE_KEYCHAIN]                  ← this script only, see below
//
// A `notarytool store-credentials` profile works here but NOT for the .app half of the build:
// electron-builder always passes mac.notarize.teamId to @electron/notarize, whose validator
// treats any teamId as password credentials and then rejects keychain credentials alongside it
// ("Cannot use password credentials, API key credentials and keychain credentials at once").
// So a full `npm run release:mac` needs the Apple ID pair — see RELEASING.md §2.
//
// With none of them set the script skips notarization and exits 0, so an unsigned local or
// fork build still produces a usable .dmg — exactly what happened before signing existed.

const fs = require('fs')
const path = require('path')
const { spawnSync } = require('child_process')

const DESKTOP_DIR = path.resolve(__dirname, '..')
const OUT_DIR = path.join(DESKTOP_DIR, 'out')

const log = (msg) => console.log(`  • ${msg}`)
const skip = (why) => {
  console.log(`\nskipped .dmg notarization — ${why}\n`)
  process.exit(0)
}

// Run a command, streaming its output. Returns the exit status instead of throwing.
function run(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: 'inherit' })
  return res.status === 0
}

// Run a command quietly and hand back its exit status + stdout.
function capture(cmd, args) {
  const res = spawnSync(cmd, args, { encoding: 'utf8' })
  return { ok: res.status === 0, stdout: res.stdout || '', stderr: res.stderr || '' }
}

// The team id notarytool needs when authenticating with an Apple ID. Env wins so CI can
// override; otherwise fall back to the one the signing config already pins.
function teamId() {
  if (process.env.APPLE_TEAM_ID) return process.env.APPLE_TEAM_ID
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(DESKTOP_DIR, 'package.json'), 'utf8'))
    const notarize = pkg.build && pkg.build.mac && pkg.build.mac.notarize
    return notarize && notarize.teamId ? notarize.teamId : null
  } catch {
    return null
  }
}

// notarytool auth arguments, or null when nothing is configured. Checked in the same order
// electron-builder checks them, so both halves of the build authenticate the same way.
function notarytoolAuth() {
  const { APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_API_KEY, APPLE_API_KEY_ID, APPLE_API_ISSUER, APPLE_KEYCHAIN, APPLE_KEYCHAIN_PROFILE } = process.env

  if (APPLE_ID && APPLE_APP_SPECIFIC_PASSWORD) {
    const team = teamId()
    if (!team) throw new Error('APPLE_ID is set but no team id — set APPLE_TEAM_ID or build.mac.notarize.teamId')
    return { args: ['--apple-id', APPLE_ID, '--password', APPLE_APP_SPECIFIC_PASSWORD, '--team-id', team], as: `Apple ID ${APPLE_ID}` }
  }
  if (APPLE_API_KEY && APPLE_API_KEY_ID && APPLE_API_ISSUER) {
    return { args: ['--key', APPLE_API_KEY, '--key-id', APPLE_API_KEY_ID, '--issuer', APPLE_API_ISSUER], as: `App Store Connect key ${APPLE_API_KEY_ID}` }
  }
  if (APPLE_KEYCHAIN_PROFILE) {
    const args = ['--keychain-profile', APPLE_KEYCHAIN_PROFILE]
    if (APPLE_KEYCHAIN) args.push('--keychain', APPLE_KEYCHAIN)
    return { args, as: `keychain profile "${APPLE_KEYCHAIN_PROFILE}"` }
  }
  return null
}

// Submit and block until Apple answers. `notarytool submit --wait` can exit 0 on a rejected
// submission (submitting worked; the *notarization* didn't), so read the status out of the
// JSON rather than trusting the exit code, and dump Apple's log when it isn't Accepted.
function notarize(dmg, auth) {
  log(`submitting to Apple as ${auth.as} — this usually takes a few minutes…`)
  const res = spawnSync(
    'xcrun',
    ['notarytool', 'submit', dmg, '--wait', '--timeout', '30m', '--output-format', 'json', ...auth.args],
    { encoding: 'utf8' },
  )
  let result
  try {
    result = JSON.parse(res.stdout)
  } catch {
    console.error(res.stdout)
    console.error(res.stderr)
    throw new Error("notarytool didn't return a result — see its error above")
  }
  if (result.status !== 'Accepted') {
    console.error(`\nnotarization ${result.status || 'failed'}: ${result.message || ''}`)
    if (result.id) {
      console.error('\nApple\'s notarization log:')
      run('xcrun', ['notarytool', 'log', result.id, ...auth.args])
    }
    throw new Error(`notarization was not accepted (status: ${result.status})`)
  }
  log(`accepted  submission=${result.id}`)
}

// Everything Gatekeeper will check on the user's machine, checked here instead.
// Returns the labels of the checks that failed, so the error names them.
function verify(dmgs, appPath) {
  const failed = []
  const check = (label, cmd, args) => {
    console.log(`\n$ ${label}`)
    if (!run(cmd, args)) failed.push(label)
  }
  if (appPath) {
    check('codesign --verify (app)', 'codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath])
    check('stapler validate (app)', 'xcrun', ['stapler', 'validate', appPath])
    check('spctl --assess (app)', 'spctl', ['--assess', '--type', 'exec', '--verbose=2', appPath])
  }
  for (const dmg of dmgs) {
    check('codesign --verify (dmg)', 'codesign', ['--verify', '--verbose=2', dmg])
    check('stapler validate (dmg)', 'xcrun', ['stapler', 'validate', dmg])
    check('spctl --assess (dmg)', 'spctl', ['--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', dmg])
  }
  return failed
}

function main() {
  if (process.platform !== 'darwin') skip('not macOS')
  if (!fs.existsSync(OUT_DIR)) skip(`no build output at ${OUT_DIR} — run \`npm run dist:mac\` first`)

  const dmgs = fs.readdirSync(OUT_DIR).filter((f) => f.endsWith('.dmg')).map((f) => path.join(OUT_DIR, f))
  if (dmgs.length === 0) skip(`no .dmg in ${OUT_DIR} — run \`npm run dist:mac\` first`)

  // An unsigned image can't be notarized; that's the fork/no-certificate build, not an error.
  const unsigned = dmgs.filter((dmg) => !capture('codesign', ['--display', dmg]).ok)
  if (unsigned.length) skip(`${path.basename(unsigned[0])} isn't signed (no Developer ID certificate available)`)

  const auth = notarytoolAuth()
  if (!auth) skip('no notarization credentials in the environment (see the header of this script)')

  // out/mac-arm64/Olisar.app — the bundle electron-builder signed, notarized and stapled.
  const appDir = fs.readdirSync(OUT_DIR).find((f) => f.startsWith('mac'))
  const app = appDir && fs.readdirSync(path.join(OUT_DIR, appDir)).find((f) => f.endsWith('.app'))
  const appPath = app ? path.join(OUT_DIR, appDir, app) : null

  // Stop before spending a round trip at Apple if the .app half never happened. That's what a
  // `dist:mac` run with no credentials leaves behind ("skipped macOS notarization" in its log):
  // notarizing the image would still succeed and register the app with Apple, so `spctl` passes
  // — but with no ticket stapled to the bundle, Gatekeeper falls back to an online check once
  // the app is dragged out of the image.
  if (appPath && !capture('xcrun', ['stapler', 'validate', appPath]).ok) {
    throw new Error(
      `${path.basename(appPath)} has no stapled notarization ticket, so the .dmg would ship an\n` +
      '  app that only validates online. electron-builder skipped it — it had no credentials when\n' +
      '  the app was built. Re-run `npm run release:mac` with the credentials already exported.',
    )
  }

  for (const dmg of dmgs) {
    console.log(`\nnotarizing ${path.basename(dmg)}`)
    notarize(dmg, auth)
    if (!run('xcrun', ['stapler', 'staple', dmg])) throw new Error('stapling the .dmg failed')
    log('ticket stapled')
  }

  console.log('\nverifying the signed + notarized build')
  const failed = verify(dmgs, appPath)
  if (failed.length) throw new Error(`these checks failed — ${failed.join(', ')}`)
  console.log('\nall checks passed — the .dmg is signed, notarized and stapled\n')
}

try {
  main()
} catch (err) {
  console.error(`\nnotarize-dmg failed: ${err.message}\n`)
  process.exit(1)
}
