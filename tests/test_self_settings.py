"""Olisar changing its own console settings from a conversation.

Run:  uv run python -m unittest tests.test_self_settings -v

Two things are pinned here. The first is the token budget the design exists for: only
``open_settings`` is declared on an ordinary reply, and the write tools arrive only after it
has run, in that reply, outside the console's test chat.

The second is that a change made by asking lands exactly where the console's would, with the
console's limits. A setting the model can write out of range isn't a setting the console
guards, so the numeric bounds are checked against ``api/schemas.py`` rather than restated.
Everything else is the write path against a real schema: what's refused leaves the row as it
was, and what's accepted is audited under the member who asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from annotated_types import Ge, Le
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.schemas import ConfigIn, PersonaIn, ProactivityIn
from olisar import self_settings
from olisar.db.models import (
    AuditLog,
    Base,
    GuildChannelInfo,
    GuildConfig,
    GuildFact,
    KBSource,
    KBStatus,
    Persona,
    ProactivityConfig,
    ProactivityLevel,
    UserProfile,
)
from olisar.guild_setup import ensure_guild_defaults
from olisar.pipeline import _run_tool_loop
from olisar.tools import TOOLS, ToolContext, sandbox_tools, tools_with_extensions, with_settings_tools

GUILD = 5001
OTHER_GUILD = 5002
USER = 4242
WRITES = {"change_setting", "settings_action"}


def _names(tools: list) -> set[str]:
    return {d.name for t in tools for d in (t.function_declarations or [])}


class OnlyTheReadIsPaidForUpFront(unittest.TestCase):
    def test_an_ordinary_reply_declares_the_read_and_nothing_else(self):
        ext = types.FunctionDeclaration(name="ext_tool", description="an extension's tool")
        for tools in (TOOLS, tools_with_extensions([ext])):
            self.assertIn("open_settings", _names(tools))
            self.assertFalse(WRITES & _names(tools))

    def test_the_test_chat_never_gets_any_of_them(self):
        self.assertFalse(self_settings.TOOL_NAMES & _names(sandbox_tools([])))

    def test_unlocking_adds_the_writes_once(self):
        once = with_settings_tools(TOOLS)
        self.assertTrue(WRITES <= _names(once))
        self.assertIs(with_settings_tools(once), once)

    def test_every_declared_action_has_a_handler(self):
        self.assertEqual(set(self_settings.ACTIONS), set(self_settings._ACTIONS))


def _call(name: str, **args):
    call = MagicMock()
    call.name = name
    call.args = args
    return call


def _resp(*calls, text=None):
    resp = MagicMock()
    parts = []
    for c in calls:
        p = MagicMock()
        p.function_call = c
        p.text = None
        parts.append(p)
    resp.candidates[0].content.parts = parts
    resp.text = text
    return resp


class TheReadUnlocksTheWrites(unittest.TestCase):
    """The unlock happens inside the tool loop, between one model call and the next."""

    def _tools_per_call(self, first_call: str) -> list[set[str]]:
        client = MagicMock()
        client.generate_with_tools = AsyncMock(
            side_effect=[_resp(_call(first_call, section="behavior")), _resp(text="done")]
        )
        ctx = ToolContext(session=None, cfg_guild=GUILD, channel_id=1, user_id=USER,
                          display_name="ada")

        async def fake_execute(name, args, c):
            if name == "open_settings":
                c.settings_open = True
            return "ok"

        with patch("olisar.pipeline.get_gemini", return_value=client), patch(
            "olisar.pipeline.execute_tool", new=AsyncMock(side_effect=fake_execute)
        ), patch("olisar.pipeline._complete_truncated", new=AsyncMock(return_value="done")):
            asyncio.run(_run_tool_loop([], "sys", None, ctx, tools=TOOLS))
        return [_names(c.kwargs["tools"]) for c in client.generate_with_tools.call_args_list]

    def test_the_call_after_a_read_can_change_settings(self):
        first, second = self._tools_per_call("open_settings")
        self.assertFalse(WRITES & first)
        self.assertTrue(WRITES <= second)

    def test_any_other_tool_leaves_them_out(self):
        for tools in self._tools_per_call("recall_memory"):
            self.assertFalse(WRITES & tools)


class TheBoundsAreTheConsoles(unittest.TestCase):
    """Each key's range is the one the console's API validates, and each key is one the
    console's API accepts at all."""

    SCHEMAS = {Persona: PersonaIn, GuildConfig: ConfigIn, ProactivityConfig: ProactivityIn}

    def test_every_key_is_a_field_the_api_takes(self):
        for key, f in {**self_settings._PERSONA, **self_settings._BEHAVIOR}.items():
            with self.subTest(key=key):
                self.assertIn(f.attr, self.SCHEMAS[f.table].model_fields)
                self.assertTrue(hasattr(f.table, f.attr))

    def test_numeric_ranges_match(self):
        for key, f in {**self_settings._PERSONA, **self_settings._BEHAVIOR}.items():
            if f.kind not in ("int", "float"):
                continue
            meta = self.SCHEMAS[f.table].model_fields[f.attr].metadata
            lo = next((m.ge for m in meta if isinstance(m, Ge)), None)
            hi = next((m.le for m in meta if isinstance(m, Le)), None)
            with self.subTest(key=key):
                self.assertEqual((f.lo, f.hi), (lo, hi))


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home")
            await ensure_guild_defaults(s, OTHER_GUILD, name="Elsewhere")
        self.session = self.Session()
        self.ctx = ToolContext(session=self.session, cfg_guild=GUILD, channel_id=1,
                               user_id=USER, display_name="ada")

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def run_tool(self, name: str, **args) -> str:
        return await self_settings.run(name, args, self.ctx)

    async def change(self, key: str, value: str, **extra) -> str:
        return await self.run_tool("change_setting", key=key, value=value, **extra)

    async def action(self, action: str, **args) -> str:
        return await self.run_tool("settings_action", action=action, **args)

    async def row(self, table, pk=GUILD):
        async with self.Session() as s:
            return await s.get(table, pk)


