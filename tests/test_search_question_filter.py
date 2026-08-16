"""The question-drop filter: what it matches, and why it ships off.

The filter itself works — it identifies questions put to the bot accurately, and those
really do crowd out everything else on a keyword search for the same subject. What the
arena A/B found is that removing them makes Olisar's answers *worse* on the case that
motivated the work, because the crowd of questions is the only absence signal the
retrieval layer emits. See ``_drop_bot_questions_enabled`` for the full argument.

So these tests pin two separate things: the matcher's behaviour, which should stay correct
in case the flag is ever turned back on, and the default, which is the finding.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from olisar.memory.search import _drop_bot_questions_enabled, _is_question_to_bot

NAMES = ["olisar", "oli"]


class QuestionMatching(unittest.TestCase):
    """Both conditions have to hold: addressed to the bot, *and* a question."""

    def test_question_to_the_bot_matches(self):
        for text in (
            "olisar who posted the setup guide?",
            "hey oli, does anyone know where the schedule went",
            "Olisar — what happened to the bracket thread",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_question_to_bot(text, NAMES))

    def test_statement_to_the_bot_survives(self):
        """A statement naming the bot is real evidence and must not be filtered."""
        for text in (
            "olisar said the schedule moved to friday",
            "oli already posted the link above",
        ):
            with self.subTest(text=text):
                self.assertFalse(_is_question_to_bot(text, NAMES))

    def test_question_not_addressed_to_the_bot_survives(self):
        """Members asking each other is ordinary server history, not noise."""
        self.assertFalse(_is_question_to_bot("who posted the setup guide?", NAMES))

    def test_empty_and_nameless(self):
        self.assertFalse(_is_question_to_bot("", NAMES))
        self.assertFalse(_is_question_to_bot("olisar who did this?", []))


class DefaultIsOff(unittest.TestCase):
    """The finding, pinned.

    Dropping questions promotes members' speculation into the top slots, and Olisar
    reports speculation as fact — it invented a Twitter account "dead for at least a year"
    in every run of the dropped arm, where the kept arm correctly said the server has
    none. Present-fact retrieval is unaffected either way, so there is no trade.
    """

    def test_off_unless_asked_for(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLISAR_SEARCH_DROP_BOT_QUESTIONS", None)
            self.assertFalse(_drop_bot_questions_enabled())

    def test_explicitly_enabled(self):
        for value in ("1", "true", "yes"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"OLISAR_SEARCH_DROP_BOT_QUESTIONS": value}):
                    self.assertTrue(_drop_bot_questions_enabled())

    def test_explicitly_disabled(self):
        for value in ("0", "false", "no"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"OLISAR_SEARCH_DROP_BOT_QUESTIONS": value}):
                    self.assertFalse(_drop_bot_questions_enabled())


if __name__ == "__main__":
    unittest.main()
