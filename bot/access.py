"""Discord-side glue for role-based access control.

Turns a message author or interaction user into the inputs ``access_allowed``
needs: their (non-@everyone) role ids and whether they're a server admin. DM users
aren't Members, so we resolve them to their membership in the home guild — that way
the same role rules apply in DMs and a blocked member can't sidestep them by DMing.
"""

from __future__ import annotations

import discord

from olisar.access import access_allowed
from olisar.config import settings


def dm_home_guild_id(bot: discord.Client) -> int:
    """The guild whose config + persona govern DMs: the configured ``target_guild_id`` if
    the bot is actually in it, otherwise the first guild the bot *is* in — so DMs still work
    (and use a real server's persona, knowledge, and roles) even when ``target_guild_id`` is
    stale or points at a server the bot has since left. Falls back to ``target_guild_id`` if
    the bot is in no guild at all."""
    target = settings.target_guild_id
    if target and bot.get_guild(target) is not None:
        return target
    guilds = bot.guilds
    return guilds[0].id if guilds else target


def resolve_member(bot: discord.Client, user: discord.abc.User) -> discord.Member | None:
    """A guild Member for ``user``: itself if already a Member, else the DM sender found in
    the home guild — or, failing that, in any guild the bot shares with them — so the same
    role rules apply in DMs. None if they aren't a member of any of the bot's guilds."""
    if isinstance(user, discord.Member):
        return user
    home = bot.get_guild(dm_home_guild_id(bot))
    member = home.get_member(user.id) if home else None
    if member is not None:
        return member
    for guild in bot.guilds:  # fall back to any shared guild (target may be stale)
        member = guild.get_member(user.id)
        if member is not None:
            return member
    return None


def _role_ids(member: discord.Member | None) -> set[int]:
    if member is None:
        return set()
    return {role.id for role in member.roles if not role.is_default()}


def member_allowed(member: discord.Member | None, *, allowed, blocked) -> bool:
    """Whether ``member`` may use Olisar under the guild's access lists. A None
    member (e.g. a DM from a non-member) is treated as having no roles / no admin."""
    perms = getattr(member, "guild_permissions", None)
    return access_allowed(
        role_ids=_role_ids(member),
        is_admin=bool(perms and perms.manage_guild),
        allowed=allowed,
        blocked=blocked,
    )
