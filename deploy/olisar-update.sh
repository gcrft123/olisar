#!/usr/bin/env bash
#
# Olisar server self-update — pull the newest *release* and apply it, health-gated, with
# automatic rollback. Installed to ~/olisar/olisar-update.sh by the desktop app's deploy
# (and by deploy/bootstrap.sh), then driven over SSH by the client:
#
#   * automatically, when the app finds itself on a newer build than the VM — which is the
#     case every time it relaunches after updating itself
#   * by hand on the VM: ./olisar-update.sh
#
# One implementation, every trigger: the client never reimplements any of this. (There used
# to be a daily systemd timer here as well; the client drives updates now, so a VM that
# still has that timer installed gets it removed on the next connect.)
#
# The compose file is rewritten on every run and pinned to an immutable digest, so
# "what is deployed" is a fact on disk rather than whatever :latest happened to be. The
# previous digest is kept in versions.json — that is what makes rollback possible.
#
#   ./olisar-update.sh [--force] [--start] [--tag v2.0 | --tag v2.0.beta-1]
#
# Without --tag it resolves GitHub's latest release, which is always a stable one (a beta
# published there without its pre-release flag is refused). The app always passes --tag: the
# newest release on its own update channel, so a beta tester's VM runs the betas.
#
# It never moves the server to an older release than the one it runs: an app switched from
# beta to stable leaves its server on the beta until a stable release passes it, and so does
# a run by hand. --force does move it back, and re-applies the release it's already on.
#
# --start brings the container up even if it wasn't already running; that makes a first
# deploy the same code path as an update, so there's only one place that knows how to put
# a version onto this VM.
#
# Outcome is written to last-update.json (read by the control panel) and echoed.
set -uo pipefail

REPO="gcrft123/olisar"
IMAGE="ghcr.io/${REPO}"
# Resolve our own directory so this works identically over SSH and from a login shell
# (where $HOME may or may not be set).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_TIMEOUT="${OLISAR_HEALTH_TIMEOUT:-180}"   # Dockerfile start-period is 60s

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
if $SUDO docker compose version >/dev/null 2>&1; then DC="$SUDO docker compose"; else DC="$SUDO docker-compose"; fi

FORCE=0
START=0
WANT_TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --start) START=1 ;;
    --tag) WANT_TAG="${2:-}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$DIR" || { echo "no such directory: $DIR" >&2; exit 1; }

# One VM can run several bots, each from its own directory with its own copy of this script,
# and the app updates all of them when it relaunches. Take turns: two pulls and two health
# gates at once would compete for the same small VM. Best-effort — without flock, just run.
if command -v flock >/dev/null 2>&1; then
  exec 9>"${TMPDIR:-/tmp}/olisar-update.lock" && flock -w 1800 9 || true
fi

TAG=""; DIGEST=""; PREV_DIGEST=""; PREV_VERSIONS=""; FAIL_LOG=""; ROLLED_BACK=false; UPDATED=false

