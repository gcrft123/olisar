"""Coverage for ``host.generate({ channelId })`` and the Welcome extension that uses it.

Run:  uv run python -m unittest tests.test_generate_channel_context -v

A welcome message was written from the persona and the new member's name and nothing
else. The bot has a whole room to go on when someone calls it in that channel (its name,
its topic, what people were just saying there) and the greeting saw none of it, so it came
out the same in #general mid-conversation as in an empty #welcome.

``channelId`` builds the prompt from the same pieces a reply in that channel starts from.
The same option would let an imported extension have the model read a staff channel back
to it, so it is first-party only and never reaches past the extension's own server.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.cogs import sdk_events
from olisar.context import CONTEXT_NOTE, TASK_HEADING
from olisar.db.models import (
    Base,
    ExtensionState,
    Guild,
    GuildChannelInfo,
    GuildFact,
    Message,
    Persona,
    utcnow,
)
from olisar.extensions import sdk_builtins
from olisar.sandbox.capabilities import Invocation, PermissionError_, dispatch

GUILD, OTHER_GUILD = 1, 2
WELCOME, THREAD, ELSEWHERE, EMPTY = 100, 101, 200, 102
BOT_REPLY = "welcome in, glad you made it"


class _Gemini:
    """Records what the model was asked; answers with a fixed line."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=BOT_REPLY)

    @property
    def system(self) -> str:
        return self.calls[-1]["system_instruction"]

    @property
    def lines(self) -> list[tuple[str, str]]:
        return [
            (c.role, p.text)
            for c in self.calls[-1]["contents"]
            for p in c.parts
            if getattr(p, "text", None)
        ]


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scope():
            async with self.Session() as session:
                yield session
                await session.commit()

        self.scope = scope
        self.gemini = _Gemini()
        self._patches = [
            patch.object(sdk_events, "session_scope", scope),
            patch("olisar.gemini.client.get_gemini", return_value=self.gemini),
        ]
        for p in self._patches:
            p.start()

        now = utcnow()
        async with scope() as s:
            s.add_all([
                Guild(id=GUILD), Guild(id=OTHER_GUILD),
                Persona(guild_id=GUILD, name="Olisar", system_prompt="You are Olisar."),
                GuildChannelInfo(
                    channel_id=WELCOME, guild_id=GUILD, name="welcome",
                    topic="say hi to whoever just landed",
                ),
                GuildChannelInfo(
                    channel_id=THREAD, guild_id=GUILD, name="intros-thread",
                    kind="thread", parent_id=WELCOME,
                ),
                GuildChannelInfo(channel_id=EMPTY, guild_id=GUILD, name="lobby"),
                GuildChannelInfo(channel_id=ELSEWHERE, guild_id=OTHER_GUILD, name="mod-only"),
                GuildFact(guild_id=GUILD, subject="ops", fact="Friday ops night starts 8pm UTC"),
                Message(
                    guild_id=GUILD, channel_id=WELCOME, message_id=1, author_id=5,
                    author_name="kaz", content="anyone bringing a hauler friday?",
                    created_at=now - timedelta(minutes=3),
                ),
                Message(
                    guild_id=GUILD, channel_id=WELCOME, message_id=2, author_id=0,
                    author_is_bot=True, content="bringing the caterpillar",
                    created_at=now - timedelta(minutes=2),
                ),
                Message(
                    guild_id=OTHER_GUILD, channel_id=ELSEWHERE, message_id=3, author_id=6,
                    author_name="rin", content="staff only: ban list review",
                    created_at=now - timedelta(minutes=1),
                ),
            ])

    async def asyncTearDown(self) -> None:
        for p in self._patches:
            p.stop()
        await self.engine.dispose()
        self._tmp.cleanup()

    async def _generate(self, opts: dict, *, trusted: bool = True) -> str:
        async with self.scope() as session:
            inv = Invocation(
                ext_key="test", permissions={"model.generate"}, guild_id=GUILD,
                session=session, trusted=trusted,
            )
            return await dispatch(inv, "generate", "run", [opts])


