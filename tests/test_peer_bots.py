"""Coverage for the emulated-member allowlist and the prompt-override seam.

Run:  uv run python -m unittest tests.test_peer_bots -v

Both are sandbox-only mechanisms that live in production code paths, so what actually needs
proving is that they are *inert* when unconfigured — an empty allowlist must reduce to the
old `not author.bot`, and an unset/missing/corrupt override file must yield the operating
rules compiled into the source. A regression in either direction is invisible in normal use
and changes how the shipped bot behaves.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from olisar import peers, prompt_overrides
from olisar.persona import OPERATING_RULES, build_system_prompt


class _User:
    """Stand-in for a discord.py User/Member — peers reads only `.id` and `.bot`."""

    def __init__(self, user_id: int, is_bot: bool) -> None:
        self.id = user_id
        self.bot = is_bot


HUMAN = _User(100, False)
STRANGER_BOT = _User(200, True)
EMULATOR = _User(300, True)


class PeerAllowlistTests(unittest.TestCase):
    def _with_ids(self, ids):
        return patch.object(peers.settings, "peer_bot_ids", ids)

    def test_empty_allowlist_is_exactly_not_bot(self):
        """The production configuration: no allowlist, so the predicate is the old check."""
        with self._with_ids([]):
            self.assertTrue(peers.is_member_author(HUMAN))
            self.assertFalse(peers.is_member_author(STRANGER_BOT))
            self.assertFalse(peers.is_member_author(EMULATOR))
            self.assertFalse(peers.is_peer_bot(EMULATOR))

    def test_allowlisted_bot_counts_as_a_member(self):
        with self._with_ids([300]):
            self.assertTrue(peers.is_member_author(EMULATOR))
            self.assertTrue(peers.is_peer_bot(EMULATOR))

    def test_allowlist_does_not_leak_to_other_bots(self):
        """Allowlisting one emulator must not admit every bot in the server — a real
        Discord server has other bots in it, and their chatter is not test input."""
        with self._with_ids([300]):
            self.assertFalse(peers.is_member_author(STRANGER_BOT))

    def test_humans_are_never_gated_by_the_allowlist(self):
        with self._with_ids([999]):
            self.assertTrue(peers.is_member_author(HUMAN))

    def test_author_without_a_bot_flag_reads_as_a_person(self):
        """Mirrors the `not author.bot` semantics this replaced: no bot flag means human.

        The predicate only ever *widens* who counts as a member, so an unrecognised author
        object is not a security question — but it must not raise on the message listener's
        hot path, which the old attribute access would have done.
        """
        with self._with_ids([300]):
            self.assertTrue(peers.is_member_author(object()))
            self.assertFalse(peers.is_peer_bot(object()))

    def test_unparseable_id_on_a_bot_is_not_admitted(self):
        """A bot whose id can't be read is not the allowlisted emulator, so it stays out."""

        class Broken:
            bot = True
            id = "not-a-number"

        with self._with_ids([300]):
            self.assertFalse(peers.is_member_author(Broken()))


class PromptOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "overrides.json"
        prompt_overrides._cache_key = None
        prompt_overrides._cache = {}
        prompt_overrides._warned.clear()
        self.addCleanup(prompt_overrides._warned.clear)

    def _env(self, value: str | None):
        env = dict(os.environ)
        if value is None:
            env.pop(prompt_overrides._ENV_VAR, None)
        else:
            env[prompt_overrides._ENV_VAR] = value
        return patch.dict(os.environ, env, clear=True)

    def _write(self, payload) -> None:
        self.path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )

    def test_unset_env_returns_the_compiled_in_rules(self):
        with self._env(None):
            self.assertIn(OPERATING_RULES, build_system_prompt(
                persona_name="O", system_prompt="hi", tone_notes=""
            ))

    def test_override_replaces_the_block(self):
        self._write({"operating_rules": "REPLACEMENT RULES"})
        with self._env(str(self.path)):
            prompt = build_system_prompt(persona_name="O", system_prompt="hi", tone_notes="")
            self.assertIn("REPLACEMENT RULES", prompt)
            self.assertNotIn(OPERATING_RULES, prompt)

    def test_missing_file_falls_back_to_defaults(self):
        """A stale path must not strip the guardrails — the whole point of the fallback."""
        with self._env(str(self.path.parent / "does-not-exist.json")):
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), OPERATING_RULES)

    def test_malformed_json_falls_back_to_defaults(self):
        self._write("{not json at all")
        with self._env(str(self.path)):
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), OPERATING_RULES)

    def test_non_object_json_falls_back_to_defaults(self):
        self._write(["a", "list"])
        with self._env(str(self.path)):
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), OPERATING_RULES)

    def test_blank_and_non_string_values_are_ignored(self):
        """An empty operating-rules block would silently disarm every guardrail, so an
        empty/null value means 'no override' rather than 'use nothing'."""
        self._write({"operating_rules": "   ", "tools_note": None, "proactive_note": 42})
        with self._env(str(self.path)):
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), OPERATING_RULES)
            self.assertEqual(prompt_overrides.tools_note("TOOLS"), "TOOLS")
            self.assertEqual(prompt_overrides.proactive_note("PRO"), "PRO")

    def test_edit_is_picked_up_without_a_restart(self):
        """Variant swaps rely on the mtime check — without it a sweep would score the
        first variant repeatedly and report the differences as noise."""
        self._write({"operating_rules": "FIRST"})
        with self._env(str(self.path)):
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), "FIRST")
            self._write({"operating_rules": "SECOND"})
            os.utime(self.path, (0, 0))  # force a distinct mtime
            self.assertEqual(prompt_overrides.operating_rules(OPERATING_RULES), "SECOND")

    def test_active_reports_keys_not_text(self):
        self._write({"operating_rules": "hello"})
        with self._env(str(self.path)):
            self.assertEqual(prompt_overrides.active(), {"operating_rules": "5 chars"})


if __name__ == "__main__":
    unittest.main()
