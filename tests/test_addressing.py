"""Coverage for telling "talking to Olisar" from "talking about Olisar".

Run:  uv run python -m unittest tests.test_addressing -v

The name trigger matches anywhere in a message, so "olisar was down yesterday" used to
get a full reply. The heuristic sorts the clear cases for free and hands only the middle
to a one-word model call — which must fail *open*, since a bot that goes silent when its
quota runs out is a far worse failure than one that answers when it needn't have.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import olisar.addressing as addressing
from olisar.addressing import ADDRESSED, AMBIGUOUS, PASSING, confirm_addressed, name_mention_kind

NAMES = ["olisar", "oli"]


def kind(text: str) -> str:
    return name_mention_kind(text, NAMES)


class HeuristicTest(unittest.TestCase):
    def test_vocative_opening(self) -> None:
        for text in ("olisar help", "hey olisar", "yo oli what's up", "@olisar ping"):
            self.assertEqual(kind(text), ADDRESSED, text)

    def test_name_at_the_end(self) -> None:
        for text in ("thanks olisar", "good night oli!", "that was great olisar"):
            self.assertEqual(kind(text), ADDRESSED, text)

    def test_question_anywhere_counts(self) -> None:
        self.assertEqual(kind("does olisar know when the patch drops?"), ADDRESSED)

    def test_second_person_counts(self) -> None:
        self.assertEqual(kind("i think olisar can do that for you"), ADDRESSED)

    def test_talked_about_is_passing(self) -> None:
        for text in (
            "olisar was down yesterday",
            "olisar's memory is impressive",
            "i asked olisar already",
            "olisar keeps forgetting my name",
            "olisar doesn't respond in that channel",
            "olisar said the patch was delayed",
        ):
            self.assertEqual(kind(text), PASSING, text)

    def test_an_imperative_is_not_gossip(self) -> None:
        """The words that read as third-person one moment and as an order the next are
        left to the classifier rather than guessed at."""
        self.assertNotEqual(kind("oli just do it"), PASSING)
        self.assertNotEqual(kind("olisar keep an eye on that"), PASSING)

    def test_genuinely_unclear_goes_upstairs(self) -> None:
        self.assertEqual(kind("someone should get olisar in here"), AMBIGUOUS)

    def test_no_name_at_all_is_ambiguous(self) -> None:
        """The trigger can fire on a mention or a reply, where there's no name to read."""
        self.assertEqual(kind("what do you think"), AMBIGUOUS)

    def test_a_question_beats_a_third_person_verb(self) -> None:
        self.assertEqual(kind("olisar is that right?"), ADDRESSED)


class ConfirmTest(unittest.TestCase):
    def _confirm(self, text: str) -> bool:
        return asyncio.run(confirm_addressed("someone should get olisar in here", text))

    def test_model_says_mentioning(self) -> None:
        client = AsyncMock()
        client.generate.return_value = type("R", (), {"text": "mentioning"})()
        with patch.object(addressing, "get_gemini", return_value=client):
            self.assertFalse(self._confirm("Olisar"))

    def test_model_says_addressed(self) -> None:
        client = AsyncMock()
        client.generate.return_value = type("R", (), {"text": "addressed"})()
        with patch.object(addressing, "get_gemini", return_value=client):
            self.assertTrue(self._confirm("Olisar"))

    def test_failure_replies_anyway(self) -> None:
        """Rate-limited or broken, it must not make the bot mute."""
        client = AsyncMock()
        client.generate.side_effect = RuntimeError("quota")
        with patch.object(addressing, "get_gemini", return_value=client):
            self.assertTrue(self._confirm("Olisar"))

    def test_garbage_answer_replies_anyway(self) -> None:
        client = AsyncMock()
        client.generate.return_value = type("R", (), {"text": ""})()
        with patch.object(addressing, "get_gemini", return_value=client):
            self.assertTrue(self._confirm("Olisar"))


if __name__ == "__main__":
    unittest.main()