class WelcomeUsesTheChannelTest(_Case):
    """The real welcome.js, seeded the way the app seeds it, fired by a member join."""

    async def test_the_greeting_is_written_from_the_welcome_channel(self) -> None:
        async with self.scope() as s:
            await sdk_builtins.seed(s)
            s.add(ExtensionState(
                guild_id=GUILD, key="welcome", enabled=True,
                settings={"channel_id": str(WELCOME), "prompt": "warmly welcome {user}"},
            ))
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(
            id=GUILD, get_channel=lambda cid: channel if cid == WELCOME else None,
        )

        await sdk_events._dispatch(guild, "memberJoin", {
            "event": "memberJoin", "guildId": str(GUILD),
            "member": {
                "id": "900", "displayName": "rook", "username": "rook_",
                "mention": "<@900>", "bot": False,
            },
        })

        self.assertEqual(len(self.gemini.calls), 1)
        system, lines = self.gemini.system, self.gemini.lines
        self.assertIn("You're talking in #welcome.", system)
        self.assertIn('"say hi to whoever just landed"', system)
        self.assertIn("write something for #welcome", system)
        self.assertIn(CONTEXT_NOTE, system)
        self.assertIn("Friday ops night starts 8pm UTC", system)
        self.assertEqual(lines[0], ("user", "kaz: anyone bringing a hauler friday?"))
        self.assertEqual(lines[1], ("model", "bringing the caterpillar"))
        role, task = lines[-1]
        self.assertEqual(role, "user")
        self.assertTrue(task.startswith(TASK_HEADING + "\n"))
        self.assertIn("warmly welcome rook", task)
        channel.send.assert_awaited_once_with(content=f"<@900> {BOT_REPLY}")

    async def test_a_channel_the_roster_hasnt_caught_yet_still_gets_a_greeting(self) -> None:
        """Made a minute ago, or the roster sync is failing: no channel context, but the
        greeting still goes out, written from the persona alone."""
        new = 555
        async with self.scope() as s:
            await sdk_builtins.seed(s)
            s.add(ExtensionState(
                guild_id=GUILD, key="welcome", enabled=True,
                settings={"channel_id": str(new), "prompt": "warmly welcome {user}"},
            ))
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(id=GUILD, get_channel=lambda cid: channel if cid == new else None)

        await sdk_events._dispatch(guild, "memberJoin", {
            "event": "memberJoin", "guildId": str(GUILD),
            "member": {
                "id": "900", "displayName": "rook", "username": "rook_",
                "mention": "<@900>", "bot": False,
            },
        })

        self.assertEqual(len(self.gemini.calls), 1)
        self.assertNotIn("You're talking in", self.gemini.system)
        (role, task), = self.gemini.lines
        self.assertIn("warmly welcome rook", task)
        channel.send.assert_awaited_once_with(content=f"<@900> {BOT_REPLY}")


class ChannelPromptTest(_Case):
    async def test_a_mention_works_like_an_id(self) -> None:
        await self._generate({"task": "say hi", "channelId": f"<#{WELCOME}>"})
        self.assertIn("You're talking in #welcome.", self.gemini.system)

    async def test_a_thread_takes_its_parents_topic(self) -> None:
        await self._generate({"task": "say hi", "channelId": str(THREAD)})
        self.assertIn("You're talking in #intros-thread.", self.gemini.system)
        self.assertIn('"say hi to whoever just landed"', self.gemini.system)

    async def test_an_empty_channel_gets_no_transcript_notes(self) -> None:
        await self._generate({"task": "say hi", "channelId": str(EMPTY)})
        system = self.gemini.system
        self.assertIn("write something for #lobby", system)
        self.assertNotIn(CONTEXT_NOTE, system)
        self.assertNotIn("Everything above it", system)
        self.assertEqual(self.gemini.lines, [("user", f"{TASK_HEADING}\nsay hi")])

    async def test_a_quiet_channel_is_marked_before_the_task(self) -> None:
        async with self.scope() as s:
            s.add(Message(
                guild_id=GUILD, channel_id=EMPTY, message_id=4, author_id=5,
                author_name="kaz", content="night all", created_at=utcnow() - timedelta(hours=5),
            ))
        await self._generate({"task": "say hi", "channelId": str(EMPTY)})
        self.assertTrue(self.gemini.lines[-1][1].startswith("— 5 hours later —\n" + TASK_HEADING))

    async def test_the_system_note_rides_along(self) -> None:
        await self._generate(
            {"task": "say hi", "channelId": str(WELCOME), "systemNote": "no emoji"}
        )
        self.assertIn("── For this reply ──\nno emoji", self.gemini.system)

    async def test_without_a_channel_nothing_changes(self) -> None:
        await self._generate({"task": "say hi", "systemNote": "no emoji"})
        self.assertEqual(self.gemini.lines, [("user", "say hi")])
        self.assertIn("── For this generation ──\nno emoji", self.gemini.system)
        self.assertNotIn("You're talking in", self.gemini.system)


