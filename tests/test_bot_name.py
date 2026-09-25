"""Coverage for calling the bot by the name its operator gave it.

Run:  uv run python -m unittest tests.test_bot_name -v

Operators bring their own Discord bot, so the bot members talk to is called whatever they
named it, and "Olisar" is only the default. A new server starts from that name (persona,
system prompt, name trigger), the slash commands describe the bot by it, and the prompts
that judge a conversation call the bot what its own transcript lines call it. Servers that
already exist keep what they have, so their prompts don't move under them.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.botinfo import bot_name
from bot.cogs.slash import Slash, name_commands
from olisar import discord_app, proactivity
from olisar.context import build_contents, persona_name
from olisar.db.models import Base, GuildConfig, Persona
from olisar.guild_setup import ensure_guild_defaults, name_trigger_for
from olisar.messages import DEFAULT_COMMAND_MESSAGES
from olisar.persona import DEFAULT_SYSTEM_PROMPT

GUILD = 7001
BOT_ID = 99


class NewServerDefaultsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    async def provision(self, **kw):
        async with self.Session() as s:
            await ensure_guild_defaults(s, GUILD, **kw)
            await s.commit()

    async def rows(self):
        async with self.Session() as s:
            return await s.get(Persona, GUILD), await s.get(GuildConfig, GUILD)

    async def test_a_new_server_starts_from_the_bots_name(self):
        await self.provision(name="Home", bot_name="Everest")
        persona, config = await self.rows()
        self.assertEqual(persona.name, "Everest")
        self.assertTrue(persona.system_prompt.startswith("You are Everest — "))
        self.assertNotIn("Olisar", persona.system_prompt)
        self.assertEqual(config.name_triggers, ["everest"])

    async def test_without_a_name_it_is_olisar_as_before(self):
        await self.provision(name="Home")
        persona, config = await self.rows()
        self.assertEqual(persona.name, "Olisar")
        self.assertEqual(persona.system_prompt, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(config.name_triggers, ["olisar"])

    async def test_an_existing_server_keeps_what_it_has(self):
        await self.provision(name="Home")
        await self.provision(name="Home", bot_name="Everest")
        persona, config = await self.rows()
        self.assertEqual(persona.name, "Olisar")
        self.assertEqual(persona.system_prompt, DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(config.name_triggers, ["olisar"])

    async def test_transcripts_use_the_persona_name(self):
        await self.provision(name="Home", bot_name="Everest")
        async with self.Session() as s:
            self.assertEqual(await persona_name(s, GUILD), "Everest")
            self.assertEqual(await persona_name(s, GUILD + 1), "Olisar")


class NameTriggerTest(unittest.TestCase):
    def test_the_trigger_is_the_name_as_typed(self):
        self.assertEqual(name_trigger_for("Everest"), ["everest"])
        self.assertEqual(name_trigger_for("Support Bot"), ["support bot"])

    def test_edge_punctuation_is_dropped_so_word_boundaries_can_match(self):
        self.assertEqual(name_trigger_for("Mr. Bot!"), ["mr. bot"])

    def test_a_name_with_nothing_to_type_gets_no_trigger(self):
        self.assertEqual(name_trigger_for("✨✨"), [])


class SlashDescriptionTest(unittest.TestCase):
    def _cog(self):
        return Slash(SimpleNamespace(user=None))

    def _descriptions(self, cog):
        return {c.qualified_name: c.description for c in cog.walk_app_commands()}

    def test_descriptions_name_the_bot_and_the_group_keeps_its_name(self):
        cog = self._cog()
        name_commands(cog, "Everest")
        described = self._descriptions(cog)
        self.assertEqual(described["ask"], "Ask Everest something.")
        self.assertEqual(described["olisar"], "Configure Everest in this server.")
        self.assertIn("olisar watch", described)
        self.assertFalse([d for d in described.values() if "Olisar" in d])

    def test_another_instance_still_starts_from_olisar(self):
        """The bot can restart in-process under a new name; renaming one cog's copies must
        leave the next cog something to rename."""
        name_commands(self._cog(), "Everest")
        fresh = self._descriptions(self._cog())
        self.assertEqual(fresh["ask"], "Ask Olisar something.")
        self.assertEqual(fresh["olisar watch"], "Have Olisar read & remember this channel.")

    def test_the_longest_name_fits_discords_limit(self):
        cog = self._cog()
        name_commands(cog, "x" * 32)  # Discord usernames top out at 32
        self.assertLessEqual(max(len(d) for d in self._descriptions(cog).values()), 100)


class ClassifierNameTest(unittest.TestCase):
    def _instruction(self, call, text):
        client = AsyncMock()
        client.generate.return_value = SimpleNamespace(text=text)
        with patch.object(proactivity, "get_gemini", return_value=client):
            asyncio.run(call())
        return client.generate.call_args.kwargs["system_instruction"]

    def test_the_chime_in_classifier_uses_the_name(self):
        verdict = '{"should_respond": false, "confidence": 0.1}'
        for follow_up in (False, True):
            instruction = self._instruction(
                lambda: proactivity.classify("kaz: hm", follow_up=follow_up, name="Everest"),
                verdict,
            )
            self.assertIn("'Everest'", instruction)
            self.assertNotIn("Olisar", instruction)

    def test_the_reaction_picker_uses_the_name(self):
        instruction = self._instruction(
            lambda: proactivity.pick_reaction_emoji("kaz: gg", name="Everest"), "none"
        )
        self.assertTrue(instruction.startswith("You are Everest, "))


def _row(mid, author, text, *, is_bot=False, reply_to=None, ago=0.0):
    return SimpleNamespace(
        message_id=mid, author_id=author, author_is_bot=is_bot, author_name="",
        content=text, reply_to_message_id=reply_to,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=ago),
    )


class _FakeSession:
    def __init__(self, rows, profiles):
        self.rows, self.profiles = rows, profiles

    async def scalars(self, stmt):
        if stmt.column_descriptions[0]["entity"].__tablename__ == "user_profile":
            people = [SimpleNamespace(user_id=u, display_name=n) for u, n in self.profiles.items()]
            return SimpleNamespace(all=lambda: people)
        return SimpleNamespace(all=lambda: list(reversed(self.rows)))


class ReplyToTheBotTest(unittest.TestCase):
    def test_a_reply_to_the_bot_names_it_by_its_persona(self):
        rows = [
            _row(10, BOT_ID, "try the long way round", is_bot=True, ago=60),
            _row(11, 5, "that worked", reply_to=10, ago=30),
        ]
        contents, _ = asyncio.run(build_contents(
            _FakeSession(rows, {5: "kaz"}), channel_id=1, current_message_id=0,
            bot_user_id=BOT_ID, current_display_name="kaz", current_text="thanks",
            own_name="Everest",
        ))
        texts = [p.text for c in contents for p in c.parts if getattr(p, "text", None)]
        self.assertIn('kaz (replying to Everest: "try the long way round"): that worked', texts)


class ConsoleNameTest(unittest.TestCase):
    def _request(self, user=None):
        bot = SimpleNamespace(user=user) if user is not None else None
        supervisor = SimpleNamespace(bot=bot)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot_supervisor=supervisor)))

    def tearDown(self):
        discord_app.invalidate()

    def test_the_running_bot_says_its_own_name(self):
        self.assertEqual(bot_name(self._request(SimpleNamespace(display_name="Everest"))), "Everest")

    def test_a_stopped_bot_falls_back_to_the_fetched_application(self):
        discord_app._cache = {"bot": {"username": "Everest"}}
        self.assertEqual(bot_name(self._request()), "Everest")

    def test_nothing_known_is_empty_and_never_asks_discord(self):
        discord_app.invalidate()
        with patch.object(discord_app, "_call", side_effect=AssertionError("no network")):
            self.assertEqual(bot_name(self._request()), "")


class CommandReplyTest(unittest.TestCase):
    def test_no_default_reply_calls_the_bot_olisar(self):
        """They're written in the bot's first person, so they never need its name."""
        self.assertFalse([k for k, v in DEFAULT_COMMAND_MESSAGES.items() if "Olisar" in v])


if __name__ == "__main__":
    unittest.main()
