"""Coverage for what a rendered transcript actually says.

Run:  uv run python -m unittest tests.test_transcript_shape -v

Three things a channel is that a flat list of lines isn't: several conversations at once
(so reply structure has to survive), a thing with holes in it (so silences have to be
visible), and a room with other bots in it (so "a bot said this" can't mean "I said
this"). Each was being thrown away somewhere between the database and the prompt.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from olisar.context import GAP_SECONDS, _elapsed, build_contents, speaker_name
from olisar.persona import DEFAULT_SLANG_DENSITY, SERVER_TYPES, room_notes
from olisar.proactivity import reaction_score

BOT_ID = 99


def _row(mid, author, text, *, is_bot=False, name="", reply_to=None, ago=0.0):
    return SimpleNamespace(
        message_id=mid,
        author_id=author,
        author_is_bot=is_bot,
        author_name=name,
        content=text,
        reply_to_message_id=reply_to,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=ago),
    )


class FakeSession:
    """Just enough AsyncSession for build_contents: it runs two kinds of query."""

    def __init__(self, rows, profiles=None, older=None):
        self.rows = rows
        self.profiles = profiles or {}
        self.older = older or []
        self.queries = 0

    async def scalars(self, stmt):
        self.queries += 1
        table = stmt.column_descriptions[0]["entity"].__tablename__
        if table == "user_profile":
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(user_id=uid, display_name=n)
                    for uid, n in self.profiles.items()
                ]
            )
        # The window read is ordered desc + limited; the reply-target read isn't.
        if stmt._limit_clause is not None:
            return SimpleNamespace(all=lambda: list(reversed(self.rows)))
        return SimpleNamespace(all=lambda: list(self.older))


def _texts(contents):
    return [(c.role, p.text) for c in contents for p in c.parts if getattr(p, "text", None)]


def _build(session, **kw):
    return asyncio.run(
        build_contents(
            session,
            channel_id=1,
            current_message_id=0,
            bot_user_id=BOT_ID,
            current_display_name="Wumpus",
            current_text="so what do you reckon",
            **kw,
        )
    )


class ReplyStructureTest(unittest.TestCase):
    def test_reply_marker_survives_into_the_transcript(self) -> None:
        rows = [
            _row(10, 5, "anyone up for ranked", ago=300),
            _row(11, 6, "what rank are you", ago=200),
            _row(12, 5, "gold 2", reply_to=11, ago=100),
        ]
        contents, _ = _build(FakeSession(rows, {5: "kaz", 6: "rin"}))
        line = [t for _, t in _texts(contents) if t.startswith("kaz (replying")][0]
        self.assertIn('(replying to rin: "what rank are you")', line)
        self.assertTrue(line.endswith(": gold 2"))

    def test_a_target_outside_the_window_is_fetched(self) -> None:
        rows = [_row(12, 5, "gold 2", reply_to=99, ago=10)]
        older = [_row(99, 6, "what rank are you", ago=9000)]
        session = FakeSession(rows, {5: "kaz", 6: "rin"}, older=older)
        contents, _ = _build(session)
        self.assertIn('(replying to rin: "what rank are you")', _texts(contents)[0][1])

    def test_no_extra_query_when_nothing_replies(self) -> None:
        session = FakeSession([_row(10, 5, "hi", ago=10)], {5: "kaz"})
        _build(session)
        self.assertEqual(session.queries, 2)  # the window + the name lookup, nothing more


class GapMarkerTest(unittest.TestCase):
    def test_a_long_silence_is_marked(self) -> None:
        rows = [
            _row(10, 5, "night all", ago=GAP_SECONDS * 20),
            _row(11, 6, "morning", ago=5),
        ]
        contents, _ = _build(FakeSession(rows, {5: "kaz", 6: "rin"}))
        marked = [t for _, t in _texts(contents) if t.startswith("—")]
        self.assertEqual(len(marked), 1)
        self.assertTrue(marked[0].startswith("— "))
        self.assertIn("later —\nrin:", marked[0])

    def test_a_live_burst_is_not_marked(self) -> None:
        rows = [_row(10, 5, "yo", ago=20), _row(11, 6, "hey", ago=10)]
        contents, _ = _build(FakeSession(rows, {5: "kaz", 6: "rin"}))
        self.assertFalse([t for _, t in _texts(contents) if t.startswith("—")])

    def test_the_new_message_is_marked_when_the_channel_had_died(self) -> None:
        rows = [_row(10, 5, "anyway", ago=GAP_SECONDS * 8)]
        contents, _ = _build(FakeSession(rows, {5: "kaz"}))
        self.assertTrue(_texts(contents)[-1][1].startswith("— "))

    def test_elapsed_reads_like_a_person(self) -> None:
        self.assertEqual(_elapsed(1200), "20 minutes later")
        self.assertEqual(_elapsed(4000), "an hour later")
        self.assertEqual(_elapsed(10800), "3 hours later")
        self.assertEqual(_elapsed(90000), "a day later")
        self.assertEqual(_elapsed(400000), "4 days later")


class OtherBotsTest(unittest.TestCase):
    def test_another_bot_is_a_named_speaker_not_olisar(self) -> None:
        rows = [
            _row(10, 42, "GG @kaz, you reached level 5!", is_bot=True, name="MEE6", ago=60),
            _row(11, BOT_ID, "nice one", is_bot=True, ago=30),
        ]
        contents, _ = _build(FakeSession(rows, {}))
        roles = _texts(contents)
        self.assertEqual(roles[0], ("user", "MEE6 (bot): GG @kaz, you reached level 5!"))
        self.assertEqual(roles[1], ("model", "nice one"))

    def test_a_nameless_bot_row_is_olisar(self) -> None:
        """Every row written before author_name existed is one of Olisar's own."""
        self.assertEqual(speaker_name(_row(1, 7, "x", is_bot=True), {}), "Olisar")
        self.assertEqual(speaker_name(_row(1, 7, "x", is_bot=True), {}, own="you"), "you")

    def test_a_human_prefers_their_current_profile_name(self) -> None:
        row = _row(1, 5, "x", name="old nick")
        self.assertEqual(speaker_name(row, {5: "kaz"}), "kaz")
        self.assertEqual(speaker_name(row, {}), "old nick")


