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
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.context import CONTEXT_NOTE, TASK_HEADING
from olisar.db.models import (
    Base,
    Guild,
    GuildChannelInfo,
    GuildFact,
    Message,
    Persona,
    utcnow,
)
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


if __name__ == "__main__":
    unittest.main()
