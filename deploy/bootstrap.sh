#!/usr/bin/env bash
#
# Olisar server bootstrap — run on a fresh Linux VM (e.g. an Oracle Cloud Free Tier
# Ubuntu ARM instance) to install Docker and bring up the bot's backend 24/7, reachable
# over Tailscale Funnel at an https://…ts.net URL (no domain, no open ports).
#
#   curl -fsSL https://raw.githubusercontent.com/gcrft123/olisar/main/deploy/bootstrap.sh | bash
#
# Reads answers interactively, or from matching env vars if already set (for scripting).
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/gcrft123/olisar/main/deploy"
DIR="${OLISAR_DIR:-$HOME/olisar}"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
ask() { # ask VAR "Prompt" — keep existing env value if set
  local var="$1" prompt="$2" cur="${!1:-}"
  if [ -n "$cur" ]; then printf '%s: (using value already set)\n' "$prompt"; return; fi
  printf '%s: ' "$prompt"; read -r "$var" </dev/tty
}

say "Installing Docker (if needed)…"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO usermod -aG docker "$USER" || true
fi
if $SUDO docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

mkdir -p "$DIR"; cd "$DIR"
say "Fetching the updater…"
# The updater owns the compose file: it resolves the newest release, pins that image by
# digest, applies it health-gated, and rolls back if it doesn't come up. Installing it here
# means a hand-bootstrapped VM behaves exactly like one the desktop app deployed.
curl -fsSL "$REPO_RAW/olisar-update.sh" -o olisar-update.sh
curl -fsSL "$REPO_RAW/olisar-update.service" -o olisar-update.service
curl -fsSL "$REPO_RAW/olisar-update.timer" -o olisar-update.timer
chmod +x olisar-update.sh

if [ ! -f .env ]; then
  say "Let's configure Olisar. (From the Discord Developer Portal + your API keys.)"
  ask DISCORD_TOKEN          "Discord bot token"
  ask DISCORD_CLIENT_ID      "Discord client ID"
  ask DISCORD_CLIENT_SECRET  "Discord client secret"
  ask ADMIN_ALLOWLIST        "Your Discord user ID (so you can sign in)"
  ask GEMINI_API_KEY         "Gemini API key (free — aistudio.google.com/apikey)"
  ask TAILSCALE_AUTH         "Tailscale auth key (login.tailscale.com/admin/settings/keys)"
  TARGET_GUILD_ID="${TARGET_GUILD_ID:-}"
  cat > .env <<EOF
DISCORD_TOKEN=${DISCORD_TOKEN}
DISCORD_CLIENT_ID=${DISCORD_CLIENT_ID}
DISCORD_CLIENT_SECRET=${DISCORD_CLIENT_SECRET}
ADMIN_ALLOWLIST=${ADMIN_ALLOWLIST}
TARGET_GUILD_ID=${TARGET_GUILD_ID}
GEMINI_API_KEY=${GEMINI_API_KEY}
TAILSCALE_AUTH=${TAILSCALE_AUTH}
OLISAR_FUNNEL_HOSTNAME=${OLISAR_FUNNEL_HOSTNAME:-olisar}
OLISAR_HEADLESS=1
EOF
  chmod 600 .env
else
  say ".env already exists — reusing it."
fi

say "Installing the daily update timer…"
if command -v systemctl >/dev/null 2>&1; then
  sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$USER|g" olisar-update.service \
    | $SUDO tee /etc/systemd/system/olisar-update.service >/dev/null
  $SUDO install -m 0644 olisar-update.timer /etc/systemd/system/olisar-update.timer
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now olisar-update.timer || true
else
  echo "(no systemd here — run ./olisar-update.sh yourself to update)"
fi

say "Pulling the latest Olisar release and starting it…"
./olisar-update.sh --start || { echo "Update/start failed — see the output above."; exit 1; }

say "Waiting for the public URL (this can take up to ~2 minutes on first run)…"
URL=""
for _ in $(seq 1 60); do
  # The backend publishes its public URL in state.json; fall back to the log marker.
  CID="$($SUDO $DC ps -q 2>/dev/null | head -1)"
  if [ -n "$CID" ]; then
    URL="$($SUDO docker exec "$CID" cat /var/lib/olisar/state.json 2>/dev/null \
      | grep -oE 'https://[A-Za-z0-9._-]+\.ts\.net' | head -1 || true)"
  fi
  [ -z "$URL" ] && URL="$($SUDO $DC logs 2>/dev/null | grep -oE 'OLISAR_FUNNEL_URL=https://[^ ]+' | tail -1 | cut -d= -f2- || true)"
  [ -n "$URL" ] && break
  sleep 3
done

echo
if [ -n "$URL" ]; then
  say "Olisar is live."
  cat <<EOF

  Dashboard / public URL:   $URL
  Discord OAuth redirect:   ${URL%/}/auth/callback

Next steps:
  1. In the Discord Developer Portal → your app → OAuth2 → Redirects, add:
       ${URL%/}/auth/callback
  2. Open $URL in a browser and sign in with Discord (the account whose ID you allowlisted).

Manage it later with:  cd $DIR && $DC logs -f   |   $DC restart   |   ./olisar-update.sh
(The update timer already runs daily — ./olisar-update.sh just does it now.)
EOF
else
  echo "Couldn't read the public URL yet. Check logs:  cd $DIR && $DC logs -f"
  echo "(Most often: a bad or already-used Tailscale auth key, or Funnel not enabled for the tailnet.)"
fi
