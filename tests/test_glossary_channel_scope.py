"""A glossary fact reaches only people who can open the channel it was learned in.

Run:  uv run python -m unittest tests.test_glossary_channel_scope -v

The glossary is folded into every reply, and facts are mined from what members say. Summaries
and older messages went through the asker's channel filter, but the glossary didn't, so a
fact mined from a staff channel ("staff plan to ban the Red faction on Friday") reached every
member, and every DM. The console's "mine from memory" and "deep mine from index" also mined
several channels in one batch and kept no channel for what they found.

Now a fact mined from a channel keeps it, both mines work a channel at a time, and a reply
carries the fact only when its asker can open that channel. A fact with no channel (added in
the console or by an extension) is carried as before. Text written into a channel with no
asker (host.generate) keeps to facts from that channel or from none.
"""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.db.models import (
    Base,
    ChannelAllowlist,
    ChannelMode,
    Guild,
    GuildFact,
    Message,
    Persona,
    SearchMessage,
)
from olisar.memory import facts, maintenance, retriever
from olisar.pipeline import channel_task_prompt

GUILD = 1001
PUBLIC = 3003
STAFF = 3004
USER = 5005


async def _only_public(ids: set[int]) -> set[int]:
    return {PUBLIC} & set(ids)


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as s:
            s.add(Guild(id=GUILD, name="Home"))
            s.add(Persona(guild_id=GUILD, name="Olisar", system_prompt="You are Olisar."))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def add_facts(self, *rows: tuple[str, int | None, int]) -> None:
        async with self.scope() as s:
            for fact, channel, mentions in rows:
                s.add(GuildFact(guild_id=GUILD, subject=fact.split()[0], fact=fact,
                                source_channel_id=channel, mentions=mentions))


class Replies(_Db):
    async def test_a_fact_from_a_channel_the_asker_cant_open_is_left_out(self):
        await self.add_facts(
            ("Nightfall: staff plan to ban the Red faction on Friday", STAFF, 1),
            ("ICA is short for Ironclad Assault", PUBLIC, 1),
            ("Movie Night is the Friday watch-party", None, 1),
        )
        async with self.Session() as s:
            block = await facts.glossary_block(s, GUILD, readable=_only_public)
        self.assertNotIn("Red faction", block)
        self.assertIn("Ironclad Assault", block)
        self.assertIn("Movie Night", block)

    async def test_hidden_facts_dont_take_the_visible_ones_places(self):
        await self.add_facts(
            ("Alpha is a staff secret", STAFF, 90),
            ("Bravo is a staff secret", STAFF, 80),
            ("Charlie is the public fact", PUBLIC, 1),
        )
        async with self.Session() as s:
            block = await facts.glossary_block(s, GUILD, limit=1, readable=_only_public)
        self.assertIn("Charlie", block)

    async def test_recall_uses_the_askers_filter(self):
        await self.add_facts(("Nightfall: staff plan to ban the Red faction", STAFF, 1),
                             ("ICA is short for Ironclad Assault", PUBLIC, 1))
        with patch.object(retriever, "embed_query", AsyncMock(return_value=None)):
            async with self.Session() as s:
                block = await retriever.recall(
                    s, cfg_guild=GUILD, user_id=USER, query_text="", recent_ids=set(),
                    channel_id=PUBLIC, readable=_only_public,
                )
        self.assertNotIn("Red faction", block)
        self.assertIn("Ironclad Assault", block)

    async def test_writing_into_a_channel_keeps_to_that_channels_facts(self):
        await self.add_facts(("Nightfall: staff plan to ban the Red faction", STAFF, 1),
                             ("ICA is short for Ironclad Assault", PUBLIC, 1),
                             ("Movie Night is the Friday watch-party", None, 1))
        async with self.Session() as s:
            system, _ = await channel_task_prompt(
                s, guild_id=GUILD, channel_id=PUBLIC, channel_name="general",
                channel_topic="", task="greet the new member",
            )
        self.assertNotIn("Red faction", system)
        self.assertIn("Ironclad Assault", system)
        self.assertIn("Movie Night", system)


class _Miner:
    """A stand-in for the glossary model: one fact per call, named after the first
    codeword in the transcript, so each fact can be traced to the channel it came from."""

    def __init__(self) -> None:
        self.transcripts: list[str] = []

    async def generate(self, *, contents, **_kw):
        transcript = contents[0].split("CHAT TO MINE:\n", 1)[1]
        self.transcripts.append(transcript)
        word = re.search(r"code-(\w+)", transcript).group(1)
        return NS(text=json.dumps([{"subject": word, "fact": f"{word} is a codeword"}]))


class ConsoleMines(_Db):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.scope() as s:
            for cid in (PUBLIC, STAFF):
                s.add(ChannelAllowlist(guild_id=GUILD, channel_id=cid, mode=ChannelMode.both))
            # Interleaved in time, as a real sample is.
            for i in range(6):
                cid, word = (PUBLIC, "public") if i % 2 else (STAFF, "staff")
                s.add(Message(guild_id=GUILD, channel_id=cid, message_id=100 + i,
                              author_id=USER, content=f"code-{word} message {i}"))
                s.add(SearchMessage(guild_id=GUILD, channel_id=cid, channel_name=word,
                                    message_id=100 + i, author_id=USER, author_name="m",
                                    content=f"code-{word} message {i}"))
        self.miner = _Miner()

    async def mined(self) -> dict[str, int | None]:
        async with self.Session() as s:
            rows = (await s.scalars(select(GuildFact))).all()
        return {r.subject: r.source_channel_id for r in rows}

    def patched(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(maintenance, "session_scope", self.scope))
        stack.enter_context(patch.object(facts, "get_gemini", return_value=self.miner))
        return stack

    async def test_mine_from_memory_keeps_each_facts_channel(self):
        with self.patched():
            result = await maintenance.mine_glossary_now(GUILD)
        self.assertEqual(result["mined"], 6)
        self.assertEqual(await self.mined(), {"public": PUBLIC, "staff": STAFF})
        for transcript in self.miner.transcripts:
            self.assertFalse("code-public" in transcript and "code-staff" in transcript)

    async def test_deep_mine_keeps_each_facts_channel(self):
        with self.patched():
            result = await maintenance.deep_mine_glossary_now(GUILD)
        self.assertEqual(result["sampled"], 6)
        self.assertEqual(await self.mined(), {"public": PUBLIC, "staff": STAFF})
        self.assertEqual(len(self.miner.transcripts), 2)

    def test_batches_are_capped_within_a_channel(self):
        items = [(i, PUBLIC) for i in range(maintenance.GLOSSARY_MINE_BATCH + 1)]
        batches = maintenance._channel_batches(items, lambda it: it[1])
        self.assertEqual([len(b) for _, b in batches], [maintenance.GLOSSARY_MINE_BATCH, 1])


if __name__ == "__main__":
    unittest.main()
