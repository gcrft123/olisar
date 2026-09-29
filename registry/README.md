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
| POST | `/v1/publishers/verify` | bind a Discord id via Discord OAuth (bearer token) |
| POST | `/v1/publish` | publish a bundle signed by the publisher's key (bearer token) |
| POST | `/v1/yank` | yank one of your extensions (bearer token) |
| POST | `/v1/_dev/publish` | **local-only** seeding (gated by `DEV_SEED`) |

The `/v1/dev/*` moderation routes and the report/standing routes are in `src/index.ts`.

## Publisher registration

A publisher is an Ed25519 key bound to a handle. Knowing a public key isn't enough to claim it (every signed bundle carries one), so registering proves the caller holds the private key:

1. `POST /v1/publishers/challenge` returns `{ "nonce": "<64 hex>", "expires_at": <unix seconds> }`. A nonce is good for 5 minutes and one attempt.
2. Sign the UTF-8 string `olisar-registry/register:<nonce>:<handle>` with the publisher key (handle lowercased), then `POST /v1/publishers/register` with `{ public_key, handle, nonce, signature }` (key and signature base64). The response carries the bearer token.

Registering again with the same key rotates the token and may rename the handle. The prefix keeps a bundle signature (which covers a bare `content_hash`) from ever passing as a register proof. The bot and `marketplace-extensions/_publish.py` build the message with `olisar.extensions.signing.register_message`.

Register never sets a Discord id; any `discord_id` in the body is ignored. Only `/v1/publishers/verify`, which checks the caller's OAuth token with Discord, sets `discord_id` and `verified = 1`. The `/v1/dev/*` routes require a verified publisher whose Discord id is in the `developers` table.

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
