"""Discord-side glue for role-based access control.

Turns a message author or interaction user into the inputs ``access_allowed``
needs: their (non-@everyone) role ids and whether they're a server admin. DM users
aren't Members, so we resolve them to their membership in the home guild — that way
the same role rules apply in DMs and a blocked member can't sidestep them by DMing.
Someone who isn't a member of the home guild has no roles and isn't an admin there.
"""

from __future__ import annotations

import discord

from olisar import guild_approval, moderation
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


def resolve_member(
    bot: discord.Client, user: discord.abc.User, guild_id: int | None = None
) -> discord.Member | None:
    """A guild Member for ``user``: itself if already a Member, else ``user`` found in
    ``guild_id`` (where a command was run), or for a DM (no ``guild_id``) in the home guild,
    so the same role rules apply in DMs. None if they aren't a member there.

    Only the home guild counts in a DM, because a DM is checked against the home guild's
    lists. It used to fall back to any guild the bot shares with them, and then Manage
    Server in some other guild passed the home guild's allow list."""
    if isinstance(user, discord.Member):
        return user
    guild = bot.get_guild(guild_id or dm_home_guild_id(bot))
    if guild is None or guild_approval.is_pending(guild.id):
        return None  # not one the operator has let the bot work in
    return guild.get_member(user.id)


def _role_ids(member: discord.Member | None) -> set[int]:
    if member is None:
        return set()
    return {role.id for role in member.roles if not role.is_default()}


def member_allowed(
    member: discord.Member | None, *, allowed, blocked, user_id: int | None = None
) -> bool:
    """Whether ``member`` may use Olisar under the guild's access lists. A None member
    (e.g. a DM from a non-member) is treated as having no roles / no admin. Pass
    ``user_id`` (the raw author/interaction user id) so a globally-banned user is refused
    even when they aren't a guild member."""
    uid = user_id if user_id is not None else getattr(member, "id", None)
    if moderation.is_banned(uid):
        return False  # global ban (synced from the registry) overrides everything
    perms = getattr(member, "guild_permissions", None)
    return access_allowed(
        role_ids=_role_ids(member),
        is_admin=bool(perms and perms.manage_guild),
        allowed=allowed,
        blocked=blocked,
    )
