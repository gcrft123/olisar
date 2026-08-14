"""Coverage for the chat-cadence layer: message splitting, reply anchoring, typing pace.

Run:  uv run python -m unittest tests.test_chat_realism -v

Olisar used to answer as one message, always carrying Discord's reply reference, with
the typing indicator held for however long the model took. Real Discord turns fragment
across 2-4 quick sends, the reply affordance is used to disambiguate rather than by
default, and typing tracks what got written. These lock in the parts of that which are
decidable without a live gateway.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

import discord

from bot.replies import STALE_ANCHOR, TYPING_MAX, TYPING_MIN, anchor_for, typing_seconds
from olisar.context import channel_note
from olisar.persona import (
    DEFAULT_TONE_NOTES,
    SPLIT_MARKER,
    SUPERSEDED_TONE_NOTES,
    refreshed_tone_notes,
    split_messages,
    strip_breaks,
)


def _msg(*, mid: int, channel_id: int = 10, author_id: int = 5, age: float = 0.0, dm: bool = False):
    """A stand-in for discord.Message with only what the helpers touch."""
    return SimpleNamespace(
        id=mid,
        guild=None if dm else SimpleNamespace(id=1),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=author_id),
        created_at=discord.utils.utcnow() - timedelta(seconds=age),
    )


def _bot(*, cached=(), user_id: int = 99):
    return SimpleNamespace(user=SimpleNamespace(id=user_id), cached_messages=list(cached))


class SplitMessagesTest(unittest.TestCase):
    def test_plain_text_is_one_message(self) -> None:
        self.assertEqual(split_messages("just a reply"), ["just a reply"])

    def test_marker_splits_and_strips(self) -> None:
        self.assertEqual(
            split_messages(f"lancia delta {SPLIT_MARKER}  best one ever "),
            ["lancia delta", "best one ever"],
        )

    def test_extra_breaks_collapse_into_the_last_piece(self) -> None:
        """A model asking for eight bubbles doesn't get to flood the channel."""
        pieces = split_messages(SPLIT_MARKER.join("abcdefgh"))
        self.assertEqual(len(pieces), 3)
        self.assertEqual(pieces[:2], ["a", "b"])
        self.assertEqual(pieces[2], "c\nd\ne\nf\ng\nh")

    def test_empty_pieces_are_dropped(self) -> None:
        self.assertEqual(split_messages(f"{SPLIT_MARKER}hi{SPLIT_MARKER}{SPLIT_MARKER}"), ["hi"])

    def test_blank_input_sends_nothing(self) -> None:
        self.assertEqual(split_messages("   "), [])
        self.assertEqual(split_messages(""), [])

    def test_strip_breaks_folds_to_one_body(self) -> None:
        self.assertEqual(strip_breaks(f"one{SPLIT_MARKER}two{SPLIT_MARKER}three"), "one\ntwo\nthree")
        self.assertNotIn(SPLIT_MARKER, strip_breaks(f"a{SPLIT_MARKER}b"))


class AnchorForTest(unittest.TestCase):
    def test_quiet_channel_gets_no_reply_reference(self) -> None:
        target = _msg(mid=100)
        self.assertIsNone(anchor_for(_bot(), target))

    def test_dm_never_anchors(self) -> None:
        self.assertIsNone(anchor_for(_bot(), _msg(mid=100, dm=True)))

    def test_room_moved_on_anchors(self) -> None:
        target = _msg(mid=100)
        newer = _msg(mid=101, author_id=7)
        self.assertIs(anchor_for(_bot(cached=[target, newer]), target), target)

    def test_our_own_later_message_does_not_count_as_the_room_moving_on(self) -> None:
        target = _msg(mid=100)
        ours = _msg(mid=101, author_id=99)
        self.assertIsNone(anchor_for(_bot(cached=[target, ours]), target))

    def test_other_channels_do_not_count(self) -> None:
        target = _msg(mid=100, channel_id=10)
        elsewhere = _msg(mid=101, channel_id=11, author_id=7)
        self.assertIsNone(anchor_for(_bot(cached=[elsewhere]), target))

    def test_scrolled_away_message_anchors(self) -> None:
        target = _msg(mid=100, age=STALE_ANCHOR + 5)
        self.assertIs(anchor_for(_bot(), target), target)

    def test_no_message_at_all(self) -> None:
        self.assertIsNone(anchor_for(_bot(), None))


