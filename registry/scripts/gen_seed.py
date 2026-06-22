"""Generate signed .olx seed payloads for local registry testing.

Reuses the bot's own bundle + signing modules so the seeds are real, signed bundles
(byte-identical to what an export would produce). Writes one JSON payload per extension
into registry/seed/, each shaped for the dev-only /v1/_dev/publish endpoint:
    { "namespace": ..., "publisher": {handle, public_key, fingerprint, verified}, "bundle": {...} }

Run from the repo root:  PYTHONPATH=. uv run python registry/scripts/gen_seed.py
"""

from __future__ import annotations

import json
from pathlib import Path

from olisar.extensions import bundle, signing

OUT = Path(__file__).resolve().parents[1] / "seed"

# One demo publisher signs everything.
PRIV, PUB = signing.generate()
FP = signing.fingerprint(PUB)
PUBLISHER = {"handle": "olisar-demo", "public_key": PUB, "fingerprint": FP, "verified": True}

SAMPLES = [
    {
        "id": "coin_flip",
        "name": "Coin Flip",
        "version": "1.0.0",
        "category": "Games",
        "description": "Flip a coin from chat — a tiny SDK tool.",
        "permissions": [],
        "source": (
            'defineExtension({\n'
            '  id: "coin_flip", name: "Coin Flip", version: "1.0.0", category: "Games",\n'
            '  description: "Flip a coin from chat.",\n'
            '  tools: [{ name: "coin_flip", description: "Flip a coin; returns heads or tails.",\n'
            '    parameters: { type: "object", properties: {} },\n'
            '    handler: (): string => (Math.random() < 0.5 ? "heads" : "tails") }],\n'
            '});\n'
        ),
    },
    {
        "id": "weather_lookup",
        "name": "Weather Lookup",
        "version": "0.2.0",
        "category": "Utilities",
        "description": "Look up current weather for a city via a public API.",
        "permissions": ["fetch"],
        "source": (
            'interface Args { city: string }\n'
            'defineExtension({\n'
            '  id: "weather_lookup", name: "Weather Lookup", version: "0.2.0", category: "Utilities",\n'
            '  description: "Look up current weather for a city.",\n'
            '  permissions: ["fetch"],\n'
            '  tools: [{ name: "weather", description: "Current weather for a city.",\n'
            '    parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] },\n'
            '    handler: async (args: Args): Promise<string> => {\n'
            '      const r = await host.fetch("https://wttr.in/" + encodeURIComponent(args.city) + "?format=3");\n'
            '      return await r.text();\n'
            '    } }],\n'
            '});\n'
        ),
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for s in SAMPLES:
        doc = bundle.build_bundle(
            ext_id=s["id"], name=s["name"], version=s["version"], category=s["category"],
            description=s["description"], source=s["source"], permissions=s["permissions"],
            author_id=None, author_name=PUBLISHER["handle"],
        )
        signing.sign_bundle(doc, PRIV, PUB)
        payload = {"namespace": PUBLISHER["handle"], "publisher": PUBLISHER, "bundle": doc}
        path = OUT / f"{s['id']}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {path}  (signed by {FP})")


if __name__ == "__main__":
    main()
