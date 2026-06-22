# Olisar extension registry

A Cloudflare Worker that hosts the extension marketplace catalog (D1) and the `.olx`
bundle blobs (R2). This is the **consume** API the bot's console browses and installs
from. The bot always re-transpiles and verifies a bundle locally on install, so the
registry is a discovery + distribution layer — never a trusted compiler.

## Endpoints (read-only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/health` | liveness |
| GET | `/v1/search?q=&category=&limit=&offset=` | search the catalog |
| GET | `/v1/ext/:namespace/:name` | extension detail + versions |
| GET | `/v1/ext/:namespace/:name/:version` | download the `.olx` bundle (JSON) |
| POST | `/v1/_dev/publish` | **local-only** seeding (gated by `DEV_SEED`) |

The authenticated publish pipeline (Discord OAuth + signature verification + namespacing)
lands in a later phase; `/v1/_dev/publish` is a local stand-in for it.

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
