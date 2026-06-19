# Setting up Olisar

Olisar is a self-hosted AI Discord bot. You run **one app on your own machine**; it hosts
the bot for your Discord server(s) and serves the admin console. There's no server to rent
and no `.env` to edit — everything is configured in a first-run wizard.

Because each install uses **your own** Discord bot and **your own** free API keys, you
stay on the free tiers (Gemini, and optionally Cloudflare). Your data — the message index,
member profiles, knowledge base, and settings — lives only on your machine.

Two ways to run it:

- **Desktop app** (recommended) — install, complete the wizard, done. Steps 1–6 below.
- **From source** (developers) — run the unified backend directly, or build the installer
  yourself. See [Building & running from source](#building--running-from-source).

---

## 1. Install the desktop app

Download the build for your OS, then open it:

- **macOS** (Apple Silicon) — open `Olisar-<version>-arm64.dmg`, drag **Olisar** to
  Applications. The app is unsigned and runs a bundled helper process, so the reliable way
  to clear Gatekeeper is to run this **once** in Terminal:
  ```sh
  xattr -dr com.apple.quarantine /Applications/Olisar.app
  ```
  (Right-click → Open approves the main app but can still leave the bundled backend
  blocked, so prefer the command above.) Then open Olisar normally.
- **Windows** — run `Olisar Setup <version>.exe`. SmartScreen may warn about an unknown
  publisher: click **More info → Run anyway**.

A bot must stay running to respond, so install it on a machine that's usually on (your
desktop, a home server, a mini PC). Closing the window keeps Olisar running in the menu
bar / system tray — use **Quit Olisar** from the tray to fully stop it.

---

## 2. Create your Discord application (once)

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. **New Application** → name it (e.g. "Olisar").
2. **Bot** tab → **Reset Token** → copy it. Under *Privileged Gateway Intents*, enable
   **Message Content Intent** and **Server Members Intent** (Olisar needs both).
3. **OAuth2** tab → copy the **Client ID** and **Client Secret** (Reset to reveal).
4. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`, give it permission
   to read/send messages, and use the generated URL to **invite the bot to your server**.

You'll paste the token, client ID, and client secret into the setup wizard next. Keep the
portal tab open — the wizard shows a **redirect URL** to add under **OAuth2 → Redirects**.

---

## 3. First-run setup wizard

Launch Olisar. The window opens to a 4-step wizard:

1. **Bot token** — paste it and click *Test token* (Olisar confirms "Connected as …").
2. **Application** — paste Client ID + Client Secret. Optionally set your main server's ID
   (right-click the server in Discord with Developer Mode on → *Copy Server ID*).
3. **Access** — choose **This machine only** (you administer locally) or **Remote access**
   (other admins sign in over a tunnel — see §4). The wizard shows the exact **redirect
   URL** to paste into the Developer Portal → **OAuth2 → Redirects**. For local use that's
   `http://127.0.0.1:<port>/auth/callback`.
4. **API keys** — paste your free **Gemini API key**
   ([Google AI Studio](https://aistudio.google.com/apikey)). Cloudflare (image generation)
   and UEX (Star Citizen) are optional and can be added later under **API keys**.

Click **Finish & start Olisar**. The bot connects and the window reloads to **Continue
with Discord** — sign in with the Discord account that has *Manage Server* on your server
to reach the console.

> **Access is live-checked.** Only accounts with *Manage Server* on a server Olisar is in
> (or an allowlisted operator) can open the console — and if that permission is removed,
> access is revoked on the next request.

---

## 4. Remote access (optional) — Tailscale Funnel

By default the console is reachable only on your machine. To let other server admins sign
in from anywhere, Olisar can expose it over **Tailscale Funnel** — a free, stable public
`https://…ts.net` address with **no domain and no port-forwarding required**. Olisar runs
the bundled Tailscale helper for you; the admins who sign in don't need Tailscale at all.

1. Create a free [Tailscale account](https://login.tailscale.com/start) (sign in with
   Google, GitHub, Microsoft, etc.).
2. Generate an **auth key** at
   [Settings → Keys → Generate auth key](https://login.tailscale.com/admin/settings/keys) —
   turn on **Reusable**. Copy it (`tskey-auth-…`).
3. In the setup wizard **Access** step (or the menu-bar icon), pick **Remote access**,
   paste the auth key, optionally set a device name, and click **Enable remote access**.
4. The first time, Tailscale may ask you to turn on **Funnel** for this device — Olisar
   shows the exact link to click; click it, then press **Enable remote access** again.
5. Add **both** redirect URLs the wizard shows to the Developer Portal → OAuth2 →
   Redirects: the loopback one **and** `https://<your-name>.<tailnet>.ts.net/auth/callback`.

Once it's on, the console's sidebar shows an **Open from the web** link with a Copy button —
share that with your other admins. The URL is **stable** across restarts, so you register
it once. The auth key is stored locally and only ever passed to the bundled Tailscale
helper — it's never shown in the console or sent anywhere.

---

## 5. Where your data lives

- **macOS** — `~/Library/Application Support/Olisar/`
- **Windows** — `%APPDATA%\Olisar\`

This holds `olisar.db` (everything Olisar knows) and uploaded knowledge-base files. Back up
this folder to keep your bot's memory; delete it to start fresh.

---

## 6. Troubleshooting

- **"Backend: starting…" never turns to online** — open the tray → *Refresh status*. If the
  tray shows *vector engine FAILED*, the build is missing its native library; reinstall.
- **Login says you lack access** — sign in with an account that has *Manage Server* on a
  server the bot is in, and confirm the redirect URL in the portal matches exactly.
- **Bot is online but never replies** — add a Gemini API key under **API keys**. The free
  tier rate-limits under load; replies may be briefly delayed.
- **Privileged intents error on start** — enable Message Content + Server Members intents in
  the Developer Portal → Bot.
- **Discord login bounces / "invalid or expired state"** — the redirect URL for the address
  you're using isn't registered. Add the exact `…/auth/callback` the wizard shows (both the
  loopback and `…ts.net` ones for remote access).

---

## Building & running from source

Requires **Python 3.13** (a Homebrew Python on macOS, *not* Apple's system Python — the
system build disables `enable_load_extension`, which `sqlite-vec` needs),
[uv](https://docs.astral.sh/uv/), and **Node 18+**.

Run the unified backend (bot + API + dashboard) directly, no Electron:

```sh
uv sync --all-extras                                   # create .venv + install deps
cd web && npm install && npm run build && cd ..        # build the dashboard once
OLISAR_DATA_DIR=/tmp/olisar uv run python -m olisar.runtime --port 8800
```

Open `http://127.0.0.1:8800/` — you'll get the same first-run wizard, then the console.

For dashboard development with hot-reload, run the API and the Vite dev server separately:

```sh
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
cd web && npm run dev                                   # dev server on :5173
```

Build the desktop installer:

```sh
cd web && npm run build && cd ..                        # 1. dashboard
uv run pyinstaller desktop/backend.spec --noconfirm --clean   # 2. bundle the backend
# 3. (optional) build the Tailscale Funnel helper — see desktop/resources/README.md
cd desktop && npm install && npm run dist               # 4. installer for the current OS
#   npm run dist:mac   → unsigned .dmg + .app
#   npm run dist:win   → NSIS .exe   (run on Windows / CI)
```
