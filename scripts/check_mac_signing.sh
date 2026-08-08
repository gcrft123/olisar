#!/usr/bin/env bash
# Answer "will the next release produce a signed, notarized .dmg?" — in about a minute, and
# without spending a release tag to find out.
#
# All four v1.4.0 macOS builds died on `⨯ <repo>/desktop not a file`, an error that names
# neither signing nor the secret behind it. MACOS_CERTIFICATE was unset, so electron-builder
# read CSC_LINK="" as a *path* and resolved it to the working directory. The certificate was
# added afterwards — and then sat unexercised, because release.yml only runs on a `v*` tag, so
# the only way to test a signing secret was to cut a release and watch. That is the loop this
# script exists to break: same credentials, resolved in the same order as the build, no build.
#
# CI runs it as the `mac-preflight` job ahead of the macOS build, and it can be run on its own
# from the Actions tab at any time (.github/workflows/mac-signing-preflight.yml). Locally, run
# it with nothing exported to check the login keychain the way `npm run release:mac` would:
#
#   scripts/check_mac_signing.sh
#
# Exit 0: this environment produces a signed, notarized build — or has no credentials at all,
# which is the documented unsigned-build path for forks. Exit 1: the release would fail or
# ship unsigned. Every problem found is reported, not just the first.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

problems=()
problem() { problems+=("$1"); }
ok() { printf '  ✓ %s\n' "$1"; }

# A missing certificate isn't an error — it's the documented unsigned-build path — but it is
# the one outcome that passes quietly while shipping something different from what was asked
# for. Raise it to a run annotation so it shows on the run itself, not only in this log.
warn() {
  printf '  ! %s\n' "$1"
  [[ -n "${GITHUB_ACTIONS:-}" ]] && echo "::warning title=Unsigned macOS build::$1"
  return 0
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "not macOS — nothing to check"
  exit 0
fi

echo "checking macOS signing + notarization credentials"

# The team id electron-builder pins for the .app half. notarize-dmg.js prefers APPLE_TEAM_ID
# for the .dmg half, so both are checked against the certificate below.
pinned_team="$(python3 - "$REPO_ROOT/desktop/package.json" <<'PY'
import json, sys
notarize = json.load(open(sys.argv[1])).get("build", {}).get("mac", {}).get("notarize", {})
print(notarize.get("teamId", "") if isinstance(notarize, dict) else "")
PY
)"

# ── the signing certificate ──────────────────────────────────────────────────────────────
# Resolved the way electron-builder resolves it: CSC_LINK as a path, else as base64. With
# neither, a local build falls back to whatever Developer ID identity is in the login keychain.

identity=""
tmp=""
cleanup() {
  [[ -n "$tmp" ]] || return 0
  security delete-keychain "$tmp/preflight.keychain" >/dev/null 2>&1
  rm -rf "$tmp"
}
trap cleanup EXIT