emit() {  # emit <ok:true|false> <status> <message>
  cat > "$DIR/last-update.json" <<EOF
{
  "ok": $1,
  "status": "$2",
  "message": "$3",
  "tag": "$TAG",
  "digest": "$DIGEST",
  "previous_digest": "$PREV_DIGEST",
  "rolled_back": $ROLLED_BACK,
  "updated": $UPDATED,
  "at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  echo "olisar-update: $2 — $3"
}

write_compose() {  # write_compose <image-ref>
  cat > "$DIR/docker-compose.yml" <<EOF
# Managed by olisar-update.sh — pinned to an immutable digest. Edits are overwritten.
services:
  olisar:
    image: $1
    env_file: .env
    volumes:
      - olisar-data:/var/lib/olisar
    restart: unless-stopped

volumes:
  olisar-data:
EOF
}

container_id() { $DC ps -q 2>/dev/null | head -1; }

is_running() {
  local cid; cid="$(container_id)"
  [ -n "$cid" ] || return 1
  [ "$($SUDO docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null)" = "running" ]
}

image_id() {  # image_id <image-ref> — that image's ID on this host, "" if it isn't here
  $SUDO docker image inspect --format '{{.Id}}' "$1" 2>/dev/null
}

# Wait for the container to be running <image-ref> AND passing its healthcheck. The image
# matters: when `up` doesn't replace the container, the old one is still there, and its
# health says nothing about the image just pinned. It counts as that image by its image ID
# or by the reference compose created it from (the pinned digest). Bails early on an
# explicit "unhealthy" verdict rather than burning the whole timeout. An image with no
# HEALTHCHECK (anything built before we added one) can only be judged on "running".
wait_healthy() {  # wait_healthy <image-ref>
  local ref="$1" want deadline=$((SECONDS + HEALTH_TIMEOUT)) cid img st hl
  want="$(image_id "$ref")"
  while [ $SECONDS -lt $deadline ]; do
    cid="$(container_id)"
    if [ -n "$cid" ]; then
      img="$($SUDO docker inspect --format '{{.Image}}|{{.Config.Image}}' "$cid" 2>/dev/null)"
      if { [ -n "$want" ] && [ "${img%%|*}" = "$want" ]; } || [ "${img#*|}" = "$ref" ]; then
        st="$($SUDO docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null)"
        hl="$($SUDO docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
        [ "$hl" = "unhealthy" ] && return 1
        if [ "$st" = "running" ] && { [ "$hl" = "healthy" ] || [ -z "$hl" ]; }; then return 0; fi
        if [ "$st" = "exited" ] || [ "$st" = "dead" ]; then return 1; fi
      fi
    fi
    sleep 3
  done
  return 1
}

# ${TAG} didn't come up: put the previous image back, if there is one, say how that went,
# and stop. Whether the rollback recovered is judged on the previous image running healthy,
# not on `up` succeeding: when the new one never started, the old container may still be up.
roll_back() {  # roll_back <what went wrong> <status: recovered> <status: didn't> <status: nothing to go back to>
  if [ -z "$PREV_DIGEST" ]; then
    emit false "$4" "${TAG} $1 and there is no previous image to roll back to"
  else
    write_compose "${IMAGE}@${PREV_DIGEST}"
    if [ -n "$PREV_VERSIONS" ]; then printf '%s\n' "$PREV_VERSIONS" > "$DIR/versions.json"; else rm -f "$DIR/versions.json"; fi
    ROLLED_BACK=true
    $DC up -d >/dev/null 2>&1
    if wait_healthy "${IMAGE}@${PREV_DIGEST}"; then
      emit false "$2" "${TAG} $1; rolled back to the previous image"
    else
      emit false "$3" "${TAG} $1 and the rollback did not recover"
    fi
  fi
  echo "$FAIL_LOG" >&2
  exit 1
}

# Bring up <image-ref>, which the compose file already pins, for --start, and stop: a re-run
# of a half-finished deploy brings the container up, and a redeploy that just wrote a new
# .env gets a container built from it (compose recreates when the resolved config changed and
# leaves an unchanged one alone). Skipping this for a running container left a redeploy's new
# keys unread: it kept the environment it started with, a dead Tailscale key included.
start_pinned() {  # start_pinned <image-ref> <status> <what the server is on>
  local out
  if ! out="$($DC up -d 2>&1)"; then
    emit false up-failed "$3 but docker compose up failed"
    echo "$out" | tail -20 >&2
    exit 1
  fi
  if wait_healthy "$1"; then
    if [ "$WAS_RUNNING" -eq 1 ]; then
      emit true "$2" "$3; applied its configuration"
    else
      emit true started "$3; started the server"
    fi
    exit 0
  fi
  emit false unhealthy "$3 but the server did not become healthy"
  exit 1
}

# ── versions ─────────────────────────────────────────────────────────────────
# Read the way olisar/versioning.py reads them ("v2.0", "v2.0.beta-1", "1.5.0",
# "2.0.0-beta.1"). Prints a key that sorts as the releases do, a beta before the release it
# leads up to (2.0.beta-9 < 2.0), or fails for anything that isn't a version.
version_key() {
  local re='^[vV]?([0-9]+)\.([0-9]+)(\.([0-9]+))?([.-]?([bB][eE][tT][aA]|[bB])[.-]?([0-9]+))?$' stable=1
  [[ "${1:-}" =~ $re ]] || return 1
  [ -n "${BASH_REMATCH[7]:-}" ] && stable=0
  printf '%06d.%06d.%06d.%d.%06d\n' "$((10#${BASH_REMATCH[1]}))" "$((10#${BASH_REMATCH[2]}))" \
    "$((10#${BASH_REMATCH[4]:-0}))" "$stable" "$((10#${BASH_REMATCH[7]:-0}))"
}

# Whether release $1 comes before release $2. False when either isn't a version, so nothing
# is refused on a guess.
older_than() {
  local a b
  a="$(version_key "$1")" && b="$(version_key "$2")" || return 1
  [[ "$a" < "$b" ]]
}

is_beta() {
  local key
  key="$(version_key "$1")" || return 1
  [ "$(echo "$key" | cut -d. -f4)" = 0 ]
}

json_field() {  # json_field <key> <file> — a string field of a JSON file this script wrote
  grep -m1 "^[[:space:]]*\"$1\"[[:space:]]*:" "$2" 2>/dev/null | sed -E 's/^[^:]*:[[:space:]]*"([^"]*)".*/\1/'
}

# ── what's deployed now ──────────────────────────────────────────────────────
CURRENT_REF="$(grep -oE '^[[:space:]]*image:[[:space:]]*\S+' "$DIR/docker-compose.yml" 2>/dev/null | head -1 | awk '{print $2}')"
case "$CURRENT_REF" in
  *@sha256:*) PREV_DIGEST="${CURRENT_REF##*@}" ;;
