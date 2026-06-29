"""Effective deployment config: first-run wizard value (``app_config`` row) when set,
else the ``.env`` value from ``settings``.

The config analogue of :mod:`olisar.runtime_keys`. It lets the packaged desktop app be
configured entirely from the setup wizard — Discord credentials, the public URL, the
Tailscale Funnel — with no ``.env`` file, while a developer's ``.env`` keeps working
unchanged (every getter falls back to ``settings``). A short in-process cache mirrors
``runtime_keys``; callers invalidate after a save.
"""

from __future__ import annotations

import logging
import secrets
import time

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig

log = logging.getLogger("olisar.runtime_config")

# String fields read straight through (DB value, else .env).
_STR_FIELDS = (
    "discord_token",
    "discord_client_id",
    "discord_client_secret",
    "session_secret",
    "public_base_url",
    "tunnel_hostname",
    "tunnel_node",
    "tunnel_token",
)
_CACHE_TTL = 5.0  # seconds
_INSECURE_DEFAULT = "dev-insecure-secret"  # the .env placeholder; never sign with it

_cache: dict | None = None
_cache_at = 0.0
_local_base_url = ""  # the loopback origin the unified server is listening on


def set_local_base_url(url: str) -> None:
    """Record the actual loopback origin the unified backend bound to (the port is
    chosen at runtime), so ``public_base_url()`` can return it in local mode."""
    global _local_base_url
    _local_base_url = url.rstrip("/")


def local_base_url() -> str:
    """The loopback origin Olisar is actually serving on — the Tailscale Funnel proxies
    here. Falls back to the configured api port outside the unified app."""
    return _local_base_url or f"http://127.0.0.1:{settings.api_port}"


async def _load() -> dict:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _CACHE_TTL:
        return _cache
    values: dict = {}
    try:
        async with session_scope() as session:
            row = await session.get(AppConfig, 1)
            if row is not None:
                for f in _STR_FIELDS:
                    values[f] = getattr(row, f, "") or ""
                values["target_guild_id"] = int(row.target_guild_id or 0)
                values["tunnel_enabled"] = bool(row.tunnel_enabled)
                values["configured"] = bool(row.configured)
                values["extension_risk_threshold"] = int(
                    getattr(row, "extension_risk_threshold", None) or 70
                )
    except Exception:
        # app_config may not exist yet (fresh DB) — fall back to .env silently-ish.
        log.debug("reading app_config failed; falling back to .env config", exc_info=True)
    _cache, _cache_at = values, now
    return values


def invalidate() -> None:
    """Drop the cache so the next read re-queries the DB (call after a save)."""
    global _cache
    _cache = None


async def _str(field: str) -> str:
    db_value = (await _load()).get(field) or ""
    return db_value or getattr(settings, field, "") or ""


async def discord_token() -> str:
    return await _str("discord_token")


async def discord_client_id() -> str:
    return await _str("discord_client_id")


async def discord_client_secret() -> str:
    return await _str("discord_client_secret")


async def target_guild_id() -> int:
    db_value = (await _load()).get("target_guild_id") or 0
    return int(db_value) or int(settings.target_guild_id or 0)


async def tunnel_enabled() -> bool:
    return bool((await _load()).get("tunnel_enabled"))


async def tunnel_hostname() -> str:
    return await _str("tunnel_hostname")


async def tunnel_node() -> str:
    return await _str("tunnel_node")


async def tunnel_token() -> str:
    return await _str("tunnel_token")


async def public_base_url() -> str:
    """The origin admins reach the dashboard at: the tunnel host when enabled, else a
    stored override, else the live loopback origin, else the .env default (dev)."""
    data = await _load()
    if data.get("tunnel_enabled") and data.get("tunnel_hostname"):
        return f"https://{data['tunnel_hostname']}"
    override = data.get("public_base_url") or ""
    if override:
        return override.rstrip("/")
    if _local_base_url:
        return _local_base_url
    return settings.public_base_url.rstrip("/")


async def oauth_redirect_uri() -> str:
    return (await public_base_url()).rstrip("/") + "/auth/callback"


async def session_secret() -> str:
    """Resolve the cookie-signing secret. Prefer the DB value; else a real .env value;
    else generate a strong secret once and persist it so it survives restarts (a
    changing secret would invalidate every signed session cookie)."""
    data = await _load()
    if data.get("session_secret"):
        return data["session_secret"]
    env_secret = getattr(settings, "session_secret", "") or ""
    if env_secret and env_secret != _INSECURE_DEFAULT:
        return env_secret
    generated = secrets.token_urlsafe(48)
    try:
        await save(session_secret=generated)
    except Exception:
        log.exception("could not persist generated session secret; using ephemeral one")
    return generated


async def extension_risk_threshold() -> int:
    """The risk score (0-100) at/above which publishing an extension is blocked. Stored in
    app_config; defaults to 70. Clamped to a sane range."""
    raw = (await _load()).get("extension_risk_threshold")
    val = int(raw) if raw is not None else 70
    return max(1, min(val, 100))


async def is_configured() -> bool:
    """Whether first-run setup is complete. The DB ``configured`` flag normally counts —
    a populated ``.env`` no longer auto-skips the wizard, so the operator/dev confirms
    each desktop install once (pre-filled from ``.env`` if available).

    Exception: a **headless server deployment** (``OLISAR_HEADLESS=1``, e.g. the Docker
    image on a cloud VM) with the essential Discord credentials supplied via env is
    treated as configured, so it serves the dashboard rather than the loopback-only
    wizard (which can't be reached over the public Funnel)."""
    if bool((await _load()).get("configured")):
        return True
    return bool(
        settings.headless
        and settings.discord_token
        and settings.discord_client_id
        and settings.discord_client_secret
    )


async def save(**fields: object) -> None:
    """Upsert the singleton ``app_config`` row with the given non-None fields, then
    drop the cache so the next read reflects them."""
    async with session_scope() as session:
        row = await session.get(AppConfig, 1)
        if row is None:
            row = AppConfig(id=1)
            session.add(row)
        for key, value in fields.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
    invalidate()
