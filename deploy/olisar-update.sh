#!/usr/bin/env bash
#
# Olisar server self-update — pull the newest *release* and apply it, health-gated, with
# automatic rollback. Installed to ~/olisar/olisar-update.sh by the desktop app's deploy
# (and by deploy/bootstrap.sh), then driven from two places:
#
#   * olisar-update.timer  — daily, so a server updates even when nobody opens the app
#   * the desktop control panel's "Update now" — same script over SSH
#
# One implementation, two triggers: the client no longer reimplements any of this.
#
# The compose file is rewritten on every run and pinned to an immutable digest, so
# "what is deployed" is a fact on disk rather than whatever :latest happened to be. The
# previous digest is kept in versions.json — that is what makes rollback possible.
#
#   ./olisar-update.sh [--force] [--start] [--tag vX.Y.Z]
#
# --start brings the container up even if it wasn't already running; that makes a first
# deploy the same code path as an update, so there's only one place that knows how to put
# a version onto this VM.
#
# Outcome is written to last-update.json (read by the control panel) and echoed.
set -uo pipefail

REPO="gcrft123/olisar"
IMAGE="ghcr.io/${REPO}"
# Resolve our own directory so this works identically from SSH, cron and systemd (where
# $HOME may be unset).
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

TAG=""; DIGEST=""; PREV_DIGEST=""; ROLLED_BACK=false; UPDATED=false

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

# Wait for the container to be up AND passing its healthcheck. Bails early on an explicit
# "unhealthy" verdict rather than burning the whole timeout. An image with no HEALTHCHECK
# (anything built before we added one) can only be judged on "running".
wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) cid st hl
  while [ $SECONDS -lt $deadline ]; do
    cid="$(container_id)"
    if [ -n "$cid" ]; then
      st="$($SUDO docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null)"
      hl="$($SUDO docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
      [ "$hl" = "unhealthy" ] && return 1
      if [ "$st" = "running" ] && { [ "$hl" = "healthy" ] || [ -z "$hl" ]; }; then return 0; fi
      if [ "$st" = "exited" ] || [ "$st" = "dead" ]; then return 1; fi
    fi
    sleep 3
  done
  return 1
}

# ── what's deployed now ──────────────────────────────────────────────────────
CURRENT_REF="$(grep -oE '^[[:space:]]*image:[[:space:]]*\S+' "$DIR/docker-compose.yml" 2>/dev/null | head -1 | awk '{print $2}')"
case "$CURRENT_REF" in
  *@sha256:*) PREV_DIGEST="${CURRENT_REF##*@}" ;;
esac

# ── which release do we want ─────────────────────────────────────────────────
TAG="$WANT_TAG"
if [ -z "$TAG" ]; then
  TAG="$(curl -fsSL --max-time 20 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
    | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
fi
if [ -z "$TAG" ]; then
  emit false no-release "could not resolve the latest release tag from GitHub"
  exit 1
fi

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

WAS_RUNNING=0; is_running && WAS_RUNNING=1

if [ "$DIGEST" = "$PREV_DIGEST" ] && [ "$FORCE" -eq 0 ]; then
  # Already pinned to this digest. Still honour --start so a re-run of a half-finished
  # deploy brings the container up rather than reporting success over a dead VM.
  if [ "$START" -eq 1 ] && [ "$WAS_RUNNING" -eq 0 ]; then
    $DC up -d >/dev/null 2>&1
    if wait_healthy; then
      emit true started "already on ${TAG}; started the server"
      exit 0
    fi
    emit false unhealthy "already on ${TAG} but the server did not become healthy"
    exit 1
  fi
  emit true up-to-date "already on ${TAG}"
  exit 0
fi
NEW_REF="${IMAGE}@${DIGEST}"

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

$DC up -d >/dev/null 2>&1
if wait_healthy; then
  UPDATED=true
  $SUDO docker image prune -f >/dev/null 2>&1 || true
  emit true updated "updated to ${TAG} and healthy"
  exit 0
fi

# ── health gate failed → roll back ───────────────────────────────────────────
FAIL_LOG="$($DC logs --tail 50 --no-color 2>/dev/null | tail -50)"
if [ -n "$PREV_DIGEST" ]; then
  write_compose "${IMAGE}@${PREV_DIGEST}"
  $DC up -d >/dev/null 2>&1
  ROLLED_BACK=true
  if wait_healthy; then
    emit false rolled-back "${TAG} failed its healthcheck; rolled back to the previous image"
  else
    emit false rollback-unhealthy "${TAG} failed its healthcheck and the rollback did not recover"
  fi
else
  emit false unhealthy "${TAG} failed its healthcheck and there is no previous image to roll back to"
fi
echo "$FAIL_LOG" >&2
exit 1