esac
# Which release that is: the version CI stamps on every image (the tag it was built from),
# or, for an image without one, the tag recorded here when that same digest was pinned.
DEPLOYED=""
if [ -n "$CURRENT_REF" ]; then
  DEPLOYED="$($SUDO docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$CURRENT_REF" 2>/dev/null)"
  if ! version_key "$DEPLOYED" >/dev/null && [ -n "$PREV_DIGEST" ] \
    && [ "$(json_field digest "$DIR/versions.json")" = "$PREV_DIGEST" ]; then
    DEPLOYED="$(json_field tag "$DIR/versions.json")"
  fi
fi
WAS_RUNNING=0; is_running && WAS_RUNNING=1

# ── which release do we want ─────────────────────────────────────────────────
TAG="$WANT_TAG"
if [ -z "$TAG" ]; then
  LATEST="$(curl -fsSL --max-time 20 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null)"
  TAG="$(printf '%s\n' "$LATEST" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  # GitHub never calls a pre-release its latest release, but it would a beta published
  # without that flag. Without --tag this is the stable path, so a beta is refused either way.
  case "$LATEST" in *'"prerelease":true'*|*'"prerelease": true'*) BETA="$TAG" ;; *) BETA="" ;; esac
  is_beta "$TAG" && BETA="$TAG"
  if [ -n "$BETA" ]; then
    TAG=""
    emit false no-release "GitHub's latest release, ${BETA}, is a beta; pass --tag to install a beta"
    exit 1
  fi
fi
if [ -z "$TAG" ]; then
  emit false no-release "could not resolve the latest release tag from GitHub"
  exit 1
fi

# ── never backwards ──────────────────────────────────────────────────────────
# The server stays on a newer release than the one asked for (see the top of this file). With
# --start it's still brought up on it, with whatever .env a redeploy just wrote.
if [ "$FORCE" -eq 0 ] && older_than "$TAG" "$DEPLOYED"; then
  AHEAD="the server is on ${DEPLOYED#[vV]}, which is newer than ${TAG#[vV]}"
  echo "olisar-update: --force installs ${TAG} anyway" >&2
  [ "$START" -eq 1 ] && start_pinned "$CURRENT_REF" server-ahead "$AHEAD"
  emit true server-ahead "$AHEAD"
  exit 0
