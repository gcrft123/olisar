"""Discord server management for the arena, via the steward bot.

The agent needs real server administration, not just a chat channel: private channels it
isn't a member of, role-gated channels, categories, roles it can hand out and take away.
Access control is a large part of Olisar's behaviour surface (channel modes, allowed and
blocked roles, private-vs-public privacy handling), and none of it can be exercised against
a single flat public channel.

Everything the harness creates is prefixed ``ARENA_PREFIX`` and every role is suffixed
``ROLE_SUFFIX``. ``reset`` deletes only those. The arena server may well have channels a
human made, and a teardown that guessed would eventually delete one of them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from arena.config import ArenaConfig
from arena.discord_rest import (
    CHANNEL_CATEGORY,
    CHANNEL_TEXT,
    DiscordRest,
    private_overwrites,
)
from arena.fleet import registry

log = logging.getLogger("arena.guild")

ARENA_PREFIX = "arena-"
ROLE_SUFFIX = " (arena)"

_SLUG = re.compile(r"[^a-z0-9-]+")


def channel_slug(name: str) -> str:
    """Discord channel names: lowercase, no spaces, 100 chars. Always arena-prefixed so
    teardown can be certain of what it made."""
    slug = _SLUG.sub("-", name.strip().lower().replace(" ", "-")).strip("-")
    if not slug.startswith(ARENA_PREFIX):
        slug = ARENA_PREFIX + slug
    return slug[:100]


@dataclass(frozen=True)
class GuildSnapshot:
    """What the arena server currently looks like."""

    name: str
    channels: list[dict[str, Any]]
    roles: list[dict[str, Any]]
    members: list[dict[str, Any]]

    def channel_by_name(self, name: str) -> dict[str, Any] | None:
        wanted = channel_slug(name)
        for channel in self.channels:
            if channel.get("name") == wanted or str(channel.get("id")) == name:
                return channel
        return None

    def role_by_name(self, name: str) -> dict[str, Any] | None:
        wanted = name if name.endswith(ROLE_SUFFIX) else name + ROLE_SUFFIX
        for role in self.roles:
            if role.get("name") in (wanted, name):
                return role
        return None

    def arena_channels(self) -> list[dict[str, Any]]:
        return [c for c in self.channels if str(c.get("name", "")).startswith(ARENA_PREFIX)]

    def arena_roles(self) -> list[dict[str, Any]]:
        return [r for r in self.roles if str(r.get("name", "")).endswith(ROLE_SUFFIX)]


class Steward:
    """The Administrator bot that shapes the arena server."""

    def __init__(self, cfg: ArenaConfig) -> None:
        cfg.require("steward_token", "guild_id")
        self._cfg = cfg
        self._rest = DiscordRest(cfg.steward_token, label="steward")

    async def __aenter__(self) -> "Steward":
        await self._rest.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._rest.__aexit__(*exc)

    @property
    def rest(self) -> DiscordRest:
        return self._rest

    async def snapshot(self) -> GuildSnapshot:
        gid = self._cfg.guild_id
        guild = await self._rest.guild(gid)
        channels = await self._rest.channels(gid)
        roles = await self._rest.roles(gid)
        try:
            members = await self._rest.members(gid)
        except Exception:
            # Listing members needs the GUILD_MEMBERS intent toggled on the steward's
            # application. Everything else works without it, so degrade rather than fail
            # the whole snapshot — `arena doctor` reports the missing toggle explicitly.
            log.warning("couldn't list guild members — enable the Server Members Intent "
                        "on the steward application")
            members = []
        return GuildSnapshot(
            name=guild.get("name", ""), channels=channels, roles=roles, members=members
        )

    # ── channels ──────────────────────────────────────────────────────────

    async def ensure_category(self, name: str) -> int:
        snapshot = await self.snapshot()
        slug = channel_slug(name)
        for channel in snapshot.channels:
            if channel.get("type") == CHANNEL_CATEGORY and channel.get("name") == slug:
                return int(channel["id"])
        created = await self._rest.create_channel(self._cfg.guild_id, slug, kind=CHANNEL_CATEGORY)
        return int(created["id"])

    async def ensure_channel(
        self,
        name: str,
        *,
        private: bool = False,
        members: list[int] | None = None,
        category: str = "",
        topic: str = "",
        recreate: bool = False,
    ) -> int:
        """Create (or find) a channel and return its id.

        ``private=True`` denies ``@everyone`` and allows only ``members`` — which must
        include the arena Olisar's own id if Olisar is meant to see it. That's the point of
        the flag: a private channel Olisar *isn't* in is exactly how you test that it can't
        read what it shouldn't.

        ``recreate`` deletes and rebuilds an existing channel, so a scenario can start from
        a genuinely empty history rather than one polluted by the previous run.
        """
        gid = self._cfg.guild_id
        slug = channel_slug(name)
        snapshot = await self.snapshot()
        existing = snapshot.channel_by_name(slug)
        if existing and not recreate:
            return int(existing["id"])
        if existing and recreate:
            await self._rest.delete_channel(int(existing["id"]))
            log.info("recreated #%s from empty", slug)

        parent_id = await self.ensure_category(category) if category else None
        overwrites = None
        if private:
            allowed = list(members or [])
            if not allowed:
                raise ValueError(
                    "a private channel needs at least one allowed member id — otherwise "
                    "nothing, including the harness, can post in it"
                )
            overwrites = private_overwrites(gid, allowed)
        created = await self._rest.create_channel(
            gid, slug, kind=CHANNEL_TEXT, parent_id=parent_id, topic=topic, overwrites=overwrites
        )
        log.info("created #%s (%s)", slug, "private" if private else "public")
        return int(created["id"])

    async def delete_channel(self, name_or_id: str) -> bool:
        snapshot = await self.snapshot()
        channel = snapshot.channel_by_name(name_or_id)
        if channel is None:
            return False
        await self._rest.delete_channel(int(channel["id"]))
        return True

    # ── roles ─────────────────────────────────────────────────────────────

    async def ensure_role(self, name: str, *, color: int = 0) -> int:
        snapshot = await self.snapshot()
        existing = snapshot.role_by_name(name)
        if existing:
            return int(existing["id"])
        created = await self._rest.create_role(
            self._cfg.guild_id, name + ROLE_SUFFIX, color=color
        )
        log.info("created role %s", created.get("name"))
        return int(created["id"])

    async def assign_role(self, role_name: str, user_id: int) -> None:
        role_id = await self.ensure_role(role_name)
        await self._rest.add_role(self._cfg.guild_id, user_id, role_id)

    async def unassign_role(self, role_name: str, user_id: int) -> None:
        snapshot = await self.snapshot()
        role = snapshot.role_by_name(role_name)
        if role:
            await self._rest.remove_role(self._cfg.guild_id, user_id, int(role["id"]))

    # ── teardown ──────────────────────────────────────────────────────────

    async def reset(self) -> dict[str, int]:
        """Delete every arena-created channel and role. Leaves anything a human made."""
        snapshot = await self.snapshot()
        channels = snapshot.arena_channels()
        roles = snapshot.arena_roles()
        for channel in channels:
            await self._rest.delete_channel(int(channel["id"]))
        for role in roles:
            await self._rest.delete_role(self._cfg.guild_id, int(role["id"]))
        log.info("reset: removed %d channels, %d roles", len(channels), len(roles))
        return {"channels": len(channels), "roles": len(roles)}


async def olisar_user_id(cfg: ArenaConfig) -> int:
    """The arena Olisar's own Discord id — needed whenever a private channel is supposed
    to include it."""
    async with DiscordRest(cfg.discord_token, label="olisar") as rest:
        return await rest.my_id()


async def fleet_user_ids(cfg: ArenaConfig) -> list[int]:
    return registry.peer_ids(cfg)