class ReactionScoreTest(unittest.TestCase):
    def test_questions_are_a_hard_zero(self) -> None:
        """The old gate scored *for* questions while the prompt said to skip them."""
        for text in ("anyone know how to fix this?", "what time is the raid", "why is it down"):
            self.assertEqual(reaction_score(text), 0.0, text)

    def test_the_things_people_actually_react_to(self) -> None:
        for text in ("lmao that's brutal", "gg everyone", "rip my sanity", "[image: cat.png]"):
            self.assertGreater(reaction_score(text), 0.0, text)

    def test_trivial_messages_score_zero(self) -> None:
        self.assertEqual(reaction_score("k"), 0.0)
        self.assertEqual(reaction_score(""), 0.0)

    def test_a_wall_of_text_scores_below_a_one_liner(self) -> None:
        self.assertLess(reaction_score("gg " + "context " * 60), reaction_score("gg"))


class RoomNotesTest(unittest.TestCase):
    def test_unset_room_adds_nothing(self) -> None:
        self.assertEqual(room_notes("", None), "")

    def test_each_server_type_says_something(self) -> None:
        for key in SERVER_TYPES:
            if key:
                self.assertIn(SERVER_TYPES[key], room_notes(key))

    def test_unknown_type_is_ignored_rather_than_echoed(self) -> None:
        self.assertEqual(room_notes("wharrgarbl", None), "")

    def test_slang_levels_are_distinct(self) -> None:
        notes = {room_notes("", d) for d in range(4)}
        self.assertEqual(len(notes), 4)
        self.assertTrue(room_notes("", DEFAULT_SLANG_DENSITY))


if __name__ == "__main__":
    unittest.main()