class Reading(_Db):
    async def test_a_listing_shows_each_key_with_its_range(self):
        out = await self.run_tool("open_settings", section="behavior")
        self.assertIn("context_message_limit=12 [3-100]", out)
        self.assertIn("proactivity.level=", out)
        self.assertIn("[off|low|med|high]", out)
        self.assertTrue(self.ctx.settings_open)

    async def test_no_section_still_names_every_key(self):
        # The model does call it with no arguments, and a bare "which section?" costs a
        # whole extra model call.
        for args in ({}, {"section": "everything"}):
            out = await self.run_tool("open_settings", **args)
            self.assertIn("grounding_enabled", out)
            self.assertIn("rebuild_impression", out)
        self.assertTrue(self.ctx.settings_open)

    async def test_long_text_is_cut_short_and_one_key_comes_back_whole(self):
        prompt = "You are Olisar. " * 40
        await self.change("system_prompt", prompt)
        listing = await self.run_tool("open_settings", section="persona")
        self.assertNotIn(prompt.strip(), listing)
        self.assertIn(f"({len(prompt.strip())} chars)", listing)
        full = await self.run_tool("open_settings", section="persona", filter="system_prompt")
        self.assertIn(prompt.strip(), full)

    async def test_a_reply_read_by_name_shows_its_placeholders(self):
        out = await self.run_tool("open_settings", section="replies", filter="reply.ping")
        self.assertIn("(default)", out)
        self.assertIn("{latency}", out)

    async def test_the_glossary_filters_by_words(self):
        async with self.scope() as s:
            s.add(GuildFact(guild_id=GUILD, subject="MN", fact="MN is Movie Night on Friday"))
            s.add(GuildFact(guild_id=GUILD, subject="GG", fact="GG is the guild games night"))
        out = await self.run_tool("open_settings", section="glossary", filter="movie")
        self.assertIn("MN: MN is Movie Night", out)
        self.assertNotIn("GG", out)


