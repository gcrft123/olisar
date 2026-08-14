"""Mint an admin console session for the arena operator. Runs as a subprocess.

Why a subprocess: ``olisar.config.settings`` is an ``lru_cache``d singleton read at import
time, and the database path comes from ``OLISAR_DATA_DIR``. Importing Olisar inside the
harness process would bind it to whichever environment the CLI happened to start with —
almost certainly the developer's own instance. Running this under
``ArenaConfig.child_env()`` guarantees the session is minted against the arena database and
signed with the arena's session secret.

Prints ``{"cookie": ..., "operator_id": ...}`` on stdout. Everything else goes to stderr so
the caller can parse stdout unconditionally.

This is not an authentication bypass in any meaningful sense: it runs with filesystem
access to the arena's own SQLite database, which is strictly more access than the session
it produces. It exists so an autonomous agent can drive the console without a browser
OAuth round-trip against a Discord app that only it uses.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def _main() -> int:
    from olisar.runtime import paths

    paths.bootstrap_env()

    operator_id = int(os.environ.get("ADMIN_ALLOWLIST", "0").split(",")[0] or 0)
    if not operator_id:
        print("ADMIN_ALLOWLIST is empty — set ARENA_OPERATOR_ID", file=sys.stderr)
        return 2

    from api.auth.sessions import create_session, sign_sid
    from olisar.db.engine import session_scope
    from olisar.db.models import AdminGrant, AdminUser, Guild

    guild_id = int(os.environ.get("TARGET_GUILD_ID", "0") or 0)

    async with session_scope() as session:
        admin = await session.get(AdminUser, operator_id)
        if admin is None:
            admin = AdminUser(discord_user_id=operator_id)
            session.add(admin)
        admin.username = admin.username or "arena-operator"
        # Allowlisted: `require_guild_admin` then admits the operator without the live
        # Manage-Server re-check against a running bot, so the console stays reachable
        # while the instance under test is stopped, restarting, or crashed — which is
        # exactly when the harness most needs to read its state.
        admin.is_allowlisted = True
        admin.granted_via = AdminGrant.allowlist
        if guild_id and str(guild_id) not in (admin.managed_guild_ids or []):
            admin.managed_guild_ids = [*(admin.managed_guild_ids or []), str(guild_id)]

        # `require_guild_admin` also insists the guild row exists and is active. It's
        # normally written when the bot connects; seed it so a mint before first boot
        # still yields a usable session.
        if guild_id:
            guild = await session.get(Guild, guild_id)
            if guild is None:
                session.add(Guild(id=guild_id, name="Arena", active=True))
            elif not guild.active:
                guild.active = True

    sid = await create_session(operator_id)
    cookie = await sign_sid(sid)
    print(json.dumps({"cookie": cookie, "operator_id": operator_id, "guild_id": guild_id}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