fi

# ── tell the console ─────────────────────────────────────────────────────────
# The running container keeps serving the console through the pull, which is the slow part.
# This file in its data directory is how the console knows to say "Updating…" rather than
# carry on as if nothing were happening (olisar/updates.py, `updating`). Only a running
# container can be told. The trap takes the file away on every way out, from whichever
# container is up by then: the new one, or the one rolled back to. If none is, the file stays
# behind, and the backend ignores one that names its own version or has gone stale.
UPDATING_FILE="/var/lib/olisar/updating.json"
mark_updating() {
  case "$TAG" in *[!A-Za-z0-9._-]*) return 0 ;; esac
  is_running || return 0
  $DC exec -T olisar sh -c "printf '{\"tag\": \"%s\", \"at\": \"%s\"}\n' '$TAG' '$(date -u +%Y-%m-%dT%H:%M:%SZ)' > $UPDATING_FILE" >/dev/null 2>&1 || true
}
clear_updating() {
  is_running || return 0
  $DC exec -T olisar rm -f "$UPDATING_FILE" >/dev/null 2>&1 || true
}
mark_updating
trap clear_updating EXIT

# ── pull and resolve the tag to an immutable digest ──────────────────────────
if ! PULL_OUT="$($SUDO docker pull "${IMAGE}:${TAG}" 2>&1)"; then
  emit false pull-failed "docker pull ${IMAGE}:${TAG} failed"
  echo "$PULL_OUT" | tail -20 >&2
  exit 1
fi
DIGEST="$($SUDO docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "${IMAGE}:${TAG}" 2>/dev/null | sed -E 's/.*@//')"
if [ -z "$DIGEST" ]; then
  emit false no-digest "pulled ${TAG} but could not resolve its digest"
  exit 1
fi

if [ "$DIGEST" = "$PREV_DIGEST" ] && [ "$FORCE" -eq 0 ]; then
  # Already pinned to this digest. --start still runs `up -d`, running or not.
  [ "$START" -eq 1 ] && start_pinned "${IMAGE}@${DIGEST}" up-to-date "already on ${TAG}"
  emit true up-to-date "already on ${TAG}"
  exit 0
fi
NEW_REF="${IMAGE}@${DIGEST}"

PREV_VERSIONS="$(cat "$DIR/versions.json" 2>/dev/null)"
cat > "$DIR/versions.json" <<EOF
{
  "tag": "$TAG",
  "digest": "$DIGEST",
  "previous_digest": "$PREV_DIGEST",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
write_compose "$NEW_REF"

# A stopped bot stays stopped — we still repin it, so the operator's next Start boots the
# new version deliberately instead of silently jumping. (--start overrides: a first deploy
# has nothing running yet by definition.)
if [ "$WAS_RUNNING" -eq 0 ] && [ "$START" -eq 0 ]; then
  UPDATED=true
  emit true staged "pinned ${TAG}; the server is stopped, so it was not started"
  exit 0
fi

# A failed `up` usually leaves the old container running, healthy, on the old image: it is
# a failed update, whatever the health gate would say about that container.
if ! FAIL_LOG="$($DC up -d 2>&1)"; then
  roll_back "could not be started" up-failed up-failed up-failed
fi
if wait_healthy "$NEW_REF"; then
  UPDATED=true
  $SUDO docker image prune -f >/dev/null 2>&1 || true
  emit true updated "updated to ${TAG} and healthy"
  exit 0
fi

# ── health gate failed → roll back ───────────────────────────────────────────
FAIL_LOG="$($DC logs --tail 50 --no-color 2>/dev/null | tail -50)"
roll_back "failed its healthcheck" rolled-back rollback-unhealthy unhealthy
