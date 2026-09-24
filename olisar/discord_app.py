"""The bot's own Discord application: who operates it, and what setup can do for it.

A self-hosted Olisar packaged as a desktop app has no ``ADMIN_ALLOWLIST`` env
(that only exists for ``.env``-driven source runs), so the operator is defined as
**whoever owns the Discord application the bot runs as** — fetched from
``GET /applications/@me`` with the bot token. For a personally-owned app that's the
owner; for a team-owned app it's the team owner plus its members. The result is
cached (ownership is stable) and combined with ``ADMIN_ALLOWLIST`` in the auth flow,
so operator features work in the packaged app with zero configuration.

The same call answers most of what setup used to send the operator to the Developer
Portal for: the client ID, whether the privileged intents are on, and which OAuth
redirect URLs are registered. An app in fewer than 100 servers may switch its own
intents on, so setup does that instead of asking. Redirect URLs are the exception:
Discord ignores them in ``PATCH /applications/@me``, so setup can only check for them.
"""

from __future__ import annotations

import base64
import logging
import time
import urllib.parse

import aiohttp

log = logging.getLogger("olisar.discord_app")

_API = "https://discord.com/api/v10"
_ENDPOINT = f"{_API}/applications/@me"
_TTL = 3600.0  # owner is stable; refresh hourly to pick up an ownership transfer
_cache: dict | None = None
_cache_at = 0.0

# What Olisar does in a server, and nothing more: it reads and answers messages (threads
# included), reacts, and posts generated images and extension embeds. No roles,
# moderation, webhooks, pins or voice. Mention Everyone is left out on purpose: replies
# allow @everyone, so granting it would let a model-written @everyone ping a whole server.
INVITE_PERMISSIONS = {
    "add_reactions": 1 << 6,
    "view_channel": 1 << 10,
    "send_messages": 1 << 11,
    "embed_links": 1 << 14,
    "attach_files": 1 << 15,
    "read_message_history": 1 << 16,
    "send_messages_in_threads": 1 << 38,
}
INVITE_PERMISSIONS_VALUE = sum(INVITE_PERMISSIONS.values())
INVITE_SCOPES = ("bot", "applications.commands")

# Application flag bits for each privileged intent: (granted once the app is verified,
# self-serve while it's in fewer than 100 servers). Either one lets the bot connect.
_INTENT_FLAGS = {
    "message_content": (1 << 18, 1 << 19),
    "members": (1 << 14, 1 << 15),
    "presences": (1 << 12, 1 << 13),
}


class BadToken(Exception):
    """Discord rejected the bot token."""


class DiscordUnavailable(Exception):
    """Discord answered with something other than the application (an outage, a rate
    limit). Unreachable Discord raises ``aiohttp.ClientError`` instead."""


def invite_url(client_id: str | int) -> str:
    """The link that adds the bot to a server with exactly the permissions it uses."""
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": " ".join(INVITE_SCOPES),
        "permissions": INVITE_PERMISSIONS_VALUE,
    })
    return f"https://discord.com/oauth2/authorize?{query}"


def required_intents() -> list[str]:
    """The privileged intents ``bot/client.py`` asks for. Discord refuses the whole
    connection if any of them is off, so the bot just stays offline."""
    from olisar.config import settings

    names = ["message_content", "members"]
    if settings.enable_presence_intent:
        names.append("presences")
    return names


def missing_intents(flags: int, wanted: list[str]) -> list[str]:
    return [n for n in wanted if not flags & (_INTENT_FLAGS[n][0] | _INTENT_FLAGS[n][1])]


def _bot(token: str) -> dict:
    return {"Authorization": f"Bot {token}"}


async def _call(
    method: str, url: str, *, headers: dict, json: dict | None = None, data: dict | None = None,
) -> tuple[int, object]:
    """One Discord API request: (status, parsed body). Raises ``aiohttp.ClientError`` when
    Discord can't be reached. The one seam the tests replace."""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=headers, json=json, data=data) as resp:
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                body = None
            return resp.status, body


def _extract_owner_ids(app: dict) -> set[int]:
    """Pull the operator user IDs out of a Discord application object."""
    ids: set[int] = set()
    owner = app.get("owner") or {}
    if owner.get("id"):
        ids.add(int(owner["id"]))
    team = app.get("team") or {}
    if team:
        if team.get("owner_user_id"):
            ids.add(int(team["owner_user_id"]))
        for member in team.get("members") or []:
            uid = (member.get("user") or {}).get("id")
            if uid:
                ids.add(int(uid))
    return ids


async def application() -> dict | None:
    """The configured bot's application object, cached for ``_TTL`` seconds.

    Best-effort: None if the token is missing or Discord is unreachable. Failures are
    not cached, so a transient error can't lock the operator out permanently.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _TTL:
        return _cache

    from olisar.runtime_config import discord_token

    token = await discord_token()
    if not token:
        return None
    try:
        status, app = await _call("GET", _ENDPOINT, headers=_bot(token))
    except Exception:
        log.exception("application fetch errored")
        return None
    if status != 200 or not isinstance(app, dict):
        log.warning("application fetch failed (HTTP %s)", status)
        return None
    _cache, _cache_at = app, now
    return app


async def owner_ids() -> set[int]:
    """Discord user IDs that own/control the bot's application — i.e. the operators.
    Empty when the application can't be read (callers fall back to ``ADMIN_ALLOWLIST``)."""
    app = await application()
    return _extract_owner_ids(app) if app else set()


