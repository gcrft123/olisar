"""In a DM, only membership in the home server counts.

Run:  uv run python -m unittest tests.test_dm_home_membership -v

A DM borrows the home server's persona, knowledge and tools, and anyone who shares any
server with the bot can DM it. Two things followed from that. The role gate resolved a DM
sender to their membership in whichever shared server it found them in, so Manage Server
in some other server counted as admin against the home server's allow list. And the home
server's own data went to anyone who could DM: its knowledge base and glossary (in recall
and through the tools), writes to its glossary, and its extensions.

Now a DM sender is looked up in the home server only. Someone who isn't a member there can
still talk to the bot, but gets none of that server's data: the server tools are neither
declared nor run, recall leaves out the glossary and knowledge base, and no extension is
offered.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.access import member_allowed, resolve_member
from bot.actions import BotActions
from olisar import pipeline
from olisar.db.models import Base, GuildFact
from olisar.guild_setup import ensure_guild_defaults
from olisar.memory import retriever
from olisar.tools import GUILD_TOOLS, ToolContext, execute_tool

HOME = 1001
OTHER = 2002
DM_CHANNEL = 4004
USER = 5005
ALLOWED_ROLE = 7007


def _not_found(*_a, **_k):
    raise discord.NotFound(NS(status=404, reason="nf"), "unknown member")


def _bot(*, member_of_home: bool):
    member = NS(id=USER, roles=[], guild_permissions=NS(manage_guild=False))
    home = NS(
        id=HOME,
        get_member=lambda uid: member if member_of_home and uid == USER else None,
        fetch_member=AsyncMock(side_effect=_not_found),
        default_role=NS(id=HOME),
    )
    return NS(get_guild=lambda gid: home if gid == HOME else None, guilds=[home])


class TheRoleGate(unittest.TestCase):
    def test_manage_server_elsewhere_doesnt_pass_the_home_allow_list(self):
        admin_elsewhere = NS(id=USER, roles=[], guild_permissions=NS(manage_guild=True))
        home = NS(id=HOME, get_member=lambda uid: None)
        other = NS(id=OTHER, get_member=lambda uid: admin_elsewhere)
        bot = NS(guilds=[home, other], get_guild={HOME: home, OTHER: other}.get)
        with patch("bot.access.settings.target_guild_id", HOME):
            who = resolve_member(bot, NS(id=USER))
        self.assertIsNone(who)
        self.assertFalse(member_allowed(who, allowed=[str(ALLOWED_ROLE)], blocked=[], user_id=USER))

    def test_a_home_member_is_found_there(self):
        role = NS(id=ALLOWED_ROLE, is_default=lambda: False)
        member = NS(id=USER, roles=[role], guild_permissions=NS(manage_guild=False))
        home = NS(id=HOME, get_member=lambda uid: member)
        bot = NS(guilds=[home], get_guild={HOME: home}.get)
        with patch("bot.access.settings.target_guild_id", HOME):
            who = resolve_member(bot, NS(id=USER))
        self.assertIs(who, member)
        self.assertTrue(member_allowed(who, allowed=[str(ALLOWED_ROLE)], blocked=[], user_id=USER))


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s:
            await ensure_guild_defaults(s, HOME, name="Home")
            await s.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    def ctx(self, session, *, member_of_home: bool, is_dm: bool = True) -> ToolContext:
        return ToolContext(
            session=session, cfg_guild=HOME, channel_id=DM_CHANNEL, user_id=USER,
            display_name="someone", is_dm=is_dm,
            actions=BotActions(_bot(member_of_home=member_of_home)),
        )

    async def glossary_rows(self) -> int:
        async with self.Session() as s:
            return await s.scalar(select(func.count()).select_from(GuildFact))


class TheServerTools(_Db):
    async def test_a_non_member_cant_write_the_glossary(self):
        async with self.Session() as session:
            out = await execute_tool(
                "remember_server_fact", {"fact": "the staff rules are public"},
                self.ctx(session, member_of_home=False),
            )
            await session.commit()
        self.assertIn("isn't one", out)
        self.assertEqual(await self.glossary_rows(), 0)

    async def test_a_non_member_cant_search_the_knowledge_base(self):
        search = AsyncMock(return_value="INTERNAL RUNBOOK")
        async with self.Session() as session:
            with patch("olisar.tools.search_knowledge", search):
                out = await execute_tool(
                    "query_knowledge", {"query": "runbook"}, self.ctx(session, member_of_home=False)
                )
        search.assert_not_awaited()
        self.assertNotIn("INTERNAL RUNBOOK", out)

    async def test_nor_see_who_is_around(self):
        async with self.Session() as session:
            ctx = self.ctx(session, member_of_home=False)
            ctx.actions.user_status = AsyncMock(return_value="online")
            ctx.actions.who_in_voice = AsyncMock(return_value="Alice")
            await execute_tool("get_user_status", {"user": "alice"}, ctx)
            await execute_tool("who_is_in_voice", {}, ctx)
        ctx.actions.user_status.assert_not_awaited()
        ctx.actions.who_in_voice.assert_not_awaited()

    async def test_the_membership_is_asked_once_per_reply(self):
        async with self.Session() as session:
            ctx = self.ctx(session, member_of_home=False)
            with patch("olisar.tools.search_knowledge", AsyncMock(return_value="")):
                await execute_tool("query_knowledge", {"query": "a"}, ctx)
                await execute_tool("query_knowledge", {"query": "b"}, ctx)
        self.assertEqual(ctx.actions.bot.get_guild(HOME).fetch_member.await_count, 1)

    async def test_a_home_member_keeps_them_in_a_dm(self):
        async with self.Session() as session:
            out = await execute_tool(
                "remember_server_fact", {"subject": "ICA", "fact": "ICA is Ironclad Assault"},
                self.ctx(session, member_of_home=True),
            )
            await session.commit()
        self.assertTrue(out.startswith("Added to the server glossary"), out)
        self.assertEqual(await self.glossary_rows(), 1)

    async def test_a_server_channel_doesnt_ask(self):
        async with self.Session() as session:
            ctx = self.ctx(session, member_of_home=False, is_dm=False)
            await execute_tool("remember_server_fact", {"fact": "ICA is Ironclad Assault"}, ctx)
            await session.commit()
        self.assertEqual(await self.glossary_rows(), 1)
        self.assertIsNone(ctx.in_guild)


class TheReply(unittest.IsolatedAsyncioTestCase):
    """What generate_reply hands the model in a DM."""

    async def reply(self, *, member: bool) -> dict:
        seen: dict = {}

        async def fake_loop(contents, system, model, ctx, **kw):
            seen["tools"] = {d.name for t in kw["tools"] for d in t.function_declarations or []}
            seen["ctx"], seen["system"] = ctx, system
            return "ok"

        actions = MagicMock()
        actions.is_member = AsyncMock(return_value=member)
        actions.is_admin = AsyncMock(return_value=False)
        actions.channel_directory = AsyncMock(return_value="")
        session = MagicMock()
        session.get = AsyncMock(return_value=NS(
            silent_acks_enabled=True, presence_tools_enabled=True, default_model=None,
            command_messages={}, context_message_limit=None, name="Olisar",
        ))
        gather = AsyncMock(return_value=pipeline.GatheredExtensions())
        recall = AsyncMock(return_value="")
        with patch.object(pipeline, "_run_tool_loop", new=fake_loop), patch.object(
            pipeline, "build_contents", new=AsyncMock(return_value=([], set()))
        ), patch.object(pipeline, "people_directory", new=AsyncMock(return_value="")), patch.object(
            pipeline, "recall", new=recall
        ), patch.object(pipeline, "gather_enabled", new=gather), patch.object(
            pipeline, "_persona_prompt", return_value="persona"
        ):
            await pipeline.generate_reply(
                session, guild_id=0, home_guild_id=HOME, channel_id=DM_CHANNEL,
                current_message_id=2, bot_user_id=3, user_id=USER, display_name="someone",
                user_text="what's in the runbook?", actions=actions,
            )
        seen["gather"], seen["recall"] = gather, recall
        return seen

    async def test_a_non_member_gets_none_of_the_servers_data(self):
        seen = await self.reply(member=False)
        self.assertFalse(GUILD_TOOLS & seen["tools"])
        self.assertIn("web_search", seen["tools"])
        self.assertFalse(seen["ctx"].in_guild)
        self.assertNotIn("query_knowledge", seen["system"])
        seen["gather"].assert_not_awaited()
        self.assertFalse(seen["recall"].await_args.kwargs["member"])

    async def test_a_member_gets_all_of_it(self):
        seen = await self.reply(member=True)
        self.assertLessEqual(GUILD_TOOLS, seen["tools"])
        self.assertTrue(seen["ctx"].in_guild)
        seen["gather"].assert_awaited_once()
        self.assertTrue(seen["recall"].await_args.kwargs["member"])


class Recall(_Db):
    async def test_leaves_out_the_glossary_and_knowledge_base_for_a_non_member(self):
        async with self.Session() as s:
            s.add(GuildFact(guild_id=HOME, subject="Op", fact="Op Nightfall is on Friday"))
            await s.commit()

        async def readable(ids):
            return set()

        kb = AsyncMock(return_value="Knowledge base: INTERNAL RUNBOOK")
        with patch.object(retriever, "embed_query", AsyncMock(return_value=[0.1])), patch.object(
            retriever, "knn", AsyncMock(return_value=[])
        ), patch.object(retriever, "kb_block_from_qvec", kb):
            async with self.Session() as s:
                outsider = await retriever.recall(
                    s, cfg_guild=HOME, user_id=USER, query_text="runbook", recent_ids=set(),
                    channel_id=DM_CHANNEL, readable=readable, member=False,
                )
                member = await retriever.recall(
                    s, cfg_guild=HOME, user_id=USER, query_text="runbook", recent_ids=set(),
                    channel_id=DM_CHANNEL, readable=readable,
                )
        self.assertNotIn("Nightfall", outsider)
        self.assertNotIn("RUNBOOK", outsider)
        self.assertIn("Nightfall", member)
        self.assertIn("RUNBOOK", member)


if __name__ == "__main__":
    unittest.main()
