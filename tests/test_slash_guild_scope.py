"""/ask and /catchup follow the rules of the server they're run in.

Run:  uv run python -m unittest tests.test_slash_guild_scope -v

Both commands read the home server's settings wherever they were used, so in any other
server the bot is in, that server's role gate didn't apply (a role it blocked could still
use /ask there) and neither did its mention policy (the reply could ping what it blocked).
Each now reads the settings of the server the command came from, and resolves the member
there; a DM still borrows the home server.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.cogs import slash as slash_mod
from olisar import pipeline
from olisar.db.models import Base, GuildConfig
from olisar.guild_setup import ensure_guild_defaults

HOME = 1001
OTHER = 2002
CHANNEL = 3003
USER = 5005
BLOCKED_ROLE = 7008
BOT_ID = 9


class _Chan:
    id = CHANNEL
    name = "general"
    topic = ""


class _Slash(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as s:
            await ensure_guild_defaults(s, HOME, name="Home")
            await ensure_guild_defaults(s, OTHER, name="Elsewhere")

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def configure(self, guild_id: int, **values) -> None:
        async with self.scope() as s:
            cfg = await s.get(GuildConfig, guild_id)
            for key, value in values.items():
                setattr(cfg, key, value)

    def cog_and_interaction(self, guild_id: int, *, roles=()):
        member = NS(id=USER, roles=list(roles), guild_permissions=NS(manage_guild=False),
                    display_name="m")
        other = NS(id=OTHER, get_member=lambda uid: member if uid == USER else None)
        home = NS(id=HOME, get_member=lambda uid: None)
        cog = slash_mod.Slash.__new__(slash_mod.Slash)
        cog.bot = NS(user=NS(id=BOT_ID), guilds=[home, other],
                     get_guild={HOME: home, OTHER: other}.get)
        interaction = NS(
            guild_id=guild_id, channel_id=CHANNEL, channel=_Chan(), user=member, id=77,
            response=NS(send_message=AsyncMock(), defer=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )
        return cog, interaction

    def patched(self, **extra):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(slash_mod, "session_scope", self.scope))
        stack.enter_context(patch.object(slash_mod.settings, "target_guild_id", HOME))
        stack.enter_context(patch("bot.access.settings.target_guild_id", HOME))
        for name, value in extra.items():
            stack.enter_context(patch.object(slash_mod, name, value))
        return stack


class Ask(_Slash):
    async def test_a_role_the_server_blocked_is_refused_there(self):
        await self.configure(OTHER, blocked_role_ids=[str(BLOCKED_ROLE)])
        role = NS(id=BLOCKED_ROLE, is_default=lambda: False)
        cog, inter = self.cog_and_interaction(OTHER, roles=[role])
        gen = AsyncMock(return_value=pipeline.Reply("hi"))
        with self.patched(generate_reply=gen):
            await slash_mod.Slash.ask.callback(cog, inter, "hello")
        gen.assert_not_awaited()
        inter.response.send_message.assert_awaited_once()

    async def test_the_home_servers_block_list_doesnt_apply_elsewhere(self):
        await self.configure(HOME, blocked_role_ids=[str(BLOCKED_ROLE)])
        role = NS(id=BLOCKED_ROLE, is_default=lambda: False)
        cog, inter = self.cog_and_interaction(OTHER, roles=[role])
        gen = AsyncMock(return_value=pipeline.Reply("hi"))
        with self.patched(generate_reply=gen):
            await slash_mod.Slash.ask.callback(cog, inter, "hello")
        gen.assert_awaited_once()

    async def test_the_reply_follows_that_servers_mention_policy(self):
        await self.configure(HOME, blocked_mentions=[])
        await self.configure(OTHER, blocked_mentions=["everyone", "here", "roles"])
        cog, inter = self.cog_and_interaction(OTHER)
        gen = AsyncMock(return_value=pipeline.Reply("@everyone raid now"))
        with self.patched(generate_reply=gen):
            await slash_mod.Slash.ask.callback(cog, inter, "say it")
        text = inter.followup.send.await_args.args[0]
        mentions = inter.followup.send.await_args.kwargs["allowed_mentions"]
        self.assertNotIn("@everyone", text)
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.roles)


class Catchup(_Slash):
    async def test_a_role_the_server_blocked_is_refused_there(self):
        await self.configure(OTHER, blocked_role_ids=[str(BLOCKED_ROLE)])
        role = NS(id=BLOCKED_ROLE, is_default=lambda: False)
        cog, inter = self.cog_and_interaction(OTHER, roles=[role])
        catchup = AsyncMock(return_value="all quiet")
        with self.patched(generate_catchup=catchup):
            await slash_mod.Slash.catchup.callback(cog, inter, None)
        catchup.assert_not_awaited()

    async def test_the_digest_follows_that_servers_mention_policy(self):
        await self.configure(HOME, blocked_mentions=[])
        await self.configure(OTHER, blocked_mentions=["everyone", "here", "roles"])
        cog, inter = self.cog_and_interaction(OTHER)
        catchup = AsyncMock(return_value="someone said @here meeting")
        with self.patched(generate_catchup=catchup):
            await slash_mod.Slash.catchup.callback(cog, inter, None)
        text = inter.followup.send.await_args.args[0]
        self.assertNotIn("@here", text)
        self.assertFalse(inter.followup.send.await_args.kwargs["allowed_mentions"].everyone)


if __name__ == "__main__":
    unittest.main()
