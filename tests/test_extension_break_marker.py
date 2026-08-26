"""The delivery marker must not escape into an extension's hands.

Reported from a live server: a welcome message arrived with "[[break]]" printed in it.
The chain is entirely inside the host, and any extension holding both permissions would
hit it — welcome.js is only the first that does.

  welcome.js asks for a greeting in the server's voice   host.generate
  the persona prompt teaches the model to mark beats     SPLIT_MARKER in DEFAULT_TONE_NOTES
  the model obliges                                      "welcome![[break]]glad you're here"
  the extension gets a plain string and forwards it      host.discord.send
  the event send path chunks for length, never splits    bot/cogs/sdk_events.py

bot/replies.py turns the marker into consecutive messages, which is what it is for. Nothing
on the extension path does, and an extension cannot be expected to know the marker exists.
"""

from __future__ import annotations

import unittest

from olisar.persona import DEFAULT_TONE_NOTES, SPLIT_MARKER, strip_breaks


class TheMarkerIsTaught(unittest.TestCase):
    def test_the_persona_prompt_asks_for_it(self):
        """Why generated text contains it at all — this is the source of the leak."""
        self.assertIn(SPLIT_MARKER, DEFAULT_TONE_NOTES)


class FoldingAtTheBoundary(unittest.TestCase):
    def test_the_reported_shape(self):
        raw = f"welcome in, @rook!{SPLIT_MARKER}have a look at the rules when you get a sec"
        folded = strip_breaks(raw)
        self.assertNotIn(SPLIT_MARKER, folded)
        self.assertEqual(folded, "welcome in, @rook!\nhave a look at the rules when you get a sec")

    def test_several_markers(self):
        self.assertNotIn(SPLIT_MARKER, strip_breaks(f"a{SPLIT_MARKER}b{SPLIT_MARKER}c"))

    def test_text_without_one_is_untouched(self):
        plain = "welcome in, glad to have you"
        self.assertEqual(strip_breaks(plain), plain)

    def test_generate_folds_before_returning(self):
        """The fix, asserted at the line that ships it rather than on strip_breaks alone."""
        import inspect

        from olisar.sandbox.capabilities import _generate

        source = inspect.getsource(_generate)
        self.assertIn("strip_breaks", source)
        self.assertNotIn(
            'return (result.text or "").strip()',
            source,
            "host.generate returned raw model text again — the marker will leak",
        )


if __name__ == "__main__":
    unittest.main()
