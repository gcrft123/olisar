# Hosting the Olisar backend on a server

Run the same unified backend the desktop app runs (API + Discord bot + dashboard) on a
small always-on Linux VM, so the bot stays online 24/7 without your computer. Tested
target: an **Oracle Cloud Free Tier** ARM instance (`VM.Standard.A1.Flex`, Ubuntu,
aarch64) — but any Linux VM with Docker works. Public access is via **Tailscale Funnel**
(a free `https://…ts.net` URL, no domain, no open ports).

## Quick start

On a fresh VM:

```sh
curl -fsSL https://raw.githubusercontent.com/gcrft123/olisar/main/deploy/bootstrap.sh | bash
```

It installs Docker, asks for your Discord credentials + a Gemini key + a Tailscale auth
key, starts the container, and prints your `https://…ts.net` URL and the OAuth redirect
to register. Then open the URL and sign in with Discord.

## Manual

```sh
cp .env.example .env && $EDITOR .env      # fill it in
docker compose up -d                      # pulls the prebuilt image (ghcr.io/gcrft123/olisar)
docker compose logs -f                    # copy the printed OLISAR_FUNNEL_URL=https://…ts.net
```

Build locally instead of pulling (e.g. from a clone): `docker compose up -d --build`.

## What you need

- **Discord app** — bot token + OAuth2 client id/secret (Developer Portal). Register the
  printed `…/auth/callback` under OAuth2 → Redirects.
- **Your Discord user ID** in `ADMIN_ALLOWLIST` so you can sign in to the console.
- **Gemini API key** (free) — required for the bot to reply.
- **Tailscale account + a reusable auth key** for the public URL. The first time, Tailscale
  may ask you to enable Funnel for the device — the logs print the link.

All settings are in [`.env.example`](.env.example).

## Notes

- **Persistence:** the SQLite DB, knowledge uploads, and the Tailscale node identity (so
  the URL stays stable) all live in the `olisar-data` Docker volume.
- **Updating:** `docker compose pull && docker compose up -d`.
- **`SESSION_SECRET`** is auto-generated and persisted on first run — don't set it.
- The image needs a Python with loadable SQLite extensions (sqlite-vec); the official
  `python` base image has this.
- The desktop app is **optional** once you host on a server — manage everything from the
  browser at your `…ts.net` URL. (Don't run both pointed at the same Discord bot token.)