def invalidate() -> None:
    """Drop the cached application (e.g. after the bot token changes)."""
    global _cache
    _cache = None


# ── Setup ───────────────────────────────────────────────────────────────────────────
# These take the token explicitly: during setup it's the one the operator just pasted,
# not yet saved anywhere.


async def _read(token: str) -> dict:
    status, app = await _call("GET", _ENDPOINT, headers=_bot(token))
    if status == 401:
        raise BadToken()
    if status != 200 or not isinstance(app, dict):
        raise DiscordUnavailable(status)
    return app


def _avatar(app: dict) -> str:
    bot = app.get("bot") or {}
    if bot.get("id") and bot.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{bot['id']}/{bot['avatar']}.png"
    if app.get("icon"):
        return f"https://cdn.discordapp.com/app-icons/{app['id']}/{app['icon']}.png"
    return ""


def _summary(app: dict) -> dict:
    """What the setup wizard shows and gates on."""
    return {
        "id": str(app["id"]),
        "username": (app.get("bot") or {}).get("username") or app.get("name") or "",
        "avatar": _avatar(app),
        "bot_public": bool(app.get("bot_public", True)),
        # With this on, an invite link needs a full OAuth round trip, so a plain one fails.
        "code_grant": bool(app.get("bot_require_code_grant")),
        "intents_missing": missing_intents(int(app.get("flags") or 0), required_intents()),
        "redirect_uris": list(app.get("redirect_uris") or []),
        "invite_url": invite_url(app["id"]),
    }


def _install_leaves_out_bot(app: dict) -> bool:
    """Whether the app's default server install (the "Add App" button on the bot's
    profile) adds its commands without the bot itself, which is Discord's default for
    a new application. A custom install URL, or a guild install the operator turned off,
    is theirs to keep."""
    if app.get("custom_install_url"):
        return False
    guild = (app.get("integration_types_config") or {}).get("0")
    params = (guild or {}).get("oauth2_install_params")
    return bool(params) and "bot" not in (params.get("scopes") or [])


async def prepare(token: str) -> dict:
    """Read the bot's application and fix what it can fix on itself: switch on the
    privileged intents Olisar can't connect without, and make the default install add
    the bot. A fix Discord refuses (an app in 100+ servers can't self-serve intents) is
    left for the operator, reported through ``intents_missing``.

    Raises ``BadToken`` if Discord rejects the token, ``DiscordUnavailable`` or
    ``aiohttp.ClientError`` if it can't answer.
    """
    app = await _read(token)
    bot = app.get("bot")

    flags = int(app.get("flags") or 0)
    missing = missing_intents(flags, required_intents())
    if missing:
        add = 0
        for name in missing:
            add |= _INTENT_FLAGS[name][1]
        status, patched = await _call("PATCH", _ENDPOINT, headers=_bot(token), json={"flags": flags | add})
        if status == 200 and isinstance(patched, dict):
            app = patched
            log.info("turned on intents for application %s: %s", app.get("id"), ", ".join(missing))
        else:
            log.warning("couldn't turn on intents (HTTP %s): %s", status, str(patched)[:200])

    if _install_leaves_out_bot(app):
        # Both fields, in one request: Discord only sometimes copies the legacy
        # install_params into the guild install's config, which is what "Add App" reads.
        # The user install ("1") is passed back unchanged.
        params = {"scopes": list(INVITE_SCOPES), "permissions": str(INVITE_PERMISSIONS_VALUE)}
        config = {**app["integration_types_config"], "0": {"oauth2_install_params": params}}
        status, patched = await _call(
            "PATCH", _ENDPOINT, headers=_bot(token),
            json={"install_params": params, "integration_types_config": config},
        )
        if status == 200 and isinstance(patched, dict):
            app = patched
        else:
            log.warning("couldn't set the default install (HTTP %s): %s", status, str(patched)[:200])

    # A PATCH answers with the application, but nothing promises it carries the bot user.
    if bot and not app.get("bot"):
        app = {**app, "bot": bot}
    return _summary(app)


async def inspect(token: str) -> dict:
    """The same summary as ``prepare``, read-only: for checking again while the operator
    registers a redirect URL or turns an intent on by hand."""
    return _summary(await _read(token))


async def bot_guilds(token: str) -> list[dict]:
    """The servers the bot is in. Works whether or not the bot is running: joining a
    server is an OAuth grant, not a gateway event."""
    status, guilds = await _call("GET", f"{_API}/users/@me/guilds", headers=_bot(token))
    if status == 401:
        raise BadToken()
    if status != 200 or not isinstance(guilds, list):
        raise DiscordUnavailable(status)
    return [
        {
            "id": str(g["id"]),
            "name": g.get("name") or str(g["id"]),
            "icon": f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else "",
        }
        for g in guilds
    ]


async def check_client_secret(client_id: str, client_secret: str) -> bool:
    """Whether the client secret belongs to the client ID, via a client-credentials grant
    (a token for the app's owner; team apps may ask for ``identify`` too). Nobody has to
    sign in for this, so a wrong secret shows up now rather than at the first sign-in."""
    pair = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    status, _ = await _call(
        "POST", f"{_API}/oauth2/token",
        headers={"Authorization": f"Basic {pair}"},
        data={"grant_type": "client_credentials", "scope": "identify"},
    )
    if status == 200:
        return True
    if status in (400, 401):
        return False
    raise DiscordUnavailable(status)