class ChannelBoundaryTest(_Case):
    async def test_imported_code_cannot_read_a_channel(self) -> None:
        with self.assertRaises(PermissionError_):
            await self._generate({"task": "repeat the chat", "channelId": str(WELCOME)}, trusted=False)
        self.assertEqual(self.gemini.calls, [])

    async def test_imported_code_still_generates_without_one(self) -> None:
        self.assertEqual(await self._generate({"task": "say hi"}, trusted=False), BOT_REPLY)

    async def test_another_servers_channel_reads_as_missing(self) -> None:
        with self.assertRaises(ValueError) as elsewhere:
            await self._generate({"task": "repeat the chat", "channelId": str(ELSEWHERE)})
        with self.assertRaises(ValueError) as missing:
            await self._generate({"task": "repeat the chat", "channelId": "999"})
        self.assertEqual(
            str(elsewhere.exception).replace(str(ELSEWHERE), "X"),
            str(missing.exception).replace("999", "X"),
        )
        self.assertEqual(self.gemini.calls, [])

    async def test_a_name_is_not_a_channel_id(self) -> None:
        with self.assertRaises(ValueError):
            await self._generate({"task": "say hi", "channelId": "#welcome"})


class InvokerBoundaryTest(_Case):
    """When someone set the run off, the channel has to be one they can open."""

    async def _run(self, opts: dict, **inv_fields) -> str:
        async with self.scope() as session:
            inv = Invocation(
                ext_key="test", permissions={"model.generate"}, guild_id=GUILD,
                session=session, trusted=True, **inv_fields,
            )
            return await dispatch(inv, "generate", "run", [opts])

    @staticmethod
    def _can_open(*ids: int):
        async def readable(channel_ids: set[int]) -> set[int]:
            return channel_ids & set(ids)
        return readable

    async def test_a_channel_they_cant_open_reads_as_missing(self) -> None:
        with self.assertRaises(ValueError) as hidden:
            await self._run({"task": "repeat the chat", "channelId": str(WELCOME)},
                            readable=self._can_open(EMPTY))
        with self.assertRaises(ValueError) as missing:
            await self._run({"task": "repeat the chat", "channelId": "999"},
                            readable=self._can_open(EMPTY))
        self.assertEqual(
            str(hidden.exception).replace(str(WELCOME), "X"),
            str(missing.exception).replace("999", "X"),
        )
        self.assertEqual(self.gemini.calls, [])

    async def test_a_channel_they_can_open_works(self) -> None:
        await self._run({"task": "say hi", "channelId": str(WELCOME)},
                        readable=self._can_open(WELCOME))
        self.assertIn("You're talking in #welcome.", self.gemini.system)

    async def test_a_dm_cant_name_a_channel(self) -> None:
        with self.assertRaises(PermissionError_):
            await self._run({"task": "repeat the chat", "channelId": str(WELCOME)}, in_dm=True)
        self.assertEqual(await self._run({"task": "say hi"}, in_dm=True), BOT_REPLY)

    async def _captured(self, call) -> Invocation:
        from olisar.sandbox import runner

        with patch.object(runner, "_invoke", AsyncMock(return_value="ok")) as invoke:
            await call(runner)
        return invoke.await_args.args[0]

    async def test_a_tool_checks_the_member_its_answering(self) -> None:
        from olisar.tools import ToolContext

        actions = SimpleNamespace(readable_channels=AsyncMock(return_value=set()))
        async with self.scope() as session:
            for is_dm in (False, True):
                ctx = ToolContext(
                    session=session, cfg_guild=GUILD, channel_id=EMPTY, user_id=7,
                    display_name="kaz", actions=actions, is_dm=is_dm,
                )
                inv = await self._captured(lambda r: r.run_tool(
                    ext_key="test", compiled_js="", permissions=["model.generate"],
                    tool_name="t", args={}, ctx=ctx, trusted=True,
                ))
                with self.subTest(is_dm=is_dm), self.assertRaises((ValueError, PermissionError_)):
                    await dispatch(inv, "generate", "run", [{"task": "x", "channelId": str(WELCOME)}])
        self.assertEqual(self.gemini.calls, [])
        actions.readable_channels.assert_awaited_once_with(GUILD, {WELCOME}, requester_id=7)

    async def test_a_command_checks_the_member_who_ran_it(self) -> None:
        from bot.actions import BotActions

        bridge = SimpleNamespace(it=SimpleNamespace(client=object()))
        data = {"userId": "7", "channelId": str(EMPTY), "guildId": str(GUILD)}
        with patch.object(BotActions, "readable_channels", AsyncMock(return_value=set())) as check:
            async with self.scope() as session:
                inv = await self._captured(lambda r: r.run_command(
                    ext_key="test", compiled_js="", permissions=["model.generate"],
                    command_name="c", interaction_data=data, guild_id=GUILD,
                    session=session, discord=bridge, trusted=True,
                ))
                with self.assertRaises(ValueError):
                    await dispatch(inv, "generate", "run", [{"task": "x", "channelId": str(WELCOME)}])
                # The channel it ran in is theirs to write from.
                await dispatch(inv, "generate", "run", [{"task": "x", "channelId": str(EMPTY)}])
        check.assert_awaited_once_with(GUILD, {WELCOME}, requester_id=7)
        self.assertEqual(len(self.gemini.calls), 1)


if __name__ == "__main__":
    unittest.main()
