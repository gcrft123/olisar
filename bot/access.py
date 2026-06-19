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


def resolve_member(bot: discord.Client, user: discord.abc.User) -> discord.Member | None:
    """A guild Member for ``user``: itself if already a Member, else the home-guild
    member by id (for DM senders), or None if they aren't a member / aren't cached."""
    if isinstance(user, discord.Member):
        return user
    home = bot.get_guild(settings.target_guild_id)
    return home.get_member(user.id) if home else None


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
