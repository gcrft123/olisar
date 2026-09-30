# Olisar extension registry

A Cloudflare Worker that hosts the extension marketplace catalog (D1) and the `.olx`
bundle blobs (R2). This is the **consume** API the bot's console browses and installs
from. The bot always re-transpiles and verifies a bundle locally on install, so the
registry is a discovery + distribution layer — never a trusted compiler.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/health` | liveness |
| GET | `/v1/search?q=&category=&limit=&offset=` | search the catalog |
| GET | `/v1/ext/:namespace/:name` | extension detail + versions |
| GET | `/v1/ext/:namespace/:name/:version` | download the `.olx` bundle (JSON) |
| POST | `/v1/publishers/challenge` | issue a single-use register nonce |
| POST | `/v1/publishers/register` | claim a handle, or rotate the token (signed nonce) |
| POST | `/v1/publishers/verify/start` | a Discord sign-in link for the publisher (bearer token) |
| GET | `/v1/publishers/verify/callback` | Discord's redirect; shows which publisher it will link |
| POST | `/v1/publishers/verify/confirm` | link the Discord account and set the verified badge |
| GET | `/v1/publishers/me` | the publisher's handle, fingerprint and verified flag (bearer token) |
| POST | `/v1/publish` | publish a bundle signed by the publisher's key (bearer token) |
| POST | `/v1/yank` | yank one of your extensions (bearer token) |
| POST | `/v1/_dev/publish` | **local-only** seeding (gated by `DEV_SEED`) |

The `/v1/dev/*` moderation routes and the report/standing routes are in `src/index.ts`.

## Publisher registration

A publisher is an Ed25519 key bound to a handle. Knowing a public key isn't enough to claim it (every signed bundle carries one), so registering proves the caller holds the private key:

1. `POST /v1/publishers/challenge` returns `{ "nonce": "<64 hex>", "expires_at": <unix seconds> }`. A nonce is good for 5 minutes and one attempt.
2. Sign the UTF-8 string `olisar-registry/register:<nonce>:<handle>` with the publisher key (handle lowercased), then `POST /v1/publishers/register` with `{ public_key, handle, nonce, signature }` (key and signature base64). The response carries the bearer token.

Registering again with the same key rotates the token and may rename the handle. The prefix keeps a bundle signature (which covers a bare `content_hash`) from ever passing as a register proof. The bot and `marketplace-extensions/_publish.py` build the message with `olisar.extensions.signing.register_message`.

Register never sets a Discord id; any `discord_id` in the body is ignored. Only Discord verification sets `discord_id` and `verified = 1`. The `/v1/dev/*` routes require a verified publisher whose Discord id is in the `developers` table.

## Discord verification

The registry runs the sign-in through its own Discord application, never a token a bot forwards: whoever runs a Discord app gets the token of everyone who signs in to it, so a forwarded token proves nothing about who wanted to verify what.

1. The bot calls `POST /v1/publishers/verify/start` with its publisher token and opens the returned `url` in a browser.
2. The person signs in with Discord, which redirects to `/v1/publishers/verify/callback`. The registry exchanges the code with its own secret, reads who signed in, and shows a page naming the Discord account and the publisher handle. It gives that browser a short-lived cookie.
3. Pressing the button posts to `/v1/publishers/verify/confirm`, which needs that cookie, then sets `discord_id` and `verified = 1`. A link someone else started can't be finished by them.
4. The bot reads the result from `GET /v1/publishers/me`.

The old `POST /v1/publishers/verify`, which took a forwarded token, answers 410.

Setup, once per registry:
- Create a Discord application for the registry in the [Developer Portal](https://discord.com/developers/applications). Its name is what the sign-in screen shows.
- Under OAuth2 → Redirects, add `https://<your worker>/v1/publishers/verify/callback`.
- Put its client ID in `wrangler.jsonc` as the `DISCORD_CLIENT_ID` var, and its client secret in a Worker secret: `npx wrangler secret put DISCORD_CLIENT_SECRET`.

Until both are set, `/v1/publishers/verify/start` answers 503. For local tests, `DISCORD_API` in `.dev.vars` points the token exchange at a stand-in for Discord.

## Local development (no Cloudflare account needed)

```bash
cd registry
npm install
cp .dev.vars.example .dev.vars   # enables the local /v1/_dev/publish seeding endpoint
npm run dev                 # wrangler dev — local D1 + R2 via Miniflare

# in another shell: generate signed seed bundles and load them
PYTHONPATH=.. uv run python scripts/gen_seed.py     # writes seed/*.json (from repo root: registry/scripts/gen_seed.py)
for f in seed/*.json; do curl -s -XPOST localhost:8787/v1/_dev/publish -d @"$f"; done

curl -s 'localhost:8787/v1/search' | jq
curl -s 'localhost:8787/v1/ext/olisar-demo/coin_flip' | jq
curl -s 'localhost:8787/v1/ext/olisar-demo/coin_flip/1.0.0' | jq   # the .olx
```

The Worker self-creates its tables on the first `/v1/_dev/publish` (so local dev needs no
migration step). `schema.sql` is the source of truth for a real deploy.

## Deploy (later, explicit)

```bash
wrangler d1 create olisar-registry          # paste the id into wrangler.jsonc
wrangler r2 bucket create olisar-registry-bundles
npm run schema:remote                        # apply schema.sql to the real D1
npm run deploy
```
`DEV_SEED` lives only in `.dev.vars` (local, gitignored), so a deployed registry never
exposes the seeding endpoint — no manual step needed.