class ChangingAKey(_Db):
    async def test_an_accepted_change_is_stored_and_audited_under_the_member(self):
        out = await self.change("context_message_limit", "20")
        self.assertEqual(out, "context_message_limit: 12 → 20.")
        self.assertEqual((await self.row(GuildConfig)).context_message_limit, 20)
        async with self.Session() as s:
            entry = (await s.scalars(select(AuditLog))).one()
        self.assertEqual(entry.actor, str(USER))
        self.assertEqual(entry.action, "update_config")
        self.assertEqual(entry.before, {"context_message_limit": 12})
        self.assertEqual(entry.after, {"context_message_limit": 20, "via": "chat"})

    async def test_a_refused_change_leaves_the_row_alone(self):
        for key, value in [
            ("context_message_limit", "0"),
            ("context_message_limit", "12.5"),
            ("proactivity.confidence_threshold", "nan"),
            ("default_model", "gpt-4"),
            ("blocked_mentions", "everyone, admins"),
            ("reply_in_dms", "maybe"),
            ("desired_bio", "x" * 301),
            ("name", "   "),
        ]:
            with self.subTest(key=key, value=value):
                out = await self.change(key, value)
                self.assertIn("not changed", out)
        config = await self.row(GuildConfig)
        self.assertEqual(config.context_message_limit, 12)
        self.assertEqual(config.version, 1)
        async with self.Session() as s:
            self.assertEqual((await s.scalars(select(AuditLog))).all(), [])

    async def test_values_are_read_the_way_people_write_them(self):
        await self.change("reply_in_dms", "off")
        await self.change("blocked_mentions", "@everyone, here")
        await self.change("proactivity.level", "HIGH")
        await self.change("proactivity.quiet_hours", "22-6")
        await self.change("server_type", "gaming")
        config, pro = await self.row(GuildConfig), await self.row(ProactivityConfig)
        self.assertFalse(config.reply_in_dms)
        self.assertEqual(config.blocked_mentions, ["everyone", "here"])
        self.assertIs(pro.level, ProactivityLevel.high)
        self.assertEqual(pro.quiet_hours, {"start": 22, "end": 6})
        await self.change("proactivity.quiet_hours", "off")
        await self.change("server_type", "none")
        self.assertEqual((await self.row(ProactivityConfig)).quiet_hours, {})
        self.assertEqual((await self.row(Persona)).server_type, "")

    async def test_an_unchanged_value_writes_nothing(self):
        out = await self.change("context_message_limit", "12")
        self.assertIn("already", out)
        self.assertEqual((await self.row(GuildConfig)).version, 1)

    async def test_unknown_keys_and_misplaced_edits_are_refused(self):
        self.assertIn("No setting", await self.change("temperature", "2"))
        self.assertIn("only work on text", await self.change("reply_in_dms", "x", find="y"))


class EditingText(_Db):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.change("system_prompt", "You are Olisar.\nYou like trains. Be kind.")

    async def prompt(self) -> str:
        return (await self.row(Persona)).system_prompt

    async def test_find_swaps_one_passage_across_a_line_break(self):
        await self.change("system_prompt", "Olisar. You love boats.", find="Olisar. You like trains.")
        self.assertEqual(await self.prompt(), "You are Olisar. You love boats. Be kind.")

    async def test_a_passage_that_isnt_there_or_is_there_twice_is_refused(self):
        self.assertIn("isn't in", await self.change("system_prompt", "x", find="planes"))
        self.assertIn("2 times", await self.change("system_prompt", "x", find="You"))
        self.assertEqual(await self.prompt(), "You are Olisar.\nYou like trains. Be kind.")

    async def test_append_adds_a_line(self):
        await self.change("system_prompt", "Never spoil films.", append="true")
        self.assertEqual(await self.prompt(), "You are Olisar.\nYou like trains. Be kind.\nNever spoil films.")


class CommandReplies(_Db):
    async def custom(self) -> dict:
        return (await self.row(GuildConfig)).command_messages or {}

    async def test_set_then_reset(self):
        self.assertEqual(await self.change("reply.ping", "pong! {latency}ms"), "reply.ping changed.")
        self.assertEqual((await self.custom())["ping"], "pong! {latency}ms")
        self.assertIn("reset", await self.change("reply.ping", ""))
        self.assertNotIn("ping", await self.custom())

    async def test_resetting_a_default_writes_nothing(self):
        self.assertIn("already", await self.change("reply.ping", ""))
        self.assertEqual((await self.row(GuildConfig)).version, 1)

    async def test_an_edit_to_the_default_text_stores_the_result(self):
        await self.change("reply.ping", "pang", find="pong")
        self.assertEqual((await self.custom())["ping"], "pang — {latency} ms")

    async def test_unknown_reply(self):
        self.assertIn("No reply", await self.change("reply.hello", "hi"))


