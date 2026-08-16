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



class LabellingInsteadOfDropping(unittest.TestCase):
    """The synthesis of two measured results.

    Dropping questions made absent-fact fabrication worse. A tools_note forbidding
    invented affordances landed inside the noise on every dimension while both arms went
    on inventing wikis and archives. The reason both failed is the same: "when search
    comes back with nothing, say you don't know" is an instruction Olisar cannot execute,
    because search never comes back with nothing — `kw` is rank-normalised, so ten
    near-misses and ten real answers look identical. Labelling makes the absence legible
    at the only layer that can see it.
    """

    def test_off_unless_asked_for(self):
        from olisar.memory.search import _label_questions_mode

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLISAR_SEARCH_LABEL_QUESTIONS", None)
            self.assertEqual(_label_questions_mode(), "off")

    def test_tags_only(self):
        from olisar.memory.search import _label_questions_mode

        for value in ("1", "tags", "yes"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"OLISAR_SEARCH_LABEL_QUESTIONS": value}):
                    self.assertEqual(_label_questions_mode(), "tags")

    def test_the_losing_mode_is_still_reachable(self):
        """tags+note lost by 0.56 helpfulness; kept only so it can be re-run."""
        from olisar.memory.search import _label_questions_mode

        for value in ("2", "note", "tags+note"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"OLISAR_SEARCH_LABEL_QUESTIONS": value}):
                    self.assertEqual(_label_questions_mode(), "tags+note")

    def test_it_is_not_the_drop_flag(self):
        """Opposite behaviours; enabling one must not enable the other."""
        from olisar.memory.search import _drop_bot_questions_enabled, _label_questions_mode

        with mock.patch.dict(os.environ, {"OLISAR_SEARCH_LABEL_QUESTIONS": "1"}):
            os.environ.pop("OLISAR_SEARCH_DROP_BOT_QUESTIONS", None)
            self.assertEqual(_label_questions_mode(), "tags")
            self.assertFalse(_drop_bot_questions_enabled())


if __name__ == "__main__":
    unittest.main()
