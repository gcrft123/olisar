"""A small Discord REST client for the harness.

Deliberately REST-only — no gateway, no ``discord.py`` client, no privileged intents.
The harness never needs to *receive* an event in real time: it drives turn-taking itself
and reads the channel back with ``GET /channels/{id}/messages?after=``, which also picks
up anything Olisar said on its own (a proactive chime, a tool-posted image) without a
websocket. That removes N gateway connections, N sets of portal toggles, and the whole
class of "the emulator silently disconnected mid-scenario" failures.

Every emulator, and the steward, is just a bot token wrapped in one of these.

Not covered, on purpose: receiving DMs. Discord refuses bot-to-bot DMs, so a scenario that
exercises Olisar's ``send_dm`` path can observe the *call* (via the audit log and Olisar's
own logs) but not the delivered message. DM-content scenarios run on the fast lane, or from
the operator's own account. See arena/README.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("arena.rest")

API = "https://discord.com/api/v10"

# Discord's own guidance: back off on 429 for the returned retry_after. Five attempts
# comfortably covers a burst of channel creations without masking a real outage.
_MAX_ATTEMPTS = 5


class DiscordError(RuntimeError):
    """A non-retryable Discord API error, carrying the status and body for the log."""

    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"{status} on {path}: {body[:400]}")
        self.status = status
        self.body = body


# Discord channel type ids (only the ones the harness creates).
CHANNEL_TEXT = 0
CHANNEL_CATEGORY = 4

# Permission bits used for private-channel overwrites.
PERM_VIEW_CHANNEL = 1 << 10
PERM_SEND_MESSAGES = 1 << 11
PERM_READ_HISTORY = 1 << 16


class DiscordRest:
    """One bot token's REST surface. Async-context-managed; share one per token."""

    def __init__(self, token: str, *, label: str = "") -> None:
        if not token:
            raise ValueError("a Discord bot token is required")
        self._token = token
        self.label = label or "bot"
        self._client: httpx.AsyncClient | None = None
        self._me: dict[str, Any] | None = None

    async def __aenter__(self) -> "DiscordRest":
        self._client = httpx.AsyncClient(
            base_url=API,
            timeout=httpx.Timeout(20.0),
            headers={
                "Authorization": f"Bot {self._token}",
                # Discord asks bots to identify themselves; an honest UA also makes the
                # arena's traffic obvious in any audit of the test server.
                "User-Agent": "OlisarArena (+https://github.com/olisar, harness)",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._client is None:
            raise RuntimeError("DiscordRest used outside its async context")
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response = await self._client.request(method, path, **kwargs)
            if response.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(response.json().get("retry_after", 1.0))
                except Exception:
                    pass
                # Cap the sleep: a global rate limit reports seconds, but a bad token or a
                # bot removed from the guild can return a long one, and a scenario that
                # silently naps for a minute reads as a hang.
                delay = min(retry_after, 10.0)
                log.warning("%s rate-limited on %s; retrying in %.1fs", self.label, path, delay)
                await asyncio.sleep(delay)
                continue
            if response.status_code in (500, 502, 503, 504) and attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(min(2.0 * attempt, 8.0))
                continue
            if response.status_code >= 400:
                raise DiscordError(response.status_code, response.text, path)
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise DiscordError(429, "exhausted retries", path)

    # ── identity ──────────────────────────────────────────────────────────

    async def me(self) -> dict[str, Any]:
        """This token's own user object. Cached — it never changes within a run."""
        if self._me is None:
            self._me = await self._request("GET", "/users/@me")
        return self._me

    async def my_id(self) -> int:
        return int((await self.me())["id"])

    # ── reading ───────────────────────────────────────────────────────────

    async def guild(self, guild_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/guilds/{guild_id}")

    async def channels(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/guilds/{guild_id}/channels") or []

    async def roles(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"/guilds/{guild_id}/roles") or []

    async def members(self, guild_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """Guild members. Needs the GUILD_MEMBERS privileged intent enabled on the
        *steward's* application (it's a REST call, but Discord gates it on the same
        toggle). arena/README.md lists it as a required portal switch."""
        return await self._request(
            "GET", f"/guilds/{guild_id}/members", params={"limit": limit}
        ) or []

    async def messages(
        self, channel_id: int, *, after: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Recent messages, oldest-first. ``after`` makes this an incremental poll — the
        harness's substitute for a gateway subscription."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = str(after)
        raw = await self._request("GET", f"/channels/{channel_id}/messages", params=params) or []
        return list(reversed(raw))  # Discord returns newest-first

    # ── writing ───────────────────────────────────────────────────────────

    async def send(
        self, channel_id: int, content: str, *, reply_to: int | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "content": content[:2000],
            # An emulator must never be able to ping @everyone or a real person into a
            # test run; the harness posts a lot and the server may have humans in it.
            "allowed_mentions": {"parse": []},
        }
        if reply_to:
            payload["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": False}
        return await self._request("POST", f"/channels/{channel_id}/messages", json=payload)

    async def typing(self, channel_id: int) -> None:
        """Show the typing indicator — emulators use it so pacing looks human in the
        recorded server, and so a human watching a run can follow along."""
        await self._request("POST", f"/channels/{channel_id}/typing")

    async def react(self, channel_id: int, message_id: int, emoji: str) -> None:
        await self._request(
            "PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"
        )

    # ── server management (steward only) ──────────────────────────────────

    async def create_channel(
        self,
        guild_id: int,
        name: str,
        *,
        kind: int = CHANNEL_TEXT,
        parent_id: int | None = None,
        topic: str = "",
        overwrites: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "type": kind}
        if parent_id:
            payload["parent_id"] = str(parent_id)
        if topic:
            payload["topic"] = topic[:1024]
        if overwrites is not None:
            payload["permission_overwrites"] = overwrites
        return await self._request("POST", f"/guilds/{guild_id}/channels", json=payload)

    async def delete_channel(self, channel_id: int) -> None:
        await self._request("DELETE", f"/channels/{channel_id}")

    async def create_role(
        self, guild_id: int, name: str, *, color: int = 0, permissions: str = "0"
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/guilds/{guild_id}/roles",
            json={"name": name, "color": color, "permissions": permissions, "mentionable": False},
        )

    async def delete_role(self, guild_id: int, role_id: int) -> None:
        await self._request("DELETE", f"/guilds/{guild_id}/roles/{role_id}")

    async def add_role(self, guild_id: int, user_id: int, role_id: int) -> None:
        await self._request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")

    async def remove_role(self, guild_id: int, user_id: int, role_id: int) -> None:
        await self._request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")

    async def audit_log(self, guild_id: int, limit: int = 25) -> dict[str, Any]:
        return await self._request(
            "GET", f"/guilds/{guild_id}/audit-logs", params={"limit": limit}
        ) or {}


def private_overwrites(guild_id: int, allowed_ids: list[int]) -> list[dict[str, Any]]:
    """Permission overwrites for a private channel: deny ``@everyone``, allow each id.

    ``@everyone``'s role id equals the guild id — that's the Discord convention, not a
    coincidence to look up. Type 0 is a role overwrite, type 1 a member overwrite.
    """
    allow = str(PERM_VIEW_CHANNEL | PERM_SEND_MESSAGES | PERM_READ_HISTORY)
    overwrites: list[dict[str, Any]] = [
        {"id": str(guild_id), "type": 0, "deny": str(PERM_VIEW_CHANNEL), "allow": "0"}
    ]
    overwrites += [{"id": str(uid), "type": 1, "allow": allow, "deny": "0"} for uid in allowed_ids]
    return overwrites