class KnowledgeActions(_Db):
    async def source(self, sid: int) -> KBSource | None:
        return await self.row(KBSource, sid)

    async def test_adding_a_site_queues_it_with_its_options(self):
        out = await self.action("kb_add_site", target="https://wiki.example", depth="2",
                                pages="40", hours="24")
        self.assertIn("Queued source #1", out)
        src = await self.source(1)
        self.assertEqual((src.type.value, src.crawl_depth, src.max_pages), ("website", 2, 40))
        self.assertEqual(src.refresh_interval_hours, 24)
        self.assertIsNotNone(src.next_refresh_at)

    async def test_bad_input_adds_nothing(self):
        self.assertIn("http", await self.action("kb_add_page", target="wiki.example"))
        self.assertIn("depth", await self.action("kb_add_site", target="https://a.b", depth="9"))
        self.assertIsNone(await self.source(1))

    async def test_schedule_refresh_and_remove(self):
        await self.action("kb_add_page", target="https://a.example")
        self.assertIn("already being read", await self.action("kb_refresh", target="1"))
        async with self.scope() as s:
            (await s.get(KBSource, 1)).status = KBStatus.ready
        self.assertIn("Re-reading", await self.action("kb_refresh", target="#1"))
        self.assertIs((await self.source(1)).status, KBStatus.pending)
        self.assertIn("every 6h", await self.action("kb_schedule", target="1", hours="6"))
        self.assertEqual((await self.source(1)).refresh_interval_hours, 6)
        self.assertIn("Removed source #1", await self.action("kb_remove", target="1"))
        self.assertIsNone(await self.source(1))

    async def test_another_servers_source_is_out_of_reach(self):
        async with self.scope() as s:
            s.add(KBSource(guild_id=OTHER_GUILD, type="url", uri="https://x.example"))
        self.assertIn("No source #1", await self.action("kb_remove", target="1"))
        self.assertIsNotNone(await self.source(1))

    async def test_rebuilding_the_index_rearms_only_indexed_channels(self):
        async with self.scope() as s:
            s.add(GuildChannelInfo(channel_id=1, guild_id=GUILD, backfill_done=True))
            s.add(GuildChannelInfo(channel_id=2, guild_id=GUILD, backfill_done=True,
                                   index_enabled=False))
        self.assertIn("Re-indexing 1 channels", await self.action("index_rebuild"))
        self.assertTrue((await self.row(GuildChannelInfo, 2)).backfill_done)

    async def test_glossary_delete_takes_several_ids_and_only_this_servers(self):
        async with self.scope() as s:
            s.add(GuildFact(guild_id=GUILD, subject="A", fact="a"))
            s.add(GuildFact(guild_id=GUILD, subject="B", fact="b"))
            s.add(GuildFact(guild_id=OTHER_GUILD, subject="C", fact="c"))
        out = await self.action("glossary_delete", target="#1, 2 and 3")
        self.assertEqual(out, "Deleted #1, #2. No fact #3 here.")
        self.assertIsNotNone(await self.row(GuildFact, 3))


class RebuildingAnImpression(_Db):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.scope() as s:
            s.add(UserProfile(user_id=1, guild_id=GUILD, display_name="Alex"))
            s.add(UserProfile(user_id=2, guild_id=GUILD, display_name="Alexa"))

    async def test_an_ambiguous_name_asks_for_the_id(self):
        out = await self.action("rebuild_impression", target="ale")
        self.assertIn("Alex (1)", out)
        self.assertIn("Alexa (2)", out)

    async def test_the_impression_itself_stays_out_of_the_reply(self):
        built = AsyncMock(return_value={"ok": True, "impression": "SECRET", "messages": 30})
        with patch("olisar.memory.personas.build_persona_now", new=built):
            out = await self.action("rebuild_impression", target="alex")
        self.assertEqual(out, "Rebuilt Alex's impression from 30 messages.")
        self.assertEqual(built.await_args.kwargs["user_id"], 1)

    async def test_nobody_by_that_name(self):
        self.assertIn("No member", await self.action("rebuild_impression", target="zed"))


if __name__ == "__main__":
    unittest.main()
