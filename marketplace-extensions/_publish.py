"""Publish the marketplace extensions in this folder under the `olisar` handle, each
signed with the bot's Ed25519 key. Reads the signing identity from the DESKTOP app's
database (the bot that owns the `olisar` namespace on the registry) so it can't pick up
a stale key. Run from the repo root:  uv run python marketplace-extensions/_publish.py
"""
import asyncio
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

import httpx

from olisar.config import settings
from olisar.extensions import signing, bundle
from olisar.sandbox import transpile
from olisar import sandbox
from olisar.sandbox.transpile import SDK_VERSION

REG = (settings.registry_url or "https://olisar-registry.gabrielyp.workers.dev").rstrip("/")
HANDLE = "olisar"
DISCORD_ID = "1089250623490359378"
AUTHOR_ID = 1089250623490359378
AUTHOR_NAME = "Olisar"
HERE = os.path.dirname(os.path.abspath(__file__))
APP_DB = os.path.expanduser("~/Library/Application Support/Olisar/olisar.db")


async def main():
    con = sqlite3.connect(APP_DB)
    priv_key, pub_key, fp = con.execute(
        "select private_key, public_key, fingerprint from signing_identity"
    ).fetchone()
    con.close()
    if not (priv_key and pub_key):
        print("no signing identity in the app DB"); return
    print(f"signing key fingerprint={fp}")

    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(REG + "/v1/publishers/register",
                         json={"public_key": pub_key, "handle": HANDLE, "discord_id": DISCORD_ID})
        if r.status_code != 200:
            print("REGISTER FAILED", r.status_code, r.text[:300]); return
        data = r.json()
        token = data["token"]
        print(f"handle={data['handle']!r} fingerprint={data.get('fingerprint')}")

    only = sys.argv[1] if len(sys.argv) > 1 else None  # optional: publish just one file
    files = sorted(glob.glob(os.path.join(HERE, "*.js")))
    if only:
        files = [f for f in files if os.path.basename(f) == only]
    okc = 0
    for f in files:
        src = open(f, encoding="utf-8").read()
        try:
            compiled = await transpile.transpile(src)
            m = await sandbox.extract_manifest(compiled)
        except Exception as e:
            print(f"[FAIL] {os.path.basename(f)}: build error: {e}"); continue
        doc = bundle.build_bundle(
            ext_id=m["id"], name=m.get("name", m["id"]), version=m.get("version", "1.0.0"),
            category=m.get("category", ""), description=m.get("description", ""),
            source=src, permissions=m.get("permissions", []),
            sdk_version=SDK_VERSION, author_id=AUTHOR_ID, author_name=AUTHOR_NAME,
        )
        signing.sign_bundle(doc, priv_key, pub_key)
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(REG + "/v1/publish", json={"bundle": doc},
                             headers={"authorization": "Bearer " + token})
        if r.status_code == 200:
            okc += 1
            b = r.json()
            print(f"[OK]   {m['id']:13} -> {b.get('namespace', HANDLE)}/{b.get('name', m['id'])} v{b.get('version', m.get('version'))}")
        else:
            print(f"[FAIL] {m['id']:13} HTTP {r.status_code}: {r.text[:200]}")
    print(f"\npublished {okc}/{len(files)} under '{HANDLE}'")


asyncio.run(main())
