"""Which Discord account each emulator token belongs to.

Olisar has to be told the emulators' user ids (``OLISAR_PEER_BOT_IDS``) *before* it starts,
or it treats them as ordinary bots and ignores everything they say. The ids aren't in the
tokens, so they're resolved once with ``GET /users/@me`` per token and cached next to the
arena database. The cache is keyed by persona and stores the id, username, and a token
fingerprint, so replacing a token is detected and re-resolved rather than silently reusing
the previous bot's id — which would look like "Olisar ignores exactly one emulator".

``peer_ids`` is deliberately a pure cache read with no network: it is called while building
the child process environment, where an await on Discord would be both slow and a strange
place to fail.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from arena.config import ArenaConfig
from arena.discord_rest import DiscordRest
from arena.fleet.persona import third_party_keys

log = logging.getLogger("arena.registry")


@dataclass(frozen=True)
class FleetMember:
    """A resolved emulator: its persona key and the Discord account behind the token."""

    key: str
    user_id: int
    username: str


def _cache_path(cfg: ArenaConfig) -> Path:
    return cfg.data_dir / "fleet.json"


def _fingerprint(token: str) -> str:
    """A short digest of the token, so a swapped token invalidates its cache entry
    without the cache file ever containing a credential."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _read(cfg: ArenaConfig) -> dict[str, dict]:
    path = _cache_path(cfg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("fleet cache at %s is unreadable — re-resolving", path)
        return {}
    return data if isinstance(data, dict) else {}


def _write(cfg: ArenaConfig, data: dict[str, dict]) -> None:
    path = _cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def cached(cfg: ArenaConfig) -> list[FleetMember]:
    """Every emulator whose cached id still matches its current token."""
    data = _read(cfg)
    members: list[FleetMember] = []
    for key, token in sorted(cfg.fleet_tokens.items()):
        entry = data.get(key)
        if not entry or entry.get("fingerprint") != _fingerprint(token):
            continue
        members.append(
            FleetMember(key=key, user_id=int(entry["user_id"]), username=entry.get("username", key))
        )
    return members


def peer_ids(cfg: ArenaConfig) -> list[int]:
    """Resolved emulator ids, for ``OLISAR_PEER_BOT_IDS``. Cache-only, never network.

    Personas flagged ``third_party_bot`` are excluded: they are playing the server's *other*
    bots, and the whole point is that Olisar sees them as bots. Allowlisting them would make
    ``see_other_bots`` untestable, because there would be no bot left in the arena for it to
    govern.
    """
    excluded = third_party_keys()
    ids = [m.user_id for m in cached(cfg) if m.key not in excluded]
    return sorted(set(ids))


async def resolve(cfg: ArenaConfig, *, force: bool = False) -> list[FleetMember]:
    """Resolve every configured token to its account, refreshing the cache.

    Also resolves the arena Olisar token (when set) purely to check it isn't one of the
    emulator tokens — the single most likely setup mistake, and one whose symptom (Olisar
    replying to itself forever) is expensive to debug from the outside.
    """
    data = _read(cfg)
    resolved: list[FleetMember] = []

    olisar_id = 0
    if cfg.discord_token:
        try:
            async with DiscordRest(cfg.discord_token, label="olisar") as rest:
                olisar_id = await rest.my_id()
        except Exception:
            log.warning("couldn't identify the arena Olisar token", exc_info=True)

    for key, token in sorted(cfg.fleet_tokens.items()):
        fingerprint = _fingerprint(token)
        entry = data.get(key)
        if not force and entry and entry.get("fingerprint") == fingerprint:
            resolved.append(
                FleetMember(key, int(entry["user_id"]), entry.get("username", key))
            )
            continue
        async with DiscordRest(token, label=key) as rest:
            me = await rest.me()
        user_id, username = int(me["id"]), me.get("username", key)
        if olisar_id and user_id == olisar_id:
            raise ValueError(
                f"ARENA_BOT_TOKEN_{key.upper()} is the same bot as ARENA_DISCORD_TOKEN "
                f"({username}). Each emulator needs its own Discord application."
            )
        data[key] = {"user_id": user_id, "username": username, "fingerprint": fingerprint}
        resolved.append(FleetMember(key, user_id, username))
        log.info("resolved emulator %s -> %s (%s)", key, username, user_id)

    duplicates = {m.user_id for m in resolved}
    if len(duplicates) != len(resolved):
        raise ValueError(
            "two emulator tokens belong to the same Discord application — "
            "each persona needs its own bot."
        )

    _write(cfg, data)
    return resolved
