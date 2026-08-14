"""Coverage for the follow-up signal in the proactivity cascade.

Run:  uv run python -m unittest tests.test_proactive_followup -v

Olisar says something, a member answers it without using the reply arrow, and nothing
about that message says "bot": it isn't a question, it doesn't say the name, and the
heuristic scores it near zero. It was then judged against a threshold set for butting
into strangers' conversations — so Olisar walked away from exchanges it had started.
Both gates now bend for it, and neither opens without the structural fact that Olisar
wrote the message directly above.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import olisar.proactivity as proactivity
from olisar.context import is_own_message
from olisar.proactivity import (
    FOLLOW_UP_FLOOR,
    classify,
    follow_up_score,
    heuristic_score,
    relaxed_threshold,
)


def _msg(*, is_bot=False, name=""):
    return SimpleNamespace(author_is_bot=is_bot, author_name=name)


class FollowUpScoreTest(unittest.TestCase):
    def test_nothing_fires_without_olisar_above(self) -> None:
        """"wait really?" between two other people is not Olisar's to answer."""
        for text in ("wait really?", "yeah but do you think that works", "huh"):
            self.assertEqual(follow_up_score(text, after_olisar=False), 0.0, text)

    def test_olisar_speaking_last_is_itself_a_signal(self) -> None:
        self.assertGreater(follow_up_score("that tracks", after_olisar=True), 0.0)

    def test_the_clearest_follow_ups_score_highest(self) -> None:
        direct = follow_up_score("wait what do you mean?", after_olisar=True)
        vague = follow_up_score(
            "anyway the patch notes are up on the website somewhere i think", after_olisar=True
        )
        self.assertEqual(direct, 1.0)
        self.assertGreater(direct, vague)

    def test_a_short_answer_beats_the_question_heuristic(self) -> None:
        """The case the old gate missed entirely: an answer with no question mark."""
        text = "yeah i tried that already"
        self.assertLess(heuristic_score(text, 20.0), 0.4)  # below every level threshold
        self.assertGreaterEqual(follow_up_score(text, after_olisar=True), 0.4)

    def test_empty_message(self) -> None:
        self.assertEqual(follow_up_score("", after_olisar=True), 0.0)


class RelaxedThresholdTest(unittest.TestCase):
    def test_no_follow_up_leaves_the_operators_bar_alone(self) -> None:
        self.assertEqual(relaxed_threshold(0.7, 0.0), 0.7)

    def test_a_follow_up_lowers_it(self) -> None:
        self.assertLess(relaxed_threshold(0.7, 1.0), 0.7)

    def test_the_bar_is_eased_never_waived(self) -> None:
        self.assertEqual(relaxed_threshold(0.35, 1.0), FOLLOW_UP_FLOOR)

    def test_a_low_bar_is_never_raised_by_this(self) -> None:
        """An operator who set 0.2 asked for eager. The floor bounds the relief, not
        them — handing back 0.3 here would tighten a threshold while claiming to relax it."""
        self.assertEqual(relaxed_threshold(0.2, 0.0), 0.2)
        self.assertEqual(relaxed_threshold(0.2, 1.0), 0.2)

    def test_relief_scales_with_the_signal(self) -> None:
        self.assertGreater(relaxed_threshold(0.9, 0.3), relaxed_threshold(0.9, 1.0))


class ClassifierPromptTest(unittest.TestCase):
    def _instruction(self, **kw) -> str:
        client = AsyncMock()
        client.generate.return_value = SimpleNamespace(text='{"should_respond": true, "confidence": 0.9}')
        with patch.object(proactivity, "get_gemini", return_value=client):
            asyncio.run(classify("kaz: yeah i tried that", **kw))
        return client.generate.call_args.kwargs["system_instruction"]

    def test_the_follow_up_case_is_put_to_the_classifier(self) -> None:
        instruction = self._instruction(follow_up=True)
        self.assertIn("ALREADY in", instruction)
        self.assertIn("Lean towards yes", instruction)

    def test_a_cold_chime_is_judged_as_before(self) -> None:
        self.assertNotIn("ALREADY in", self._instruction())

    def test_the_base_rules_survive_either_way(self) -> None:
        for kw in ({}, {"follow_up": True}):
            self.assertIn("UNPROMPTED", self._instruction(**kw))


class OwnMessageTest(unittest.TestCase):
    def test_the_row_above_is_recognised_as_olisars(self) -> None:
        self.assertTrue(is_own_message(_msg(is_bot=True)))
        self.assertFalse(is_own_message(_msg(is_bot=True, name="MEE6")))
        self.assertFalse(is_own_message(_msg()))


if __name__ == "__main__":
    unittest.main()