class TypingPaceTest(unittest.TestCase):
    def test_short_and_long_replies_stay_in_bounds(self) -> None:
        for text in ("np", "x" * 4000, ""):
            secs = typing_seconds(text)
            self.assertGreater(secs, 0)
            self.assertLessEqual(secs, TYPING_MAX * 1.2)
            self.assertGreaterEqual(secs, TYPING_MIN * 0.85)

    def test_longer_text_takes_longer_to_type(self) -> None:
        """Jitter must not swamp the signal — that's the whole point of the pacing."""
        self.assertLess(max(typing_seconds("ok") for _ in range(20)),
                        min(typing_seconds("x" * 300) for _ in range(20)))


class ToneNotesRefreshTest(unittest.TestCase):
    """A style rewrite that only reaches new installs isn't a rewrite. The seed list is
    the mechanism, and it has to keep growing — an entry dropped from it is a server that
    quietly keeps a default nobody chose."""

    def test_every_superseded_seed_moves_forward(self) -> None:
        for old in SUPERSEDED_TONE_NOTES:
            self.assertEqual(refreshed_tone_notes(old), DEFAULT_TONE_NOTES)
            self.assertEqual(refreshed_tone_notes(f"  {old}\n"), DEFAULT_TONE_NOTES)

    def test_the_1_4_4_seed_is_still_listed(self) -> None:
        """The release before this one refreshed servers onto its own seed; matching only
        the oldest would strand exactly the ones that refresh reached."""
        self.assertGreaterEqual(len(SUPERSEDED_TONE_NOTES), 2)

    def test_an_admins_own_writing_is_never_touched(self) -> None:
        for text in ("be nice", SUPERSEDED_TONE_NOTES[0] + "\n- and be brief", "formal, always"):
            self.assertIsNone(refreshed_tone_notes(text))

    def test_the_current_default_is_left_alone(self) -> None:
        """Not merely a no-op: returning it would dirty the row on every connect."""
        self.assertIsNone(refreshed_tone_notes(DEFAULT_TONE_NOTES))

    def test_empty_notes_stay_empty(self) -> None:
        """Cleared on purpose is a choice too — the persona alone carries the voice."""
        self.assertIsNone(refreshed_tone_notes(""))
        self.assertIsNone(refreshed_tone_notes("   \n "))

    def test_the_current_default_is_not_also_a_superseded_one(self) -> None:
        self.assertNotIn(DEFAULT_TONE_NOTES.strip(), [s.strip() for s in SUPERSEDED_TONE_NOTES])

    def test_the_default_teaches_the_split_marker(self) -> None:
        """The examples are what carry the rhythm; the marker has to survive edits here."""
        self.assertIn(SPLIT_MARKER, DEFAULT_TONE_NOTES)


class ChannelNoteTest(unittest.TestCase):
    def test_names_the_room(self) -> None:
        note = channel_note("help")
        self.assertIn("#help", note)
        self.assertIn("register", note)

    def test_topic_is_quoted_and_clipped(self) -> None:
        note = channel_note("#general", "  chat about   anything  ")
        self.assertIn('"chat about anything"', note)
        self.assertLess(len(channel_note("general", "x" * 5000)), 900)

    def test_no_channel_no_note(self) -> None:
        self.assertEqual(channel_note(""), "")
        self.assertEqual(channel_note("  "), "")


if __name__ == "__main__":
    unittest.main()