if [[ -n "${CSC_LINK:-}" ]]; then
  tmp="$(mktemp -d)"
  keychain="$tmp/preflight.keychain"
  p12="$tmp/cert.p12"

  if [[ "$CSC_LINK" == /* || "$CSC_LINK" == .* || "$CSC_LINK" == "~"* || "$CSC_LINK" == file://* ]]; then
    src="${CSC_LINK#file://}"
    src="${src/#\~/$HOME}"
    if [[ -f "$src" ]]; then
      cp "$src" "$p12"
    else
      problem "CSC_LINK points at $src, which isn't a file."
    fi
  else
    # Node's Buffer.from(…, 'base64') ignores whitespace, so a `base64 -i cert.p12` value that
    # wrapped at 76 columns still works for electron-builder. Match that tolerance.
    printf '%s' "$CSC_LINK" | tr -d '[:space:]' | openssl base64 -d -A >"$p12" 2>/dev/null
    if [[ ! -s "$p12" ]]; then
      problem "MACOS_CERTIFICATE isn't valid base64 — re-export it with \`base64 -i cert.p12 | pbcopy\` (RELEASING.md §2)."
      : >"$p12"
    fi
  fi

  if [[ -s "$p12" ]]; then
    kcpass="$(openssl rand -base64 24)"
    security create-keychain -p "$kcpass" "$keychain" >/dev/null 2>&1
    security set-keychain-settings "$keychain" >/dev/null 2>&1 # no auto-lock while we look
    security unlock-keychain -p "$kcpass" "$keychain" >/dev/null 2>&1

    if import_err="$(security import "$p12" -k "$keychain" -P "${CSC_KEY_PASSWORD:-}" -T /usr/bin/codesign 2>&1)"; then
      identity="$(security find-identity -v -p codesigning "$keychain" 2>/dev/null | grep -m1 'Developer ID Application')"
      if [[ -z "$identity" ]]; then
        problem "MACOS_CERTIFICATE imported but holds no valid \"Developer ID Application\" identity. An expired certificate, or a Development/Distribution one, looks exactly like this — expired certificates aren't listed at all."
      fi
    elif [[ -z "${CSC_KEY_PASSWORD:-}" ]]; then
      problem "MACOS_CERTIFICATE wouldn't import and MACOS_CERTIFICATE_PASSWORD is empty — set it to the password used when the .p12 was exported."
    else
      problem "MACOS_CERTIFICATE wouldn't import: ${import_err##*security: }. Usually a wrong MACOS_CERTIFICATE_PASSWORD, or a .p12 exported without its private key."
    fi
  fi
elif [[ -z "${CI:-}" ]]; then
  identity="$(security find-identity -v -p codesigning 2>/dev/null | grep -m1 'Developer ID Application')"
fi

if [[ -z "$identity" && ${#problems[@]} -eq 0 ]]; then
  if [[ -n "${CI:-}" ]]; then
    warn "no MACOS_CERTIFICATE — the release will build an UNSIGNED, un-notarized .dmg (RELEASING.md §2)."
  else
    warn "no \"Developer ID Application\" identity in the login keychain — a local build here would be unsigned."
  fi
  echo
  echo "nothing to notarize with; stopping here."
  exit 0
fi

# ── the team id ──────────────────────────────────────────────────────────────────────────
# Apple rejects a submission whose notarizing team isn't the team that signed the bundle, and
# the two halves of the build read the team from different places — so both have to agree.

cert_team=""
if [[ -n "$identity" ]]; then
  cert_team="$(sed -n 's/.*(\([A-Z0-9]\{10\}\))".*/\1/p' <<<"$identity")"
  ok "certificate: $(sed -n 's/.*"\(.*\)".*/\1/p' <<<"$identity")"
fi

if [[ -z "$pinned_team" ]]; then
  problem "desktop/package.json has no build.mac.notarize.teamId, so electron-builder won't notarize the .app — and notarize-dmg.js then refuses to ship a .dmg with no stapled ticket."
elif [[ -n "$cert_team" && "$cert_team" != "$pinned_team" ]]; then
  problem "the certificate belongs to team $cert_team but desktop/package.json pins build.mac.notarize.teamId=$pinned_team. Apple rejects a submission from a team that didn't sign it."
fi

if [[ -n "${APPLE_TEAM_ID:-}" && -n "$pinned_team" && "${APPLE_TEAM_ID}" != "$pinned_team" ]]; then
  problem "APPLE_TEAM_ID (${APPLE_TEAM_ID}) and desktop/package.json's build.mac.notarize.teamId ($pinned_team) disagree — the .app is notarized under one and the .dmg under the other."
fi

team="${APPLE_TEAM_ID:-$pinned_team}"

# ── the notarization credentials ─────────────────────────────────────────────────────────
# Checked in the order electron-builder and notarize-dmg.js check them, then actually used:
# `notarytool history` is an authenticated call that submits nothing.

auth_label=""
auth=()
if [[ -n "${APPLE_ID:-}" || -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]]; then
  if [[ -z "${APPLE_ID:-}" ]]; then
    problem "APPLE_APP_SPECIFIC_PASSWORD is set but APPLE_ID isn't — electron-builder fails the build on this rather than falling back."
  elif [[ -z "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]]; then
    problem "APPLE_ID is set but APPLE_APP_SPECIFIC_PASSWORD isn't — electron-builder fails the build on this rather than falling back."
  elif [[ -z "$team" ]]; then
    problem "APPLE_ID is set but there's no team id — set APPLE_TEAM_ID or build.mac.notarize.teamId."
  else
    auth_label="Apple ID ${APPLE_ID}"
    auth=(--apple-id "$APPLE_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$team")
  fi
elif [[ -n "${APPLE_API_KEY:-}" || -n "${APPLE_API_KEY_ID:-}" || -n "${APPLE_API_ISSUER:-}" ]]; then
  if [[ -z "${APPLE_API_KEY:-}" || -z "${APPLE_API_KEY_ID:-}" || -z "${APPLE_API_ISSUER:-}" ]]; then
    problem "an App Store Connect key is half-configured — APPLE_API_KEY, APPLE_API_KEY_ID and APPLE_API_ISSUER all have to be set."
  else
    auth_label="App Store Connect key ${APPLE_API_KEY_ID}"
    auth=(--key "$APPLE_API_KEY" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER")
  fi
elif [[ -n "${APPLE_KEYCHAIN_PROFILE:-}" ]]; then
  # notarize-dmg.js accepts a stored profile, but the .app half can't use one: electron-builder
  # always passes mac.notarize.teamId through, and @electron/notarize rejects a teamId and
  # keychain credentials together. See RELEASING.md §2.
  problem "APPLE_KEYCHAIN_PROFILE is the only notarization credential set. It can't notarize the .app — electron-builder passes build.mac.notarize.teamId alongside it and @electron/notarize refuses both at once. Set APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD instead."
else
  problem "a signing certificate is configured but no notarization credentials are. The .app would be signed and left un-notarized, and notarize-dmg.js fails the release when it finds no stapled ticket. Set APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD (RELEASING.md §2)."
fi

if [[ ${#auth[@]} -gt 0 ]]; then
  if history_err="$(xcrun notarytool history --output-format json "${auth[@]}" 2>&1 >/dev/null)"; then
    ok "notarization: authenticated as $auth_label (team $team)"
  else
    problem "Apple rejected the notarization credentials ($auth_label, team $team): ${history_err##*Error: }"
  fi
fi

# ── verdict ──────────────────────────────────────────────────────────────────────────────

echo
if [[ ${#problems[@]} -gt 0 ]]; then
  echo "these would break the release:"
  for p in "${problems[@]}"; do printf '  ✗ %s\n' "$p"; done
  echo
  exit 1
fi
echo "ready — these credentials produce a signed, notarized and stapled .dmg."
