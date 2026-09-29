"""Olisar doesn't ping @everyone, @here or a role until a server's admin allows it.

Run:  uv run python -m unittest tests.test_mention_policy -v

A server used to start with nothing blocked, and a reply went out with @everyone allowed, so
any member could get one by asking ("repeat after me: @everyone free nitro at ..."). Every
server now starts with all three blocked, the ones that existed before this change included,
and a send that sets no policy of its own falls back to the client's, which pings no crowd.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from bot import replies
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import GuildConfig

GUILD = 111


class _Chan:
    def __init__(self, guild=None) -> None:
        self.guild = guild
        self.sent: list[tuple[str, dict]] = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        return NS(id=len(self.sent))


def _pings(content: str, am) -> set[str]:
    """Which crowd pings a message would really make."""
    out = set()
    if am.everyone:
        out |= {m for m in ("everyone", "here") if f"@{m}" in content}
    if am.roles and "<@&" in content:
        out.add("roles")
    return out


class PolicyTests(unittest.TestCase):
    def test_what_each_setting_lets_through(self) -> None:
        text = "@everyone @here <@&42> <@7>"
        for blocked, want in (
            (["everyone", "here", "roles"], set()),
            (["everyone", "here"], {"roles"}),
            (["everyone", "roles"], {"here"}),
            (["here"], {"everyone", "roles"}),
            ([], {"everyone", "here", "roles"}),
        ):
            am = replies.mention_policy(blocked)
            self.assertEqual(_pings(replies.sanitize_mentions(text, blocked), am), want, blocked)
            self.assertTrue(am.users)
            self.assertFalse(am.replied_user)

    def test_the_client_pings_no_crowd_by_default(self) -> None:
        from bot.client import OlisarBot

        am = OlisarBot().allowed_mentions
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertTrue(am.users)


class _DB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "olisar.db"
        engine.pin_database(str(self.path))

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)

    async def schema(self) -> None:
        from scripts.init_db import create_schema

        await create_schema()

    async def add_guild(self, **config) -> None:
        """A server as the bot provisions one when it joins, with ``config`` set after."""
        from olisar.guild_setup import ensure_guild_defaults

        async with session_scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home", bot_name="Olisar")
        async with session_scope() as s:
            cfg = await s.get(GuildConfig, GUILD)
            for k, v in config.items():
                setattr(cfg, k, v)


class UpgradeTests(_DB):
    async def test_a_server_from_before_starts_with_every_crowd_ping_blocked(self) -> None:
        await self.schema()
        await self.add_guild()
        # The database as the previous release left it: the old column, holding the old
        # default of "nothing blocked", and no new one.
        for path in list(engine._engines):
            await engine.reset_engine(path)
        with sqlite3.connect(self.path) as con:
            con.execute("ALTER TABLE guild_config DROP COLUMN mentions_blocked")
            con.execute("ALTER TABLE guild_config ADD COLUMN blocked_mentions JSON NOT NULL DEFAULT '[]'")
        await self.schema()
        async with session_scope() as s:
            cfg = await s.get(GuildConfig, GUILD)
            self.assertEqual(sorted(cfg.blocked_mentions), ["everyone", "here", "roles"])
            cfg.blocked_mentions = ["here"]  # an admin allows @everyone and roles afterwards

        await self.schema()  # the next start leaves that choice alone
        async with session_scope() as s:
            self.assertEqual((await s.get(GuildConfig, GUILD)).blocked_mentions, ["here"])


class ReplyTests(_DB):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.schema()

    async def reply(self, text: str) -> tuple[str, dict]:
        chan = _Chan(guild=NS(id=GUILD))
        await replies.send_reply(chan, text)
        return chan.sent[0]

    async def test_a_new_server_blocks_crowd_pings(self) -> None:
        await self.add_guild()
        content, kw = await self.reply("@everyone free nitro <@&42>")
        self.assertEqual(_pings(content, kw["allowed_mentions"]), set())

    async def test_a_server_with_no_settings_yet_blocks_them_too(self) -> None:
        content, kw = await self.reply("@here look")
        self.assertEqual(_pings(content, kw["allowed_mentions"]), set())

    async def test_an_admin_can_allow_them(self) -> None:
        await self.add_guild(blocked_mentions=["roles"])
        content, kw = await self.reply("@everyone raid now <@&42>")
        self.assertEqual(_pings(content, kw["allowed_mentions"]), {"everyone"})

    async def test_a_channel_reminder_follows_the_server(self) -> None:
        from datetime import datetime, timezone

        from bot.cogs import reminders
        from olisar.db.models import Reminder

        chan = _Chan(guild=NS(id=GUILD))
        cog = reminders.Reminders.__new__(reminders.Reminders)
        cog.bot = NS(get_channel=lambda cid: chan)
        await cog._deliver(Reminder(guild_id=GUILD, channel_id=5, user_id=7, target="channel",
                                    content="@everyone click this", scheduled_at=datetime.now(timezone.utc)))
        content, kw = chan.sent[0]
        self.assertEqual(_pings(content, kw["allowed_mentions"]), set())
        self.assertIn("<@7>", content)  # the person who set it still gets pinged
        self.assertTrue(kw["allowed_mentions"].users)


if __name__ == "__main__":
    unittest.main()
